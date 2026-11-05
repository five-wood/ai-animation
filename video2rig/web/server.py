"""video2rig web dashboard.

  /data/video2rig/.venv/bin/python -m uvicorn web.server:app --host 0.0.0.0 --port 8600   (cwd = code/)

Read-only browsing of every project (reference picture, training samples with their
labels, training curves, exam report, outputs incl. 3D preview) plus a small job queue
to upload a model / video and run prepare, gen, train, exam, animate.
"""
import os
import re
import io
import sys
import json
import glob
import time
import shutil
import signal
import tempfile
import threading
import subprocess
import uuid
from functools import lru_cache

import numpy as np
import cv2
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE)
from v2r.viz import draw_kps, composite_bg, put_title  # noqa: E402
from web.project_store import ProjectStore  # noqa: E402
from web.task_monitor import TaskMonitor, read_process_identity, bounded_log_tail  # noqa: E402

ROOT = os.environ.get("V2R_ROOT", "/data/video2rig")
PROJ = os.path.join(ROOT, "projects")
INPUTS = os.path.join(ROOT, "inputs")
VIDEOS = os.path.join(ROOT, "videos")
JOBS_DIR = os.path.join(ROOT, "jobs")
PY = os.path.join(ROOT, ".venv", "bin", "python")
for d in (PROJ, INPUTS, VIDEOS, JOBS_DIR):
    os.makedirs(d, exist_ok=True)

app = FastAPI(title="video2rig")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")
VIDEO_BINDINGS_LOCK = threading.Lock()
PROJECT_STORE = ProjectStore(PROJ)
TASK_MONITOR = TaskMonitor(PROJ, JOBS_DIR)
_STORE_ROOT = PROJ
_MONITOR_ROOT = PROJ


def project_store():
    global PROJECT_STORE, _STORE_ROOT
    if _STORE_ROOT != PROJ:
        PROJECT_STORE, _STORE_ROOT = ProjectStore(PROJ), PROJ
    return PROJECT_STORE


def task_monitor():
    global TASK_MONITOR, _MONITOR_ROOT
    if _MONITOR_ROOT != PROJ:
        TASK_MONITOR, _MONITOR_ROOT = TaskMonitor(PROJ, JOBS_DIR), PROJ
    return TASK_MONITOR


def task_snapshot():
    return task_monitor().snapshot(JOBS.list())


@app.on_event('startup')
def warm_project_summaries():
    # Pay the one-time metadata parse before readiness, never on first navigation.
    project_store().list(lambda n: bool(NAME_RE.fullmatch(n)) and not n.startswith('.'))


def safe_name(n):
    if not n or not NAME_RE.match(n) or n.startswith("."):
        raise HTTPException(400, f"bad name: {n}")
    return n


def proj_dir(p):
    d = os.path.join(PROJ, safe_name(p))
    if not os.path.isdir(d):
        raise HTTPException(404, f"no project {p}")
    return d


def inside(base, path):
    base, path = os.path.realpath(base), os.path.realpath(path)
    if not (path == base or path.startswith(base + os.sep)):
        raise HTTPException(403, "outside project")
    return path


