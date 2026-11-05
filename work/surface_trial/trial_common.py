"""Shared helpers for the surface trial (SAM 2 silhouettes, Open3D visibility).  Experimental, not v2r code."""
import json
import os

import numpy as np

E = "/data/video2rig/experiments/surface_trial"
PROJ = "/data/video2rig/projects"


def load_project(p):
    P = os.path.join(PROJ, p)
    rig = json.load(open(os.path.join(P, "rig.json")))
    studio = json.load(open(os.path.join(P, "studio.json")))
    return P, rig, studio


def chain_top(bones, i):
    while bones[i]["parent"] >= 0:
        i = bones[i]["parent"]
    return i


def prop_keypoints(rig):
    """Keypoints whose bone chain does not hang under the main skeleton root (m11101: the guns)."""
    bones = rig["bones"]
    roots = [i for i, b in enumerate(bones) if b["parent"] < 0]
    main = max(roots, key=lambda r: sum(1 for i in range(len(bones)) if chain_top(bones, i) == r))
    return np.array([chain_top(bones, k["bone"]) != main for k in rig["keypoints"]])


def cam_arrays(cam):
    K, R, t = np.array(cam["K"], float), np.array(cam["R"], float), np.array(cam["t"], float)
    return K, R, t, -R.T @ t


def project(cam, X):
    K, R, t, _ = cam_arrays(cam)
    Xc = X @ R.T + t
    uv = Xc @ K.T
    return np.concatenate([uv[..., :2] / uv[..., 2:3], Xc[..., 2:3]], -1)


def pixel_rays(cam, step=1):
    """Rays through pixel centres of a (h/step, w/step) grid: (H,W,6) float32 [origin, unit dir], dir.z_cam."""
    K, R, t, C = cam_arrays(cam)
    us = np.arange(0, cam["w"], step) + step / 2.0
    vs = np.arange(0, cam["h"], step) + step / 2.0
    uu, vv = np.meshgrid(us, vs)
    pix = np.stack([uu, vv, np.ones_like(uu)], -1)
    d = pix @ np.linalg.inv(K).T @ R
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    rays = np.concatenate([np.broadcast_to(C, d.shape), d], -1).astype(np.float32)
    return rays, (d @ R[2]).astype(np.float32)


def o3d_scene(V, F):
    import open3d as o3d
    s = o3d.t.geometry.RaycastingScene()
    s.add_triangles(o3d.core.Tensor(np.ascontiguousarray(V, np.float32)),
                    o3d.core.Tensor(np.ascontiguousarray(F, np.uint32)))
    return s


def cast(scene, rays):
    import open3d as o3d
    ans = scene.cast_rays(o3d.core.Tensor(np.ascontiguousarray(rays, np.float32)))
    return ans["t_hit"].numpy(), ans["primitive_ids"].numpy()


def iou(a, b):
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 1.0


def boundary_dist(a, b):
    """Mean symmetric distance (px) between the boundaries of two binary masks."""
    import cv2

    def edge(m):
        m = m.astype(np.uint8)
        return (m - cv2.erode(m, np.ones((3, 3), np.uint8))) > 0

    ea, eb = edge(a), edge(b)
    if ea.sum() == 0 or eb.sum() == 0:
        return float("nan")
    da = cv2.distanceTransform((~eb).astype(np.uint8), cv2.DIST_L2, 3)
    db = cv2.distanceTransform((~ea).astype(np.uint8), cv2.DIST_L2, 3)
    return float(0.5 * (da[ea].mean() + db[eb].mean()))


def read_video(path):
    import cv2
    cap = cv2.VideoCapture(path)
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f)
    return out


def panels(frame, studio):
    """The exam and the Seedance clips are 1280x720 split at the middle = exactly the studio panels."""
    w = studio["panel_w"]
    assert frame.shape[1] == 2 * w and frame.shape[0] == studio["panel_h"], frame.shape
    return [frame[:, :w], frame[:, w:]]


def calibration(rd):
    """The per-view frame-0 calibration the pipeline actually applied (studio = video * s + shift), recovered
    exactly from reading.npz: uv_raw is the calibrated reading, uv_reader the reading before calibration
    (both before the near/far swap fix, so the keypoint order matches)."""
    out = []
    for v in range(rd["uv_raw"].shape[1]):
        a = rd["uv_reader"][:, v].reshape(-1, 2)
        b = rd["uv_raw"][:, v].reshape(-1, 2)
        am = a - a.mean(0)
        s = float((am * (b - b.mean(0))).sum() / max((am ** 2).sum(), 1e-12))
        shift = b.mean(0) - s * a.mean(0)
        resid = float(np.abs(a * s + shift - b).max())
        out.append((s, shift.tolist(), resid))
    return out


def warp_mask(m, s, shift):
    import cv2
    M = np.float32([[s, 0, shift[0]], [0, s, shift[1]]])
    return cv2.warpAffine(m.astype(np.uint8), M, (m.shape[1], m.shape[0]), flags=cv2.INTER_NEAREST) > 0
