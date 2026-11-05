set -e
E=/data/video2rig/experiments/surface_trial
BL=/opt/unimate/blender-4.0.2-linux-x64/blender
for P in m11101 lizard; do
  for X in exam exam_strike; do
    $BL -b --factory-startup --python $E/scripts/export_trial_meshes.py -- /data/video2rig/projects/$P $X $E/data/${P}_${X}_mesh.npz 3000 \
      > $E/out/export_${P}_${X}.log 2>&1
    echo "== $P $X"
    grep -q "EXPORT DONE" $E/out/export_${P}_${X}.log || { grep -A8 Traceback $E/out/export_${P}_${X}.log; exit 1; }
    grep "^\[trial\]" $E/out/export_${P}_${X}.log | tail -5
  done
done
