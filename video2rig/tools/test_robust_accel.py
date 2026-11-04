"""Unit tests for the robust acceleration penalty and the corroborated temporal weights.

    /data/video2rig/.venv/bin/python tools/test_robust_accel.py
"""
from pathlib import Path
import sys
import unittest

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2r.solve_temporal import robust_square, rotation_acceleration_loss  # noqa: E402
from v2r.observations import observation_weights  # noqa: E402


class RobustSquare(unittest.TestCase):
    def test_off_is_plain_square(self):
        x2 = torch.tensor([0.0, 1e-6, 0.3, 4.0])
        self.assertTrue(torch.equal(robust_square(x2, 0.0), x2))

    def test_small_values_quadratic(self):
        x2 = torch.tensor([1e-8, 1e-6])
        r = robust_square(x2, 0.1)
        self.assertTrue(torch.allclose(r, x2, rtol=1e-3))

    def test_large_values_linear(self):
        s = 0.01
        x = torch.tensor([1.0, 2.0])
        r = robust_square(x * x, s)
        # ~ 2*s*|x| for |x| >> s: doubling x doubles the penalty (quadratic would quadruple)
        self.assertAlmostEqual(float(r[1] / r[0]), 2.0, delta=0.05)
        self.assertTrue(bool((r < x * x).all()))

    def test_monotone_and_nonnegative(self):
        x2 = torch.linspace(0, 5, 101)
        r = robust_square(x2, 0.05)
        self.assertTrue(bool((r >= 0).all()))
        self.assertTrue(bool((r[1:] > r[:-1]).all()))

    def test_rotation_loss_default_unchanged(self):
        torch.manual_seed(0)
        a = torch.randn(9, 3, 3)
        R = torch.linalg.qr(a).Q
        R = R * torch.sign(torch.det(R))[:, None, None]
        self.assertTrue(torch.allclose(rotation_acceleration_loss(R, 24), rotation_acceleration_loss(R, 24, 0.0)))
        self.assertLessEqual(float(rotation_acceleration_loss(R, 24, 0.02)), float(rotation_acceleration_loss(R, 24)))


class Corroborate(unittest.TestCase):
    def setUp(self):
        T, V, K = 40, 2, 3
        rng = np.random.default_rng(1)
        self.rig = {"keypoints": [{}] * K, "edges": [[0, 1], [1, 2]]}
        self.studio = {"panel_w": 640, "panel_h": 720}
        base = np.array([[300., 300.], [320., 360.], [340., 420.]])
        uv = np.repeat(base[None, None], T, 0).repeat(V, 1) + rng.normal(0, .3, (T, V, K, 2))
        uv[20:, :, 2] += 60.0  # an abrupt real jump of keypoint 2 in both views
        self.uv, self.conf = uv, np.full((T, V, K), .9)
        self.res_meet = np.zeros((T, K, V))  # rays meet: the jump is seen consistently
        self.res_miss = self.res_meet.copy()
        self.res_miss[19:22, 2] = 40.0  # rays miss at the jump: more likely a reader failure

    def test_default_unchanged(self):
        a, _ = observation_weights(self.rig, self.studio, self.uv, self.conf, self.res_meet, 24)
        b, _ = observation_weights(self.rig, self.studio, self.uv, self.conf, self.res_meet, 24, corroborate=False)
        self.assertTrue(np.array_equal(a, b))

    def test_relaxes_only_when_rays_meet(self):
        w0, d0 = observation_weights(self.rig, self.studio, self.uv, self.conf, self.res_meet, 24)
        w1, d1 = observation_weights(self.rig, self.studio, self.uv, self.conf, self.res_meet, 24, corroborate=True)
        w2, d2 = observation_weights(self.rig, self.studio, self.uv, self.conf, self.res_miss, 24, corroborate=True)
        jump = slice(18, 23)
        self.assertLess(d0["temporal_factor"][jump, :, 2].min(), 0.9)  # the jump is penalised without it
        self.assertGreater(d1["temporal_factor"][jump, :, 2].min(), 0.99)
        self.assertLess(d2["temporal_factor"][19:22, :, 2].min(), d1["temporal_factor"][19:22, :, 2].min())
        self.assertTrue(np.all(w1 >= w0 - 1e-12))


if __name__ == "__main__":
    unittest.main()
