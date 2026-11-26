# Loose R*-Tree Forest

A size-aware quadtree directory of dynamically promoted local R*-trees for single-threaded, in-memory two-dimensional rectangle workloads, using conservative subtree bounding boxes for exact range and $k$-nearest-neighbor queries.

- [Paper (PDF)](output/pdf/latex/spatial.pdf)
- [LaTeX source](manuscripts/latex/spatial.tex)
- [Markdown draft](manuscripts/spatial.md)

## Overview

Each in-domain rectangle is assigned to a single directory cell containing its center at the deepest level (up to depth 6) whose cell width and height are at least those of the rectangle (loose quadtree placement). Cells start as flat vectors and promote to Boost.Geometry R*-trees when insertions exceed node capacity. Ancestor nodes maintain live descendant counts and conservative bounding boxes so range pruning, covered-subtree emission, and best-first $k$-nearest-neighbor pruning remain exact under insertions, deletions, and moves.

Across 24 U.S. Census TIGER/Line county road workloads (capacity 32, three 12,000-operation profiles per county) plus four 60,000-operation scaling traces (capacity 64), the loose directory achieved:

- **$3.34\times$** [$3.11\text{--}3.46$] median replay speedup over the optimized pruned single R*-tree at 30% reinsertion (**$2.55\times$** [$2.44\text{--}2.68$] without reinsertion).
- **$1.25\times$** (30% reinsertion) and **$1.26\times$** (reinsertion off) speedup over a strict containing-cell directory in the same run.
- **152/152** trace and reinsertion evaluations matching archived query signatures, with 316,717 primary queries per setting verified by exhaustive scan.

## Repository Layout

- `native/` — C++ implementation (`rtree_forest.hpp`, `rtree_policy.hpp`, `rtree.cpp`).
- `tests/` — Verification test suite (`test_native.py`).
- `benchmarks/` — Native build helper (`build.py`), frozen trace generator/fetcher (`paper_data.py`, `spatial.py`), and timing harness (`twice_spatial.py`).
- `manuscripts/` — LaTeX source (`latex/spatial.tex`), Markdown draft (`spatial.md`), protocol (`PROTOCOL.md`), archived run journal (`runs/spatial.jsonl`), and summary receipts (`summary.json`).
- `followup/`, `doubling/`, `twice/`, `paper/` — Archived input manifests and historical benchmark journals.

## Reproduce

The archived benchmark suite ran on Python 3.12 on Windows with MSYS2 UCRT64 GCC and Boost.Geometry (`rtree.exe`). Set `WORKHORSES_UCRT64` if MSYS2 is installed outside its default path.

```sh
python -m pip install -r requirements.txt
python -B -m benchmarks.build packages spatial
python -B -m benchmarks.paper_data --restore --inputs-only spatial
python -B -c "from pathlib import Path; from benchmarks.paper_data import check_frozen; check_frozen(Path('followup/inputs-spatial.json'), True, True)"
python -B -m unittest discover -s tests -v
python -B -m benchmarks.twice_spatial --loose --depth 6 --output .research/replay/spatial.jsonl
```

For single-threaded timing comparisons, set BLAS, OpenMP, and MKL thread limits to `1` and use a fresh output path. Archived measurements and SHA-256 receipts are in [manuscripts/summary.json](manuscripts/summary.json) and [manuscripts/runs/spatial.jsonl](manuscripts/runs/spatial.jsonl).
