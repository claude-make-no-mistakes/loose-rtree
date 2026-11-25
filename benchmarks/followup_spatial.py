"""Compare exact R*-tree subtree pruning on new Census counties."""

import argparse
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory

from benchmarks.common import BIN, ROOT, WORK, schedule
from benchmarks.fetch import sha256
from benchmarks.paper_common import Journal, PAPER, portable_file
from benchmarks.paper_data import CACHE, _spatial, check_frozen, rank
from benchmarks.paper_spatial import PROFILES, replay
from benchmarks.spatial import make_trace, shapefile_boxes


FOLLOWUP = ROOT / "followup"
MANIFEST = FOLLOWUP / "inputs-spatial.json"
METHODS = {f"{name}-{count}": (f"{name}-{count}", 32, 96)
           for count in (0, 9) for name in ("stock", "prefix", "pruned")}
SEED = 2_612_317
REPEATS = 3


def fetch():
    if MANIFEST.exists():
        check_frozen(MANIFEST, restore=True, inputs_only=True)
        return
    prior = PAPER / "inputs-spatial.json"
    seen = {row["county"] for row in json.loads(prior.read_text(encoding="utf-8"))["cases"]}
    frozen = _spatial(extra_excluded=seen, namespace="followup-county", per_stratum=3)
    frozen["prior_manifest_sha256"] = sha256(prior)
    with MANIFEST.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(frozen, indent=2, ensure_ascii=False) + "\n")
    missing = [row["county"] for row in frozen["cases"] if "error" in row]
    print(f"Froze {len(frozen['cases'])} new counties. Unavailable archives: {missing}", flush=True)


def _evaluate(trace, *, capacity, stride, seed):
    checks = {method: replay(trace, method, "verify", capacity, stride, methods=METHODS) for method in METHODS}
    for name, row in checks.items():
        suffix = name.rsplit("-", 1)[1]
        stock, prefix = (checks[f"{method}-{suffix}"] for method in ("stock", "prefix"))
        row["verified"] = False
        if row["status"] != "ok" or stock["status"] != "ok":
            continue
        row["verified"] = (
            row["query_signature"] == stock["query_signature"]
            and row["layout_signature"] == stock["layout_signature"]
            and row["query_signature_count"] == stock["queries"]
            and row["verified_queries"] == stock["verified_queries"]
            and (stride > 1 or row["verified_queries"] == stock["queries"])
        )
        if name.startswith("pruned"):
            row["verified"] &= (row["overlap_pruning"] and prefix["status"] == "ok"
                                and row["overlap_possible"] == prefix["overlap_tested"]
                                and row["overlap_tested"] <= row["overlap_possible"]
                                and row["split_corner_calls"] == prefix["split_corner_calls"]
                                and row["split_bound_expansions"] == prefix["split_bound_expansions"])
    recorded = [{"method": method, "stage": "verify", **row} for method, row in checks.items()]
    for repeat, method in schedule(METHODS, seed=seed, repeats=REPEATS + 1):
        row = replay(trace, method, "timed", capacity, stride, methods=METHODS)
        if repeat:
            recorded.append({"method": method, "stage": "timed", "repeat": repeat - 1, **row})
    return recorded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--output", type=Path, default=FOLLOWUP / "runs" / "spatial.jsonl")
    args = parser.parse_args()
    if args.fetch:
        fetch()
        return
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]
    protocol = {"study": "spatial-pruning-v1", "seed": SEED, "probe": args.probe,
                "manifest_sha256": sha256(MANIFEST), "program_sha256": sha256(BIN / "rtree.exe"),
                "methods": {k: list(v) for k, v in METHODS.items()}, "profiles": list(PROFILES),
                "repeats": REPEATS, "primary": [32, 12_000, 3000, 20_000, 1],
                "scaling": [64, 60_000, 60_000, 120_000, 37]}
    journal = Journal(args.output, protocol)
    with TemporaryDirectory(dir=WORK, prefix="followup-spatial-") as sandbox:
        trace = Path(sandbox) / "trace.txt"
        for receipt in rows[:1] if args.probe else rows:
            county = receipt["county"]
            tiers = [("primary", p) for p in PROFILES]
            if receipt["scale_case"] and not args.probe:
                tiers.append(("scaling", "write-heavy"))
            data = list(shapefile_boxes(portable_file(CACHE, receipt))) if "error" not in receipt else []
            for tier, profile in tiers:
                name = f"{county}/{tier}/{profile}"
                if name in journal.completed:
                    continue
                traits = {"county": county, "region": receipt["region"], "population": receipt["population"],
                          "tier": tier, "profile": profile}
                if not data:
                    journal.add(name, attributes={**traits, "error": receipt.get("error", "empty road archive")}, attempts=[])
                    continue
                capacity, operations, initial_limit, limit, stride = protocol[tier]
                operations = 1800 if args.probe else operations
                seed = SEED + int(rank("followup-trace", name)[:10], 16)
                sample = random.Random(seed).sample(data, min(len(data), limit))
                initial = min(initial_limit, len(sample) // 2)
                text = make_trace(sample, profile, initial, operations, seed)
                trace.write_text(text, encoding="ascii", newline="\n")
                attempts = _evaluate(trace, capacity=capacity, stride=stride, seed=seed)
                journal.add(name, attributes={**traits, "seed": seed, "capacity": capacity, "operations": operations,
                                              "initial": initial, "trace_sha256": sha256(trace)}, attempts=attempts)
                failures = [r["method"] for r in attempts if not r.get("verified", r["status"] == "ok")]
                print(f"{name} finished. Failing methods: {failures}", flush=True)


if __name__ == "__main__":
    main()
