#!/bin/bash
# Read-only preflight for the keyframe web deploy. Changes nothing.
set -u
echo "== host"; hostname; uname -m; date -Is; whoami
echo "== disk"; df -h /data | tail -1
echo "== service"; systemctl is-active video2rig-web; systemctl show video2rig-web -p MainPID -p ActiveEnterTimestamp -p ExecStart -p WorkingDirectory -p User | sed 's/^/  /'
echo "== web files sha256"
cd /data/video2rig/code/web && sha256sum server.py project_store.py task_monitor.py static/index.html 2>&1
ls -la /data/video2rig/code/web/ | head -30
echo "== static entry refs"; grep -o '/static/releases/[^"]*' /data/video2rig/code/web/static/index.html | sort -u
echo "== releases"; ls -la /data/video2rig/code/web/static/releases/
for r in /data/video2rig/code/web/static/releases/ui-v1-*; do echo "-- $r"; (cd "$r" && sha256sum *.js *.css index.html 2>/dev/null); done
echo "== keyframes_store present?"; ls -la /data/video2rig/code/web/keyframes_store.py 2>&1
echo "== web package init"; ls -la /data/video2rig/code/web/__init__.py 2>&1
echo "== backups dir"; ls -la /data/video2rig/web-release-backups/ 2>&1
echo "== jobs (web queue)"; curl -s -m 10 http://127.0.0.1:8600/api/tasks | /data/video2rig/.venv/bin/python -c "import json,sys;d=json.load(sys.stdin);t=d.get('tasks',[]);print('monitor',d.get('monitor'));print('active',[(x.get('id'),x.get('status'),x.get('source'),x.get('project')) for x in t if x.get('status') in ('queued','running','unknown')])"
echo "== gpu procs"; nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>&1 | head
echo "== v2r python procs"; ps -eo pid,etime,cmd | grep -E 'v2r|python' | grep -v -E 'grep|uvicorn' | head -20
echo "== project ladingwu outputs"; ls -d /data/video2rig/projects/*/out/*ladingwu* 2>/dev/null
for d in /data/video2rig/projects/m11101_tex/out/ladingwu_temporal; do echo "-- $d"; ls -la "$d" | head -40; done
echo "== source video"; ls -la /data/video2rig/videos/ladingwu.mp4 2>&1
echo "== max2026 tools on server?"; ls /data/video2rig/code/tools/max2026 2>&1 | head; ls -d /data/video2rig/code/video2rig 2>&1
echo "== code root layout"; ls /data/video2rig/code | head -40
echo "== python env numpy"; /data/video2rig/.venv/bin/python -c "import numpy,sys;print(sys.version.split()[0],numpy.__version__)"
