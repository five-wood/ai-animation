"""Stdlib-only monitor fixtures: no real processes, GPU, server or dependencies.

Run: python tools/test_task_monitor.py
"""
import errno
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from web.task_monitor import (TaskMonitor, bounded_log_tail, classify_argv, parse_cmdline,
                              parse_proc_stat, parse_progress, read_process_identity,
                              MAX_LOG_BYTES, MAX_RETAINED, RETENTION_TTL)


def stat_line(pid, ppid=1, start=100, comm="python (worker) ) tricky"):
    # fields 3..22, with comm deliberately containing a closing parenthesis.
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(start)]
    return f"{pid} ({comm}) " + " ".join(fields) + "\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "app"
        self.projects = self.root / "projects"
        self.code = self.root / "code"
        self.jobs = self.root / "jobs"
        self.proc = self.base / "proc"
        for directory in (self.projects / "dragon", self.code, self.jobs, self.proc):
            directory.mkdir(parents=True, exist_ok=True)
        (self.proc / "stat").write_text("cpu 1 2 3\nbtime 1000\n", encoding="ascii")
        self.now = 1100.0
        self.links = {}
        self.real_readlink = os.readlink
        self.link_patch = mock.patch("web.task_monitor.os.readlink", side_effect=self.readlink)
        self.link_patch.start()
        self.monitor = TaskMonitor(self.projects, self.jobs, self.proc, lambda: self.now)
        self.monitor._ticks = 100.0

    def tearDown(self):
        self.link_patch.stop()
        self.temp.cleanup()

    def readlink(self, path, *args, **kwargs):
        path = os.fspath(path)
        if path in self.links:
            value = self.links[path]
            if isinstance(value, Exception):
                raise value
            return value
        return self.real_readlink(path, *args, **kwargs)

    def process(self, pid=10, ppid=1, start=100, argv=None, cwd=None, stdout=None, stderr=None):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        (directory / "stat").write_text(stat_line(pid, ppid, start), encoding="ascii")
        argv = argv or ["/app/.venv/bin/python", "-m", "v2r.cli", "train", str(self.projects / "dragon")]
        (directory / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in argv) + b"\0")
        self.links[str(directory / "cwd")] = str(cwd or self.code)
        if stdout:
            self.links[str(directory / "fd" / "1")] = str(stdout)
        if stderr:
            self.links[str(directory / "fd" / "2")] = str(stderr)
        return argv

    def log(self, relative, text, mtime=1050):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def web(self, **values):
        job = {"id": "web-1", "project": "dragon", "cmd": "train", "args": {}, "status": "running",
               "created": 1000, "started": 1001, "ended": None, "rc": None, "log": str(self.jobs / "web-1.log")}
        job.update(values)
        return job

    def cli_tasks(self, snapshot=None):
        return [task for task in (snapshot or self.monitor.snapshot([]))["tasks"] if task["source"] == "cli"]


