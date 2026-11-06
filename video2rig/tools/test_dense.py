"""Dense correspondence head: model shapes, loss behaviour and the label resampling onto the heat grid.
(The labels themselves are checked against the training pictures' alpha by work/dense/check_labels.py.)"""
import os
import sys
import unittest

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.train import dense_loss  # noqa: E402


class TestDense(unittest.TestCase):
    def test_loss_prefers_true_coords_and_learns_scale(self):
        torch.manual_seed(0)
        coord = torch.rand(2, 3, 8, 8) * 2 - 1
        mask = (torch.rand(2, 8, 8) > 0.3).float()
        good = torch.cat([coord, (mask * 8 - 4)[:, None], torch.full((2, 1, 8, 8), -3.0)], 1)
        bad = good.clone()
        bad[:, :3] += 0.5
        lf_g, lc_g, med_g = dense_loss(good, coord, mask)
        lf_b, lc_b, med_b = dense_loss(bad, coord, mask)
        self.assertLess(float(lc_g), float(lc_b))
        self.assertAlmostEqual(float(med_g), 0.0, places=5)
        self.assertAlmostEqual(float(lf_g), float(lf_b), places=6)
        # with a wrong answer, a larger predicted scale lowers the loss (the head can say "unsure")
        unsure = bad.clone()
        unsure[:, 4] = 0.4
        self.assertLess(float(dense_loss(unsure, coord, mask)[1]), float(lc_b))

    def test_background_does_not_count_in_coord_loss(self):
        coord = torch.zeros(1, 3, 4, 4)
        mask = torch.zeros(1, 4, 4)
        mask[0, :2] = 1
        pred = torch.zeros(1, 5, 4, 4)
        pred[0, :3, 2:] = 5.0  # wrong only on background cells
        self.assertAlmostEqual(float(dense_loss(pred, coord, mask)[1]), float(dense_loss(torch.zeros(1, 5, 4, 4), coord, mask)[1]))

    def test_model_dense_head(self):
        from v2r.model import JointReader
        m = JointReader(5, backbone="vit_small_patch14_dinov2.lvd142m", pretrained=False, img_size=(56, 56), dense=True)
        x = torch.zeros(1, 3, 56, 56)
        hm, dp, dn = m(x, torch.tensor([0]), with_dense=True)
        self.assertEqual(tuple(dn.shape), (1, 5, hm.shape[2], hm.shape[3]))
        hm2, dp2 = m(x, torch.tensor([0]))
        self.assertTrue(torch.equal(hm, hm2))
        # frozen trunk: no gradient reaches the backbone, the dense head still learns
        hm3, dp3, dn3 = m(x, torch.tensor([0]), with_dense=True, frozen_trunk=True)
        self.assertTrue(torch.allclose(hm3, hm) and torch.allclose(dn3, dn))
        dn3.sum().backward()
        self.assertTrue(all(p.grad is None for n, p in m.named_parameters() if not n.startswith("dn.")))
        self.assertTrue(any(p.grad is not None for p in m.dn.parameters()))
        # own dense decoder (two-step): keypoint outputs unchanged, dense decoder gets gradients, keypoint decoder not
        md = JointReader(5, backbone="vit_small_patch14_dinov2.lvd142m", pretrained=False, img_size=(56, 56),
                         dense=True, dense_decoder=True)
        sd = {k: v for k, v in m.state_dict().items() if not k.startswith("dn.")}
        md.load_state_dict(sd, strict=False)
        hm4, dp4, dn4 = md(x, torch.tensor([0]), with_dense=True, frozen_trunk=True)
        self.assertTrue(torch.allclose(hm4, hm))
        dn4.sum().backward()
        self.assertTrue(any(p.grad is not None for p in md.dproj.parameters()))
        self.assertTrue(all(p.grad is None for p in md.proj.parameters()))
        m0 = JointReader(5, backbone="vit_small_patch14_dinov2.lvd142m", pretrained=False, img_size=(56, 56))
        self.assertFalse(hasattr(m0, "dn"))
        with self.assertRaises(ValueError):
            m0(x, torch.tensor([0]), with_dense=True)


if __name__ == "__main__":
    unittest.main()
