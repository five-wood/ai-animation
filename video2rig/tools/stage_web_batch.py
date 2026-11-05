"""Package only Web code and dry tests for isolated server-environment validation.
Never imports production server, launches work, or changes the running service.
"""
from pathlib import Path
import hashlib
import json
import tarfile

repo = Path(__file__).resolve().parents[2]
paths = ['video2rig/web/server.py', 'video2rig/web/project_store.py', 'video2rig/web/task_monitor.py',
         'video2rig/tools/test_project_store.py', 'video2rig/tools/test_task_monitor.py',
         'video2rig/tools/test_web_tasks.py', 'tools/test_run_binding.py',
         'video2rig/v2r/__init__.py', 'video2rig/v2r/viz.py']
paths += [f'video2rig/web/static/{name}' for name in ['index.html','app.css','app.js','run-page.js','results.js','task-ui.js']]
digest = hashlib.sha256(b''.join((repo/p).read_bytes() for p in paths)).hexdigest()[:12]
folder = repo / 'work/web-ui-v2'
folder.mkdir(parents=True, exist_ok=True)
archive = folder / f'validation-{digest}.tar.gz'
with tarfile.open(archive, 'w:gz') as tar:
    for path in paths:
        tar.add(repo / path, arcname=path)
print(json.dumps({'id': digest, 'archive':str(archive)}))
