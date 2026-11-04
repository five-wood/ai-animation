"""Heuristic observation weights, not calibrated probabilities or visibility.

The reader has no visibility head.  Image bounds, peak strength, heatmap shape,
track innovations, projected edge lengths and ray disagreement are only evidence
for *usefulness*, not an estimate of occlusion/visibility or of which view is right.
This module intentionally depends on NumPy only, not on the reader or solver.
"""
import warnings

import numpy as np


WEIGHT_FLOOR = 1e-3
TEMPORAL_RADIUS_SECONDS = 0.125
SCALE_RADIUS_SECONDS = 0.5


def _median(a, axis=0):
    """Internal NaNs mark unavailable evidence; public diagnostics stay finite."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(a, axis=axis)


def _finite(a, default=0.0):
    return np.nan_to_num(a, nan=default, posinf=default, neginf=default)


def _soft_factor(score, floor):
    # Squaring a bounded score avoids overflow even on corrupt input.
    return floor + (1.0 - floor) / (1.0 + np.minimum(score, 1e6) ** 2)


def _local_prediction(uv, valid, fps):
    """Leave-one-out, robust linear fit in actual seconds, including endpoints.

    Two IRLS rounds suppress contamination of neighbouring predictions by a
    single bad observation.  Neither speed nor displacement is a penalty.
    """
    T, V, K, _ = uv.shape
    radius = max(0, int(min(T - 1, max(2, np.floor(TEMPORAL_RADIUS_SECONDS * fps + 0.5)))))
    prediction = uv.copy()
    supported = np.zeros((T, V, K), dtype=bool)
    for t in range(T):
        idx = np.arange(max(0, t - radius), min(T, t + radius + 1))
        idx = idx[idx != t]
        if len(idx) < 2:
            continue
        # Normalised time is proportional to seconds but well-conditioned for
        # any finite fps; fps already determined the physical-time window.
        x = (idx - t) / max(radius, 1)
        y = uv[idx]
        mask = valid[idx]
        # A gentle physical-time taper; all samples remain usable at boundaries.
        taper = np.exp(-0.5 * x ** 2)
        # A bounded-cost Theil--Sen seed is important: plain least squares can
        # spread one impulse into false innovations in neighbouring frames.
        # At most nine equally spaced neighbours keep this independent of fps.
        seed = np.unique(np.linspace(0, len(idx) - 1, min(9, len(idx))).astype(int))
        pa, pb = np.triu_indices(len(seed), 1)
        a, b = seed[pa], seed[pb]
        pair_ok = mask[a] & mask[b]
        pair_slope = (y[b] - y[a]) / (x[b] - x[a])[:, None, None, None]
        slope = _finite(_median(np.where(pair_ok[..., None], pair_slope, np.nan), 0))
        intercept = _finite(_median(np.where(mask[..., None], y - x[:, None, None, None] * slope[None], np.nan), 0))
        error = np.linalg.norm(y - intercept[None] - x[:, None, None, None] * slope[None], axis=-1)
        seed_scale = np.maximum(1.0, 1.4826 * _finite(_median(np.where(mask, error, np.nan), 0)))
        huber = np.minimum(1.0, 2.5 * seed_scale[None] / np.maximum(error, 1e-12))
        w = mask * taper[:, None, None] * huber
        for iteration in range(3):
            sw = w.sum(0)
            sx = (w * x[:, None, None]).sum(0)
            sxx = (w * x[:, None, None] ** 2).sum(0)
            sy = (w[..., None] * y).sum(0)
            sxy = (w[..., None] * x[:, None, None, None] * y).sum(0)
            det = sw * sxx - sx ** 2
            ok = (mask.sum(0) >= 2) & (det > 1e-12)
            den = np.where(ok, det, 1.0)[..., None]
            intercept = (sxx[..., None] * sy - sx[..., None] * sxy) / den
            slope = (sw[..., None] * sxy - sx[..., None] * sy) / den
            if iteration < 2:
                error = np.linalg.norm(y - intercept[None] - x[:, None, None, None] * slope[None], axis=-1)
                scale = np.maximum(1.0, 1.4826 * _median(np.where(mask, error, np.nan), 0))
                huber = np.minimum(1.0, 2.5 * _finite(scale, 1.0)[None] / np.maximum(error, 1e-12))
                w = mask * taper[:, None, None] * huber
        prediction[t] = np.where(ok[..., None], intercept, uv[t])
        supported[t] = ok & valid[t]
    return prediction, supported


def _temporal_evidence(uv, valid, fps, pixel_floor):
    prediction, supported = _local_prediction(uv, valid, fps)
    innovation = uv - prediction
    # Coherent image-plane acceleration (translation, a camera move, etc.) is
    # not reader failure.  Require >=3 supported points for the robust median.
    common = _finite(_median(np.where(supported[..., None], innovation, np.nan), axis=2))
    common = np.where((supported.sum(2) >= 3)[..., None], common, 0.0)
    relative = innovation - common[:, :, None]
    relative = np.where(supported[..., None], relative, 0.0)
    T = len(uv)
    r = max(0, int(min(T - 1, max(2, np.floor(SCALE_RADIUS_SECONDS * fps + 0.5)))))
    center = np.zeros_like(relative)
    scale = np.empty(supported.shape)
    for t in range(T):
        sl = slice(max(0, t - r), min(T, t + r + 1))
        data = np.where(supported[sl, ..., None], relative[sl], np.nan)
        med = _finite(_median(data, 0))
        mad = _finite(_median(np.abs(data - med[None]), 0))
        center[t] = med
        # Componentwise robust MAD, with an image-resolution-aware pixel floor.
        scale[t] = np.maximum(pixel_floor[:, None], 1.4826 * np.linalg.norm(mad, axis=-1))
    distance = np.linalg.norm(relative - center, axis=-1)
    z = np.where(supported, distance / scale, 0.0)
    # A broad dead zone allows ordinary articulation and imperfect linear fits.
    score = np.maximum(z - 3.0, 0.0) / 3.0
    return dict(temporal_prediction_px=prediction,
                temporal_supported=supported,
                temporal_innovation_px=innovation,
                temporal_common_innovation_px=common,
                temporal_relative_innovation_px=relative,
                temporal_center_px=center,
                temporal_scale_px=scale,
                temporal_distance_px=distance,
                temporal_z=z,
                temporal_factor=_soft_factor(score, 0.04))


def _edge_evidence(rig, uv, valid, pixel_floor):
    T, V, K, _ = uv.shape
    edges = np.asarray(rig.get("edges", []))
    if not edges.size:
        edges = np.empty((0, 2), dtype=int)
    if edges.ndim != 2 or edges.shape[1] != 2:
        raise ValueError("rig['edges'] must contain keypoint index pairs")
    if not np.issubdtype(edges.dtype, np.integer):
        if not np.all(np.isfinite(edges)) or not np.all(edges == np.floor(edges)):
            raise ValueError("rig edges must be integer keypoint indices")
    edges = edges.astype(int)
    if np.any(edges < 0) or np.any(edges >= K):
        raise ValueError("rig edges contain out-of-range keypoint indices")
    E = len(edges)
    length = np.zeros((T, V, E))
    median = np.zeros((V, E))
    scale = np.zeros((V, E))
    z = np.zeros((T, V, E))
    edge_factor = np.ones((T, V, E))
    point_z = np.zeros((T, V, K))
    point_scale = np.zeros((V, K))
    for e, (a, b) in enumerate(edges):
        length[..., e] = np.linalg.norm(uv[:, :, a] - uv[:, :, b], axis=-1)
        good = valid[:, :, a] & valid[:, :, b]
        values = np.where(good, length[..., e], np.nan)
        med = _finite(_median(values, 0))
        mad = _finite(_median(np.abs(values - med[None]), 0))
        # A generous fractional floor and robust per-view temporal scale avoid
        # interpreting normal foreshortening/perspective as a rigid 2D length.
        s = np.maximum.reduce([1.4826 * mad, 0.25 * med, 2.0 * pixel_floor])
        median[:, e], scale[:, e] = med, s
        zz = np.where(good, np.abs(length[..., e] - med[None]) / s[None], 0.0)
        z[..., e] = zz
        score = np.maximum(zz - 3.0, 0.0) / 3.0
        edge_factor[..., e] = _soft_factor(score, 0.25)
        # A projected edge cannot determine which endpoint is wrong.  Max,
        # rather than a product, avoids penalising high-degree joints more.
        for k in (a, b):
            point_z[:, :, k] = np.maximum(point_z[:, :, k], zz)
            point_scale[:, k] = np.maximum(point_scale[:, k], s)
    point_factor = _soft_factor(np.maximum(point_z - 3.0, 0.0) / 3.0, 0.25)
    return dict(edge_indices=edges, edge_length_px=length, edge_median_px=median,
                edge_scale_px=scale, edge_z=z, edge_factor=edge_factor,
                bone_z=point_z, bone_scale_px=point_scale, bone_factor=point_factor)


def observation_weights(rig, studio, uv, conf, res, fps, heat_quality=None):
    """Return ``(weights, diagnostics)`` without changing any input.

    Parameters
    ----------
    uv : array (T,V,K,2), image pixel coordinates
    conf : array (T,V,K), the reader's original heatmap peak (not probability)
    res : array (T,K,V), nonnegative ray/reprojection disagreement in pixels
    fps : positive finite scalar, required even for short sequences
    heat_quality : optional dict
        ``ambiguity_ratio`` (T,V,K): strongest secondary peak *outside* the
        main-peak exclusion radius divided by main peak. ``spread_px`` of the
        same shape: heatmap spatial spread in image pixels. No quality data is
        inferred if these arrays are missing.

    All per-observation factors have shape (T,V,K); edge diagnostics use
    (T,V,E) or (V,E); vector predictions/innovations append an xy dimension.
    ``temporal_common_innovation_px`` is (T,V,2). All diagnostic values are
    finite NumPy arrays, including raw arrays with separate finite masks.
    ``raw_product`` multiplies bounds_factor, peak_factor, temporal_factor,
    bone_factor, ray_factor, ambiguity_factor and spread_factor (not the
    per-edge edge_factor). The final clamp guarantees strictly positive weights
    >= WEIGHT_FLOOR, even out of frame.

    Ray evidence is shared symmetrically across views and bounded [0.8,1]:
    with two views it cannot identify a wrong view, and must not erase both.
    Image bounds are geometric checks only, never visibility estimation.
    """
    if isinstance(fps, (bool, np.bool_)) or np.ndim(fps) != 0:
        raise ValueError("fps must be a positive finite scalar")
    try:
        fps = float(fps)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("fps must be a positive finite scalar") from exc
    if not np.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be a positive finite scalar")
    uv = np.asarray(uv, dtype=float)
    conf = np.asarray(conf, dtype=float)
    res = np.asarray(res, dtype=float)
    if uv.ndim != 4 or uv.shape[-1] != 2 or any(n < 1 for n in uv.shape[:3]):
        raise ValueError("uv must have nonempty shape (T,V,K,2)")
    T, V, K, _ = uv.shape
    if conf.shape != (T, V, K) or res.shape != (T, K, V):
        raise ValueError("conf must have shape (T,V,K), res must have shape (T,K,V)")
    if "keypoints" in rig and len(rig["keypoints"]) != K:
        raise ValueError("rig keypoint count does not match uv")
    cams = studio.get("cams")
    if cams is not None and len(cams) != V:
        raise ValueError("studio camera count does not match uv")
    widths, heights = [], []
    for v in range(V):
        cam = cams[v] if cams is not None else {}
        widths.append(cam.get("w", studio.get("panel_w", 0)))
        heights.append(cam.get("h", studio.get("panel_h", 0)))
    dims = np.asarray([widths, heights], dtype=float)
    if not np.all(np.isfinite(dims)) or np.any(dims <= 0):
        raise ValueError("studio must supply positive finite image widths and heights")
    width, height = dims
    pixel_floor = np.maximum(1.5, 0.002 * np.hypot(width, height))
    coord_finite = np.all(np.isfinite(uv), axis=-1)
    # Bound corrupt finite magnitudes before subtraction/norms, avoiding overflow.
    safe_uv = np.clip(_finite(uv), -1e12, 1e12)
    in_frame = (coord_finite & (uv[..., 0] >= 0) & (uv[..., 0] < width[None, :, None])
                & (uv[..., 1] >= 0) & (uv[..., 1] < height[None, :, None]))
    bounds_factor = np.where(in_frame, 1.0, 0.01)
    peak_finite = np.isfinite(conf)
    peak = np.clip(_finite(conf), 0.0, 1.0)
    peak_factor = 0.05 + 0.95 * peak
    d = dict(uv_finite=coord_finite, in_frame=in_frame,
             peak_raw=_finite(conf), peak_finite=peak_finite,
             peak_clipped=peak, bounds_factor=bounds_factor, peak_factor=peak_factor,
             pixel_floor_px=pixel_floor, fps=np.asarray(fps),
             temporal_radius_seconds=np.asarray(TEMPORAL_RADIUS_SECONDS),
             scale_radius_seconds=np.asarray(SCALE_RADIUS_SECONDS),
             weight_floor=np.asarray(WEIGHT_FLOOR))
    d.update(_temporal_evidence(safe_uv, in_frame, fps, pixel_floor))
    d.update(_edge_evidence(rig, safe_uv, in_frame, pixel_floor))
    rr = res.transpose(0, 2, 1)
    ray_valid = np.isfinite(rr) & (rr >= 0)
    # An unavailable residual is not positive evidence of disagreement.
    ray = np.where(ray_valid, np.clip(_finite(rr), 0, 1e12), 0.0)
    shared_ray = ray.max(axis=1)
    ray_scale = max(3.0, 0.01 * float(np.median(np.hypot(width, height))))
    shared_factor = _soft_factor(shared_ray / ray_scale, 0.8)
    ray_factor = np.broadcast_to(shared_factor[:, None], (T, V, K)).copy()
    if V == 1:
        ray_factor.fill(1.0)
    d.update(ray_residual_px=ray, ray_residual_finite=ray_valid,
             ray_shared_px=shared_ray, ray_scale_px=np.asarray(ray_scale), ray_factor=ray_factor)
    heat_quality = {} if heat_quality is None else heat_quality
    if not isinstance(heat_quality, dict):
        raise ValueError("heat_quality must be a dict or None")
    for name in ("ambiguity_ratio", "spread_px"):
        available = name in heat_quality
        raw = np.asarray(heat_quality[name], dtype=float) if available else np.zeros((T, V, K))
        if raw.shape != (T, V, K):
            raise ValueError("heat_quality[%r] must have shape (T,V,K)" % name)
        good = np.isfinite(raw) & (raw >= 0) & available
        clean = np.where(good, np.clip(_finite(raw), 0, 1e12), 0.0)
        if name == "ambiguity_ratio":
            score = np.maximum(clean - 0.35, 0.0) / 0.5
            factor = _soft_factor(score, 0.2)
            d["ambiguity_factor"] = factor
        else:
            # Broad peaks are weak evidence, not a hard invalidation.
            spread_scale = np.maximum(3.0, 2.0 * pixel_floor)[None, :, None]
            score = np.maximum(clean / spread_scale - 1.0, 0.0) / 2.0
            d["spread_factor"] = _soft_factor(score, 0.3)
            d["spread_scale_px"] = np.broadcast_to(spread_scale, (T, V, K)).copy()
        d[name] = clean
        d[name + "_available"] = good
    factors = ("bounds_factor", "peak_factor", "temporal_factor", "bone_factor",
               "ray_factor", "ambiguity_factor", "spread_factor")
    product = np.ones((T, V, K))
    for name in factors:
        product *= d[name]
    weights = np.clip(product, WEIGHT_FLOOR, 1.0)
    d["raw_product"] = product
    d["weights"] = weights.copy()
    return weights, d
