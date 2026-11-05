set -e
# scripts live in $E/scripts (not $E/src, which holds the sam2 repository and would shadow the package)
E=/data/video2rig/experiments/surface_trial
cd $E/scripts
bash run_export.sh
echo "######## OPEN3D"
$E/.venv/bin/python trial_open3d.py 2>&1 | tee $E/out/open3d.log | grep -vE "^\[Open3D"
echo "######## SAM2"
$E/.venv/bin/python trial_sam2.py 2>&1 | tee $E/out/sam2.log | grep -vE "frame loading|propagate in video|it/s\]"