class PureParserTests(unittest.TestCase):
    def test_parentheses_stat_and_identity(self):
        self.assertEqual(parse_proc_stat(stat_line(17, 4, 876))["starttime"], 876)
        self.assertEqual(parse_proc_stat(stat_line(17, 4, 876))["ppid"], 4)
        for text in ("17 (bad", "17 () S", "NaN (x) S 1", "17 (x) S -1"):
            self.assertIsNone(parse_proc_stat(text))

    def test_cmdline_must_be_complete(self):
        self.assertEqual(parse_cmdline(b"python\0-m\0v2r.cli\0"), ["python", "-m", "v2r.cli"])
        self.assertEqual(parse_cmdline(b"python\0truncated"), [])

    def test_current_branch_step_progress_overrides_epoch(self):
        lines = ["[v2r] train items=500 val items=50 K=20 net=448x448 heat=32x32",
                 "[v2r] 200 optimiser steps (5 epochs max), hidden_empty=True, smear=0.3",
                 "[v2r] ep 0 step 50/200 hm 0.25 depth 0.04 dense fg 0.2 coord 0.3 err 0.1 1.0min",
                 "[v2r] == epoch 0: val px err median 4.0 p90 9.0"]
        phase = parse_progress(lines, "train", {"epochs": 40})["phase"]
        self.assertEqual((phase["key"], phase["current"], phase["total"], phase["unit"]), ("train", 50, 200, "步"))
        self.assertEqual(phase["percent"], 25.0)

    def test_only_explicit_valid_denominator_has_percent(self):
        for line in ("[v2r] ep 0 step 12/0 hm 0.2", "[v2r] ep 0 step 300/200 hm 0.2",
                     "[v2r] == epoch 2: val px err median 4.0", "[v2r] solve stage 1 it 100 loss 0.3",
                     "[v2r] training done in 1 min (50 steps), best val px mean 4.0"):
            self.assertNotIn("percent", parse_progress([line], "train")["phase"], line)
        self.assertEqual(parse_progress(["[v2r] epoch 2/8 loss 0.4"], "train")["phase"]["percent"], 25)
        phase = parse_progress(["[v2r] == epoch 0: val px err median 4.0"], "train", {"epochs": 20})["phase"]
        self.assertEqual(phase["current"], 1)
        self.assertIsNone(phase["total"])

    def test_new_training_header_drops_old_counters(self):
        lines = ["[v2r] ep 9 step 1000/1000 hm 0.1", "[v2r] training done in 1 min",
                 "[v2r] train items=100 val items=20", "[v2r] 500 optimiser steps (20 epochs max), hidden_empty=False, smear=0.0"]
        self.assertNotIn("percent", parse_progress(lines, "train")["phase"])

    def test_gen_exam_and_pipeline_markers(self):
        phase = parse_progress(["[v2r] 25/100 samples 0.4s/sample"], "gen")["phase"]
        self.assertEqual((phase["current"], phase["total"], phase["percent"]), (25, 100, 25))
        phase = parse_progress(["[v2r] motion frame 15/150"], "exam")["phase"]
        self.assertEqual(phase["percent"], 10)
        for line, key in (("[v2r] video clip.mp4: 300 frames @ 30.0 fps", "read"),
                          ("[v2r] check: two-view consistency passed = True", "check"),
                          ("[v2r] solve stage 0 it 100 loss 0.5", "solve"),
                          ("[v2r] solved in 15s", "export"),
                          ("[v2r] NEEDS_REVIEW: mismatch", "review")):
            parsed = parse_progress([line], "animate")
            self.assertEqual(parsed["phase"]["key"], key)
            self.assertNotIn("percent", parsed["phase"])
        self.assertEqual(parse_progress([], "pipeline")["phase"]["key"], "unknown")


class ClassificationTests(Fixture):
    def test_exact_project_path_and_allowlisted_commands(self):
        argv = self.process()
        result = classify_argv(argv, str(self.code), str(self.projects), [str(self.code)])
        self.assertEqual(result["project"], "dragon")
        for cmd in ("prepare", "gen", "train", "exam", "animate", "stabilize"):
            pos = (["monster.fbx", "../projects/dragon"] if cmd == "prepare" else
                   ["../projects/dragon", "clip.mp4"] if cmd == "animate" else
                   ["../projects/dragon", "original"] if cmd == "stabilize" else ["../projects/dragon"])
            self.assertIsNotNone(classify_argv(["python3.11", "-u", "-m", "v2r.cli", cmd] + pos,
                                              str(self.code), str(self.projects), [str(self.code)]))
        invalid = [
            ["python", "-c", "python -m v2r.cli train " + str(self.projects / "dragon")],
            ["python", "-m", "other.v2r.cli", "train", str(self.projects / "dragon")],
            ["python", "-m", "v2r.cli", "inspect", "model.fbx"],
            ["notpython", "-m", "v2r.cli", "train", str(self.projects / "dragon")],
            ["python", "-m", "v2r.cli", "train", str(self.projects) + "-foreign/dragon"],
            ["python", "-m", "v2r.cli", "train", str(self.projects / "dragon" / "nested")],
            ["python", "-m", "v2r.cli", "train", str(self.projects / "dragon" / ".." / ".." / "secret")],
        ]
        for argv in invalid:
            self.assertIsNone(classify_argv(argv, str(self.code), str(self.projects), [str(self.code)]), argv)
        self.assertIsNone(classify_argv(self.process(), str(self.base / "foreign"), str(self.projects), [str(self.code)]))

    def test_pipeline_script_must_be_exact_known_root(self):
        valid = ["bash", str(self.code / "run_all.sh"), "monster.fbx", "dragon"]
        self.assertEqual(classify_argv(valid, str(self.code), str(self.projects), [str(self.code)])["cmd"], "pipeline")
        for argv in (["bash", "-c", "bash run_all.sh monster.fbx dragon"],
                     ["bash", str(self.base / "evil" / "run_all.sh"), "model", "dragon"],
                     ["bash", "run_all.sh", "model", "../dragon"]):
            self.assertIsNone(classify_argv(argv, str(self.code), str(self.projects), [str(self.code)]))

    def test_option_before_project_does_not_confuse_project(self):
        argv = ["python", "-m", "v2r.cli", "train", "--steps", "400", "../projects/dragon"]
        self.assertEqual(classify_argv(argv, str(self.code), str(self.projects), [str(self.code)])["args"]["steps"], "400")


