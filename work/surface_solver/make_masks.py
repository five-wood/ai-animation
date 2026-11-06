"""Silhouette masks for the solver A/B, in the solver's image coordinates (studio panel pixels).

  surface_trial venv: python make_masks.py <project dir name> <tag> [--sam] [--gt]

  --sam : SAM 2 (large) video masks of the clip, prompted with the 10 most spread confident keypoints of the
          clip's own reading on frame 0 ("all" mode: gun included, the solver's coverage term ignores outline
          pixels far from the body).  Real clip: masks of the video panels, then moved by the same frame-0
          calibration the pipeline applied to the keypoints.  -> masks/<proj>_<tag>.npz
  --gt  : exam only - sharp silhouette of the true pose (Open3D ray casting of the Blender-skinned mesh,
          without props, no motion blur): the upper bound.  -> masks/<proj>_<tag>_gt.npz
"""
import json
import os
import sys
import time

import cv2
import numpy as np
import torch

E = "/data/video2rig/experiments/surface_solver"
sys.path.insert(0, "/data/video2rig/experiments/surface_trial/scripts")
sys.path.insert(0, f"{E}/code")
from trial_common import read_video, panels, calibration, pixel_rays, o3d_scene  # noqa: E402
from v2r.geom import Skeleton, load_surface, quat_to_mat_np  # noqa: E402
from v2r.silhouette import save_masks  # noqa: E402

PROJ = "/data/video2rig/projects"
REAL = {"m11101": ("Seedance25_Split_screen_two_loc_1793704727806",
                   "Seedance25_Split_screen_two_loc_1793704727806_retrain2_temporal")}


def pdir(name):
    return f"{PROJ}/{name}" if os.path.isdir(f"{PROJ}/{name}") else f"{E}/exams/{name}"


def base(name):
    return name.rsplit("_s", 1)[0] if "_s" in name and name.rsplit("_s", 1)[1].isdigit() else name


def spread(p, n):
    if len(p) <= n:
        return np.arange(len(p))
    idx = [int(np.argmin(np.linalg.norm(p - p.mean(0), axis=1)))]
    d = np.linalg.norm(p - p[idx[0]], axis=1)
    while len(idx) < n:
        j = int(np.argmax(d))
        idx.append(j)
        d = np.minimum(d, np.linalg.norm(p - p[j], axis=1))
    return np.array(idx)


def sam_masks(proj, tag):
    from sam2.build_sam import build_sam2_video_predictor
    P = pdir(proj)
    studio = json.load(open(f"{P}/studio.json"))
    h, w = studio["panel_h"], studio["panel_w"]
    if tag == "real":
        video, outn = f"/data/video2rig/videos/{REAL[proj][0]}.mp4", REAL[proj][1]
    else:
        video, outn = f"{P}/{tag}/exam_video.mp4", tag
    rd = np.load(f"{P}/out/{outn}/reading.npz")
    uv, conf = rd["uv"].copy(), rd["conf"]
    cal = calibration(rd) if tag == "real" else None
    if cal is not None:  # reading coordinates -> video panel coordinates (what SAM 2 sees)
        for v, (s, sh, _) in enumerate(cal):
            uv[:, v] = (uv[:, v] - np.array(sh)) / s
    frames = read_video(video)
    T = len(frames)
    pred = build_sam2_video_predictor("configs/sam2.1/sam2.1_hiera_l.yaml",
                                      "/data/video2rig/experiments/surface_trial/ckpt/sam2.1_hiera_large.pt",
                                      device="cuda")
    out = np.zeros((T, 2, h, w), bool)
    for v in range(2):
        jdir = f"{E}/tmp/{proj}_{tag}_{v}"
        os.makedirs(jdir, exist_ok=True)
        for t, f in enumerate(frames):
            cv2.imwrite(f"{jdir}/{t:05d}.jpg", panels(f, studio)[v], [cv2.IMWRITE_JPEG_QUALITY, 95])
        ok = (conf[0, v] > 0.5) & (uv[0, v, :, 0] >= 0) & (uv[0, v, :, 0] < w) & (uv[0, v, :, 1] >= 0) & (uv[0, v, :, 1] < h)
        pts = uv[0, v, ok]
        pts = pts[spread(pts, 10)].astype(np.float32)
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            st = pred.init_state(video_path=jdir)
            pred.add_new_points_or_box(inference_state=st, frame_idx=0, obj_id=1, points=pts,
                                       labels=np.ones(len(pts), np.int32))
            for f, _, logits in pred.propagate_in_video(st):
                out[f, v] = (logits[0, 0] > 0).cpu().numpy()
            pred.reset_state(st)
        for fn in os.listdir(jdir):
            os.remove(f"{jdir}/{fn}")
        os.rmdir(jdir)
        if cal is not None:
            s, sh, _ = cal[v]
            M = np.float32([[s, 0, sh[0]], [0, s, sh[1]]])
            for t in range(T):
                out[t, v] = cv2.warpAffine(out[t, v].astype(np.uint8), M, (w, h), flags=cv2.INTER_NEAREST) > 0
    os.makedirs(f"{E}/masks", exist_ok=True)
    save_masks(f"{E}/masks/{proj}_{tag}.npz", out)
    return out


def gt_masks(proj, tag):
    P = pdir(proj)
    rig = json.load(open(f"{P}/rig.json"))
    studio = json.load(open(f"{P}/studio.json"))
    gt = json.load(open(f"{P}/{tag}/frames/motion_gt.json"))
    sk = Skeleton(rig, dtype=torch.float64)
    surf = load_surface(f"{E}/surface/{base(proj)}.npz", dtype=torch.float64)
    G = torch.tensor(np.array([f["G"] for f in gt["frames"]]))
    R = torch.tensor(quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]])))
    with torch.no_grad():
        world, _ = sk.fk_rot(G, R)
        Vt = sk.skin_points(world, surf).numpy()
    rays = [pixel_rays(c, 1)[0] for c in studio["cams"]]
    T = len(gt["frames"])
    out = np.zeros((T, 2, studio["panel_h"], studio["panel_w"]), bool)
    import open3d as o3d
    for t in range(T):
        sc = o3d_scene(Vt[t], surf["F"])
        for v in range(2):
            out[t, v] = np.isfinite(sc.cast_rays(o3d.core.Tensor(rays[v]))["t_hit"].numpy())
    os.makedirs(f"{E}/masks", exist_ok=True)
    save_masks(f"{E}/masks/{proj}_{tag}_gt.npz", out)
    return out


if __name__ == "__main__":
    proj, tag = sys.argv[1], sys.argv[2]
    t0 = time.time()
    s = g = None
    if "--gt" in sys.argv:
        g = gt_masks(proj, tag)
    if "--sam" in sys.argv:
        s = sam_masks(proj, tag)
    msg = f"[masks] {proj} {tag}"
    if s is not None and g is not None:
        inter = (s & g).reshape(len(s), 2, -1).sum(-1)
        uni = (s | g).reshape(len(s), 2, -1).sum(-1)
        iou = inter / np.maximum(uni, 1)
        msg += f" SAM vs sharp-GT IoU mean {iou.mean():.4f} min {iou.min():.4f}"
    print(msg + f" ({time.time() - t0:.0f}s)", flush=True)
