"""Silhouette terms for the solvers (opt-in; both solvers leave them off by default).

The creature's silhouette in every view and frame (a mask, e.g. from SAM 2, in the solver's image coordinates =
studio panel pixels; for a real clip already moved by the frame-0 calibration) constrains the skinned model:

 inside   : every surface point, seen or hidden, must project inside the silhouette.  Bilinear lookup of the
            distance to the silhouette (zero inside), so a limb poking out is pulled back in.
 coverage : fixed pixels on the silhouette's outline (and a few inside) must have a projected surface point near
            them, so a limb the picture shows is not left out of the model.

Both use the keypoint image term's robust Geman-McClure form and scale (distances in body-size pixels), so a
strength of 1 weighs a point off the silhouette by d like a keypoint off by d.  Everything that depends on a pose -
which points are fast (motion blur makes the outline unreliable there) and which outline pixels belong to the body
(not to a prop the model does not have) - is decided ONCE from a reference pose, so the objective is a fixed
function of the parameters (safe for L-BFGS line searches).  Prop triangles are not in the surface (load_surface).
"""
import numpy as np
import torch
import torch.nn.functional as F_


def load_masks(path):
    """masks npz -> (T,V,H,W) bool.  Stored bit-packed along the last axis with its shape."""
    z = np.load(path)
    shp = tuple(int(s) for s in z["shape"])
    return np.unpackbits(z["mask"], axis=-1)[..., :shp[-1]].astype(bool).reshape(shp)


def save_masks(path, mask):
    mask = np.asarray(mask, bool)
    np.savez_compressed(path, mask=np.packbits(mask, axis=-1), shape=np.array(mask.shape))


def area_vertex_subset(V, F, n, seed=0):
    """About n vertices spread evenly over the surface: drawn without replacement with probability proportional
    to the vertex's share of the adjacent triangle area (vertices are dense on faces and sparse on bodies)."""
    V = np.asarray(V, np.float64)
    a = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    va = np.zeros(len(V))
    for k in range(3):
        np.add.at(va, F[:, k], a / 3)
    used = np.flatnonzero(va > 0)
    n = min(n, len(used))
    rng = np.random.RandomState(seed)
    return np.sort(rng.choice(used, n, replace=False, p=va[used] / va[used].sum()))


def robust(d, c=0.03):
    """Same Geman-McClure shape and scale as the keypoint image term (d in body-size units)."""
    d2 = d * d
    return d2 / (d2 + c * c) * c * c * 1000


class DenseTerms:
    """Dense surface observations (upper-bound experiments; a future reader output):

    correspondence : for chosen surface vertices, their observed image position in a view and frame (where the
                     vertex is seen), like extra keypoints on the skin.  uv (T,V,N,2), w (T,V,N) >= 0.
    normal         : observed unit camera-space normal at those vertices, (T,V,N,3); penalty 1 - cos.
    depth          : observed camera depth at those vertices up to an unknown per-frame scale and offset
                     (what a monocular depth estimate gives); the scale / offset are solved in closed form.

    vid: (N,) indices into the surface vertices.  Weights are fixed (no pose-dependent selection)."""

    def __init__(self, sk, cams, surf, vid, w, scale_px, uv=None, normal=None, depth=None):
        dev, dt = sk.device, sk.dtype
        t = lambda a: None if a is None else torch.tensor(np.asarray(a), dtype=dt, device=dev)
        it = torch.tensor(np.asarray(vid), dtype=torch.long, device=dev)
        self.sub = {"V": surf["V"][it], "N": surf["N"][it], "w_idx": surf["w_idx"][it], "w_val": surf["w_val"][it]}
        self.sk, self.cams, self.scale_px = sk, cams, float(scale_px)
        self.w, self.uv, self.n, self.z = t(w), t(uv), t(normal), t(depth)

    def losses(self, world):
        P, N = self.sk.skin_points(world, self.sub, normals=True)  # T,N,3
        out = {}
        wsum = self.w.sum().clamp(min=1e-6)
        if self.uv is not None:
            uv, _ = self.cams.project_t(P)
            d = (uv - self.uv).norm(dim=-1) / self.scale_px
            out["dense_uv"] = (self.w * robust(d)).sum() / wsum
        if self.n is not None:
            Nc = torch.einsum("vij,tnj->tvni", self.cams.Rt, N)
            out["dense_normal"] = (self.w * (1 - (Nc * self.n).sum(-1))).sum() / wsum
        if self.z is not None:
            Xc = torch.einsum("vij,tnj->tvni", self.cams.Rt, P) + self.cams.tt[None, :, None, :]
            z = Xc[..., 2]
            # per frame and view: best a * observed + b for the predicted depth (weighted least squares)
            w = self.w
            sw = w.sum(-1, keepdim=True).clamp(min=1e-6)
            mo, mp = (w * self.z).sum(-1, keepdim=True) / sw, (w * z).sum(-1, keepdim=True) / sw
            co, cp = self.z - mo, z - mp
            a = (w * co * cp).sum(-1, keepdim=True) / (w * co * co).sum(-1, keepdim=True).clamp(min=1e-9)
            r = (cp - a * co) / self.sk.size
            out["dense_depth"] = (w * robust(r, 0.02)).sum() / wsum
        return out


