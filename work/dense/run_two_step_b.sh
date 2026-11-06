#!/bin/bash
# Two-step, second try: frozen control reader (backbone + keypoint decoder + heads) and a dense part with its OWN
# decoder (projection + 2 upsampling stages, initialised from the keypoint decoder) + dense head.
# m11101_tex_2step (first try: shared decoder, head only) is kept as m11101_tex_2step_headonly.
set -e
SRC=/data/video2rig/projects/m11101_tex
P=/data/video2rig/projects/m11101_tex_2step
C=/data/video2rig/experiments/surface_solver/code
D=/data/video2rig/experiments/surface_solver
PY=/data/video2rig/.venv/bin/python
PYO=/data/video2rig/experiments/surface_trial/.venv/bin/python
TS=$(date +%Y%m%d-%H%M%S)
LOG=/data/video2rig/backups/retrain-m11101_tex_2step_b-$TS.log
exec > >(tee -a $LOG) 2>&1
echo "[2step-b] start $TS"
if pgrep -f "python -m v2r\.cli (gen|train|exam|animate|prepare) " >/dev/null; then echo "[2step-b] BUSY, abort"; exit 1; fi
if [ -d $P ] && [ ! -e ${P}_headonly ]; then mv $P ${P}_headonly; echo "[2step-b] first try kept as ${P}_headonly"; fi
[ -e $P ] && { echo "[2step-b] $P exists, abort"; exit 1; }
mkdir -p $P
cp $SRC/rig.json $SRC/studio.json $SRC/prepare_opts.json $SRC/rest_kp2d.json $SRC/surface.npz $SRC/reference_two_views.png $P/
cp -al $SRC/train_data $P/train_data
cp -r $SRC/exam $SRC/exam_strike $P/
rm -f $P/exam/exam_report.json $P/exam_strike/exam_report.json
cd $C
echo "[2step-b] === train dense decoder + head (frozen m11101_tex reader)"
$PY -m v2r.cli train $P --steps 6000 --smear 0.3 --dense-from $SRC/model/best.pt > $P/train.log 2>&1
grep -E 'two-step' $P/train.log; grep -E '== epoch|training done' $P/train.log | tail -n 2
echo "[2step-b] === keypoints unchanged?"
for style in smooth strike; do
  $PY -c "from v2r.exam import exam; exam('$P', render=False, style='$style')" > $P/exam_$style.log 2>&1
  grep -E '\[v2r\] (joint error|bone angle|EXAM)' $P/exam_$style.log
done
$PY - <<EOF
import numpy as np
for tag in ("exam", "exam_strike"):
    a = np.load("$SRC/out/%s/reading.npz" % tag); b = np.load("$P/out/%s/reading.npz" % tag)
    print("[2step-b]", tag, "keypoint reading identical to m11101_tex:", np.array_equal(a["uv_reader"], b["uv_reader"]))
EOF
echo "[2step-b] === dense evaluation"
cd $D
$PYO -u scripts/eval_dense_reader.py $P $P:exam $P:exam_strike --out $D/dense_eval_m11101_tex_2step.json 2>&1 | grep -E "^\[dense\]|Error|Traceback"
echo "[2step-b] done $(date +%H:%M:%S)"
