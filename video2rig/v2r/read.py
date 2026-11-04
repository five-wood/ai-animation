"""Read a two-view video: split the halves, run the joint reader on every frame."""
import os
import json
import subprocess
import numpy as np
import cv2
import torch

from v2r.data import to_tensor
from v2r.model import JointReader, decode, heatmap_quality


def load_reader(project, device="cuda"):
    st = torch.load(os.path.join(project, "model", "best.pt"), map_location="cpu", weights_only=False)
    W, H = st["net_wh"]
    m = JointReader(st["K"], n_views=len(st["views"]), backbone=st["backbone"], pretrained=False,
                    img_size=(H, W))
    m.load_state_dict(st["model"])
    return m.to(device).eval(), st


def read_video_frames(path, max_frames=None):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
        if max_frames and len(frames) >= max_frames:
            break
    cap.release()
    if not frames:
        raise SystemExit(f"could not read frames from {path}")
    return frames, fps


def find_split(frames):
    """Find the vertical seam between the two camera views (strongest persistent vertical edge
    near the middle); fall back to the exact half."""
    h, w = frames[0].shape[:2]
    acc = np.zeros(w)
    for f in frames[:: max(1, len(frames) // 20)]:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        acc += np.abs(np.diff(g, axis=1)).mean(0).tolist() + [0]
    lo, hi = int(0.4 * w), int(0.6 * w)
    c = lo + int(np.argmax(acc[lo:hi]))
    strength = acc[c] / (np.median(acc[lo:hi]) + 1e-6)
    return (c + 1) if strength > 4 else w // 2


def split_views(frame, split, studio, swap=False):
    """Return list of panels (resized to the studio panel size) and their crop boxes."""
    h, w = frame.shape[:2]
    halves = [(0, split), (split, w)]
    if swap:
        halves = halves[::-1]
    out, boxes = [], []
    pw, ph = studio["panel_w"], studio["panel_h"]
    for x0, x1 in halves:
        crop = frame[:, x0:x1]
        ch, cw = crop.shape[:2]
        # letterbox to the panel aspect ratio (centre), remember the transform
        s = min(pw / cw, ph / ch)
        nw, nh = int(round(cw * s)), int(round(ch * s))
        img = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
        canvas = np.full((ph, pw, 3), int(np.median(img[:4].reshape(-1, 3), 0).mean()), np.uint8)
        ox, oy = (pw - nw) // 2, (ph - nh) // 2
        canvas[oy:oy + nh, ox:ox + nw] = img
        out.append(canvas)
        boxes.append({"x0": x0, "x1": x1, "scale": s, "ox": ox, "oy": oy})
    return out, boxes


def tta_views(n, shift=4.0, zoom=0.04):
    """n read copies as (scale, dx, dy) in net-input pixels: the picture itself first, then copies on a ring of
    `shift` px, alternately zoomed in and out by `zoom` about the net input's centre.  Deterministic, so a
    clip reads the same every time."""
    out = [(1.0, 0.0, 0.0)]
    for k in range(1, n):
        a = 2.0 * np.pi * (k - 1) / max(n - 1, 1)
        out.append((1.0 + (zoom if k % 2 else -zoom), shift * float(np.cos(a)), shift * float(np.sin(a))))
    return out


def _copy_affine(W, H, s, dx, dy):
    """3x3 net-input -> copy net-input similarity: scale s about the centre, then shift (dx, dy)."""
    cx, cy = W / 2.0, H / 2.0
    return np.array([[s, 0.0, (1 - s) * cx + dx], [0.0, s, (1 - s) * cy + dy], [0.0, 0.0, 1.0]])


def _back_grid(W, H, hw, hh, A, device):
    """grid_sample grid that samples a copy's heatmap at every base heatmap cell (align_corners=False).
    Base heat cell (j, i) covers net-input pixel ((j + .5) W/hw, (i + .5) H/hh); the copy saw that point
    at A @ x; normalised copy coordinate = 2 x' / size - 1."""
    xs = (np.arange(hw) + 0.5) * W / hw
    ys = (np.arange(hh) + 0.5) * H / hh
    gx, gy = np.meshgrid(xs, ys)
    X = A[0, 0] * gx + A[0, 1] * gy + A[0, 2]
    Y = A[1, 0] * gx + A[1, 1] * gy + A[1, 2]
    g = np.stack([2 * X / W - 1, 2 * Y / H - 1], -1).astype(np.float32)
    return torch.from_numpy(g)[None].to(device)


@torch.no_grad()
def _forward_tta(model, panels, Ms, view, W, H, hw, hh, copies, device):
    """Read each panel once per copy, bring every copy's probability / depth maps back onto the base
    copy's grid and average them.  Returns heat logits (of the averaged probability) and depth maps."""
    import torch.nn.functional as F
    prob_sum, dep_sum = None, None
    for s, dx, dy in copies:
        A = _copy_affine(W, H, s, dx, dy)
        ims = []
        for img, M in zip(panels, Ms):
            Mc = (A @ np.vstack([M, [0.0, 0.0, 1.0]]))[:2].astype(np.float32)
            interp = cv2.INTER_LINEAR if Mc[0, 0] >= 1 else cv2.INTER_AREA
            ims.append(to_tensor(cv2.warpAffine(img, Mc, (W, H), flags=interp, borderMode=cv2.BORDER_REPLICATE)))
        x = torch.stack(ims).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            hm, dp = model(x, view)
        pr, dp = torch.sigmoid(hm.float()), dp.float()
        if (s, dx, dy) != (1.0, 0.0, 0.0):
            grid = _back_grid(W, H, hw, hh, A, device).expand(pr.shape[0], -1, -1, -1)
            pr = F.grid_sample(pr, grid, mode="bilinear", padding_mode="zeros", align_corners=False)
            dp = F.grid_sample(dp, grid, mode="bilinear", padding_mode="border", align_corners=False)
        prob_sum = pr if prob_sum is None else prob_sum + pr
        dep_sum = dp if dep_sum is None else dep_sum + dp
    p = (prob_sum / len(copies)).clamp(1e-6, 1 - 1e-6)
    return torch.log(p / (1 - p)), dep_sum / len(copies)


@torch.no_grad()
def _read_pass(model, st, panels_per_frame, mats, device, batch, return_quality=False, tta=1):
    """mats[t][v]: 2x3 panel->net matrix. Returns uv in panel px, conf, depth.
    tta > 1: every panel is read that many times (tta_views) and the heatmaps are averaged before decoding;
    a reading error that does not repeat between the shifted copies shrinks in the average."""
    W, H = st["net_wh"]
    hw, hh = st["heat_wh"]
    T = len(panels_per_frame)
    V = len(panels_per_frame[0])
    items = [(t, v) for t in range(T) for v in range(V)]
    uv = np.zeros((T, V, st["K"], 2), np.float32)
    conf = np.zeros((T, V, st["K"]), np.float32)
    dep = np.zeros((T, V, st["K"]), np.float32)
    quality = {k: np.zeros_like(dep) for k in ("ambiguity_ratio", "spread_px")}
    copies = tta_views(int(tta)) if tta and int(tta) > 1 else [(1.0, 0.0, 0.0)]
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        view = torch.tensor([v for _, v in chunk], device=device)
        hm, dp = _forward_tta(model, [panels_per_frame[t][v] for t, v in chunk], [mats[t][v] for t, v in chunk],
                              view, W, H, hw, hh, copies, device)
        p, c, d = decode(hm.float(), dp.float())
        # Statistics are not differentiable solver inputs; compute on CPU to avoid
        # allocating peak-distance/mask tensors alongside GPU features and render jobs.
        hq = heatmap_quality(hm.detach().float().cpu()) if return_quality else None
        p = ((p + 0.5) * torch.tensor([W / hw, H / hh], device=device)).cpu().numpy()
        for j, (t, v) in enumerate(chunk):
            M = mats[t][v]
            Minv = cv2.invertAffineTransform(M)
            uv[t, v] = p[j] @ Minv[:, :2].T + Minv[:, 2]
            conf[t, v] = c[j].cpu().numpy()
            dep[t, v] = d[j].cpu().numpy()
            if hq is not None:
                quality["ambiguity_ratio"][t,v] = hq["ambiguity_ratio"][j].cpu().numpy()
                quality["spread_px"][t,v] = hq["spread_heatmap_px"][j].cpu().numpy() * (W / hw) / M[0,0]
    return (uv, conf, dep, quality) if return_quality else (uv, conf, dep)


def run_reader(model, st, panels_per_frame, device="cuda", batch=8, two_pass=True, margin=1.35, return_quality=False,
               tta=1):
    """panels_per_frame: list over T of list over V of BGR panel images (studio size).
    Pass 1 reads the whole panel; pass 2 reads a creature-centred crop (temporally smoothed box).
    tta: read copies averaged in the final pass (1 = a single read, the original behaviour).
    Returns uv (T,V,K,2) in panel px, conf (T,V,K), depth (T,V,K)."""
    from v2r.data import crop_matrix, box_from_points
    W, H = st["net_wh"]
    T = len(panels_per_frame)
    V = len(panels_per_frame[0])
    ph, pw = panels_per_frame[0][0].shape[:2]
    full = np.float32([[W / pw, 0, 0], [0, H / ph, 0]])
    first = _read_pass(model, st, panels_per_frame, [[full] * V for _ in range(T)], device, batch,
                       return_quality=return_quality and not two_pass, tta=1 if two_pass else tta)
    uv, conf, dep = first[:3]
    if not two_pass:
        return first
    boxes = np.zeros((T, V, 3))
    for t in range(T):
        for v in range(V):
            good = conf[t, v] > 0.2
            pts = uv[t, v][good] if good.sum() >= 3 else uv[t, v]
            boxes[t, v] = box_from_points(pts, W, H, margin=margin)
    # temporal smoothing of the boxes (median then mean)
    k = 2
    pad = np.pad(boxes, ((k, k), (0, 0), (0, 0)), mode="edge")
    boxes = np.median(np.stack([pad[i:i + T] for i in range(2 * k + 1)]), 0)
    mats = [[crop_matrix(*boxes[t, v], W, H) for v in range(V)] for t in range(T)]
    return _read_pass(model, st, panels_per_frame, mats, device, batch, return_quality=return_quality, tta=tta)


def write_video(path, frames, fps):
    h, w = frames[0].shape[:2]
    tmp = path + ".tmp.mp4"
    vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        vw.write(f)
    vw.release()
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-crf", "20", "-movflags", "+faststart", path])
    if r.returncode == 0:
        os.remove(tmp)
    else:
        os.replace(tmp, path)
