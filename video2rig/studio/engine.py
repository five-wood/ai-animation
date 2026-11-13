"""Job engines.

V2REngine submits work to the internal dashboard (http://127.0.0.1:8600, POST /api/jobs) so the studio
shares its single GPU queue, and reads job records/logs from the shared jobs directory.
FakeEngine simulates the same contract for local development (no GPU / Blender).

Interface:
  submit(project, cmd, args) -> job id        raises Busy (project busy, retry later) or Rejected(message)
  status(job_id) -> {status, rc, error, log}  status: queued|running|done|failed|needs_review|cancelled|interrupted|missing
  cancel(job_id)
"""
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import config


class Busy(Exception):
    pass


class Rejected(Exception):
    pass


def tail_text(path, limit=16000):
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode('utf-8', 'replace')
    except OSError:
        return ''


class V2REngine:
    def __init__(self, api=config.V2R_API, jobs_dir=config.JOBS_DIR):
        self.api = api.rstrip('/')
        self.jobs_dir = jobs_dir

    def _post(self, path, body):
        req = urllib.request.Request(self.api + path, data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode('utf-8')).get('detail')
            except Exception:  # noqa: BLE001
                detail = str(e)
            if e.code == 409 and isinstance(detail, str) and '任务' in detail:
                raise Busy(detail)
            raise Rejected(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))
        except (urllib.error.URLError, OSError) as e:
            raise Busy(f'动画服务暂时无法连接：{e}')

    def submit(self, project, cmd, args):
        job = self._post('/api/jobs', {'project': project, 'cmd': cmd, 'args': args})
        return job['id']

    def status(self, job_id):
        path = os.path.join(self.jobs_dir, os.path.basename(job_id) + '.json')
        try:
            with open(path, encoding='utf-8') as f:
                j = json.load(f)
        except (OSError, ValueError):
            return {'status': 'missing', 'rc': None, 'error': '', 'log': ''}
        return {'status': j.get('status'), 'rc': j.get('rc'), 'error': j.get('error') or '',
                'log': tail_text(j.get('log') or '')}

    def cancel(self, job_id):
        try:
            self._post(f'/api/jobs/{job_id}/cancel', {})
        except (Busy, Rejected):
            pass


