"""Evaluate a dense reader on exam clips (and optionally the real clip): read the dense maps on the same crops as the
keypoint reading, locate fixed surface vertices, compare them with the true positions (exams), and solve with the
temporal solver with / without the read dense observations (and with perfect ones for reference).

  surface_trial venv:  python eval_dense_reader.py <project> <clip dirs ...> --out <json>
A clip dir is a project-like directory with <tag>/exam_video.mp4, <tag>/frames/motion_gt.json and out/<tag>/reading.npz
(written by v2r.exam with this project's keypoint reader), given as <dir>:<tag>.
"""
import argparse
import io
import contextlib
import json
import os
import sys
import time

import numpy as np
import torch

D = "/data/video2rig/experiments/surface_solver"
sys.path.insert(0, f"{D}/scripts")
import ab_multi  # noqa: E402
import ab_solver  # noqa: E402

mods = ab_solver.load_code(f"{D}/code")
from v2r.read import load_reader, read_video_frames, split_views, run_reader  # noqa: E402
from v2r.data import crop_matrix, box_from_points  # noqa: E402
from v2r.dense_read import read_dense_maps, match_vertices, rest_normalised  # noqa: E402
from v2r.geom import Skeleton, load_surface, quat_to_mat_np  # noqa: E402
from v2r.silhouette import area_vertex_subset  # noqa: E402


