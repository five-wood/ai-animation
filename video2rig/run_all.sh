#!/bin/bash
# One-shot per-monster setup on the server:
#   bash run_all.sh <model.fbx|glb> <project-name> [extra prepare args...]
# prepare -> gen 3000 poses -> train reader -> exam.  Logs in projects/<name>/*.log
set -e
MODEL=$1; NAME=$2; shift 2
cd /data/video2rig/code
PY=../.venv/bin/python
P=../projects/$NAME
mkdir -p $P
if [ ! -f $P/rig.json ]; then
  $PY -m v2r.cli prepare "$MODEL" $P "$@" 2>&1 | tee $P/prepare.log | grep "\[v2r\]"
fi
$PY -m v2r.cli gen $P --n ${V2R_N:-3000} --workers ${V2R_WORKERS:-10} > $P/gen.log 2>&1
$PY -m v2r.cli train $P --epochs ${V2R_EPOCHS:-20} > $P/train.log 2>&1
$PY -m v2r.cli exam $P > $P/exam.log 2>&1
grep -E "EXAM|error" $P/exam.log