class DiscoveryTests(Fixture):
    def test_fork_workers_deduped_but_independent_roots_not(self):
        argv = self.process(10)
        self.process(11, ppid=10, start=101, argv=argv)
        self.process(12, ppid=11, start=102, argv=argv)
        self.process(20, ppid=1, start=200, argv=argv)
        tasks = self.cli_tasks()
        self.assertEqual({t["pid"] for t in tasks}, {10, 20})
        self.assertEqual(read_process_identity(10, self.proc), 100)
        self.assertTrue(all(t["read_only"] and not t["can_cancel"] for t in tasks))
        self.assertEqual(tasks[1]["id"], "cli:10:100")

    def test_managed_identity_dedupes_descendants_not_pid_reuse(self):
        argv = self.process(10)
        self.process(11, ppid=10, argv=argv)
        self.process(20, start=200, argv=argv)
        snapshot = self.monitor.snapshot([self.web(pid=10, process_start=100, argv=argv)])
        self.assertEqual({t["pid"] for t in self.cli_tasks(snapshot)}, {20})
        self.assertEqual(snapshot["tasks"][0]["source"], "web")
        self.assertTrue(snapshot["tasks"][0]["can_cancel"])
        snapshot = self.monitor.snapshot([self.web(pid=10, process_start=999, argv=argv)])
        self.assertEqual({t["pid"] for t in self.cli_tasks(snapshot)}, {10, 20})

    def test_older_web_argv_match_only_unambiguous_root(self):
        argv = self.process(10)
        self.process(11, ppid=10, argv=argv)
        self.assertEqual(self.cli_tasks(self.monitor.snapshot([self.web(argv=argv)])), [])
        self.process(20, start=200, argv=argv)
        self.now += 3
        self.assertEqual({t["pid"] for t in self.cli_tasks(self.monitor.snapshot([self.web(argv=argv)]))}, {10, 20})

    def test_pipeline_and_live_child_one_task_real_stage(self):
        self.process(10, argv=["bash", str(self.code / "run_all.sh"), "monster.fbx", "dragon"])
        self.process(20, ppid=10, start=200)
        self.process(21, ppid=20, start=201)
        # Later mtime on unrelated old exam log must not override a live train.
        self.log("projects/dragon/exam.log", "[v2r] EXAM PASSED\n", mtime=1099)
        self.log("projects/dragon/train.log", "[v2r] ep 0 step 50/200 hm 0.3\n", mtime=1050)
        tasks = self.cli_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual((tasks[0]["cmd"], tasks[0]["phase"]["key"]), ("pipeline", "train"))
        self.assertEqual(tasks[0]["phase"]["percent"], 25)

    def test_ttl_cache_identity_and_ended_unknown_retention(self):
        self.process()
        task = self.cli_tasks()[0]
        first_progress = task["progress_updated_at"]
        # Delete only this fixture PID tree (never a real proc entry).
        target = self.proc / "10"
        self.assertEqual(target.resolve().parent, self.proc.resolve())
        shutil.rmtree(target)
        self.now += 1
        self.assertEqual(self.cli_tasks()[0]["status"], "running")
        self.assertEqual(self.cli_tasks()[0]["progress_updated_at"], first_progress)
        self.now += 2
        ended = self.cli_tasks()[0]
        self.assertEqual(ended["id"], task["id"])
        self.assertEqual(ended["status"], "ended_unknown")
        self.assertIsNone(ended["rc"])
        self.assertIsNone(ended["ended"])
        self.now += RETENTION_TTL + 1
        self.assertEqual(self.cli_tasks(), [])
        self.assertIsNone(self.monitor.task_log(task["id"], []))

    def test_reused_pid_has_new_id_and_old_unknown_outcome(self):
        self.process()
        first = self.cli_tasks()[0]["id"]
        self.process(start=300)
        self.now += 3
        tasks = self.cli_tasks()
        self.assertEqual({t["id"] for t in tasks}, {first, "cli:10:300"})
        self.assertEqual(next(t for t in tasks if t["id"] == first)["status"], "ended_unknown")

    def test_discovery_unavailable_is_not_idle(self):
        self.process()
        self.cli_tasks()
        self.now += 3
        original = os.scandir
        def denied(path):
            if os.fspath(path) == str(self.proc):
                raise PermissionError(errno.EACCES, "fixture denied")
            return original(path)
        with mock.patch("web.task_monitor.os.scandir", side_effect=denied):
            snapshot = self.monitor.snapshot([])
        self.assertFalse(snapshot["monitor"]["available"])
        self.assertIn("unavailable", snapshot["monitor"]["warning"])
        self.assertEqual(snapshot["tasks"][0]["status"], "unknown")

    def test_unreadable_process_and_missing_boot_warn(self):
        self.process()
        self.links[str(self.proc / "10" / "cwd")] = PermissionError(errno.EACCES, "fixture denied")
        result = self.monitor.snapshot([])
        self.assertFalse(result["monitor"]["available"])
        self.assertIn("unreadable", result["monitor"]["warning"])
        (self.proc / "stat").write_text("cpu 1\n", encoding="ascii")
        self.now += 3
        result = self.monitor.snapshot([])
        self.assertFalse(result["monitor"]["available"])
        self.assertIsNotNone(result["monitor"]["warning"])

    def test_late_managed_identity_removes_prior_external_observation(self):
        argv = self.process()
        self.assertEqual(len(self.cli_tasks()), 1)
        snapshot = self.monitor.snapshot([self.web(pid=10, process_start=100, argv=argv)])
        self.assertEqual(self.cli_tasks(snapshot), [])
        self.assertEqual(len(snapshot["tasks"]), 1)

    def test_history_bound_active_preserved_terminal_decorations_cached(self):
        jobs = [self.web(id=f"job-{i}", status="done", created=i) for i in range(80)]
        jobs.append(self.web(id="old-active", status="queued", created=-1))
        with mock.patch.object(self.monitor, "_decorate", wraps=self.monitor._decorate) as decorate:
            snapshot = self.monitor.snapshot(jobs)
            self.assertEqual(len(snapshot["tasks"]), 61)
            self.assertTrue(any(task["id"] == "old-active" for task in snapshot["tasks"]))
            self.assertEqual(decorate.call_count, 61)
            self.now += 1
            self.monitor.snapshot(jobs)
            self.assertEqual(decorate.call_count, 62)  # only active job redone
        self.assertEqual(self.monitor.task_log("job-0", jobs), {"lines": []})

    def test_app_root_constructor_and_json_schema_preserves_web(self):
        monitor = TaskMonitor(self.root, self.jobs, self.proc, lambda: self.now)
        job = self.web(status="cancelled", rc=-15, ended=1099, custom="preserved")
        snapshot = monitor.snapshot([job])
        task = snapshot["tasks"][0]
        self.assertEqual(set(snapshot), {"tasks", "observed_at", "monitor"})
        self.assertEqual(set(snapshot["monitor"]), {"available", "warning"})
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(task["custom"], "preserved")
        self.assertFalse(task["can_cancel"])
        self.assertEqual(job.get("source"), None)  # caller's object not mutated
        json.dumps(snapshot)


