"""Small stat-aware project summary cache; never imports training libraries.

Metadata counts are cached rather than the large pose arrays. JSONL logs are indexed
incrementally; partial writes are retried instead of becoming a cached empty project.
"""
from copy import deepcopy
import json
from pathlib import Path
import threading
import time


class ProjectStore:
    def __init__(self, projects, ttl=2.0, clock=time.monotonic):
        self.root = Path(projects)
        self.ttl = ttl
        self.clock = clock
        self._lock = threading.RLock()
        self._files = {}
        self._logs = {}
        self._summaries = {}
        self._listed = None
        self._listed_at = -float('inf')
        self.metrics = {'json_reads': 0, 'jsonl_bytes': 0, 'builds': 0}

    @staticmethod
    def stamp(path):
        try:
            s = path.stat()
            return (s.st_ino, s.st_size, s.st_mtime_ns)
        except OSError:
            return None

    def invalidate(self, name=None):
        with self._lock:
            self._listed = None
            if name is None:
                self._summaries.clear()
            else:
                self._summaries.pop(name, None)

    def _json(self, path, default=None, count=False):
        sig = self.stamp(path)
        if sig is None:
            self._files.pop(path, None)
            return default
        cached = self._files.get(path)
        if cached and cached[0] == sig:
            return cached[1]
        try:
            with path.open(encoding='utf-8') as f:
                value = json.load(f)
            self.metrics['json_reads'] += 1
            if count:
                value = len(value) if isinstance(value, list) else 0
            if self.stamp(path) == sig:
                self._files[path] = (sig, value)
            return value
        except (OSError, ValueError, TypeError):
            # A writer may be replacing the file. Never memoize a parse failure.
            return default

    def _log(self, path):
        sig = self.stamp(path)
        if sig is None:
            self._logs.pop(path, None)
            return 0, None
        old = self._logs.get(path)
        if old and old['sig'] == sig:
            return old['count'], old['last']
        # Identity and growth check: append if old complete prefix remains unchanged.
        append = bool(old and old['sig'][0] == sig[0] and sig[1] > old['sig'][1])
        count, last, offset = (old['count'], old['last'], old['offset']) if append else (0, None, 0)
        try:
            with path.open('rb') as f:
                if append and old['prefix']:
                    prefix = f.read(len(old['prefix']))
                    if prefix != old['prefix']:
                        count, last, offset = 0, None, 0
                f.seek(offset)
                data = f.read()
                self.metrics['jsonl_bytes'] += len(data)
                # Only parse complete records. Keep incomplete tail for the next read.
                cut = data.rfind(b'\n') + 1
                for line in data[:cut].splitlines():
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):
                            last = value
                            count += 1
                    except (ValueError, UnicodeError):
                        pass
                f.seek(0)
                prefix = f.read(min(offset + cut, 128))
            if self.stamp(path) == sig:
                self._logs[path] = {'sig': sig, 'offset': offset + cut, 'prefix': prefix, 'count': count, 'last': last}
            return count, last
        except OSError:
            return 0, None

    def _build(self, name):
        d = self.root / name
        rig = self._json(d / 'rig.json', {}) or {}
        studio = self._json(d / 'studio.json', {}) or {}
        prepare = self._json(d / 'prepare_opts.json', {}) or {}
        metas = sorted((d / 'train_data').glob('meta_*.json'))
        samples = sum(self._json(f, 0, count=True) or 0 for f in metas)
        epochs, last = self._log(d / 'model' / 'train_log.jsonl')
        exam = self._json(d / 'exam' / 'exam_report.json')
        outs = sorted(f.name for f in (d / 'out').glob('*') if f.is_dir())
        reader = (d / 'model' / 'best.pt').is_file()
        stage = 'prepared' if rig else 'empty'
        if samples:
            stage = 'data'
        if reader:
            stage = 'trained'
        if exam:
            stage = 'exam_passed' if exam.get('passed') else 'exam_failed'
        self.metrics['builds'] += 1
        return {
            'name': name, 'model': rig.get('model'),
            'model_name': str(rig.get('model') or '').replace('\\', '/').split('/')[-1],
            'has_reader': reader,
            'visual_profile': studio.get('visual_profile') or prepare.get('visual_profile', 'legacy'),
            'bones': len(rig.get('bones', [])), 'active': rig.get('n_active'),
            'keypoints': len(rig.get('keypoints', [])), 'samples': samples,
            'epochs': epochs, 'last_val': last,
            'exam': {k: exam.get(k) for k in ('passed', 'joint_err_mean_pct_of_size',
                     'bone_angle_mean_deg', 'bone_angle_median_deg', 'root_angle_mean_deg')} if exam else None,
            'outputs': outs, 'stage': stage, 'mtime': d.stat().st_mtime,
            'has_reference': (d / 'reference_two_views.png').is_file(),
        }

    def _signature(self, d):
        paths = [d / rel for rel in ('rig.json', 'studio.json', 'prepare_opts.json', 'model/best.pt',
                 'model/train_log.jsonl', 'exam/exam_report.json', 'reference_two_views.png', 'out')]
        paths.extend(sorted((d / 'train_data').glob('meta_*.json')))
        return tuple((str(p), self.stamp(p)) for p in paths)

    def summary(self, name):
        # Server validates project paths before calling. Defend independently too.
        path = (self.root / name).resolve()
        if path.parent != self.root.resolve() or not path.is_dir():
            raise FileNotFoundError(name)
        with self._lock:
            now = self.clock()
            signature = self._signature(path)
            cached = self._summaries.get(name)
            if cached and now - cached[0] < self.ttl and cached[2] == signature:
                return deepcopy(cached[1])
            result = self._build(name)
            self._summaries[name] = (now, result, signature)
            if len(self._files) > 512:
                self._files = {p: v for p, v in self._files.items() if p.exists()}
            return deepcopy(result)

    def list(self, valid_name):
        with self._lock:
            now = self.clock()
            if self._listed is not None and now - self._listed_at < self.ttl:
                return deepcopy(self._listed)
            names = sorted(p.name for p in self.root.iterdir() if p.is_dir() and valid_name(p.name))
            rows = []
            for name in names:
                try:
                    rows.append(self.summary(name))
                except FileNotFoundError:
                    continue
            self._summaries = {k: v for k, v in self._summaries.items() if k in names}
            # Remove file/log cache entries for deleted projects so memory stays bounded.
            alive = set(names)
            for cache in (self._files, self._logs):
                for path in list(cache):
                    try:
                        name = path.relative_to(self.root).parts[0]
                    except ValueError:
                        name = None
                    if name not in alive:
                        cache.pop(path, None)
            self._listed_at, self._listed = now, rows
            return deepcopy(rows)
