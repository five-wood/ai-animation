"""Solver A/B over many exam clips (several random seeds, smooth + strike) and the real clip.

    python ab_multi.py --code <code dir> --variants 'legacy;temporal;legacy:{"silhouette_strength":0.3}' \
        --clips m11101:exam,m11101:exam_strike,m11101_s11:exam,... --real m11101 --out /tmp/x.json

A clip "<name>:<tag>" is a project directory (the real projects under /data/video2rig/projects, the extra seeds
under experiments/surface_solver/exams) with out/<tag>/reading.npz and <tag>/frames/motion_gt.json.  Every
variant of one solver family gets the identical observations (ab_solver.solve_variant).  Scores are written to a
scratch directory; projects are only read.

Extra per clip: whole-body orientation error = full 3D rotation error of the main skeleton's first active bone
below the root (pelvis), which means the same whatever the rig folds into G.

Surface variants: kwargs "silhouette_strength" / "coverage_strength" make the harness load
experiments/surface_solver/surface/<project>.npz and masks/<clip>.npz and pass them to the solver as
surface= / masks= (the solver ignores them at strength 0); "masks_kind": "gt" uses the sharp true silhouette;
"dense": {...} (temporal only) adds perfect dense observations (see load_aux).

Run with the surface_trial venv (Open3D) for dense variants; the plain venv is enough otherwise.
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/data/video2rig/experiments/surface_solver/scripts")
sys.path.insert(0, "/data/video2rig/experiments/surface_solver/code")  # v2r with the local changes, before ab_solver
import ab_solver  # noqa: E402

PROJ = "/data/video2rig/projects"
EXAMS = "/data/video2rig/experiments/surface_solver/exams"
D = "/data/video2rig/experiments/surface_solver"
REAL = {"m11101": "Seedance25_Split_screen_two_loc_1793704727806_retrain2_temporal"}


def project_dir(name):
    return f"{PROJ}/{name}" if os.path.isdir(f"{PROJ}/{name}") else f"{EXAMS}/{name}"


def base_project(name):
    """m11101_s11 -> m11101 (the surface / rig owner)."""
    return name.rsplit("_s", 1)[0] if "_s" in name and name.rsplit("_s", 1)[1].isdigit() else name


def body_bone(rig):
    bones = rig["bones"]
    roots = [i for i, b in enumerate(bones) if b["parent"] < 0]

    def top(i):
        while bones[i]["parent"] >= 0:
            i = bones[i]["parent"]
        return i

    main = max(roots, key=lambda r: sum(1 for i in range(len(bones)) if top(i) == r))
    for i, b in enumerate(bones):  # first bone below the main root (topological order)
        if b["parent"] == main:
            return i
    return main


def body_error(rig, gt, sol, Skeleton, quat_to_mat_np, unit_rot_np):
    sk = Skeleton(rig)
    bi = body_bone(rig)
    G = np.array([f["G"] for f in gt["frames"]])
    R = quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]]))
    with torch.no_grad():
        w, _ = sk.fk_rot(torch.tensor(G, dtype=torch.float32), torch.tensor(R, dtype=torch.float32))
    Ra, Rb = unit_rot_np(w.numpy()[:, bi, :3, :3]), unit_rot_np(np.asarray(sol["world"])[:, bi, :3, :3])
    M = np.einsum("tji,tjk->tik", Ra, Rb)
    a = np.degrees(np.arccos(np.clip((np.trace(M, axis1=1, axis2=2) - 1) / 2, -1, 1)))
    return float(a.mean()), float(np.percentile(a, 90))


def perfect_dense(proj, tag, surf, n_points=400, noise_px=0.0, seed=0):
    """Ground-truth dense observations of an exam clip: the true image position, camera normal and camera
    depth of fixed surface vertices, only where Open3D ray casting says the vertex is seen (weight 1)."""
    import open3d as o3d
    from v2r.geom import Skeleton, quat_to_mat_np
    from v2r.silhouette import area_vertex_subset
    P = project_dir(proj)
    rig = json.load(open(f"{P}/rig.json"))
    studio = json.load(open(f"{P}/studio.json"))
    gt = json.load(open(f"{P}/{tag}/frames/motion_gt.json"))
    sk = Skeleton(rig, dtype=torch.float64)
    s64 = {k: (v.double() if torch.is_tensor(v) and v.is_floating_point() else v) for k, v in surf.items()}
    G = torch.tensor(np.array([f["G"] for f in gt["frames"]]))
    R = torch.tensor(quat_to_mat_np(np.array([f["quat"] for f in gt["frames"]])))
    with torch.no_grad():
        world, _ = sk.fk_rot(G, R)
        Vall = sk.skin_points(world, s64).numpy()
        vid = area_vertex_subset(s64["V"].numpy(), surf["F"], n_points, seed)
        sub = {"V": s64["V"][vid], "N": s64["N"][vid], "w_idx": s64["w_idx"][vid], "w_val": s64["w_val"][vid]}
        Pt, Nt = sk.skin_points(world, sub, normals=True)
    Pt, Nt = Pt.numpy(), Nt.numpy()
    T, V, N = len(gt["frames"]), len(studio["cams"]), len(vid)
    uv = np.zeros((T, V, N, 2))
    nrm = np.zeros((T, V, N, 3))
    dep = np.zeros((T, V, N))
    w = np.zeros((T, V, N))
    rng = np.random.RandomState(seed)
    eps = 2e-3 * sk.size
    for t in range(T):
        sc = o3d.t.geometry.RaycastingScene()
        sc.add_triangles(o3d.core.Tensor(Vall[t].astype(np.float32)), o3d.core.Tensor(surf["F"].astype(np.uint32)))
        for v, c in enumerate(studio["cams"]):
            Kc, Rc, tc = np.array(c["K"]), np.array(c["R"]), np.array(c["t"])
            C = -Rc.T @ tc
            d = Pt[t] - C
            dist = np.linalg.norm(d, axis=1)
            rays = np.concatenate([np.broadcast_to(C, d.shape), d / dist[:, None]], 1).astype(np.float32)
            th = sc.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
            Xc = Pt[t] @ Rc.T + tc
            p = Xc @ Kc.T
            uv[t, v] = p[:, :2] / p[:, 2:3] + rng.randn(N, 2) * noise_px
            nrm[t, v] = Nt[t] @ Rc.T
            dep[t, v] = Xc[:, 2]
            w[t, v] = th >= dist - eps
    return {"vid": vid, "w": w, "uv": uv, "normal": nrm, "depth": dep}


def load_aux(kw, proj, tag, device="cuda"):
    """Surface / mask / dense data for surface variants (only loaded when such a term is on).
    kwargs: "masks_kind": "sam" (default) or "gt" (sharp true silhouette); "dense": {"uv": s, "normal": s,
    "depth": s, "points": n, "noise_px": x} = perfect dense observations of the exam with these strengths."""
    sil = any(kw.get(k, 0) for k in ("silhouette_strength", "coverage_strength"))
    dense = kw.get("dense")
    kind = kw.get("masks_kind", "sam")
    kw = {k: v for k, v in kw.items() if k not in ("masks_kind", "dense")}
    if not sil and not dense:
        return kw
    from v2r.geom import load_surface
    kw["surface"] = load_surface(f"{D}/surface/{base_project(proj)}.npz", device=device)
    if sil:
        from v2r.silhouette import load_masks
        kw["masks"] = load_masks(f"{D}/masks/{proj}_{tag}{'_gt' if kind == 'gt' else ''}.npz")
    if dense:
        n, noise = int(dense.get("points", 400)), float(dense.get("noise_px", 0.0))
        cache = f"{D}/dense/{proj}_{tag}_{n}_{noise}.npz"
        if os.path.exists(cache):
            obs = dict(np.load(cache))
        else:
            obs = perfect_dense(proj, tag, load_surface(f"{D}/surface/{base_project(proj)}.npz"), n, noise)
            os.makedirs(f"{D}/dense", exist_ok=True)
            np.savez_compressed(cache, **obs)
        use = {k: obs[k] for k in ("vid", "w")}
        for k in ("uv", "normal", "depth"):
            if dense.get(k, 0) > 0:
                use[k] = obs[k]
        kw["dense"] = use
        kw["dense_strength"] = {f"dense_{k}": float(dense.get(k, 0)) for k in ("uv", "normal", "depth")}
    return kw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=f"{D}/code")
    ap.add_argument("--variants", required=True, help="';'-separated family[:json-kwargs]")
    ap.add_argument("--clips", required=True, help="comma separated <project>:<tag>")
    ap.add_argument("--real", default="", help="comma separated real projects (m11101)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    mods = ab_solver.load_code(a.code)
    from v2r.geom import Skeleton, quat_to_mat_np, unit_rot_np
    ab_solver.SCRATCH = "/tmp/v2r_ab_multi_scratch"
    variants = [v for v in a.variants.split(";") if v]
    results = json.load(open(a.out)) if os.path.exists(a.out) else {}
    jobs = [(c.split(":")[0], c.split(":")[1]) for c in a.clips.split(",") if c]
    jobs += [(r, "real") for r in a.real.split(",") if r]
    for v in variants:
        family, _, opts = v.partition(":")
        kw0 = json.loads(opts) if opts else {}
        for proj, tag in jobs:
            key = f"{proj}:{tag}"
            if key in results.get(v, {}):
                continue
            if tag == "real" and (kw0.get("masks_kind") == "gt" or kw0.get("dense")):
                continue  # ground-truth inputs exist only for the exams
            P = project_dir(proj)
            rig = json.load(open(f"{P}/rig.json"))
            studio = json.load(open(f"{P}/studio.json"))
            out_dir = f"{P}/out/{REAL[proj]}" if tag == "real" else f"{P}/out/{tag}"
            rd = {k: x for k, x in np.load(f"{out_dir}/reading.npz").items()}
            fps = float(np.load(f"{out_dir}/motion.npz")["fps"])
            kw = load_aux(kw0, proj, tag)
            torch.manual_seed(0)
            np.random.seed(0)
            t0 = time.time()
            sol = solve_with_kwargs(mods, rig, studio, rd, fps, family, kw)
            rec = {"seconds": round(time.time() - t0, 1)}
            if tag == "real":
                rec.update(ab_solver.real_metrics(mods, sol["rig"], studio, rd, sol, fps))
            else:
                with contextlib.redirect_stdout(io.StringIO()):
                    m = ab_solver.exam_metrics(a.code, P, rig, sol, exam_dir=tag)
                gt = json.load(open(f"{P}/{tag}/frames/motion_gt.json"))
                m["body_deg"], m["body_p90_deg"] = body_error(rig, gt, sol, Skeleton, quat_to_mat_np, unit_rot_np)
                rec.update(m)
            results.setdefault(v, {})[key] = rec
            json.dump(results, open(a.out, "w"), indent=1)
            line = f"[ab] {v[:60]:60s} {key:24s}"
            if tag == "real":
                line += (f" Rhand {rec['step_deg[Bip001 R Hand]']:.2f}/{rec['jerk_deg[Bip001 R Hand]']:.2f} local "
                         f"{rec['local_jerk_rms_deg']:.2f} impact {rec['impact_peak_mean_pct']:.2f}% stop "
                         f"{rec['impact_stop_frames_mean']:.1f}f res hands {rec['res_px_median_hands']:.2f}")
            else:
                im = rec.get("impact") or {}
                line += (f" joint {rec['joint_err_mean_pct_of_size']:.2f}% limb {rec['bone_angle_mean_deg']:.2f} twist "
                         f"{rec['twist_mean_deg']:.2f} body {rec['body_deg']:.2f}"
                         + (f" peak {im['peak_speed_ratio']:.0%} lag {im['stop_lag_frames']:.2f}" if im else ""))
            print(line + f" {rec['seconds']:.0f}s", flush=True)


def solve_with_kwargs(mods, rig, studio, rd, fps, family, kw):
    """ab_solver.solve_variant, but kwargs may hold non-JSON objects (surface tensors, masks)."""
    solve, solve_temporal, Cams, Skeleton, unit_rot_np, triangulate_all, observation_weights = mods
    kw = dict(kw)
    freeze = kw.pop("freeze", [])
    corroborate = kw.pop("corroborate", False)
    rig = ab_solver.freeze_rig(rig, freeze)
    if family == "temporal":
        kw.setdefault("robust_pos", 0.003)
        kw.setdefault("robust_rot", 0.02)
    uv, dep = rd["uv"], rd["depth"]
    cams = Cams(studio)
    if family == "legacy":
        conf_s, X_fit = (rd["conf_fit"], rd["X_fit"]) if "conf_fit" in rd else (rd["conf"], rd["X"])
        sol = solve.fit(rig, studio, uv, conf_s, X_fit, depth=dep, verbose=False, **kw)
    elif family == "temporal":
        conf_s, _ = observation_weights(rig, studio, uv, rd["conf"], rd["res"], fps, None, corroborate=corroborate)
        X_fit, _ = triangulate_all(cams, uv, conf_s)
        sol = solve_temporal.fit_temporal(rig, studio, uv, conf_s, X_fit, fps, depth=dep, verbose=False, **kw)
    else:
        raise ValueError(family)
    sol["rig"] = rig
    return sol


if __name__ == "__main__":
    main()
