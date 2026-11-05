/* Dependency-free lifecycle/clock tests for the isolated results module.
 * Run: node video2rig/tools/test_results.cjs
 * This is a DOM/Three mock test, not a substitute for a real WebGL/media smoke test.
 */
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");

class Element {
  constructor(id = "") {
    Object.assign(this, { id, hidden: false, style: {}, dataset: {}, events: {}, children: [], value: "", height: 160, currentTime: 0, duration: 0, textContent: "" });
  }
  get clientWidth() { return this.hidden ? 0 : 700; }
  set innerHTML(html) {
    this.html = html;
    if (this.id === "host") {
      this.nodes = {};
      for (const [, id] of html.matchAll(/id="([^"]+)"/g)) this.nodes[id] = new Element(id);
      this.nodes.outFilter.value = "all";
      this.nodes.outStableCutoff.value = "1.5";
      this.nodes.outModes.buttons = ["video", "3d", "compare"].map((mode) => {
        const e = new Element();
        e.dataset.outMode = mode;
        return e;
      });
    } else if (this.id === "outList") {
      this.children = [...html.matchAll(/data-out-name="([^"]*)"/g)].map((match) => {
        const e = new Element();
        e.dataset.outName = match[1];
        return e;
      });
    }
  }
  querySelectorAll(selector) {
    if (this.id === "host" && selector === "[id]") return Object.values(this.nodes);
    if (this.id === "outModes") return this.buttons;
    if (this.id === "outList") return this.children;
    return [];
  }
  addEventListener(type, callback) { (this.events[type] ??= []).push(callback); }
  async fire(type) {
    for (const callback of this.events[type] || []) await callback({ target: this, preventDefault() {} });
  }
  setAttribute() {}
  removeAttribute() {}
  replaceChildren() { this.children = []; }
  append(child) { this.children.push(child); }
  remove() {}
  focus() {}
  pause() {}
  load() {}
  play() { this.onplay?.(); return Promise.resolve(); }
  getBoundingClientRect() { return { width: 700, height: 360 }; }
}

const host = new Element("host");
const tab = new Element("tab");
tab.display = "none";
let rendererCount = 0, disposedCount = 0, mixer;
const glbLoads = [];
class Scene {
  constructor() { this.children = []; }
  add(child) { this.children.push(child); }
  traverse(callback) {
    for (const child of this.children) {
      callback(child);
      child.traverse?.(callback);
    }
  }
}
const THREE = {
  LoadingManager: class {},
  WebGLRenderer: class {
    constructor() {
      rendererCount++;
      this.domElement = new Element();
      this.renderLists = { dispose() {} };
    }
    setPixelRatio() {} setSize() {} render() {} dispose() {} forceContextLoss() {}
  },
  Scene,
  Color: class {},
  PerspectiveCamera: class {
    constructor() { this.position = { set() {}, copy() { return { add() {} }; } }; }
    updateProjectionMatrix() {}
  },
  HemisphereLight: class {},
  DirectionalLight: class { constructor() { this.position = { set() {} }; } },
  GridHelper: class { constructor() { this.position = {}; } },
  Box3: class { setFromObject() { return this; } isEmpty() { return true; } },
  AnimationMixer: class {
    constructor() { mixer = this; }
    clipAction() { return { setLoop() {}, play() {} }; }
    setTime(time) { this.time = time; }
    stopAllAction() {} uncacheRoot() {}
  },
  LoopOnce: 1,
};
const context = {
  THREE,
  GLTFLoader: class { load(url, success, progress, error) { glbLoads.push({ url, success, error }); } },
  OrbitControls: class { constructor() { this.target = {}; } addEventListener() {} update() {} dispose() {} },
  document: { hidden: false, querySelector: () => host, getElementById: () => tab, addEventListener() {} },
  window: { devicePixelRatio: 2, addEventListener() {} },
  getComputedStyle: () => ({ display: tab.display }),
  MutationObserver: class { observe() {} },
  ResizeObserver: class { observe() {} },
  requestAnimationFrame: () => 1,
  cancelAnimationFrame() {},
  AbortController,
  console,
};
vm.createContext(context);
const source = fs.readFileSync(path.join(__dirname, "../web/static/results.js"), "utf8")
  .replace(/^import .*;\r?\n/gm, "")
  .replace("export function initResults", "function initResults");
new vm.Script(source + "\nglobalThis.init = initResults;").runInContext(context);

let project = "A", chartCount = 0;
const requests = [], submissions = [];
const results = context.init({
  api: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })),
  submit: async (...args) => { submissions.push(args); return { id: "job" }; },
  current: () => project,
  notify() {},
  chart(canvas) {
    chartCount++;
    assert.equal(canvas.height, 160);
    assert.equal(canvas.dataset.height, "160");
  },
});
const output = (name, files = ["lab.mp4", "motion.npz", name + ".fbx"]) => ({
  name, files, summary: { frames: 90, fps: 30, status: "completed" }, check: { same_move: true }, mtime: 1,
});
const root = () => ({ geometry: { dispose() { disposedCount++; } }, traverse(callback) { callback(this); } });
const flush = () => new Promise((resolve) => setImmediate(resolve));

