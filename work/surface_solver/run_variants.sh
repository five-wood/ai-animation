# Stage 1 / 2 variants, queued after the masks and the baseline.  Results: ab_silhouette.json, ab_dense.json.
set -e
D=/data/video2rig/experiments/surface_solver
while pgrep -f "run_masks.sh|run_baseline.sh|make_exams.sh" >/dev/null; do sleep 30; done
CL=""
for P in m11101 m11101_s11 m11101_s23 m11101_s37 lizard lizard_s11 lizard_s23 lizard_s37; do
  for X in exam exam_strike; do CL="$CL,$P:$X"; done
done
CL=${CL#,}
cd $D
PYS=/data/video2rig/experiments/surface_trial/.venv/bin/python
PYO=/data/video2rig/experiments/surface_trial/.venv/bin/python   # Open3D for the perfect dense data
# stage 1: silhouette terms, SAM 2 masks (real input) and the sharp true silhouette (upper bound of this term)
V1='temporal:{"silhouette_strength":0.3}'
V1="$V1;temporal:{\"silhouette_strength\":1.0}"
V1="$V1;temporal:{\"silhouette_strength\":0.3,\"coverage_strength\":0.3}"
V1="$V1;temporal:{\"silhouette_strength\":1.0,\"coverage_strength\":1.0}"
V1="$V1;temporal:{\"silhouette_strength\":1.0,\"coverage_strength\":1.0,\"masks_kind\":\"gt\"}"
V1="$V1;legacy:{\"silhouette_strength\":1.0,\"coverage_strength\":1.0}"
$PYS scripts/ab_multi.py --code $D/code --variants "$V1" --clips "$CL" --real m11101 --out $D/ab_silhouette.json \
  2>&1 | grep -E "^\[ab\]|Error|Traceback"
echo STAGE1 DONE
# stage 2: perfect dense observations (exam only), each kind alone and together
V2='temporal:{"dense":{"uv":1.0}}'
V2="$V2;temporal:{\"dense\":{\"normal\":0.3}}"
V2="$V2;temporal:{\"dense\":{\"depth\":1.0}}"
V2="$V2;temporal:{\"dense\":{\"uv\":1.0,\"noise_px\":3.0}}"
$PYO scripts/ab_multi.py --code $D/code --variants "$V2" --clips "$CL" --out $D/ab_dense.json \
  2>&1 | grep -E "^\[ab\]|Error|Traceback"
echo STAGE2 DONE
