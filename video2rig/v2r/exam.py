"""The exam: make up a motion ourselves (every joint random but inside its limits),
film it in our studio with the same two cameras, let the tool read it like any
Seedance clip, and compare its answer with the motion we made, bone by bone."""
import os
import json
import random
import numpy as np
import cv2
import torch

from v2r.cli import run_blender
from v2r.data import studio_background, add_shadow
from v2r.geom import Skeleton, quat_to_mat_np, unit_rot_np, twist_angle_np
from v2r.read import write_video
from v2r.pipeline import animate


def build_exam_video(project, frames_dir, gt, out_path, compress=True):
    studio = json.load(open(os.path.join(project, "studio.json")))
    rng = random.Random(0)
    h, w = studio["panel_h"], studio["panel_w"]
    clean = studio.get("visual_profile", "legacy") == "soft-clean"
    bgs = [np.full((h,w,3),200.,np.float32) if clean else studio_background(h,w,rng)
           for _ in studio["cams"]]
    out = []
    for fr in gt["frames"]:
        row = []
        for v, c in enumerate(studio["cams"]):
            rgba = cv2.imread(os.path.join(frames_dir, fr[c["name"]]["img"]), cv2.IMREAD_UNCHANGED)
            a = rgba[..., 3].astype(np.float32) / 255
            bg = bgs[v].copy() if clean else add_shadow(bgs[v].copy(), a, random.Random(1))
            img = rgba[..., :3].astype(np.float32) * a[..., None] + bg * (1 - a[..., None])
            row.append(np.clip(img, 0, 255).astype(np.uint8))
        out.append(np.concatenate(row, 1))
    tmp = out_path + ".raw.mp4"
    write_video(tmp, out, gt["fps"])
    crf = 30 if compress else 14
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-c:v", "libx264", "-crf", str(crf),
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path], check=True)
    os.remove(tmp)


def angle_between(a, b):
    c = (a * b).sum(-1) / (np.linalg.norm(a, axis=-1) * np.linalg.norm(b, axis=-1) + 1e-12)
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def exam(project, frames=150, seed=7, render=True, compress=True, device="cuda", video_only=False):
    rig = json.load(open(os.path.join(project, "rig.json")))
    ex = os.path.join(project, "exam")
    fr_dir = os.path.join(ex, "frames")
    os.makedirs(fr_dir, exist_ok=True)
    gt_path = os.path.join(fr_dir, "motion_gt.json")
    if render or not os.path.exists(gt_path):
        run_blender("gen_poses.py", os.path.abspath(project), os.path.abspath(fr_dir),
                    json.dumps({"mode": "motion", "frames": frames, "seed": seed, "samples": 16}),
                    log=os.path.join(ex, "render.log"))
    gt = json.load(open(gt_path))
    video = os.path.join(ex, "exam_video.mp4")
    build_exam_video(project, fr_dir, gt, video, compress)
    if video_only:
        return None
    out, sol, summ = animate(project, video, name="exam", split="half", device=device, calib_mode="off")
    return grade(project, rig, gt, sol["G"], sol["world"], sol["kp"], summ["reproj_px_median"])


def regrade(project):
    """Grade the existing exam result again (out/exam/motion.npz) without rendering or solving."""
    rig = json.load(open(os.path.join(project, "rig.json")))
    gt = json.load(open(os.path.join(project, "exam", "frames", "motion_gt.json")))
    out = os.path.join(project, "out", "exam")
    mot = np.load(os.path.join(out, "motion.npz"))
    sk = Skeleton(rig)
    with torch.no_grad():
        w_pr, kp_pr = sk.fk_rot(torch.tensor(mot["G"], dtype=torch.float32),
                                torch.tensor(quat_to_mat_np(mot["quat"]), dtype=torch.float32))
    summ = {}
    if os.path.exists(os.path.join(out, "summary.json")):
        summ = json.load(open(os.path.join(out, "summary.json")))
    return grade(project, rig, gt, mot["G"], w_pr.numpy(), kp_pr.numpy(), summ.get("reproj_px_median"))


