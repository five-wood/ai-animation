"""Regression tests for diagnostics, rejection persistence and UI stale-export filtering."""
import os
import sys
import json
import tempfile
import unittest
import numpy as np
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.diagnostics import frame_ranges, build_diagnostics
from v2r.geom import Cams
from v2r.solve import triangulate_all, consistency_report
from v2r import pipeline


class DiagnosticsTest(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(frame_ranges([], 24), [])
        self.assertEqual(frame_ranges([0, 1, 1, 0, 1], 10), [
            dict(start_frame=1, end_frame=2, start_seconds=.1, end_seconds=.3),
            dict(start_frame=4, end_frame=4, start_seconds=.4, end_seconds=.5)])

    def test_saved_rejection_and_exam_geometry(self):
        project = os.environ.get("V2R_TEST_PROJECT", "/data/video2rig/projects/lizard")
        rig = json.load(open(os.path.join(project, "rig.json")))
        studio = json.load(open(os.path.join(project, "studio.json")))
        gt = json.load(open(os.path.join(project, "exam/frames/motion_gt.json")))
        uv = np.array([[f[c["name"]]["uvz"] for c in studio["cams"]] for f in gt["frames"]])[..., :2]
        conf = np.ones(uv.shape[:-1]) * .9
        cams = Cams(studio)
        X, res = triangulate_all(cams, uv, conf)
        clean = consistency_report(cams, rig, X, res, conf, uv)
        self.assertTrue(clean["same_move"])
        self.assertLess(clean["median_residual_rel"], 1e-6)
        rd = np.load(os.path.join(project, "out/xiyi/reading.npz"))
        uv = rd["uv_raw"].copy()
        conf = rd["conf"].copy()
        T = len(uv)
        with tempfile.TemporaryDirectory() as tmp:
            # Run the real rejection branch with the reader replaced by the saved real reading.
            for name, value in (("rig.json", rig), ("studio.json", studio)):
                json.dump(value, open(os.path.join(tmp, name), "w"))
            from unittest.mock import MagicMock
            model = MagicMock()
            panels = [[np.zeros((8, 8, 3), dtype=np.uint8)] * 2 for _ in range(T)]
            with patch.object(pipeline, "read_video_frames", return_value=([np.zeros((8, 16, 3), dtype=np.uint8)] * T, 24)), \
                 patch.object(pipeline, "split_views", return_value=(panels[0], [])), \
                 patch.object(pipeline, "load_reader", return_value=(model, {})), \
                 patch.object(pipeline, "run_reader", return_value=(uv, conf, rd["depth"])), \
                 patch.object(pipeline, "make_diagnostic_video") as mv, \
                 patch.object(pipeline, "fit") as solver:
                with self.assertRaisesRegex(SystemExit, "NEEDS_REVIEW"):
                    pipeline.animate(tmp, "saved_real.mp4", name="rejected", split="half", calib_mode="off")
                solver.assert_not_called()
                mv.assert_called_once()
            out = os.path.join(tmp, "out/rejected")
            for f in ("diagnostics.json", "reading.npz", "check.json", "summary.json"):
                self.assertTrue(os.path.isfile(os.path.join(out, f)), f)
            diag = json.load(open(os.path.join(out, "diagnostics.json")))
            self.assertEqual(diag["decision"], "needs_review")
            self.assertEqual(len(diag["keypoints"]), len(rig["keypoints"]))
            self.assertGreater(len(diag["bad_ranges"]), 0)
            self.assertEqual(json.load(open(os.path.join(out, "summary.json")))["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
