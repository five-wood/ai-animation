"""Draw generated labels on a few samples -> contact sheet."""
import sys, os, json, glob
import numpy as np
import cv2
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from v2r.viz import composite_bg, draw_kps, put_title

proj, gen, out = sys.argv[1], sys.argv[2], sys.argv[3]
n = int(sys.argv[4]) if len(sys.argv) > 4 else 8
rig = json.load(open(os.path.join(proj, "rig.json")))
studio = json.load(open(os.path.join(proj, "studio.json")))
meta = []
for f in sorted(glob.glob(os.path.join(gen, "meta_*.json"))):
    meta += json.load(open(f))
tiles = []
for rec in meta[:n]:
    row = []
    for c in studio["cams"]:
        im = cv2.imread(os.path.join(gen, rec[c["name"]]["img"]), cv2.IMREAD_UNCHANGED)
        im = composite_bg(im)
        im = draw_kps(im, np.array(rec[c["name"]]["uvz"]), rig, vis=np.array(rec[c["name"]]["vis"]))
        put_title(im, f"{rec['id']} {c['name']} {'FAST' if rec['fast'] else ''}")
        row.append(im)
    tiles.append(np.concatenate(row, 1))
cols = 2
rows = [np.concatenate(tiles[i:i + cols], 1) for i in range(0, len(tiles) - len(tiles) % cols or len(tiles), cols)]
sheet = np.concatenate(rows, 0)
s = 1600 / sheet.shape[1]
cv2.imwrite(out, cv2.resize(sheet, None, fx=s, fy=s, interpolation=cv2.INTER_AREA))
print("wrote", out)