def grade(project, rig, gt, G_pr, w_pr, kp_pr, reproj_px_median=None):
    ex = os.path.join(project, "exam")
    sk = Skeleton(rig)
    T = len(gt["frames"])
    G_gt = np.array([f["G"] for f in gt["frames"]])
    R_gt = quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]]))
    with torch.no_grad():
        w_gt, kp_gt = sk.fk_rot(torch.tensor(G_gt, dtype=torch.float32), torch.tensor(R_gt, dtype=torch.float32))
    w_gt, kp_gt = w_gt.numpy(), kp_gt.numpy()
    kp_bl = np.array([f["kp3d"] for f in gt["frames"]])
    assert np.abs(kp_bl - kp_gt).max() < 1e-3 * sk.size, "FK mismatch with Blender ground truth"
    # 3D joint error (world, absolute) and after removing the global offset (pose only)
    e3 = np.linalg.norm(kp_pr - kp_gt, axis=-1)
    off = (kp_pr - kp_gt).mean(1, keepdims=True)
    e3_local = np.linalg.norm(kp_pr - off - kp_gt, axis=-1)
    active = sk.active
    # bone by bone, for every bone that moves a keypoint:
    #  * limb direction: angle of the bone's real limb axis (head -> next keypoint), the
    #    thing two cameras can see.  This is the graded "bone angle".
    #  * twist: rotation about that axis (swing-twist split), reported separately; a single
    #    point per bone cannot show it, so it is not part of pass/fail.
    #  * yaxis: the previous metric (Blender +Y), kept for comparison with older reports.
    axes, src = sk.limb_axes()
    ang = angle_between(sk.limb_dirs(w_gt)[:, active], sk.limb_dirs(w_pr)[:, active])
    ang_y = angle_between(sk.bone_dirs(w_gt)[:, active], sk.bone_dirs(w_pr)[:, active])
    Rg, Rp = unit_rot_np(w_gt[:, active, :3, :3]), unit_rot_np(w_pr[:, active, :3, :3])
    R_rel = np.einsum("tbji,tbjk->tbik", Rg, Rp)  # gt^T pr, in the bone's own frame
    twist = np.abs(np.degrees(twist_angle_np(R_rel, axes[active][None])))
    root_ang = angle_between(G_gt[:, :3, 1], np.asarray(G_pr)[:, :3, 1])
    names = [rig["bones"][i]["name"] for i in active]
    per_bone = {n: {"mean_deg": float(ang[:, j].mean()), "p90_deg": float(np.percentile(ang[:, j], 90)),
                    "twist_mean_deg": float(twist[:, j].mean()), "yaxis_mean_deg": float(ang_y[:, j].mean()),
                    "axis": src[i]}
                for j, (i, n) in enumerate(zip(active, names))}
    per_kp = {k["name"]: float(e3[:, i].mean() / sk.size * 100) for i, k in enumerate(rig["keypoints"])}
    report = {
        "frames": T,
        "metric_version": 2,
        "joint_err_mean_pct_of_size": float(e3.mean() / sk.size * 100),
        "joint_err_p90_pct_of_size": float(np.percentile(e3, 90) / sk.size * 100),
        "pose_err_mean_pct_of_size": float(e3_local.mean() / sk.size * 100),
        "bone_angle_mean_deg": float(ang.mean()),
        "bone_angle_median_deg": float(np.median(ang)),
        "bone_angle_p90_deg": float(np.percentile(ang, 90)),
        "twist_mean_deg": float(twist.mean()),
        "twist_median_deg": float(np.median(twist)),
        "twist_p90_deg": float(np.percentile(twist, 90)),
        "bone_yaxis_angle_mean_deg": float(ang_y.mean()),
        "root_angle_mean_deg": float(root_ang.mean()),
        "frames_all_bones_within_15deg": float((ang.max(1) < 15).mean()),
        "reproj_px_median": reproj_px_median,
        "per_bone": per_bone, "per_keypoint_pct": per_kp,
        "size": sk.size,
    }
    # pass / fail thresholds (tune per production needs)
    report["passed"] = bool(report["bone_angle_mean_deg"] < 10 and report["joint_err_mean_pct_of_size"] < 3)
    json.dump(report, open(os.path.join(ex, "exam_report.json"), "w"), indent=1)
    worst = sorted(per_bone.items(), key=lambda kv: -kv[1]["mean_deg"])[:5]
    worst_tw = sorted(per_bone.items(), key=lambda kv: -kv[1]["twist_mean_deg"])[:5]
    n_x = sum(1 for i in active if src[i] != "y" and abs(axes[i][1]) < 0.9)
    print("[v2r] ===== EXAM =====")
    print(f"[v2r] joint error mean {report['joint_err_mean_pct_of_size']:.2f}% of body size "
          f"(p90 {report['joint_err_p90_pct_of_size']:.2f}%), pose-only {report['pose_err_mean_pct_of_size']:.2f}%")
    print(f"[v2r] bone angle (limb direction) error mean {report['bone_angle_mean_deg']:.1f} deg, median "
          f"{report['bone_angle_median_deg']:.1f}, p90 {report['bone_angle_p90_deg']:.1f}; root {report['root_angle_mean_deg']:.1f} deg")
    print(f"[v2r] twist error (not graded) mean {report['twist_mean_deg']:.1f} deg, median {report['twist_median_deg']:.1f}, "
          f"p90 {report['twist_p90_deg']:.1f}; old +Y-axis metric {report['bone_yaxis_angle_mean_deg']:.1f} deg "
          f"({n_x}/{len(active)} bones have a limb axis off Blender +Y)")
    print("[v2r] worst bones: " + ", ".join(f"{n} {v['mean_deg']:.1f}" for n, v in worst))
    print("[v2r] worst twist: " + ", ".join(f"{n} {v['twist_mean_deg']:.1f}" for n, v in worst_tw))
    print(f"[v2r] EXAM {'PASSED' if report['passed'] else 'FAILED'}")
    return report
