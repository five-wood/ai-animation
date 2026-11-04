"""Offline, zero-phase temporal regularisation of a solved skeletal animation.

This is a post-process, NOT a correction to the detected motion or a quality pass.
Local Euler channels are in the solver's bounded XYZ convention; smooth these and
clip to the same joint limits. Smooth root translation about the rig centre and
sign-continuous unit quaternions, not the entries of a rotation matrix. Recompute
keypoints through FK, so lengths stay fixed. Original outputs are never modified.

Minimise ||z-y||² + lambda ||D² z||² jointly over the entire clip. The frequency
parameter denotes approximately half-amplitude gain (not -3dB) at that frequency.
This symmetric offline filter has no causal lag. Boundary transients and amplitude
changes are quantified in stability.json; no promise of exact foot locking.
"""
import os
import re
import json
import time
import shutil
import argparse
import numpy as np
from scipy.linalg import solveh_banded
from scipy.spatial.transform import Rotation

from v2r.geom import Skeleton, Cams, quat_to_mat_np


def regularize(values, fps, cutoff_hz=2.0):
    """Symmetric second-difference smoother, time axis first; handles short clips."""
    values = np.asarray(values, dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("non-finite motion values")
    if fps <= 0 or not 0 < cutoff_hz < fps / 2:
        raise ValueError("cutoff_hz must be positive and below Nyquist (fps/2)")
    T = len(values)
    if T < 3:
        return values.copy()
    lam = 1.0 / (16 * np.sin(np.pi * cutoff_hz / fps) ** 4)
    # Lower-banded SPD matrix I + lambda D2.T D2, including natural boundaries.
    diag = np.ones(T)
    off1 = np.zeros(T - 1)
    off2 = np.zeros(T - 2)
    diag[:-2] += lam
    diag[1:-1] += 4 * lam
    diag[2:] += lam
    off1[:-1] -= 2 * lam
    off1[1:] -= 2 * lam
    off2[:] = lam
    ab = np.zeros((3, T))
    ab[0] = diag
    ab[1, :-1] = off1
    ab[2, :-2] = off2
    result = solveh_banded(ab, values.reshape(T, -1), lower=True)
    return result.reshape(values.shape)


def continuous_quats(q):
    q = np.asarray(q, dtype=np.float64).copy()
    for t in range(1, len(q)):
        dots = np.sum(q[t] * q[t - 1], axis=-1, keepdims=True)
        q[t] *= np.where(dots < 0, -1, 1)
    return q


def stabilize_arrays(rig, motion, cutoff_hz=2.0):
    sk = Skeleton(rig)
    fps = float(motion["fps"])
    G = np.asarray(motion["G"], dtype=np.float64)
    eul = np.asarray(motion["eul"], dtype=np.float64)
    if eul.shape != (len(G), sk.A, 3):
        raise ValueError("Motion active bones do not match the prepared rig")
    E = regularize(eul, fps, cutoff_hz)
    lo, hi = sk.limits[sk.active, :, 0], sk.limits[sk.active, :, 1]
    clip_count = int(((E < lo) | (E > hi)).sum())
    E = np.clip(E, lo, hi)
    # Root path: c' = G*c, not G.translation (rotation-about-centre is coupled to translation).
    centres = np.einsum("tij,j->ti", G[:, :3, :3], sk.center) + G[:, :3, 3]
    sm_centres = regularize(centres, fps, cutoff_hz)
    q_root = continuous_quats(Rotation.from_matrix(G[:, :3, :3]).as_quat())  # xyzw
    sq = regularize(q_root, fps, cutoff_hz)
    norms = np.linalg.norm(sq, axis=-1, keepdims=True)
    if (norms < 1e-5).any():
        raise ValueError("Root rotation changes too abruptly to smooth safely")
    R = Rotation.from_quat(sq / norms).as_matrix()
    gs = np.broadcast_to(np.eye(4), G.shape).copy()
    gs[:, :3, :3] = R
    gs[:, :3, 3] = sm_centres - np.einsum("tij,j->ti", R, sk.center)
    world, kp = sk.fk_np(gs, E)
    quat = continuous_quats(sk.local_quats(E))
    return {"G": gs, "eul": E, "quat": quat, "kp": kp, "fps": fps}, clip_count


def angular_steps(q_wxyz):
    q = continuous_quats(q_wxyz)
    # quaternion angle 2*atan2 is accurate near zero, unlike arccos(dot)
    w0, v0 = q[:-1, ..., :1], q[:-1, ..., 1:]
    w1, v1 = q[1:, ..., :1], q[1:, ..., 1:]
    rel_w = w0 * w1 + (v0 * v1).sum(-1, keepdims=True)
    rel_v = w0 * v1 - w1 * v0 - np.cross(v0, v1)
    rel_v = np.where(rel_w < 0, -rel_v, rel_v)
    angle = 2 * np.arctan2(np.linalg.norm(rel_v, axis=-1), np.abs(rel_w[..., 0]))
    return np.degrees(angle)


def temporal_metrics(rig, motion, studio=None, reading=None):
    sk = Skeleton(rig)
    fps = float(motion["fps"])
    q = np.asarray(motion["quat"])[:, sk.active]
    e = np.asarray(motion["eul"])
    kp = np.asarray(motion["kp"])
    G = np.asarray(motion["G"])
    steps = angular_steps(q)
    accel = np.linalg.norm(np.diff(e, n=2, axis=0), axis=-1) * 180 / np.pi
    root_q = Rotation.from_matrix(G[:, :3, :3]).as_quat()[:, [3, 0, 1, 2]]
    z = kp[:, 0, 2]
    root_range = float(np.ptp(z))
    # Broad minimum (sub-frame stable estimate), not a noise-sensitive argmin of a flat hold.
    low = np.flatnonzero(z <= z.min() + max(0.05 * root_range, 1e-8))
    out = {
        "local_step_p90_deg": float(np.percentile(steps, 90)) if steps.size else 0,
        "local_step_p99_deg": float(np.percentile(steps, 99)) if steps.size else 0,
        "local_euler_accel_rms_deg_per_frame2": float(np.sqrt(np.mean(accel ** 2))) if accel.size else 0,
        "root_step_p90_deg": float(np.percentile(angular_steps(root_q), 90)) if len(G) > 1 else 0,
        "keypoint_accel_rms_pct_size_per_frame2": float(np.sqrt(np.mean(np.sum(np.diff(kp, n=2, axis=0) ** 2, -1))) / sk.size * 100) if len(kp) > 2 else 0,
        "root_height_range": root_range,
        "low_hold_centre_seconds": float(low.mean() / fps) if low.size else 0,
        "per_bone": {},
    }
    for i, bi in enumerate(sk.active):
        out["per_bone"][rig["bones"][bi]["name"]] = {
            "step_p90_deg": float(np.percentile(steps[:, i], 90)) if steps.size else 0,
            "euler_accel_rms_deg_per_frame2": float(np.sqrt(np.mean(accel[:, i] ** 2))) if accel.size else 0}
    if studio is not None and reading is not None:
        pred = np.moveaxis(Cams(studio).project_np(kp), 0, 1)[..., :2]
        res = np.linalg.norm(pred - reading["uv"], axis=-1)
        out["reprojection_median_px"] = float(np.median(res))
        out["reprojection_p90_px"] = float(np.percentile(res, 90))
    return out


def stabilize(project, source, name=None, cutoff_hz=2.0, render=True):
    project = os.path.abspath(project)
    name = name or source + "_stable"
    for n in (source, name):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", n):
            raise ValueError("source/name must be a safe output directory name")
    if source == name:
        raise ValueError("Do not overwrite the original result")
    src = os.path.join(project, "out", source)
    dst = os.path.join(project, "out", name)
    if os.path.exists(dst):
        raise ValueError("Output already exists; choose another name to preserve previous results")
    with open(os.path.join(project, "rig.json")) as f:
        rig = json.load(f)
    with open(os.path.join(project, "studio.json")) as f:
        studio = json.load(f)
    with np.load(os.path.join(src, "motion.npz")) as f:
        motion = {k: f[k].copy() for k in f.files}
    reading = None
    if os.path.isfile(os.path.join(src, "reading.npz")):
        with np.load(os.path.join(src, "reading.npz")) as f:
            reading = {k: f[k].copy() for k in f.files}
    with open(os.path.join(src, "summary.json")) as f:
        source_summary = json.load(f)
    t0 = time.time()
    sm, nclip = stabilize_arrays(rig, motion, cutoff_hz)
    before = temporal_metrics(rig, motion, studio, reading)
    after = temporal_metrics(rig, sm, studio, reading)
    comparison = {"source": source, "cutoff_hz": cutoff_hz,
                  "method": "offline symmetric second-difference regularisation; half-amplitude cutoff",
                  "frames": len(sm["G"]), "fps": sm["fps"], "clipped_channels": nclip,
                  "before": before, "after": after,
                  "root_height_range_ratio": after["root_height_range"] / max(before["root_height_range"], 1e-12),
                  "low_hold_centre_shift_seconds": after["low_hold_centre_seconds"] - before["low_hold_centre_seconds"],
                  "note": "Temporal stability metrics measure smoothness, not motion ground-truth accuracy. No foot locking was added."}
    os.makedirs(dst)
    np.savez(os.path.join(dst, "motion.npz"), **sm)
    for fn in ("check.json", "reading.npz", "diagnostics.json", "diagnostic.mp4"):
        f = os.path.join(src, fn)
        if os.path.isfile(f):
            shutil.copy2(f, os.path.join(dst, fn))
    with open(os.path.join(dst, "stability.json"), "w") as f:
        json.dump(comparison, f, indent=1)
    from v2r.cli import run_blender
    run_blender("export_anim.py", project, os.path.join(dst, "motion.npz"), dst,
                json.dumps({"name": name, "fps": sm["fps"], "render": render}),
                log=os.path.join(dst, "export.log"))
    if render and reading is not None:
        # Find the source clip, not a lab or synthetic reference video.
        video = source_summary.get("source_video") or os.path.join(os.path.dirname(os.path.dirname(project)), "videos", source + ".mp4")
        if os.path.isfile(video):
            from v2r.read import read_video_frames, split_views, find_split
            from v2r.pipeline import make_lab_video
            frames, _ = read_video_frames(video, max_frames=len(sm["G"]))
            split_x = source_summary.get("split_x") or find_split(frames)
            panels = [split_views(fr, split_x, studio, source_summary.get("swap", False))[0] for fr in frames]
            if len(panels) == len(sm["G"]):
                proj = np.moveaxis(Cams(studio).project_np(sm["kp"]), 0, 1)[..., :2]
                make_lab_video(project, dst, panels, reading["uv"], reading["conf"], proj, sm["fps"], rig, studio)
    summary = dict(source_summary)
    summary.update({"status": "forced_preview" if summary.get("forced") else "stabilized", "stabilized_from": source, "cutoff_hz": cutoff_hz,
                    "seconds": time.time() - t0,
                    "reproj_px_median": after.get("reprojection_median_px"),
                    "reproj_px_p90": after.get("reprojection_p90_px")})
    with open(os.path.join(dst, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(f"[v2r] stabilized {source} -> {name} ({cutoff_hz:.2f}Hz)")
    print(f"[v2r] angle acceleration RMS {before['local_euler_accel_rms_deg_per_frame2']:.3f} -> "
          f"{after['local_euler_accel_rms_deg_per_frame2']:.3f} deg/frame^2; "
          f"root height amplitude retained {comparison['root_height_range_ratio'] * 100:.1f}%")
    return comparison
