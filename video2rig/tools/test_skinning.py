"""Torch linear blend skinning (Skeleton.skin_points) against Blender's deformed mesh.

Needs a surface file exported with the Blender check frames:
  blender -b --factory-startup --python v2r/blender/export_surface.py -- <project> <out.npz> \
      '{"check_exam": "exam", "check_frames": [0, 37, 75, 112, 149]}'
  python tools/test_skinning.py <project> <out.npz>
Passes when the largest vertex error is below 0.5% of the body size (the plan's threshold).  Also a
self-contained unit test of the rest pose (no Blender needed) when run without arguments.
"""
import json
import os
import sys
import unittest

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.geom import Skeleton, load_surface, quat_to_mat_np  # noqa: E402


def two_bone_rig():
    eye = np.eye(4)
    b1 = eye.copy()
    b1[:3, 3] = [0, 1, 0]
    bones = [{"name": "root", "parent": -1, "rest": eye.tolist(), "length": 1.0, "active": False,
              "limits": [[-90, 90]] * 3},
             {"name": "arm", "parent": 0, "rest": b1.tolist(), "length": 1.0, "active": True,
              "limits": [[-90, 90]] * 3}]
    kps = [{"name": "root", "bone": 0, "type": "head", "rest_world": [0, 0, 0], "side": "M"},
           {"name": "arm.tip", "bone": 1, "type": "tail", "tail_len": 1.0, "rest_world": [0, 2, 0], "side": "M"}]
    return {"bones": bones, "keypoints": kps, "world_matrix": eye.tolist(), "center": [0, 1, 0], "size": 2.0}


class TestSkinning(unittest.TestCase):
    def test_rest_pose_is_identity(self):
        sk = Skeleton(two_bone_rig())
        surf = {"V": torch.tensor([[0.1, 0.5, 0.0], [0.0, 1.5, 0.2]]), "N": torch.tensor([[1.0, 0, 0], [0, 0, 1.0]]),
                "w_idx": torch.tensor([[0, 1, 0, 0], [1, 0, 0, 0]]),
                "w_val": torch.tensor([[0.5, 0.5, 0, 0], [1.0, 0, 0, 0]])}
        w, _ = sk.fk(torch.eye(4)[None], torch.zeros(1, sk.A, 3))
        P, N = sk.skin_points(w, surf, normals=True)
        self.assertLess(float((P[0] - surf["V"]).abs().max()), 1e-6)
        self.assertLess(float((N[0] - surf["N"]).abs().max()), 1e-6)

    def test_rigid_child_rotation(self):
        sk = Skeleton(two_bone_rig())
        surf = {"V": torch.tensor([[0.0, 2.0, 0.0]]), "N": torch.tensor([[0.0, 1.0, 0.0]]),
                "w_idx": torch.tensor([[1, 0, 0, 0]]), "w_val": torch.tensor([[1.0, 0, 0, 0]])}
        e = torch.zeros(1, sk.A, 3)
        e[0, 0, 0] = np.pi / 2  # 90 deg about local X: the arm's +Y turns to +Z
        w, kp = sk.fk(torch.eye(4)[None], e)
        P = sk.skin_points(w, surf)
        self.assertLess(float((P[0, 0] - kp[0, 1]).abs().max()), 1e-5)  # the point at the tip follows the tip


def check_against_blender(project, surf_path):
    rig = json.load(open(os.path.join(project, "rig.json")))
    sk = Skeleton(rig, dtype=torch.float64)
    z = np.load(surf_path)
    surf = load_surface(surf_path, dtype=torch.float64, keep_props=True)
    G = torch.tensor(z["check_G"], dtype=torch.float64)
    R = torch.tensor(quat_to_mat_np(z["check_quat"]), dtype=torch.float64)
    w, _ = sk.fk_rot(G, R)
    P = sk.skin_points(w, surf).numpy()
    err = np.linalg.norm(P - z["check_V"], axis=-1) / sk.size * 100  # % of body size
    tri_prop = z["tri_prop"].astype(bool)
    body_v = np.zeros(len(surf["V"]), bool)
    body_v[z["F"][~tri_prop].ravel()] = True
    print(f"[skin] {project}: frames {z['check_frames'].tolist()} vertices {err.shape[1]}: error % of size "
          f"mean {err.mean():.4f} p99 {np.percentile(err, 99):.4f} max {err.max():.4f}; body-only max "
          f"{err[:, body_v].max():.4f}")
    worst = np.unravel_index(np.argmax(err), err.shape)
    print(f"[skin] worst vertex {worst[1]} frame {z['check_frames'][worst[0]]}: weights "
          f"{[(rig['bones'][int(b)]['name'], round(float(v), 3)) for b, v in zip(z['w_idx'][worst[1]], z['w_val'][worst[1]]) if v > 0]}")
    return float(err[:, body_v].max())


if __name__ == "__main__":
    if len(sys.argv) == 3:
        m = check_against_blender(sys.argv[1], sys.argv[2])
        print("[skin] PASS" if m < 0.5 else "[skin] FAIL (max error >= 0.5% of body size)")
        sys.exit(0 if m < 0.5 else 1)
    unittest.main()
