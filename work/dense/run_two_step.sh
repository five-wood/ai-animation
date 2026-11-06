#!/bin/bash
# Two-step dense reader: the control keypoint reader (m11101_tex) frozen + a dense head trained on top.
# New project m11101_tex_2step; m11101_tex and m11101_tex_dense stay as they are (and are backed up) for comparison.
set -e
SRC=/data/video2rig/projects/m11101_tex
OLDD=/data/video2rig/projects/m11101_tex_dense
P=/data/video2rig/projects/m11101_tex_2step
C=/data/video2rig/experiments/surface_solver/code
D=/data/video2rig/experiments/surface_solver
PY=/data/video2rig/.venv/bin/python
PYO=/data/video2rig/experiments/surface_trial/.venv/bin/python
TS=$(date +%Y%m%d-%H%M%S)
LOG=/data/video2rig/backups/retrain-m11101_tex_2step-$TS.log
exec > >(tee -a $LOG) 2>&1
echo "[2step] start $TS"
if pgrep -f "python -m v2r\.cli (gen|train|exam|animate|prepare) " >/dev/null; then echo "[2step] BUSY, abort"; exit 1; fi
[ -e $P ] && { echo "[2step] $P exists, abort"; exit 1; }
# keep the two current readers for later comparisons
B=/data/video2rig/backups/readers-m11101_tex-$TS
mkdir -p $B/m11101_tex $B/m11101_tex_dense
cp -a $SRC/model/best.pt $SRC/model/train_log.jsonl $B/m11101_tex/
cp -a $OLDD/model/best.pt $OLDD/model/train_log.jsonl $B/m11101_tex_dense/
echo "[2step] readers backed up -> $B"
mkdir -p $P
cp $SRC/rig.json $SRC/studio.json $SRC/prepare_opts.json $SRC/rest_kp2d.json $SRC/surface.npz $SRC/reference_two_views.png $P/
cp -al $SRC/train_data $P/train_data
cp -r $SRC/exam $SRC/exam_strike $P/
rm -f $P/exam/exam_report.json $P/exam_strike/exam_report.json
cd $C
echo "[2step] === train dense head only (frozen m11101_tex reader)"
$PY -m v2r.cli train $P --steps 3000 --smear 0.3 --dense-from $SRC/model/best.pt > $P/train.log 2>&1
grep -E 'two-step|== epoch|training done' $P/train.log | sed -n '1p;$p' ; grep -E '== epoch' $P/train.log | tail -n 1
echo "[2step] === keypoints unchanged? (same exam reading as the control reader)"
for style in smooth strike; do
  $PY -c "from v2r.exam import exam; exam('$P', render=False, style='$style')" > $P/exam_$style.log 2>&1
  grep -E '\[v2r\] (joint error|bone angle|EXAM)' $P/exam_$style.log
done
$PY - <<EOF
import numpy as np
for tag in ("exam", "exam_strike"):
    a = np.load("$SRC/out/%s/reading.npz" % tag); b = np.load("$P/out/%s/reading.npz" % tag)
    print("[2step]", tag, "keypoint reading identical to m11101_tex:", np.array_equal(a["uv_reader"], b["uv_reader"]),
          "max diff px", float(np.abs(a["uv_reader"] - b["uv_reader"]).max()))
EOF
echo "[2step] === dense evaluation (same as m11101_tex_dense)"
cd $D
$PYO -u scripts/eval_dense_reader.py $P $P:exam $P:exam_strike --out $D/dense_eval_m11101_tex_2step.json 2>&1 | grep -E "^\[dense\]|Error|Traceback"
echo "[2step] done $(date +%H:%M:%S)"
