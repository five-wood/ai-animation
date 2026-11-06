# Stage 0 baseline: legacy + temporal on every exam clip (4 seeds x smooth/strike x 2 creatures) + m11101 real.
set -e
D=/data/video2rig/experiments/surface_solver
while pgrep -f "make_exams.sh" >/dev/null; do sleep 30; done
CL=""
for P in m11101 m11101_s11 m11101_s23 m11101_s37 lizard lizard_s11 lizard_s23 lizard_s37; do
  for X in exam exam_strike; do CL="$CL,$P:$X"; done
done
cd $D
/data/video2rig/.venv/bin/python scripts/ab_multi.py --code $D/code --variants 'legacy;temporal' \
  --clips "${CL#,}" --real m11101 --out $D/ab_baseline.json 2>&1 | grep -E "^\[ab\]|Error|Traceback"
echo BASELINE DONE
