"""Dry API regression tests; all files are temporary and no real jobs run.

Run from the repository root: python tools/test_run_binding.py
FastAPI/numpy are required. OpenCV is mocked if unavailable; video decoding and
encoding are explicitly mocked even when it is installed.
"""
import asyncio
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SERVER_FILE = REPO / "video2rig" / "web" / "server.py"
if not SERVER_FILE.exists():
    SERVER_FILE = REPO / "web" / "server.py"


def load_server():
    # Import creates a Jobs singleton normally. Suppress its worker before import,
    # and redirect every import-time filesystem side effect to a temporary root.
    with tempfile.TemporaryDirectory() as root:
        cv2_fallback = types.ModuleType("cv2")
        cv2_fallback.IMWRITE_JPEG_QUALITY = 1
        cv2_patch = (mock.patch.dict(sys.modules, {"cv2": cv2_fallback})
                     if importlib.util.find_spec("cv2") is None else mock.patch.dict(sys.modules, {}))
        with cv2_patch, mock.patch.dict(os.environ, {"V2R_ROOT": root}), \
                mock.patch("threading.Thread.start") as start:
            spec = importlib.util.spec_from_file_location("run_binding_test_server", SERVER_FILE)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            start.assert_called_once()
    return module


server = load_server()


class RunBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        paths = {"ROOT": str(self.root)}
        for key, folder in (("PROJ", "projects"), ("INPUTS", "inputs"),
                            ("VIDEOS", "videos"), ("JOBS_DIR", "jobs")):
            path = self.root / folder
            path.mkdir()
            paths[key] = str(path)
        self.queue = mock.Mock()
        self.queue.add.side_effect = lambda project, cmd, args: {
            "project": project, "cmd": cmd, "args": args, "dry_run": True}
        paths["JOBS"] = self.queue
        patcher = mock.patch.multiple(server, **paths)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.project("alpha", reader=True)
        self.project("beta", reader=True)
        self.project("m11101")  # Existing empty project is safe to retry.
        (Path(server.VIDEOS) / "clip.mp4").write_bytes(b"fake video; never decoded")
        (Path(server.INPUTS) / "model.glb").write_bytes(b"fake model")
        subprocess_guard = mock.patch.object(server.subprocess, "Popen", side_effect=AssertionError("No real jobs"))
        subprocess_guard.start()
        self.addCleanup(subprocess_guard.stop)

    def project(self, name, reader=False):
        path = Path(server.PROJ) / name
        path.mkdir(exist_ok=True)
        if reader:
            (path / "model").mkdir(exist_ok=True)
            (path / "model" / "best.pt").write_bytes(b"fake checkpoint")
        return path

    def write_json(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def assert_http(self, status, fn, *args, **kwargs):
        with self.assertRaises(server.HTTPException) as raised:
            fn(*args, **kwargs)
        self.assertEqual(raised.exception.status_code, status)
        return str(raised.exception.detail)

    def job(self, project="alpha", cmd="animate", **args):
        if cmd == "animate":
            args.setdefault("video", "clip.mp4")
        if cmd == "pipeline":
            args.setdefault("model", "model.glb")
        return asyncio.run(server.new_job({"project": project, "cmd": cmd, "args": args}))

    def test_summary_reader_model_name_and_visual_profile(self):
        p = Path(server.PROJ) / "alpha"
        self.write_json(p / "rig.json", {"model": "/inputs/character.glb"})
        self.write_json(p / "prepare_opts.json", {"visual_profile": "soft-clean"})
        result = server.project_summary("alpha")
        self.assertTrue(result["has_reader"])
        self.assertEqual(result["model_name"], "character.glb")
        self.assertEqual(result["model"], "/inputs/character.glb")
        self.assertEqual(result["visual_profile"], "soft-clean")
        self.write_json(p / "studio.json", {"visual_profile": "legacy"})
        self.assertEqual(server.project_summary("alpha")["visual_profile"], "legacy")
        self.assertFalse(server.project_summary("m11101")["has_reader"])
        self.assertEqual(server.project_summary("m11101")["model_name"], "")

    def test_binding_persists_and_inputs_keep_existing_metadata(self):
        self.assertIsNone(server.list_inputs()["videos"][0]["project"])
        result = server.bind_video({"video": "clip.mp4", "project": "m11101"})
        self.assertEqual(result, {"ok": True, "video": "clip.mp4", "project": "m11101", "manual": True})
        bindings = json.loads((self.root / "video_bindings.json").read_text(encoding="utf-8"))
        self.assertEqual(bindings, {"clip.mp4": "m11101"})
        self.assertFalse(list(self.root.glob(".video_bindings-*.tmp")))
        video = server.list_inputs()["videos"][0]
        self.assertEqual(video["project"], "m11101")
        self.assertTrue({"name", "size", "mtime"}.issubset(video))
        self.assertEqual(server.list_inputs()["models"][0]["name"], "model.glb")
        self.queue.add.assert_not_called()

    def test_binding_validation(self):
        self.assert_http(400, server.bind_video, {"video": "../clip.mp4", "project": "alpha"})
        self.assert_http(400, server.bind_video, {"video": "clip.mp4", "project": "../alpha"})
        self.assert_http(404, server.bind_video, {"video": "missing.mp4", "project": "alpha"})
        self.assert_http(404, server.bind_video, {"video": "clip.mp4", "project": "missing"})
        self.assertFalse((self.root / "video_bindings.json").exists())

    def test_concurrent_bindings_do_not_lose_updates(self):
        names = [f"clip_{i}.mp4" for i in range(12)]
        for name in names:
            (Path(server.VIDEOS) / name).write_bytes(b"fake")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(lambda name: server.bind_video({"video": name, "project": "alpha"}), names))
        self.assertEqual(server.video_bindings(), {name: "alpha" for name in names})

    def test_video_routes_and_first_frame_jpeg(self):
        self.assertEqual(server.video_file("clip.mp4").path, str(Path(server.VIDEOS) / "clip.mp4"))
        self.assert_http(400, server.video_file, "../clip.mp4")
        self.assert_http(404, server.video_thumbnail, "missing.mp4")
        capture = mock.Mock()
        frame = object()
        capture.read.return_value = (True, frame)
        encoded = mock.Mock()
        encoded.tobytes.return_value = b"jpeg bytes"
        with mock.patch.object(server.cv2, "VideoCapture", return_value=capture, create=True) as open_video, \
                mock.patch.object(server.cv2, "imencode", return_value=(True, encoded), create=True) as encode:
            response = server.video_thumbnail("clip.mp4")
        open_video.assert_called_once_with(str(Path(server.VIDEOS) / "clip.mp4"))
        capture.read.assert_called_once_with()
        capture.release.assert_called_once_with()
        encode.assert_called_once_with(".jpg", frame, [server.cv2.IMWRITE_JPEG_QUALITY, 85])
        self.assertEqual(response.body, b"jpeg bytes")
        self.assertEqual(response.media_type, "image/jpeg")

    def test_thumbnail_decode_and_encode_failures(self):
        capture = mock.Mock()
        capture.read.return_value = (False, None)
        with mock.patch.object(server.cv2, "VideoCapture", return_value=capture, create=True):
            self.assert_http(400, server.video_thumbnail, "clip.mp4")
        capture.release.assert_called_once_with()
        capture.read.return_value = (True, object())
        with mock.patch.object(server.cv2, "VideoCapture", return_value=capture, create=True), \
                mock.patch.object(server.cv2, "imencode", return_value=(False, None), create=True):
            self.assert_http(500, server.video_thumbnail, "clip.mp4")

    def test_video_symlinks_cannot_escape_video_directory(self):
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"outside")
        link = Path(server.VIDEOS) / "escape.mp4"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("Creating symlinks is unavailable on this system")
        self.assert_http(403, server.video_file, "escape.mp4")
        self.assert_http(403, server.video_thumbnail, "escape.mp4")
        self.assert_http(403, server.bind_video, {"video": "escape.mp4", "project": "alpha"})

    def test_animate_requires_trained_reader(self):
        detail = self.assert_http(400, self.job, "m11101")
        self.assertIn("best.pt", detail)
        self.queue.add.assert_not_called()

    def test_animate_binding_mismatch_requires_explicit_true(self):
        server.bind_video({"video": "clip.mp4", "project": "alpha"})
        for confirmation in (None, False, "true", 1):
            with self.subTest(confirmation=confirmation):
                detail = self.assert_http(409, self.job, "beta", confirm_mismatch=confirmation)
                self.assertIn("alpha", detail)
                self.assertIn("confirm_mismatch", detail)
        self.queue.add.assert_not_called()
        self.assertEqual(self.job("beta", confirm_mismatch=True)["project"], "beta")
        self.assertEqual(self.job("alpha")["project"], "alpha")

    def test_unbound_video_dry_job_and_existing_animate_validation(self):
        self.assertTrue(self.job()["dry_run"])
        self.assert_http(400, self.job, solver="bad")
        result = self.job(solver="temporal")
        self.assertEqual(result["args"]["name"], "clip_temporal")
        self.assert_http(400, self.job, video="missing.mp4")

    def test_pipeline_blocks_each_old_artifact_even_empty_metadata(self):
        for relative in ("rig.json", "model/best.pt", "train_data/meta_000.json"):
            with self.subTest(relative=relative):
                p = self.project("existing_" + relative.replace("/", "_").replace(".", "_"))
                artifact = p / relative
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text("[]", encoding="utf-8")
                detail = self.assert_http(409, self.job, p.name, "pipeline")
                self.assertIn("new project", detail)
        self.queue.add.assert_not_called()

    def test_pipeline_new_and_existing_empty_projects_remain_allowed(self):
        self.assertTrue(self.job("new_project", "pipeline")["dry_run"])
        (Path(server.PROJ) / "m11101" / "prepare.log").write_text("failed before preparing")
        self.assertTrue(self.job("m11101", "pipeline")["dry_run"])
        self.assertEqual(self.queue.add.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
