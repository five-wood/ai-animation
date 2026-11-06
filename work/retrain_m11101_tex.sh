#!/bin/bash
# m11101_tex (textured, gun-free model): keypoint reader with the same settings as m11101 retrain #2 / m11101_nogun.
# prepare was done separately (reference picture).  This run: gen -> train -> exam (smooth + strike).
# It is also the control for the later dense-correspondence reader (same training pictures).
set -e
P=/data/video2rig/projects/m11101_tex
CODE=/data/video2rig/code
PY=/data/video2rig/.venv/bin/python
TS=$(date +%Y%m%d-%H%M%S)
LOG=/data/video2rig/backups/retrain-m11101_tex-$TS.log
exec > >(tee -a $LOG) 2>&1
echo "[tex] start $TS, code $(cat $CODE/DEPLOYED_COMMIT)"
if pgrep -f "python -m v2r\.cli (gen|train|exam|animate|prepare) " >/dev/null; then echo "[tex] BUSY, abort"; exit 1; fi
[ -f $P/rig.json ] || { echo "[tex] no rig.json, run prepare first"; exit 1; }
[ -e $P/train_data ] && { echo "[tex] train_data exists, abort"; exit 1; }
cd $CODE
echo "[tex] === gen (segment visibility, contact poses)"
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
print("[tex] labelled seen:", f"{vis.mean():.1%}", "midline", {kn[i]: round(float(vis[:, :, i].mean()), 2) for i in core})
EOF
echo "[tex] === train"
$PY -m v2r.cli train $P --steps 9000 --hidden-empty --smear 0.3 > $P/train.log 2>&1
grep -E '== epoch|training done' $P/train.log | tail -n 2
echo "[tex] === exam (smooth)"
$PY -m v2r.cli exam $P > $P/exam.log 2>&1
grep -E '\[v2r\] (check:|joint error|bone angle|twist error|worst|EXAM)' $P/exam.log
echo "[tex] === exam (strike)"
$PY -m v2r.cli exam $P --style strike > $P/exam_strike.log 2>&1
grep -E '\[v2r\] (joint error|bone angle|impacts|EXAM)' $P/exam_strike.log
echo "[tex] done $(date +%H:%M:%S)"
