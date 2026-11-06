"""Per-frame body (pelvis) orientation error of the saved exam solutions: few bad frames or uniformly worse?"""
import json
import sys

import numpy as np
import torch

sys.path.insert(0, "/data/video2rig/code")
from v2r.geom import Skeleton, quat_to_mat_np, unit_rot_np  # noqa: E402

PR = "/data/video2rig/projects"
for proj in ("m11101", "m11101_nogun"):
    rig = json.load(open(f"{PR}/{proj}/rig.json"))
    sk = Skeleton(rig)
    pel = [b["name"] for b in rig["bones"]].index("Bip001 Pelvis")
    for ex in ("exam", "exam_strike"):
        gt = json.load(open(f"{PR}/{proj}/{ex}/frames/motion_gt.json"))
        mot = np.load(f"{PR}/{proj}/out/{ex}/motion.npz")
        G = np.array([f["G"] for f in gt["frames"]])
        R = quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]]))
        with torch.no_grad():
            wg, _ = sk.fk_rot(torch.tensor(G, dtype=torch.float32), torch.tensor(R, dtype=torch.float32))
            wp, _ = sk.fk_rot(torch.tensor(mot["G"], dtype=torch.float32),
                              torch.tensor(quat_to_mat_np(mot["quat"]), dtype=torch.float32))
        Ra, Rb = unit_rot_np(wg.numpy()[:, pel, :3, :3]), unit_rot_np(wp.numpy()[:, pel, :3, :3])
        M = np.einsum("tji,tjk->tik", Ra, Rb)
        a = np.degrees(np.arccos(np.clip((np.trace(M, axis1=1, axis2=2) - 1) / 2, -1, 1)))
        # body pose range in the exam: how far the true body turns away from facing the front camera
        yaw = np.degrees(np.arctan2(Ra[:, 0, 1], Ra[:, 1, 1]))
        worst = np.argsort(-a)[:5]
        print(f"{proj:13s} {ex:12s} mean {a.mean():5.1f} median {np.median(a):5.1f} p90 {np.percentile(a, 90):5.1f} "
              f"max {a.max():5.1f}  frames>20deg {int((a > 20).sum()):3d}  mean w/o worst 10% "
              f"{np.sort(a)[:int(0.9 * len(a))].mean():4.1f} | true yaw range {yaw.min():.0f}..{yaw.max():.0f} "
              f"| worst frames {worst.tolist()}")
