"""Unit tests of the silhouette terms (v2r.silhouette) and their opt-in use in the solvers."""
import os
import sys
import unittest

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.geom import Skeleton, Cams  # noqa: E402
from v2r.silhouette import SilhouetteTerms, area_vertex_subset, save_masks, load_masks  # noqa: E402


def rig_and_studio():
    eye = np.eye(4)
    b1 = eye.copy()
    b1[:3, 3] = [0, 0, 1]
    b1[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]  # bone +Y along world +Z
    bones = [{"name": "root", "parent": -1, "rest": eye.tolist(), "length": 1.0, "active": False,
              "limits": [[-90, 90]] * 3},
             {"name": "arm", "parent": 0, "rest": b1.tolist(), "length": 1.0, "active": True,
              "limits": [[-90, 90]] * 3}]
    kps = [{"name": "root", "bone": 0, "type": "head", "rest_world": [0, 0, 0], "side": "M"},
           {"name": "arm", "bone": 1, "type": "head", "rest_world": [0, 0, 1], "side": "M"},
           {"name": "arm.tip", "bone": 1, "type": "tail", "tail_len": 1.0, "rest_world": [0, 0, 2], "side": "M"}]
    rig = {"bones": bones, "keypoints": kps, "world_matrix": eye.tolist(), "center": [0, 0, 1], "size": 2.0}
    K = [[400.0, 0, 50], [0, 400.0, 50], [0, 0, 1]]  # 1 world unit ~ 40 px
    # camera on -Y looking at +Y, image y down = world -Z
    R = [[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]]
    cams = [{"name": "front", "K": K, "R": R, "t": [0, 1.0, 10.0], "w": 100, "h": 100}]
    return rig, {"cams": cams, "panel_w": 100, "panel_h": 100}


def column_surface():
    """A thin ribbon along the arm (z 1..2, x +-0.05) rigid on the arm bone, and one on the root (z 0..1)."""
    zs = np.linspace(0.0, 2.0, 41)
    V = np.concatenate([np.stack([np.full(41, -0.05), np.zeros(41), zs], 1),
                        np.stack([np.full(41, 0.05), np.zeros(41), zs], 1)])
    F = np.array([[i, i + 1, 41 + i] for i in range(40)] + [[i + 1, 42 + i, 41 + i] for i in range(40)])
    w_idx = np.zeros((len(V), 4), np.int64)
    w_idx[np.r_[20:41, 61:82], 0] = 1  # z >= 1: on the arm
    w_val = np.zeros((len(V), 4), np.float32)
    w_val[:, 0] = 1
    t = lambda a: torch.tensor(np.asarray(a), dtype=torch.float32)
    return {"V": t(V), "N": t(np.tile([0, -1.0, 0], (len(V), 1))), "w_idx": torch.tensor(w_idx), "w_val": t(w_val),
            "F": F, "tri_prop": np.zeros(len(F), bool)}


