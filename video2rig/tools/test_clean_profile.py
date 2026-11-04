"""Synthetic visual-profile regressions, without model or real project data.

Run from the code root: python -B tools/test_clean_profile.py
Requires numpy, opencv-python and torch; writes fixtures only in a temp directory.
"""
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2r import data


def rgba_fixture():
    rgba = np.zeros((96, 112, 4), np.uint8)
    rgba[24:80, 32:76] = (50, 120, 180, 255)
    rgba[23, 32:76] = (50, 120, 180, 128)
    return rgba


def original_compose(rgba, rng, train=True):
    """Frozen pre-profile implementation: catches changes to legacy RNG order."""
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3].astype(np.float32) / 255.0
    h, w = alpha.shape
    if not train:
        bg = np.full((h, w, 3), 200.0, np.float32)
        return (rgb * alpha[..., None] + bg * (1 - alpha[..., None])).astype(np.uint8)
    rgb = data.fg_jitter(rgb, rng)
    bg = data.studio_background(h, w, rng)
    if rng.random() < 0.7:
        bg = data.add_shadow(bg, alpha, rng)
    img = rgb * alpha[..., None] + bg * (1 - alpha[..., None])
    if rng.random() < 0.3:
        img = data.add_dust(img, alpha, rng)
    return data.degrade(img, rng)


class CleanProfileTests(unittest.TestCase):
    def setUp(self):
        self.rgba = rgba_fixture()
        self.np_state = np.random.get_state()

    def tearDown(self):
        np.random.set_state(self.np_state)

    def test_default_explicit_legacy_and_original_are_bit_identical(self):
        for train in (False, True):
            for seed in range(32):
                outputs, states = [], []
                for compose in (original_compose, data.compose,
                                lambda rgba, rng, train: data.compose(rgba, rng, train, profile="legacy")):
                    np.random.seed(seed + 123)
                    rng = random.Random(seed)
                    outputs.append(compose(self.rgba, rng, train))
                    states.append(rng.getstate())
                    # Also verify global numpy RNG progression is unchanged.
                    outputs.append(np.random.rand(5))
                np.testing.assert_array_equal(outputs[0], outputs[2])
                np.testing.assert_array_equal(outputs[0], outputs[4])
                np.testing.assert_array_equal(outputs[1], outputs[3])
                np.testing.assert_array_equal(outputs[1], outputs[5])
                self.assertEqual(states[0], states[1])
                self.assertEqual(states[0], states[2])

    def test_soft_clean_never_calls_shadow_or_dust(self):
        with patch.object(data, "add_shadow", side_effect=AssertionError("shadow called")), \
             patch.object(data, "add_dust", side_effect=AssertionError("dust called")):
            for train in (False, True):
                for seed in range(32):
                    out = data.compose(self.rgba, random.Random(seed), train, "soft-clean")
                    self.assertEqual(out.shape, self.rgba.shape[:2] + (3,))
                    self.assertEqual(out.dtype, np.uint8)

    def test_transparent_pixels_are_neutral_not_black(self):
        transparent = self.rgba[..., 3] == 0
        for train in (False, True):
            for seed in range(32):
                out = data.compose(self.rgba, random.Random(seed), train, "soft-clean")
                bg = out[transparent]
                np.testing.assert_array_equal(bg[:, 0], bg[:, 1])
                np.testing.assert_array_equal(bg[:, 0], bg[:, 2])
                self.assertGreaterEqual(int(bg.min()), 180)
                self.assertLessEqual(int(bg.max()), 224)
                if train:
                    self.assertLessEqual(int(bg.max()) - int(bg.min()), 16)
        transparent_rgba = np.zeros_like(self.rgba)
        out = data.compose(transparent_rgba, random.Random(4), profile="soft-clean")
        self.assertGreater(int(out.min()), 0)
        self.assertGreater(np.ptp(out[..., 0]), 0)

    def test_foreground_augmentation_is_preserved(self):
        with patch.object(data, "fg_jitter", return_value=np.full((96, 112, 3), 77, np.float32)) as jitter, \
             patch.object(data, "degrade", return_value=np.full((96, 112, 3), 91, np.uint8)) as degrade:
            out = data.compose(self.rgba, random.Random(1), profile="soft-clean")
            jitter.assert_called_once()
            degrade.assert_called_once()
            np.testing.assert_array_equal(degrade.call_args.args[0], np.full((96, 112, 3), 77))
            np.testing.assert_array_equal(out[self.rgba[..., 3] == 255], 91)
            self.assertFalse(np.all(out[self.rgba[..., 3] == 0] == 91))
        with patch.object(data, "fg_jitter", side_effect=AssertionError("val jitter")), \
             patch.object(data, "degrade", side_effect=AssertionError("val degrade")):
            data.compose(self.rgba, random.Random(1), train=False, profile="soft-clean")

    def test_invalid_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown visual profile"):
            data.compose(self.rgba, random.Random(0), profile="typo")

    def test_poseviews_config_forwarding_and_labels(self):
        with tempfile.TemporaryDirectory(prefix="v2r_clean_profile_") as tmp:
            project = Path(tmp)
            (project / "train_data").mkdir()
            rig = {"keypoints": [{"name": "a"}, {"name": "b"}], "size": 2.0}
            studio = {"panel_w": 112, "panel_h": 96, "cams": [{"name": "front"}]}
            record = {"front": {"img": "view.png", "uvz": [[40, 40, 1], [65, 65, 3]], "vis": [1, 0]}}
            records = [dict(record, id=i) for i in range(12)]
            for name, obj in (("rig.json", rig), ("studio.json", studio), ("train_data/meta_0.json", records)):
                (project / name).write_text(json.dumps(obj), encoding="utf-8")
            self.assertTrue(cv2.imwrite(str(project / "train_data/view.png"), self.rgba))
            for split in ("train", "val"):
                # First use no prepare_opts, then an opts file without visual_profile.
                opts = project / "prepare_opts.json"
                if opts.exists():
                    opts.unlink()
                legacy = data.PoseViews(tmp, split=split, target_w=112, seed=7)
                self.assertEqual(legacy.visual_profile, "legacy")
                opts.write_text("{}", encoding="utf-8")
                self.assertEqual(data.PoseViews(tmp, split=split, target_w=112).visual_profile, "legacy")
                opts.write_text(json.dumps({"visual_profile": "soft-clean"}), encoding="utf-8")
                clean = data.PoseViews(tmp, split=split, target_w=112, seed=7)
                self.assertEqual(clean.visual_profile, "soft-clean")
                self.assertEqual(legacy.items, clean.items)
                # Train full-frame mode isolates the unchanged affine/label path
                # from intentionally different profile RNG consumption at crop time.
                legacy.full_frac = clean.full_frac = 1.0
                with patch.object(data, "compose", wraps=data.compose) as compose:
                    a, b = legacy[1], clean[1]
                    self.assertEqual(compose.call_args_list[0].kwargs["profile"], "legacy")
                    self.assertEqual(compose.call_args_list[1].kwargs["profile"], "soft-clean")
                for key in ("uv", "inside", "vis", "depth"):
                    self.assertTrue(torch.equal(a[key], b[key]), (split, key))
                self.assertEqual(a["view"], b["view"])
                self.assertEqual(a["px_scale"], b["px_scale"])
                np.testing.assert_array_equal(b["depth"].numpy(), [-0.5, 0.5])
                self.assertEqual(clean.rig, rig)
                self.assertEqual(clean.studio, studio)
            opts.write_text(json.dumps({"visual_profile": "typo"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unknown visual profile"):
                data.PoseViews(tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
