"""From 2D joints in two views to skeleton rotations.

1. fix left/right (near/far) label swaps per view using the other camera
2. triangulate ("the two lines cross") -> per-frame 3D joints + consistency score
3. fit the skeleton: global transform + local joint rotations, every joint kept
   inside its limits, reprojection + 3D + depth-order + smoothness terms.
"""
import math
import numpy as np
import torch

from v2r.geom import Cams, Skeleton, triangulate, kabsch, rot6d_to_mat, mat_to_rot6d, global_from_params


# ----------------------------------------------------------------------------- triangulation + checks
def reproj_residual(cams, X, uv):
    """X (...,3), uv (...,V,2) -> residual px (...,V)"""
    p = cams.project_np(X)  # (V,...,3)
    p = np.moveaxis(p, 0, -2)[..., :2]
    return np.linalg.norm(p - uv, axis=-1)


def limb_chains(rig):
    """Group mirror pairs into limbs: connected components (via drawing edges) of left-side
    keypoints that have a mirror.  Returns list of (left_idx[], right_idx[])."""
    kps = rig["keypoints"]
    left = [i for i, k in enumerate(kps) if k["side"] == "L" and k["mirror"] >= 0]
    par = {i: i for i in left}

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    for a, b in rig["edges"]:
        if a in par and b in par:
            par[find(a)] = find(b)
    groups = {}
    for i in left:
        groups.setdefault(find(i), []).append(i)
    return [(g, [kps[i]["mirror"] for i in g]) for g in groups.values()]


def _chain_cost(cams, rig, uv_t, idx, size_px):
    """uv_t (T,V,K,2) restricted to idx -> per-frame cost: ray miss + bone length error (px)."""
    u = uv_t[:, :, idx].transpose(0, 2, 1, 3)  # (T,n,V,2)
    X = triangulate(cams.P, u, np.ones(u.shape[:3]))
    rep = reproj_residual(cams, X, u).sum((-1, -2))
    return rep, X


def fix_swaps(cams, rig, uv, conf, switch_px=None, return_permutation=False):
    """Near/far (left/right) label swaps: for every limb, in every frame and view, test
    'labels as read' vs 'left and right limb exchanged in that view'.  The right answer is
    the one where the two cameras' rays cross AND the limb keeps its real bone lengths.
    A Viterbi pass adds a penalty for switching state between frames."""
    uv, conf = uv.copy(), conf.copy()
    T, V, K, _ = uv.shape
    permutation = np.broadcast_to(np.arange(K), (T,V,K)).copy()
    size_px = float(np.median(np.ptp(uv.reshape(-1, K, 2), axis=1).max(-1)))
    rest = np.array([k["rest_world"] for k in rig["keypoints"]])
    px_per_unit = size_px / rig["size"]
    edges = [tuple(e) for e in rig["edges"]]
    switch_px = switch_px if switch_px is not None else 0.02 * size_px
    n_swaps = 0
    for L, R in limb_chains(rig):
        both = L + R
        # edges touching this limb pair (incl. the attachment to the body)
        es = [(a, b) for a, b in edges if a in both or b in both]
        L0 = np.array([np.linalg.norm(rest[a] - rest[b]) for a, b in es])
        # state = bitmask of views whose L/R limb labels are exchanged
        S = 1 << V
        pts = sorted(set([p for e in es for p in e]))
        pos = {p: n for n, p in enumerate(pts)}
        LR = L + R
        cost = np.zeros((T, S))
        for s in range(S):
            u = uv.copy()
            for v in range(V):
                if s >> v & 1:
                    u[:, v, L], u[:, v, R] = uv[:, v, R], uv[:, v, L]
            uu = u[:, :, pts].transpose(0, 2, 1, 3)
            X = triangulate(cams.P, uu, np.ones(uu.shape[:3]))
            rep = reproj_residual(cams, X, uu).sum((-1, -2))
            Ls = np.stack([np.linalg.norm(X[:, pos[a]] - X[:, pos[b]], axis=-1) for a, b in es], 1)
            len_err = (np.abs(Ls - L0[None]) * px_per_unit).sum(1)
            # 2D continuity per view: limb labels should not jump between neighbouring frames
            # (compared with the raw reading of the neighbours, which is right most of the time)
            cont = np.zeros(T)
            for dt in (-2, -1, 1, 2):
                nb = np.clip(np.arange(T) + dt, 0, T - 1)
                d = np.linalg.norm(u[:, :, LR] - uv[nb][:, :, LR], axis=-1)  # (T,V,n)
                cont += np.minimum(d, 0.15 * size_px).sum((1, 2)) / abs(dt)
            prior = bin(s).count("1") * 0.01 * size_px  # the reader is usually right
            cost[:, s] = rep + len_err + 0.25 * cont + prior
        # Viterbi over time, switching labels costs a little
        acc = cost[0].copy()
        back = np.zeros((T, S), int)
        nbits = np.array([[bin(a ^ b).count("1") for b in range(S)] for a in range(S)])
        for t in range(1, T):
            trans = acc[:, None] + switch_px * nbits
            back[t] = trans.argmin(0)
            acc = trans.min(0) + cost[t]
        path = np.zeros(T, int)
        path[-1] = acc.argmin()
        for t in range(T - 1, 0, -1):
            path[t - 1] = back[t, path[t]]
        for t in np.nonzero(path > 0)[0]:
            for v in range(V):
                if path[t] >> v & 1:
                    a, b = uv[t, v, L].copy(), uv[t, v, R].copy()
                    uv[t, v, L], uv[t, v, R] = b, a
                    ca, cb = conf[t, v, L].copy(), conf[t, v, R].copy()
                    conf[t, v, L], conf[t, v, R] = cb, ca
                    pa, pb = permutation[t,v,L].copy(), permutation[t,v,R].copy()
                    permutation[t,v,L], permutation[t,v,R] = pb, pa
                    n_swaps += 1
    if return_permutation:
        return uv, conf, n_swaps, permutation
    return uv, conf, n_swaps


