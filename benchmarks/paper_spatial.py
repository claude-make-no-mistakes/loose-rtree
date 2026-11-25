"""Confirm the stock/cached split equivalence and measure dynamic replay."""

import argparse
from hashlib import sha256 as digest
import json
from pathlib import Path
import random
import subprocess
from tempfile import TemporaryDirectory

from benchmarks.common import BIN, WORK, schedule
from benchmarks.fetch import sha256
from benchmarks.paper_common import Journal, PAPER, SEED, portable_file
from benchmarks.paper_data import CACHE, rank
from benchmarks.spatial import make_trace, shapefile_boxes


METHODS = {
    "stock-0": ("stock-0", 32, 96),
    "stock-9": ("stock-9", 32, 96),
    "prefix-0": ("prefix-0", 32, 96),
    "prefix-9": ("prefix-9", 32, 96),
    "history-16": ("history", 16, 48),
    "geometry": ("geometry", 32, 96),
}
PROFILES = ("moving-hotspot", "write-heavy", "insert-only")


def replay(path, method, mode, capacity, stride, *, methods=METHODS):
    variant, window, threshold = methods[method]
    command = [str(BIN / "rtree.exe"), str(path), variant, mode, str(window), str(threshold),
               str(capacity), str(stride)]
    try:
        program = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    if program.returncode:
        return {"status": "failed", "exit": program.returncode, "error": program.stderr.strip()[-1000:]}
    return {"status": "ok", **json.loads(program.stdout)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PAPER / "runs" / "spatial.jsonl")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    manifest = PAPER / "inputs-spatial.json"
    rows = json.loads(manifest.read_text(encoding="utf-8"))["cases"]
    protocol = {"study": "spatial-v2", "input_manifest_sha256": sha256(manifest), "probe": args.probe,
                "program_sha256": sha256(BIN / "rtree.exe"), "methods": {k: list(v) for k, v in METHODS.items()},
                "profiles": list(PROFILES), "repeats": 3,
                "primary": {"capacity": 32, "operations": 12_000, "scan_stride": 1, "initial_limit": 3000},
                "scaling": {"capacity": 64, "operations": 60_000, "scan_stride": 37, "initial_limit": 60_000}}
    journal = Journal(args.output, protocol)
    with TemporaryDirectory(dir=WORK, prefix="paper-spatial-") as scratch:
        trace = Path(scratch) / "trace.txt"
        for receipt in rows[:1] if args.probe else rows:
            county = receipt["county"]
            if "error" in receipt:
                if county not in journal.completed:
                    journal.add(county, attributes=receipt, attempts=[])
                continue
            data = list(shapefile_boxes(portable_file(CACHE, receipt)))
            slots = [("primary", p) for p in PROFILES]
            if receipt["scale_case"] and not args.probe:
                slots.append(("scaling", "write-heavy"))
            for tier, profile in slots:
                case = f"{county}/{tier}/{profile}"
                if case in journal.completed:
                    continue
                options = protocol[tier]
                capacity, stride = options["capacity"], options["scan_stride"]
                operations = 1200 if args.probe else options["operations"]
                seed = SEED + int(rank("trace", case)[:10], 16)
                rng = random.Random(seed)
                sample = rng.sample(data, min(len(data), 20_000 if tier == "primary" else 120_000))
                initial = min(options["initial_limit"], len(sample) // 2)
                if initial < 128:
                    journal.add(case, attributes={**receipt, "tier": tier, "profile": profile, "error": "insufficient road records"},
                                attempts=[])
                    continue
                text = make_trace(sample, profile, initial, operations, seed)
                trace.write_text(text, encoding="ascii", newline="\n")
                methods = tuple(METHODS) if tier == "primary" else ("stock-0", "prefix-0", "stock-9", "prefix-9", "history-16")
                checks = {method: replay(trace, method, "verify", capacity, stride) for method in methods}
                stock = checks["stock-0"]
                checks_ok = stock["status"] == "ok"
                for method, row in checks.items():
                    row["verified"] = False
                    if row["status"] == "ok" and checks_ok:
                        row["verified"] = (
                            row["query_signature"] == stock["query_signature"]
                            and row["query_signature_count"] == stock["queries"]
                            and (stride != 1 or row["verified_queries"] == row["queries"])
                        )
                    if method.startswith("prefix"):
                        native = checks[method.replace("prefix", "stock")]
                        row["identical_layout"] = (row.get("layout_signature") == native.get("layout_signature")
                                                   and row["status"] == native["status"] == "ok")
                        row["verified"] &= row["identical_layout"]
                attempts = [{"method": method, "stage": "verify", **data} for method, data in checks.items()]
                for repeat, method in schedule(methods, seed=seed, repeats=4):
                    result = replay(trace, method, "timed", capacity, stride)
                    if repeat:
                        attempts.append({"method": method, "stage": "timed", "repeat": repeat - 1, **result})
                traits = {"county": county, "region": receipt["region"], "urban": receipt["urban"], "tier": tier,
                          "label": receipt["label"], "profile": profile, "source_count": len(data),
                          "initial": initial, "operations": operations, "seed": seed, "capacity": capacity,
                          "trace_sha256": digest(text.encode()).hexdigest()}
                journal.add(case, attributes=traits, attempts=attempts)
                failures = [method for method, rec in checks.items() if not rec["verified"]]
                print(f"spatial {case} finished, verification failures {failures}", flush=True)


if __name__ == "__main__":
    main()
