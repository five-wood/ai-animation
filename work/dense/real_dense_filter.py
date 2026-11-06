"""Why do the dense correspondences help on the exams but not on the real clip, and do simple filters fix it?

Filters of the matched surface vertices (per frame), no ground truth needed:
  twoview : the vertex is matched in both views and the two image positions agree in 3D (triangulated point
            reprojects within 1.5% of the body's image size in both views);
  agree   : the matched position is within 8% of the body's image size of where the keypoint-only solve puts it.
Diagnostic: share of matched vertices that pass the two-view test, exams vs real clip (a domain-gap measure).
Solves (temporal, dense strength 3): exams (both clips) and the real clip; real clip also silhouette IoU + jitter.
surface_trial venv; run from experiments/surface_trial/src/sam2 is not needed (no SAM here).
"""
import contextlib
import io
import json
import sys

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
from dense_video import render, rays_of, skin_all  # noqa: E402
from v2r.read import load_reader, read_video_frames, split_views  # noqa: E402
from v2r.dense_read import read_dense_maps, match_vertices, rest_normalised  # noqa: E402
from v2r.geom import Skeleton, Cams, load_surface, quat_to_mat_np, unit_rot_np, triangulate  # noqa: E402
from v2r.silhouette import area_vertex_subset, load_masks  # noqa: E402

CTRL = "/data/video2rig/projects/m11101_tex"
DENSE = "/data/video2rig/projects/m11101_tex_dense"
name = sys.argv[1]
rig = json.load(open(f"{CTRL}/rig.json"))
studio = json.load(open(f"{CTRL}/studio.json"))
cams = Cams(studio)
surf_cpu = load_surface(f"{CTRL}/surface.npz")
surf = load_surface(f"{CTRL}/surface.npz", device="cuda")
vid = area_vertex_subset(surf_cpu["V"].numpy(), surf_cpu["F"], 400, 0)
sk_c = Skeleton(rig, device="cuda")
sub = {k: surf[k][torch.tensor(vid, device="cuda")] for k in ("V", "w_idx", "w_val")}


def scale_of(rd):
    uv = rd["uv"]
    return float(np.median(np.ptp(uv.reshape(-1, uv.shape[2], 2), axis=1).max(-1)))


def twoview_mask(uv, w, scale_px, frac=0.015):
    T, V, N, _ = uv.shape
    keep = np.zeros(w.shape, bool)
    both = (w > 0).all(1)  # T,N
    for t in range(T):
        n = np.flatnonzero(both[t])
        if not len(n):
            continue
        X = triangulate(cams.P, uv[t][:, n].transpose(1, 0, 2), np.ones((len(n), V)))
        p = cams.project_np(X)[..., :2]  # V,n,2
        err = np.linalg.norm(p - uv[t][:, n], axis=-1).max(0)
        ok = n[err < frac * scale_px]
        keep[t, :, ok] = True
    return keep


def agree_mask(uv, w, sol, scale_px, frac=0.08):
    with torch.no_grad():
        P = sk_c.skin_points(torch.tensor(np.asarray(sol["world"]), dtype=torch.float32, device="cuda"), sub)
        p, _ = cams.project_t(P.cpu())  # T,V,N,2
    return (w > 0) & (np.linalg.norm(p.numpy() - uv, axis=-1) < frac * scale_px)


def solve(rd, fps, uv, w, s=3.0):
    kw = {} if s == 0 else {"dense": {"vid": vid, "w": w, "uv": uv}, "dense_strength": {"dense_uv": s}, "surface": surf}
    torch.manual_seed(0)
    np.random.seed(0)
    return ab_multi.solve_with_kwargs(mods, rig, studio, rd, fps, "temporal", kw)


out = {}
# ---------------------------------------------------------------- exams
for tag in ("exam", "exam_strike"):
    rd = {k: x for k, x in np.load(f"{CTRL}/out/{tag}/reading.npz").items()}
    fps = float(np.load(f"{CTRL}/out/{tag}/motion.npz")["fps"])
    gt = json.load(open(f"{CTRL}/{tag}/frames/motion_gt.json"))
    obs = dict(np.load(f"{D}/dense/read_m11101_tex_dense_{tag}.npz"))
    uv, w = obs["uv"], obs["w"]
    sc = scale_of(rd)
    base = solve(rd, fps, uv, w, 0.0)
    tv = twoview_mask(uv, w, sc)
    ag = agree_mask(uv, w, base, sc)
    rec = {"matched": float((w > 0).mean()), "pass_twoview_of_matched": float(tv.sum() / max((w > 0).sum(), 1)),
           "pass_agree_of_matched": float(ag.sum() / max((w > 0).sum(), 1))}
    for nm, mask in (("none", w > 0), ("twoview", tv), ("agree", ag), ("both", tv & ag)):
        sol = base if nm == "base" else solve(rd, fps, uv, w * mask)
        with contextlib.redirect_stdout(io.StringIO()):
            m = ab_solver.exam_metrics(f"{D}/code", CTRL, rig, sol, exam_dir=tag)
        m["body_deg"], _ = ab_multi.body_error(rig, gt, sol, Skeleton, quat_to_mat_np, unit_rot_np)
        rec[nm] = {k: m[k] for k in ("joint_err_mean_pct_of_size", "bone_angle_mean_deg", "twist_mean_deg", "body_deg")}
    with contextlib.redirect_stdout(io.StringIO()):
        mb = ab_solver.exam_metrics(f"{D}/code", CTRL, rig, base, exam_dir=tag)
    mb["body_deg"], _ = ab_multi.body_error(rig, gt, base, Skeleton, quat_to_mat_np, unit_rot_np)
    rec["keypoints_only"] = {k: mb[k] for k in ("joint_err_mean_pct_of_size", "bone_angle_mean_deg", "twist_mean_deg", "body_deg")}
    out[tag] = rec
    print(f"[filter] {tag:12s} matched {rec['matched']:.1%}, pass two-view {rec['pass_twoview_of_matched']:.1%}, "
          f"pass agree {rec['pass_agree_of_matched']:.1%}", flush=True)
    for nm in ("keypoints_only", "none", "twoview", "agree", "both"):
        r = rec[nm]
        print(f"[filter]    {nm:15s} joint {r['joint_err_mean_pct_of_size']:.2f}% limb {r['bone_angle_mean_deg']:.2f} "
              f"twist {r['twist_mean_deg']:.2f} body {r['body_deg']:.2f}", flush=True)

