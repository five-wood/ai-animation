#!/bin/bash
# Dense-correspondence reader for m11101_tex, queued after the keypoint reader (control) has finished.
#  1. copy the project (rig, studio, train_data, exams) into m11101_tex_dense (train_data hard-linked)
#  2. train with the same settings + --dense 1.0 (scratch code = deployed code + local changes, not deployed)
#  3. exams with the dense reader's keypoints (smooth + strike), same exam motions as m11101_tex
#  4. dense evaluation: read dense maps on the exam clips, accuracy vs ground truth, solve with / without
set -e
SRC=/data/video2rig/projects/m11101_tex
P=/data/video2rig/projects/m11101_tex_dense
C=/data/video2rig/experiments/surface_solver/code
D=/data/video2rig/experiments/surface_solver
PY=/data/video2rig/.venv/bin/python
PYO=/data/video2rig/experiments/surface_trial/.venv/bin/python
TS=$(date +%Y%m%d-%H%M%S)
LOG=/data/video2rig/backups/retrain-m11101_tex_dense-$TS.log
exec > >(tee -a $LOG) 2>&1
echo "[dense] queued $TS"
while ! grep -q "\[tex\] done" $(ls -t /data/video2rig/backups/retrain-m11101_tex-2*.log | head -1) 2>/dev/null; do
  pgrep -f "retrain-m11101_tex.sh" >/dev/null || { echo "[dense] keypoint run is not running and not done, abort"; exit 1; }
  sleep 60
done
echo "[dense] start $(date +%H:%M:%S)"
[ -e $P ] && { echo "[dense] $P exists, abort"; exit 1; }
mkdir -p $P
cp $SRC/rig.json $SRC/studio.json $SRC/prepare_opts.json $SRC/rest_kp2d.json $SRC/surface.npz $SRC/reference_two_views.png $P/
cp -al $SRC/train_data $P/train_data
cp -r $SRC/exam $SRC/exam_strike $P/      # same exam motions and videos
rm -f $P/exam/exam_report.json $P/exam_strike/exam_report.json
cd $C
echo "[dense] === train (dense 1.0)"
$PY -m v2r.cli train $P --steps 9000 --hidden-empty --smear 0.3 --dense 1.0 > $P/train.log 2>&1
grep -E '== epoch|training done' $P/train.log | tail -n 2
for style in smooth strike; do
  echo "[dense] === exam ($style), video reused"
  $PY -c "from v2r.exam import exam; exam('$P', render=False, style='$style')" > $P/exam_$style.log 2>&1
  grep -E '\[v2r\] (joint error|bone angle|twist error|worst bones|impacts|EXAM)' $P/exam_$style.log
done
echo "[dense] === dense evaluation"
cd $D
$PYO -u scripts/eval_dense_reader.py $P $P:exam $P:exam_strike --out $D/dense_eval_m11101_tex.json 2>&1 | grep -E "^\[dense\]|Error|Traceback"
echo "[dense] done $(date +%H:%M:%S)"
