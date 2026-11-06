"""Check dense labels against the training pictures: label mask vs rendered alpha (IoU at the label grid), and a
picture of a few samples with the label coordinates shown as colours (x -> red, y -> green, z -> blue) next to the
render, plus the dataset's resampled labels after a random crop (they must stay on the creature).

    python check_labels.py <project> <n samples> <out.jpg>
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, "/data/video2rig/experiments/surface_solver/code")
from v2r.data import PoseViews  # noqa: E402

P, n, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
td = f"{P}/train_data"
step = json.load(open(f"{td}/dense_meta.json"))["step"]
ds = PoseViews(P, "train", dense=True)
ious, rows = [], []
idx = [int(i) for i in np.linspace(0, len(ds.items) - 1, n)]
for k in range(n):
    rec, v = ds.items[idx[k]]
    vn = ds.views[v]
    rgba = cv2.imread(f"{td}/{rec[vn]['img']}", cv2.IMREAD_UNCHANGED)
    z = np.load(f"{td}/dense_{rec['id']:06d}.npz")
    m, c = z["mask"][v], z["coord"][v].astype(np.float32)
    a = cv2.resize(rgba[..., 3], (m.shape[1], m.shape[0]), interpolation=cv2.INTER_AREA) > 127
    ious.append((a & m).sum() / max((a | m).sum(), 1))
    if k < 4:
        col = ((c + 1) / 2 * 255).clip(0, 255).astype(np.uint8)[..., ::-1]
        col[~m] = 60
        col = cv2.resize(col, (rgba.shape[1], rgba.shape[0]), interpolation=cv2.INTER_NEAREST)
        bg = np.full(rgba.shape[:2] + (3,), 200, np.float32)
        al = rgba[..., 3:4] / 255.0
        img = (rgba[..., :3] * al + bg * (1 - al)).astype(np.uint8)
        it = ds[idx[k]]
        x = it["img"].numpy().transpose(1, 2, 0) * np.array([0.229, 0.224, 0.225]) + np.array([0.485, 0.456, 0.406])
        x = (x[..., ::-1] * 255).clip(0, 255).astype(np.uint8)
        hc = ((it["dense_coord"].numpy().transpose(1, 2, 0) + 1) / 2 * 255).clip(0, 255).astype(np.uint8)[..., ::-1]
        hm = it["dense_mask"].numpy() > 0
        hc[~hm] = 60
        hc = cv2.resize(hc, (x.shape[1], x.shape[0]), interpolation=cv2.INTER_NEAREST)
        blend = (0.5 * x + 0.5 * hc).astype(np.uint8)
        h = 360
        tiles = [cv2.resize(t, (int(t.shape[1] * h / t.shape[0]), h)) for t in (img, col, x, blend)]
        rows.append(np.concatenate(tiles, 1))
cv2.imwrite(out, np.concatenate(rows, 0), [cv2.IMWRITE_JPEG_QUALITY, 88])
print(f"[check] {n} samples: label mask vs alpha IoU mean {np.mean(ious):.4f} min {np.min(ious):.4f} -> {out}")
