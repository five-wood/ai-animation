"""Read-only, stdlib-only discovery of video2rig CLI work and real log progress.

Pass the *projects directory* as project_root (passing its application parent is
also supported). No subprocess is started, signalled, adopted or submitted here.
Process identity is (pid, Linux stat field 22), never pid alone. A missing CLI
process has an unknown outcome, regardless of old artifacts or reassuring logs.
"""
import copy
import errno
import math
import os
import re
import stat
import threading
import time

COMMANDS = frozenset(("prepare", "gen", "train", "exam", "animate", "stabilize"))
LABELS = {"prepare": "准备骨架", "gen": "生成训练数据", "train": "训练识别器",
          "exam": "考核识别器", "animate": "生成动画", "stabilize": "稳定化",
          "pipeline": "完整流程", "unknown": "等待阶段依据", "exam_render": "渲染考核片段",
          "exam_grade": "考核评分", "read": "读取关键点", "check": "检查双视角一致性",
          "solve": "求解动作", "export": "导出动画", "preview": "生成预览", "review": "需要人工检查",
          "keyframes": "生成候选关键帧"}
UNITS = {"steps": "步", "epochs": "轮", "samples": "个", "frames": "帧"}
SCAN_TTL = 2.0
RETENTION_TTL = 3600.0
MAX_RETAINED = 256
MAX_WEB_HISTORY = 60
TERMINAL_CACHE_TTL = 30.0
MAX_PROCESSES = 16384
MAX_PROC_BYTES = 65536
MAX_LOG_BYTES = 262144
MAX_TAIL = 1000
MAX_LOG_FILES = 32
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?\Z", re.I)


def parse_proc_stat(text):
    """Parse Linux stat safely even when comm contains spaces or parentheses."""
    try:
        left, right = text.index("("), text.rindex(")")
        pid = int(text[:left].strip())
        fields = text[right + 1:].split()  # starts at field 3 (state)
        ppid, start = int(fields[1]), int(fields[19])
        if pid <= 0 or ppid < 0 or start < 0 or len(fields[0]) != 1:
            return None
        return {"pid": pid, "ppid": ppid, "state": fields[0], "starttime": start}
    except (ValueError, IndexError):
        return None


def parse_cmdline(data):
    """Decode NUL-separated argv; reject a truncated (non-NUL-terminated) read."""
    if not data or not data.endswith(b"\0"):
        return []
    return [part.decode("utf-8", "surrogateescape") for part in data[:-1].split(b"\0")]


def _read_bounded(path, limit=MAX_PROC_BYTES):
    with open(path, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise OSError(errno.EFBIG, "monitor read bound exceeded", path)
    return data


def read_process_identity(pid, proc_root="/proc"):
    """Return raw start ticks (JSON integer) for the server's recorded child pid."""
    try:
        parsed = parse_proc_stat(_read_bounded(os.path.join(proc_root, str(int(pid)), "stat")).decode("utf-8", "replace"))
        return parsed["starttime"] if parsed and parsed["pid"] == int(pid) else None
    except (OSError, ValueError, TypeError):
        return None


def _inside(base, path):
    try:
        return os.path.commonpath((os.path.realpath(base), os.path.realpath(path))) == os.path.realpath(base)
    except (ValueError, OSError):
        return False


def _absolute(path, cwd):
    return os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))


def _option(argv, name):
    for i, item in enumerate(argv):
        if item.startswith(name + "="):
            return item[len(name) + 1:]
        if item == name and i + 1 < len(argv):
            return argv[i + 1]
    return None


# Only options actually accepted by cli.py: used to locate positional projects
# without accidentally treating --geometry-from or --name as the project.
_FLAGS = frozenset(("--procedural-material", "--generic-limits", "--fist-points", "--offaxis-points",
                    "--drop-props", "--no-drop-props", "--fresh", "--contact-poses", "--hidden-empty",
                    "--reuse", "--clean", "--regrade", "--swap", "--force"))
_VALUES = frozenset(("--facing", "--up", "--visual-profile", "--geometry-from", "--limits", "--fill",
                     "--min-seg", "--keep-bones", "--panel-w", "--panel-h", "--n", "--offset", "--workers",
                     "--seed", "--fast-frac", "--samples", "--visibility", "--epochs", "--batch", "--lr",
                     "--img", "--backbone", "--steps", "--smear", "--dense", "--frames", "--style", "--name",
                     "--cutoff", "--split", "--fps", "--max-frames", "--solver", "--calib", "--tta"))


