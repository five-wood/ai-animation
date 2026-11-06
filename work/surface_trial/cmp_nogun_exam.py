"""m11101 vs m11101_nogun exams: reader accuracy and a body-orientation metric that means the same in both rigs.
Read-only (CPU)."""
import json
import sys

import numpy as np
import torch

sys.path.insert(0, "/data/video2rig/code")
from v2r.geom import Skeleton, quat_to_mat_np, unit_rot_np  # noqa: E402

PR = "/data/video2rig/projects"


def rot_angle(Ra, Rb):
    R = np.einsum("...ji,...jk->...ik", Ra, Rb)
    return np.degrees(np.arccos(np.clip((np.trace(R, axis1=-2, axis2=-1) - 1) / 2, -1, 1)))


out = {}
for proj in ("m11101", "m11101_nogun"):
    rig = json.load(open(f"{PR}/{proj}/rig.json"))
    studio = json.load(open(f"{PR}/{proj}/studio.json"))
    sk = Skeleton(rig)
    names = [b["name"] for b in rig["bones"]]
    pel = names.index("Bip001 Pelvis")
    kn = [k["name"] for k in rig["keypoints"]]
    for ex in ("exam", "exam_strike"):
        gt = json.load(open(f"{PR}/{proj}/{ex}/frames/motion_gt.json"))
        rep = json.load(open(f"{PR}/{proj}/{ex}/exam_report.json"))
        mot = np.load(f"{PR}/{proj}/out/{ex}/motion.npz")
        rd = np.load(f"{PR}/{proj}/out/{ex}/reading.npz")
        G_gt = np.array([f["G"] for f in gt["frames"]])
        R_gt = quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]]))
        with torch.no_grad():
            w_gt, _ = sk.fk_rot(torch.tensor(G_gt, dtype=torch.float32), torch.tensor(R_gt, dtype=torch.float32))
            w_pr, _ = sk.fk_rot(torch.tensor(mot["G"], dtype=torch.float32),
                                torch.tensor(quat_to_mat_np(mot["quat"]), dtype=torch.float32))
        w_gt, w_pr = w_gt.numpy(), w_pr.numpy()
        body = rot_angle(unit_rot_np(w_gt[:, pel, :3, :3]), unit_rot_np(w_pr[:, pel, :3, :3]))
        # reader accuracy on visible keypoints (raw reader output, exam has no calibration)
        errs, errs_rel = [], []
        for v, c in enumerate(studio["cams"]):
            uvz = np.array([f[c["name"]]["uvz"] for f in gt["frames"]])[..., :2]
            vis = np.array([f[c["name"]]["vis"] for f in gt["frames"]]) > 0.5
            e = np.linalg.norm(rd["uv_reader"][:, v] - uvz, axis=-1)
            sz = np.ptp(uvz, axis=1).max(-1)[:, None]
            errs.append(e[vis])
            errs_rel.append((e / sz * 100)[vis])
        e, er = np.concatenate(errs), np.concatenate(errs_rel)
        # how hard is this exam: mean GT speed of the keypoints (body sizes per second) and pelvis turn rate
        kp = np.array([f["kp3d"] for f in gt["frames"]])
        speed = float(np.linalg.norm(np.diff(kp, axis=0), axis=-1).mean() / sk.size * 30)
        turn = float(rot_angle(unit_rot_np(w_gt[:-1, pel, :3, :3]), unit_rot_np(w_gt[1:, pel, :3, :3])).mean() * 30)
        pb = rep["per_bone"]
        out[f"{proj}/{ex}"] = {
            "joint_pct": round(rep["joint_err_mean_pct_of_size"], 2), "limb_deg": round(rep["bone_angle_mean_deg"], 1),
            "twist_deg": round(rep["twist_mean_deg"], 1), "root_yaxis_deg": round(rep["root_angle_mean_deg"], 1),
            "body_full_rot_deg": round(float(body.mean()), 1), "passed": rep["passed"],
            "reader_visible_px_mean": round(float(e.mean()), 2), "reader_visible_px_median": round(float(np.median(e)), 2),
            "reader_visible_pct_of_size_median": round(float(np.median(er)), 2),
            "gt_kp_speed_size_per_s": round(speed, 3), "gt_body_turn_deg_per_s": round(turn, 1),
            "impact": rep.get("impact"),
            "bones": {n: round(v["mean_deg"], 1) for n, v in pb.items()},
        }
print(json.dumps(out))
