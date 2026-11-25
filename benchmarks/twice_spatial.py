"""Paired trials of dynamic R*-tree shards on the frozen county traces."""

import argparse
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory

from benchmarks.common import BIN, ROOT, WORK, schedule
from benchmarks.fetch import sha256
from benchmarks import followup_spatial as spatial
from benchmarks.paper_common import Journal, load_journal, portable_file
from benchmarks.paper_data import CACHE, rank


REPEATS = 3
PRIMARY = (32, 12_000, 3000, 20_000, 1)
SCALING = (64, 60_000, 60_000, 120_000, 37)


def _verify(trace, capacity, stride, methods, expected):
    checked = {}
    for method in methods:
        row = spatial.replay(trace, method, "verify", capacity, stride, methods=methods)
        suffix = method[-1]
        control = expected[f"pruned-{suffix}"]
        same_queries = ("query_signature", "verified_queries", "query_signature_count")
        is_grid = method.startswith(("grid-", "loose-"))
        row["verified"] = (
            row["status"] == "ok" and all(row[k] == control[k] for k in same_queries)
            and row["verified_queries"] == (row["queries"] if stride == 1 else control["verified_queries"])
            and (row["forest_depth"] == int(method.split("-")[1]) if is_grid
                 else row["layout_signature"] == expected[method]["layout_signature"])
        )
        checked[method] = row
    return [{"method": name, "stage": "verify", **r} for name, r in checked.items()]


def run(path, *, probe, depth, loose=False):
    _, frozen = load_journal(ROOT / "doubling/runs/spatial.jsonl")
    reference = {r["unit"]: r for r in frozen}
    modes = (("pruned-9", "planar-9", *(f"grid-{i}-9" for i in range(3, 7))) if probe else
             tuple(f"{name}-{r}" for r in (0, 9) for name in ("pruned", "planar", f"grid-{depth}")))
    if loose:
        modes = tuple(f"{name}-{r}" for r in (0, 9) for name in ("pruned", f"grid-{depth}", f"loose-{depth}"))
    methods = {name: (name, 32, 96) for name in modes}
    protocol = {"study": "rtree-forest-v1", "probe": probe, "depth": depth, "seed": spatial.SEED,
                "repeats": REPEATS if not probe else 2, "methods": list(methods),
                "manifest_sha256": sha256(spatial.MANIFEST), "reference_sha256": sha256(ROOT / "doubling/runs/spatial.jsonl"),
                "program_sha256": sha256(BIN / "rtree.exe"), "primary": list(PRIMARY), "scaling": list(SCALING)}
    if loose:
        protocol["study"] = "loose-forest-v1"
    journal = Journal(path, protocol)
    cases = json.loads(spatial.MANIFEST.read_text(encoding="utf-8"))["cases"]
    if probe:
        cases = cases[::6]
    with TemporaryDirectory(dir=WORK, prefix="rtree-forest-") as scratch:
        trace = Path(scratch) / "trace.txt"
        for case in cases:
            county = case["county"]
            units = [("primary", profile, PRIMARY) for profile in spatial.PROFILES]
            if case["scale_case"]:
                units.append(("scaling", "write-heavy", SCALING))
            data = list(spatial.shapefile_boxes(portable_file(CACHE, case)))
            for tier, profile, profile_settings in units:
                name = f"{county}/{tier}/{profile}"
                if name in journal.completed:
                    continue
                control = reference[name]
                capacity, operations, initial_limit, limit, stride = profile_settings
                seed = spatial.SEED + int(rank("followup-trace", name)[:10], 16)
                sample = random.Random(seed).sample(data, min(len(data), limit))
                initial = min(initial_limit, len(sample) // 2)
                trace.write_text(spatial.make_trace(sample, profile, initial, operations, seed),
                                 encoding="ascii", newline="\n")
                digest = sha256(trace)
                if digest != control["attributes"]["trace_sha256"]:
                    raise ValueError(f"{name} differs from the frozen trace")
                expected = {r["method"]: r for r in control["attempts"] if r["stage"] == "verify"}
                rows = _verify(trace, capacity, stride, methods, expected)
                for repeat, method in schedule(methods, seed=seed, repeats=protocol["repeats"] + 1):
                    row = spatial.replay(trace, method, "timed", capacity, stride, methods=methods)
                    rows.append({"repeat": repeat - 1, "stage": "timed", "method": method, **row})
                journal.add(name, attributes={"county": county, "tier": tier, "profile": profile,
                                               "trace_sha256": digest, "capacity": capacity, "initial": initial,
                                               "operations": operations, "stride": stride}, attempts=rows)
                issues = [r["method"] for r in rows if not r.get("verified", r["status"] == "ok")]
                print(f"{name} passed" if not issues else f"{name} failed {issues}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--loose", action="store_true")
    parser.add_argument("--depth", type=int, choices=range(3, 7), default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    default = "spatial-probe.jsonl" if args.probe else "spatial.jsonl"
    runs = ROOT / ("manuscripts/runs" if args.loose else "twice/runs")
    run(args.output or runs / default, probe=args.probe, depth=args.depth, loose=args.loose)