def triangulate_all(cams, uv, conf):
    T, V, K, _ = uv.shape
    X = triangulate(cams.P, uv.transpose(0, 2, 1, 3), conf.transpose(0, 2, 1))  # (T,K,3)
    res = reproj_residual(cams, X, uv.transpose(0, 2, 1, 3))  # (T,K,V)
    return X, res


def consistency_report(cams, rig, X, res, conf, uv):
    """Do both pictures show the same move?  Uses the residual of the 'two lines crossing'
    relative to the creature's size on screen, and the bone-length stability of the
    triangulated skeleton (a different move in the two halves breaks both)."""
    T = X.shape[0]
    size_px = np.median([np.ptp(uv[t, v], axis=0).max() for t in range(T) for v in range(uv.shape[1])])
    good = conf.min(1) > 0.3  # (T,K)
    rel = np.where(good, res.max(-1), np.nan) / max(size_px, 1)
    frame_rel = np.nanmedian(rel, 1)
    # bone length stability between keypoints connected in the rig drawing
    edges = np.array(rig["edges"]) if rig["edges"] else np.zeros((0, 2), int)
    L = np.linalg.norm(X[:, edges[:, 0]] - X[:, edges[:, 1]], axis=-1)
    rest = np.array([k["rest_world"] for k in rig["keypoints"]])
    L0 = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=-1)
    len_err = np.median(np.abs(L - L0[None]) / (L0[None] + 0.02 * rig["size"]), 1)
    bad = (frame_rel > 0.03) | (len_err > 0.35)
    return {
        "size_px": float(size_px),
        "frame_residual_rel": frame_rel.tolist(),
        "frame_bonelen_err": len_err.tolist(),
        "median_residual_rel": float(np.nanmedian(frame_rel)),
        "bad_frames": int(bad.sum()),
        "bad_fraction": float(bad.mean()),
        "same_move": bool(bad.mean() < 0.25),
        "bad_mask": bad.tolist(),
    }


def clean_tracks(uv, conf, res=None, size_px=None, win=3, jump=0.08, miss=0.05):
    """Down-weight 2D detections that (a) jump away from their temporal median or
    (b) whose two camera rays do not cross (large triangulation residual)."""
    T, V, K, _ = uv.shape
    conf = conf.copy()
    size_px = size_px or float(np.median(np.ptp(uv.reshape(-1, K, 2), axis=1).max(-1)))
    pad = np.pad(uv, ((win, win), (0, 0), (0, 0), (0, 0)), mode="edge")
    med = np.median(np.stack([pad[i:i + T] for i in range(2 * win + 1)]), 0)
    d = np.linalg.norm(uv - med, axis=-1) / size_px
    n_jump = int((d > jump).sum())
    conf = np.where(d > jump, conf * 0.05, conf)
    n_miss = 0
    if res is not None:
        r = res.transpose(0, 2, 1) / size_px  # (T,V,K)
        bad = r > miss
        n_miss = int(bad.sum())
        conf = np.where(bad, conf * 0.2, conf)
    return conf, n_jump, n_miss


# ----------------------------------------------------------------------------- skeleton fit
def init_global(sk, X, w):
    """Per-frame rigid fit of the rest keypoints to the triangulated ones (core joints weigh more)."""
    T = X.shape[0]
    Rg = np.zeros((T, 3, 3))
    tr = np.zeros((T, 3))
    c = sk.center
    for t in range(T):
        R, tt = kabsch(sk.rest_kp, X[t], w[t] + 1e-3)
        Rg[t] = R
        # G = T(c+tr) R T(-c)  ->  x' = R x + (c + tr - R c)
        tr[t] = tt - c + R @ c
    # temporal continuity of rotation (no flips)
    return Rg, tr


