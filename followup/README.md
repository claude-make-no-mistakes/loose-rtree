# Reproduce the follow-up experiments

The [follow-up results](RESULTS.md) extend the [original report](../paper/REPORT.md). The [protocol](PROTOCOL.md) identifies each comparator, correctness requirement, and independent unit. [Machine-readable results](summary.json)

| Study | Inputs | Raw results |
| --- | --- | --- |
| Cubature | Fresh seeded scalar integrands. The journal records the dimensions, families, tolerances, and seed for every draw. | [Cubature](runs/cubature.jsonl) |
| Spatial indexing | 24 new Census counties, with four prespecified larger traces. [Input receipts](inputs-spatial.json) | [Spatial indexing](runs/spatial.jsonl) |
| Sparse factorization | The earlier study's 19 SPD and 24 nonsymmetric patterns, with new seeded numerical sequences. [Matrix receipts](../paper/inputs-sparse.json) | [Sparse factorization](runs/sparse.jsonl) |
| Binary delta | 16 new release projects, including whole wheels and available common members. [Input receipts](inputs-delta.json) | [Binary delta](runs/delta.jsonl) |
| Archive-aware delta | The same 16 whole wheels. | [Archive-aware delta](runs/archive.jsonl) · [Summary](archive-summary.json) |

Use the pinned Python dependencies and Windows UCRT64 setup in the [main reproduction guide](../README.md). The two `--fetch` commands restore frozen files and verify their hashes when the follow-up manifests already exist. Native packages, public input caches, and scratch outputs live under `.research`.

```powershell
$py = 'C:\Users\Achita\.codex\tools\science\Scripts\python.exe'
$env:OPENBLAS_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$env:RAYON_NUM_THREADS = '1'

& $py -B -m benchmarks.fetch tools
& $py -B -m benchmarks.build packages cuba delta sparse paramd spatial
& $py -B -m benchmarks.followup_spatial --fetch
& $py -B -m benchmarks.followup_delta --fetch
& $py -B -m benchmarks.paper_data --restore --inputs-only sparse
& $py -B -m unittest discover -s tests -v

& $py -B -m benchmarks.followup_spatial --output .research/followup-replay/spatial.jsonl
& $py -B -m benchmarks.followup_cubature --output .research/followup-replay/cubature.jsonl
& $py -B -m benchmarks.followup_sparse --output .research/followup-replay/sparse.jsonl
& $py -B -m benchmarks.followup_delta --output .research/followup-replay/delta.jsonl
& $py -B -m benchmarks.followup_analysis --runs .research/followup-replay --output .research/followup-replay/summary.json
& $py -B -m benchmarks.followup_archive --output .research/followup-replay/archive.jsonl --summary .research/followup-replay/archive-summary.json
```

Run timing studies serially. To recompute the archived summary, run `python -B -m benchmarks.followup_analysis` from the repository root. The summary stores the raw journal SHA-256 digests and stage settings. The analyzer rejects probe journals and checks the expected cohorts, caps, and stated invariants. Timing confidence intervals resample the independent input units with 20,000 fixed-seed bootstrap draws.

Regenerate the [summary figure](figures/results.pdf) with `python -B -m benchmarks.followup_figures` in the reporting environment defined by [the reporting requirements](../paper/report-requirements.txt). The figure uses both archived summaries.

## Implementation entry points

- `workhorses.cubature.integrate(..., mode="blind-linear")` attempts the active-set fits before the cached nonlinear fit. `Hinge.integral()` accumulates exact integer powers for every mode.
- `rtree.exe` accepts `pruned-0` and `pruned-9` for matched prefix/suffix splitting and overlap pruning. The numeric guard enables pruning on supported Cartesian coordinate ranges.
- `FixedPatternCholesky`, `FixedPatternLU`, and `DiagonalShiftFamily` live in `workhorses.sparse`. Give a plan canonical CSC values in the original order. For Cholesky, pass the upper triangle. Use the context managers to release native state deterministically.
- `workhorses.delta.optimize` uses the local boundary rewrite. `workhorses.delta_hybrid.combine` mixes two verified BSDIFF40 parses and selects on total compressed patch length.
- `workhorses.delta_archive.normalize` prepares a ZIP-aware transform and recipe for a BSDIFF40 encoder. `apply_archive_patch` accepts both its wrapped patches and raw BSDIFF40. Wrapped patches require the recorded zlib runtime.
