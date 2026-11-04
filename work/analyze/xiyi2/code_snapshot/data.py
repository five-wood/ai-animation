"""Training data: rendered RGBA views + exact labels, made "a bit worse, like Seedance does"."""
import os
import glob
import json
import math
import random
import numpy as np
import cv2
import torch
from torch.utils.data import Dataset

cv2.setNumThreads(1)
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def net_input_size(panel_w, panel_h, target_w=448, patch=14):
    s = target_w / panel_w
    W = int(round(panel_w * s / patch)) * patch
    H = int(round(panel_h * s / patch)) * patch
    return W, H


def to_tensor(img_bgr):
    x = img_bgr[..., ::-1].astype(np.float32) / 255.0
    x = (x - MEAN) / STD
    return torch.from_numpy(np.ascontiguousarray(x.transpose(2, 0, 1)))


# ----------------------------------------------------------------------------- backgrounds / degradations
def studio_background(h, w, rng):
    """Grey studio sweep like the Seedance clips (gradient, soft vignette, low-freq noise)."""
    base = rng.uniform(140, 235)
    tint = np.array([rng.uniform(-8, 8), rng.uniform(-8, 8), rng.uniform(-8, 8)])
    yy = np.linspace(-1, 1, h)[:, None]
    xx = np.linspace(-1, 1, w)[None, :]
    g = base + rng.uniform(-25, 25) * yy + rng.uniform(-10, 10) * xx
    g = g - rng.uniform(0, 30) * (xx ** 2 + yy ** 2)
    noise = cv2.resize(np.random.RandomState(rng.randint(0, 1 << 30)).randn(max(2, h // 64), max(2, w // 64)),
                       (w, h), interpolation=cv2.INTER_CUBIC)
    g = g + noise * rng.uniform(0, 8)
    img = np.clip(g[..., None] + tint[None, None, :], 0, 255)
    if rng.random() < 0.3:  # occasional darker/coloured backdrop
        img = img * rng.uniform(0.5, 1.0)
    return img.astype(np.float32)


def add_shadow(bg, alpha, rng):
    """Soft ground shadow under the creature: blurred, squashed copy of the silhouette."""
    h, w = alpha.shape
    ys, xs = np.nonzero(alpha > 0.5)
    if len(ys) < 20:
        return bg
    bottom = ys.max()
    sil = alpha.copy()
    sq = rng.uniform(0.08, 0.3)
    M = np.float32([[1, rng.uniform(-0.6, 0.6), 0], [0, sq, bottom * (1 - sq)]])
    sh = cv2.warpAffine(sil, M, (w, h))
    k = int(rng.uniform(9, 41)) | 1
    sh = cv2.GaussianBlur(sh, (k, k), 0)
    return bg * (1 - rng.uniform(0.2, 0.55) * sh[..., None])


def add_dust(img, alpha, rng):
    """Light dust clouds around the feet (Seedance loves them)."""
    h, w = alpha.shape
    ys, xs = np.nonzero(alpha > 0.5)
    if len(ys) < 20:
        return img
    bottom = ys.max()
    x0, x1 = xs.min(), xs.max()
    layer = np.zeros((h, w), np.float32)
    for _ in range(rng.randint(2, 8)):
        cx = rng.uniform(x0 - 0.2 * (x1 - x0), x1 + 0.2 * (x1 - x0))
        cy = bottom - rng.uniform(-10, 40)
        r = rng.uniform(10, 0.25 * (x1 - x0) + 12)
        cv2.circle(layer, (int(cx), int(cy)), int(r), 1.0, -1)
    k = int(rng.uniform(21, 61)) | 1
    layer = cv2.GaussianBlur(layer, (k, k), 0) * rng.uniform(0.3, 0.8)
    col = rng.uniform(190, 240)
    return img * (1 - layer[..., None]) + col * layer[..., None]


def motion_blur(img, rng):
    k = rng.randint(3, 11)
    ker = np.zeros((k, k), np.float32)
    ker[k // 2, :] = 1.0 / k
    M = cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), rng.uniform(0, 180), 1)
    ker = cv2.warpAffine(ker, M, (k, k))
    ker /= max(ker.sum(), 1e-6)
    return cv2.filter2D(img, -1, ker)


def degrade(img, rng):
    """Global degradations: colour, blur, noise, resampling, JPEG."""
    img = img.astype(np.float32)
    if rng.random() < 0.8:
        a = rng.uniform(0.7, 1.3)
        b = rng.uniform(-25, 25)
        img = img * a + b
    if rng.random() < 0.5:
        gam = rng.uniform(0.75, 1.35)
        img = 255.0 * np.clip(img / 255.0, 0, 1) ** gam
    if rng.random() < 0.5:
        gray = img.mean(2, keepdims=True)
        img = gray + (img - gray) * rng.uniform(0.4, 1.4)
    if rng.random() < 0.4:
        img = img * np.array([rng.uniform(0.85, 1.15) for _ in range(3)])[None, None]
    img = np.clip(img, 0, 255).astype(np.uint8)
    if rng.random() < 0.35:
        s = rng.uniform(0.3, 1.6)
        img = cv2.GaussianBlur(img, (0, 0), s)
    if rng.random() < 0.25:
        img = motion_blur(img, rng)
    if rng.random() < 0.4:
        h, w = img.shape[:2]
        f = rng.uniform(0.35, 0.8)
        img = cv2.resize(cv2.resize(img, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA), (w, h),
                         interpolation=cv2.INTER_LINEAR)
    if rng.random() < 0.4:
        n = np.random.RandomState(rng.randint(0, 1 << 30)).randn(*img.shape) * rng.uniform(2, 9)
        img = np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)
    if rng.random() < 0.7:
        q = rng.randint(25, 90)
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        img = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return img


