"""SAM 2 trial (experimental, read-only on the projects).

Silhouettes of the creature in each panel of the exam videos (compared with the rendered alpha and with the
sharp Open3D silhouette) and of the real Seedance clip (frame 0 compared with the rest-pose silhouette after
the same frame-0 calibration the pipeline applies to the keypoints; keypoint coverage; area stability).
Prompts are the reader's own confident keypoints - no manual clicks.

Variants: model large / tiny; prompts on frame 0 only or every 30 frames; "all" (gun included, positive)
or "body" (gun keypoints as negative points, compared with the body-only silhouette).
"""
import json
import os
import sys
import time

import cv2
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trial_common import (E, PROJ, load_project, prop_keypoints, iou, boundary_dist, read_video, panels,  # noqa: E402
                          calibration, warp_mask)
from sam2.build_sam import build_sam2_video_predictor  # noqa: E402

MODELS = {"large": ("configs/sam2.1/sam2.1_hiera_l.yaml", f"{E}/ckpt/sam2.1_hiera_large.pt"),
          "tiny": ("configs/sam2.1/sam2.1_hiera_t.yaml", f"{E}/ckpt/sam2.1_hiera_tiny.pt")}
REAL = "Seedance25_Split_screen_two_loc_1793704727806"
CLIPS = [
    ("m11101", "exam", f"{PROJ}/m11101/exam/exam_video.mp4", f"{PROJ}/m11101/out/exam/reading.npz"),
    ("m11101", "exam_strike", f"{PROJ}/m11101/exam_strike/exam_video.mp4", f"{PROJ}/m11101/out/exam_strike/reading.npz"),
    ("lizard", "exam", f"{PROJ}/lizard/exam/exam_video.mp4", f"{PROJ}/lizard/out/exam/reading.npz"),
    ("lizard", "exam_strike", f"{PROJ}/lizard/exam_strike/exam_video.mp4", f"{PROJ}/lizard/out/exam_strike/reading.npz"),
    ("m11101", "real", f"/data/video2rig/videos/{REAL}.mp4", f"{PROJ}/m11101/out/{REAL}_retrain2_temporal/reading.npz"),
]
K3 = np.ones((3, 3), np.uint8)


def spread(p, n):
    """Farthest-point subset of n points (deterministic, starts at the most central one)."""
    if n is None or len(p) <= n:
        return np.arange(len(p))
    idx = [int(np.argmin(np.linalg.norm(p - p.mean(0), axis=1)))]
    d = np.linalg.norm(p - p[idx[0]], axis=1)
    while len(idx) < n:
        j = int(np.argmax(d))
        idx.append(j)
        d = np.minimum(d, np.linalg.norm(p - p[j], axis=1))
    return np.array(idx)


def prompt_points(uv, conf, prop, t, v, mode, w, h, npos=10, thr=0.5):
    """Positive points: confident keypoints of the reader (props included in mode "all"), spread over the body.
    Mode "body": confident prop (gun) keypoints become negative points."""
    p, c = uv[t, v], conf[t, v]
    ok = (c > thr) & (p[:, 0] >= 0) & (p[:, 0] < w) & (p[:, 1] >= 0) & (p[:, 1] < h)
    pos = p[ok] if mode == "all" else p[ok & ~prop]
    pos = pos[spread(pos, npos)]
    neg = p[ok & prop] if mode == "body" else p[:0]
    return np.concatenate([pos, neg]), np.concatenate([np.ones(len(pos), np.int32), np.zeros(len(neg), np.int32)])


def run(pred, state, uv, conf, prop, v, mode, frames, w, h, npos):
    pred.reset_state(state)
    for t in frames:
        pts, lab = prompt_points(uv, conf, prop, t, v, mode, w, h, npos)
        if len(pts):
            pred.add_new_points_or_box(inference_state=state, frame_idx=int(t), obj_id=1,
                                       points=pts.astype(np.float32), labels=lab)
    out = {}
    for f, _, logits in pred.propagate_in_video(state):
        out[f] = (logits[0, 0] > 0).cpu().numpy()
    return np.stack([out[f] for f in range(len(out))])


def mask_stats(pred, gt):
    """pred, gt (T,H,W) bool."""
    ious, bds, holes, leaks = [], [], [], []
    for p, g in zip(pred, gt):
        ious.append(iou(p, g))
        bds.append(boundary_dist(p, g))
        core = cv2.erode(g.astype(np.uint8), K3, iterations=5) > 0
        near = cv2.dilate(g.astype(np.uint8), K3, iterations=5) > 0
        holes.append(float((core & ~p).sum() / max(core.sum(), 1)))
        leaks.append(float((p & ~near).sum() / max(g.sum(), 1)))
    ious = np.array(ious)
    return {"iou_mean": float(ious.mean()), "iou_min": float(ious.min()), "iou_p5": float(np.percentile(ious, 5)),
            "frac_iou_below_0.95": float((ious < 0.95).mean()), "boundary_px_mean": float(np.nanmean(bds)),
            "hole_frac_mean": float(np.mean(holes)), "hole_frac_max": float(np.max(holes)),
            "leak_frac_mean": float(np.mean(leaks)), "leak_frac_max": float(np.max(leaks)),
            "worst_frame": int(np.argmin(ious)), "per_frame_iou": [round(float(x), 4) for x in ious]}


