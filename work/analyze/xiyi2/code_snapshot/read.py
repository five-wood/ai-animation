"""Read a two-view video: split the halves, run the joint reader on every frame."""
import os
import json
import subprocess
import numpy as np
import cv2
import torch

from v2r.data import to_tensor
from v2r.model import JointReader, decode


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


@torch.no_grad()
def _read_pass(model, st, panels_per_frame, mats, device, batch):
    """mats[t][v]: 2x3 panel->net matrix. Returns uv in panel px, conf, depth."""
    W, H = st["net_wh"]
    hw, hh = st["heat_wh"]
    T = len(panels_per_frame)
    V = len(panels_per_frame[0])
    items = [(t, v) for t in range(T) for v in range(V)]
    uv = np.zeros((T, V, st["K"], 2), np.float32)
    conf = np.zeros((T, V, st["K"]), np.float32)
    dep = np.zeros((T, V, st["K"]), np.float32)
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
            hm, dp = model(x, view)
        p, c, d = decode(hm.float(), dp.float())
        p = ((p + 0.5) * torch.tensor([W / hw, H / hh], device=device)).cpu().numpy()
        for j, (t, v) in enumerate(chunk):
            M = mats[t][v]
            Minv = cv2.invertAffineTransform(M)
            uv[t, v] = p[j] @ Minv[:, :2].T + Minv[:, 2]
            conf[t, v] = c[j].cpu().numpy()
            dep[t, v] = d[j].cpu().numpy()
    return uv, conf, dep


def run_reader(model, st, panels_per_frame, device="cuda", batch=8, two_pass=True, margin=1.35):
    """panels_per_frame: list over T of list over V of BGR panel images (studio size).
    Pass 1 reads the whole panel; pass 2 reads a creature-centred crop (temporally smoothed box).
    Returns uv (T,V,K,2) in panel px, conf (T,V,K), depth (T,V,K)."""
    from v2r.data import crop_matrix, box_from_points
    W, H = st["net_wh"]
    T = len(panels_per_frame)
    V = len(panels_per_frame[0])
    ph, pw = panels_per_frame[0][0].shape[:2]
    full = np.float32([[W / pw, 0, 0], [0, H / ph, 0]])
    uv, conf, dep = _read_pass(model, st, panels_per_frame, [[full] * V for _ in range(T)], device, batch)
    if not two_pass:
        return uv, conf, dep
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
    uv2, conf2, dep2 = _read_pass(model, st, panels_per_frame, mats, device, batch)
    return uv2, conf2, dep2


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
