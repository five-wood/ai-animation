#!/bin/bash
# Real clip ladingwu.mp4 on m11101_tex with the control (keypoint) reader, temporal solver, deployed code.
# New output name; nothing existing is overwritten.
set -e
P=/data/video2rig/projects/m11101_tex
V=/data/video2rig/videos/ladingwu.mp4
N=ladingwu_temporal
if pgrep -f "python -m v2r\.cli (gen|train|exam|animate|prepare) " >/dev/null; then echo "BUSY"; exit 1; fi
[ -e $P/out/$N/motion.npz ] && { echo "$P/out/$N exists, abort"; exit 1; }
cd /data/video2rig/code
/data/video2rig/.venv/bin/python -m v2r.cli animate $P $V --name $N --solver temporal > /tmp/animate_$N.log 2>&1 || { tail -30 /tmp/animate_$N.log; exit 1; }
cp /tmp/animate_$N.log $P/out/$N/animate.log
grep -E "\[v2r\] (video|frame-0|check:|solved|done in|NEEDS)" $P/out/$N/animate.log
