"""Verified Web-only deploy, allowed only when managed queue + CLI monitor are idle.

Run on the existing server with a staged source root and immutable static release.
Does not sync v2r algorithms, project data or service configuration. The caller must
have authorized a short Web service restart. Backups are operational rollback data
outside the publicly served static root, not an alternate legacy Web entry.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

parser=argparse.ArgumentParser()
parser.add_argument('--source',required=True)
parser.add_argument('--release',required=True)
parser.add_argument('--expected-server-sha256',required=True)
parser.add_argument('--expected-entry-sha256',required=True)
parser.add_argument('--apply',action='store_true')
args=parser.parse_args()
root=Path('/data/video2rig').resolve(strict=True)
code=(root/'code').resolve(strict=True)
source=Path(args.source).resolve(strict=True)
assert str(source).startswith('/tmp/v2r-web-validation-')
assert args.release.startswith('ui-v1-') and len(args.release)==18
static=code/'web/static'
release=(static/'releases'/args.release).resolve(strict=True)
assert release.parent==static/'releases'
manifest=json.loads((release/'manifest.json').read_text())
STATIC_NAMES={'index.html','app.css','app.js','run-page.js','results.js','task-ui.js','keyframes.js','keyframes-core.js'}
for name,expected in manifest['files'].items():
    assert name in STATIC_NAMES
    assert hashlib.sha256((release/name).read_bytes()).hexdigest()==expected
entry=static/'index.html'
assert hashlib.sha256(entry.read_bytes()).hexdigest()==args.expected_entry_sha256
assert hashlib.sha256((code/'web/server.py').read_bytes()).hexdigest()==args.expected_server_sha256
sys.path.insert(0,str(source/'video2rig'))
from web.task_monitor import TaskMonitor

def idle_check():
    with urllib.request.urlopen('http://127.0.0.1:8600/api/jobs',timeout=20) as r:
        jobs=json.load(r)
    assert not any(j['status'] in ('queued','running') for j in jobs),'Web tasks exist; abort without restart'
    monitor=TaskMonitor(root/'projects',root/'jobs')
    snapshot=monitor.snapshot(jobs)
    assert snapshot['monitor']['available'],snapshot['monitor']['warning']
    assert not any(t['status'] in ('running','queued','unknown') for t in snapshot['tasks']),'CLI tasks exist; abort without restart'
    print('Preflight idle: no managed/observed CLI tasks')

idle_check()
if not args.apply:
    print('Dry run passed; no production files changed')
    raise SystemExit(0)
# Restrict touched files to these Web modules; no algorithms, no config/data changes.
names=['server.py','project_store.py','task_monitor.py','keyframes_store.py']
backup=root/'web-release-backups'/args.release
assert not backup.exists(),'Backup already exists; inspect before retry'
backup.mkdir(parents=True)
existed={}
for name in names:
    target=code/'web'/name
    existed[name]=target.exists()
    if target.exists():shutil.copy2(target,backup/name)
shutil.copy2(entry,backup/'index.html')
(backup/'record.json').write_text(json.dumps({'release':args.release,'existing':existed,'source':str(source),'before_entry_sha256':args.expected_entry_sha256},indent=2))
# Recheck immediately before service restart; abort rather than cancel work.
idle_check()
try:
    for name in names:
        target=code/'web'/name
        temp=target.with_suffix('.next.tmp')
        assert target.parent==code/'web' and temp.parent==code/'web'
        temp.write_bytes((source/'video2rig/web'/name).read_bytes())
        os.replace(temp,target)
    temp=static/'index.next.tmp'
    temp.write_bytes((release/'index.html').read_bytes())
    os.replace(temp,entry)
    subprocess.run(['systemctl','restart','video2rig-web'],check=True)
    ready=False
    for _ in range(40):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8600/api/capabilities',timeout=3) as r:
                cap=json.load(r)
            ready=cap.get('version')==2 and cap.get('unified_tasks') is True
            if ready:break
        except Exception:pass
        time.sleep(.5)
    assert ready,'New backend readiness failed'
    with urllib.request.urlopen('http://127.0.0.1:8600/api/tasks',timeout=10) as r:
        snapshot=json.load(r)
    assert 'tasks' in snapshot and snapshot['monitor']['available']
    with urllib.request.urlopen('http://127.0.0.1:8600/openapi.json',timeout=10) as r:
        paths=json.load(r)['paths']
    assert '/api/projects/{p}/out/{name}/keyframes' in paths and '/api/projects/{p}/out/{name}/keyframes/artist' in paths,'keyframe routes missing'
    subprocess.run(['systemctl','is-active','--quiet','video2rig-web'],check=True)
    print('DEPLOYED',args.release,'Web-only backend + static entry')
    print('Operational backup (not publicly served):',backup)
except Exception:
    # Restore code/entry only; task and model data are never restored or deleted.
    for name in names:
        target=code/'web'/name
        if existed[name]:shutil.copy2(backup/name,target)
        elif target.exists():
            assert target.parent==code/'web'
            target.unlink()
    shutil.copy2(backup/'index.html',entry)
    subprocess.run(['systemctl','restart','video2rig-web'],check=True)
    print('Web files rolled back after deployment failure',file=sys.stderr)
    raise
