"""Read-only performance/discovery check; does not import server or Jobs."""
from pathlib import Path
import json
import sys
import time
sys.path.insert(0, '/tmp/v2r-web-validation-7565fe952117/video2rig')
from web.project_store import ProjectStore
from web.task_monitor import TaskMonitor
root=Path('/data/video2rig')
store=ProjectStore(root/'projects')
for label in ['cold','warm','warm-again']:
    before=time.perf_counter();rows=store.list(lambda n: not n.startswith('.'));elapsed=time.perf_counter()-before
    print(json.dumps({'label':label,'seconds':elapsed,'projects':len(rows),'metrics':store.metrics}))
jobs=[]
for file in (root/'jobs').glob('*.json'):
    try: jobs.append(json.loads(file.read_text()))
    except (OSError,ValueError):pass
monitor=TaskMonitor(root/'projects',root/'jobs')
for label in ['cold','warm']:
    before=time.perf_counter();snapshot=monitor.snapshot(jobs)
    print(json.dumps({'label':'tasks-'+label,'seconds':time.perf_counter()-before,'monitor':snapshot['monitor'],'tasks':len(snapshot['tasks']),'active':[{k:t.get(k) for k in ['id','project','cmd','status','source','phase']} for t in snapshot['tasks'] if t['status'] in ['running','queued','unknown']]}))
