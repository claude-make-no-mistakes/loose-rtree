# A size-aware directory of R*-trees for dynamic rectangle workloads

## Abstract

An R*-tree pays for overlap minimization and reinsertion on the update path. We study a directory that routes each planar rectangle by its size and centre and searches with conservative subtree bounds. On traces from 24 U.S. counties, the loose directory reached 3.34× the median replay speed of an optimized single R*-tree with 30% reinsertion and 1.25× that of a strict containing-cell directory. The moving-hotspot profile reached 1.85× against the single tree, compared with 6.48× on insert-only traces. All 152 trace and reinsertion comparisons preserved the archived query signatures. [Extension results](summary.json) · [Protocol](PROTOCOL.md)

## Motivation and design

Beckmann and colleagues use area, margin and overlap to guide R*-tree organization. Maintaining that organization has a cost even in an in-memory two-dimensional index. Our control already computes split bounds with prefix and suffix passes and abandons overlap accumulations once they cannot win. A planar specialization preserved stock query and layout signatures but improved median replay speed by only 1.07× with reinsertion. We therefore changed where insertion and splitting take place. [Original R*-tree paper](https://people.csail.mit.edu/tdanford/6830papers/beckmann-r-star-tree.pdf) · [Topology-preserving path](../doubling/RESULTS.md)

The directory fixes its grid from the initial insertion prefix. An in-grid rectangle has one owner. In strict mode that owner is the deepest cell containing the entire rectangle. Loose mode uses the cell containing its centre at the deepest level where the cell is at least as wide and as tall as the rectangle. A rectangle can extend outside this cell. Out-of-domain rectangles use the root. Each owner starts with a flat vector and promotes it to a local R*-tree when an insertion would exceed capacity. A move removes the old rectangle and inserts the new one. Size-dependent placement has prior art in loose quadtrees for moving objects. Our experiment couples this rule to promoted R*-tree shards on interleaved real-road-box traces. [Samet, Sankaranarayanan and Auerbach](https://www.cs.umd.edu/~hjs/pubs/sigmod13-header.pdf) · [Directory implementation](../native/rtree_forest.hpp)

Each directory node maintains a count and a bounding rectangle for its live descendants. After deletion the bound may remain larger than necessary. This can increase search work. It preserves the containment property required for pruning. A range query skips any subtree with a disjoint bound. A nearest query uses squared point-to-bound distance as a lower bound and maintains the best $k$ distances observed. The range contract covers IDs with inclusive boundary intersection. The nearest contract permits different IDs at the same distance and requires the correct distance multiset.

## Workload and verification

The primary inputs comprise three deterministic 12,000-operation replays for each of 24 counties, at capacity 32. Four larger write-heavy traces have 60,000 operations and capacity 64. Tests cover insertion, deletion, motion, intersection and nearest queries. We replay the same text and verify its digest for every method. Within each county we sum the median replay times of the three profiles before computing the paired ratio. We report build-inclusive times separately. Intervals use the county as the resampling unit. [Trace generator and manifests](../followup/README.md) · [2024 Census TIGER/Line roads](https://www2.census.gov/geo/tiger/TIGER2024/ROADS/)

At verification checkpoints the runner enumerates live entries, checks bounds and counts, and inspects the occupancy and levels of promoted R*-trees. Primary queries have a separate exhaustive scan over the active rectangles. The larger traces scan a sample and compare every query signature to its frozen counterpart. Each reinsertion setting covers 316,717 primary queries and 83,690 scaling signatures, including 2,265 independently scanned scaling queries. [Replay and verification code](../native/rtree.cpp)

## Results and interpretation

In the strict forest study, capacity-32 speed ratios against the pruned single tree were 2.01× [1.94–2.09] with reinsertion off and 2.65× [2.44–2.82] with 30% reinsertion. Charging the initial build gave 2.21× and 3.01×. The smaller hotspot gain suggested that rectangle placement near cell boundaries deserved a direct comparison. [Strict forest results](../twice/summary.json)

| Loose placement, capacity 32 | Reinsertion off | 30% reinsertion |
| --- | ---: | ---: |
| Replay vs. pruned single tree | 2.55× [2.44–2.68] | 3.34× [3.11–3.46] |
| Replay vs. strict forest in the same run | 1.26× | 1.25× |
| Replay and initial build vs. single tree | 2.77× | 3.72× |
| Moving hotspot vs. single tree | 1.72× | 1.85× |
| Write-heavy vs. single tree | 2.67× | 3.17× |
| Insert-only vs. single tree | 3.98× | 6.48× |

At capacity 64 the four scaling replays reached median speedups of 2.72× and 3.28× over the single tree with reinsertion off and on, respectively. Moving a rectangle across a grid line no longer forces it into an ancestor solely because it straddles the boundary. This placement effect is a plausible explanation for the observed gain. The benchmark measures whole replays and cannot attribute a fraction of the saving to that mechanism alone. [Raw replays and independent query checks](runs/spatial.jsonl)

The evidence concerns single-threaded in-memory rectangles derived from road geometry. The replay checks coordinate magnitudes across the trace before enabling its specialized planar arithmetic. The directory itself draws its bounds from the initial insertions. Disk traversal, a shifting geographic extent and application-specific nearest-neighbour tie-breaking could change the cost or the required output. A cohort with different geometry distributions would test those dependencies.
