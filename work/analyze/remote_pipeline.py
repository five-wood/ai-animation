"""video -> checked -> joints -> 3D -> skeleton -> FBX/GLB/BVH + side-by-side lab video."""
import os
import json
import time
import subprocess
import numpy as np
import cv2

from v2r.geom import Cams, mat_to_quat_np
from v2r.read import load_reader, read_video_frames, find_split, split_views, run_reader, write_video
from v2r.solve import fix_swaps, triangulate_all, consistency_report, fit, clean_tracks
from v2r.viz import draw_kps, put_title, composite_bg
from v2r.cli import run_blender


def smooth_quats_sign(q):
    """q (T,B,4): make consecutive quaternions use the same hemisphere."""
    q = q.copy()
    for t in range(1, q.shape[0]):
        s = np.sign((q[t] * q[t - 1]).sum(-1, keepdims=True))
        s[s == 0] = 1
        q[t] *= s
    return q


def calibrate_first_frame(project, rig, uv, conf, n0=3):
    """Seedance starts from our reference picture, so the first frames show the rest pose.
    Fit a per-view 2D similarity (scale + shift) mapping the reader's rest-pose joints onto
    the studio projection; apply only if the first frames really match the rest pose and the
    correction is meaningful (video re-framed / resized by the generator)."""
    path = os.path.join(project, "rest_kp2d.json")
    if not os.path.exists(path):
        return None
    rest = json.load(open(path))
    studio = json.load(open(os.path.join(project, "studio.json")))
    views = []
    params = []
    for v, c in enumerate(studio["cams"]):
        tgt = np.array(rest["proj"][c["name"]])[:, :2]
        src = uv[:n0, v].mean(0)
        w = conf[:n0, v].min(0)
        w = w / max(w.sum(), 1e-9)
        ms, mt = (w[:, None] * src).sum(0), (w[:, None] * tgt).sum(0)
        ss = np.sqrt((w * ((src - ms) ** 2).sum(1)).sum())
        st_ = np.sqrt((w * ((tgt - mt) ** 2).sum(1)).sum())
        s = st_ / max(ss, 1e-9)
        res = np.sqrt((w * (((src - ms) * s + mt - tgt) ** 2).sum(1)).sum())
        size = np.ptp(tgt, 0).max()
        ok = res < 0.06 * size  # first frames really are the rest pose
        shift = (mt - ms * s)
        meaningful = abs(s - 1) > 0.02 or np.linalg.norm(mt - ms) > 0.02 * size
        views.append({"view": c["name"], "scale": float(s), "tx": float(mt[0] - ms[0]), "ty": float(mt[1] - ms[1]),
                      "rest_match_px": float(res), "used": bool(ok and meaningful)})
        params.append((s, shift, ok and meaningful))
    if not any(p[2] for p in params):
        return None

    def apply(u):
        u = u.copy()
        for v, (s, shift, use) in enumerate(params):
            if use:
                u[:, v] = u[:, v] * s + shift
        return u

    return {"views": views, "apply": apply}


