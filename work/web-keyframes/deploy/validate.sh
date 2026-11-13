#!/bin/bash
# Isolated validation in /tmp with the production Python env. Does not touch /data/video2rig/code or the service.
set -eu
ID="$1"
SRC="/tmp/v2r-web-validation-$ID"
rm -rf "$SRC"; mkdir -p "$SRC"
tar -xzf "/tmp/validation-$ID.tar.gz" -C "$SRC"
touch "$SRC/video2rig/web/__init__.py"
cd "$SRC"
PY=/data/video2rig/.venv/bin/python
echo "== py_compile"; $PY -m py_compile video2rig/web/server.py video2rig/web/keyframes_store.py && echo ok
echo "== test_web_keyframes"; $PY video2rig/tools/test_web_keyframes.py 2>&1 | tail -4
echo "== test_web_tasks"; $PY video2rig/tools/test_web_tasks.py 2>&1 | tail -3
echo "== test_project_store"; $PY video2rig/tools/test_project_store.py 2>&1 | tail -2
echo "== test_task_monitor"; $PY video2rig/tools/test_task_monitor.py 2>&1 | tail -2
echo "== real sample through keyframes_store (read-only view)"
$PY - "$SRC" <<'EOF'
import sys, json, tempfile, shutil, os
sys.path.insert(0, sys.argv[1] + '/video2rig')
from web import keyframes_store as ks
src = '/data/video2rig/projects/m11101_tex/out/ladingwu_temporal'
with tempfile.TemporaryDirectory() as d:
    shutil.copy(src + '/summary.json', d)
    shutil.copy('/tmp/keyframes_auto.json', d)
    s = json.load(open(d + '/summary.json'))
    v = ks.view(d, s, None)
    print('fps', v['fps'], 'frames', v['frames'], 'video_name', v['video_name'], 'auto keys', len(v['auto']['keyframes']), 'ladder', v['auto']['breakdown_ladder']['suggested_count'], 'errors', v['errors'])
EOF
echo "VALIDATION DONE $SRC"
