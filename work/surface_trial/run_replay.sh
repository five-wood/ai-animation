# m11101's two exam motions replayed on m11101_nogun in a scratch project (projects are only read).
set -e
S=/data/video2rig/experiments/nogun_same_motion
SRC=/data/video2rig/projects/m11101
NG=/data/video2rig/projects/m11101_nogun
BL=/opt/unimate/blender-4.0.2-linux-x64/blender
PY=/data/video2rig/.venv/bin/python
if pgrep -f 'v2r.cli (gen|train|exam|animate|prepare)' >/dev/null; then echo "BUSY"; exit 1; fi
rm -rf $S/proj && mkdir -p $S/proj
for f in rig.json studio.json prepare_opts.json rest_kp2d.json; do cp $NG/$f $S/proj/; done
ln -s $NG/model $S/proj/model
for X in exam exam_strike; do
  mkdir -p $S/proj/$X/frames
  $BL -b --factory-startup --python $S/replay_exam.py -- $S/proj $S/proj/$X/frames \
      "{\"replay\": \"$SRC/$X/frames/motion_gt.json\", \"samples\": 16}" > $S/render_$X.log 2>&1 \
      || { tail -30 $S/render_$X.log; exit 1; }
  grep -E "\[replay\] done|props dropped" $S/render_$X.log
done
cd /data/video2rig/code
for style in smooth strike; do
  $PY -c "from v2r.exam import exam; exam('$S/proj', render=False, style='$style')" > $S/exam_$style.log 2>&1 || { tail -30 $S/exam_$style.log; exit 1; }
  grep -E '\[v2r\] (check:|joint error|bone angle|twist error|worst|impacts|EXAM)' $S/exam_$style.log
done
echo REPLAY DONE