class FakeEngine:
    """Copies fixture outputs after a few simulated seconds. Same project may run one job at a time."""

    def __init__(self, fixture=config.FAKE_FIXTURE, speed=config.FAKE_SPEED):
        self.fixture = fixture
        self.speed = max(speed, 0.01)
        self.jobs = {}
        self.lock = threading.Lock()
        self.dir = os.path.join(config.DATA, 'fake_jobs')
        os.makedirs(self.dir, exist_ok=True)

    def submit(self, project, cmd, args):
        with self.lock:
            if any(j['project'] == project and j['status'] in ('queued', 'running') for j in self.jobs.values()):
                raise Busy('当前项目已有运行或排队任务')
            jid = time.strftime('%Y%m%d-%H%M%S') + f'-{cmd}-{project}-' + uuid.uuid4().hex[:8]
            j = {'id': jid, 'project': project, 'cmd': cmd, 'args': args, 'status': 'queued', 'rc': None,
                 'error': '', 'log': os.path.join(self.dir, jid + '.log'), 'cancel': False}
            self.jobs[jid] = j
        threading.Thread(target=self._run, args=(j,), daemon=True).start()
        return jid

    def status(self, job_id):
        j = self.jobs.get(job_id)
        if not j:
            return {'status': 'missing', 'rc': None, 'error': '', 'log': ''}
        return {'status': j['status'], 'rc': j['rc'], 'error': j['error'], 'log': tail_text(j['log'])}

    def cancel(self, job_id):
        j = self.jobs.get(job_id)
        if j:
            j['cancel'] = True

    # ------------------------------------------------------------------ simulation
    def _say(self, j, line, wait):
        with open(j['log'], 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        end = time.time() + wait / self.speed
        while time.time() < end:
            if j['cancel']:
                raise InterruptedError
            time.sleep(0.05)

    def _run(self, j):
        time.sleep(0.5 / self.speed)
        j['status'] = 'running'
        try:
            getattr(self, '_' + j['cmd'])(j)
            if j['status'] == 'running':
                j['status'], j['rc'] = 'done', 0
        except InterruptedError:
            j['status'], j['rc'] = 'cancelled', -15
        except Exception as e:  # noqa: BLE001
            j['status'], j['rc'], j['error'] = 'failed', 1, str(e)
            self._say(j, f'Traceback: {e}', 0)

    def _animate(self, j):
        a = j['args']
        name = a['name']
        proj = os.path.join(config.PROJ, j['project'])
        out = os.path.join(proj, 'out', name)
        self._say(j, f"[v2r] video {a['video']}: 193 frames @ 24.00 fps, 1280x720, split at x=640", 1.5)
        self._say(j, '[v2r] joints read in 7.7s', 1.5)
        # Dev convention: an uploaded file whose original name contains "bad" fails the two-view check.
        import sqlite3
        with sqlite3.connect(config.DB_PATH) as c:
            row = c.execute('SELECT orig_name FROM videos WHERE file = ?', (a['video'],)).fetchone()
        bad = bool(row) and 'bad' in row[0].lower() and not a.get('force')
        if bad:
            os.makedirs(out, exist_ok=True)
            self._say(j, '[v2r] check: two-view consistency passed = False', 0.5)
            with open(os.path.join(out, 'summary.json'), 'w') as f:
                json.dump({'frames': 193, 'fps': 24.0, 'status': 'needs_review', 'source_video': a['video']}, f)
            self._say(j, '[v2r] NEEDS_REVIEW: two-view check failed', 0)
            j['status'], j['rc'] = 'needs_review', 3
            return
        self._say(j, '[v2r] check: two-view consistency passed = True', 0.5)
        self._say(j, '[v2r] solve stage 0 it 0 loss 0.45', 2)
        self._say(j, '[v2r] solved in 53.4s; reprojection error median 4.15px p90 42.65px', 0.5)
        self._say(j, '[v2r] exported FBX/GLB/BVH in 39.7s', 1.5)
        src = os.path.join(self.fixture, 'out', 'ladingwu_temporal')
        os.makedirs(out, exist_ok=True)
        for fn in os.listdir(src):
            if fn.startswith('keyframes'):
                continue
            dst = fn.replace('ladingwu_temporal', name)
            if os.path.isdir(os.path.join(src, fn)):
                shutil.copytree(os.path.join(src, fn), os.path.join(out, dst), dirs_exist_ok=True)
            else:
                shutil.copy2(os.path.join(src, fn), os.path.join(out, dst))
        summ = json.load(open(os.path.join(src, 'summary.json')))
        summ['source_video'] = a['video']
        summ['forced'] = bool(a.get('force'))
        summ['status'] = 'forced_preview' if a.get('force') else 'complete'
        json.dump(summ, open(os.path.join(out, 'summary.json'), 'w'))
        self._say(j, f'[v2r] done in 12s: {out}', 0)

    def _keyframes(self, j):
        out = os.path.join(config.PROJ, j['project'], 'out', j['args']['name'])
        self._say(j, '[v2r] keyframes stage 1/3 contacts', 1)
        self._say(j, '[v2r] keyframes stage 2/3 candidates', 1)
        src = os.path.join(self.fixture, 'out', 'ladingwu_temporal', 'keyframes_auto.json')
        shutil.copy2(src, os.path.join(out, '.keyframes_auto.json.tmp'))
        os.replace(os.path.join(out, '.keyframes_auto.json.tmp'), os.path.join(out, 'keyframes_auto.json'))
        self._say(j, '[v2r] keyframes done', 0)

    def _pipeline(self, j):
        self._say(j, f"[v2r] pipeline args {json.dumps(j['args'].get('extra', []))}", 0)
        proj = os.path.join(config.PROJ, j['project'])
        os.makedirs(os.path.join(proj, 'model'), exist_ok=True)
        if 'noarm' in os.path.basename(j['args']['model']).lower():
            self._say(j, 'NO_ARMATURE: the model has no skeleton', 0.5)
            raise RuntimeError('blender prepare.py failed')
        for step in ('prepare', 'gen', 'train', 'exam'):
            self._say(j, f'[v2r] {step} ...', 2)
        for fn in ('rig.json', 'studio.json', 'prepare_opts.json', 'reference_two_views.png'):
            shutil.copy2(os.path.join(self.fixture, fn), os.path.join(proj, fn))
        rig = json.load(open(os.path.join(proj, 'rig.json')))
        rig['model'] = j['args']['model']
        json.dump(rig, open(os.path.join(proj, 'rig.json'), 'w'))
        open(os.path.join(proj, 'model', 'best.pt'), 'wb').write(b'fake')


_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = FakeEngine() if config.ENGINE == 'fake' else V2REngine()
    return _ENGINE
