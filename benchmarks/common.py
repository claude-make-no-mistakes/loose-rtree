"""Measurement and provenance shared by the four experiments."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import random
import statistics
import sys


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".research"
BIN = WORK / "bin"
SEED = 20261123


def schedule(methods, *, seed, repeats):
    rng = random.Random(seed)
    for repeat in range(repeats):
        order = list(methods)
        rng.shuffle(order)
        for method in order:
            yield repeat, method


def source_digest():
    digest = sha256()
    files = sorted(
        path for directory in ("workhorses", "benchmarks", "native")
        for path in (ROOT / directory).rglob("*")
        if path.is_file() and path.suffix in (".py", ".cpp", ".h", ".c", ".hpp", ".json")
    )
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def metadata():
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "cpu": os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()),
        "logical_cpus": os.cpu_count(),
        "python": sys.version.split()[0],
        "source_sha256": source_digest(),
        "thread_limits": {key: os.environ.get(key) for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")},
    }


def write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A failed or interrupted run must not overwrite an earlier record.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")


def interval(values, *, seed=SEED, samples=10000):
    values = list(values)
    if not values:
        return None
    median = statistics.median(values)
    if len(values) < 3:
        return {"n": len(values), "median": median, "interval95": None}
    rng = random.Random(seed)
    replicates = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(samples))
    return {"n": len(values), "median": median, "interval95": [replicates[samples // 40], replicates[-samples // 40 - 1]]}
