"""Real clip: keypoints of the control reader (m11101_tex) + dense correspondences read by the dense reader
(m11101_tex_dense).  Temporal solves: keypoints only / + dense (strength 1, 3).  No ground truth, so compared by
 - silhouette agreement: the solved model rendered (Open3D) vs the SAM 2 silhouette of the video, IoU per frame;
 - smoothness: hand rotation relative to the forearm (mean step / mean change of step), whole-body local jerk;
 - keypoint residual (reading vs projection of the solved keypoints).
Writes a comparison video and exports the strength-3 solution (new output name, FBX/GLB/BVH, no preview renders).

  surface_trial venv:  python real_dense_compare.py <video basename>      e.g. ladingwu
"""
import json
import os
import subprocess
import sys
import time

import cv2
import numpy as np
import torch

D = "/data/video2rig/experiments/surface_solver"
sys.path.insert(0, f"{D}/scripts")
sys.path.insert(0, "/data/video2rig/experiments/surface_trial/scripts")
import ab_multi  # noqa: E402
import ab_solver  # noqa: E402

mods = ab_solver.load_code(f"{D}/code")
from eval_dense_reader import crops_like_reader  # noqa: E402
from trial_common import calibration  # noqa: E402
from dense_video import render, outline, text, rays_of, skin_all, FONT_S  # noqa: E402
from v2r.read import load_reader, read_video_frames, split_views  # noqa: E402
from v2r.dense_read import read_dense_maps, match_vertices, rest_normalised  # noqa: E402
from v2r.geom import Skeleton, load_surface, unit_rot_np  # noqa: E402
from v2r.silhouette import area_vertex_subset, save_masks  # noqa: E402

CTRL = "/data/video2rig/projects/m11101_tex"
DENSE = "/data/video2rig/projects/m11101_tex_dense"
name = sys.argv[1]
OUTN = f"{name}_temporal"
out_dir = f"{CTRL}/out/{OUTN}"
VID = f"{D}/video"
os.makedirs(VID, exist_ok=True)
rig = json.load(open(f"{CTRL}/rig.json"))
studio = json.load(open(f"{CTRL}/studio.json"))
summ = json.load(open(f"{out_dir}/summary.json"))
rd = {k: x for k, x in np.load(f"{out_dir}/reading.npz").items()}
fps = float(np.load(f"{out_dir}/motion.npz")["fps"])
frames, _ = read_video_frames(summ["source_video"])
panels = [split_views(f, summ["split_x"], studio, summ.get("swap", False))[0] for f in frames]
T, V = len(panels), len(studio["cams"])
cal = calibration(rd)
print(f"[real] {name}: {T} frames, calibration (scale, shift) {[(round(c[0], 4), [round(x, 2) for x in c[1]]) for c in cal]}",
      flush=True)

# ---------------------------------------------------------------- dense reading
t0 = time.time()
model, st = load_reader(DENSE, "cuda")
mats = crops_like_reader(model, st, panels, "cuda")
xyz, fg, sc, c2p = read_dense_maps(model, st, panels, mats, "cuda")
del model
torch.cuda.empty_cache()
surf_cpu = load_surface(f"{CTRL}/surface.npz")
surf = load_surface(f"{CTRL}/surface.npz", device="cuda")
vid = area_vertex_subset(surf_cpu["V"].numpy(), surf_cpu["F"], 400, 0)
uv_d, w_d = match_vertices(xyz, fg, sc, c2p, rest_normalised(surf_cpu["V"].numpy(), st["dense_meta"]), vid)
for v, (s, sh, _) in enumerate(cal):  # same frame-0 calibration as the keypoints (identity if none was applied)
    uv_d[:, v] = uv_d[:, v] * s + np.array(sh)
print(f"[real] dense read {time.time() - t0:.0f}s; vertices matched per view/frame {(w_d > 0).mean():.1%}", flush=True)

# ---------------------------------------------------------------- solves
sols = {}
for nm, s in (("keypoints", 0.0), ("dense_1", 1.0), ("dense_3", 3.0)):
    kw = {} if s == 0 else {"dense": {"vid": vid, "w": w_d, "uv": uv_d}, "dense_strength": {"dense_uv": s}, "surface": surf}
    torch.manual_seed(0)
    np.random.seed(0)
    sols[nm] = ab_multi.solve_with_kwargs(mods, rig, studio, rd, fps, "temporal", kw)
    print(f"[real] solved {nm}", flush=True)

# ---------------------------------------------------------------- SAM 2 silhouettes of the video
from sam2.build_sam import build_sam2_video_predictor  # noqa: E402
pred = build_sam2_video_predictor("configs/sam2.1/sam2.1_hiera_l.yaml",
                                  "/data/video2rig/experiments/surface_trial/ckpt/sam2.1_hiera_large.pt", device="cuda")
