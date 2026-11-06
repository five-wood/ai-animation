"""Experimental joint temporal solver; legacy solver remains unchanged.

Observational weights are heuristic reliability scores, never calibrated probabilities.
No contacts, per-creature bone-name rules or clip-specific frame intervals are used.

Root and local SO(3) increments are regularised together, along with world keypoint
acceleration. Relative rotation matrix differences are a smooth chordal surrogate for
changes in angular velocity (no Euler differencing, acos singularity or quaternion
sign ambiguity). All accelerations scale with fps**2. The loss is averaged over time,
so fixed-duration signals have approximately frame-rate independent regularisation.
Constants are expressed relative to 30Hz for readable, dimensionless coefficients.
"""
import numpy as np
import torch
from v2r.geom import (Skeleton, Cams, rot6d_to_mat, mat_to_rot6d,
                      global_from_params, euler_xyz_t)
from v2r.solve import fit as legacy_fit


def robust_square(x2, scale):
    """Squared magnitude x2 -> Charbonnier-type penalty: x2 for small values, ~2*scale*|x| for
    large ones.  scale <= 0 keeps the plain square (legacy behaviour)."""
    if scale <= 0:
        return x2
    s2 = scale * scale
    return 2 * s2 * (torch.sqrt(1 + x2 / s2) - 1)


def rotation_acceleration_loss(R, fps, robust=0.):
    """R (T,...,3,3). Chordal acceleration of body-frame relative rotations.

    Constant world angular velocity also gives constant body relative increments.
    Dividing a matrix difference by sqrt(2) matches small-angle vector magnitudes.
    robust > 0: large accelerations (abrupt stops) are penalised linearly, not quadratically.
    """
    if len(R) < 3:
        return R.sum() * 0
    delta = R[:-1].transpose(-1, -2) @ R[1:]
    accel = (delta[1:] - delta[:-1]) * (float(fps) / 30.0) ** 2
    return robust_square(accel.square().sum((-1, -2)) * .5, robust).mean()


