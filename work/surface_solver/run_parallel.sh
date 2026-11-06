# Stage 1 / 2 variants in parallel workers (GPU is under-used by one solve), one JSON per worker.
set -e
D=/data/video2rig/experiments/surface_solver
CL=""
for P in m11101 m11101_s11 m11101_s23 m11101_s37 lizard lizard_s11 lizard_s23 lizard_s37; do
  for X in exam exam_strike; do CL="$CL,$P:$X"; done
done
CL=${CL#,}
cd $D
PY=/data/video2rig/experiments/surface_trial/.venv/bin/python
run() {  # name, real?, variants
  local name=$1 real=$2 vars=$3
  local R=""; [ "$real" = 1 ] && R="--real m11101"
  $PY -u scripts/ab_multi.py --code $D/code --variants "$vars" --clips "$CL" $R --out $D/ab_$name.json \
    > $D/logs/ab_$name.log 2>&1
  echo "$name done" >> $D/logs/parallel.log
}
run sil_a 1 'temporal:{"silhouette_strength":0.3};temporal:{"silhouette_strength":1.0}' &
run sil_b 1 'temporal:{"silhouette_strength":0.3,"coverage_strength":0.3};temporal:{"silhouette_strength":1.0,"coverage_strength":1.0}' &
run sil_c 1 'temporal:{"silhouette_strength":1.0,"coverage_strength":1.0,"masks_kind":"gt"};legacy:{"silhouette_strength":1.0,"coverage_strength":1.0}' &
run dense_a 0 'temporal:{"dense":{"uv":1.0}};temporal:{"dense":{"normal":0.3}}' &
run dense_b 0 'temporal:{"dense":{"depth":1.0}};temporal:{"dense":{"uv":1.0,"noise_px":3.0}}' &
wait
echo ALL PARALLEL DONE >> $D/logs/parallel.log
