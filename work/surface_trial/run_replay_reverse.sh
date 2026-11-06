# Reverse check: m11101_nogun's own exam motions replayed on m11101 (with guns, old reader), scratch project.
set -e
S=/data/video2rig/experiments/nogun_same_motion
SRC=/data/video2rig/projects/m11101_nogun
OLD=/data/video2rig/projects/m11101
BL=/opt/unimate/blender-4.0.2-linux-x64/blender
PY=/data/video2rig/.venv/bin/python
if pgrep -f 'v2r.cli (gen|train|exam|animate|prepare)' >/dev/null; then echo "BUSY"; exit 1; fi
rm -rf $S/proj_guns && mkdir -p $S/proj_guns
for f in rig.json studio.json prepare_opts.json rest_kp2d.json; do cp $OLD/$f $S/proj_guns/; done
ln -s $OLD/model $S/proj_guns/model
for X in exam exam_strike; do
  mkdir -p $S/proj_guns/$X/frames
  $BL -b --factory-startup --python $S/replay_exam.py -- $S/proj_guns $S/proj_guns/$X/frames \
      "{\"replay\": \"$SRC/$X/frames/motion_gt.json\", \"samples\": 16}" > $S/render_guns_$X.log 2>&1 \
      || { tail -30 $S/render_guns_$X.log; exit 1; }
  grep -E "\[replay\] done" $S/render_guns_$X.log
done
cd /data/video2rig/code
for style in smooth strike; do
  $PY -c "from v2r.exam import exam; exam('$S/proj_guns', render=False, style='$style')" > $S/exam_guns_$style.log 2>&1 || { tail -30 $S/exam_guns_$style.log; exit 1; }
  grep -E '\[v2r\] (joint error|bone angle|worst bones|impacts|EXAM)' $S/exam_guns_$style.log
done
echo REVERSE DONE
