"""Build a 'bad Seedance clip' from the exam video: left half = the motion, right half =
the same motion played backwards (two different moves in the two pictures)."""
import sys, os
import cv2
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from v2r.read import read_video_frames, write_video

src, dst = sys.argv[1], sys.argv[2]
frames, fps = read_video_frames(src)
h, w = frames[0].shape[:2]
out = []
T = len(frames)
for t in range(T):
    f = frames[t].copy()
    f[:, w // 2:] = frames[T - 1 - t][:, w // 2:]
    out.append(f)
write_video(dst, out, fps)
print("wrote", dst, T, "frames")
