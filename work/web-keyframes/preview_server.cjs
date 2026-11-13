// Local preview of the video2rig web UI for the keyframe page. NOT production.
// node work/web-keyframes/preview_server.cjs [port]
// - static: video2rig/web/static (three.js is stubbed: the 3D view is not part of this preview)
// - keyframes GET/PUT: the real web/keyframes_store.py through `python kf_bridge.py`
// - data: work/web-keyframes/fixture/projects/<p>/out/<name>/ (copies; originals are never touched)
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const PORT = Number(process.argv[2] || 18600);
const REPO = path.resolve(__dirname, "../..");
const STATIC = path.join(REPO, "video2rig/web/static");
const FIX = path.join(__dirname, "fixture");
const PROJ = path.join(FIX, "projects");
const VIDEOS = path.join(FIX, "videos");
const BRIDGE = path.join(__dirname, "kf_bridge.py");
const NAME = /^[A-Za-z0-9_\-.]+$/;
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".mp4": "video/mp4", ".png": "image/png" };

const send = (res, code, body, type = "application/json") => {
  res.writeHead(code, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body));
};
const safe = (n) => typeof n === "string" && NAME.test(n) && !n.startsWith(".");
const projects = () => fs.existsSync(PROJ) ? fs.readdirSync(PROJ).filter((n) => safe(n) && fs.statSync(path.join(PROJ, n)).isDirectory()) : [];
function outputs(p) {
  const dir = path.join(PROJ, p, "out");
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((n) => safe(n) && fs.statSync(path.join(dir, n)).isDirectory()).map((n) => {
    const o = path.join(dir, n);
    const files = fs.readdirSync(o).filter((f) => fs.statSync(path.join(o, f)).isFile());
    let summary = null;
    try { summary = JSON.parse(fs.readFileSync(path.join(o, "summary.json"), "utf8")); } catch { /* none */ }
    return { name: n, files, summary, diagnostics: null, stability: null, check: summary?.check || {}, has_keyframes: { auto: files.includes("keyframes_auto.json"), artist: files.includes("keyframes_artist.json") }, mtime: fs.statSync(o).mtimeMs / 1000 };
  }).sort((a, b) => b.mtime - a.mtime);
}
function bridge(args, input) {
  const r = spawnSync("python", [BRIDGE, ...args], { input: input || "", encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
  if (r.status !== 0) return { status: 500, body: { detail: `bridge failed: ${r.stderr || r.error}` } };
  return JSON.parse(r.stdout);
}
function serveFile(req, res, file) {
  const stat = fs.statSync(file);
  const type = TYPES[path.extname(file)] || "application/octet-stream";
  const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range || "");
  if (range) {
    const start = range[1] ? Number(range[1]) : 0;
    const end = range[2] ? Math.min(Number(range[2]), stat.size - 1) : stat.size - 1;
    res.writeHead(206, { "Content-Type": type, "Content-Range": `bytes ${start}-${end}/${stat.size}`, "Accept-Ranges": "bytes", "Content-Length": end - start + 1 });
    fs.createReadStream(file, { start, end }).pipe(res);
    return;
  }
  res.writeHead(200, { "Content-Type": type, "Content-Length": stat.size, "Accept-Ranges": "bytes", "Cache-Control": "no-store" });
  fs.createReadStream(file).pipe(res);
}
const JOBS = [];
function runJob(job) {
  job.status = "running"; job.started = Date.now() / 1000;
  job.phase = { key: "keyframes", label: "判断着地", current: 1, total: 3 };
  const { spawn } = require("node:child_process");
  const proc = spawn("python", [path.join(REPO, "video2rig/tools/max2026/run_keyframes.py"), path.join(PROJ, job.project), job.args.name],
    { env: { ...process.env, PYTHONIOENCODING: "utf-8" }, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  const onData = (c) => {
    log += c;
    const m = [...log.matchAll(/keyframes stage (\d)\/3 (\w+)/g)].at(-1);
    if (m) job.phase = { key: "keyframes", label: { contacts: "判断着地", candidates: "抽取候选关键帧", ladder: "计算过渡键阶梯" }[m[2]], current: Number(m[1]), total: 3 };
  };
  proc.stdout.on("data", onData); proc.stderr.on("data", onData);
  proc.on("close", (code) => {
    job.ended = Date.now() / 1000; job.rc = code; job.status = code === 0 ? "done" : "failed";
    const f = /KEYFRAMES_FAILED: (.*)/.exec(log); if (f) job.error = f[1].trim();
    fs.writeFileSync(path.join(__dirname, `job-${job.id}.log`), log);
  });
}
const THREE_STUB = "export class WebGLRenderer{constructor(){throw new Error('预览服务没有提供 three.js')}};export const LoopOnce=1;export class LoadingManager{};export class Scene{};export class Color{};export class PerspectiveCamera{};export class HemisphereLight{};export class DirectionalLight{};export class GridHelper{};export class Box3{};export class AnimationMixer{};";

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  try {
    if (url.pathname === "/") return serveFile(req, res, path.join(STATIC, "index.html"));
    if (parts[0] === "static") {
      if (parts[1] === "vendor") return send(res, 200, parts.at(-1).includes("GLTF") ? "export class GLTFLoader{load(u,s,p,e){e(new Error('预览服务没有 3D'))}}" : parts.at(-1).includes("Orbit") ? "export class OrbitControls{}" : THREE_STUB, TYPES[".js"]);
      const f = path.join(STATIC, ...parts.slice(1));
      if (!f.startsWith(STATIC) || !fs.existsSync(f)) return send(res, 404, { detail: "not found" });
      return serveFile(req, res, f);
    }
    if (parts[0] !== "api") return send(res, 404, { detail: "not found" });
    const a = parts.slice(1);
    if (a[0] === "projects" && a.length === 1) return send(res, 200, projects().map((n) => ({ name: n, stage: "exam_passed", has_reader: true, bones: 176, keypoints: 43, samples: 3000, epochs: 20, outputs: outputs(n).map((o) => o.name), mtime: 1 })));
    if (a[0] === "projects" && a.length === 2) return safe(a[1]) && projects().includes(a[1]) ? send(res, 200, { name: a[1], stage: "exam_passed", has_reader: true, bones: 176, keypoints: 43, samples: 3000, epochs: 20, outputs: outputs(a[1]).map((o) => o.name), has_reference: false, mtime: 1, rig: { keypoints: [] } }) : send(res, 404, { detail: "no project" });
    if (a[0] === "projects" && a[2] === "outputs") return send(res, 200, outputs(a[1]));
    if (a[0] === "projects" && a[2] === "out" && a[4] === "curve") return send(res, 200, { residual: null, bonelen: null, bad: null });
    if (a[0] === "projects" && a[2] === "out" && a[4] === "keyframes") {
      if (!safe(a[1]) || !safe(a[3])) return send(res, 400, { detail: "bad name" });
      const dir = path.join(PROJ, a[1], "out", a[3]);
      if (!fs.existsSync(dir)) return send(res, 404, { detail: "no result" });
      if (req.method === "GET" && a.length === 5) {
        const r = bridge(["view", dir]);
        if (r.status === 200 && r.body.video_name && fs.existsSync(path.join(VIDEOS, r.body.video_name))) r.body.video = { name: r.body.video_name, url: `/api/videos/${encodeURIComponent(r.body.video_name)}/file` };
        return send(res, r.status, r.body);
      }
      if (req.method === "PUT" && a[5] === "artist") {
        let body = "";
        req.on("data", (c) => { body += c; });
        req.on("end", () => { const r = bridge(["save", dir], body); send(res, r.status, r.body); });
        return;
      }
    }
    if (a[0] === "videos" && a[2] === "file") {
      const f = path.join(VIDEOS, a[1]);
      if (!safe(a[1]) || !fs.existsSync(f)) return send(res, 404, { detail: "no video" });
      return serveFile(req, res, f);
    }
    if (a[0] === "tasks" && a.length === 1) return send(res, 200, { tasks: JOBS.slice().reverse(), monitor: { available: true, warning: null } });
    if (a[0] === "jobs" && a.length === 1 && req.method === "GET") return send(res, 200, JOBS.slice().reverse());
    if (a[0] === "jobs" && a.length === 1 && req.method === "POST") {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        const j = JSON.parse(body || "{}");
        if (j.cmd !== "keyframes") return send(res, 400, { detail: "preview server only runs keyframes jobs" });
        const r = bridge(["check", path.join(PROJ, j.project), String(j.args?.name || ""), j.args?.replace === true ? "1" : "0"]);
        if (r.status !== 200) return send(res, r.status, r.body);
        if (JOBS.some((x) => x.project === j.project && ["queued", "running"].includes(x.status))) return send(res, 409, { detail: "当前项目已有运行或排队任务，请先查看任务中心。" });
        const job = { id: `kf-${Date.now()}`, project: j.project, cmd: "keyframes", args: r.body.args, status: "queued", created: Date.now() / 1000, source: "web" };
        JOBS.push(job);
        setTimeout(() => runJob(job), 1500);   // mimic the queue
        send(res, 200, job);
      });
      return;
    }
    if (a[0] === "capabilities") return send(res, 200, { version: 2, unified_tasks: true });
    if (a[0] === "system") return send(res, 200, { gpu: [], disk_free_gb: 100, time: Date.now() / 1000 });
    if (a[0] === "inputs") return send(res, 200, { models: [], videos: [] });
    if (a[0] === "project-log") return send(res, 200, { lines: [] });
    return send(res, 404, { detail: `preview server: ${url.pathname} not mocked` });
  } catch (e) {
    return send(res, 500, { detail: String(e) });
  }
});
server.listen(PORT, "127.0.0.1", () => console.log(`preview on http://127.0.0.1:${PORT}/ (fixture ${FIX})`));
