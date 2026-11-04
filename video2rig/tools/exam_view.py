"""Frames of the exam: GT 3D keypoints vs solved, projected into both views, plus per-frame leg error."""
import json, sys, os
import numpy as np
import cv2
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from v2r.geom import Cams
from v2r.viz import draw_kps, put_title

p, out = sys.argv[1], sys.argv[2]
frames = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [10, 60, 110]
rig = json.load(open(os.path.join(p, "rig.json")))
studio = json.load(open(os.path.join(p, "studio.json")))
gt = json.load(open(os.path.join(p, "exam", "frames", "motion_gt.json")))
mot = np.load(os.path.join(p, "out", "exam", "motion.npz"))
rd = np.load(os.path.join(p, "out", "exam", "reading.npz"))
cams = Cams(studio)
rows = []
for t in frames:
    row = []
    for v, c in enumerate(studio["cams"]):
        im = cv2.imread(os.path.join(p, "exam", "frames", gt["frames"][t][c["name"]]["img"]), cv2.IMREAD_UNCHANGED)
        a = im[..., 3:4] / 255.0
        im = (im[..., :3] * a + 200 * (1 - a)).astype(np.uint8)
        g = np.array(gt["frames"][t][c["name"]]["uvz"])[:, :2]
        s = cams.project_np(mot["kp"][t])[v][:, :2]
        r = rd["uv"][t, v]
        A = draw_kps(im, g, rig, r=3)
        B = draw_kps(im, r, rig, conf=rd["conf"][t, v], r=3)
        C = draw_kps(im, s, rig, r=3)
        for x, title in ((A, "GT"), (B, "READ"), (C, "SOLVED")):
            put_title(x, f"{title} {c['name']} t={t}")
        row += [A, B, C]
    rows.append(np.concatenate(row, 1))
img = np.concatenate(rows, 0)
img = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
cv2.imwrite(out, img)
