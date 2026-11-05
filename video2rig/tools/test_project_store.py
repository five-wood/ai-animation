"""Stdlib-only summary cache regressions. No GPU/network/service imports."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('project_store', Path(__file__).resolve().parents[1] / 'web/project_store.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p = self.root / 'alpha'
        (self.p / 'model').mkdir(parents=True)
        (self.p / 'train_data').mkdir()
        self.now = 0
        self.store = module.ProjectStore(self.root, clock=lambda: self.now)
        self.write('rig.json', {'model': '/inputs/alpha.glb', 'bones': [{'name': 'root'}], 'keypoints': []})
        self.write('train_data/meta_0.json', [{'id': 1}, {'id': 2}])
        (self.p / 'model/train_log.jsonl').write_text('{"epoch":0,"px_med":2}\n')

    def write(self, rel, value):
        path = self.p / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_warm_summary_no_reparse_and_defensive_copy(self):
        one = self.store.summary('alpha')
        reads = dict(self.store.metrics)
        one['outputs'].append('mutated')
        two = self.store.summary('alpha')
        self.assertEqual(self.store.metrics, reads)
        self.assertEqual(two['outputs'], [])
        self.assertEqual(two['samples'], 2)
        self.assertEqual(two['epochs'], 1)

    def test_list_ttl_reuses_metadata_counts_across_rebuild(self):
        self.store.list(lambda n: True)
        reads = self.store.metrics['json_reads']
        bytes_read = self.store.metrics['jsonl_bytes']
        self.now = 10
        result = self.store.list(lambda n: True)
        self.assertEqual(result[0]['name'], 'alpha')
        self.assertEqual(self.store.metrics['json_reads'], reads)
        self.assertEqual(self.store.metrics['jsonl_bytes'], bytes_read)
        # No metadata arrays remain retained in the cache.
        value = self.store._files[self.p / 'train_data/meta_0.json'][1]
        self.assertEqual(value, 2)

    def test_file_change_detected_before_summary_ttl(self):
        self.assertEqual(self.store.summary('alpha')['visual_profile'], 'legacy')
        self.write('studio.json', {'visual_profile': 'soft-clean'})
        self.assertEqual(self.store.summary('alpha')['visual_profile'], 'soft-clean')
        self.write('train_data/meta_1.json', [{'id': 3}])
        self.assertEqual(self.store.summary('alpha')['samples'], 3)

    def test_incremental_append_partial_tail_and_truncate(self):
        self.store.summary('alpha')
        log = self.p / 'model/train_log.jsonl'
        with log.open('a') as f:
            f.write('{"epoch":1}\n{"epoch":')
        result = self.store.summary('alpha')
        self.assertEqual(result['epochs'], 2)
        self.assertEqual(result['last_val']['epoch'], 1)
        with log.open('a') as f:
            f.write('2}\n')
        self.assertEqual(self.store.summary('alpha')['epochs'], 3)
        log.write_text('{"epoch":9}\n')
        result = self.store.summary('alpha')
        self.assertEqual(result['epochs'], 1)
        self.assertEqual(result['last_val']['epoch'], 9)

    def test_failed_json_is_not_cached_forever(self):
        path = self.p / 'train_data/meta_0.json'
        path.write_text('[')
        self.assertEqual(self.store.summary('alpha')['samples'], 0)
        self.write('train_data/meta_0.json', [{'id': 4}])
        self.assertEqual(self.store.summary('alpha')['samples'], 1)

    def test_prerequisites_outputs_and_invalidate(self):
        self.assertEqual(self.store.summary('alpha')['stage'], 'data')
        (self.p / 'model/best.pt').write_bytes(b'fake')
        self.assertEqual(self.store.summary('alpha')['stage'], 'trained')
        self.write('exam/exam_report.json', {'passed': True})
        (self.p / 'out/walk').mkdir(parents=True)
        result = self.store.summary('alpha')
        self.assertEqual(result['stage'], 'exam_passed')
        self.assertEqual(result['outputs'], ['walk'])
        self.store.invalidate('alpha')
        self.assertNotIn('alpha', self.store._summaries)

    def test_no_path_escape(self):
        with self.assertRaises(FileNotFoundError):
            self.store.summary('../alpha')


if __name__ == '__main__':
    unittest.main(verbosity=2)