class SilhouetteTerms:
    def __init__(self, sk, cams, surf, masks, scale_px, ref_world, n_points=1500, n_boundary=96, n_interior=32,
                 speed_frac=0.04, near_frac=0.08, downsample=2, seed=0):
        """sk Skeleton, cams Cams (same device), surf load_surface() dict, masks (T,V,H,W) bool,
        scale_px the solver's body size in pixels, ref_world (T,B,4,4) reference pose (the warm start).
        speed_frac: a point moving more than speed_frac * body px per frame (at the reference pose) gets less
        weight, nothing above twice that.  near_frac: outline pixels farther than this from the reference model are
        not the body (a prop, a background object) and are ignored by the coverage term."""
        import cv2
        dev, dt = sk.device, sk.dtype
        masks = np.asarray(masks, bool)
        T, V, H, W = masks.shape
        if T != ref_world.shape[0] or V != cams.V:
            raise ValueError("masks do not match the clip (frames / views)")
        self.sk, self.cams, self.scale_px = sk, cams, float(scale_px)
        self.H, self.W = H, W
        idx = area_vertex_subset(surf["V"].cpu().numpy(), surf["F"], n_points, seed)
        it = torch.tensor(idx, dtype=torch.long, device=dev)
        self.sub = {"V": surf["V"][it], "w_idx": surf["w_idx"][it], "w_val": surf["w_val"][it]}
        with torch.no_grad():
            uv_ref = self._project(ref_world)  # T,V,N,2
            # image motion around frame t: the larger of the steps from t-1 and to t+1 (a central difference
            # would read zero at a turnaround, where the exposure still smears)
            sp = torch.zeros(uv_ref.shape[:-1], dtype=dt, device=dev)
            if T > 1:
                step = (uv_ref[1:] - uv_ref[:-1]).norm(dim=-1)
                sp[:-1] = step
                sp[1:] = torch.maximum(sp[1:], step)
            thr = speed_frac * self.scale_px
            self.w_pt = (2 - sp / thr).clamp(0, 1)  # T,V,N
        # distance (body-size units) to the silhouette, zero inside, at 1/downsample resolution
        h, w = H // downsample, W // downsample
        dist = np.zeros((T, V, h, w), np.float32)
        rng = np.random.RandomState(seed + 1)
        Kc = n_boundary + n_interior
        pix = np.zeros((T, V, Kc, 2), np.float32)
        pw = np.zeros((T, V, Kc), np.float32)
        k3 = np.ones((3, 3), np.uint8)
        uv_np, wpt_np = uv_ref.cpu().numpy(), self.w_pt.cpu().numpy()
        self.empty = 0
        for t in range(T):
            for v in range(V):
                m = masks[t, v]
                if not m.any():
                    self.empty += 1
                    continue
                md = cv2.resize(m.astype(np.float32), (w, h), interpolation=cv2.INTER_AREA) >= 0.5
                dist[t, v] = cv2.distanceTransform((~md).astype(np.uint8), cv2.DIST_L2, 3) * downsample / self.scale_px
                mu = m.astype(np.uint8)
                edge = np.argwhere(mu - cv2.erode(mu, k3) > 0)[:, ::-1] + 0.5  # (x, y) pixel centres
                inner = np.argwhere(cv2.erode(mu, k3, iterations=3) > 0)[:, ::-1] + 0.5
                sel = []
                for src, n in ((edge, n_boundary), (inner, n_interior)):
                    if len(src):
                        sel.append(src[rng.choice(len(src), n, replace=len(src) < n)])
                    else:
                        sel.append(np.zeros((n, 2)))
                p = np.concatenate(sel).astype(np.float32)
                d = np.linalg.norm(p[:, None] - uv_np[t, v][None], axis=-1)  # Kc,N
                near = d.argmin(1)
                ok = d.min(1) <= near_frac * self.scale_px
                pix[t, v] = p
                pw[t, v] = ok * wpt_np[t, v][near]
                if not len(edge):
                    pw[t, v, :n_boundary] = 0
                if not len(inner):
                    pw[t, v, n_boundary:] = 0
        has = torch.tensor(masks.reshape(T, V, -1).any(-1), device=dev)
        self.w_pt = self.w_pt * has[..., None]
        self.dist = torch.tensor(dist, dtype=dt, device=dev).reshape(T * V, 1, h, w)
        self.pix = torch.tensor(pix, dtype=dt, device=dev)
        self.pw = torch.tensor(pw, dtype=dt, device=dev)
        self.coverage_kept = float((pw > 0).mean())

    def _project(self, world):
        P = self.sk.skin_points(world, self.sub)  # T,N,3
        uv, _ = self.cams.project_t(P)  # T,V,N,2
        return uv

    def losses(self, world):
        """-> (inside, coverage) scalars for the pose `world` (T,B,4,4)."""
        uv = self._project(world)
        T, V, N, _ = uv.shape
        g = torch.stack([uv[..., 0] / self.W * 2 - 1, uv[..., 1] / self.H * 2 - 1], -1).reshape(T * V, 1, N, 2)
        d = F_.grid_sample(self.dist, g, mode="bilinear", padding_mode="border", align_corners=False)
        d = d.reshape(T, V, N)
        inside = (self.w_pt * robust(d)).sum() / self.w_pt.sum().clamp(min=1e-6)
        dd = torch.cdist(self.pix.reshape(T * V, -1, 2), uv.reshape(T * V, N, 2)).min(-1).values
        dd = dd.reshape(T, V, -1) / self.scale_px
        coverage = (self.pw * robust(dd)).sum() / self.pw.sum().clamp(min=1e-6)
        return inside, coverage
