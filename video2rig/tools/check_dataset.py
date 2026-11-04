"""Show augmented training items with their labels."""
import sys, os
import numpy as np
import cv2
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from v2r.data import PoseViews, MEAN, STD
from v2r.viz import draw_kps

ds = PoseViews(sys.argv[1], "train")
ds.epoch = 3
tiles = []
for i in range(0, 12 * 37, 37):
    it = ds[i]
    img = ((it["img"].numpy().transpose(1, 2, 0) * STD + MEAN) * 255).clip(0, 255).astype(np.uint8)[..., ::-1].copy()
    uv = it["uv"].numpy()
    tiles.append(draw_kps(img, uv, ds.rig, vis=it["vis"].numpy(), r=3))
rows = [np.concatenate(tiles[i:i + 6], 1) for i in range(0, 12, 6)]
cv2.imwrite(sys.argv[2], np.concatenate(rows, 0))
print(ds.W, ds.H, ds.hw, ds.hh, len(ds))
