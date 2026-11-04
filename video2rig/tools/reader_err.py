"""2D reader error on the exam clip vs the known answer (per keypoint, visible / hidden)."""
import json, sys, os
import numpy as np
p = sys.argv[1]
name = sys.argv[2] if len(sys.argv) > 2 else "exam"
rig = json.load(open(os.path.join(p, "rig.json")))
studio = json.load(open(os.path.join(p, "studio.json")))
gt = json.load(open(os.path.join(p, "exam", "frames", "motion_gt.json")))
rd = np.load(os.path.join(p, "out", name, "reading.npz"))
views = [c["name"] for c in studio["cams"]]
g = np.array([[f[v]["uvz"] for v in views] for f in gt["frames"]])[..., :2]
vis = np.array([[f[v]["vis"] for v in views] for f in gt["frames"]])
for key in ("uv_raw", "uv"):
    e = np.linalg.norm(rd[key] - g, axis=-1)
    print(f"{key}: visible median {np.median(e[vis > 0]):.2f}px mean {e[vis > 0].mean():.2f}  "
          f"hidden median {np.median(e[vis == 0]):.2f}px  all p90 {np.percentile(e, 90):.2f}  >20px {(e > 20).mean() * 100:.1f}%")
e = np.linalg.norm(rd["uv"] - g, axis=-1)
print("per keypoint median px (front/side):")
print(" ".join(f"{k['name']}:{np.median(e[:, 0, i]):.0f}/{np.median(e[:, 1, i]):.0f}" for i, k in enumerate(rig["keypoints"])))
size_px = np.median(np.ptp(g.reshape(-1, g.shape[2], 2), axis=1).max(-1))
print(f"creature size on screen ~{size_px:.0f}px")
