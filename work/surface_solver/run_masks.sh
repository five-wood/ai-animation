# Masks for every A/B clip (after the extra exams exist).  surface_trial venv (SAM 2 + Open3D).
set -e
D=/data/video2rig/experiments/surface_solver
PY=/data/video2rig/experiments/surface_trial/.venv/bin/python
while pgrep -f "make_exams.sh" >/dev/null; do sleep 30; done
cd /data/video2rig/experiments/surface_trial/src/sam2
for P in m11101 lizard m11101_s11 m11101_s23 m11101_s37 lizard_s11 lizard_s23 lizard_s37; do
  for X in exam exam_strike; do
    [ -f $D/masks/${P}_${X}.npz ] && [ -f $D/masks/${P}_${X}_gt.npz ] && continue
    PYTHONPATH=$D/scripts $PY $D/scripts/make_masks.py $P $X --gt --sam 2>&1 | grep -E "^\[masks\]|Error|Traceback" || true
  done
done
PYTHONPATH=$D/scripts $PY $D/scripts/make_masks.py m11101 real --sam 2>&1 | grep -E "^\[masks\]|Error|Traceback" || true
echo MASKS DONE
