"""Replay identical dynamic queries and updates on Boost R*-tree policies."""

import argparse
from hashlib import sha256 as digest
import json
import math
from pathlib import Path
import random
import statistics
import struct
import subprocess
from tempfile import TemporaryDirectory
import zipfile

from benchmarks.common import BIN, SEED, WORK, interval, metadata, schedule, write_result
from benchmarks.fetch import REGIONS, cached_asset


PROFILE = ("moving-hotspot", "write-heavy")
CONTROLS = ("stock-0", "stock-3", "stock-9", "geometry")
METHODS = {
    "history": ("history", 32, 96),
    "history-16": ("history", 16, 48),
    "history-64": ("history", 64, 192),
    "hindsight": ("hindsight", 32, 0),
    **{method: (method, 32, 96) for method in CONTROLS},
}


def shapefile_boxes(path):
    """Read bounding boxes from the TIGER/Line PolyLine records."""
    with zipfile.ZipFile(path) as archive:
        shp = next(m for m in archive.namelist() if m.endswith(".shp"))
        with archive.open(shp) as data:
            head = data.read(100)
            if len(head) != 100 or struct.unpack_from(">i", head)[0] != 9994:
                raise ValueError(f"{path.name} is not a shapefile")
            while header := data.read(8):
                _, size = struct.unpack(">2i", header)
                record = data.read(2 * size)
                if size < 2 or len(record) != 2 * size:
                    raise ValueError(f"{path.name} contains a truncated record")
                shape = struct.unpack_from("<i", record)[0]
                if shape == 0:
                    continue
                if shape not in (3, 13, 23) or len(record) < 36:
                    raise ValueError(f"{path.name} contains unsupported shape {shape}")
                box = struct.unpack_from("<4d", record, 4)
                if not all(map(math.isfinite, box)) or box[0] > box[2] or box[1] > box[3]:
                    raise ValueError(f"{path.name} contains an invalid road box")
                yield tuple(round(x * 1_000_000) for x in box)


