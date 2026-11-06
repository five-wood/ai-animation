#!/bin/bash
# m11101_nogun: m11101 without its two guns (prepare drops prop meshes by default since 81b98f7).
# Same options as the current m11101 (retrain #2, c8f38b7) otherwise.  New project; projects/m11101 is never touched.
set -e
P=/data/video2rig/projects/m11101_nogun
CODE=/data/video2rig/code
PY=/data/video2rig/.venv/bin/python
TS=$(date +%Y%m%d-%H%M%S)
LOG=/data/video2rig/backups/retrain-m11101_nogun-$TS.log
exec > >(tee -a $LOG) 2>&1
echo "[nogun] start $TS, code $(cat $CODE/DEPLOYED_COMMIT)"
if pgrep -f 'v2r.cli (gen|train|exam|animate|prepare)' >/dev/null; then echo "[nogun] BUSY, abort"; exit 1; fi
if [ -e $P ]; then echo "[nogun] $P already exists, abort"; exit 1; fi
cd $CODE
echo "[nogun] === prepare"
mkdir -p $P
$PY -m v2r.cli prepare /data/video2rig/inputs/11101_01.fbx $P --facing=-Y --up=+Z --visual-profile legacy \
    --procedural-material --fist-points --offaxis-points --drop-props > /tmp/m11101_nogun_prepare.log 2>&1
cp /tmp/m11101_nogun_prepare.log $P/prepare.log
grep -E '\[v2r\] (props dropped|bones=|prepare done)' $P/prepare.log
echo "[nogun] === gen (segment visibility)"
$PY -m v2r.cli gen $P --n 3000 --workers 10 --contact-poses --visibility segment > $P/gen.log 2>&1
tail -n 2 $P/gen.log
$PY - <<EOF
import json, glob, numpy as np
P = "$P"
rig = json.load(open(f"{P}/rig.json")); kn = [k["name"] for k in rig["keypoints"]]
meta = []
for f in sorted(glob.glob(f"{P}/train_data/meta_*.json")):
    meta += json.load(open(f))
vis = np.array([[m[v]["vis"] for v in ("front", "side")] for m in meta])
core = [i for i, k in enumerate(rig["keypoints"]) if k["side"] == "M"]
print("[nogun] labelled seen:", f"{vis.mean():.1%}", "midline", {kn[i]: round(float(vis[:, :, i].mean()), 2) for i in core})
EOF
echo "[nogun] === train"
$PY -m v2r.cli train $P --steps 9000 --hidden-empty --smear 0.3 > $P/train.log 2>&1
grep -E '== epoch|training done' $P/train.log | tail -n 2
echo "[nogun] === exam (smooth)"
$PY -m v2r.cli exam $P > $P/exam.log 2>&1
grep -E '\[v2r\] (check:|joint error|bone angle|twist error|worst|EXAM)' $P/exam.log
echo "[nogun] === exam (strike)"
$PY -m v2r.cli exam $P --style strike > $P/exam_strike.log 2>&1
grep -E '\[v2r\] (joint error|bone angle|impacts|EXAM)' $P/exam_strike.log
echo "[nogun] done $(date +%H:%M:%S)"
