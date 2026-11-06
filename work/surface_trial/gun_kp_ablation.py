"""Old m11101 exams, saved readings, legacy solver: as is vs with the 4 gun keypoints given zero weight.
In the exam the guns are root bones moved only by the global transform G, so their keypoints are direct
evidence of the body's global pose there.  Scores go to the ab_solver scratch dir; projects are only read."""
import json
import sys

import numpy as np

sys.path.insert(0, "/tmp")
import ab_solver  # noqa: E402

CODE = "/data/video2rig/code"
mods = ab_solver.load_code(CODE)
from v2r.geom import Skeleton, quat_to_mat_np, unit_rot_np  # noqa: E402
import torch  # noqa: E402

PR = "/data/video2rig/projects"


def body_rot(rig, gt, sol):
    sk = Skeleton(rig)
    pel = [b["name"] for b in rig["bones"]].index("Bip001 Pelvis")
    G = np.array([f["G"] for f in gt["frames"]])
    R = quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]]))
    with torch.no_grad():
        w, _ = sk.fk_rot(torch.tensor(G, dtype=torch.float32), torch.tensor(R, dtype=torch.float32))
    Ra, Rb = unit_rot_np(w.numpy()[:, pel, :3, :3]), unit_rot_np(sol["world"][:, pel, :3, :3])
    M = np.einsum("tji,tjk->tik", Ra, Rb)
    return float(np.degrees(np.arccos(np.clip((np.trace(M, axis1=1, axis2=2) - 1) / 2, -1, 1))).mean())


out = {}
for proj in ("m11101", "m11101_nogun"):
    rig = json.load(open(f"{PR}/{proj}/rig.json"))
    studio = json.load(open(f"{PR}/{proj}/studio.json"))
    kn = [k["name"] for k in rig["keypoints"]]
    gun = [i for i, n in enumerate(kn) if n in ("22101_weapon", "25101_bone01", "25101_bone01.tip", "Dumm_input_ke.tip")]
    for ex in ("exam", "exam_strike"):
        rd = {k: v for k, v in np.load(f"{PR}/{proj}/out/{ex}/reading.npz").items()}
        fps = float(np.load(f"{PR}/{proj}/out/{ex}/motion.npz")["fps"])
        gt = json.load(open(f"{PR}/{proj}/{ex}/frames/motion_gt.json"))
        cases = [("as_is", rd)]
        if gun:
            r2 = dict(rd)
            r2["conf_fit"] = rd["conf_fit"].copy()
            r2["conf_fit"][:, :, gun] = 0.0
            cases.append(("gun_kp_weight_0", r2))
        for tag, r in cases:
            torch.manual_seed(0)
            np.random.seed(0)
            sol = ab_solver.solve_variant(mods, rig, studio, r, fps, "legacy")
            m = ab_solver.exam_metrics(CODE, f"{PR}/{proj}", rig, sol, exam_dir=ex)
            m.pop("per_bone")
            m["body_full_rot_deg"] = round(body_rot(rig, gt, sol), 2)
            out[f"{proj}/{ex}/{tag}"] = m
            print(f"{proj:13s} {ex:12s} {tag:16s} joint {m['joint_err_mean_pct_of_size']:.2f}% limb "
                  f"{m['bone_angle_mean_deg']:.1f} twist {m['twist_mean_deg']:.1f} body {m['body_full_rot_deg']:.1f}", flush=True)
json.dump(out, open("/tmp/gun_kp_ablation.json", "w"), indent=1)