def crops_like_reader(model, st, panels, device):
    """Same pass-2 crops as run_reader (box from the pass-1 keypoints, temporally smoothed)."""
    W, H = st["net_wh"]
    T, V = len(panels), len(panels[0])
    ph, pw = panels[0][0].shape[:2]
    full = np.float32([[W / pw, 0, 0], [0, H / ph, 0]])
    from v2r.read import _read_pass
    uv, conf, _ = _read_pass(model, st, panels, [[full] * V for _ in range(T)], device, 8)[:3]
    boxes = np.zeros((T, V, 3))
    for t in range(T):
        for v in range(V):
            good = conf[t, v] > 0.2
            pts = uv[t, v][good] if good.sum() >= 3 else uv[t, v]
            boxes[t, v] = box_from_points(pts, W, H, margin=1.35)
    k = 2
    pad = np.pad(boxes, ((k, k), (0, 0), (0, 0)), mode="edge")
    boxes = np.median(np.stack([pad[i:i + T] for i in range(2 * k + 1)]), 0)
    return [[crop_matrix(*boxes[t, v], W, H) for v in range(V)] for t in range(T)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--points", type=int, default=400)
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    P = a.project
    rig = json.load(open(f"{P}/rig.json"))
    studio = json.load(open(f"{P}/studio.json"))
    model, st = load_reader(P, "cuda")
    assert st.get("dense"), "reader has no dense head"
    surf = load_surface(f"{P}/surface.npz", device="cuda")
    surf_cpu = load_surface(f"{P}/surface.npz")
    vid = area_vertex_subset(surf_cpu["V"].numpy(), surf_cpu["F"], a.points, 0)
    rest_n = rest_normalised(surf_cpu["V"].numpy(), st["dense_meta"])
    results = json.load(open(a.out)) if os.path.exists(a.out) else {}
    for spec in a.clips:
        cdir, tag = spec.rsplit(":", 1)
        key = f"{os.path.basename(cdir)}:{tag}"
        t0 = time.time()
        if tag == "real":
            vid_path = json.load(open(f"{cdir}/summary.json"))["source_video"]
            out_dir = cdir
        else:
            vid_path = f"{cdir}/{tag}/exam_video.mp4"
            out_dir = f"{cdir}/out/{tag}"
        frames, fps = read_video_frames(vid_path)
        split = frames[0].shape[1] // 2
        panels = [split_views(f, split, studio)[0] for f in frames]
        mats = crops_like_reader(model, st, panels, "cuda")
        xyz, fg, sc, c2p = read_dense_maps(model, st, panels, mats, "cuda")
        uv_d, w_d = match_vertices(xyz, fg, sc, c2p, rest_n, vid)
        rd = {k: x for k, x in np.load(f"{out_dir}/reading.npz").items()}
        if tag == "real":  # the reading was calibrated; move the dense observations the same way
            sys.path.insert(0, "/data/video2rig/experiments/surface_trial/scripts")
            from trial_common import calibration
            for v, (s, sh, _) in enumerate(calibration(rd)):
                uv_d[:, v] = uv_d[:, v] * s + np.array(sh)
        rec = {"read_seconds": round(time.time() - t0, 1), "seen_frac": float((w_d > 0).mean())}
        np.savez_compressed(f"{D}/dense/read_{os.path.basename(cdir)}_{tag}.npz", uv=uv_d, w=w_d, vid=vid)
        if tag != "real":
            gt = json.load(open(f"{cdir}/{tag}/frames/motion_gt.json"))
            # true image positions of the chosen vertices, and true visibility (Open3D)
            sk = Skeleton(rig, dtype=torch.float64)
            s64 = {k: (v.double() if torch.is_tensor(v) and v.is_floating_point() else v) for k, v in surf_cpu.items()}
            G = torch.tensor(np.array([f["G"] for f in gt["frames"]]))
            R = torch.tensor(quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]])))
            import open3d as o3d
            with torch.no_grad():
                world, _ = sk.fk_rot(G, R)
                Vall = sk.skin_points(world, s64).numpy()
            Pt = Vall[:, vid]
            vis = np.zeros(w_d.shape, bool)
            uv_true = np.zeros(uv_d.shape)
            for t in range(len(gt["frames"])):
                scn = o3d.t.geometry.RaycastingScene()
                scn.add_triangles(o3d.core.Tensor(Vall[t].astype(np.float32)), o3d.core.Tensor(surf_cpu["F"].astype(np.uint32)))
                for v, c in enumerate(studio["cams"]):
                    Kc, Rc, tc = np.array(c["K"]), np.array(c["R"]), np.array(c["t"])
                    C = -Rc.T @ tc
                    d = Pt[t] - C
                    dist = np.linalg.norm(d, axis=1)
                    th = scn.cast_rays(o3d.core.Tensor(np.concatenate([np.broadcast_to(C, d.shape), d / dist[:, None]], 1)
                                                       .astype(np.float32)))["t_hit"].numpy()
                    vis[t, v] = th >= dist - 2e-3 * sk.size
                    p = (Pt[t] @ Rc.T + tc) @ Kc.T
                    uv_true[t, v] = p[:, :2] / p[:, 2:3]
            got = w_d > 0
            err = np.linalg.norm(uv_d - uv_true, axis=-1)
            rec.update({"true_visible_frac": float(vis.mean()),
                        "found_of_visible": float((got & vis).sum() / max(vis.sum(), 1)),
                        "false_found_of_hidden": float((got & ~vis).sum() / max((~vis).sum(), 1)),
                        "err_px_median_found": float(np.median(err[got])) if got.any() else None,
                        "err_px_p90_found": float(np.percentile(err[got], 90)) if got.any() else None,
                        "err_px_median_found_visible": float(np.median(err[got & vis])) if (got & vis).any() else None})
        # solves: temporal, + read dense, (exam) + perfect dense
        fps_c = float(np.load(f"{out_dir}/motion.npz")["fps"])
        variants = [("temporal", {}), ("read_dense", {"dense": {"vid": vid, "w": w_d, "uv": uv_d},
                                                       "dense_strength": {"dense_uv": a.strength}, "surface": surf})]
        for name, kw in variants:
            torch.manual_seed(0)
            np.random.seed(0)
            sol = ab_multi.solve_with_kwargs(mods, rig, studio, rd, fps_c, "temporal", kw)
            if tag == "real":
                m = ab_solver.real_metrics(mods, sol["rig"], studio, rd, sol, fps_c)
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    m = ab_solver.exam_metrics(f"{D}/code", cdir, rig, sol, exam_dir=tag)
                from v2r.geom import unit_rot_np
                m["body_deg"], m["body_p90_deg"] = ab_multi.body_error(rig, gt, sol, Skeleton, quat_to_mat_np, unit_rot_np)
            rec[name] = m
            np.savez_compressed(f"{D}/dense/sol_{os.path.basename(cdir)}_{tag}_{name}.npz", G=sol["G"], eul=sol["eul"],
                                world=sol["world"], kp=sol["kp"])
        results[key] = rec
        json.dump(results, open(a.out, "w"), indent=1, default=float)
        line = f"[dense] {key:28s} found {rec.get('found_of_visible', float('nan')):.2f} of visible, err median " \
               f"{rec.get('err_px_median_found') or float('nan'):.1f}px"
        for name, _ in variants:
            m = rec[name]
            if tag == "real":
                line += f" | {name} Rhand {m['step_deg[Bip001 R Hand]']:.2f} local {m['local_jerk_rms_deg']:.2f} " \
                        f"impact {m['impact_peak_mean_pct']:.1f}%"
            else:
                line += f" | {name} limb {m['bone_angle_mean_deg']:.2f} twist {m['twist_mean_deg']:.2f} body {m['body_deg']:.2f}"
        print(line, flush=True)


if __name__ == "__main__":
    main()
