"""Append-only paired records and statistics for the expanded cohort."""

from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median

import numpy as np

from benchmarks.common import ROOT, metadata
from benchmarks.fetch import sha256


PAPER = ROOT / "paper"
SEED = 923_731


def portable_file(cache: Path, receipt: dict) -> Path:
    path = cache / receipt["file"]
    if sha256(path) != receipt["sha256"]:
        raise ValueError(f"asset differs from the frozen manifest: {path}")
    return path


class Journal:
    """Persist each completed unit so a blocked run can resume at the next input."""

    def __init__(self, path, protocol):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            header = {"type": "metadata", "protocol": protocol, "runtime": metadata()}
            with self.path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(header, allow_nan=False) + "\n")
        header, self.units = load_journal(path)
        if header["protocol"] != protocol:
            raise ValueError(f"protocol differs from the existing journal {path}")
        self.completed = {block["unit"] for block in self.units}
        self.sessions = []

    def add(self, unit, *, attributes, attempts):
        if unit in self.completed:
            raise ValueError(f"duplicate unit {unit}")
        payload = json.dumps({"type": "unit", "unit": unit, "attributes": attributes, "attempts": attempts},
                             allow_nan=False, separators=(",", ":"))
        if not self.sessions:
            record = {"type": "session", "runtime": metadata()}
            self.sessions.append(record)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, allow_nan=False) + "\n")
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(payload + "\n")
        self.completed.add(unit)


def load_journal(path):
    with Path(path).open(encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    if not records or records[0].get("type") != "metadata":
        raise ValueError(f"missing experiment header in {path}")
    units = [record for record in records[1:] if record["type"] == "unit"]
    if len({block["unit"] for block in units}) != len(units):
        raise ValueError(f"duplicate units in {path}")
    header = {**records[0], "sessions": [record["runtime"] for record in records[1:] if record["type"] == "session"]}
    return header, units


def paired_ratio(values, *, seed=SEED):
    """Median unit log-ratio and percentile bootstrap over independent units."""
    ratios = np.asarray(values, dtype=float)
    if not len(ratios):
        return {"n": 0, "estimate": None, "interval95": None}
    if not np.isfinite(ratios).all() or not (ratios > 0).all():
        raise ValueError("paired ratios must be finite and positive")
    log_ratios = np.log(ratios)
    return {**cluster_interval(log_ratios, statistic=np.median, seed=seed, transform=np.exp)}


def cluster_interval(values, *, statistic=np.mean, seed=SEED, transform=float):
    units = np.asarray(list(values), dtype=float)
    if not len(units):
        return {"n": 0, "estimate": None, "interval95": None}
    stat = float(transform(statistic(units)))
    if len(units) < 4:
        return {"n": len(units), "estimate": stat, "interval95": None}
    indices = np.random.default_rng(seed).integers(len(units), size=(20_000, len(units)))
    low, high = np.quantile(statistic(units[indices], axis=1), [0.025, 0.975])
    return {"n": len(units), "estimate": stat, "interval95": [float(transform(low)), float(transform(high))]}


def medians_by(rows, field, key):
    groups = defaultdict(list)
    for row in rows:
        if row.get(field) is not None:
            groups[key(row)].append(row[field])
    return {group: median(values) for group, values in groups.items()}


def finite(value):
    return value if math.isfinite(value) else None
