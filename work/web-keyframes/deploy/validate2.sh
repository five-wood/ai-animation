#!/bin/bash
# Isolated validation in /tmp with the production Python env. Does not touch /data/video2rig/code or the service.
set -eu
ID="$1"
SRC="/tmp/v2r-web-validation-$ID"
rm -rf "$SRC"; mkdir -p "$SRC"
tar -xzf "/tmp/validation-$ID.tar.gz" -C "$SRC"
touch "$SRC/video2rig/web/__init__.py"
mkdir -p "$SRC/tools-stage"; tar -xzf /tmp/kf-tools.tar.gz -C "$SRC/tools-stage"
PY=/data/video2rig/.venv/bin/python
cd "$SRC"
echo "== py_compile"; $PY -m py_compile video2rig/web/server.py video2rig/web/keyframes_store.py video2rig/web/task_monitor.py tools-stage/max2026/*.py && echo ok
echo "== tests"
for t in test_web_keyframes test_web_tasks test_project_store test_task_monitor; do printf '%s: ' $t; $PY video2rig/tools/$t.py 2>&1 | tail -1; done
echo "== run_keyframes on COPIES of real results (production dirs untouched)"
W=$(mktemp -d /tmp/v2r-kf-run-XXXX)
P=/data/video2rig/projects
run() {  # project result
  mkdir -p "$W/$1/out/$2"; cp "$P/$1/rig.json" "$W/$1/"
  for f in motion.npz reading.npz summary.json; do [ -f "$P/$1/out/$2/$f" ] && cp "$P/$1/out/$2/$f" "$W/$1/out/$2/"; done
  /usr/bin/time -f "   %es %MKB" $PY tools-stage/max2026/run_keyframes.py "$W/$1" "$2" 2>&1 | grep -E '^\[v2r\]|s .*KB' | grep -vE 'stage [12]/3' | tail -4
}
run m11101_tex ladingwu_temporal
cmp <($PY -c "import json;d=json.load(open('$W/m11101_tex/out/ladingwu_temporal/keyframes_auto.json'));print([k['frame'] for k in d['keyframes']],[b['frame'] for b in d['breakdown_ladder']['breakdowns']])") \
    <($PY -c "import json;d=json.load(open('$P/m11101_tex/out/ladingwu_temporal/keyframes_auto.json'));print([k['frame'] for k in d['keyframes']],[b['frame'] for b in d['breakdown_ladder']['breakdowns']])") \
  && echo "   same frames + ladder as the deployed candidate file"
run m11101_nogun 112_temporal
run m11101 333
run m11101_tex ladingwu_dense3
run lizard "$(ls $P/lizard/out | head -1)"
rm -rf "$W"
echo "VALIDATION DONE $SRC"
