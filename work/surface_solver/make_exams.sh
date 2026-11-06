set -e
D=/data/video2rig/experiments/surface_solver
cd /data/video2rig/code
for P in m11101 lizard; do
  for S in 11 23 37; do
    for ST in smooth strike; do
      /data/video2rig/.venv/bin/python $D/scripts/make_exams.py $P $S $ST > $D/logs/exam_${P}_s${S}_${ST}.log 2>&1 \
        || { echo "FAILED $P $S $ST"; tail -20 $D/logs/exam_${P}_s${S}_${ST}.log; }
      grep -E "\[exams\]|check:" $D/logs/exam_${P}_s${S}_${ST}.log | tail -2
    done
  done
done
echo ALL EXAMS DONE