def _positionals(argv):
    result, i = [], 0
    while i < len(argv):
        item = argv[i]
        if item == "--":
            return result + argv[i + 1:]
        if item in _FLAGS:
            i += 1
        elif item.split("=", 1)[0] in _VALUES:
            i += 1 if "=" in item else 2
        elif item.startswith("-"):
            return []
        else:
            result.append(item)
            i += 1
    return result


def classify_argv(argv, cwd, project_root, code_roots):
    """Pure strict classifier; no substring matching, shells -c, or other modules.

    Returns project/cmd/args/argv for a real Python module invocation or an exact
    known-code run_all.sh. Relative paths are resolved using the process cwd.
    """
    if not argv or not cwd or not _inside_any(code_roots, cwd):
        return None
    name = os.path.basename(argv[0])
    cmd, rest, pipeline = None, [], False
    if _PYTHON.fullmatch(name):
        i = 1
        while i < len(argv) and argv[i] in ("-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO"):
            i += 1
        if argv[i:i + 2] != ["-m", "v2r.cli"] or i + 2 >= len(argv):
            return None
        cmd, rest = argv[i + 2], argv[i + 3:]
        if cmd not in COMMANDS:
            return None
        pos = _positionals(rest)
        project_index = 1 if cmd == "prepare" else 0
        minimum = 2 if cmd in ("prepare", "animate", "stabilize") else 1
        if len(pos) != minimum:
            return None
        path = _absolute(pos[project_index], cwd)
    elif name in ("bash", "bash.exe"):
        i = 1
        if i < len(argv) and argv[i] == "--":
            i += 1
        if i + 2 >= len(argv):
            return None
        script = _absolute(argv[i], cwd)
        if script not in {os.path.realpath(os.path.join(root, "run_all.sh")) for root in code_roots}:
            return None
        project = argv[i + 2]
        if not _NAME.fullmatch(project):
            return None
        path = os.path.realpath(os.path.join(project_root, project))
        cmd, rest, pos, pipeline = "pipeline", argv[i + 1:], argv[i + 1:], True
    else:
        return None
    base = os.path.realpath(project_root)
    # A project is exactly one direct child, including for not-yet-created prepare.
    if os.path.dirname(path) != base or not _inside(base, path) or not _NAME.fullmatch(os.path.basename(path)):
        return None
    args = {"argv": list(rest)}
    for option in ("epochs", "steps", "n", "workers", "name", "style"):
        value = _option(rest, "--" + option)
        if value is not None:
            args[option] = value
    if cmd == "prepare" or pipeline:
        args["model"] = pos[0]
    if cmd == "animate":
        args["video"] = pos[1]
    if cmd == "stabilize":
        args["source"] = pos[1]
    return {"project": os.path.basename(path), "cmd": cmd, "args": args, "argv": list(argv)}


def _inside_any(roots, path):
    return any(_inside(root, path) for root in roots)


def _phase(key, current=None, total=None, unit=None, label=None):
    phase = {"key": key, "label": label or LABELS.get(key, key.replace("_", " ").capitalize()),
             "current": current, "total": total, "unit": UNITS.get(unit, unit)}
    # No guessed denominators, phase-count percentages or clamping invalid data.
    if (isinstance(current, (int, float)) and isinstance(total, (int, float))
            and math.isfinite(current) and math.isfinite(total) and total > 0 and 0 <= current <= total):
        phase["percent"] = 100.0 * current / total
    return phase


