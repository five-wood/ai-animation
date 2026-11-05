"""Open3D trial (experimental, read-only on the projects).

Per exam frame, with the posed mesh exported by export_trial_meshes.py:
  * visibility of 3000 fixed surface samples in both studio cameras by Open3D ray casting, compared with
    the Blender BVH reference computed in the export (same geometry, same rule);
  * the plan's simple point z-buffer at 1/4 (and 1/8) resolution, with and without a 3x3 neighbourhood and
    with the mesh vertices as extra occluders, compared with the same reference;
  * sharp silhouettes (and body-only silhouettes without the props) by casting one ray per pixel, compared
    with the exam's rendered alpha (which has motion blur);
  * a depth convention check (Open3D camera-plane depth vs the projected z of visible samples);
  * timings.
Writes data/<clip>_o3dsil.npz, data/<project>_rest_sil.npz, out/open3d_<clip>.json, a few PNGs.
"""
import json
import os
import sys
import time

import cv2
import numpy as np
from scipy.ndimage import minimum_filter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trial_common import E, load_project, project, pixel_rays, o3d_scene, cast, iou, boundary_dist  # noqa: E402

CLIPS = [("m11101", "exam"), ("m11101", "exam_strike"), ("lizard", "exam"), ("lizard", "exam_strike")]
INVALID = np.uint32(0xFFFFFFFF)


def zbuffer_vis(uvz, occ, cam, cell, nb, tol):
    W, H = cam["w"] // cell, cam["h"] // cell
    grid = np.full((H, W), np.inf)

    def cells(p):
        cu = np.floor(p[:, 0] / cell).astype(int)
        cv = np.floor(p[:, 1] / cell).astype(int)
        ok = (cu >= 0) & (cu < W) & (cv >= 0) & (cv < H) & (p[:, 2] > 0)
        return cu, cv, ok

    cu, cv, ok = cells(occ)
    np.minimum.at(grid, (cv[ok], cu[ok]), occ[ok, 2])
    if nb > 1:
        grid = minimum_filter(grid, size=nb, mode="constant", cval=np.inf)
    su, sv, sok = cells(uvz)
    vis = np.zeros(len(uvz), bool)
    vis[sok] = uvz[sok, 2] <= grid[sv[sok], su[sok]] + tol
    return vis


class Agree:
    def __init__(self):
        self.n = self.same = self.fv = self.fh = self.hid = self.vis = 0

    def add(self, pred, ref, m):
        p, r = pred[m], ref[m]
        self.n += len(p)
        self.same += int((p == r).sum())
        self.fv += int((p & ~r).sum())
        self.fh += int((~p & r).sum())
        self.hid += int((~r).sum())
        self.vis += int(r.sum())

    def result(self):
        return {"accuracy": self.same / max(self.n, 1),
                "false_visible_of_hidden": self.fv / max(self.hid, 1),
                "false_hidden_of_visible": self.fh / max(self.vis, 1),
                "n": self.n}


def silhouettes(scene, rays, tri_prop):
    t, prim = cast(scene, rays)
    hit = np.isfinite(t)
    body = hit.copy()
    body[hit] = ~tri_prop[prim[hit]]
    return hit, body, t


