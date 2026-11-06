"""Read dense correspondences from a video with a dense reader (opt-in).

The dense head predicts, for every heat-grid cell, the rest-pose surface point the pixel shows (normalised body-box
coordinates), whether it is the creature and how sure it is (log-scale).  For the solver, a fixed set of surface
vertices (area-uniform, v2r.silhouette.area_vertex_subset) is located in each view and frame: every vertex takes
the creature cell whose predicted coordinate is nearest to its own rest coordinate, if that distance is small and
the cell is confident.  Hidden vertices find no close cell and get zero weight.  Output arrays plug into
solve_temporal.fit_temporal(dense=..., dense_strength={"dense_uv": s}).
"""
import cv2
import numpy as np
import torch


@torch.no_grad()
def read_dense_maps(model, st, panels_per_frame, mats, device="cuda", batch=8):
    """Dense head on the given crops (mats[t][v] panel -> net, e.g. the keypoint pass-2 crops).
    Returns xyz (T,V,h,w,3) float16, fg (T,V,h,w) float16 probability, scale (T,V,h,w) float16, and the
    heat-cell -> panel-pixel affine per (t,v)."""
    from v2r.data import to_tensor
    W, H = st["net_wh"]
    hw, hh = st["heat_wh"]
    T, V = len(panels_per_frame), len(panels_per_frame[0])
    xyz = np.zeros((T, V, hh, hw, 3), np.float16)
    fg = np.zeros((T, V, hh, hw), np.float16)
    sc = np.zeros((T, V, hh, hw), np.float16)
    cell2panel = np.zeros((T, V, 2, 3), np.float32)
    items = [(t, v) for t in range(T) for v in range(V)]
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        ims = []
        for t, v in chunk:
            M = mats[t][v]
            interp = cv2.INTER_LINEAR if M[0, 0] >= 1 else cv2.INTER_AREA
            ims.append(to_tensor(cv2.warpAffine(panels_per_frame[t][v], M, (W, H), flags=interp,
                                                borderMode=cv2.BORDER_REPLICATE)))
        x = torch.stack(ims).to(device)
        view = torch.tensor([v for _, v in chunk], device=device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            _, _, dn = model(x, view, with_dense=True)
        dn = dn.float()
        out_xyz = dn[:, :3].permute(0, 2, 3, 1).cpu().numpy()
        out_fg = torch.sigmoid(dn[:, 3]).cpu().numpy()
        out_sc = torch.exp(dn[:, 4].clamp(-6, 3)).cpu().numpy()
        for j, (t, v) in enumerate(chunk):
            xyz[t, v], fg[t, v], sc[t, v] = out_xyz[j], out_fg[j], out_sc[j]
            # heat cell (cx, cy) centre -> net px ((cx + .5) W/hw, (cy + .5) H/hh) -> panel px (inverse of M)
            Mi = cv2.invertAffineTransform(mats[t][v])
            S = np.float32([[W / hw, 0, 0.5 * W / hw], [0, H / hh, 0.5 * H / hh], [0, 0, 1]])
            cell2panel[t, v] = (np.vstack([Mi, [0, 0, 1]]) @ S)[:2]
    return xyz, fg, sc, cell2panel


def match_vertices(xyz, fg, sc, cell2panel, rest_norm, vid, max_dist=0.08, fg_min=0.5, max_scale=0.15):
    """For each chosen vertex (rest_norm[vid], normalised body-box rest coordinates) and each (t, v): the creature
    cell with the nearest predicted coordinate.  Accepted when the coordinate distance < max_dist (body-box units),
    the cell is foreground and its predicted scale < max_scale.  Sub-cell position: weighted mean of the accepted
    cells within 1.5x the best distance.  Returns uv (T,V,N,2) panel px and w (T,V,N) in [0,1]."""
    T, V, hh, hw, _ = xyz.shape
    P = rest_norm[vid].astype(np.float32)  # N,3
    N = len(P)
    uv = np.zeros((T, V, N, 2), np.float32)
    w = np.zeros((T, V, N), np.float32)
    gy, gx = np.mgrid[0:hh, 0:hw]
    cells = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size)], 0).astype(np.float32)
    for t in range(T):
        for v in range(V):
            ok = (fg[t, v].ravel() >= fg_min) & (sc[t, v].ravel() < max_scale)
            if ok.sum() < 3:
                continue
            X = xyz[t, v].reshape(-1, 3)[ok].astype(np.float32)
            Sc = sc[t, v].ravel()[ok].astype(np.float32)
            pix = (cell2panel[t, v] @ cells[:, ok]).T  # cells -> panel px
            Xt = torch.from_numpy(X)
            d = torch.cdist(torch.from_numpy(P), Xt)  # N, M
            best, arg = d.min(1)
            best = best.numpy()
            near = (d <= torch.from_numpy(best[:, None]) * 1.5 + 1e-3).numpy()
            for n in np.flatnonzero(best < max_dist):
                sel = near[n]
                ww = 1.0 / (Sc[sel] + 1e-3)
                uv[t, v, n] = (pix[sel] * ww[:, None]).sum(0) / ww.sum()
                w[t, v, n] = float(np.clip(1 - best[n] / max_dist, 0, 1))
    return uv, w


def rest_normalised(surf_V, dense_meta):
    c, h = np.array(dense_meta["center"]), np.array(dense_meta["half"])
    return (np.asarray(surf_V) - c) / h
