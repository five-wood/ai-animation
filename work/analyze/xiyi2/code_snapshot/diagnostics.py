"""Diagnostics available even when a clip cannot be reconstructed consistently.

A ray residual is disagreement between detected 2D points under the reference cameras.
It is not ground truth for whether Seedance or the reader caused the disagreement.
"""
import os
import json
import cv2
import numpy as np

from v2r.viz import draw_kps, put_title
from v2r.read import write_video


def frame_ranges(mask, fps):
    """Inclusive frame ranges, end_seconds is exclusive (end of the final frame)."""
    mask = np.asarray(mask, dtype=bool)
    if not mask.size:
        return []
    starts = np.flatnonzero(mask & ~np.r_[False, mask[:-1]])
    ends = np.flatnonzero(mask & ~np.r_[mask[1:], False])
    return [{"start_frame": int(a), "end_frame": int(b), "start_seconds": float(a / fps),
             "end_seconds": float((b + 1) / fps)} for a, b in zip(starts, ends)]


def build_diagnostics(rig, studio, uv, conf, res, report, fps):
    scale = max(float(report["size_px"]), 1.0)
    T, V, K = conf.shape
    valid = conf.min(axis=1) > 0.3
    ray_pct = res.max(axis=-1) / scale * 100  # T,K
    records = []
    bad = np.asarray(report["bad_mask"], dtype=bool)
    motion = bad if bad.any() else np.ones(T, dtype=bool)
    for k, kp in enumerate(rig["keypoints"]):
        good = valid[:, k]
        values = ray_pct[good, k]
        sel = good & motion
        active_values = ray_pct[sel, k]
        records.append({"index": k, "name": kp["name"], "side": kp["side"],
                        "median_ray_pct": float(np.median(values)) if values.size else None,
                        "p90_ray_pct": float(np.percentile(values, 90)) if values.size else None,
                        "flagged_segment_median_ray_pct": float(np.median(active_values)) if active_values.size else None,
                        "mean_confidence": float(conf[:, :, k].mean()),
                        "valid_frames": int(good.sum())})
    records.sort(key=lambda r: -(r["flagged_segment_median_ray_pct"] or 0))
    fr = np.asarray(report["frame_residual_rel"], dtype=float)
    le = np.asarray(report["frame_bonelen_err"], dtype=float)
    return {"version": 1, "frames": T, "fps": float(fps), "duration_seconds": T / fps,
            "decision": "consistent" if report["same_move"] else "needs_review",
            "meaning": "Two-view detections disagree under the reference cameras; this alone does not prove that the generated motions differ.",
            "thresholds": {"frame_ray_pct": 3.0, "frame_bonelen_relative": 0.35,
                           "maximum_bad_fraction_exclusive": 0.25},
            "bad_ranges": frame_ranges(bad, fps),
            "ray_flagged_frames": int((fr > 0.03).sum()),
            "bonelen_flagged_frames": int((le > 0.35).sum()),
            "low_confidence_frames": int((valid.sum(1) < max(4, K // 2)).sum()),
            "keypoints": records,
            "note": "Confidence is the heatmap peak, not a calibrated probability. Occlusion, identity swaps, re-framing, detector domain shift and truly inconsistent video can all increase residuals."}


def save_reading(out, uv, uv_raw, conf, depth, X, res, uv_reader=None, X_fit=None, conf_fit=None):
    # X and res must always refer to the same (check-stage) triangulation.
    arrays = dict(uv=uv, uv_raw=uv_raw, conf=conf, depth=depth, X=X, res=res)
    if X_fit is not None:
        arrays["X_fit"] = X_fit
    if conf_fit is not None:
        arrays["conf_fit"] = conf_fit
    if uv_reader is not None:
        arrays["uv_reader"] = uv_reader
    np.savez(os.path.join(out, "reading.npz"), **arrays)


def make_diagnostic_video(out, panels, uv, conf, res, report, rig, studio, fps):
    """Only shows observed points (not the solved model). Mark high ray residuals in red.

    An epipolar line is drawn for the worst disagreement among reliable correspondences
    in the opposite view. It gives an inspectable constraint instead of claiming to know
    the correct location of the hidden joint.
    """
    from v2r.geom import Cams
    cams = Cams(studio)
    size_px = max(float(report["size_px"]), 1.0)
    # Fundamental matrix for reference views: x1^T F x0 = 0.
    Rrel = cams.R[1] @ cams.R[0].T
    trel = cams.t[1] - Rrel @ cams.t[0]
    tx, ty, tz = trel
    skew = np.array([[0, -tz, ty], [tz, 0, -tx], [-ty, tx, 0]])
    F = np.linalg.inv(cams.K[1]).T @ skew @ Rrel @ np.linalg.inv(cams.K[0])
    frames = []
    for t, panels_t in enumerate(panels):
        per_joint = res[t].max(-1) / size_px
        reliable = conf[t].min(0) > 0.3
        candidates = np.flatnonzero(reliable)
        worst = int(candidates[np.argmax(per_joint[candidates])]) if candidates.size else None
        row = []
        for v, c in enumerate(studio["cams"]):
            im = draw_kps(panels_t[v], uv[t, v], rig, conf=conf[t, v], r=3)
            high = reliable & (per_joint > 0.03)
            for k in np.flatnonzero(high):
                point = tuple(np.rint(uv[t, v, k]).astype(int))
                cv2.circle(im, point, 7, (40, 40, 240), 1, cv2.LINE_AA)
            if worst is not None and per_joint[worst] > 0.03:
                other = 1 - v
                hp = np.r_[uv[t, other, worst], 1.0]
                line = (F if v == 1 else F.T) @ hp
                a, b, cc = line
                if abs(b) > abs(a) and abs(b) > 1e-9:
                    p0, p1 = (0, int(-cc / b)), (c["w"] - 1, int(-(a * (c["w"] - 1) + cc) / b))
                elif abs(a) > 1e-9:
                    p0, p1 = (int(-cc / a), 0), (int(-(b * (c["h"] - 1) + cc) / a), c["h"] - 1)
                else:
                    p0 = p1 = (0, 0)
                p0 = tuple(int(np.clip(x, -100000, 100000)) for x in p0)
                p1 = tuple(int(np.clip(x, -100000, 100000)) for x in p1)
                cv2.line(im, p0, p1, (80, 210, 240), 1, cv2.LINE_AA)
                put_title(im, f"worst: {rig['keypoints'][worst]['name']}  {100 * per_joint[worst]:.1f}%", (8, 66), 0.42)
            put_title(im, f"READ {c['name']}  frame {t}  {t / fps:.2f}s", (8, 20), 0.46)
            ray = report["frame_residual_rel"][t] * 100
            verdict = "REVIEW" if report["bad_mask"][t] else "within threshold"
            put_title(im, f"median ray miss {ray:.2f}%  {verdict}", (8, 43), 0.42)
            put_title(im, "red rings: >3% residual; yellow: other-view epipolar line", (8, im.shape[0] - 12), 0.35)
            row.append(im)
        frames.append(np.concatenate(row, 1))
    write_video(os.path.join(out, "diagnostic.mp4"), frames, fps)
