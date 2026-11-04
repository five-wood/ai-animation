"""Unit tests for the read-time averaging (TTA) geometry, the front-view axis weights, off-axis keypoints in
the FK, and the smear augmentation.  No model, no GPU needed.

    /data/video2rig/.venv/bin/python tools/test_reader_tta.py
"""
from pathlib import Path
import random
import sys
import unittest

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2r.read import tta_views, _copy_affine, _back_grid  # noqa: E402
from v2r.solve import view_axis_weights  # noqa: E402
from v2r.data import smear_segment  # noqa: E402


class TTAGeometry(unittest.TestCase):
    def test_first_copy_is_identity_and_deterministic(self):
        c = tta_views(8)
        self.assertEqual(c[0], (1.0, 0.0, 0.0))
        self.assertEqual(len(c), 8)
        self.assertEqual(c, tta_views(8))
        self.assertEqual(tta_views(1), [(1.0, 0.0, 0.0)])

    def test_back_grid_recovers_a_shifted_peak(self):
        """A heatmap peak seen by a shifted/zoomed copy, sampled back with _back_grid, lands on the base
        cell of the true point (within half a cell)."""
        W, H, hw, hh = 448, 504, 128, 144
        x0, y0 = 201.3, 260.7  # true point in base net-input pixels
        for s, dx, dy in tta_views(8)[1:]:
            A = _copy_affine(W, H, s, dx, dy)
            xc, yc = A[0, 0] * x0 + A[0, 2], A[1, 1] * y0 + A[1, 2]  # where the copy sees it
            ys, xs = np.mgrid[0:hh, 0:hw]
            cx, cy = (xs + 0.5) * W / hw, (ys + 0.5) * H / hh
            heat = np.exp(-((cx - xc) ** 2 + (cy - yc) ** 2) / (2 * 6.0 ** 2)).astype(np.float32)
            t = torch.from_numpy(heat)[None, None]
            grid = _back_grid(W, H, hw, hh, A, "cpu")
            back = torch.nn.functional.grid_sample(t, grid, mode="bilinear", padding_mode="zeros",
                                                   align_corners=False)[0, 0].numpy()
            iy, ix = np.unravel_index(np.argmax(back), back.shape)
            bx, by = (ix + 0.5) * W / hw, (iy + 0.5) * H / hh
            self.assertLess(abs(bx - x0), 0.6 * W / hw + 1e-6, (s, dx, dy))
            self.assertLess(abs(by - y0), 0.6 * H / hh + 1e-6, (s, dx, dy))


class AxisWeights(unittest.TestCase):
    def test_default_all_ones(self):
        studio = {"cams": [{"name": "front"}, {"name": "side"}]}
        self.assertTrue(torch.equal(view_axis_weights(studio), torch.ones(2, 2)))

    def test_front_vertical_only(self):
        studio = {"cams": [{"name": "front"}, {"name": "side"}]}
        w = view_axis_weights(studio, 0.25)
        self.assertEqual(w.tolist(), [[1.0, 0.25], [1.0, 1.0]])


class OffAxisFK(unittest.TestCase):
    def test_offaxis_point_rides_on_its_bone(self):
        from v2r.geom import Skeleton
        # two bones along +Y; a skin point 0.1 off the second bone's line (local +X), 0.05 along it
        rest0 = np.eye(4)
        rest1 = np.eye(4)
        rest1[1, 3] = 1.0
        rig = {"bones": [{"name": "a", "parent": -1, "rest": rest0.tolist(), "length": 1.0, "active": False,
                          "limits": [[-180, 180]] * 3},
                         {"name": "b", "parent": 0, "rest": rest1.tolist(), "length": 1.0, "active": True,
                          "limits": [[-90, 90]] * 3}],
               "keypoints": [{"name": "b", "bone": 1, "type": "head", "rest_world": [0, 1, 0]},
                             {"name": "b.tip", "bone": 1, "type": "tail", "tail_len": 1.0, "rest_world": [0, 2, 0]},
                             {"name": "b.side_a", "bone": 1, "type": "offaxis", "local": [0.1, 0.05, 0.0],
                              "rest_world": [0.1, 1.05, 0.0]}],
               "world_matrix": np.eye(4).tolist(), "center": [0, 1, 0], "size": 2.0}
        sk = Skeleton(rig)
        G = np.eye(4)[None]
        _, kp0 = sk.fk_np(G, np.zeros((1, 1, 3)))
        self.assertTrue(np.allclose(kp0[0], sk.rest_kp, atol=1e-6))
        # twist bone b by 90 deg about its own axis (+Y): the on-axis points stay, the off-axis one turns
        _, kp1 = sk.fk_np(G, np.array([[[0.0, np.pi / 2, 0.0]]]))
        self.assertTrue(np.allclose(kp1[0, :2], kp0[0, :2], atol=1e-6))
        self.assertTrue(np.allclose(kp1[0, 2], [0.0, 1.05, -0.1], atol=1e-5), kp1[0, 2])


class Smear(unittest.TestCase):
    def test_changes_only_around_the_limb_and_keeps_shape(self):
        img = np.full((200, 200, 3), 200, np.uint8)
        img[90:110, 60:140] = 60  # a dark horizontal limb
        uv = np.array([[60.0, 100.0], [140.0, 100.0], [20.0, 20.0]], np.float32)
        out = smear_segment(img.copy(), uv, 0, 1, random.Random(0))
        self.assertEqual(out.shape, img.shape)
        self.assertEqual(out.dtype, img.dtype)
        diff = np.abs(out.astype(int) - img.astype(int)).sum(-1)
        self.assertGreater(int((diff > 10).sum()), 200)  # visibly smeared
        self.assertEqual(int(diff[:20, :20].sum()), 0)     # far corner untouched

    def test_offscreen_segment_is_noop(self):
        img = np.full((50, 50, 3), 100, np.uint8)
        uv = np.array([[-10.0, 5.0], [80.0, 5.0]], np.float32)
        self.assertTrue(np.array_equal(smear_segment(img.copy(), uv, 0, 1, random.Random(0)), img))


if __name__ == "__main__":
    unittest.main()