class TestSilhouette(unittest.TestCase):
    def setUp(self):
        self.rig, self.studio = rig_and_studio()
        self.sk = Skeleton(self.rig)
        self.cams = Cams(self.studio)
        self.surf = column_surface()

    def pose(self, angle):
        e = torch.zeros(1, self.sk.A, 3)
        e[0, 0, 2] = angle  # about the bone's local Z: swings the arm sideways in the image
        w, _ = self.sk.fk(torch.eye(4)[None], e)
        return w

    def mask_of(self, world):
        terms = SilhouetteTerms(self.sk, self.cams, self.surf, np.ones((1, 1, 100, 100), bool), 20.0, world,
                                n_points=24)
        uv = terms._project(world)[0, 0].numpy()
        m = np.zeros((100, 100), bool)
        for x, y in uv:
            m[max(0, int(y) - 2):int(y) + 3, max(0, int(x) - 2):int(x) + 3] = True
        return m

    def test_true_pose_has_lower_loss(self):
        w_true = self.pose(0.0)
        m = self.mask_of(w_true)[None, None]
        terms = SilhouetteTerms(self.sk, self.cams, self.surf, m, 20.0, w_true, n_points=24, n_boundary=40,
                                n_interior=8)
        i0, c0 = terms.losses(w_true)
        i1, c1 = terms.losses(self.pose(0.6))
        self.assertLess(float(i0), 1e-3)
        self.assertGreater(float(i1), float(i0) + 1e-3)
        self.assertGreater(float(c1), float(c0))

    def test_gradient_points_back(self):
        w_true = self.pose(0.0)
        m = self.mask_of(w_true)[None, None]
        terms = SilhouetteTerms(self.sk, self.cams, self.surf, m, 20.0, w_true, n_points=24)
        a = torch.tensor(0.3, requires_grad=True)
        e = torch.zeros(1, self.sk.A, 3)
        e = e.index_put((torch.tensor([0]), torch.tensor([0]), torch.tensor([2])), a.reshape(1))
        w, _ = self.sk.fk(torch.eye(4)[None], e)
        inside, cov = terms.losses(w)
        (inside + cov).backward()
        self.assertGreater(float(a.grad), 0)  # decreasing the angle lowers the loss

    def test_deterministic(self):
        w_true = self.pose(0.0)
        m = self.mask_of(w_true)[None, None]
        terms = SilhouetteTerms(self.sk, self.cams, self.surf, m, 20.0, w_true, n_points=24)
        a = [float(x) for x in terms.losses(self.pose(0.2))]
        b = [float(x) for x in terms.losses(self.pose(0.2))]
        self.assertEqual(a, b)

    def test_fast_points_get_no_weight(self):
        w = torch.cat([self.pose(0.0), self.pose(0.8), self.pose(0.0)])
        m = np.ones((3, 1, 100, 100), bool)
        terms = SilhouetteTerms(self.sk, self.cams, self.surf, m, 20.0, w, n_points=24, speed_frac=0.04)
        # arm points move ~ tens of px per frame: zero weight; the root points do not move: full weight
        self.assertLess(float(terms.w_pt[:, 0, :].min()), 0.01)
        self.assertGreater(float(terms.w_pt.max()), 0.99)

    def test_mask_io_and_subset(self):
        import tempfile
        m = np.random.RandomState(0).rand(2, 2, 7, 13) > 0.5
        with tempfile.TemporaryDirectory() as d:
            save_masks(os.path.join(d, "m.npz"), m)
            self.assertTrue(np.array_equal(load_masks(os.path.join(d, "m.npz")), m))
        idx = area_vertex_subset(self.surf["V"].numpy(), self.surf["F"], 10)
        self.assertEqual(len(idx), len(np.unique(idx)))

    def test_solvers_unchanged_when_off(self):
        """Strength 0 must not need surface / masks and must not change the result (legacy + temporal)."""
        from v2r.solve import fit
        from v2r.solve_temporal import fit_temporal
        rig = dict(self.rig)
        # a fourth, off-axis keypoint so the rigid initialisation is well posed (not all points on one line)
        rig["keypoints"] = self.rig["keypoints"] + [{"name": "side", "bone": 0, "type": "offaxis",
                                                     "local": [0.4, 0.3, 0.0], "rest_world": [0.4, 0.3, 0.0],
                                                     "side": "M"}]
        studio = {"cams": self.studio["cams"] + [{"name": "side", "K": self.studio["cams"][0]["K"],
                                                 "R": [[0, 1.0, 0], [0, 0, -1.0], [-1.0, 0, 0]],
                                                 "t": [0, 1.0, 10.0], "w": 100, "h": 100}],
                  "panel_w": 100, "panel_h": 100}
        cams = Cams(studio)
        T = 5
        kp = np.array([k["rest_world"] for k in rig["keypoints"]], float)
        # small noise: the legacy image term's sqrt has no gradient at an exactly zero residual
        uv = np.stack([cams.project_np(kp)[..., :2]] * T) + np.random.RandomState(0).randn(T, 2, len(kp), 2) * 0.5
        conf = np.ones(uv.shape[:-1])
        X3 = np.stack([kp] * T)
        out = []
        for extra in ({}, {"silhouette_strength": 0., "coverage_strength": 0.}):
            torch.manual_seed(0)
            a = fit(rig, studio, uv, conf, X3, iters=30, device="cpu", verbose=False, **extra)
            torch.manual_seed(0)
            b = fit_temporal(rig, studio, uv, conf, X3, 30.0, iters=20, warm_iters=30, device="cpu", verbose=False,
                             **extra)
            out.append((a, b))
        for k in ("G", "eul"):
            self.assertTrue(np.isfinite(out[0][0][k]).all())
            self.assertTrue(np.array_equal(out[0][0][k], out[1][0][k]))
            self.assertTrue(np.array_equal(out[0][1][k], out[1][1][k]))


if __name__ == "__main__":
    unittest.main()
