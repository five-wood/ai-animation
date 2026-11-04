"""Drawing helpers (OpenCV)."""
import numpy as np
import cv2

SIDE_COL = {"L": (40, 160, 255), "R": (255, 150, 40), "M": (235, 235, 235)}  # BGR: L orange, R blue


def composite_bg(rgba, bg=(205, 205, 205)):
    if rgba.shape[2] == 3:
        return rgba
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    out = rgba[..., :3].astype(np.float32) * a + np.array(bg, np.float32) * (1 - a)
    return out.astype(np.uint8)


def draw_kps(img, uv, rig, vis=None, conf=None, r=4, edges=True, label=False):
    img = img.copy()
    kps = rig["keypoints"]
    if edges:
        for a, b in rig["edges"]:
            pa, pb = uv[a], uv[b]
            if np.all(np.isfinite(pa)) and np.all(np.isfinite(pb)):
                cv2.line(img, tuple(int(x) for x in pa[:2]), tuple(int(x) for x in pb[:2]), (90, 220, 90), 1,
                         cv2.LINE_AA)
    for i, k in enumerate(kps):
        p = uv[i]
        if not np.all(np.isfinite(p[:2])):
            continue
        col = SIDE_COL[k["side"]]
        if conf is not None and conf[i] < 0.3:
            col = (60, 60, 200)
        filled = -1 if (vis is None or vis[i] > 0.5) else 1
        cv2.circle(img, (int(p[0]), int(p[1])), r, col, filled, cv2.LINE_AA)
        if label:
            cv2.putText(img, str(i), (int(p[0]) + 4, int(p[1]) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.3, col, 1)
    return img


def put_title(img, text, org=(8, 20), scale=0.5):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)
    return img