class LogTests(Fixture):
    def test_stdout_regular_target_only_and_stale_project_logs_ignored(self):
        stale = self.log("projects/dragon/train.log", "[v2r] ep 20 step 1000/1000 hm 0.1\n", 999)
        self.process(stdout="pipe:[1234]")
        task = self.cli_tasks()[0]
        self.assertNotIn("percent", task["phase"])
        self.assertEqual(self.monitor.task_log(task["id"], []), {"lines": []})
        current = self.log("logs/current.log", "[v2r] ep 0 step 50/200 hm 0.3\n", 1050)
        self.links[str(self.proc / "10" / "fd" / "1")] = str(current)
        task = self.cli_tasks()[0]
        self.assertEqual(task["phase"]["percent"], 25)
        self.assertEqual(task["progress_updated_at"], 1050)
        self.assertIn("50/200", self.monitor.task_log(task["id"], [])["lines"][0])
        self.assertIsNone(self.monitor.task_log(str(stale), []))
        self.assertIsNone(self.monitor.task_log("../../secret", []))

    def test_foreign_stdout_and_project_path_never_read(self):
        secret = self.base / "secret.log"
        secret.write_text("[v2r] ep 0 step 200/200 hm 0.1\n", encoding="utf-8")
        os.utime(secret, (1050, 1050))
        self.process(stdout=secret)
        task = self.cli_tasks()[0]
        self.assertNotIn("percent", task["phase"])
        self.assertEqual(self.monitor.task_log(task["id"], []), {"lines": []})
        job = self.web(log=str(secret))
        self.assertEqual(self.monitor.task_log(job["id"], [job]), {"lines": []})

    def test_huge_tail_bounded_and_nonregular_or_stale_rejected(self):
        path = self.log("huge.log", "x" * (MAX_LOG_BYTES * 2) + "\nlast one\nlast two\n")
        result = bounded_log_tail(path, 1)
        self.assertEqual(result["lines"], ["last two"])
        self.assertEqual(bounded_log_tail(path, 0)["lines"], [])
        self.assertIsNone(bounded_log_tail(path, min_mtime=1060))
        self.assertIsNone(bounded_log_tail(self.root))
        if hasattr(os, "mkfifo"):
            fifo = self.root / "pipe"
            os.mkfifo(fifo)
            self.assertIsNone(bounded_log_tail(fifo))

    def test_web_log_plus_fresh_stage_logs_status_never_quality_inferred(self):
        self.log("jobs/web-1.log", "[v2r] prepare done\n", 1050)
        self.log("projects/dragon/train.log", "[v2r] ep 0 step 50/200 hm 0.3\n", 1060)
        self.log("projects/dragon/exam.log", "[v2r] EXAM PASSED\n", 999)
        job = self.web(cmd="pipeline", status="done", rc=0, ended=1090)
        task = self.monitor.snapshot([job])["tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["phase"]["key"], "train")
        self.assertEqual(len(self.monitor.task_log(job["id"], [job])["lines"]), 2)
        self.assertNotIn("quality", task)
        self.assertFalse(task["can_cancel"])

    def test_newer_run_logs_do_not_redecorate_old_terminal_or_queued_job(self):
        self.log("projects/dragon/train.log", "[v2r] ep 0 step 100/200 hm 0.1\n", 1080)
        old = self.web(status="done", ended=1070)
        queued = self.web(id="queued", status="queued", started=None)
        for task in self.monitor.snapshot([old, queued])["tasks"]:
            self.assertNotIn("percent", task["phase"])
        self.assertEqual(self.monitor.task_log(old["id"], [old]), {"lines": []})

    def test_success_print_does_not_become_success_on_disappearance(self):
        path = self.log("projects/dragon/train.log", "[v2r] training done in 1 min (200 steps), best val px mean 2.0\n", 1050)
        self.process(stdout=path)
        task = self.cli_tasks()[0]
        self.assertEqual(task["status"], "running")
        target = self.proc / "10"
        self.assertEqual(target.resolve().parent, self.proc.resolve())
        shutil.rmtree(target)
        self.now += 3
        task = self.cli_tasks()[0]
        self.assertEqual(task["status"], "ended_unknown")
        self.assertNotEqual(task["status"], "done")
        self.assertEqual(len(self.monitor.task_log(task["id"], [])["lines"]), 1)

    def test_worker_progress_is_labeled_local_not_global(self):
        self.process(argv=["python", "-m", "v2r.cli", "gen", str(self.projects / "dragon"), "--n", "3000"])
        self.log("projects/dragon/logs/gen_000000.log", "[v2r] 25/300 samples 0.3s/sample\n", 1050)
        self.log("projects/dragon/logs/gen_000300.log", "[v2r] 50/300 samples 0.4s/sample\n", 1051)
        phase = self.cli_tasks()[0]["phase"]
        self.assertEqual((phase["current"], phase["total"]), (50, 300))
        self.assertIn("单个", phase["label"])

    def test_no_stale_training_jsonl_is_read(self):
        self.process()
        self.log("projects/dragon/model/train_log.jsonl", '{"epoch":99,"train_hm":0.1}\n', 1090)
        task = self.cli_tasks()[0]
        self.assertIsNone(task["phase"]["current"])
        self.assertNotIn("percent", task["phase"])
        self.assertEqual(self.monitor.task_log(task["id"], []), {"lines": []})


if __name__ == "__main__":
    unittest.main()
