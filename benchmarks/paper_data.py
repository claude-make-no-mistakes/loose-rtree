"""Select and hash the independent public workloads for the expanded studies."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

from scipy.io import loadmat

from benchmarks.common import ROOT, WORK
from benchmarks.fetch import REGIONS, SPARSE, sha256


CACHE = WORK / "paper-assets"
PAPER = ROOT / "paper"
CATALOG = "https://www.cise.ufl.edu/research/sparse/files/ss_index.mat"
CATALOG_SHA = "42e348196d112ef9a118d2ed0960d771e393264e7a9206139431a6428bb77809"
POPULATION = ("https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/"
              "counties/totals/co-est2024-alldata.csv")
ROADS = "https://www2.census.gov/geo/tiger/TIGER2024/ROADS"
FLORIDA = "https://www.cise.ufl.edu/research/sparse/MM"
PAIRS = (
    ("pip", "24.3.1", "25.0.1"), ("packaging", "24.2", "25.0"),
    ("requests", "2.32.3", "2.32.4"), ("httpx", "0.27.2", "0.28.1"),
    ("urllib3", "2.3.0", "2.4.0"), ("click", "8.1.8", "8.2.1"),
    ("rich", "13.9.4", "14.0.0"), ("jinja2", "3.1.5", "3.1.6"),
    ("attrs", "24.3.0", "25.1.0"), ("itsdangerous", "2.1.2", "2.2.0"),
    ("pytest", "8.3.5", "8.4.0"), ("filelock", "3.17.0", "3.18.0"),
    ("pygments", "2.18.0", "2.19.1"), ("idna", "3.10", "3.11"),
    ("orjson", "3.10.18", "3.11.0"), ("pydantic_core", "2.27.2", "2.33.2"),
    ("markupsafe", "3.0.2", "3.0.3"), ("msgpack", "1.1.0", "1.1.1"),
    ("pyyaml", "6.0.2", "6.0.3"), ("brotli", "1.1.0", "1.2.0"),
    ("kiwisolver", "1.4.8", "1.4.9"), ("regex", "2024.11.6", "2025.7.29"),
    ("cffi", "1.17.1", "2.0.0"), ("psutil", "6.1.1", "7.0.0"),
)


def obtain(name, url, digest=None, signature=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    temporary = path.with_suffix(path.suffix + ".part")
    if not path.is_file():
        request = Request(url, headers={"User-Agent": "workhorse-research/2.0"})
        try:
            with urlopen(request, timeout=35) as stream, temporary.open("wb") as output:
                total = 0
                while block := stream.read(1 << 20):
                    total += len(block)
                    if total > 128 << 20:
                        raise ValueError("paper asset exceeded the download bound")
                    output.write(block)
            if digest is not None and sha256(temporary) != digest:
                raise ValueError(f"download digest mismatch for {name}")
            if signature:
                with temporary.open("rb") as stream:
                    if stream.read(len(signature)) != signature:
                        raise ValueError(f"unexpected file signature for {name}")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    observed = sha256(path)
    if digest is not None and observed != digest:
        raise ValueError(f"cached digest mismatch for {name}")
    return {"file": name, "url": url, "sha256": observed, "bytes": path.stat().st_size}


def rank(*fields):
    from hashlib import sha256 as h
    return h(("workhorses-paper-3691|" + "|".join(map(str, fields))).encode()).hexdigest()


def sparse_cohort(catalog):
    pub = loadmat(catalog, simplify_cells=True)["ss_index"]
    excluded = {a.split("/")[0] for cohort in SPARSE.values() for kind in cohort.values() for a in kind}
    bounds = {"spd": (800, 5000, 15000, 120001), "lu": (800, 4000, 12000, 45001)}
    cohorts = []
    for kind, limits in bounds.items():
        visited = set()
        for level in (2, 1, 0):
            units = {}
            for i, (group, name) in enumerate(zip(pub["Group"], pub["Name"])):
                n, size = int(pub["nrows"][i]), int(pub["nnz"][i])
                if group in excluded or group in visited or not limits[level] <= n < limits[level + 1]:
                    continue
                if n != pub["ncols"][i] or not pub["isReal"][i] or pub["isBinary"][i] or size > 900_000:
                    continue
                if not (0 < pub["amd_lnz"][i] <= 8_000_000 and 0 < pub["amd_flops"][i] <= 3_000_000_000):
                    continue
                symmetry = pub["numerical_symmetry"][i]
                if kind == "spd" and (symmetry != 1 or not pub["posdef"][i]):
                    continue
                if kind == "lu" and (symmetry >= 1 or pub["sprank"][i] != n):
                    continue
                candidate = {"matrix": f"{group}/{name}", "group": group, "kind": kind,
                             "size_stratum": level, "catalog_order": n, "catalog_nnz": size}
                units.setdefault(group, []).append(candidate)
            chosen = sorted(units, key=lambda group: rank("group", kind, level, group))[:8]
            for group in chosen:
                visited.add(group)
                item = min(units[group], key=lambda r: rank("matrix", r["matrix"]))
                cohorts.append(item)
    return cohorts


def _sparse():
    catalog = obtain("ss_index.mat", CATALOG, CATALOG_SHA, b"MATLAB 5.0 MAT")
    cohort = sparse_cohort(CACHE / catalog["file"])

    def fetch(row):
        matrix = row["matrix"]
        file = matrix.replace("/", "__") + ".tar.gz"
        try:
            return {**row, **obtain(file, f"{FLORIDA}/{matrix}.tar.gz", signature=b"\x1f\x8b")}
        except (OSError, ValueError) as error:
            return {**row, "error": str(error)}

    with ThreadPoolExecutor(8) as executor:
        return {"catalog": catalog, "cases": list(executor.map(fetch, cohort))}


def spatial_cohort(population, *, extra_excluded=(), namespace="county", per_stratum=3):
    records = list(csv.DictReader(io.StringIO(population.read_bytes().decode("cp1252"))))
    exclude = {value for cohort in REGIONS.values() for value in cohort}
    exclude.update(extra_excluded)
    universe = []
    for row in records:
        if row["SUMLEV"] != "050" or row["REGION"] not in ("1", "2", "3", "4"):
            continue
        code = row["STATE"].zfill(2) + row["COUNTY"].zfill(3)
        count = int(row["POPESTIMATE2024"])
        if code in exclude or count < 75_000:
            continue
        universe.append({"county": code, "label": f"{row['CTYNAME']}, {row['STNAME']}",
                         "region": int(row["REGION"]), "population": count, "urban": count >= 250_000})
    chosen = []
    for reg in range(1, 5):
        for urban in (False, True):
            eligible = [r for r in universe if (r["region"], r["urban"]) == (reg, urban)]
            selected = sorted(eligible, key=lambda r: rank(namespace, r["county"]))[:per_stratum]
            for i, row in enumerate(selected):
                chosen.append({**row, "scale_case": urban and not i})
    if len(chosen) != 8 * per_stratum:
        raise ValueError(f"population catalog yielded only {len(chosen)} counties")
    return chosen


def _spatial(*, extra_excluded=(), namespace="county", per_stratum=3):
    catalog = obtain("co-est2024-alldata.csv", POPULATION, signature=b"SUMLEV,")
    cohort = spatial_cohort(CACHE / catalog["file"], extra_excluded=extra_excluded,
                            namespace=namespace, per_stratum=per_stratum)

    def fetch(row):
        name = f"tl_2024_{row['county']}_roads.zip"
        url = f"{ROADS}/{name}"
        try:
            return {**row, **obtain(name, url, signature=b"PK")}
        except (OSError, ValueError) as error:
            try:
                return {**row, **obtain(name, url + "?v=2024", signature=b"PK"), "first_attempt_error": str(error)}
            except (OSError, ValueError) as recovery:
                return {**row, "error": str(recovery), "first_attempt_error": str(error)}

    with ThreadPoolExecutor(6) as executor:
        return {"catalog": catalog, "cases": list(executor.map(fetch, cohort))}


def _wheel_choices(data):
    wanted = ("cp312-cp312-win_amd64", "cp312-abi3-win_amd64", "cp311-abi3-win_amd64",
              "cp310-abi3-win_amd64", "cp39-abi3-win_amd64", "cp38-abi3-win_amd64",
              "cp37-abi3-win_amd64", "py3-none-any", "py2.py3-none-any")
    candidates = {}
    for asset in data["urls"]:
        filename = asset["filename"]
        if not filename.endswith(".whl") or asset["yanked"] or asset["size"] > 6 << 20:
            continue
        tag = "-".join(filename.removesuffix(".whl").split("-")[-3:])
        if tag in wanted:
            candidates[tag] = asset
    return candidates, wanted


def _pypi(project, version):
    name = f"pypi-{project}-{version}.json"
    receipt = obtain(name, f"https://pypi.org/pypi/{project}/{version}/json", signature=b"{")
    return json.loads((CACHE / name).read_text(encoding="utf-8")), receipt


def _delta(pairs=PAIRS):
    def fetch(pair):
        project, first, last = pair
        row = {"project": project, "before": first, "after": last}
        try:
            left, left_meta = _pypi(project, first)
            right, right_meta = _pypi(project, last)
            a, order = _wheel_choices(left)
            b, _ = _wheel_choices(right)
            tag = next((key for key in order if key in a and key in b), None)
            if tag is None:
                raise ValueError("no common supported platform-and-ABI wheel tag")
            files = [obtain(item["filename"], item["url"], item["digests"]["sha256"], b"PK") for item in (a[tag], b[tag])]
            return {**row, "tag": tag, "release_metadata": [left_meta, right_meta], "files": files}
        except (OSError, ValueError) as error:
            return {**row, "error": str(error)}

    with ThreadPoolExecutor(8) as executor:
        return {"cases": list(executor.map(fetch, pairs))}


def frozen_receipts(document):
    if isinstance(document, dict):
        if {"file", "url", "sha256", "bytes"} <= document.keys():
            yield document
        for value in document.values():
            yield from frozen_receipts(value)
    elif isinstance(document, list):
        for value in document:
            yield from frozen_receipts(value)


def check_frozen(path, restore, inputs_only):
    document = json.loads(path.read_text(encoding="utf-8"))
    if inputs_only:
        cases = [row for row in document["cases"] if "error" not in row]
        document = [row.get("files", row) for row in cases]
    receipts = list(frozen_receipts(document))

    def check(receipt):
        file = CACHE / receipt["file"]
        if restore and not file.is_file():
            obtain(receipt["file"], receipt["url"], receipt["sha256"])
        if file.stat().st_size != receipt["bytes"] or sha256(file) != receipt["sha256"]:
            raise ValueError(f"frozen asset differs from its receipt: {file}")

    with ThreadPoolExecutor(6) as executor:
        list(executor.map(check, receipts))
    print(f"{path.name}: verified {len(receipts)} frozen assets", flush=True)


def main():
    readers = {"sparse": _sparse, "spatial": _spatial, "delta": _delta}
    parser = argparse.ArgumentParser(description=__doc__)
    frozen = parser.add_mutually_exclusive_group()
    frozen.add_argument("--verify", action="store_true", help="check existing assets against the frozen manifests")
    frozen.add_argument("--restore", action="store_true", help="fetch missing assets with their frozen hashes")
    parser.add_argument("--inputs-only", action="store_true", help="skip ancillary catalog and release metadata receipts")
    parser.add_argument("lanes", nargs="+", choices=readers)
    args = parser.parse_args()
    if args.inputs_only and not (args.verify or args.restore):
        parser.error("--inputs-only requires --verify or --restore")
    for lane in args.lanes:
        path = PAPER / f"inputs-{lane}.json"
        if args.verify or args.restore:
            check_frozen(path, args.restore, args.inputs_only)
            continue
        if path.exists():
            raise SystemExit(f"{path} is already frozen. Use its recorded inputs.")
        table = readers[lane]()
        with path.open("x", encoding="utf-8") as stream:
            json.dump(table, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        failures = [row for row in table["cases"] if "error" in row]
        print(lane, len(table["cases"]) - len(failures), "of", len(table["cases"]), "available", flush=True)
        for row in failures:
            print(row, flush=True)


if __name__ == "__main__":
    main()
