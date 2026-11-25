# Loose R*-tree forest

[Paper PDF](output/pdf/latex/spatial.pdf) · [LaTeX source](manuscripts/latex/spatial.tex) · [Markdown draft](manuscripts/spatial.md)

The implementation, focused regression tests, frozen input receipts, and archived benchmark journals for this study are included. Linked combined reports and summaries provide historical context. The PDF's local links resolve from its checked-in location.

## Reproduce

The archived experiments used Python 3.12 and Windows. For native builds, install MSYS2 UCRT64 GCC and its build prerequisites, including bzip2 development files for wheel deltas. The build helper fetches pinned upstream tools and sources. Set `WORKHORSES_UCRT64` if MSYS2 is installed outside the default directory. Run the following from this repository root.

~~~powershell
python -m pip install -r requirements.txt
python -B -m benchmarks.build packages spatial
python -B -m benchmarks.paper_data --restore --inputs-only spatial
python -B -c "from pathlib import Path; from benchmarks.paper_data import check_frozen; check_frozen(Path('followup/inputs-spatial.json'), True, True)"
python -B -m unittest discover -s tests -v
python -B -m benchmarks.twice_spatial --loose --depth 6 --output .research/replay/spatial.jsonl
~~~

Use a fresh output path for each timing comparison. Set the BLAS, OpenMP, MKL, and Rayon thread limits to one. Results and coverage are in [the verified summary](manuscripts/summary.json), and in the study's journals under `manuscripts/runs`. Timings depend on the workstation.
