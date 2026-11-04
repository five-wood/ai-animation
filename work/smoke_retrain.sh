#!/bin/bash
# Smoke test of the retrain-prep options on SCRATCH copies only (never the live project):
#   1. prepare with --fist-points --offaxis-points       -> rig.json keypoints, FK check
#   2. gen 6 poses with --contact-poses                  -> renders + labels, reach residual
#   3. train 30 steps with --hidden-empty --smear 0.5    -> runs, logs conf visible/hidden
#   4. smear sheet                                        -> picture to eyeball
# Usage: bash smoke_retrain.sh /tmp/v2r_tta   (code dir)
set -e
CODE=$1
PY=/data/video2rig/.venv/bin/python
S=/tmp/v2r_smoke
rm -rf $S && mkdir -p $S
cd $CODE
echo "== 1. prepare (fist + off-axis points)"
$PY -m v2r.cli prepare /data/video2rig/inputs/11101_01.fbx $S/p --facing=-Y --up=+Z --procedural-material \
    --fist-points --offaxis-points > $S/prepare.log 2>&1 || { tail -n 30 $S/prepare.log; exit 1; }
grep -E '\[v2r\] (fist|off-axis|rigid accessory|bones=)' $S/prepare.log
$PY - <<EOF
import json, numpy as np, sys, torch
sys.path.insert(0, "$CODE")
from v2r.geom import Skeleton
new = json.load(open("$S/p/rig.json")); old = json.load(open("/data/video2rig/projects/m11101/rig.json"))
kn = [k["name"] for k in new["keypoints"]]; ko = [k["name"] for k in old["keypoints"]]
print("keypoints", len(ko), "->", len(kn), "| dropped", sorted(set(ko) - set(kn)), "| added", sorted(set(kn) - set(ko)))
sk = Skeleton(new)
G = np.eye(4)[None]; E = np.zeros((1, sk.A, 3))
_, kp = sk.fk_np(G, E)
err = np.abs(kp[0] - sk.rest_kp).max() / sk.size
print("rest FK reproduces every keypoint (incl. off-axis): max err %.2e of size" % err)
assert err < 1e-4, "FK mismatch"
EOF
echo "== 2. gen 16 poses with contact poses"
mkdir -p $S/td
/opt/unimate/blender-4.0.2-linux-x64/blender -b --factory-startup --python v2r/blender/gen_poses.py -- \
    $S/p $S/td '{"mode":"random","n":16,"start":0,"seed":11,"fast_frac":0.0,"samples":4,"contact_poses":true}' \
    > $S/gen.log 2>&1 || { tail -n 30 $S/gen.log; exit 1; }
grep -E '\[v2r\] gen done|Error|Traceback' $S/gen.log
$PY - <<EOF
import json, glob, numpy as np
meta = json.load(open(sorted(glob.glob("$S/td/meta_*.json"))[0]))
print("samples", len(meta), "contact kinds", [m.get("contact") for m in meta])
rig = json.load(open("$S/p/rig.json"))
kn = [k["name"] for k in rig["keypoints"]]
L = [i for i, n in enumerate(kn) if n == "Bip001 L Hand"][0]; R = [i for i, n in enumerate(kn) if n == "Bip001 R Hand"][0]
for m in meta:
    kp = np.array(m["kp3d"])
    print("   %-10s hand-hand distance %.3f of size, vis front %d/%d" % (m.get("contact"), np.linalg.norm(kp[L] - kp[R]) / rig["size"],
          int(sum(m["front"]["vis"])), len(kn)))
import cv2
tiles = []
for m in [m for m in meta if m.get("contact")][:6]:
    row = []
    for v in ("front", "side"):
        im = cv2.imread("$S/td/" + m[v]["img"], cv2.IMREAD_UNCHANGED)
        a = im[..., 3:4].astype(np.float32) / 255
        im = (im[..., :3] * a + 200 * (1 - a)).astype(np.uint8)
        cv2.putText(im, m["contact"], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 200), 2)
        row.append(cv2.resize(im, (256, 288)))
    tiles.append(np.concatenate(row, 0))
if tiles:
    cv2.imwrite("/tmp/v2r_contact_sheet.png", np.concatenate(tiles, 1))
    print("contact sheet written,", len(tiles), "poses")
EOF
echo "== 3. train 30 steps, hidden-empty + smear (scratch project)"
cp $S/p/studio.json $S/p/studio.json.bak
ln -sfn $S/td $S/p/train_data
$PY - <<EOF 2>&1 | grep -E '\[v2r\]|Error|Traceback' | tail -n 6
import sys; sys.path.insert(0, "$CODE")
from v2r.train import train
train("$S/p", batch=4, workers=2, steps=30, hidden_empty=True, smear_frac=0.5)
EOF
echo "== 4. smear sheet (same clean picture, left without, right with a smear; label points in red)"
$PY - <<EOF
import sys, random, numpy as np, cv2, json
sys.path.insert(0, "$CODE")
from v2r.data import PoseViews, compose, smear_segment
ds = PoseViews("/data/video2rig/projects/m11101", "train", smear_frac=1.0)
print("limb segments for smear:", len(ds.limb_segments))
tiles = []
for i in range(6):
    rec, v = ds.items[i * 37 + 1]
    rgba = cv2.imread("/data/video2rig/projects/m11101/train_data/" + rec[ds.views[v]]["img"], cv2.IMREAD_UNCHANGED)
    uv = np.array(rec[ds.views[v]]["uvz"], np.float32)[:, :2]
    clean = compose(rgba, random.Random(i), train=False).astype(np.float32)
    rng = random.Random(100 + i)
    a, b = ds.limb_segments[int(rng.random() * len(ds.limb_segments))]
    sm = smear_segment(clean.copy(), uv, a, b, rng)
    for img in (clean, sm):
        for p in (uv[a], uv[b]):
            cv2.circle(img, (int(p[0]), int(p[1])), 4, (0, 0, 255), -1)
    tiles.append(np.concatenate([cv2.resize(clean.astype(np.uint8), (256, 288)), cv2.resize(sm.astype(np.uint8), (256, 288))], 1))
sheet = np.concatenate([np.concatenate(tiles[:3], 1), np.concatenate(tiles[3:], 1)], 0)
cv2.imwrite("$S/smear_sheet.png", sheet)
print("sheet written")
EOF
cp /tmp/v2r_contact_sheet.png /tmp/v2r_contact_sheet_out.png 2>/dev/null || true
cp $S/smear_sheet.png /tmp/v2r_smear_sheet.png
rm -rf $S
echo SMOKE OK