def fit_temporal(rig, studio, uv, weights, X3, fps, depth=None, iters=850,
                 device="cuda", warm_iters=900, verbose=True, initial=None,
                 position_strength=400., local_strength=24., root_strength=40.,
                 stationary_starts=None, stationary_segment_weights=None, stationary_anchor_strength=0.,
                 depth_mode="numeric", depth_strength=.025, order_config=None,
                 robust_pos=0., robust_rot=0., front_vertical=1.0,
                 surface=None, masks=None, silhouette_strength=0., coverage_strength=0., silhouette_opts=None,
                 dense=None, dense_strength=None):
    """Warm fit followed by coupled root/local temporal refinement, no post-filter.

    `initial` is optional for controlled ablations. When omitted, a bounded legacy fit
    provides a common pose basin. No source file is mutated. Limits remain enforced.

    robust_pos / robust_rot (0 = off, the original quadratic penalties): Charbonnier scale of
    the world keypoint acceleration (body-size units, 30Hz reference) and of the local rotation
    acceleration.  Accelerations well below the scale are still penalised quadratically (jitter);
    larger ones only linearly, so abrupt stops are not smeared over several frames.
    Measured 2026-11-04 (work/analyze/research): 0.003 / 0.02 kept jitter at the temporal level
    and restored impacts to about the legacy level on m11101 and lizard (real clip + smooth exam
    + strike exam).

    silhouette_strength / coverage_strength (0 = off): v2r.silhouette terms in the refinement, needing
    surface (geom.load_surface) and masks (T,V,H,W) in the solver's image coordinates.  The warm start is
    their fixed reference pose (point speeds, which outline pixels are body).  silhouette_opts: extra
    SilhouetteTerms arguments.

    dense / dense_strength (None = off): dense surface observations for upper-bound experiments, a dict of
    v2r.silhouette.DenseTerms arguments (vid, w, uv / normal / depth) with surface, and strengths per term
    {"dense_uv": .., "dense_normal": .., "dense_depth": ..}.
    """
    if depth_mode not in ("off","numeric","order"):
        raise ValueError("Unknown depth mode")
    if not np.isfinite(depth_strength) or depth_strength<0:
        raise ValueError("depth_strength must be nonnegative finite")
    if not np.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be finite and positive")
    uv = np.asarray(uv, float)
    weights = np.asarray(weights, float)
    X3 = np.asarray(X3, float)
    if uv.ndim != 4 or uv.shape[-1] != 2 or weights.shape != uv.shape[:-1]:
        raise ValueError("Expected uv T,V,K,2 and weights T,V,K")
    if not all(np.isfinite(x).all() for x in (uv, weights, X3)):
        raise ValueError("Non-finite observation input")
    if (weights < 0).any() or weights.sum() <= 0:
        raise ValueError("Nonnegative, nonempty observation weights required")
    if stationary_starts is not None:
        ss=np.asarray(stationary_starts)
        sw=np.asarray(stationary_segment_weights)
        if ss.shape != weights.shape or sw.shape != weights.shape:
            raise ValueError("Stationary segment arrays must match T,V,K")
        if not np.isfinite(ss).all() or not np.isfinite(sw).all() or (sw<0).any():
            raise ValueError("Invalid stationary evidence")
        if (ss>=len(uv)).any() or (ss < -1).any() or not np.all(ss==np.floor(ss)):
            raise ValueError("Invalid stationary segment start indices")
        if np.any((ss<0)&(sw>0)):
            raise ValueError("Unsupported stationary entries must have zero weight")
    if initial is None:
        initial = legacy_fit(rig, studio, uv, weights, X3, depth=depth if depth_mode=="numeric" else None,
                             iters=warm_iters, device=device, verbose=verbose, fps=fps, front_vertical=front_vertical)
    sk = Skeleton(rig, device=device)
    cams = Cams(studio, device=device)
    td = lambda x: torch.tensor(np.asarray(x), dtype=torch.float32, device=device)
    G0, E0 = np.asarray(initial["G"]), np.asarray(initial["eul"])
    T = len(uv)
    if len(G0) != T or E0.shape != (T, sk.A, 3):
        raise ValueError("Warm-start pose does not match the rig or clip")
    R0 = G0[:, :3, :3]
    tr0 = G0[:, :3, 3] - sk.center + np.einsum("tij,j->ti", R0, sk.center)
    r6 = torch.nn.Parameter(td(mat_to_rot6d(R0)))
    tr = torch.nn.Parameter(td(tr0))
    lo, hi = sk.t_lim[..., 0], sk.t_lim[..., 1]
    mid, half = (hi + lo) / 2, (hi - lo) / 2
    pe = torch.nn.Parameter(torch.atanh(((td(E0) - mid) / half.clamp(min=1e-6)).clamp(-.9999, .9999)))
    t_uv, t_w = td(uv), td(weights)
    # Absolute weights preserve low confidence as weak evidence; don't normalise each view
    # to unit weight, which would make a completely occluded view as strong as the visible one.
    norm = max(float(np.mean(weights)), .05)
    w3 = td(np.min(weights, axis=1))
    anchor_idx = torch.tensor(np.maximum(stationary_starts,0),dtype=torch.long,device=device) if stationary_starts is not None else None
    anchor_w = td(stationary_segment_weights) if stationary_segment_weights is not None else None
    t_X = td(X3)
    dep = td(depth) if depth is not None else None
    order_data=None
    if depth_mode=="order" and depth is not None:
        from v2r.depth_order import order_targets, ordering_loss
        order_data=order_targets(rig,depth,weights,fps,**(order_config or {}))
        order_t={k:(torch.tensor(v,dtype=torch.long,device=device) if k=="pairs" else td(v))
                 for k,v in order_data.items() if k in ("pairs","sign","margin","gate")}
    scale_px = float(np.median(np.ptp(uv.reshape(-1, uv.shape[2], 2), axis=1).max(-1)))
    scale_px = max(scale_px, 1.)
    dt_scale2 = (fps / 30.) ** 2
    from v2r.solve import view_axis_weights
    axis_w = view_axis_weights(studio, front_vertical, device)
    sil = None
    if silhouette_strength > 0 or coverage_strength > 0:
        if surface is None or masks is None:
            raise ValueError("silhouette terms need surface and masks")
        from v2r.silhouette import SilhouetteTerms
        with torch.no_grad():
            ref_world, _ = sk.fk(td(G0), td(E0))
        sil = SilhouetteTerms(sk, cams, surface, masks, scale_px, ref_world, **(silhouette_opts or {}))
    dense_t = None
    if dense is not None and dense_strength and any(v > 0 for v in dense_strength.values()):
        from v2r.silhouette import DenseTerms
        dense_t = DenseTerms(sk, cams, surface, scale_px=scale_px, **dense)

    def forward():
        R = rot6d_to_mat(r6)
        G = global_from_params(R, tr, sk.t_center)
        E = mid + half * torch.tanh(pe)
        world, kp = sk.fk(G, E)
        return R, G, E, world, kp

    def objective():
        R, G, E, world, kp = forward()
        pred, z = cams.project_t(kp)
        e2 = (((pred - t_uv) / scale_px).square() * axis_w[None, :, None, :]).sum(-1)
        # Same robust image term as legacy for an interpretable comparison.
        image = (t_w * (e2 / (e2 + .03 ** 2)) * .03 ** 2 * 1000).mean() / norm
        pos3 = (w3 * ((kp-t_X).square().sum(-1) / sk.size ** 2).clamp(max=.04)).mean() * .5 / norm
        total = image + pos3
        info = {"image": image, "tri3d": pos3}
        if T > 2:
            a = (kp[2:] - 2*kp[1:-1] + kp[:-2]) / sk.size * dt_scale2
            world_acc = robust_square(a.square().sum(-1), robust_pos).mean() * position_strength
            local_acc = rotation_acceleration_loss(euler_xyz_t(E), fps, robust_rot) * local_strength
            root_acc = rotation_acceleration_loss(R, fps) * root_strength
            centre_acc = (tr[2:] - 2*tr[1:-1] + tr[:-2]) / sk.size * dt_scale2
            centre_acc = centre_acc.square().sum(-1).mean() * position_strength
            total = total + world_acc + local_acc + root_acc + centre_acc
            info.update(world_acc=world_acc, local_acc=local_acc, root_acc=root_acc, centre_acc=centre_acc)
        if anchor_idx is not None and anchor_w is not None and stationary_anchor_strength > 0:
            # Equality to a shared *variable* projection at segment start; no manual pixel
            # anchor and no fixed world position or ground/contact assumption.
            vi=torch.arange(pred.shape[1],device=device)[None,:,None]
            ki=torch.arange(pred.shape[2],device=device)[None,None,:]
            anchor_pred=pred[anchor_idx,vi,ki]
            anchor_loss=(anchor_w*((pred-anchor_pred)/scale_px).square().sum(-1)).mean()*stationary_anchor_strength
            total=total+anchor_loss
            info["image_stationary_segment"]=anchor_loss
        if dep is not None and depth_mode=="numeric":
            dz = (z-z.mean(-1,keepdim=True))/sk.size
            depth_loss = (t_w*(dz-dep).square()).mean() * depth_strength / norm
            total = total + depth_loss
            info["depth"] = depth_loss
        if order_data is not None:
            order_loss=ordering_loss(z,order_t,sk.size)*depth_strength/norm
            total=total+order_loss
            info["depth_order"]=order_loss
        if sil is not None:
            inside, coverage = sil.losses(world)
            if silhouette_strength > 0:
                total = total + inside * silhouette_strength
                info["silhouette"] = inside * silhouette_strength
            if coverage_strength > 0:
                total = total + coverage * coverage_strength
                info["coverage"] = coverage * coverage_strength
        if dense_t is not None:
            for k, v in dense_t.losses(world).items():
                s = float(dense_strength.get(k, 0.))
                if s > 0:
                    total = total + v * s
                    info[k] = v * s
        # Weak warm-pose trust for underdetermined twists; do not freeze real visible motion.
        trust = (E-td(E0)).square().mean() * .0003
        total = total + trust
        return total, info

    opt = torch.optim.Adam([r6, tr, pe], lr=.006)
    for i in range(iters):
        rate = .006 * (.15 + .85 * (1 - i/max(iters,1)))
        for group in opt.param_groups: group["lr"] = rate
        opt.zero_grad()
        loss, info = objective()
        loss.backward()
        torch.nn.utils.clip_grad_norm_([r6,tr,pe],1.)
        opt.step()
        if verbose and (i % 250 == 0 or i == iters-1):
            print(f"[v2r] temporal it {i} loss {loss.item():.5f} " +
                  " ".join(f"{k}={v.item():.5f}" for k,v in info.items()), flush=True)
    # Joint line-search polish removes residual oscillations of the optimiser itself.
    polish = torch.optim.LBFGS([r6,tr,pe], lr=.6, max_iter=70,
                              tolerance_grad=1e-8,tolerance_change=1e-10,line_search_fn="strong_wolfe")
    def closure():
        polish.zero_grad()
        loss,_=objective();loss.backward()
        return loss
    polish.step(closure)
    with torch.no_grad():
        R,G,E,world,kp=forward()
        pred,_=cams.project_t(kp)
        loss,info=objective()
    G,E,world,kp,pred=[a.detach().cpu().numpy() for a in (G,E,world,kp,pred)]
    return {"G":G,"eul":E,"world":world,"kp":kp,"proj":pred,
            "rep_px":np.linalg.norm(pred-uv,axis=-1),"skeleton":sk,
            "solver_info":{"mode":"temporal-v1-experimental","fps":float(fps),
                           "warm_iters":warm_iters,"refine_iters":iters,
                           "position_strength":position_strength,"local_strength":local_strength,
                           "root_strength":root_strength,
                           "robust_pos":robust_pos,"robust_rot":robust_rot,"front_vertical":front_vertical,
                           "objective":float(loss),"terms":{k:float(v) for k,v in info.items()},
                           "contact_constraints":False,"post_filter":False,
                           "image_stationary_segment_strength":stationary_anchor_strength,
                           "depth_mode":depth_mode,"depth_strength":depth_strength,
                           "silhouette_strength":silhouette_strength,"coverage_strength":coverage_strength,
                           "dense_strength":dense_strength,
                           "order_gated_fraction":float(np.mean(order_data['gate']>0)) if order_data is not None and order_data['gate'].size else 0.}}