masks = np.zeros((T, V, studio["panel_h"], studio["panel_w"]), bool)
for v in range(V):
    jdir = f"{D}/tmp/real_{name}_{v}"
    os.makedirs(jdir, exist_ok=True)
    for t in range(T):
        cv2.imwrite(f"{jdir}/{t:05d}.jpg", panels[t][v], [cv2.IMWRITE_JPEG_QUALITY, 95])
    s, sh, _ = cal[v]
    p = (rd["uv"][0, v] - np.array(sh)) / s  # reading -> video panel pixels
    ok = (rd["conf"][0, v] > 0.5) & (p[:, 0] >= 0) & (p[:, 0] < studio["panel_w"]) & (p[:, 1] >= 0) & (p[:, 1] < studio["panel_h"])
    pts = p[ok]
    idx = [int(np.argmin(np.linalg.norm(pts - pts.mean(0), axis=1)))]
    dd = np.linalg.norm(pts - pts[idx[0]], axis=1)
    while len(idx) < min(10, len(pts)):
        j = int(np.argmax(dd))
        idx.append(j)
        dd = np.minimum(dd, np.linalg.norm(pts - pts[j], axis=1))
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        stt = pred.init_state(video_path=jdir)
        pred.add_new_points_or_box(inference_state=stt, frame_idx=0, obj_id=1, points=pts[idx].astype(np.float32),
                                   labels=np.ones(len(idx), np.int32))
        for f, _, logits in pred.propagate_in_video(stt):
            m = (logits[0, 0] > 0).cpu().numpy()
            if s != 1.0 or any(abs(x) > 1e-6 for x in sh):
                m = cv2.warpAffine(m.astype(np.uint8), np.float32([[s, 0, sh[0]], [0, s, sh[1]]]),
                                   (studio["panel_w"], studio["panel_h"]), flags=cv2.INTER_NEAREST) > 0
            masks[f, v] = m
        pred.reset_state(stt)
    for fn in os.listdir(jdir):
        os.remove(f"{jdir}/{fn}")
    os.rmdir(jdir)
del pred
torch.cuda.empty_cache()
os.makedirs(f"{D}/masks", exist_ok=True)
save_masks(f"{D}/masks/m11101_tex_{name}.npz", masks)

# ---------------------------------------------------------------- metrics + renders
sk = Skeleton(rig)
sk_c = Skeleton(rig, device="cuda")
names = [b["name"] for b in rig["bones"]]
kn = [k["name"] for k in rig["keypoints"]]
rays = [rays_of(c) for c in studio["cams"]]
Rc = [np.array(c["R"]) for c in studio["cams"]]
F = surf["F"]


def rel_jitter(w, child, parent):
    Rp = unit_rot_np(w[:, names.index(parent), :3, :3])
    Rch = unit_rot_np(w[:, names.index(child), :3, :3])
    rel = np.einsum("tji,tjk->tik", Rp, Rch)
    step = np.einsum("tji,tjk->tik", rel[:-1], rel[1:])
    ang = np.degrees(np.arccos(np.clip((np.trace(step, axis1=1, axis2=2) - 1) / 2, -1, 1)))
    return float(ang.mean()), float(np.abs(np.diff(ang)).mean())


Vs, metrics, ious = {}, {}, {}
for nm, sol in sols.items():
    w = np.asarray(sol["world"])
    Vs[nm] = skin_all(sk_c, torch.tensor(w, dtype=torch.float32), surf)
    iou = np.zeros((T, V))
    for t in range(T):
        for v in range(V):
            _, hit = render(Vs[nm][t], F, rays[v][0], rays[v][1], Rc[v])
            m = masks[t, v]
            iou[t, v] = (hit & m).sum() / max((hit | m).sum(), 1)
    ious[nm] = iou
    res = np.linalg.norm(np.asarray(sol["proj"]) - rd["uv"], axis=-1)
    hands = [i for i, n in enumerate(kn) if "Hand" in n or "Finger" in n]
    jr = {f"{side} hand": rel_jitter(w, f"Bip001 {side[0]} Hand", f"Bip001 {side[0]} Forearm") for side in ("Right", "Left")}
    e = np.asarray(sol["eul"])
    metrics[nm] = {
        "silhouette_iou_mean": float(iou.mean()), "silhouette_iou_p10": float(np.percentile(iou, 10)),
        "silhouette_iou_min": float(iou.min()),
        "hand_step_deg_R": jr["Right hand"][0], "hand_jerk_deg_R": jr["Right hand"][1],
        "hand_step_deg_L": jr["Left hand"][0], "hand_jerk_deg_L": jr["Left hand"][1],
        "local_jerk_rms_deg": float(np.sqrt(np.mean(np.degrees(np.linalg.norm(np.diff(e, n=2, axis=0), axis=-1)) ** 2))),
        "kp_residual_px_median": float(np.median(res)), "kp_residual_px_median_hands": float(np.median(res[:, :, hands])),
    }
    m = metrics[nm]
    print(f"[real] {nm:10s} silhouette IoU mean {m['silhouette_iou_mean']:.4f} p10 {m['silhouette_iou_p10']:.4f} "
          f"min {m['silhouette_iou_min']:.4f} | hand step/jerk R {m['hand_step_deg_R']:.2f}/{m['hand_jerk_deg_R']:.2f} "
          f"L {m['hand_step_deg_L']:.2f}/{m['hand_jerk_deg_L']:.2f} local jerk {m['local_jerk_rms_deg']:.2f} | "
          f"kp residual {m['kp_residual_px_median']:.2f} px (hands {m['kp_residual_px_median_hands']:.2f})", flush=True)