def jload(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def read_jsonl(path):
    out = []
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


# ----------------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


# ----------------------------------------------------------------------------- projects
def project_summary(name):
    proj_dir(name)
    return project_store().summary(name)


@app.get("/api/projects")
def projects():
    return project_store().list(lambda n: bool(NAME_RE.fullmatch(n)) and not n.startswith('.'))


@app.get("/api/projects/{p}")
def project(p: str):
    d = proj_dir(p)
    s = project_summary(p)
    s["rig"] = jload(os.path.join(d, "rig.json"))
    s["studio"] = jload(os.path.join(d, "studio.json"))
    s["prepare_opts"] = jload(os.path.join(d, "prepare_opts.json"))
    return s


@app.get("/api/projects/{p}/file/{path:path}")
def project_file(p: str, path: str):
    d = proj_dir(p)
    f = inside(d, os.path.join(d, path))
    if not os.path.isfile(f):
        raise HTTPException(404)
    media = None
    if f.endswith(".glb"):
        media = "model/gltf-binary"
    return FileResponse(f, media_type=media, filename=os.path.basename(f) if f.endswith(
        (".fbx", ".glb", ".bvh", ".blend", ".npz")) else None)


# ----------------------------------------------------------------------------- training samples
@lru_cache(maxsize=16)
def _meta(p, mtime_key):
    d = os.path.join(PROJ, p, "train_data")
    meta = []
    for f in sorted(glob.glob(os.path.join(d, "meta_*.json"))):
        meta += jload(f, [])
    meta.sort(key=lambda r: r["id"])
    return meta


def meta_for(p):
    d = os.path.join(PROJ, p, "train_data")
    files = glob.glob(os.path.join(d, "meta_*.json"))
    key = (len(files), max([os.path.getmtime(f) for f in files] or [0]))
    return _meta(p, key)


@app.get("/api/projects/{p}/samples")
def samples(p: str, offset: int = 0, limit: int = 48, fast: str = "all"):
    proj_dir(p)
    meta = meta_for(p)
    if fast == "fast":
        meta = [r for r in meta if r.get("fast")]
    elif fast == "slow":
        meta = [r for r in meta if not r.get("fast")]
    items = [{"id": r["id"], "fast": r.get("fast", False)} for r in meta[offset:offset + limit]]
    return {"total": len(meta), "items": items}


def _sample_record(p, sid):
    for r in meta_for(p):
        if r["id"] == sid:
            return r
    raise HTTPException(404, "no sample")


def _jpeg(img, q=85):
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return Response(enc.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/projects/{p}/sample/{sid}.jpg")
def sample_image(p: str, sid: int, labels: int = 1, aug: int = 0, w: int = 640):
    """Both views side by side; labels drawn; aug=1 shows the training-time Seedance-like augmentation."""
    d = proj_dir(p)
    rig = jload(os.path.join(d, "rig.json"))
    studio = jload(os.path.join(d, "studio.json"))
    rec = _sample_record(p, sid)
    tiles = []
    for vi, c in enumerate(studio["cams"]):
        rgba = cv2.imread(os.path.join(d, "train_data", rec[c["name"]]["img"]), cv2.IMREAD_UNCHANGED)
        if rgba is None:
            raise HTTPException(404, "image missing")
        if aug:
            import random
            from v2r.data import compose
            profile = jload(os.path.join(d, "prepare_opts.json"), {}).get("visual_profile", "legacy")
            img = compose(rgba, random.Random(sid * 7 + vi + aug * 1000), True, profile=profile)
        else:
            img = composite_bg(rgba)
        if labels:
            img = draw_kps(img, np.array(rec[c["name"]]["uvz"]), rig, vis=np.array(rec[c["name"]]["vis"]), r=4)
        put_title(img, f"#{sid} {c['name']}{'  FAST' if rec.get('fast') else ''}")
        tiles.append(img)
    img = np.concatenate(tiles, 1)
    s = w * len(tiles) / img.shape[1]
    if s < 1:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return _jpeg(img)


@app.get("/api/projects/{p}/thumb/{sid}.jpg")
def sample_thumb(p: str, sid: int):
    return sample_image(p, sid, labels=0, aug=0, w=180)


# ----------------------------------------------------------------------------- training / exam / outputs
@app.get("/api/projects/{p}/train")
def train_log(p: str):
    d = proj_dir(p)
    log = read_jsonl(os.path.join(d, "model", "train_log.jsonl"))
    steps = []
    tl = os.path.join(d, "train.log")
    if os.path.exists(tl):
        for line in open(tl, errors="ignore"):
            m = re.search(r"ep (\d+) step (\d+)/(\d+) hm ([\d.]+) depth ([\d.]+) .*?([\d.]+)min", line)
            if m:
                steps.append({"ep": int(m[1]), "step": int(m[2]), "total": int(m[3]), "hm": float(m[4]),
                              "depth": float(m[5]), "min": float(m[6])})
    ck = os.path.join(d, "model", "best.pt")
    return {"epochs": log, "steps": steps,
            "best_mtime": os.path.getmtime(ck) if os.path.exists(ck) else None,
            "running": any(j["project"] == p and j["status"] == "running" and j["cmd"] in ("train", "pipeline")
                           for j in JOBS.list())}


@app.get("/api/projects/{p}/exam")
def exam(p: str):
    d = proj_dir(p)
    rep = jload(os.path.join(d, "exam", "exam_report.json"))
    if not rep:
        raise HTTPException(404, "no exam yet")
    rep["has_video"] = os.path.exists(os.path.join(d, "out", "exam", "lab.mp4"))
    return rep


@app.get("/api/projects/{p}/outputs")
def outputs(p: str):
    d = proj_dir(p)
    res = []
    for o in sorted(glob.glob(os.path.join(d, "out", "*"))):
        if not os.path.isdir(o):
            continue
        n = os.path.basename(o)
        files = sorted(os.path.basename(f) for f in glob.glob(os.path.join(o, "*")) if os.path.isfile(f))
        summary = jload(os.path.join(o, "summary.json"))
        if summary and summary.get("status") == "needs_review":
            # Files from an older successful run may remain on disk; don't offer them as current.
            files = [f for f in files if f in ("check.json", "diagnostics.json", "diagnostic.mp4", "reading.npz", "summary.json")]
        res.append({"name": n, "files": files, "summary": summary,
                    "diagnostics": jload(os.path.join(o, "diagnostics.json")),
                    "stability": jload(os.path.join(o, "stability.json")),
                    "check": {k: v for k, v in (jload(os.path.join(o, "check.json"), {}) or {}).items()
                              if not isinstance(v, list)},
                    "mtime": os.path.getmtime(o)})
    res.sort(key=lambda r: -r["mtime"])
    return res


@app.get("/api/projects/{p}/out/{name}/curve")
def out_curve(p: str, name: str):
    """Per-frame check / reprojection info for one output (for plotting)."""
    d = proj_dir(p)
    o = os.path.join(d, "out", safe_name(name))
    chk = jload(os.path.join(o, "check.json"), {}) or {}
    res = {"residual": chk.get("frame_residual_rel"), "bonelen": chk.get("frame_bonelen_err"),
           "bad": chk.get("bad_mask")}
    return res


# ----------------------------------------------------------------------------- inputs / uploads
def video_bindings():
    """User-declared associations only; these do not identify video contents."""
    with VIDEO_BINDINGS_LOCK:
        bindings = jload(os.path.join(ROOT, "video_bindings.json"), {})
    return bindings if isinstance(bindings, dict) else {}


def video_path(name):
    path = inside(VIDEOS, os.path.join(VIDEOS, safe_name(name)))
    if not os.path.isfile(path):
        raise HTTPException(404, f"no video {name}")
    return path


@app.get("/api/inputs")
def list_inputs():
    def ls(d):
        return [{"name": f, "size": os.path.getsize(os.path.join(d, f)), "mtime": os.path.getmtime(os.path.join(d, f))}
                for f in sorted(os.listdir(d)) if os.path.isfile(os.path.join(d, f))]
    videos = ls(VIDEOS)
    bindings = video_bindings()
    for video in videos:
        video["project"] = bindings.get(video["name"])
    return {"models": ls(INPUTS), "videos": videos}


@app.get("/api/videos/{name}/file")
def video_file(name: str):
    return FileResponse(video_path(name))


@app.get("/api/videos/{name}/thumbnail.jpg")
def video_thumbnail(name: str):
    path = video_path(name)
    cap = cv2.VideoCapture(path)
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        raise HTTPException(400, "Cannot decode the video's first frame")
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(500, "Cannot encode video thumbnail")
    return Response(encoded.tobytes(), media_type="image/jpeg")


@app.post("/api/video-binding")
def bind_video(body: dict):
    """Record a manual association, not an automatically verified identity match."""
    video = safe_name(body.get("video", ""))
    project = safe_name(body.get("project", ""))
    video_path(video)
    inside(PROJ, proj_dir(project))
    path = os.path.join(ROOT, "video_bindings.json")
    with VIDEO_BINDINGS_LOCK:
        bindings = jload(path, {})
        if not isinstance(bindings, dict):
            bindings = {}
        bindings[video] = project
        # Keep readers from observing partially written JSON and serialize read-modify-write.
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=ROOT,
                                             prefix=".video_bindings-", suffix=".tmp", delete=False) as f:
                temp_path = f.name
                json.dump(bindings, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
    return {"ok": True, "video": video, "project": project, "manual": True}


@app.post("/api/upload")
async def upload(kind: str = Form(...), file: UploadFile = File(...)):
    if kind not in ("model", "video"):
        raise HTTPException(400, "kind must be model or video")
    name = safe_name(os.path.basename(file.filename))
    dst = os.path.join(INPUTS if kind == "model" else VIDEOS, name)
    with open(dst + ".part", "wb") as f:
        while True:
            chunk = await file.read(1 << 22)
            if not chunk:
                break
            f.write(chunk)
    os.replace(dst + ".part", dst)
    return {"ok": True, "path": dst, "size": os.path.getsize(dst)}


# ----------------------------------------------------------------------------- jobs
class Jobs:
    """Tiny persistent job queue: one GPU-heavy job at a time, others wait."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        for f in glob.glob(os.path.join(JOBS_DIR, "*.json")):
            j = jload(f)
            if j:
                if j["status"] in ("running", "queued"):
                    j["status"] = "interrupted"
                self.jobs[j["id"]] = j
        self.proc = None
        if os.environ.get('V2R_DISABLE_WORKER') != '1':
            threading.Thread(target=self._loop, daemon=True).start()

    def _save(self, j):
        with open(os.path.join(JOBS_DIR, j["id"] + ".json"), "w") as f:
            json.dump(j, f, indent=1)

    def list(self):
        with self.lock:
            return sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)

    def add(self, project, cmd, args):
        jid = time.strftime("%Y%m%d-%H%M%S") + f"-{cmd}-{project}-" + uuid.uuid4().hex[:8]
        j = {"id": jid, "project": project, "cmd": cmd, "args": args, "status": "queued",
             "created": time.time(), "started": None, "ended": None, "rc": None,
             "log": os.path.join(JOBS_DIR, jid + ".log")}
        with self.lock:
            if any(row['project'] == project and row['status'] in ('queued', 'running') for row in self.jobs.values()):
                raise HTTPException(409, '当前项目已有排队或运行中的任务，请等待完成。')
            self.jobs[jid] = j
            self._save(j)
        return j

    def cancel(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            if not j:
                raise HTTPException(404)
            if j["status"] == "queued":
                j["status"] = "cancelled"
                self._save(j)
            elif j['status'] == 'running':
                j['cancel_requested'] = True
                self._save(j)
                if self.proc is not None and j.get('pid') == self.proc.pid:
                    try:
                        os.killpg(self.proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        return j

    def _argv(self, j):
        p = os.path.join(PROJ, j["project"])
        a = j["args"]
        if j["cmd"] == "pipeline":
            return ["bash", os.path.join(CODE, "run_all.sh"), a["model"], j["project"]] + a.get("extra", [])
        base = [PY, "-m", "v2r.cli", j["cmd"]]
        if j["cmd"] == "prepare":
            argv = base + [a["model"], p]
            if a.get("facing"):
                argv += ["--facing=" + a["facing"]]
            if a.get("up"):
                argv += ["--up=" + a["up"]]
            if a.get("procedural_material"):
                argv += ["--procedural-material"]
            argv += ["--visual-profile", a.get("visual_profile", "legacy")]
            return argv
        if j["cmd"] == "gen":
            return base + [p, "--n", str(a.get("n", 3000)), "--workers", str(a.get("workers", 10))]
        if j["cmd"] == "train":
            return base + [p, "--epochs", str(a.get("epochs", 20))]
        if j["cmd"] == "exam":
            return base + [p]
        if j["cmd"] == "stabilize":
            return base + [p, a["source"], "--name", a["name"], "--cutoff", str(a.get("cutoff", 1.5))]
        if j["cmd"] == "animate":
            argv = base + [p, a["video"], "--name", a.get("name") or os.path.splitext(os.path.basename(a["video"]))[0]]
            if a.get("swap"):
                argv += ["--swap"]
            if a.get("force"):
                argv += ["--force"]
            argv += ["--solver", a.get("solver", "legacy")]
            return argv
        raise ValueError(j["cmd"])

    def _loop(self):
        while True:
            time.sleep(1.0)
            with self.lock:
                nxt = [j for j in self.jobs.values() if j["status"] == "queued"]
                busy = any(j["status"] == "running" for j in self.jobs.values())
            if busy or not nxt:
                continue
            observed = task_monitor().snapshot([])
            # Do not launch another heavy job while an external CLI task is observed.
            if not observed['monitor']['available'] or any(t.get('source') == 'cli' and t.get('status') in ('running', 'unknown') for t in observed['tasks']):
                continue
            j = min(nxt, key=lambda j: j["created"])
            try:
                argv = self._argv(j)
            except Exception as e:
                j["status"], j["error"] = "failed", str(e)
                self._save(j)
                continue
            os.makedirs(os.path.join(PROJ, j["project"]), exist_ok=True)
            with self.lock:
                if j['status'] != 'queued':
                    continue
                j["status"], j["started"], j["argv"] = "running", time.time(), argv
                self._save(j)
            try:
                with open(j['log'], 'w') as logf:
                    proc = subprocess.Popen(argv, cwd=CODE, stdout=logf, stderr=subprocess.STDOUT,
                                            start_new_session=True, env={**os.environ, 'PYTHONUNBUFFERED': '1'})
                    with self.lock:
                        self.proc = proc
                        j['pid'] = proc.pid
                        j['process_start'] = read_process_identity(proc.pid)
                        self._save(j)
                        if j.get('cancel_requested'):
                            try:
                                os.killpg(proc.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                    rc = proc.wait()
            except OSError as e:
                with self.lock:
                    j['status'], j['ended'], j['error'] = 'failed', time.time(), str(e)
                    self.proc = None
                    self._save(j)
                continue
            project_store().invalidate(j['project'])
            with self.lock:
                j["rc"], j["ended"] = rc, time.time()
                text = open(j["log"], errors="replace").read()[-12000:]
                needs_review = rc != 0 and "[v2r] NEEDS_REVIEW:" in text
                j["status"] = "done" if rc == 0 else ("needs_review" if needs_review else ("cancelled" if rc < 0 else "failed"))
                if needs_review:
                    j["result_name"] = j["args"].get("name") or os.path.splitext(os.path.basename(j["args"]["video"]))[0]
                self.proc = None
                self._save(j)


JOBS = Jobs()


@app.get("/api/jobs")
def jobs():
    return JOBS.list()[:60]


@app.get('/api/tasks')
def tasks():
    return task_snapshot()


@app.get('/api/tasks/{task_id}/log')
def task_log(task_id: str, tail: int = 200):
    if task_id.startswith('cli:'):
        response = task_monitor().task_log(task_id, JOBS.list(), tail=max(1, min(tail, 1000)))
        if response is None:
            raise HTTPException(404, '任务不在当前观测记录中')
        return response
    response = task_monitor().task_log(task_id, JOBS.list(), tail=max(1, min(tail, 1000)))
    if response is None:
        raise HTTPException(404, '任务不存在')
    return response


@app.get('/api/capabilities')
def capabilities():
    return {'version': 2, 'unified_tasks': True, 'project_summary_cache': True,
            'safe_retry': 'restore-inputs-with-new-output-name', 'cli_control': False}


@app.post("/api/jobs")
async def new_job(body: dict):
    project = safe_name(body.get("project", ""))
    cmd = body.get("cmd")
    if cmd not in ("pipeline", "prepare", "gen", "train", "exam", "animate", "stabilize"):
        raise HTTPException(400, "unknown cmd")
    args = body.get("args", {}) or {}
    if not isinstance(args, dict):
        raise HTTPException(400, 'args must be an object')
    observed = task_snapshot()
    if any(t.get('project') == project and t.get('status') in ('queued', 'running', 'unknown') for t in observed['tasks']):
        raise HTTPException(409, '当前项目已有运行或排队任务，请先查看任务中心。')
    args = dict(args)
    for k in ("model", "video"):
        if k in args and args[k]:
            base = INPUTS if k == "model" else VIDEOS
            v = args[k]
            if not os.path.isabs(v):
                v = os.path.join(base, safe_name(v))
            if not os.path.isfile(v):
                raise HTTPException(400, f"{k} not found: {v}")
            args[k] = v
    if cmd in ("pipeline", "prepare") and not args.get("model"):
        raise HTTPException(400, "model required")
    if cmd == "animate" and not args.get("video"):
        raise HTTPException(400, "video required")
    if cmd == "pipeline":
        d = inside(PROJ, os.path.join(PROJ, project))
        if (os.path.exists(os.path.join(d, "rig.json"))
                or os.path.exists(os.path.join(d, "model", "best.pt"))
                or glob.glob(os.path.join(d, "train_data", "meta_*.json"))):
            raise HTTPException(409, "Project already contains a rig, trained reader, or training metadata; "
                                "choose a new project name to avoid reusing an old model")
    if cmd == "animate":
        if args.get('name'):
            args['name'] = safe_name(args['name'])
        d = proj_dir(project)
        if not os.path.isfile(os.path.join(d, "model", "best.pt")):
            raise HTTPException(400, "Project has no trained reader (model/best.pt); train this project "
                                "before running animate, or select a project with a trained reader")
        bound_project = video_bindings().get(os.path.basename(args["video"]))
        if bound_project and bound_project != project and args.get("confirm_mismatch") is not True:
            raise HTTPException(409, f"Video is manually associated with project '{bound_project}', "
                                f"not '{project}'; verify the project and set args.confirm_mismatch=true "
                                "to proceed. Manual associations do not automatically verify video identity")
        if args.get("solver", "legacy") not in ("legacy", "temporal"):
            raise HTTPException(400, "Unknown solver")
        default_name = os.path.splitext(os.path.basename(args['video']))[0]
        output_name = safe_name(args.get('name') or default_name + ('_temporal' if args.get('solver') == 'temporal' else ''))
        output_exists = os.path.exists(os.path.join(PROJ, project, 'out', output_name))
        if output_exists and args.get('solver') == 'temporal':
            raise HTTPException(409, '实验结果名称已存在，请使用新名称。')
        if output_exists and args.get('overwrite') is not True:
            raise HTTPException(409, '结果名称已存在。请使用新名称保留旧结果，或明确确认覆盖。')
        args['name'] = output_name
    if cmd not in ("pipeline", "prepare"):
        proj_dir(project)
    if cmd == "stabilize":
        src = safe_name(args.get("source", ""))
        args["name"] = safe_name(args.get("name") or src + "_stable")
        if args["name"] == src:
            raise HTTPException(400, "Please choose a new name; the original is preserved")
        if not os.path.isfile(os.path.join(PROJ, project, "out", src, "motion.npz")):
            raise HTTPException(400, "No solved motion for this result")
        if os.path.exists(os.path.join(PROJ, project, "out", args["name"])):
            raise HTTPException(400, "Output name already exists; choose another name")
        try:
            cutoff = float(args.get("cutoff", 1.5))
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid cutoff")
        if not 0.25 <= cutoff <= 6:
            raise HTTPException(400, "Cutoff must be between 0.25 and 6 Hz")
        args["cutoff"] = cutoff
    return JOBS.add(project, cmd, args)


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    return JOBS.cancel(jid)


@app.get("/api/jobs/{jid}/log")
def job_log(jid: str, tail: int = 200):
    j = {x["id"]: x for x in JOBS.list()}.get(jid)
    if not j:
        raise HTTPException(404)
    result = bounded_log_tail(j['log'], tail=max(1, min(tail, 1000)))
    return {'lines': result['lines'] if result else []}


@app.get("/api/project-log/{p}/{which}")
def project_log(p: str, which: str, tail: int = 120):
    d = proj_dir(p)
    if which not in ("gen", "train", "exam", "prepare", "run_all"):
        raise HTTPException(400)
    f = os.path.join(d, which + ".log")
    result = bounded_log_tail(f, tail=max(1, min(tail, 1000)))
    return {'lines': result['lines'] if result else []}


# ----------------------------------------------------------------------------- system
@app.get("/api/system")
def system():
    gpu = []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
        for line in out.strip().splitlines():
            n, u, mu, mt, t = [x.strip() for x in line.split(",")]
            gpu.append({"name": n, "util": int(u), "mem_used": int(mu), "mem_total": int(mt), "temp": int(t)})
    except Exception:
        pass
    du = shutil.disk_usage(ROOT)
    return {"gpu": gpu, "disk_free_gb": du.free / 1e9, "time": time.time()}