# ---------------------------------------------------------------- real clip
out_dir = f"{CTRL}/out/{name}_temporal"
summ = json.load(open(f"{out_dir}/summary.json"))
rd = {k: x for k, x in np.load(f"{out_dir}/reading.npz").items()}
fps = float(np.load(f"{out_dir}/motion.npz")["fps"])
frames, _ = read_video_frames(summ["source_video"])
panels = [split_views(f, summ["split_x"], studio, summ.get("swap", False))[0] for f in frames]
model, st = load_reader(DENSE, "cuda")
mats = crops_like_reader(model, st, panels, "cuda")
xyz, fg, scm, c2p = read_dense_maps(model, st, panels, mats, "cuda")
del model
torch.cuda.empty_cache()
uv, w = match_vertices(xyz, fg, scm, c2p, rest_normalised(surf_cpu["V"].numpy(), st["dense_meta"]), vid)
for v, (s, sh, _) in enumerate(calibration(rd)):
    uv[:, v] = uv[:, v] * s + np.array(sh)
np.savez_compressed(f"{D}/dense/read_real_{name}.npz", uv=uv, w=w, vid=vid)
sc = scale_of(rd)
base = solve(rd, fps, uv, w, 0.0)
tv = twoview_mask(uv, w, sc)
ag = agree_mask(uv, w, base, sc)
masks = load_masks(f"{D}/masks/m11101_tex_{name}.npz")
rays = [rays_of(c) for c in studio["cams"]]
Rc = [np.array(c["R"]) for c in studio["cams"]]
names = [b["name"] for b in rig["bones"]]


def real_metrics(sol):
    Vt = skin_all(sk_c, torch.tensor(np.asarray(sol["world"]), dtype=torch.float32), surf)
    T = len(Vt)
    iou = np.zeros((T, 2))
    for t in range(T):
        for v in range(2):
            _, hit = render(Vt[t], surf["F"], rays[v][0], rays[v][1], Rc[v])
            iou[t, v] = (hit & masks[t, v]).sum() / max((hit | masks[t, v]).sum(), 1)
    e = np.asarray(sol["eul"])
    jerk = float(np.sqrt(np.mean(np.degrees(np.linalg.norm(np.diff(e, n=2, axis=0), axis=-1)) ** 2)))
    return {"iou_mean": float(iou.mean()), "iou_p10": float(np.percentile(iou, 10)), "iou_min": float(iou.min()),
            "local_jerk_rms_deg": jerk, "iou_per_frame": iou.mean(1).tolist()}


rec = {"matched": float((w > 0).mean()), "pass_twoview_of_matched": float(tv.sum() / max((w > 0).sum(), 1)),
       "pass_agree_of_matched": float(ag.sum() / max((w > 0).sum(), 1))}
print(f"[filter] real {name:7s} matched {rec['matched']:.1%}, pass two-view {rec['pass_twoview_of_matched']:.1%}, "
      f"pass agree {rec['pass_agree_of_matched']:.1%}", flush=True)
for nm, mask in (("keypoints_only", None), ("none", w > 0), ("twoview", tv), ("agree", ag), ("both", tv & ag)):
    sol = base if mask is None else solve(rd, fps, uv, w * mask)
    rec[nm] = real_metrics(sol)
    r = rec[nm]
    print(f"[filter]    {nm:15s} silhouette IoU mean {r['iou_mean']:.4f} p10 {r['iou_p10']:.4f} min {r['iou_min']:.4f} "
          f"local jerk {r['local_jerk_rms_deg']:.2f}", flush=True)
    if nm == "both":
        np.savez_compressed(f"{D}/dense/sol_real_{name}_both.npz", G=sol["G"], eul=sol["eul"], world=sol["world"],
                            kp=sol["kp"], proj=sol["proj"])
out["real"] = rec
json.dump(out, open(f"{D}/real_dense_filter_{name}.json", "w"), indent=1, default=float)