def overlay(img, mask, gt=None, pts=None, lab=None):
    o = img.copy()
    o[mask] = (0.55 * o[mask] + 0.45 * np.array([0, 0, 255])).astype(np.uint8)
    if gt is not None:
        cs, _ = cv2.findContours(gt.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(o, cs, -1, (0, 255, 0), 1)
    if pts is not None:
        for (x, y), l in zip(pts, lab):
            cv2.circle(o, (int(x), int(y)), 4, (255, 255, 0) if l else (255, 0, 255), -1)
    return o


def main():
    models = {}
    summary = {}
    for proj, tag, video, reading in CLIPS:
        clip = f"{proj}_{tag}"
        P, rig, studio = load_project(proj)
        cams = studio["cams"]
        h, w = studio["panel_h"], studio["panel_w"]
        rd = np.load(reading)
        is_real = tag == "real"
        # rd["uv"] and rd["conf"] are both after the near/far swap fix (same keypoint order).  The exam runs with
        # calibration off, so rd["uv"] is in panel pixels; for the real clip the calibration is undone to get
        # video panel pixels (the frames SAM 2 sees).
        uv = rd["uv"].copy()
        conf = rd["conf"]
        if is_real:
            for v, (s_, sh, _) in enumerate(calibration(rd)):
                uv[:, v] = (uv[:, v] - np.array(sh)) / s_
        prop = prop_keypoints(rig)
        frames = read_video(video)
        T = len(frames)
        pans = [[panels(f, studio)[v] for f in frames] for v in range(2)]
        gt_alpha = gt_sharp = gt_body = None
        if not is_real:
            gt = json.load(open(f"{P}/{tag}/frames/motion_gt.json"))
            gt_alpha = np.zeros((T, 2, h, w), bool)
            for t, fr in enumerate(gt["frames"]):
                for v, c in enumerate(cams):
                    gt_alpha[t, v] = cv2.imread(f"{P}/{tag}/frames/{fr[c['name']]['img']}", cv2.IMREAD_UNCHANGED)[..., 3] > 127
            s = np.load(f"{E}/data/{clip}_o3dsil.npz")
            shp = tuple(s["shape"])
            gt_sharp = np.unpackbits(s["full"], -1)[..., :shp[-1]].astype(bool)
            gt_body = np.unpackbits(s["body"], -1)[..., :shp[-1]].astype(bool)
        # (model, mode, prompt frames, number of positive points; None = every confident keypoint)
        variants = [("large", "all", "f0", 10), ("large", "all", "f0", None), ("large", "all", "every30", 10),
                    ("tiny", "all", "f0", 10)]
        if prop.any():
            variants.append(("large", "body", "f0", 10))
        res = {"frames": T, "variants": {}}
        for v in range(2):
            jdir = f"{E}/tmp/{clip}_{cams[v]['name']}"
            os.makedirs(jdir, exist_ok=True)
            for t, im in enumerate(pans[v]):
                cv2.imwrite(f"{jdir}/{t:05d}.jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 95])
        for mname in ("large", "tiny"):
            if mname not in models:
                cfg, ck = MODELS[mname]
                models[mname] = build_sam2_video_predictor(cfg, ck, device="cuda")
            pred = models[mname]
            for v in range(2):
                cname = cams[v]["name"]
                jdir = f"{E}/tmp/{clip}_{cname}"
                with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                    t0 = time.time()
                    state = pred.init_state(video_path=jdir)
                    t_init = time.time() - t0
                    for (mn, mode, sched, npos) in variants:
                        if mn != mname:
                            continue
                        key = f"{mn}_{mode}_{sched}_p{npos or 'all'}"
                        fr_list = [0] if sched == "f0" else list(range(0, T, 30))
                        t1 = time.time()
                        m = run(pred, state, uv, conf, prop, v, mode, fr_list, w, h, npos)
                        dt = time.time() - t1
                        r = res["variants"].setdefault(key, {"views": {}})
                        rv = {"init_s": t_init, "propagate_s": dt, "fps": T / dt}
                        if not is_real:
                            ref = gt_body[:, v] if mode == "body" else gt_sharp[:, v]
                            rv["vs_alpha"] = mask_stats(m, gt_alpha[:, v])
                            rv["vs_sharp_mesh"] = mask_stats(m, ref)
                            wf = rv["vs_alpha"]["worst_frame"]
                            pts, lab = prompt_points(uv, conf, prop, 0, v, mode, w, h, npos)
                            cv2.imwrite(f"{E}/out/sam_{clip}_{cname}_{key}_worst{wf:03d}.png",
                                        overlay(pans[v][wf], m[wf], gt_alpha[wf, v]))
                            cv2.imwrite(f"{E}/out/sam_{clip}_{cname}_{key}_f000.png",
                                        overlay(pans[v][0], m[0], gt_alpha[0, v], pts, lab))
                        else:
                            rest = np.load(f"{E}/data/m11101_rest_sil.npz")
                            ref0 = rest["body"][v] if mode == "body" else rest["full"][v]
                            cal = calibration(rd)[v]
                            m0w = warp_mask(m[0], cal[0], cal[1])
                            rv["frame0_vs_rest"] = {"iou_calibrated": iou(m0w, ref0), "iou_uncalibrated": iou(m[0], ref0),
                                                    "boundary_px_calibrated": boundary_dist(m0w, ref0),
                                                    "boundary_px_uncalibrated": boundary_dist(m[0], ref0),
                                                    "calibration_scale_shift_resid": list(cal)}
                            cv2.imwrite(f"{E}/out/sam_{clip}_{cname}_{key}_f000_vs_rest.png",
                                        overlay(cv2.warpAffine(pans[v][0], np.float32([[cal[0], 0, cal[1][0]], [0, cal[0], cal[1][1]]]),
                                                               (w, h), borderValue=(128, 128, 128)), m0w, ref0))
                            body_kp = ~prop
                            cov = []
                            for t in range(T):
                                ok = (conf[t, v] > 0.5) & body_kp
                                p = np.round(uv[t, v, ok]).astype(int)
                                inside = (p[:, 0] >= 0) & (p[:, 0] < w) & (p[:, 1] >= 0) & (p[:, 1] < h)
                                md = cv2.dilate(m[t].astype(np.uint8), K3, iterations=3) > 0
                                cov.append(float(md[p[inside, 1], p[inside, 0]].mean()) if inside.any() else 1.0)
                            area = m.reshape(T, -1).sum(1).astype(float)
                            rv["real"] = {"kp_inside_mean": float(np.mean(cov)), "kp_inside_min": float(np.min(cov)),
                                          "area_rel_median_min": float(area.min() / np.median(area)),
                                          "area_rel_median_max": float(area.max() / np.median(area)),
                                          "area_step_max": float(np.max(np.abs(np.diff(area)) / np.median(area)))}
                            for t in (0, 30, 60, 90, 120):
                                if t < T:
                                    pts, lab = prompt_points(uv, conf, prop, t, v, mode, w, h, npos)
                                    cv2.imwrite(f"{E}/out/sam_{clip}_{cname}_{key}_f{t:03d}.png",
                                                overlay(pans[v][t], m[t], None, pts if t == 0 else None, lab))
                            np.savez_compressed(f"{E}/data/sam_{clip}_{cname}_{key}.npz", mask=np.packbits(m, -1),
                                                shape=np.array(m.shape))
                        r["views"][cname] = rv
                    pred.reset_state(state)
                    del state
                    torch.cuda.empty_cache()
        summary[clip] = res
        json.dump(res, open(f"{E}/out/sam2_{clip}.json", "w"), indent=1)
        print(f"== {clip} ({T} frames)")
        for key, r in res["variants"].items():
            for cname, rv in r["views"].items():
                line = f"   {key:26s} {cname:5s} {rv['fps']:5.1f} fps"
                if "vs_alpha" in rv:
                    a, s = rv["vs_alpha"], rv["vs_sharp_mesh"]
                    line += (f" | vs alpha IoU {a['iou_mean']:.4f} (min {a['iou_min']:.4f} @{a['worst_frame']}) "
                             f"bd {a['boundary_px_mean']:.2f}px holes {a['hole_frac_mean']:.4f}/{a['hole_frac_max']:.4f} "
                             f"leak {a['leak_frac_mean']:.4f}/{a['leak_frac_max']:.4f} | vs sharp IoU {s['iou_mean']:.4f} "
                             f"(min {s['iou_min']:.4f})")
                else:
                    f0, rr = rv["frame0_vs_rest"], rv["real"]
                    line += (f" | f0 vs rest IoU calib {f0['iou_calibrated']:.4f} raw {f0['iou_uncalibrated']:.4f} "
                             f"bd {f0['boundary_px_calibrated']:.2f}/{f0['boundary_px_uncalibrated']:.2f}px | kp inside "
                             f"{rr['kp_inside_mean']:.3f} (min {rr['kp_inside_min']:.3f}) area {rr['area_rel_median_min']:.2f}"
                             f"..{rr['area_rel_median_max']:.2f} step {rr['area_step_max']:.3f}")
                print(line, flush=True)
    json.dump(summary, open(f"{E}/out/sam2_summary.json", "w"), indent=1)
    print("SAM2 DONE")


if __name__ == "__main__":
    main()
