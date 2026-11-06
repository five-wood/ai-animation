"""Same exam motion, two readers: m11101 (with guns) vs m11101_nogun on m11101's own exam motions (replayed).
Body orientation = full rotation error of the pelvis bone (G * pelvis), which means the same in both rigs; the
grade() 'root' metric compares G only and is not comparable once the pelvis is folded into G."""
import json
import sys

import numpy as np
import torch

sys.path.insert(0, "/data/video2rig/code")
from v2r.geom import Skeleton, quat_to_mat_np, unit_rot_np  # noqa: E402

CASES = [("m11101 (guns)", "/data/video2rig/projects/m11101"),
         ("nogun, same motion", "/data/video2rig/experiments/nogun_same_motion/proj"),
         ("nogun, own exam", "/data/video2rig/projects/m11101_nogun"),
         ("guns, nogun's motion", "/data/video2rig/experiments/nogun_same_motion/proj_guns")]
out = {}
for label, P in CASES:
    rig = json.load(open(f"{P}/rig.json"))
    sk = Skeleton(rig)
    names = [b["name"] for b in rig["bones"]]
    pel = names.index("Bip001 Pelvis")
    for ex in ("exam", "exam_strike"):
        gt = json.load(open(f"{P}/{ex}/frames/motion_gt.json"))
        rep = json.load(open(f"{P}/{ex}/exam_report.json"))
        mot = np.load(f"{P}/out/{ex}/motion.npz")
        G = np.array([f["G"] for f in gt["frames"]])
        R = quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]]))
        with torch.no_grad():
            wg, _ = sk.fk_rot(torch.tensor(G, dtype=torch.float32), torch.tensor(R, dtype=torch.float32))
            wp, _ = sk.fk_rot(torch.tensor(mot["G"], dtype=torch.float32),
                              torch.tensor(quat_to_mat_np(mot["quat"]), dtype=torch.float32))
        Ra, Rb = unit_rot_np(wg.numpy()[:, pel, :3, :3]), unit_rot_np(wp.numpy()[:, pel, :3, :3])
        M = np.einsum("tji,tjk->tik", Ra, Rb)
        body = np.degrees(np.arccos(np.clip((np.trace(M, axis1=1, axis2=2) - 1) / 2, -1, 1)))
        pb = rep["per_bone"]
        common = [n for n in pb if n != "Bip001 Pelvis"]  # same 21 graded bones in both rigs
        limb21 = float(np.mean([pb[n]["mean_deg"] for n in common]))
        twist21 = float(np.mean([pb[n]["twist_mean_deg"] for n in common]))
        out[f"{label}/{ex}"] = {
            "joint_pct": round(rep["joint_err_mean_pct_of_size"], 2), "limb_deg": round(rep["bone_angle_mean_deg"], 2),
            "limb21_deg": round(limb21, 2), "twist21_deg": round(twist21, 2), "passed": rep["passed"],
            "body_deg_mean": round(float(body.mean()), 2), "body_deg_p90": round(float(np.percentile(body, 90)), 1),
            "body_frames_over_20": int((body > 20).sum()),
            "impact_peak_kept": None if "impact" not in rep else round(rep["impact"]["peak_speed_ratio"], 3),
            "impact_stop_lag": None if "impact" not in rep else round(rep["impact"]["stop_lag_frames"], 2),
            "bones": {n: round(pb[n]["mean_deg"], 1) for n in common}}
        o = out[f"{label}/{ex}"]
        print(f"{label:20s} {ex:12s} joint {o['joint_pct']:.2f}% limb(21) {o['limb21_deg']:.1f} twist(21) "
              f"{o['twist21_deg']:.1f} body {o['body_deg_mean']:.1f} (p90 {o['body_deg_p90']}, >20deg "
              f"{o['body_frames_over_20']}) impact {o['impact_peak_kept']} lag {o['impact_stop_lag']}")
for ex in ("exam", "exam_strike"):
    a, b = out[f"m11101 (guns)/{ex}"]["bones"], out[f"nogun, same motion/{ex}"]["bones"]
    print(f"{ex}: per bone, same motion (guns -> nogun):",
          ", ".join(f"{n} {a[n]}->{b[n]}" for n in sorted(a, key=lambda n: -max(a[n], b[n]))[:8]))
json.dump(out, open("/tmp/nogun_replay_compare.json", "w"), indent=1)
