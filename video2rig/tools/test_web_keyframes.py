"""Keyframe API tests: direct FastAPI handler calls, temporary data only, worker disabled.

Run: python video2rig/tools/test_web_keyframes.py
Needs fastapi + numpy (the server environment). OpenCV is replaced by a stub when it is
not installed (keyframe handlers never use it). No network, no jobs, no GPU.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / 'video2rig'
if not CODE.exists():
    CODE = REPO
sys.path.insert(0, str(CODE))


def load_server():
    stubs = {}
    if importlib.util.find_spec('cv2') is None:
        cv2 = types.ModuleType('cv2')
        cv2.IMWRITE_JPEG_QUALITY = 1
        cv2.LINE_AA = 16
        stubs['cv2'] = cv2
    with tempfile.TemporaryDirectory() as root, mock.patch.dict(sys.modules, stubs), \
            mock.patch.dict(os.environ, {'V2R_ROOT': root, 'V2R_DISABLE_WORKER': '1'}):
        spec = importlib.util.spec_from_file_location('keyframes_test_server', CODE / 'web/server.py')
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        return server


server = load_server()
store = server.keyframes_store


def auto_doc():
    return {
        'schema': 'v2r.keyframes/1', 'source_video': 'clip.mp4', 'fps': 24.0, 'frames': 20,
        'keyframes': [
            {'id': 'k001', 'frame': 0, 'type': 'start', 'parts': ['body'], 'reason': '片段第一帧', 'confidence': 1.0, 'detail': {}},
            {'id': 'k002', 'frame': 7, 'type': 'extreme', 'parts': ['R_arm'], 'reason': '右手到头', 'confidence': 0.8, 'detail': {}},
            {'id': 'k003', 'frame': 19, 'type': 'end', 'parts': ['body'], 'reason': '最后一帧', 'confidence': 1.0, 'detail': {}},
        ],
        'curves': {'body_speed': [0.0] * 20},
        'breakdown_ladder': {'suggested_count': 1, 'breakdowns': [
            {'rank': 1, 'frame': 4, 'between': [0, 7]}, {'rank': 2, 'frame': 12, 'between': [7, 19]}],
            'error_curve': [{'n': 0, 'max_cm': 3}, {'n': 1, 'max_cm': 2}, {'n': 2, 'max_cm': 1}]},
    }


def key(kid, frame, kind='custom', origin='artist', **extra):
    row = {'id': kid, 'frame': frame, 'type': kind, 'note': '', 'origin': origin, 'auto_id': None, 'auto_frame': None}
    row.update(extra)
    return row


def draft():
    return [key('k001', 0, 'start', 'auto', auto_id='k001', auto_frame=0),
            key('k002', 9, 'extreme', 'auto', auto_id='k002', auto_frame=7, note='再晚两帧才到头'),
            key('b001', 4, 'breakdown', 'ladder', auto_frame=4, ladder_rank=1),
            key('a001', 15, 'custom', note='头先转过去')]


class KeyframeAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        values = {'ROOT': str(self.root)}
        for name, folder in [('PROJ', 'projects'), ('INPUTS', 'inputs'), ('VIDEOS', 'videos'), ('JOBS_DIR', 'jobs')]:
            (self.root / folder).mkdir()
            values[name] = str(self.root / folder)
        values['JOBS'] = mock.Mock(**{'list.return_value': []})
        patch = mock.patch.multiple(server, **values)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(server.subprocess, 'Popen', side_effect=AssertionError('No real process'))
        patch.start()
        self.addCleanup(patch.stop)
        self.out = self.root / 'projects/alpha/out/clip'
        self.out.mkdir(parents=True)
        (self.root / 'projects/alpha/out/other').mkdir()

    def write(self, name, value):
        (self.out / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def with_auto(self):
        self.write('keyframes_auto.json', auto_doc())
        self.write('summary.json', {'frames': 20, 'fps': 24.0, 'source_video': '/data/video2rig/videos/clip.mp4'})
        (self.root / 'videos/clip.mp4').write_bytes(b'fake')
        return hashlib.sha256((self.out / 'keyframes_auto.json').read_bytes()).hexdigest()

    def put(self, base=0, keys=None, removed=None, name='clip', **extra):
        body = {'author': '美术A', 'base_revision': base, 'reviewed': False, 'notes': '',
                'keyframes': draft() if keys is None else keys, 'removed_auto_ids': removed or []}
        body.update(extra)
        return server.save_out_keyframes('alpha', name, body)

    def status(self, call):
        with self.assertRaises(server.HTTPException) as raised:
            call()
        return raised.exception

    # ----------------------------------------------------------------- GET
    def test_get_missing_everything(self):
        data = server.out_keyframes('alpha', 'other')
        self.assertIsNone(data['auto'])
        self.assertIsNone(data['artist'])
        self.assertIsNone(data['video'])
        self.assertIsNone(data['fps'])
        self.assertIsNone(data['frames'])
        self.assertEqual(data['revision'], 0)
        self.assertEqual(data['errors'], {})

    def test_get_summary_only_and_missing_video(self):
        self.write('summary.json', {'frames': 30, 'fps': 30.0, 'source_video': '/data/video2rig/videos/gone.mp4'})
        data = server.out_keyframes('alpha', 'clip')
        self.assertIsNone(data['auto'])
        self.assertEqual((data['fps'], data['frames']), (30.0, 30))
        self.assertIsNone(data['video'], 'video not under VIDEOS must be null, not an error')
        self.assertEqual(data['video_name'], 'gone.mp4')

    def test_get_auto_wins_and_video_resolved_by_basename(self):
        self.with_auto()
        self.write('summary.json', {'frames': 99, 'fps': 30.0, 'source_video': '/data/video2rig/videos/clip.mp4'})
        data = server.out_keyframes('alpha', 'clip')
        self.assertEqual((data['fps'], data['frames']), (24.0, 20), 'fps/frames come from keyframes_auto first')
        self.assertEqual(data['video'], {'name': 'clip.mp4', 'url': '/api/videos/clip.mp4/file'})
        self.assertEqual(len(data['auto']['keyframes']), 3)
        self.assertIsNone(data['artist'])
        self.assertEqual(data['revision'], 0)

    def test_get_unsafe_video_name_and_broken_files(self):
        self.write('summary.json', {'frames': 20, 'fps': 24, 'source_video': '../../etc/passwd'})
        (self.out / 'keyframes_auto.json').write_text('{broken', encoding='utf-8')
        data = server.out_keyframes('alpha', 'clip')
        self.assertIsNone(data['video'])
        self.assertIsNone(data['auto'])
        self.assertIn('auto', data['errors'])

    def test_outputs_flag(self):
        self.with_auto()
        rows = {r['name']: r for r in server.outputs('alpha')}
        self.assertEqual(rows['clip']['has_keyframes'], {'auto': True, 'artist': False})
        self.assertEqual(rows['other']['has_keyframes'], {'auto': False, 'artist': False})
        self.put()
        rows = {r['name']: r for r in server.outputs('alpha')}
        self.assertEqual(rows['clip']['has_keyframes'], {'auto': True, 'artist': True})
        self.assertIn('keyframes_auto.json', rows['clip']['files'], 'existing fields unchanged')

    # ----------------------------------------------------------------- PUT
    def test_save_history_and_auto_untouched(self):
        sha = self.with_auto()
        before = (self.out / 'keyframes_auto.json').read_bytes()
        first = self.put(removed=['k003'])
        self.assertEqual(first['revision'], 1)
        saved = json.loads((self.out / 'keyframes_artist.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['schema'], 'v2r.keyframes.artist/1')
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(saved['base_auto_sha256'], sha)
        self.assertTrue(saved['saved_at'])
        self.assertEqual([k['frame'] for k in saved['keyframes']], [0, 4, 9, 15], 'sorted by frame')
        ladder = [k for k in saved['keyframes'] if k['origin'] == 'ladder'][0]
        self.assertEqual((ladder['id'], ladder['auto_id'], ladder['auto_frame'], ladder['ladder_rank']), ('b001', None, 4, 1))
        self.assertEqual(saved['removed_auto_ids'], ['k003'])
        self.assertEqual(json.loads((self.out / 'keyframes_artist_history/rev0001.json').read_text(encoding='utf-8')), saved)
        second = self.put(base=1, reviewed=True, notes='转圈段想再加两个键')
        self.assertEqual(second['revision'], 2)
        self.assertTrue((self.out / 'keyframes_artist_history/rev0001.json').is_file(), 'history only grows')
        self.assertTrue((self.out / 'keyframes_artist_history/rev0002.json').is_file())
        data = server.out_keyframes('alpha', 'clip')
        self.assertEqual(data['revision'], 2)
        self.assertTrue(data['artist']['reviewed'])
        self.assertEqual(data['artist']['notes'], '转圈段想再加两个键')
        self.assertEqual((self.out / 'keyframes_auto.json').read_bytes(), before, 'keyframes_auto.json never rewritten')
        self.assertFalse([p for p in self.out.rglob('*.tmp')], 'no temporary files left')

    def test_server_fields_cannot_be_forged(self):
        self.with_auto()
        result = self.put(revision=99, schema='x', saved_at='never', base_auto_sha256='fake')
        self.assertEqual(result['artist']['revision'], 1)
        self.assertEqual(result['artist']['schema'], 'v2r.keyframes.artist/1')
        self.assertNotEqual(result['artist']['base_auto_sha256'], 'fake')

    def test_conflict_returns_current_version(self):
        self.with_auto()
        self.put()
        error = self.status(lambda: self.put(base=0))
        self.assertEqual(error.status_code, 409)
        self.assertEqual(error.detail['current_revision'], 1)
        self.assertEqual(error.detail['artist']['revision'], 1)
        self.assertEqual(self.put(base=1)['revision'], 2, 'retry with the new base succeeds')
        self.assertEqual(self.status(lambda: self.put(base=5)).status_code, 409)

    def test_parallel_saves_on_same_base_only_one_wins(self):
        self.with_auto()
        results, barrier = [], threading.Barrier(6)

        def run():
            barrier.wait()
            try:
                results.append(self.put(base=0)['revision'])
            except server.HTTPException as e:
                results.append(e.status_code)
        threads = [threading.Thread(target=run) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [1, 409, 409, 409, 409, 409])
        self.assertEqual(sorted(p.name for p in (self.out / 'keyframes_artist_history').iterdir()), ['rev0001.json'])

    def test_missing_artist_file_keeps_history_numbering(self):
        self.with_auto()
        self.put()
        self.put(base=1)
        (self.out / 'keyframes_artist.json').write_text('{oops', encoding='utf-8')
        data = server.out_keyframes('alpha', 'clip')
        self.assertIn('artist', data['errors'])
        self.assertEqual(data['revision'], 2)
        self.assertEqual(self.put(base=2)['revision'], 3, 'history is never overwritten')

    def test_validation_failures(self):
        self.with_auto()
        auto_ref = lambda kid, frame, af: key(kid, frame, 'start', 'auto', auto_id=kid, auto_frame=af)  # noqa: E731
        cases = {
            'frame float': [key('a001', 1.0)],
            'frame bool': [key('a001', True)],
            'frame out of range': [key('a001', 20)],
            'frame negative': [key('a001', -1)],
            'same frame': [key('a001', 3), key('a002', 3)],
            'duplicate id': [key('a001', 3), key('a001', 4)],
            'bad type': [key('a001', 3, 'pose')],
            'bad origin': [key('a001', 3, origin='machine')],
            'note too long': [key('a001', 3, note='长' * 501)],
            'bad id': [key('../x', 3)],
            'auto without auto_id': [key('k001', 0, 'start', 'auto')],
            'auto id unknown': [auto_ref('k999', 0, 0)],
            'auto referenced twice': [auto_ref('k001', 0, 0), key('x', 2, 'start', 'auto', auto_id='k001')],
            'auto_frame mismatch': [auto_ref('k001', 0, 5)],
            'ladder without rank': [key('b001', 4, 'breakdown', 'ladder', auto_frame=4)],
            'ladder rank zero': [key('b000', 4, 'breakdown', 'ladder', auto_frame=4, ladder_rank=0)],
            'ladder rank twice': [key('b001', 4, 'breakdown', 'ladder', ladder_rank=1), key('b001x', 5, 'breakdown', 'ladder', ladder_rank=1)],
            'ladder rank unknown': [key('b009', 4, 'breakdown', 'ladder', ladder_rank=9)],
            'ladder with auto_id': [key('b001', 4, 'breakdown', 'ladder', ladder_rank=1, auto_id='k001')],
            'artist with auto_id': [key('a001', 3, auto_id='k001')],
            'artist with auto_frame': [key('a001', 3, auto_frame=3)],
            'not a list': 'k001',
        }
        for label, keys in cases.items():
            with self.subTest(label):
                self.assertEqual(self.status(lambda: self.put(keys=keys)).status_code, 400, label)
        too_many = [key(f'a{i:03d}', i % 20) for i in range(501)]
        self.assertEqual(self.status(lambda: self.put(keys=too_many)).status_code, 400)
        self.assertEqual(self.status(lambda: self.put(notes='长' * 2001)).status_code, 400)
        self.assertEqual(self.status(lambda: self.put(author='名' * 41)).status_code, 400)
        self.assertEqual(self.status(lambda: self.put(reviewed='yes')).status_code, 400)
        self.assertEqual(self.status(lambda: self.put(base='0')).status_code, 400)
        self.assertEqual(self.status(lambda: self.put(base=-1)).status_code, 400)
        self.assertEqual(self.status(lambda: self.put(removed=['k999'])).status_code, 400, 'removed id must exist')
        self.assertEqual(self.status(lambda: self.put(removed=['k001'])).status_code, 400, 'removed id still kept')
        self.assertEqual(self.status(lambda: self.put(removed=['k003', 'k003'])).status_code, 400)
        self.assertEqual(self.status(lambda: self.put(removed='k003')).status_code, 400)
        self.assertEqual(self.put(keys=[key('a001', 19, note='长' * 500)], notes='长' * 2000, author='名' * 40,
                              removed=['k001', 'k002', 'k003'])['revision'], 1, 'limits are inclusive')
        self.assertFalse((self.out / 'keyframes_artist_history/rev0002.json').exists())

    def test_without_auto_file(self):
        self.write('summary.json', {'frames': 10, 'fps': 24.0, 'source_video': 'clip.mp4'})
        self.assertEqual(self.put(keys=[key('a001', 2)])['revision'], 1, 'artist can label from scratch')
        self.assertIsNone(json.loads((self.out / 'keyframes_artist.json').read_text(encoding='utf-8'))['base_auto_sha256'])
        self.assertEqual(self.status(lambda: self.put(base=1, keys=[key('k001', 0, 'start', 'auto', auto_id='k001')])).status_code, 400)
        self.assertEqual(self.status(lambda: self.put(base=1, keys=[], removed=['k001'])).status_code, 400)
        self.assertEqual(self.status(lambda: self.put(base=1, keys=[key('b001', 2, 'breakdown', 'ladder', ladder_rank=1)])).status_code, 400,
                         'ladder entry without a ladder must carry its original frame')
        self.assertFalse((self.out / 'keyframes_auto.json').exists(), 'saving never creates candidates')

    def test_no_frames_anywhere_rejected(self):
        self.assertEqual(self.status(lambda: self.put(name='other', keys=[key('a001', 0)])).status_code, 400)

    def test_path_traversal_rejected(self):
        self.with_auto()
        for project, name in [('alpha', '..'), ('alpha', '../alpha'), ('alpha', '.hidden'), ('..', 'clip'),
                              ('alpha', 'clip/../../x'), ('alpha', 'missing')]:
            with self.subTest(project=project, name=name):
                code = self.status(lambda: server.out_keyframes(project, name)).status_code
                self.assertIn(code, (400, 403, 404))
                code = self.status(lambda: server.save_out_keyframes(project, name, {'base_revision': 0, 'keyframes': []})).status_code
                self.assertIn(code, (400, 403, 404))
        self.assertFalse(list((self.root / 'projects').rglob('keyframes_artist.json')))

    def test_symlinked_history_outside_result_is_rejected(self):
        self.with_auto()
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            os.symlink(outside, self.out / 'keyframes_artist_history', target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks not permitted here')
        self.assertEqual(self.status(lambda: self.put()).status_code, 403)
        self.assertFalse(list(outside.iterdir()))

    def test_candidate_file_being_rewritten_is_503(self):
        self.with_auto()
        (self.out / 'keyframes_auto.json').write_text('{half', encoding='utf-8')
        self.assertEqual(self.status(lambda: self.put()).status_code, 503)
        self.assertFalse((self.out / 'keyframes_artist.json').exists())

    def test_real_sample_if_present(self):
        sample = REPO / 'work/maxkeys/m11101_tex_ladingwu/keyframes_auto.json'
        if not sample.is_file():
            self.skipTest('sample not present')
        doc = json.loads(sample.read_text(encoding='utf-8'))
        self.write('keyframes_auto.json', doc)
        self.write('summary.json', {'frames': 193, 'fps': 24.0, 'source_video': '/data/video2rig/videos/ladingwu.mp4'})
        data = server.out_keyframes('alpha', 'clip')
        self.assertEqual(len(data['auto']['keyframes']), 34)
        self.assertEqual(data['auto']['breakdown_ladder']['suggested_count'], 16)
        rows = [key(k['id'], k['frame'], k['type'], 'auto', auto_id=k['id'], auto_frame=k['frame']) for k in doc['keyframes']]
        for b in doc['breakdown_ladder']['breakdowns'][:16]:
            rows.append(key(f"b{b['rank']:03d}", b['frame'], 'breakdown', 'ladder', auto_frame=b['frame'], ladder_rank=b['rank']))
        self.assertEqual(len(self.put(keys=copy.deepcopy(rows))['artist']['keyframes']), 50)


BIPED = ['Bip001', 'Bip001 Spine1', 'Bip001 Neck', 'Bip001 L Hand', 'Bip001 R Hand',
         'Bip001 L Forearm', 'Bip001 R Forearm', 'Bip001 L Foot', 'Bip001 R Foot']


class KeyframeJobTests(unittest.TestCase):
    """The web "生成候选关键帧" job: availability, validation, argv. No process is started."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        values = {'ROOT': str(self.root)}
        for name, folder in [('PROJ', 'projects'), ('INPUTS', 'inputs'), ('VIDEOS', 'videos'), ('JOBS_DIR', 'jobs')]:
            (self.root / folder).mkdir()
            values[name] = str(self.root / folder)
        self.added = []
        values['JOBS'] = mock.Mock(**{'list.return_value': [], 'add.side_effect': lambda p, c, a: self.added.append((p, c, a)) or {'id': 'j1', 'project': p, 'cmd': c, 'args': a}})
        self.script = self.root / 'code/tools/max2026/run_keyframes.py'
        self.script.parent.mkdir(parents=True)
        self.script.write_text('# stub', encoding='utf-8')
        values['KEYFRAME_SCRIPT'] = str(self.script)
        patch = mock.patch.multiple(server, **values)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(server, 'task_snapshot', return_value={'tasks': [], 'monitor': {'available': True}})
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(server.subprocess, 'Popen', side_effect=AssertionError('No real process'))
        patch.start()
        self.addCleanup(patch.stop)
        self.proj = self.root / 'projects/alpha'
        self.out = self.proj / 'out/clip'
        self.out.mkdir(parents=True)
        self.rig(BIPED)
        for f in ('motion.npz', 'reading.npz'):
            (self.out / f).write_bytes(b'x')
        (self.out / 'summary.json').write_text(json.dumps({'frames': 20, 'fps': 24}), encoding='utf-8')

    def rig(self, names):
        (self.proj / 'rig.json').write_text(json.dumps({'keypoints': [{'name': n} for n in names]}), encoding='utf-8')

    def submit(self, args):
        import asyncio
        return asyncio.run(server.new_job({'project': 'alpha', 'cmd': 'keyframes', 'args': args}))

    def err(self, args):
        with self.assertRaises(server.HTTPException) as raised:
            self.submit(args)
        return raised.exception

    def test_generation_flag_in_view(self):
        self.assertEqual(server.out_keyframes('alpha', 'clip')['generation'], {'available': True, 'reason': ''})
        (self.out / 'reading.npz').unlink()
        gen = server.out_keyframes('alpha', 'clip')['generation']
        self.assertFalse(gen['available'])
        self.assertIn('reading.npz', gen['reason'])

    def test_non_biped_and_missing_script(self):
        self.rig(['Liz Head', 'Liz Tail'])
        e = self.err({'name': 'clip'})
        self.assertEqual(e.status_code, 400)
        self.assertIn('Biped', e.detail)
        self.rig(BIPED)
        self.script.unlink()
        self.assertIn('run_keyframes.py', self.err({'name': 'clip'}).detail)
        self.assertEqual(self.added, [])

    def test_submit_new_and_replace_confirmation(self):
        job = self.submit({'name': 'clip', 'extra': '--evil'})
        self.assertEqual(job['cmd'], 'keyframes')
        self.assertEqual(self.added[-1], ('alpha', 'keyframes', {'name': 'clip', 'replace': False}), 'only the name is kept')
        (self.out / 'keyframes_auto.json').write_text('{}', encoding='utf-8')
        self.assertEqual(self.err({'name': 'clip'}).status_code, 409)
        self.submit({'name': 'clip', 'replace': True})
        self.assertEqual(self.added[-1][2], {'name': 'clip', 'replace': True})

    def test_bad_names_and_busy_project(self):
        self.assertEqual(self.err({'name': '../clip'}).status_code, 400)
        self.assertEqual(self.err({'name': 'nope'}).status_code, 404)
        with mock.patch.object(server, 'task_snapshot', return_value={'tasks': [{'project': 'alpha', 'status': 'running'}], 'monitor': {'available': True}}):
            self.assertEqual(self.err({'name': 'clip'}).status_code, 409)
        self.assertEqual(self.added, [])

    def test_argv(self):
        argv = server.Jobs._argv(mock.Mock(), {'project': 'alpha', 'cmd': 'keyframes', 'args': {'name': 'clip', 'replace': False}})
        self.assertEqual(argv, [server.PY, str(self.script), str(self.proj), 'clip'])

    def test_monitor_progress_markers(self):
        from web.task_monitor import parse_progress
        lines = ['[v2r] keyframes start alpha/clip', '[v2r] keyframes stage 2/3 candidates']
        phase = parse_progress(lines, 'keyframes')['phase']
        self.assertEqual((phase['current'], phase['total'], phase['label']), (2, 3, '抽取候选关键帧'))
        done = parse_progress(lines + ['[v2r] keyframes done in 9.1s: 34 candidates'], 'keyframes')['phase']
        self.assertEqual(done['label'], '候选关键帧已生成')


if __name__ == '__main__':
    unittest.main(verbosity=2)