def parse_progress(lines, command, args=None):
    """Pure parser for genuine cli/train/pipeline print markers, never outcomes.

    ``ep N`` and ``== epoch N:`` in train.py are zero-based. The former is
    context only; the latter explicitly marks completion of epoch N. Optimiser
    step x/y takes precedence: epochs may be overridden by --steps.
    """
    phase = _phase(command if command != "pipeline" else "unknown")
    evidence, step_mode = None, False
    args = args or {}
    lines = list(lines)
    # A current invocation's explicit header is a boundary in append-only
    # stdout logs. Never carry an older run's counters through a new header.
    boundaries = [i for i, line in enumerate(lines) if "[v2r] train items=" in line]
    if boundaries and command in ("train", "pipeline"):
        lines = lines[boundaries[-1]:]
    for raw in lines:
        line = raw.strip()
        if "[v2r]" not in line:
            continue
        match = re.search(r"\bep\s+(\d+)\s+step\s+(\d+)/(\d+)\b", line)
        if match and command in ("train", "pipeline"):
            phase = _phase("train", int(match[2]), int(match[3]), "steps")
            evidence, step_mode = line, True
            continue
        if re.search(r"\d+ optimiser steps \(\d+ epochs max\)", line):
            step_mode = True
            if command in ("train", "pipeline"):
                phase, evidence = _phase("train"), line
            continue
        match = re.search(r"\bepoch\s+(\d+)\s*/\s*(\d+)\b", line, re.I)
        if match and command in ("train", "pipeline") and not step_mode:
            phase, evidence = _phase("train", int(match[1]), int(match[2]), "epochs"), line
            continue
        match = re.search(r"== epoch (\d+):", line)
        if match and command in ("train", "pipeline"):
            if not step_mode:
                # Completion is explicit; a CLI --epochs argument alone is not
                # enough to invent a log denominator or imply successful work.
                phase = _phase("train", int(match[1]) + 1, None, "epochs")
            evidence = line
            continue
        match = re.search(r"\b(\d+)/(\d+) samples\b", line)
        if match and command in ("gen", "pipeline"):
            phase = _phase("gen", int(match[1]), int(match[2]), "samples", "生成训练数据（单个工作进程）")
            evidence = line
            continue
        match = re.search(r"\bkeyframes stage (\d+)/(\d+) (\w+)", line)
        if match and command == "keyframes":
            label = {"contacts": "判断着地", "candidates": "抽取候选关键帧", "ladder": "计算过渡键阶梯"}.get(match[3], "生成候选关键帧")
            phase, evidence = _phase("keyframes", int(match[1]), int(match[2]), None, label), line
            continue
        match = re.search(r"\bmotion frame (\d+)/(\d+)\b", line)
        if match and command == "exam":
            phase, evidence = _phase("exam_render", int(match[1]), int(match[2]), "frames", "渲染考核片段"), line
            continue
        # Markers say what was observed, not that the whole job succeeded.
        key = None
        if "NEEDS_REVIEW:" in line:
            key = "review"
        elif "EXAM" in line or "joint error mean" in line:
            key = "exam" if command == "pipeline" else "exam_grade"
        elif "training done" in line or "train items=" in line:
            key = "train"
        elif "labelled samples" in line or "gen:" in line or "gen done" in line:
            key = "gen" if command in ("gen", "pipeline") else None
        elif "prepare done" in line or "reference picture for Seedance:" in line or "bones=" in line:
            key = "prepare"
        elif "temporal it " in line or "solve stage " in line or "down-weighted" in line:
            key = "solve"
        elif "solved in" in line or "keyed " in line:
            key = "export"
        elif "exported FBX/GLB/BVH" in line or "export done" in line:
            key = "preview"
        elif "joints read in" in line or "check: two-view" in line or "frame-0 calibration:" in line:
            key = "check"
        elif re.search(r"\[v2r\] video .* frames @", line):
            key = "read"
        elif "stabilized " in line:
            key = "stabilize"
        elif "done in" in line:
            evidence = line
            if command == "keyframes" and "keyframes done" in line:
                phase = _phase("keyframes", None, None, None, "候选关键帧已生成")
                continue
        allowed = ({"prepare"} if command == "prepare" else {"gen"} if command == "gen" else
                   {"train"} if command == "train" else {"stabilize", "export", "preview", "review"} if command == "stabilize" else
                   {"exam_render", "exam_grade", "read", "check", "solve", "export", "preview", "review"} if command == "exam" else
                   {"read", "check", "solve", "export", "preview", "review"} if command == "animate" else None)
        if key is not None and (allowed is None or key in allowed):
            if phase["key"] != key or phase["current"] is None:
                phase = _phase(key)
            evidence = line
    return {"phase": phase, "evidence": evidence}


