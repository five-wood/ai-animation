"""Standalone synthetic observation-weight regression tests (NumPy only).

Run: python -B tools/test_observations.py
No model/checkpoint, torch, dataset or server-side source mutation is needed.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.observations import observation_weights, WEIGHT_FLOOR


def scene(fps=24, seconds=2.0):
    times = np.arange(int(seconds * fps) + 1) / fps
    rest = np.array([[200., 220.], [250., 220.], [290., 250.],
                     [250., 280.], [210., 275.]])
    uv = np.broadcast_to(rest[None, None], (len(times), 2, len(rest), 2)).copy()
    uv[:, 1, :, 0] += 15
    uv += times[:, None, None, None] * np.array([70., -15.])
    conf = np.full(uv.shape[:-1], 0.9)
    res = np.zeros((len(times), len(rest), 2))
    rig = {"keypoints": [{} for _ in rest], "edges": [[0, 1], [1, 2], [1, 3], [3, 4]]}
    studio = {"panel_w": 1280, "panel_h": 720, "cams": [{}, {}]}
    return rig, studio, uv, conf, res, times


def evaluate(fps=24, seconds=2.0):
    rig, studio, uv, conf, res, _ = scene(fps, seconds)
    return observation_weights(rig, studio, uv, conf, res, fps)


class ObservationTests(unittest.TestCase):
    def assert_finite(self, weights, diagnostics):
        self.assertTrue(np.isfinite(weights).all())
        self.assertTrue((weights >= WEIGHT_FLOOR).all())
        self.assertTrue((weights <= 1).all())
        for name, array in diagnostics.items():
            self.assertIsInstance(array, np.ndarray, name)
            self.assertTrue(np.isfinite(array).all(), name)

    def test_constant_velocity_24_48_fps(self):
        w24, d24 = evaluate(24)
        w48, d48 = evaluate(48)
        self.assert_finite(w24, d24)
        self.assert_finite(w48, d48)
        np.testing.assert_allclose(w24, w48[::2], atol=1e-9)
        np.testing.assert_allclose(d24["temporal_factor"], 1., atol=1e-10)
        np.testing.assert_allclose(d48["temporal_factor"], 1., atol=1e-10)
        self.assertGreater(w24.min(), 0.9)

    def test_fast_real_global_motion_is_not_outlier(self):
        for fps in (24, 48):
            rig, studio, uv, conf, res, t = scene(fps)
            # Fast smooth nonlinear global displacement: not just constant speed.
            uv += (np.array([150., 100.])[None] * np.sin(2 * np.pi * 3 * t)[:, None])[:, None, None]
            w, d = observation_weights(rig, studio, uv, conf, res, fps)
            self.assert_finite(w, d)
            self.assertGreater(d["temporal_innovation_px"].max(), 5.)
            np.testing.assert_allclose(d["temporal_factor"], 1., atol=1e-10)
            self.assertGreater(w.min(), 0.9)

    def test_fast_rigid_rotation_is_not_outlier(self):
        for fps in (24, 48):
            rig, studio, uv, conf, res, t = scene(fps)
            center = uv.mean(2, keepdims=True)
            angle = 2 * np.pi * 2 * t
            ca, sa = np.cos(angle), np.sin(angle)
            rotation = np.stack([ca, -sa, sa, ca], axis=-1).reshape(-1, 2, 2)
            uv = center + np.einsum("tij,tvkj->tvki", rotation, uv - center)
            w, d = observation_weights(rig, studio, uv, conf, res, fps)
            self.assert_finite(w, d)
            self.assertGreater(w.min(), 0.9)

    def test_one_view_one_frame_outlier(self):
        rig, studio, uv, conf, res, _ = scene()
        t, v, k = 24, 0, 2
        uv[t, v, k] += [120., -90.]
        w, d = observation_weights(rig, studio, uv, conf, res, 24)
        self.assert_finite(w, d)
        self.assertLess(w[t, v, k], 0.15)
        self.assertLess(d["temporal_factor"][t, v, k], 0.1)
        self.assertGreater(w[t, 1, k], 0.9)
        self.assertGreater(np.median(w[:, v, k]), 0.9)
        # The robust fit must not smear a single impulse over nearby good data.
        self.assertGreater(w[t - 3:t, v, k].min(), 0.9)
        self.assertGreater(w[t + 1:t + 4, v, k].min(), 0.9)
        self.assertGreater(np.median(w[t, v, [0, 3, 4]]), 0.9)
        self.assertGreater(d["temporal_z"][t, v, k], 10.)

    def test_ray_disagreement_is_mild_and_symmetric(self):
        rig, studio, uv, conf, res, _ = scene()
        base, _ = observation_weights(rig, studio, uv, conf, res, 24)
        res[24, 2] = [1e6, 1.]
        w, d = observation_weights(rig, studio, uv, conf, res, 24)
        np.testing.assert_array_equal(d["ray_factor"][:, 0], d["ray_factor"][:, 1])
        self.assertGreaterEqual(d["ray_factor"].min(), 0.8)
        self.assertGreaterEqual(w[24, 0, 2] / base[24, 0, 2], 0.8)
        np.testing.assert_allclose(w[24, 0, 2], w[24, 1, 2])

    def test_normal_perspective_length_changes_are_tolerated(self):
        rig, studio, uv, conf, res, t = scene()
        for v in range(2):
            center = uv[:, v].mean(1, keepdims=True)
            # A 5x range of smoothly varying projected edge lengths.
            scale = 1.0 + 0.67 * np.sin(2 * np.pi * t / 2)
            uv[:, v] = center + (uv[:, v] - center) * scale[:, None, None]
        w, d = observation_weights(rig, studio, uv, conf, res, 24)
        self.assert_finite(w, d)
        self.assertGreater(d["bone_factor"].min(), 0.95)
        # Different views get their own baseline, never a common world length.
        uv[:, 1] = uv[:, 1].mean(1, keepdims=True) + 0.2 * (uv[:, 1] - uv[:, 1].mean(1, keepdims=True))
        _, d = observation_weights(rig, studio, uv, conf, res, 24)
        self.assertGreater(d["bone_factor"].min(), 0.95)

    def test_bone_spike_and_high_degree_do_not_multiply(self):
        rig, studio, uv, conf, res, _ = scene()
        uv[24, 0, 1] += [400., 200.]
        _, d = observation_weights(rig, studio, uv, conf, res, 24)
        self.assertLess(d["bone_factor"][24, 0, 1], 0.8)
        self.assertGreaterEqual(d["bone_factor"].min(), 0.25)
        np.testing.assert_allclose(d["bone_factor"][24, 1], 1.)

    def test_optional_heatmap_quality_and_product_audit(self):
        rig, studio, uv, conf, res, _ = scene()
        quality = {"ambiguity_ratio": np.zeros_like(conf), "spread_px": np.ones_like(conf)}
        quality["ambiguity_ratio"][24, 0, 2] = 0.95
        quality["spread_px"][24, 0, 2] = 30.
        base, neutral = observation_weights(rig, studio, uv, conf, res, 24)
        w, d = observation_weights(rig, studio, uv, conf, res, 24, quality)
        self.assertLess(w[24, 0, 2], base[24, 0, 2] * 0.6)
        self.assertFalse(neutral["ambiguity_ratio_available"].any())
        self.assertTrue(d["ambiguity_ratio_available"].all())
        factors = ["bounds_factor", "peak_factor", "temporal_factor", "bone_factor",
                   "ray_factor", "ambiguity_factor", "spread_factor"]
        product = np.prod(np.stack([d[name] for name in factors]), axis=0)
        np.testing.assert_allclose(d["raw_product"], product)
        np.testing.assert_allclose(w, np.clip(product, WEIGHT_FLOOR, 1.))
        self.assert_finite(w, d)

    def test_missing_nonfinite_and_out_of_frame_remain_finite(self):
        rig, studio, uv, conf, res, _ = scene()
        uv[0, 0, 0] = [np.nan, np.inf]
        uv[1, 1, 1] = [-10., 50.]
        uv[2, 0, 1] = [1e300, -1e300]
        conf[0, 0, 0] = np.nan
        conf[1, 1, 1] = -np.inf
        conf[2, 0, 1] = 1e300
        res[3, 2] = [np.nan, np.inf]
        quality = {"spread_px": np.full_like(conf, np.nan),
                   "ambiguity_ratio": np.full_like(conf, np.inf)}
        copies = [a.copy() for a in (uv, conf, res)]
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            w, d = observation_weights(rig, studio, uv, conf, res, 24, quality)
        self.assert_finite(w, d)
        self.assertLessEqual(w[0, 0, 0], 0.01)
        self.assertLessEqual(w[1, 1, 1], 0.01)
        self.assertFalse(d["in_frame"][0, 0, 0])
        for actual, before in zip((uv, conf, res), copies):
            np.testing.assert_array_equal(actual, before)

    def test_short_constant_degenerate_sequences(self):
        for T in (1, 2, 3, 5):
            rig = {"keypoints": [{}], "edges": []}
            uv = np.zeros((T, 1, 1, 2))
            w, d = observation_weights(rig, {"panel_w": 20, "panel_h": 20},
                                       uv, np.ones((T, 1, 1)), np.zeros((T, 1, 1)), 24)
            self.assert_finite(w, d)
            np.testing.assert_allclose(w, 1.)
            self.assertEqual(d["edge_length_px"].shape, (T, 1, 0))
        rig, studio, uv, conf, res, _ = scene()
        uv.fill(np.nan)
        conf.fill(np.nan)
        res.fill(np.nan)
        w, d = observation_weights(rig, studio, uv, conf, res, 24)
        self.assert_finite(w, d)

    def test_extreme_positive_fps_and_random_missing_tracks(self):
        rng = np.random.default_rng(21)
        for T, V, K in ((1, 1, 1), (2, 2, 2), (5, 2, 7), (11, 3, 4)):
            for fps in (1e-300, 0.1, 24, 48, 1e300):
                uv = rng.normal(150, 20, (T, V, K, 2))
                conf = rng.uniform(-0.2, 1.2, (T, V, K))
                res = rng.exponential(5, (T, K, V))
                uv[rng.random((T, V, K)) < 0.15] = np.nan
                rig = {"edges": [[i, i + 1] for i in range(K - 1)]}
                studio = {"panel_w": 320, "panel_h": 240}
                with np.errstate(all="raise"):
                    w, d = observation_weights(rig, studio, uv, conf, res, fps)
                self.assert_finite(w, d)

    def test_fps_and_shapes_validation(self):
        rig, studio, uv, conf, res, _ = scene()
        for fps in (0, -24, np.nan, np.inf, None, True, [24], "nonsense"):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                observation_weights(rig, studio, uv, conf, res, fps)
        for bad_uv, bad_conf, bad_res in ((uv[..., 0], conf, res), (uv, conf[:, 0], res),
                                         (uv, conf, res[:, :, :1]), (uv[:0], conf[:0], res[:0])):
            with self.assertRaises(ValueError):
                observation_weights(rig, studio, bad_uv, bad_conf, bad_res, 24)
        with self.assertRaises(ValueError):
            observation_weights(rig, studio, uv, conf, res, 24, {"spread_px": np.zeros(2)})
        with self.assertRaises(ValueError):
            observation_weights(rig, studio, uv, conf, res, 24, [])
        with self.assertRaises(ValueError):
            observation_weights({"edges": [[0, 99]]}, studio, uv, conf, res, 24)
        with self.assertRaises(ValueError):
            observation_weights(rig, {"panel_w": 0, "panel_h": 20}, uv, conf, res, 24)


if __name__ == "__main__":
    unittest.main(verbosity=2)
