"""Dense correspondence labels for the training pictures (opt-in; the keypoint reader does not need them).

For every training picture (one random pose, one view) and every pixel of a quarter-resolution grid: which point of
the creature's surface the pixel shows, as that point's position in the REST pose, normalised to the body box
(-1..1 per axis; left / right have opposite signs, so the label tells the two sides apart).  Pixels that show
background get no label.

The pose of each picture is the one stored in its meta record (G + every bone's quaternion), the surface is the
rig's own skinned mesh (blender/export_surface.py), skinned with geom.Skeleton.skin_points (checked against
Blender), and the pixel -> surface point lookup is an Open3D ray cast with barycentric interpolation.  Motion blur:
the training pictures of "fast" poses are blurred over the move, the label is the pose at the middle of the
exposure; a per-pixel weight lowers pixels whose surface point moves a lot (see make_dense_labels).

    python -m v2r.dense_labels <project> <surface.npz> [--step 4] [--workers 8]
writes <project>/train_data/dense_<id>.npz per sample:  coord (V, h, w, 3) float16, mask (V, h, w) bool.
"""
import argparse
import glob
import json
import os
import time

import numpy as np
import torch

from v2r.geom import Skeleton, load_surface, quat_to_mat_np


def body_box(surf_V):
    lo, hi = surf_V.min(0), surf_V.max(0)
    return (lo + hi) / 2, (hi - lo) / 2


def pixel_rays(cam, step):
    K, R, t = np.array(cam["K"], float), np.array(cam["R"], float), np.array(cam["t"], float)
    C = -R.T @ t
    us = np.arange(0, cam["w"], step) + step / 2.0
    vs = np.arange(0, cam["h"], step) + step / 2.0
    uu, vv = np.meshgrid(us, vs)
    d = np.stack([uu, vv, np.ones_like(uu)], -1) @ np.linalg.inv(K).T @ R
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    return np.concatenate([np.broadcast_to(C, d.shape), d], -1).astype(np.float32)


def labels_for_pose(scene_fn, Vt, F, V0n, rays):
    """Vt (N,3) posed vertices, F (M,3), V0n (N,3) normalised rest positions, rays per view -> coord, mask."""
    import open3d as o3d
    sc = scene_fn(Vt, F)
    coords, masks = [], []
    for r in rays:
        ans = sc.cast_rays(o3d.core.Tensor(r))
        prim = ans["primitive_ids"].numpy()
        uv = ans["primitive_uvs"].numpy()
        hit = np.isfinite(ans["t_hit"].numpy())
        p = np.where(hit, prim, 0).astype(np.int64)
        tri = F[p]  # h,w,3
        b1, b2 = uv[..., 0], uv[..., 1]
        b0 = 1 - b1 - b2
        c = b0[..., None] * V0n[tri[..., 0]] + b1[..., None] * V0n[tri[..., 1]] + b2[..., None] * V0n[tri[..., 2]]
        c[~hit] = 0
        coords.append(c.astype(np.float16))
        masks.append(hit)
    return np.stack(coords), np.stack(masks)


def make_dense_labels(project, surface_path, step=4, workers=8, overwrite=False):
    import open3d as o3d

    def scene_fn(V, F):
        s = o3d.t.geometry.RaycastingScene()
        s.add_triangles(o3d.core.Tensor(V.astype(np.float32)), o3d.core.Tensor(F.astype(np.uint32)))
        return s

    rig = json.load(open(os.path.join(project, "rig.json")))
    studio = json.load(open(os.path.join(project, "studio.json")))
    td = os.path.join(project, "train_data")
    meta = []
    for f in sorted(glob.glob(os.path.join(td, "meta_*.json"))):
        meta += json.load(open(f))
    sk = Skeleton(rig, dtype=torch.float64)
    surf = load_surface(surface_path, dtype=torch.float64)  # props dropped
    F = surf["F"]
    V0 = surf["V"].numpy()
    ctr, half = body_box(V0)
    V0n = (V0 - ctr) / half
    rays = [pixel_rays(c, step) for c in studio["cams"]]
    t0 = time.time()
    n_done = 0
    for i0 in range(0, len(meta), 32):
        chunk = [m for m in meta[i0:i0 + 32]
                 if overwrite or not os.path.exists(os.path.join(td, f"dense_{m['id']:06d}.npz"))]
        if not chunk:
            continue
        G = torch.tensor(np.array([m["G"] for m in chunk]))
        R = torch.tensor(quat_to_mat_np(np.array([m["quat"] for m in chunk])))
        with torch.no_grad():
            w, kp = sk.fk_rot(G, R)
            Vt = sk.skin_points(w, surf).numpy()
        for j, m in enumerate(chunk):
            assert np.abs(kp[j].numpy() - np.array(m["kp3d"])).max() < 1e-3 * sk.size, "pose does not match labels"
            coord, mask = labels_for_pose(scene_fn, Vt[j], F, V0n, rays)
            np.savez_compressed(os.path.join(td, f"dense_{m['id']:06d}.npz"), coord=coord, mask=mask)
            n_done += 1
        if (i0 // 32) % 10 == 0:
            print(f"[v2r] dense labels {i0 + len(chunk)}/{len(meta)} {(time.time() - t0) / max(n_done, 1):.2f}s/sample",
                  flush=True)
    json.dump({"step": step, "center": ctr.tolist(), "half": half.tolist(), "surface": os.path.abspath(surface_path),
               "views": [c["name"] for c in studio["cams"]]},
              open(os.path.join(td, "dense_meta.json"), "w"), indent=1)
    print(f"[v2r] dense labels: {n_done} samples in {time.time() - t0:.0f}s (grid step {step} px)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("surface")
    ap.add_argument("--step", type=int, default=4)
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()
    make_dense_labels(a.project, a.surface, a.step, overwrite=a.overwrite)