json.dump({"metrics": metrics, "dense_matched_frac": float((w_d > 0).mean()),
           "iou_per_frame": {k: v.tolist() for k, v in ious.items()}},
          open(f"{D}/real_dense_{name}.json", "w"), indent=1)

# ---------------------------------------------------------------- comparison video
pw, ph = studio["panel_w"] // 2, studio["panel_h"] // 2
head = 100
W, H = 3 * pw, head + 2 * ph
raw = f"{VID}/m11101_tex_{name}_dense.raw.mp4"
vw = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
gain = ious["dense_3"].mean(1) - ious["keypoints"].mean(1)
still_t = int(np.argmax(gain))
for t in range(T):
    canvas = np.full((H, W, 3), 32, np.uint8)
    for v in range(V):
        tiles = [panels[t][v].copy()]
        for nm in ("keypoints", "dense_3"):
            img, _ = render(Vs[nm][t], F, rays[v][0], rays[v][1], Rc[v])
            tiles.append(outline(img, masks[t, v]))
        for i, tile in enumerate(tiles):
            tile = cv2.resize(tile, (pw, ph), interpolation=cv2.INTER_AREA)
            if i == 0:
                tile = text(tile, (8, 6), "正面" if v == 0 else "侧面", font=FONT_S, bg=(0, 0, 0))
            else:
                nm = ("keypoints", "dense_3")[i - 1]
                tile = text(tile, (8, ph - 34), f"剪影重合 {ious[nm][t, v]:.3f}", font=FONT_S, bg=(0, 0, 0))
            canvas[head + v * ph:head + (v + 1) * ph, i * pw:(i + 1) * pw] = tile
    labels = [("输入视频", f"{name}  第 {t + 1}/{T} 帧"),
              ("只用关键点", f"平均剪影重合 {metrics['keypoints']['silhouette_iou_mean']:.3f}"),
              ("+ 识别出的稠密对应", f"平均剪影重合 {metrics['dense_3']['silhouette_iou_mean']:.3f}")]
    for i, (a, b) in enumerate(labels):
        canvas = text(canvas, (i * pw + 10, 6), a)
        canvas = text(canvas, (i * pw + 10, 40), b, font=FONT_S, fill=(200, 200, 200))
    canvas = text(canvas, (pw + 10, 70), "绿线 = 视频里怪物的轮廓（SAM 2 抠出，越贴合越好）", font=FONT_S, fill=(80, 220, 80))
    vw.write(canvas)
    if t == still_t:
        cv2.imwrite(f"{VID}/m11101_tex_{name}_dense_f{t:03d}.png", canvas)
vw.release()
mp4 = f"{VID}/m11101_tex_{name}_dense.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", mp4], check=True)
os.remove(raw)
print(f"[real] video {mp4}, still frame {still_t}", flush=True)

# ---------------------------------------------------------------- export the strength-3 solution (new name)
from v2r.pipeline import smooth_quats_sign  # noqa: E402
from v2r.cli import run_blender  # noqa: E402
ex = f"{CTRL}/out/{name}_dense3"
if not os.path.exists(f"{ex}/motion.npz"):
    os.makedirs(ex, exist_ok=True)
    sol = sols["dense_3"]
    quat = smooth_quats_sign(sk.local_quats(np.asarray(sol["eul"])))
    np.savez(f"{ex}/motion.npz", G=sol["G"], quat=quat, eul=sol["eul"], kp=sol["kp"], fps=fps)
    json.dump({"frames": T, "fps": fps, "source_video": summ["source_video"], "split_x": summ["split_x"],
               "solver": "temporal + dense correspondences (experimental)", "keypoint_reader": CTRL,
               "dense_reader": DENSE, "dense_strength": 3.0, "experimental": True, "status": "complete"},
              open(f"{ex}/summary.json", "w"), indent=1)
    run_blender("export_anim.py", CTRL, f"{ex}/motion.npz", ex,
                json.dumps({"name": f"{name}_dense3", "fps": fps, "render": False}), log=f"{ex}/export.log")
    print(f"[real] exported {ex}", flush=True)
