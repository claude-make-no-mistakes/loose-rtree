# Incremental optimization results

The study uses the previous follow-up's fastest verified path as the control for each workload. The targets were 2× higher throughput and half the delivered wheel patch bytes. The input cohorts were reused for development and paired measurements. Brackets give 95% intervals from resampling independent integrands, counties, matrix patterns, or projects. [Protocol](PROTOCOL.md) · [Reproduction and journals](README.md)

## Adaptive cubature

The ray fit was **2.22× faster [2.14–2.39]** than the active-set control across the 124 jointly verified quadratic-kink conditions. The candidate verified **128/128** conditions. The control verified **124/128**. The cases cover 64 independently seeded integrands in dimensions 2, 4, 6, and 8, with two tolerances per integrand. The median speed ratios by dimension were 1.99×, 2.19×, 2.37×, and 2.68×.

A quadratic has a constant second difference along an equally spaced line. An interior slope jump adds two adjacent deviations. Their sum gives the slope jump times the spacing, and their ratio locates the kink in that interval. Rays through a common centre give signed normal projections and a common offset after choosing the orientation of the hinge. Reversing its orientation adds an affine term to the residual. The implementation checks the fitted model on separate samples, integrates the actual callback residual, and uses the earlier fit when ray recovery or validation fails. Caching the validation matrix and sampling the initial cubature rule in the validation batch reduced overhead in the second implementation.

| Other integrand family | Verified conditions in each mode | False convergences in each mode |
| --- | ---: | ---: |
| Mixed background with a kink | 32/128 | 0 |
| Two kinks | 15/64 | 1 |
| Curved kink | 24/64 | 0 |
| Smooth Gaussian | 33/64 | 0 |
| Oscillatory | 16/64 | 0 |
| Product peak | 36/64 | 1 |
| Narrow peak | 5/64 | 57 |

The extra pilot cost was most visible on narrow peaks, at **0.84×** elapsed-time throughput over all narrow conditions. The quadratic-kink wall-time ratio includes the pilot and callback costs with cached sample geometry. The ray pilot uses 250 points in two dimensions and 549 in eight dimensions, compared with 320 in the control. Callback cost can change the timing tradeoff. [Cubature journal](runs/cubature-v2.jsonl)

## Dynamic R*-trees

The specialized path uses clipped overlap widths and heights, bounded stack storage for subtree scores, and a range traversal that skips per-value tests beneath a fully covered node. The pinned Cartesian implementation preserves the order of overlap accumulation and candidate comparison. All **152** paired traces matched the layout checkpoints and query signatures from the archived control. Each mode independently checked 316,717 primary queries per reinsertion setting. The capacity-64 traces verified 2,265 sampled queries per setting and compared all 83,690 query signatures.

| Replay at capacity 32 | Reinsertion off | 30% reinsertion |
| --- | ---: | ---: |
| All profiles, paired by county | 1.06× [1.04–1.08] | 1.07× [1.07–1.09] |
| Moving hotspot | 1.03× | 1.03× |
| Write-heavy | 1.06× | 1.08× |
| Insert-only | 1.08× | 1.11× |

At capacity 64 the median speed was 1.03× without reinsertion and 1.04× with reinsertion, across four counties. All ratios time replay after initial construction. [Spatial journal](runs/spatial.jsonl)

A four-county probe tested capacities 8, 12, 16, and 24 against 32 with the planar route and reinsertion. Capacity 8 reached 1.18× on insert-only traces and 0.94× on write-heavy traces. Capacity 16 reached 1.10× on the moving-hotspot profile. All 60 verification replays matched the query signatures. The probe allowed layouts to change with capacity. [Capacity probe](runs/spatial-capacity-probe.jsonl)

## Repeated sparse SPD solves

The binary64 factor-update route applies positive diagonal changes before downdates and checks every result. In exact arithmetic the intermediate factor represents the larger of the old and new shifts at each site and stays positive definite. The implementation refactors on a large change, factor update failure, or failed residual check. The mixed route equilibrates the base matrix and uses a binary32 factor for the Woodbury preconditioner. It can make up to three corrections using binary64 residuals and solution updates.

| Method against checked Woodbury solves | 12-request throughput | Verified requests | Fallbacks |
| --- | ---: | ---: | ---: |
| Direct factor updates | 0.97× [0.79–1.07] | 684/684 | 0 |
| Binary32 factor with refinement | 0.81× [0.70–0.85] | 684/684 | 108 |

The control used 108 fallbacks across 684 requests. Direct updates were 10.80× faster on FIDAP/ex15, 3.24× on Oberwolfach/LF10000, and 2.26× on GHS_indef/bloweybq. The ratios include factor setup and all chronological shifts. Both methods use the same AMD permutation, and the summary records timing with the shared ordering charge as well.

Every result passed the normwise backward-error threshold of 10⁻¹¹. The largest manufactured forward error was 1.43% with direct updates and 0.58% with the control, both on LF10000. Factor reuse helped the previous fallback-heavy cases, and the measured forward-error tradeoff matters for those solves. [Sparse journal](runs/sparse-mixed.jsonl)

## ZIP-aware binary deltas

The tuned selector reduced delivered bytes from **888,127 to 611,442** across all 16 wheel pairs, a **31.2%** reduction including wrappers and recipes. The target was at most 444,063 bytes. Every project improved. The largest savings were rpds-py (159,437 bytes), zstandard (57,087 bytes), and lxml (27,147 bytes).

Miniz reproduced rpds-py's original DEFLATE member exactly, allowing the wheel to use the archive transform. Encoding the normalized BSDIFF parse with XZ improved the selected size across the corpus. The new wrapper uses the accompanying decoder and the recorded DEFLATE codec. All 96 timed deliveries and 429 timed candidate round trips reproduced the exact target. Stock bspatch separately verified 285 raw or inner BSDIFF40 patches.

The median project took **4.74× [3.32–5.86]** as long to encode, including the three candidate parses and their compression. Decode time was **0.95× [0.91–1.03]** the control. The best screened rearrangement of x86-64 instruction operands increased encoded size on four of five projects. [Wheel journal](runs/archive.jsonl) · [Windows executable transform screen](runs/pe-screen.txt)

For unwrapped BSDIFF40, taking the smallest patch from the archived hybrid and two alternate compatible encodes saved **22,203 bytes (0.40%)** across 32 wheel or member pairs. Twelve pairs improved. The lxml wheel accounted for 21,456 of the bytes saved. All 64 new patches passed the in-memory decoder and stock bspatch checks. [Compatible patch journal](runs/compatible.jsonl)

## Self-review

All 30 tests passed. The analysis checks the original cubature draws and point caps, the exact archived spatial trace hashes and query signatures, the frozen sparse shift sites and sequences, and the source and target hashes of the wheel pairs. It verifies complete repeat coverage before computing the headline results. [Machine-readable summary and journal hashes](summary.json)