def fit(rig, studio, uv, conf, X3, depth=None, iters=1500, device="cuda", w_smooth=1.0, w_depth=0.05,
        verbose=True, fps=None):
    # None preserves the original legacy behaviour. Experimental warm starts supply
    # fps to express acceleration penalties in the same 30Hz-reference units.
    temporal_scale = (float(fps) / 30.) ** 4 if fps is not None else 1.0
    T = uv.shape[0]
    cams = Cams(studio, device=device)
    sk = Skeleton(rig, device=device)
    size = sk.size
    # weights: core (non-limb) joints help the global fit
    core = np.array([1.0 if k["side"] == "M" else 0.3 for k in rig["keypoints"]])
    wk = conf.min(1) * core[None]
    Rg0, tr0 = init_global(sk, X3, wk)
    tdev = lambda a: torch.tensor(np.asarray(a), dtype=torch.float32, device=device)
    r6 = torch.nn.Parameter(tdev(mat_to_rot6d(Rg0)))
    trp = torch.nn.Parameter(tdev(tr0))
    pe = torch.nn.Parameter(torch.zeros(T, sk.A, 3, device=device))
    lim = sk.t_lim  # (A,3,2)
    mid, half = (lim[..., 0] + lim[..., 1]) / 2, (lim[..., 1] - lim[..., 0]) / 2
    # start the soft-limited parameters at zero rotation
    with torch.no_grad():
        z = (-mid / half.clamp(min=1e-6)).clamp(-0.95, 0.95)
        pe.copy_(torch.atanh(z)[None].expand(T, -1, -1))
    t_uv, t_conf = tdev(uv), tdev(conf)
    t_X3 = tdev(X3)
    t_w3 = tdev(conf.min(1))
    size_px = float(np.median(np.ptp(uv.reshape(-1, uv.shape[2], 2), axis=1).max(-1)))
    t_dep = tdev(depth) if depth is not None else None
    c = sk.t_center

    def forward():
        Rg = rot6d_to_mat(r6)
        G = global_from_params(Rg, trp, c)
        eul = mid + half * torch.tanh(pe)
        world, kp = sk.fk(G, eul)
        return G, eul, world, kp

    def losses(kp, eul, stage):
        puv, pz = cams.project_t(kp)  # (T,V,K,2), (T,V,K)
        e = (puv - t_uv).norm(dim=-1) / size_px
        l_rep = (t_conf * (e ** 2 / (e ** 2 + 0.03 ** 2)) * 0.03 ** 2 * 1e3).mean()  # Geman-McClure
        l_3d = (t_w3 * ((kp - t_X3).norm(dim=-1) / size).clamp(max=0.2) ** 2).mean() * 10
        l = l_rep + (l_3d if stage < 2 else 0.2 * l_3d)
        out = {"rep": l_rep.item(), "3d": l_3d.item()}
        if T > 2:
            acc = kp[2:] - 2 * kp[1:-1] + kp[:-2]
            l_s = (acc.norm(dim=-1) / size).pow(2).mean() * 50 * w_smooth
            ea = eul[2:] - 2 * eul[1:-1] + eul[:-2]
            l_s = l_s + ea.pow(2).mean() * 0.5 * w_smooth
            l_s = l_s * temporal_scale
            l = l + l_s
            out["smooth"] = l_s.item()
        if t_dep is not None and w_depth > 0:
            dz = (pz - pz.mean(-1, keepdim=True)) / size
            l_d = (t_conf * (dz - t_dep).pow(2)).mean() * w_depth
            l = l + l_d
            out["depth"] = l_d.item()
        l = l + 1e-3 * pe.pow(2).mean()  # mild preference for the middle of the range
        return l, out

    stages = [([r6, trp], 0.02, iters // 5), ([r6, trp, pe], 0.03, iters), ([r6, trp, pe], 0.005, iters // 3)]
    for si, (params, lr, n) in enumerate(stages):
        opt = torch.optim.Adam(params, lr=lr)
        for it in range(n):
            G, eul, world, kp = forward()
            l, info = losses(kp, eul, si)
            opt.zero_grad()
            l.backward()
            opt.step()
            if verbose and (it % 250 == 0 or it == n - 1):
                print(f"[v2r] solve stage {si} it {it} loss {l.item():.5f} " +
                      " ".join(f"{k} {v:.5f}" for k, v in info.items()), flush=True)
    with torch.no_grad():
        G, eul, world, kp = forward()
        puv, _ = cams.project_t(kp)
    G, eul, world, kp, puv = [x.detach().cpu().numpy() for x in (G, eul, world, kp, puv)]
    rep_px = np.linalg.norm(puv - uv, axis=-1)
    return {"G": G, "eul": eul, "world": world, "kp": kp, "proj": puv, "rep_px": rep_px, "skeleton": sk}