def fg_jitter(rgb, rng):
    """Seedance re-paints the creature a little: hue / saturation / contrast drift."""
    if rng.random() < 0.6:
        hsv = cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 0] = (hsv[..., 0] + rng.uniform(-8, 8)) % 180
        hsv[..., 1] *= rng.uniform(0.6, 1.4)
        hsv[..., 2] *= rng.uniform(0.75, 1.25)
        rgb = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    return rgb


def compose(rgba, rng, train=True):
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3].astype(np.float32) / 255.0
    h, w = alpha.shape
    if not train:
        bg = np.full((h, w, 3), 200.0, np.float32)
        return (rgb * alpha[..., None] + bg * (1 - alpha[..., None])).astype(np.uint8)
    rgb = fg_jitter(rgb, rng)
    bg = studio_background(h, w, rng)
    if rng.random() < 0.7:
        bg = add_shadow(bg, alpha, rng)
    img = rgb * alpha[..., None] + bg * (1 - alpha[..., None])
    if rng.random() < 0.3:
        img = add_dust(img, alpha, rng)
    return degrade(img, rng)


def crop_matrix(cx, cy, half_h, W, H):
    """2x3 matrix mapping panel pixels -> net-input pixels for a crop centred at (cx,cy)
    with half height half_h (the crop has the net's aspect ratio)."""
    s = H / (2.0 * half_h)
    return np.float32([[s, 0, W / 2.0 - s * cx], [0, s, H / 2.0 - s * cy]])


def box_from_points(pts, W, H, margin=1.3, min_half=40.0):
    """Crop (cx, cy, half_h) that contains the points with a margin, net aspect W/H."""
    a = W / H
    lo, hi = pts.min(0), pts.max(0)
    c = (lo + hi) / 2
    half = max((hi[0] - lo[0]) / a, hi[1] - lo[1]) / 2 * margin
    return float(c[0]), float(c[1]), float(max(half, min_half))


def random_affine(w, h, rng, strength=1.0):
    s = math.exp(rng.uniform(-0.15, 0.15) * strength)
    r = rng.uniform(-6, 6) * strength
    tx = rng.uniform(-0.08, 0.08) * w * strength
    ty = rng.uniform(-0.08, 0.08) * h * strength
    M = cv2.getRotationMatrix2D((w / 2, h / 2), r, s)
    M[:, 2] += (tx, ty)
    return M