def animate(project, video, name=None, split="auto", swap=False, fps=None, max_frames=None, force=False,
            device="cuda", render=True, iters=1500, calib_mode="auto"):
    t0 = time.time()
    rig = json.load(open(os.path.join(project, "rig.json")))
    studio = json.load(open(os.path.join(project, "studio.json")))
    name = name or os.path.splitext(os.path.basename(video))[0]
    out = os.path.join(project, "out", name)
    os.makedirs(out, exist_ok=True)
    cams = Cams(studio)

    # ---------------------------------------------------------------- read
    frames, vfps = read_video_frames(video, max_frames)
    fps = fps or vfps
    sp = find_split(frames) if split == "auto" else (frames[0].shape[1] // 2 if split == "half" else int(split))
    panels, boxes = [], None
    for f in frames:
        p, boxes = split_views(f, sp, studio, swap)
        panels.append(p)
    print(f"[v2r] video {video}: {len(frames)} frames @ {fps:.2f} fps, {frames[0].shape[1]}x{frames[0].shape[0]}, "
          f"split at x={sp}")
    model, st = load_reader(project, device)
    t1 = time.time()
    uv, conf, dep = run_reader(model, st, panels, device)
    print(f"[v2r] joints read in {time.time() - t1:.1f}s")
    del model
    calib = calibrate_first_frame(project, rig, uv, conf) if calib_mode != "off" else None
    if calib is not None:
        uv = calib["apply"](uv)
        print(f"[v2r] frame-0 calibration: " + "; ".join(
            f"{c['view']} scale {c['scale']:.3f} shift ({c['tx']:.1f},{c['ty']:.1f})px" for c in calib["views"]))
    # ---------------------------------------------------------------- check + 3D
    uv_raw = uv.copy()
    uv, conf, n_sw = fix_swaps(cams, rig, uv, conf)
    X, res = triangulate_all(cams, uv, conf)
    rep = consistency_report(cams, rig, X, res, conf, uv)
    rep["label_swaps_fixed"] = n_sw
    print(f"[v2r] check: same move in both pictures = {rep['same_move']} "
          f"(bad frames {rep['bad_frames']}/{len(frames)}, median ray miss {100 * rep['median_residual_rel']:.2f}% "
          f"of body size), near/far swaps fixed: {n_sw}")
    json.dump(rep, open(os.path.join(out, "check.json"), "w"), indent=1)
    if not rep["same_move"] and not force:
        raise SystemExit("[v2r] REJECTED: the two halves of the video do not show the same move - "
                         "generate a new video (or pass --force)")
    # ---------------------------------------------------------------- solve
    t2 = time.time()
    # frames that failed the check get less trust; jumping / non-crossing detections too
    conf_s = conf * np.where(np.array(rep["bad_mask"])[:, None, None], 0.3, 1.0)
    conf_s, n_jump, n_miss = clean_tracks(uv, conf_s, res)
    print(f"[v2r] down-weighted {n_jump} jumping and {n_miss} non-crossing detections "
          f"of {conf.size}")
    X, _ = triangulate_all(cams, uv, conf_s)
    sol = fit(rig, studio, uv, conf_s, X, depth=dep, device=device, iters=iters)
    sk = sol["skeleton"]
    quat = smooth_quats_sign(sk.local_quats(sol["eul"]))
    rep_px = sol["rep_px"]
    print(f"[v2r] solved in {time.time() - t2:.1f}s; reprojection error median {np.median(rep_px):.2f}px "
          f"p90 {np.percentile(rep_px, 90):.2f}px")
    np.savez(os.path.join(out, "motion.npz"), G=sol["G"], quat=quat, eul=sol["eul"], kp=sol["kp"], fps=fps)
    np.savez(os.path.join(out, "reading.npz"), uv=uv, uv_raw=uv_raw, conf=conf, depth=dep, X=X, res=res)
    # ---------------------------------------------------------------- export + preview
    t3 = time.time()
    run_blender("export_anim.py", os.path.abspath(project), os.path.abspath(os.path.join(out, "motion.npz")),
                os.path.abspath(out), json.dumps({"name": name, "fps": fps, "render": render}),
                log=os.path.join(out, "export.log"))
    print(f"[v2r] exported FBX/GLB/BVH in {time.time() - t3:.1f}s -> {out}")
    if render:
        make_lab_video(project, out, panels, uv, conf, sol["proj"], fps, rig, studio)
    summary = {"frames": len(frames), "fps": fps, "check": {k: rep[k] for k in
                                                            ("same_move", "bad_frames", "median_residual_rel",
                                                             "label_swaps_fixed")},
               "reproj_px_median": float(np.median(rep_px)), "reproj_px_p90": float(np.percentile(rep_px, 90)),
               "seconds": time.time() - t0}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    print(f"[v2r] done in {time.time() - t0:.0f}s: {out}")
    return out, sol, summary


def make_lab_video(project, out, panels, uv, conf, proj, fps, rig, studio):
    """Top: the input video with the joints read. Bottom: our model with the solved motion."""
    prev = os.path.join(out, "preview_frames")
    frames = []
    T, V = uv.shape[:2]
    for t in range(T):
        top, bot = [], []
        for v, c in enumerate(studio["cams"]):
            a = draw_kps(panels[t][v], uv[t, v], rig, conf=conf[t, v], r=3)
            put_title(a, f"VIDEO {c['name']}  #{t}")
            top.append(a)
            fn = os.path.join(prev, f"{t:05d}_{c['name']}.png")
            im = cv2.imread(fn, cv2.IMREAD_UNCHANGED)
            im = composite_bg(im) if im is not None else np.zeros_like(a)
            im = draw_kps(im, proj[t, v], rig, r=2)
            put_title(im, f"OUR MODEL {c['name']}")
            bot.append(im)
        f = np.concatenate([np.concatenate(top, 1), np.concatenate(bot, 1)], 0)
        frames.append(cv2.resize(f, (f.shape[1] // 2 * 2 * 3 // 4 // 2 * 2, f.shape[0] * 3 // 4 // 2 * 2)))
    write_video(os.path.join(out, "lab.mp4"), frames, fps)
