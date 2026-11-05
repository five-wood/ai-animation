"""Direct FastAPI handler tests with worker disabled and all data temporary.
Requires the existing server Python environment; no httpx, network or GPU jobs.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / 'video2rig'
if not CODE.exists():
    CODE = REPO
sys.path.insert(0, str(CODE))


def load_server():
    with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ, {'V2R_ROOT': root, 'V2R_DISABLE_WORKER': '1'}):
        spec = importlib.util.spec_from_file_location('tasks_test_server', CODE / 'web/server.py')
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        return server


server = load_server()


class TasksAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        values = {'ROOT': str(self.root)}
        for key, folder in [('PROJ','projects'),('INPUTS','inputs'),('VIDEOS','videos'),('JOBS_DIR','jobs')]:
            path = self.root / folder
            path.mkdir()
            values[key] = str(path)
        self.jobs = mock.Mock()
        self.jobs.list.return_value = []
        self.jobs.add.side_effect = lambda p,c,a: {'id':'dry', 'project':p,'cmd':c,'args':a,'status':'queued'}
        values['JOBS'] = self.jobs
        patch = mock.patch.multiple(server, **values)
        patch.start()
        self.addCleanup(patch.stop)
        self.monitor = mock.Mock()
        self.monitor.snapshot.return_value = {'tasks':[], 'observed_at':123, 'monitor':{'available':True,'warning':None}}
        patch = mock.patch.object(server,'task_monitor',return_value=self.monitor)
        patch.start()
        self.addCleanup(patch.stop)
        project = self.root / 'projects/alpha/model'
        project.mkdir(parents=True)
        (project / 'best.pt').write_bytes(b'fake')
        (self.root / 'videos/clip.mp4').write_bytes(b'fake')
        patch = mock.patch.object(server.subprocess,'Popen',side_effect=AssertionError('No real process'))
        patch.start()
        self.addCleanup(patch.stop)

    def submit(self, **args):
        return asyncio.run(server.new_job({'project':'alpha','cmd':'animate','args':{'video':'clip.mp4',**args}}))

    def test_snapshot_contract_and_read_only_log(self):
        self.assertEqual(server.tasks()['observed_at'],123)
        self.monitor.task_log.return_value = {'lines':['test']}
        self.assertEqual(server.task_log('cli:10:20',9999),{'lines':['test']})
        self.monitor.task_log.assert_called_once_with('cli:10:20',[],tail=1000)
        self.monitor.task_log.return_value = None
        with self.assertRaises(server.HTTPException) as raised:
            server.task_log('cli:missing')
        self.assertEqual(raised.exception.status_code,404)
        self.assertFalse(server.capabilities()['cli_control'])

    def test_observed_same_project_blocks_new_mutation(self):
        for status in ['running','queued','unknown']:
            self.monitor.snapshot.return_value['tasks']=[{'project':'alpha','status':status,'source':'cli'}]
            with self.assertRaises(server.HTTPException) as raised:
                self.submit()
            self.assertEqual(raised.exception.status_code,409)
        self.jobs.add.assert_not_called()
        self.monitor.snapshot.return_value['tasks']=[{'project':'beta','status':'running','source':'cli'}]
        self.assertEqual(self.submit()['status'],'queued')

    def test_collision_requires_explicit_overwrite_not_truthiness(self):
        (self.root / 'projects/alpha/out/clip').mkdir(parents=True)
        for value in [None,False,1,'true']:
            with self.assertRaises(server.HTTPException) as raised:
                self.submit(overwrite=value)
            self.assertEqual(raised.exception.status_code,409)
        result = self.submit(overwrite=True)
        self.assertEqual(result['args']['name'],'clip')

    def test_temporal_default_and_any_existing_directory_rejected(self):
        self.assertEqual(self.submit(solver='temporal')['args']['name'],'clip_temporal')
        (self.root / 'projects/alpha/out/clip_temporal').mkdir(parents=True)
        with self.assertRaises(server.HTTPException) as raised:
            self.submit(solver='temporal',overwrite=True)
        self.assertEqual(raised.exception.status_code,409)

    def test_names_are_checked_and_cli_cancel_never_signals(self):
        with self.assertRaises(server.HTTPException):
            self.submit(name='../escape')
        self.jobs.cancel.side_effect = server.HTTPException(404)
        with self.assertRaises(server.HTTPException):
            server.cancel_job('cli:12:34')
        self.jobs.add.assert_not_called()

    def test_queue_collision_ids_and_atomic_same_project_guard(self):
        with mock.patch.dict(os.environ,{'V2R_DISABLE_WORKER':'1'}):
            queue = server.Jobs()
        first = queue.add('alpha','exam',{})
        with self.assertRaises(server.HTTPException):
            queue.add('alpha','exam',{})
        queue.jobs[first['id']]['status']='done'
        second = queue.add('alpha','exam',{})
        self.assertNotEqual(first['id'],second['id'])
        self.assertTrue((self.root / 'jobs' / (second['id']+'.json')).is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