# ----------------------------------------------------------------------------- dataset
def load_meta(gen_dir):
    meta = []
    for f in sorted(glob.glob(os.path.join(gen_dir, "meta_*.json"))):
        meta += json.load(open(f))
    meta.sort(key=lambda r: r["id"])
    return meta


class PoseViews(Dataset):
    """One item = one camera view of one random pose."""

    def __init__(self, project, split="train", val_frac=0.05, target_w=448, hm_stride=3.5, seed=0):
        self.dir = os.path.join(project, "train_data")
        self.rig = json.load(open(os.path.join(project, "rig.json")))
        self.studio = json.load(open(os.path.join(project, "studio.json")))
        self.views = [c["name"] for c in self.studio["cams"]]
        meta = load_meta(self.dir)
        nval = max(8, int(len(meta) * val_frac))
        meta = meta[:-nval] if split == "train" else meta[-nval:]
        self.items = [(r, v) for r in meta for v in range(len(self.views))]
        self.train = split == "train"
        self.pw, self.ph = self.studio["panel_w"], self.studio["panel_h"]
        self.W, self.H = net_input_size(self.pw, self.ph, target_w)
        self.hw, self.hh = int(round(self.W / hm_stride)), int(round(self.H / hm_stride))
        self.K = len(self.rig["keypoints"])
        self.size = self.rig["size"]
        self.seed = seed
        self.epoch = 0
        self.full_frac = 0.25

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        rec, v = self.items[i]
        rng = random.Random((self.seed * 7919 + self.epoch) * 1000003 + i) if self.train else random.Random(i)
        vn = self.views[v]
        rgba = cv2.imread(os.path.join(self.dir, rec[vn]["img"]), cv2.IMREAD_UNCHANGED)
        if rgba.shape[2] == 3:
            rgba = np.concatenate([rgba, np.full(rgba.shape[:2] + (1,), 255, np.uint8)], 2)
        uvz = np.array(rec[vn]["uvz"], np.float32)
        vis = np.array(rec[vn]["vis"], np.float32)
        uv = uvz[:, :2].copy()
        if self.train and rng.random() < 0.85:
            M = random_affine(self.pw, self.ph, rng)
            rgba = cv2.warpAffine(rgba, M, (self.pw, self.ph), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
            uv = uv @ M[:, :2].T + M[:, 2]
        img = compose(rgba, rng, self.train)
        full = (rng.random() < self.full_frac) if self.train else (i % 4 == 0)
        if full:
            M = np.float32([[self.W / self.pw, 0, 0], [0, self.H / self.ph, 0]])
            img = cv2.resize(img, (self.W, self.H), interpolation=cv2.INTER_AREA)
        else:
            # creature-centred crop, like the inference second pass (box from noisy joints)
            pts = uv + (np.array([[rng.gauss(0, 3), rng.gauss(0, 3)]], np.float32) if self.train else 0)
            cx, cy, hb = box_from_points(pts, self.W, self.H,
                                         margin=rng.uniform(1.2, 1.6) if self.train else 1.35)
            if self.train:
                cx += rng.gauss(0, 0.06) * hb
                cy += rng.gauss(0, 0.06) * hb
            M = crop_matrix(cx, cy, hb, self.W, self.H)
            interp = cv2.INTER_LINEAR if M[0, 0] >= 1 else cv2.INTER_AREA
            img = cv2.warpAffine(img, M, (self.W, self.H), flags=interp, borderMode=cv2.BORDER_REPLICATE)
        uv_net = (uv @ M[:, :2].T + M[:, 2]).astype(np.float32)
        inside = (uv_net[:, 0] >= 0) & (uv_net[:, 0] < self.W) & (uv_net[:, 1] >= 0) & (uv_net[:, 1] < self.H)
        z = uvz[:, 2]
        depth = (z - z.mean()) / self.size  # relative depth, view independent definition
        return {
            "img": to_tensor(img),
            "uv": torch.from_numpy(uv_net.astype(np.float32)),  # in net-input pixels
            "inside": torch.from_numpy(inside.astype(np.float32)),
            "vis": torch.from_numpy(vis),
            "depth": torch.from_numpy(depth.astype(np.float32)),
            "view": v,
            "px_scale": float(1.0 / M[0, 0]),  # net px -> panel px
        }