(async () => {
  const staleList = results.refresh();
  project = "B";
  const freshList = results.refresh();
  requests[1].resolve([output("B1")]);
  await freshList;
  requests[0].resolve([output("old")]);
  await staleList;
  assert.equal(host.nodes.outTitle.textContent, "B1", "stale project response must not render");

  host.nodes.outVideo.duration = 3;
  host.nodes.outVideo.onloadedmetadata();
  host.nodes.outVideo.currentTime = 0.5;
  host.nodes.tSeek.value = "2.4";
  await host.nodes.tSeek.fire("input");
  assert.equal(host.nodes.outVideo.currentTime, 2.4, "seek must capture range value before pause resets it");
  host.nodes.outStableName.value = "B1_copy";
  await host.nodes.outStableForm.fire("submit");
  assert.equal(submissions[0][0], "B");
  assert.equal(submissions[0][1], "stabilize");
  assert.equal(submissions[0][2].source, "B1", "stable copy preserves selected source");

  project = "A";
  const list = results.refresh();
  requests.at(-1).resolve([
    output("A1", ["A1.glb"]), output("A2", ["A2.glb"]), output("AV", ["lab.mp4", "AV.glb"]),
  ]);
  await list;
  assert.equal(rendererCount, 0, "must not create WebGL or load GLB while hidden");
  tab.display = "block";
  results.resize();
  assert.equal(glbLoads.length, 1);
  await results.select("A2");
  assert.equal(glbLoads.length, 2);
  const staleRoot = root();
  glbLoads[0].success({ scene: staleRoot, scenes: [staleRoot], animations: [{ duration: 3 }] });
  assert.equal(disposedCount, 1, "stale GLB must be disposed, without duplicate disposal of shared roots");
  glbLoads[1].success({ scene: root(), animations: [{ duration: 3 }] });
  host.nodes.tSeek.value = "2";
  await host.nodes.tSeek.fire("input");
  assert.equal(mixer.time, 2, "GLB-only clock uses exact animation seconds");

  assert.equal(host.nodes.outDiagnostics.open, false, "technical diagnostics default collapsed");
  assert.equal(host.nodes.outStableDetails.open, false, "stability settings default collapsed");
  host.nodes.outDiagnostics.open = true;
  await host.nodes.outDiagnostics.fire("toggle");
  const a2curve = requests.find((request) => request.url.includes("/A/out/A2/"));
  a2curve.resolve({ residual: [0.1, 0.2] });
  await flush();
  assert.equal(chartCount, 1, "unhide canvas before checking its width");
  results.resize();
  assert.equal(chartCount, 2, "chart can redraw without DPR height compounding");
  const staleCurve = requests.find((request) => request.url.includes("/A/out/A1/"));
  staleCurve.resolve({ residual: [99] });
  await flush();
  assert.equal(chartCount, 2, "stale curve cannot replace current curve");

  await results.select("AV");
  glbLoads.at(-1).success({ scene: root(), animations: [{ duration: 3 }] });
  host.nodes.outVideo.duration = 4;
  host.nodes.outVideo.onloadedmetadata();
  assert.match(host.nodes.tSync.textContent, /时长不一致/);
  host.nodes.tSeek.value = "3.8";
  await host.nodes.tSeek.fire("input");
  assert.equal(mixer.time, 3, "approximate raw-second inspection clamps at clip end");
  host.nodes.outVideo.duration = 3.04;
  host.nodes.outVideo.onloadedmetadata();
  assert.match(host.nodes.tSync.textContent, /1 秒对应/);
  host.nodes.tSeek.value = "1.5";
  await host.nodes.tSeek.fire("input");
  assert.equal(mixer.time, 1.5, "matching durations must not normalize time");

  const requestCount = requests.length;
  const pendingGlb = glbLoads.at(-1);
  project = "C";
  results.pause();
  assert.equal(requests.length, requestCount, "project-mismatch pause must not fetch");
  assert.equal(host.nodes.outTitle.textContent, "", "project-mismatch pause clears old title");
  assert.equal(host.nodes.outDetail.hidden, true);
  assert.equal(host.nodes.tViewport.children.length, 0, "project-mismatch pause disposes the inherited model");
  pendingGlb.success({ scene: root(), animations: [{ duration: 3 }] });
  assert.equal(host.nodes.tViewport.children.length, 0, "obsolete GLB cannot survive project-mismatch pause");

  const emptyList = results.refresh();
  requests.at(-1).resolve([]);
  await emptyList;
  assert.equal(host.nodes.outDetail.hidden, true);
  assert.equal(host.nodes.outTitle.textContent, "");
  assert.equal(host.nodes.tViewport.children.length, 0);
  const failedList = results.refresh();
  requests.at(-1).reject(new Error("offline"));
  await failedList;
  assert.equal(host.nodes.outDetail.hidden, true);
  assert.match(host.nodes.outNotice.textContent, /offline/);
  console.log("PASS results: stale list/curve/GLB races, hidden loading, disposal, GLB clock, seek, stable submit, chart size, duration warning/clamp, exact seconds, empty/error cleanup");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
