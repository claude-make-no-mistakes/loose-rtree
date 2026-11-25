import ctypes
import json
from pathlib import Path
import random
import subprocess
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from benchmarks.common import BIN
from benchmarks.spatial import make_trace


def symbolic_fill(a, ordering):
    graph = [set(np.flatnonzero(row)) - {i} for i, row in enumerate(a)]
    nnz, operations = 0, 0
    for pivot in ordering:
        remaining = graph[pivot]
        length = len(remaining) + 1
        nnz += length
        operations += length ** 2
        for vertex in remaining:
            graph[vertex].update(remaining - {vertex})
            graph[vertex].discard(pivot)
        graph[pivot] = set()
    return nnz, operations




@unittest.skipUnless((BIN / "rtree.exe").is_file(), "build the tree with python -m benchmarks.build spatial")
class SpatialTests(unittest.TestCase):
    def test_mixed_replay_verifies_for_both_boost_paths_and_online_policy(self):
        rng = random.Random(971)
        boxes = []
        for _ in range(1200):
            x, y = rng.randrange(80), rng.randrange(40)
            boxes.append((x, y, x + rng.randrange(5), y + rng.randrange(3)))
        trace = make_trace(boxes, "write-heavy", 120, 2500, 17649)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.txt"
            path.write_text(trace, encoding="ascii")
            snapshots = {}
            for method in ("stock-9", "stock-0", "prefix-9", "prefix-0", "pruned-9", "pruned-0",
                           "planar-9", "planar-0", "geometry", "history", "hindsight"):
                args = [str(BIN / "rtree.exe"), str(path), method, "verify", "32", "0"]
                result = subprocess.run(args, check=True, text=True, capture_output=True, timeout=15)
                stats = json.loads(result.stdout)
                # Tree results and its leaf/MBR invariants were checked in the
                # executable against the live map on every query.
                self.assertEqual(stats["verified_queries"], stats["queries"])
                self.assertGreater(stats["queries"], 500)
                if method in ("geometry", "history"):
                    self.assertGreater(stats["redistributions"], 0, method)
                snapshots[method] = stats
            for suffix in ("0", "9"):
                standard, prefix = snapshots[f"stock-{suffix}"], snapshots[f"prefix-{suffix}"]
                pruned = snapshots[f"pruned-{suffix}"]
                self.assertEqual(standard["query_signature"], prefix["query_signature"])
                self.assertEqual(standard["layout_signature"], prefix["layout_signature"])
                self.assertEqual(standard["split_corner_calls"], prefix["split_corner_calls"])
                self.assertGreater(standard["split_corner_calls"], 0)
                # With n=33 and m=10, rescanning uses 14 legal cuts times 31
                # expansions. The prefix/suffix evaluation uses 2 times 22.
                self.assertEqual(standard["split_bound_expansions"], 434 * standard["split_corner_calls"])
                self.assertEqual(prefix["split_bound_expansions"], 44 * prefix["split_corner_calls"])
                # A partial sum can eliminate a child only when its final score
                # cannot improve stock's lexicographic comparison.
                self.assertTrue(pruned["overlap_pruning"])
                self.assertEqual(pruned["query_signature"], standard["query_signature"])
                self.assertEqual(pruned["layout_signature"], standard["layout_signature"])
                self.assertEqual(pruned["overlap_possible"], prefix["overlap_tested"])
                self.assertLess(pruned["overlap_tested"], pruned["overlap_possible"])
                planar = snapshots[f"planar-{suffix}"]
                for field in ("query_signature", "layout_signature", "overlap_tested", "overlap_possible"):
                    self.assertEqual(planar[field], pruned[field])

    def test_planar_mode_falls_back_for_large_coordinate_ranges(self):
        trace = "0 3\nI 1 -1e151 0 -9e150 1\nQ -2e151 -1 0 2\nD 1\n"
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.txt"
            path.write_text(trace, encoding="ascii")
            outcomes = []
            for method in ("pruned-9", "planar-9"):
                args = [str(BIN / "rtree.exe"), str(path), method, "verify", "32", "0"]
                outcomes.append(json.loads(subprocess.run(args, check=True, text=True, capture_output=True,
                                                          timeout=10).stdout))
        for result in outcomes:
            self.assertFalse(result["overlap_pruning"])
            self.assertEqual(result["verified_queries"], 1)
        for field in ("query_signature", "layout_signature"):
            self.assertEqual(outcomes[0][field], outcomes[1][field])

    def test_grid_promotes_a_busy_cell_and_handles_boundary_moves(self):
        rows = ["I 1 0 0 0 0", "I 2 64 64 64 64"]
        rows.extend(f"I {i} 1 1 1 1" for i in range(3, 19))
        # This rectangle's centre is in the right half. Its left overhang
        # must still be found by a range that ends before the middle grid line.
        rows.extend(("I 19 31.6 1 32.4 1.1", "Q 31.61 1.02 31.70 1.08"))
        rows.extend(("Q 1 1 1 1", "K 1 1 9", "M 4 -100 -100 -99 -99", "Q -100 -101 -99 -99",
                     "D 2", "D 8", "K 0 0 100", "Q -101 -101 65 65"))
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.txt"
            path.write_text(f"2 {len(rows)}\n" + "\n".join(rows) + "\n", encoding="ascii")
            results = []
            for mode in ("pruned-9", "grid-6-9", "loose-6-9"):
                args = [str(BIN / "rtree.exe"), str(path), mode, "verify", "32", "0", "8", "1"]
                result = subprocess.run(args, check=True, text=True, capture_output=True, timeout=10)
                results.append(json.loads(result.stdout))
        for forest in results[1:]:
            self.assertEqual(forest["forest_depth"], 6)
            self.assertEqual(forest["verified_queries"], 6)
            self.assertEqual(forest["query_signature"], results[0]["query_signature"])


if __name__ == "__main__":
    unittest.main()
