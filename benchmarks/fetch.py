"""Fetch pinned public baselines and datasets into a workspace-local cache."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".research" / "downloads"
MSYS = "https://mirrors.dotsrc.org/msys2/mingw/ucrt64"
MSYS_PREFIX = "mingw-w64-ucrt-x86_64"
ASSETS = {
    "boost.tar.zst": (f"{MSYS}/{MSYS_PREFIX}-boost-1.92.0-4-any.pkg.tar.zst",
                     "2f9935335e17f9930cba7e93a8db95bf699df2dd1c250cffe6dce89c7ea70154"),
    "suitesparse.tar.zst": (f"{MSYS}/{MSYS_PREFIX}-suitesparse-7.14.1-1-any.pkg.tar.zst",
                           "3f771546751a3e8a46b47c589bfc9ad82278ce6c88c55b3a306526f0436b05ad"),
    "openblas.tar.zst": (f"{MSYS}/{MSYS_PREFIX}-openblas-0.3.34-1-any.pkg.tar.zst",
                         "d9e14c9440d9aa70b6d09cdf0b8117995d181ced9c3d233ad7b62a907982add5"),
    "metis.tar.zst": (f"{MSYS}/{MSYS_PREFIX}-metis-5.1.0-7-any.pkg.tar.zst",
                      "7c3955943645ec701076cc49f72facbdb9361e2072a82d51711f5e881cb13673"),
    "amd_2.c": ("https://raw.githubusercontent.com/DrTimothyAldenDavis/SuiteSparse/v7.14.1/AMD/Source/amd_2.c", None),
    "amd_1.c": ("https://raw.githubusercontent.com/DrTimothyAldenDavis/SuiteSparse/v7.14.1/AMD/Source/amd_1.c", None),
    "amd_order.c": ("https://raw.githubusercontent.com/DrTimothyAldenDavis/SuiteSparse/v7.14.1/AMD/Source/amd_order.c", None),
    "amd_internal.h": ("https://raw.githubusercontent.com/DrTimothyAldenDavis/SuiteSparse/v7.14.1/AMD/Include/amd_internal.h", None),
    "Cuba-4.2.2.tar.gz": ("https://feynarts.de/cuba/Cuba-4.2.2.tar.gz", None),
    "bsdiff-4.3.tar.gz": ("https://deb.debian.org/debian/pool/main/b/bsdiff/bsdiff_4.3.orig.tar.gz", None),
    "hdiffpatch.zip": ("https://github.com/sisong/HDiffPatch/releases/download/v5.1.3/hdiffpatch_v5.1.3_bin_windows64.zip",
                       "77f141386e5d8f785c1c846e10fbbc19b6c05aa00e3f59cc44670fb3f0e2ae94"),
    "xdelta.zip": ("https://github.com/jmacd/xdelta/releases/download/v3.2.2/xdelta3-3.2.2-windows-x86_64.zip", None),
    "bsdiff6.zip": ("https://codeload.github.com/Julian-Rederle/bsdiff6/zip/25b63ab56330b42ac6cc6718a9a29d938dc79a7a", None),
    "paramd.zip": ("https://api.github.com/repos/PASSIONLab/ParAMD/zipball/3d3cc22ace088d066dfac273b5c528e536f1faa4", None),
    "openmp.tar.zst": (f"{MSYS}/{MSYS_PREFIX}-llvm-openmp-22.1.8-1-any.pkg.tar.zst",
                       "0951813aa1744f155771c76928476e29c445f9a12d4eb8afe7cead3f09c6142c"),
}
RUST_VERSION = "1.94.0"
RUST_TARGET = "x86_64-pc-windows-gnu"
RUST = tuple(f"{part}-{RUST_VERSION}-{RUST_TARGET}.tar.xz" for part in ("cargo", "rustc", "rust-std"))
for name in RUST:
    ASSETS[name] = (f"https://static.rust-lang.org/dist/{name}", None)

SPARSE = {
    "development": {"spd": ("HB/bcsstk14", "Boeing/ct20stif"),
                    "lu": ("HB/west0479", "HB/orsirr_1")},
    "holdout": {"spd": ("Williams/cant", "Schmid/thermal1", "GHS_psdef/vanbody"),
                "lu": ("Bomhof/circuit_3", "Rajat/rajat04", "Grund/b1_ss")},
}
REGIONS = {"development": ("36061", "25025"), "holdout": ("53033", "17031", "06075", "24510")}
TOOLS = tuple(name for name in ASSETS if name not in RUST)

for cohort in SPARSE.values():
    for matrix in cohort["spd"] + cohort["lu"]:
        filename = matrix.split("/")[-1] + ".tar.gz"
        ASSETS[filename] = (f"https://www.cise.ufl.edu/research/sparse/MM/{matrix}.tar.gz", None)

for county in REGIONS["development"] + REGIONS["holdout"]:
    filename = f"tl_2024_{county}_roads.zip"
    ASSETS[filename] = (f"https://www2.census.gov/geo/tiger/TIGER2024/ROADS/{filename}", None)

DELTA = {
    "development": (
        ("zstd", "v1.5.5", "v1.5.6", "zstd-win64.zip", "release"),
        ("fmt", "10.2.1", "11.0.2", ".zip", "source"),
    ),
    "holdout": (
        ("jq", "jq-1.7.1", "jq-1.8.1", "jq-windows-amd64.exe", "release"),
        ("lz4", "v1.9.4", "v1.10.0", "win64.zip", "release"),
        ("libpng", "v1.6.46", "v1.6.50", ".zip", "source"),
        ("chinook", "v1.4.4", "v1.4.5", "Chinook_Sqlite.sqlite", "release"),
    ),
}


def delta_files() -> dict[str, list[tuple[str, str, str]]]:
    repos = {"zstd": "facebook/zstd", "fmt": "fmtlib/fmt", "jq": "jqlang/jq", "lz4": "lz4/lz4",
             "libpng": "pnggroup/libpng", "chinook": "lerocha/chinook-database"}
    files = {}
    for cohort, pairs in DELTA.items():
        files[cohort] = []
        for project, before, after, asset, kind in pairs:
            names = []
            for version in (before, after):
                extension = ".zip" if kind == "source" else Path(asset).suffix
                name = f"{project}-{version}{extension}"
                repo = repos[project]
                if kind == "source":
                    url = f"https://codeload.github.com/{repo}/zip/refs/tags/{version}"
                else:
                    basename = {
                        "zstd": f"zstd-{version}-win64.zip",
                        "lz4": f"lz4_win64_{version.replace('.', '_')}.zip",
                    }.get(project, asset)
                    url = f"https://github.com/{repo}/releases/download/{version}/{basename}"
                ASSETS[name] = (url, None)
                names.append(name)
            files[cohort].append((project, *names))
    return files


DELTA_FILES = delta_files()


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def fetch(name: str) -> dict:
    url, digest = ASSETS[name]
    lockfile = ROOT / "benchmarks" / "data-lock.json"
    if digest is None and lockfile.exists():
        digest = json.loads(lockfile.read_text(encoding="utf-8")).get(name)
    dst = CACHE / name
    if not dst.exists():
        part = dst.with_suffix(dst.suffix + ".part")
        try:
            request = Request(url, headers={"User-Agent": "workhorse-research/1.0"})
            with urlopen(request, timeout=45) as response, part.open("wb") as output:
                total = 0
                while block := response.read(1 << 20):
                    total += len(block)
                    if total > 256 << 20:
                        raise ValueError("public input exceeds the 256 MiB download limit")
                    output.write(block)
            if digest and sha256(part) != digest:
                raise ValueError("download checksum differs from the lock")
            # Response pages and incomplete artifacts are rejected before caching.
            with part.open("rb") as stream:
                prefix = stream.read(8)
            signature = {".zip": b"PK", ".gz": b"\x1f\x8b", ".exe": b"MZ", ".zst": b"\x28\xb5\x2f\xfd",
                         ".xz": b"\xfd7zXZ", ".sqlite": b"SQLite f"}
            if dst.suffix in signature and not prefix.startswith(signature[dst.suffix]):
                raise ValueError(f"unexpected {dst.suffix} signature {prefix!r}")
            part.replace(dst)
        finally:
            part.unlink(missing_ok=True)
    observed = sha256(dst)
    if digest and observed != digest:
        raise ValueError(f"cached checksum differs from the lock for {name}")
    return {"name": name, "url": url, "sha256": observed, "bytes": dst.stat().st_size}


def cached_asset(name: str) -> tuple[Path, str]:
    path = CACHE / name
    if not path.is_file():
        raise FileNotFoundError(f"download {name} with python -m benchmarks.fetch first")
    return path, fetch(name)["sha256"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("groups", nargs="+", metavar="GROUP", help="tools, delta, spatial, sparse, or an individual asset name")
    args = parser.parse_args()
    groups = {
        "tools": TOOLS,
        "rust": RUST,
        "delta": tuple(name for pairs in DELTA_FILES.values() for _, *names in pairs for name in names),
        "spatial": tuple(name for name in ASSETS if name.startswith("tl_")),
        "sparse": tuple(matrix.split("/")[-1] + ".tar.gz" for cohort in SPARSE.values() for group in cohort.values() for matrix in group),
    }
    names = list(dict.fromkeys(name for group in args.groups for name in groups.get(group, (group,))))
    if missing := set(names) - ASSETS.keys():
        parser.error(f"unknown asset: {', '.join(sorted(missing))}")
    CACHE.mkdir(parents=True, exist_ok=True)
    failed = False
    with ThreadPoolExecutor(6) as executor:
        results = {name: executor.submit(fetch, name) for name in names}
        for name, future in results.items():
            try:
                receipt = future.result()
            except Exception as exc:
                receipt = {"name": name, "url": ASSETS[name][0], "error": str(exc)}
                failed = True
            print(json.dumps(receipt), flush=True)
    raise SystemExit(int(failed))


if __name__ == "__main__":
    main()