def main():
    summary = {}
    rest_done = set()
    for proj, ex in CLIPS:
        clip = f"{proj}_{ex}"
        P, rig, studio = load_project(proj)
        cams = studio["cams"]
        d = np.load(f"{E}/data/{clip}_mesh.npz")
        V, F, tri_prop = d["V"], d["F"], d["tri_prop"].astype(bool)
        s_tri, s_bary, vis_ref, inimg = d["s_tri"], d["s_bary"], d["vis_ref"], d["inimg"]
        size = float(d["size"])
        eps = 1e-3 * size
        T, nV = V.shape[:2]
        H, W = cams[0]["h"], cams[0]["w"]
        gt = json.load(open(f"{P}/{ex}/frames/motion_gt.json"))
        rays_full = [pixel_rays(c, 1) for c in cams]
        rays_q = [pixel_rays(c, 4) for c in cams]
        Cc = [(-np.array(c["R"]).T @ np.array(c["t"])) for c in cams]

        zb_cfgs = {f"zb_cell{cell}_nb{nb}_{occ}_tol{int(tol * 1000)}": (cell, nb, occ, tol)
                   for cell in (4, 8) for nb in (1, 3) for occ in ("samples", "samples+verts") for tol in (0.01, 0.03)}
        ag = {"open3d": Agree(), **{k: Agree() for k in zb_cfgs}}
        sil_full = np.zeros((T, 2, H, W), bool)
        sil_body = np.zeros((T, 2, H, W), bool)
        alpha_iou, alpha_bd, blur_band, sharp_vs_soft = [], [], [], []
        depth_err = []
        tm = {"scene_build_and_6000_rays_ms": [], "full_res_silhouettes_2views_ms": [],
              "quarter_res_silhouettes_2views_ms": [], "zbuffer_ms": []}
        for t in range(T):
            t0 = time.time()
            scene = o3d_scene(V[t], F)
            Pts = (s_bary[:, :, None] * V[t][F[s_tri]]).sum(1)
            vis_o3d = np.zeros((2, len(Pts)), bool)
            for v, c in enumerate(cams):
                dvec = Pts - Cc[v]
                dist = np.linalg.norm(dvec, axis=1)
                rays = np.concatenate([np.broadcast_to(Cc[v], dvec.shape), dvec / dist[:, None]], 1)
                th, _ = cast(scene, rays)
                vis_o3d[v] = th >= dist - eps
            tm["scene_build_and_6000_rays_ms"].append((time.time() - t0) * 1000)
            t1 = time.time()
            for v, c in enumerate(cams):
                hit, body, th = silhouettes(scene, rays_full[v][0], tri_prop)
                sil_full[t, v], sil_body[t, v] = hit, body
                if t % 10 == 0:  # depth convention: camera-plane depth at a visible sample's pixel = its projected z
                    zmap = th * rays_full[v][1]
                    uvz = project(c, Pts)
                    m = vis_o3d[v] & inimg[t, v]
                    u = np.clip(np.floor(uvz[m, 0]).astype(int), 0, W - 1)
                    vv = np.clip(np.floor(uvz[m, 1]).astype(int), 0, H - 1)
                    e = np.abs(zmap[vv, u] - uvz[m, 2]) / size
                    depth_err.append(np.median(e[np.isfinite(e)]))
            tm["full_res_silhouettes_2views_ms"].append((time.time() - t1) * 1000)
            t2 = time.time()
            for v in range(2):
                silhouettes(scene, rays_q[v][0], tri_prop)
            tm["quarter_res_silhouettes_2views_ms"].append((time.time() - t2) * 1000)
            t3 = time.time()
            for v, c in enumerate(cams):
                uvz = project(c, Pts)
                occ_v = np.concatenate([uvz, project(c, V[t])])
                ag["open3d"].add(vis_o3d[v], vis_ref[t, v], inimg[t, v])
                for k, (cell, nb, occ, tol) in zb_cfgs.items():
                    pv = zbuffer_vis(uvz, uvz if occ == "samples" else occ_v, c, cell, nb, tol * size)
                    ag[k].add(pv, vis_ref[t, v], inimg[t, v])
            tm["zbuffer_ms"].append((time.time() - t3) * 1000 / len(zb_cfgs))
            for v, c in enumerate(cams):
                a = cv2.imread(f"{P}/{ex}/frames/{gt['frames'][t][c['name']]['img']}", cv2.IMREAD_UNCHANGED)[..., 3]
                alpha_iou.append(iou(a > 127, sil_full[t, v]))
                alpha_bd.append(boundary_dist(a > 127, sil_full[t, v]))
                blur_band.append(float(((a > 12) & (a < 243)).sum() / max((a > 127).sum(), 1)))
                sharp_vs_soft.append(iou(a > 12, sil_full[t, v]))
            if t == 0 and proj == "m11101":
                for v, c in enumerate(cams):
                    _, _, th = silhouettes(scene, rays_full[v][0], tri_prop)
                    ans = scene.cast_rays(__import__("open3d").core.Tensor(rays_full[v][0]))
                    n = ans["primitive_normals"].numpy()
                    nc = n @ np.array(c["R"]).T  # world -> camera
                    nc[np.isfinite(th) & (nc[..., 2] > 0)] *= -1  # face the camera
                    img = np.where(np.isfinite(th)[..., None], (nc * [0.5, -0.5, -0.5] + 0.5) * 255, 255)
                    cv2.imwrite(f"{E}/out/o3d_normal_{clip}_{c['name']}_f0.png", img[..., ::-1].astype(np.uint8))
        ai = np.array(alpha_iou)
        tri_mesh = np.searchsorted(np.cumsum(d["mesh_vcount"]), F[:, 0], side="right")
        s_mesh = tri_mesh[s_tri]
        per_mesh = {}
        for mi, name in enumerate(d["mesh_names"]):
            sel = s_mesh == mi
            m = inimg[:, :, sel]
            per_mesh[str(name)] = {"samples": int(sel.sum()), "prop": bool(tri_prop[tri_mesh == mi].mean() > 0.5),
                                   "ref_visible": float(vis_ref[:, :, sel][m].mean()) if m.any() else None}
        res = {
            "per_mesh": per_mesh,
            "frames": T, "verts": int(nV), "tris": int(len(F)), "prop_tri_fraction": float(tri_prop.mean()),
            "visibility_vs_blender": {k: a.result() for k, a in ag.items()},
            "ref_visible_fraction": float(vis_ref[inimg].mean()),
            "sharp_vs_alpha": {"iou_mean": float(ai.mean()), "iou_min": float(ai.min()),
                               "iou_p5": float(np.percentile(ai, 5)), "frac_iou_below_0.95": float((ai < 0.95).mean()),
                               "boundary_px_mean": float(np.nanmean(alpha_bd)),
                               "soft_band_over_area_mean": float(np.mean(blur_band)),
                               "soft_band_over_area_max": float(np.max(blur_band)),
                               "iou_vs_alpha_any_coverage_mean": float(np.mean(sharp_vs_soft))},
            "depth_check_median_err_of_size": float(np.median(depth_err)),
            "body_vs_full_area": float(sil_body.sum() / max(sil_full.sum(), 1)),
            "timing_ms_per_frame": {k: float(np.median(v)) for k, v in tm.items()},
        }
        summary[clip] = res
        np.savez_compressed(f"{E}/data/{clip}_o3dsil.npz", full=np.packbits(sil_full, -1), body=np.packbits(sil_body, -1),
                            shape=np.array(sil_full.shape))
        if proj not in rest_done:
            scene = o3d_scene(d["V_rest"], F)
            rf = np.zeros((2, H, W), bool)
            rb = np.zeros((2, H, W), bool)
            for v in range(2):
                rf[v], rb[v], _ = silhouettes(scene, rays_full[v][0], tri_prop)
            np.savez_compressed(f"{E}/data/{proj}_rest_sil.npz", full=rf, body=rb)
            rest_done.add(proj)
        json.dump(res, open(f"{E}/out/open3d_{clip}.json", "w"), indent=1)
        vb = res["visibility_vs_blender"]
        print(f"== {clip}: verts {nV} tris {len(F)}; ref visible {res['ref_visible_fraction']:.3f}")
        for k in ["open3d"] + sorted(zb_cfgs):
            r = vb[k]
            print(f"   {k:40s} acc {r['accuracy']:.4f}  false-visible {r['false_visible_of_hidden']:.3f}  "
                  f"false-hidden {r['false_hidden_of_visible']:.3f}")
        s = res["sharp_vs_alpha"]
        print(f"   sharp vs alpha>0.5: IoU mean {s['iou_mean']:.4f} min {s['iou_min']:.4f} p5 {s['iou_p5']:.4f} "
              f"boundary {s['boundary_px_mean']:.2f}px; soft band/area mean {s['soft_band_over_area_mean']:.3f} "
              f"max {s['soft_band_over_area_max']:.3f}; depth check {res['depth_check_median_err_of_size']:.2e}")
        print(f"   timing ms/frame {res['timing_ms_per_frame']}")
        print(f"   per mesh {per_mesh}", flush=True)
    json.dump(summary, open(f"{E}/out/open3d_summary.json", "w"), indent=1)
    print("OPEN3D DONE")


if __name__ == "__main__":
    main()