def make_trace(source_boxes, profile, initial, count, seed):
    rng = random.Random(seed)
    if len(source_boxes) <= initial:
        raise ValueError("the trace needs more road boxes than initial entries")
    rng.shuffle(source_boxes)
    x0 = min(b[0] for b in source_boxes)
    y0 = min(b[1] for b in source_boxes)
    boxes = [(a - x0, b - y0, c - x0, d - y0) for a, b, c, d in source_boxes]
    spread = max(max(b[2] for b in boxes), max(b[3] for b in boxes))
    xorder = sorted(range(len(boxes)), key=lambda i: boxes[i][0] + boxes[i][2])
    hotspots = [boxes[xorder[len(boxes) * k // 4]] for k in (1, 3)]
    active = list(range(initial))
    current = {i: b for i, b in enumerate(boxes[:initial])}
    dormant = list(range(initial, len(boxes)))
    ops = [("I", i, *box) for i, box in current.items()]
    if profile == "insert-only":
        for i in range(count):
            source = initial + i
            ops.append(("I", source, *boxes[source % len(boxes)]))
        return f"{initial} {len(ops)}\n" + "".join(" ".join(map(str, row)) + "\n" for row in ops)
    next_id = len(boxes)
    for i in range(count):
        hotspot = hotspots[int(i >= count // 2)]
        cx, cy = (hotspot[0] + hotspot[2]) // 2, (hotspot[1] + hotspot[3]) // 2
        query_rate = 0.75 if profile == "moving-hotspot" else 0.35
        if rng.random() < query_rate:
            if rng.random() < 0.2:
                sample = boxes[rng.randrange(len(boxes))]
                cx, cy = sample[0], sample[1]
            radius = max(2, int(spread * rng.choice((0.005, 0.01, 0.03))))
            x, y = cx + rng.randint(-2 * radius, 2 * radius), cy + rng.randint(-2 * radius, 2 * radius)
            if i % 10 == 0:
                # Touch an existing boundary exactly, including zero-area boxes.
                ref = current[active[i % len(active)]]
                ops.append(("Q", ref[2], ref[3], ref[2], ref[3]))
            elif i % 7 == 0:
                ops.append(("K", x, y, (1, 5, 15)[i % 3]))
            else:
                ops.append(("Q", x - radius, y - radius, x + radius, y + radius))
        else:
            trial = rng.random()
            if trial < 0.52 or len(active) < 10:
                if dormant:
                    item = dormant.pop()
                    box = boxes[item]
                else:
                    item, next_id = next_id, next_id + 1
                    box = boxes[rng.randrange(len(boxes))]
                current[item] = box
                active.append(item)
                ops.append(("I", item, *box))
            else:
                index = rng.randrange(len(active))
                item = active[index]
                if trial < 0.77:
                    dx, dy = rng.randint(-300, 300), rng.randint(-300, 300)
                    a, b, c, d = current[item]
                    box = (a + dx, b + dy, c + dx, d + dy)
                    current[item] = box
                    ops.append(("M", item, *box))
                else:
                    active[index] = active[-1]
                    active.pop()
                    del current[item]
                    ops.append(("D", item))
    return f"{initial} {len(ops)}\n" + "".join(" ".join(map(str, row)) + "\n" for row in ops)


def _call(trace, method, stage):
    program = BIN / "rtree.exe"
    variant, window, threshold = METHODS[method]
    try:
        completed = subprocess.run([str(program), str(trace), variant, stage, str(window), str(threshold)],
                                   timeout=45, check=False, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    if completed.returncode:
        return {"status": "failed", "error": completed.stderr.strip(), "exit": completed.returncode}
    return {"status": "ok", **json.loads(completed.stdout)}


def summarize(rows, validations):
    results = {}
    verified = {(r["region"], r["profile"], r["method"]) for r in validations if r["status"] == "ok"}
    for method in sorted({r["method"] for r in rows}):
        candidate = [r for r in rows if r["method"] == method]
        grouped = {}
        metrics = ("total_seconds", "query_p95_ns", "update_p95_ns")
        for region, profile in sorted({(r["region"], r["profile"]) for r in rows}):
            selected = [r for r in candidate if r["region"] == region and r["profile"] == profile]
            controls = [r for r in rows if r["region"] == region and r["profile"] == profile and r["method"] == "stock-0"]
            if not selected or not controls or not all(r["status"] == "ok" for r in selected + controls):
                continue
            if not {(region, profile, method), (region, profile, "stock-0")} <= verified:
                continue
            grouped.setdefault(region, {})[profile] = {
                metric: statistics.median(r[metric] for r in controls) / statistics.median(r[metric] for r in selected)
                for metric in metrics
            }
        checks = [r for r in validations if r["method"] == method]
        results[method] = {
            "attempts": len(candidate), "timed_successes": sum(r["status"] == "ok" for r in candidate),
            "verified_traces": sum(r["status"] == "ok" for r in checks),
            "verified_queries": sum(r.get("verified_queries", 0) for r in checks),
            "redistributions": sum(r.get("redistributions", 0) for r in checks),
            "opportunities": sum(r.get("opportunities", 0) for r in checks),
            "range_nodes": sum(r.get("range_nodes", 0) for r in checks),
            "range_entry_tests": sum(r.get("range_entry_tests", 0) for r in checks),
            "leaf_value_write_bytes_observed": (
                None if method in ("stock-3", "stock-9")
                else sum(r.get("leaf_value_write_bytes", 0) for r in checks)
            ),
            "by_region_and_profile_vs_stock_0": grouped,
            **{metric: interval(statistics.median(row[metric] for row in region.values())
                                for region in grouped.values()) for metric in metrics},
        }
    return results


def study(args):
    rows, evidence, inputs = [], [], []
    WORK.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="spatial-", dir=WORK) as scratch:
        trace_path = Path(scratch) / "trace.txt"
        for i, region in enumerate(REGIONS[args.cohort]):
            source, source_hash = cached_asset(f"tl_2024_{region}_roads.zip")
            boxes = list(shapefile_boxes(source))
            for j, profile in enumerate(PROFILE):
                # Different regions, rather than operations or repeated timings,
                # are the independent groups for the uncertainty estimate.
                seed = SEED + (4000 if args.cohort == "holdout" else 2000) + 100 * i + j
                selected = random.Random(seed).sample(boxes, min(20_000, len(boxes)))
                trace = make_trace(selected, profile, args.initial, args.operations, seed)
                trace_path.write_text(trace, encoding="ascii", newline="\n")
                info = {"region": region, "profile": profile}
                inputs.append({**info, "seed": seed, "source_sha256": source_hash,
                               "trace_sha256": digest(trace.encode()).hexdigest()})
                for method in args.methods:
                    result = _call(trace_path, method, "verify")
                    evidence.append({**info, "method": method, **result})
                    if result["status"] == "ok" and result["verified_queries"] != result["queries"]:
                        raise RuntimeError("the independent range/nearest replay skipped a query")
                for repeat, method in schedule(args.methods, seed=seed, repeats=args.repeats + 1):
                    measured = _call(trace_path, method, "timed")
                    if repeat:
                        rows.append({**info, "method": method, "repeat": repeat - 1, **measured})
                print(f"{region} {profile} finished", flush=True)
    write_result(args.output, {"metadata": {**metadata(), "boost": "1.92.0", "seed": SEED},
                              "settings": {"cohort": args.cohort, "initial": args.initial, "operations": args.operations,
                                           "methods": args.methods, "method_options": METHODS, "repeats": args.repeats},
                              "inputs": inputs, "summary": summarize(rows, evidence), "verification": evidence, "attempts": rows})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=REGIONS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--methods", choices=METHODS, nargs="+",
                        default=[*CONTROLS, "hindsight", "history", "history-16", "history-64"])
    parser.add_argument("--initial", type=int, default=1600)
    parser.add_argument("--operations", type=int, default=8000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if min(args.initial, args.operations, args.repeats) < 1:
        parser.error("positive initial size, operations, and repeats are required")
    if args.cohort == "holdout" and ("hindsight" in args.methods or sum(m.startswith("history") for m in args.methods) > 1):
        parser.error("holdout uses past queries and at most one frozen history policy")
    if not (BIN / "rtree.exe").is_file():
        parser.error("build the native spatial benchmark first")
    study(args)


if __name__ == "__main__":
    main()
