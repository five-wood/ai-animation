"""Frames where the dense solve fits the video silhouette worst / best compared with keypoints only, and what the dense
reader matched there (matched vertex positions drawn on the video panel, coloured by their rest-pose x: left/right)."""
import json
import sys

import numpy as np

D = "/data/video2rig/experiments/surface_solver"
name = sys.argv[1]
r = json.load(open(f"{D}/real_dense_{name}.json"))
a = np.array(r["iou_per_frame"]["keypoints"])
b = np.array(r["iou_per_frame"]["dense_3"])
d = (b - a).mean(1)
print("frames where dense_3 is worse by > 0.03:", int((d < -0.03).sum()), "better by > 0.03:", int((d > 0.03).sum()),
      "of", len(d))
worst = np.argsort(d)[:8]
print("worst frames (t, view0 kp->dense, view1 kp->dense):",
      [(int(t), round(a[t, 0], 3), round(b[t, 0], 3), round(a[t, 1], 3), round(b[t, 1], 3)) for t in worst])
best = np.argsort(-d)[:5]
print("best frames:", [(int(t), round(float(d[t]), 3)) for t in best])
# where in the clip
seg = [(i, i + 24) for i in range(0, len(d), 24)]
print("per second mean IoU change:", [round(float(d[s:e].mean()), 3) for s, e in seg])
