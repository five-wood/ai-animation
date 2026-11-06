"""Real Seedance clip: SAM 2 masks at the fastest hand frames vs a slow frame (CPU only, reads saved data)."""
import json
import sys

import cv2
import numpy as np

sys.path.insert(0, "/data/video2rig/experiments/surface_trial/scripts")
from trial_common import E, read_video, calibration  # noqa: E402

P = "/data/video2rig/projects/m11101"
N = "Seedance25_Split_screen_two_loc_1793704727806"
rig = json.load(open(f"{P}/rig.json"))
kn = [k["name"] for k in rig["keypoints"]]
rd = np.load(f"{P}/out/{N}_retrain2_temporal/reading.npz")
uv = rd["uv"].copy()
for v, (s, sh, _) in enumerate(calibration(rd)):
    uv[:, v] = (uv[:, v] - np.array(sh)) / s
frames = read_video(f"/data/video2rig/videos/{N}.mp4")
T = len(frames)
hands = [kn.index("Bip001 R Hand"), kn.index("Bip001 L Hand")]
out = {}
for v, cname in enumerate(("front", "side")):
    z = np.load(f"{E}/data/sam_m11101_real_{cname}_large_all_f0_p10.npz")
    m = np.unpackbits(z["mask"], -1)[..., :z["shape"][-1]].astype(bool)
    sp = np.zeros(T)
    sp[1:] = np.linalg.norm(np.diff(uv[:, v, hands], axis=0), axis=-1).max(1)
    area = m.reshape(T, -1).sum(1).astype(float)
    picks = []
    for a, b in ((0.4, 1.2), (1.8, 2.7), (3.8, 4.7)):
        i, j = int(a * 24), int(b * 24)
        picks.append(i + int(np.argmax(sp[i:j])))
    slow = int(np.argmin(sp[5:]) + 5)
    tiles = []
    for t in picks + [slow]:
        pan = frames[t][:, v * 640:(v + 1) * 640]
        g = cv2.cvtColor(pan, cv2.COLOR_BGR2GRAY)
        o = pan.copy()
        o[m[t]] = (0.6 * o[m[t]] + 0.4 * np.array([0, 0, 255])).astype(np.uint8)
        cs, _ = cv2.findContours(m[t].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(o, cs, -1, (0, 255, 0), 1)
        tag = f"t={t} hand {sp[t]:.0f}px/f" + (" (slow)" if t == slow else "")
        for im in (pan, o):
            cv2.putText(im, tag, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 200), 2)
        tiles.append(np.concatenate([pan, o], 0))
    sheet = np.concatenate(tiles, 1)
    cv2.imwrite(f"{E}/out/blur_real_{cname}.jpg", cv2.resize(sheet, (sheet.shape[1] // 2, sheet.shape[0] // 2)),
                [cv2.IMWRITE_JPEG_QUALITY, 90])
    out[cname] = {"peak_frames": picks, "peak_speed_px": [round(float(sp[t]), 1) for t in picks],
                  "slow_frame": slow, "area_at_peaks_rel_median": [round(float(area[t] / np.median(area)), 3) for t in picks]}
print(json.dumps(out))