def bounded_log_tail(path, tail=200, max_bytes=MAX_LOG_BYTES, min_mtime=None):
    """Tail only a regular file, with bounded bytes/lines and no FIFO blocking.

    Opening is nonblocking and no-follow when the platform supports it; fstat
    validates again after open. Return None for missing/unsafe/stale paths.
    """
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or (min_mtime is not None and info.st_mtime < min_mtime):
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_ino != info.st_ino
                    or opened.st_dev != info.st_dev or (min_mtime is not None and opened.st_mtime < min_mtime)):
                return None
            offset = max(0, opened.st_size - max_bytes)
            handle.seek(offset)
            data = handle.read(max_bytes)
            if offset:
                # The first fragment may not be a complete line.
                data = data.partition(b"\n")[2]
        count = _tail_count(tail)
        lines = [line for line in data.decode("utf-8", "replace").splitlines()
                 if not line.startswith(("Fra:", "libpng"))]
        return {"lines": lines[-count:] if count else [], "mtime": opened.st_mtime, "size": opened.st_size}
    except (OSError, ValueError):
        return None


def _tail_count(tail):
    try:
        return max(0, min(int(tail), MAX_TAIL))
    except (ValueError, TypeError, OverflowError):
        return 200


class TaskMonitor:
    """Thread-safe ~2s proc discovery with bounded, one-hour external history."""

    def __init__(self, project_root, jobs_dir, proc_root="/proc", clock=time.time):
        supplied = os.path.realpath(os.fspath(project_root))
        self.project_root = (os.path.join(supplied, "projects")
                             if os.path.basename(supplied) != "projects" and os.path.isdir(os.path.join(supplied, "projects"))
                             else supplied)
        self.app_root = os.path.dirname(self.project_root)
        self.jobs_dir = os.path.realpath(os.fspath(jobs_dir))
        self.proc_root, self.clock = os.fspath(proc_root), clock
        self.code_roots = tuple(dict.fromkeys((os.path.realpath(os.path.join(self.app_root, "code")),
                                              os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
        self.lock = threading.RLock()
        self._scanned_at = None
        self._processes = {}
        self._monitor = {"available": False, "warning": "Process discovery has not run"}
        self._external = {}
        self._progress = {}
        self._terminal_cache = {}
        try:
            self._ticks = float(os.sysconf("SC_CLK_TCK"))
        except (AttributeError, OSError, ValueError):
            self._ticks = 100.0  # Linux default; also permits portable proc fixtures.

    def _scan(self, now):
        if self._scanned_at is not None and 0 <= now - self._scanned_at < SCAN_TTL:
            return
        self._scanned_at = now
        processes, denied, truncated = {}, 0, False
        try:
            boot = None
            for line in _read_bounded(os.path.join(self.proc_root, "stat")).decode("ascii", "replace").splitlines():
                if line.startswith("btime "):
                    boot = float(line.split()[1])
                    break
            if boot is None or not math.isfinite(boot):
                raise OSError("/proc/stat has no valid boot epoch")
            with os.scandir(self.proc_root) as entries:
                count = 0
                for entry in entries:
                    if not entry.name.isascii() or not entry.name.isdigit():
                        continue
                    count += 1
                    if count > MAX_PROCESSES:
                        truncated = True
                        break
                    try:
                        info = parse_proc_stat(_read_bounded(os.path.join(entry.path, "stat")).decode("utf-8", "replace"))
                        if not info or info["pid"] != int(entry.name):
                            continue
                        info["started"] = boot + info["starttime"] / self._ticks
                        info["argv"] = parse_cmdline(_read_bounded(os.path.join(entry.path, "cmdline")))
                        if not info["argv"]:
                            continue
                        # Non-target process cwd (e.g. root daemons) need not be readable.
                        executable = os.path.basename(info["argv"][0])
                        if _PYTHON.fullmatch(executable) or executable in ("bash", "bash.exe"):
                            info["cwd"] = os.path.realpath(os.readlink(os.path.join(entry.path, "cwd")))
                            info["task"] = classify_argv(info["argv"], info["cwd"], self.project_root, self.code_roots)
                        else:
                            info["task"] = None
                        # Avoid associating a reused PID/cwd/fd with the earlier
                        # stat read if the process exits during discovery.
                        if info["task"] and read_process_identity(info["pid"], self.proc_root) != info["starttime"]:
                            continue
                        processes[info["pid"]] = info
                    except OSError as exc:
                        # Vanishing PIDs are normal, permissions/IO failure aren't idle.
                        if exc.errno not in (errno.ENOENT, errno.ESRCH):
                            denied += 1
            warning = ("Process discovery incomplete: process scan bound reached" if truncated else
                       f"Process discovery incomplete: {denied} unreadable process entries" if denied else None)
            self._monitor = {"available": not (denied or truncated), "warning": warning}
            self._processes = processes
        except (OSError, ValueError, IndexError) as exc:
            self._monitor = {"available": False, "warning": "Process discovery unavailable: " + str(exc)}
            self._processes = {}

    def _ancestors(self, pid):
        seen = set()
        while pid in self._processes and pid not in seen:
            seen.add(pid)
            parent = self._processes[pid]["ppid"]
            if parent == pid or parent <= 0:
                break
            yield parent
            pid = parent

    def _managed_roots(self, web_jobs):
        roots, claimed = {}, set()
        for job in web_jobs:
            try:
                pid, start = int(job.get("pid")), int(job.get("process_start"))
                proc = self._processes.get(pid)
                if proc and proc["starttime"] == start:
                    roots[pid] = job["id"]
                    claimed.add(job["id"])
            except (ValueError, TypeError):
                pass
        for job in web_jobs:
            if (job.get("id") in claimed or job.get("pid") is not None or job.get("process_start") is not None
                    or job.get("status") != "running" or not isinstance(job.get("argv"), list)):
                continue
            matches = [p for p in self._processes.values() if p["argv"] == job["argv"] and p.get("task")
                       and p["task"]["project"] == job.get("project") and p["task"]["cmd"] == job.get("cmd")]
            ids = {p["pid"] for p in matches}
            matches = [p for p in matches if not any(a in ids for a in self._ancestors(p["pid"]))]
            # Ambiguous identical independent roots must not all disappear.
            if len(matches) == 1:
                roots[matches[0]["pid"]] = job["id"]
        return roots

    def _roots(self, managed):
        candidates = {pid: p for pid, p in self._processes.items() if p.get("task") and p["state"] not in ("Z", "X")}
        roots = []
        for pid, proc in candidates.items():
            if pid in managed or any(a in managed for a in self._ancestors(pid)):
                continue
            duplicate = False
            for ancestor in self._ancestors(pid):
                parent = candidates.get(ancestor)
                if parent and (parent["argv"] == proc["argv"] or
                               (parent["task"]["cmd"] == "pipeline" and parent["task"]["project"] == proc["task"]["project"])):
                    duplicate = True
                    break
            if not duplicate:
                roots.append(proc)
        return roots

    def _family(self, pid):
        return [p for p in self._processes.values()
                if p["pid"] == pid or pid in self._ancestors(p["pid"])]

    def _safe_log(self, path, minimum, tail=MAX_TAIL):
        if not path:
            return None
        path = os.path.realpath(path)
        if not _inside_any((self.app_root, self.jobs_dir), path):
            return None
        result = bounded_log_tail(path, tail, min_mtime=minimum)
        if result is not None:
            result["path"] = path
        return result

    def _logs(self, task, family):
        logs, seen = [], set()
        if task.get("source") == "web" and task.get("status") == "queued":
            return logs
        maximum = task.get("ended") if task.get("source") == "web" else None
        minimum = task.get("started")
        if not isinstance(minimum, (int, float)):
            minimum = task.get("created") if task.get("source") == "web" else None
        if not isinstance(minimum, (int, float)):
            return logs
        active_stages = [p for p in family if p.get("task") and p["task"]["cmd"] != "pipeline"
                         and p["task"]["project"] == task.get("project") and p["state"] not in ("Z", "X")]
        live_stage = min(active_stages, key=lambda p: p["started"]) if task.get("cmd") == "pipeline" and active_stages else None
        def add(path, threshold=None):
            if len(logs) >= MAX_LOG_FILES:
                return
            result = self._safe_log(path, minimum if threshold is None else threshold)
            if result and isinstance(maximum, (int, float)) and result["mtime"] > maximum:
                return  # a newer run's project logs are not this terminal job
            if result and result["path"] not in seen:
                seen.add(result["path"])
                logs.append(result)
        if task.get("source") == "web":
            add(task.get("log"))
        for proc in family[:MAX_LOG_FILES]:
            for stream in ("1", "2"):
                try:
                    target = os.readlink(os.path.join(self.proc_root, str(proc["pid"]), "fd", stream))
                    if os.path.isabs(target):
                        add(target)  # no opening /proc/fd, pipes, sockets or devices
                except OSError:
                    pass
        project = task.get("project", "")
        if not isinstance(project, str) or not _NAME.fullmatch(project):
            return logs
        directory = os.path.join(self.project_root, project)
        if os.path.dirname(os.path.realpath(directory)) != self.project_root:
            return logs
        command = task.get("cmd")
        stages = ((live_stage["task"]["cmd"], "run_all") if live_stage else
                  ("prepare", "gen", "train", "exam", "run_all")) if command == "pipeline" else (command,)
        for stage in stages:
            if stage in COMMANDS or stage == "run_all":
                threshold = live_stage["started"] if live_stage and stage != "run_all" else minimum
                add(os.path.join(directory, stage + ".log"), threshold)
        active = {p["task"]["cmd"] for p in family if p.get("task")}
        if command == "gen" or "gen" in active:
            # Worker logs each carry their own real numerator/denominator; never
            # silently sum them into a fabricated whole-generation percentage.
            try:
                with os.scandir(os.path.join(directory, "logs")) as entries:
                    for i, entry in enumerate(entries):
                        if i >= 128 or len(logs) >= MAX_LOG_FILES:
                            break
                        if re.fullmatch(r"gen_\d+\.log", entry.name):
                            add(entry.path)
            except OSError:
                pass
        if command == "exam" or "exam" in active:
            style = task.get("args", {}).get("style")
            tag = "exam_strike" if style == "strike" else "exam"
            add(os.path.join(directory, tag, "render.log"))
        # An explicit safe output name lets us show current export activity.
        output = task.get("args", {}).get("name")
        if isinstance(output, str) and _NAME.fullmatch(output):
            add(os.path.join(directory, "out", output, "export.log"))
        return logs

    def _decorate(self, task, family, now):
        logs = self._logs(task, family)
        command, phase_command = task.get("cmd", "unknown"), task.get("cmd", "unknown")
        live_stage = None
        if command == "pipeline":
            stages = [p for p in family if p.get("task") and p["task"]["cmd"] != "pipeline"
                      and p["task"]["project"] == task.get("project") and p["state"] not in ("Z", "X")]
            if stages:
                live_stage = min(stages, key=lambda p: p["started"])["task"]["cmd"]
                phase_command = live_stage
        selected, latest = parse_progress([], phase_command), None
        for log in sorted(logs, key=lambda value: value["mtime"]):
            parsed = parse_progress(log["lines"], phase_command, task.get("args"))
            if parsed["evidence"]:
                selected, latest = parsed, log
        if live_stage and not selected["evidence"]:
            selected = {"phase": _phase(live_stage), "evidence": None}
        evidence = []
        if live_stage:
            evidence.append({"kind": "process", "detail": "Live pipeline descendant: " + live_stage})
        if latest:
            evidence.append({"kind": "log", "path": latest["path"], "mtime": latest["mtime"], "line": selected["evidence"]})
        if latest:
            note = selected["evidence"]
        elif live_stage:
            note = "Live " + live_stage + " process; no current progress counter"
        elif task.get("status") == "running":
            note = "Process observed; no fresh progress log" if task.get("source") == "cli" else "Managed job running; no fresh progress log"
        else:
            note = "Managed job status: " + str(task.get("status", "unknown"))
        task["phase"], task["activity"] = selected["phase"], {"note": note, "evidence": evidence}
        signature = (repr(task["phase"]), repr(task["activity"]))
        prior = self._progress.get(task["id"])
        updated = (latest["mtime"] if latest else now) if not prior or prior[0] != signature else prior[1]
        task["progress_updated_at"] = updated
        self._progress[task["id"]] = (signature, updated, now)
        return logs

    def snapshot(self, web_jobs):
        with self.lock:
            now = float(self.clock())
            self._scan(now)
            jobs = [copy.deepcopy(job) for job in web_jobs if isinstance(job, dict) and "id" in job]
            jobs.sort(key=lambda job: -(job.get("created") or 0))
            managed, tasks = self._managed_roots(jobs), []
            # Preserve every active job, plus bounded recent history. Historical
            # jobs cannot make each 5s UI poll re-read the entire jobs directory.
            selected = [job for i, job in enumerate(jobs) if i < MAX_WEB_HISTORY or job.get("status") in ("queued", "running")]
            for job in selected:
                active = job.get("status") in ("queued", "running")
                job.update(source="web", read_only=False, can_cancel=active)
                pids = [pid for pid, job_id in managed.items() if job_id == job["id"]]
                signature = repr({key: value for key, value in job.items()
                                  if key not in ("phase", "activity", "progress_updated_at")})
                cached = self._terminal_cache.get(job["id"])
                if not active and cached and cached["signature"] == signature and 0 <= now - cached["at"] < TERMINAL_CACHE_TTL:
                    job.update(copy.deepcopy(cached["fields"]))
                else:
                    self._decorate(job, self._family(pids[0]) if pids else [], now)
                    if not active:
                        self._terminal_cache[job["id"]] = {"signature": signature, "at": now,
                            "fields": copy.deepcopy({key: job[key] for key in ("phase", "activity", "progress_updated_at")})}
                tasks.append(job)
            kept_ids = {job["id"] for job in selected}
            self._terminal_cache = {key: value for key, value in self._terminal_cache.items() if key in kept_ids}
            observed = set()
            for proc in self._roots(managed):
                task_id = "cli:{}:{}".format(proc["pid"], proc["starttime"])
                observed.add(task_id)
                task = copy.deepcopy(proc["task"])
                task.update(id=task_id, status="running", source="cli", read_only=True, can_cancel=False,
                            pid=proc["pid"], process_start=proc["starttime"], created=proc["started"],
                            started=proc["started"], ended=None, rc=None)
                logs = self._decorate(task, self._family(proc["pid"]), now)
                self._external[task_id] = {"task": task, "last_seen": now, "missing_since": None,
                                           "lines": [line for log in sorted(logs, key=lambda v: v["mtime"]) for line in log["lines"]][-MAX_TAIL:]}
            for task_id, record in list(self._external.items()):
                task = record["task"]
                proc = self._processes.get(task["pid"])
                if (proc and proc["starttime"] == task["process_start"] and
                        (proc["pid"] in managed or any(a in managed for a in self._ancestors(proc["pid"])))):
                    # Discovery may run just before the server persists child
                    # identity. Do not retain that same live task as CLI history.
                    del self._external[task_id]
                    continue
                if task_id not in observed:
                    if record["missing_since"] is None:
                        record["missing_since"] = now
                    if now - record["missing_since"] > RETENTION_TTL:
                        del self._external[task_id]
                        continue
                    new_status = "ended_unknown" if self._monitor["available"] else "unknown"
                    if task["status"] != new_status:
                        task["progress_updated_at"] = now
                    task["status"] = new_status
                    # No rc/ended time is invented; discovery is not waitpid.
                    task["activity"] = {"note": "No longer observed; exit status and result quality are unknown"
                                        if self._monitor["available"] else "Process visibility unavailable; state is unknown",
                                        "evidence": []}
                    task["can_cancel"] = False
                tasks.append(copy.deepcopy(task))
            if len(self._external) > MAX_RETAINED:
                remove = sorted(self._external, key=lambda key: self._external[key]["last_seen"])[:-MAX_RETAINED]
                for task_id in remove:
                    del self._external[task_id]
                tasks = [task for task in tasks if task["source"] != "cli" or task["id"] in self._external]
            for key, value in list(self._progress.items()):
                if now - value[2] > RETENTION_TTL:
                    del self._progress[key]
            if len(self._progress) > MAX_RETAINED * 4:
                self._progress = dict(sorted(self._progress.items(), key=lambda item: item[1][2], reverse=True)[:MAX_RETAINED * 4])
            tasks.sort(key=lambda task: (task["source"] != "web", -(task.get("created") or 0), str(task["id"])))
            return {"tasks": tasks, "observed_at": now, "monitor": dict(self._monitor)}

    def task_log(self, task_id, web_jobs, tail=200):
        """Resolve only registered IDs. No request string ever becomes a path."""
        with self.lock:
            snapshot = self.snapshot(web_jobs)
            task = next((item for item in snapshot["tasks"] if item["id"] == task_id), None)
            if task is None:
                # Historical Web logs remain addressable by a registered job ID
                # even when the list endpoint omits old history.
                registered = next((job for job in web_jobs if isinstance(job, dict) and job.get("id") == task_id), None)
                if registered is None:
                    return None
                task = copy.deepcopy(registered)
                task["source"] = "web"
            count = _tail_count(tail)
            if task["source"] == "cli":
                lines = self._external[task_id]["lines"]
            else:
                pids = [pid for pid, value in self._managed_roots(web_jobs).items() if value == task_id]
                logs = self._logs(task, self._family(pids[0]) if pids else [])
                lines = [line for log in sorted(logs, key=lambda value: value["mtime"]) for line in log["lines"]]
            return {"lines": lines[-count:] if count else []}
