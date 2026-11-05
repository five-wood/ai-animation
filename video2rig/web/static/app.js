import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { initRunPage } from "/static/run-page.js?v=drop-props-1";

const $ = (s) => document.querySelector(s);
const api = async (u, opt) => {
  const r = await fetch(u, opt);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
};
const fmt = (x, d = 1) => (x === null || x === undefined || Number.isNaN(x) ? "–" : Number(x).toFixed(d));
const ago = (t) => {
  const s = Date.now() / 1000 - t;
  if (s < 60) return `${s | 0} 秒前`;
  if (s < 3600) return `${(s / 60) | 0} 分钟前`;
  if (s < 86400) return `${(s / 3600) | 0} 小时前`;
  return `${(s / 86400) | 0} 天前`;
};
const STAGE = {
  empty: ["空", ""], prepared: ["已准备", ""], data: ["已生成训练图", ""], trained: ["已训练", "run"],
  exam_passed: ["考试通过", "ok"], exam_failed: ["考试未通过", "bad"],
};

let state = { projects: [], cur: null, tab: "overview", sOffset: 0, sLimit: 60, sSel: null, sAugSeed: 1 };

// ---------------------------------------------------------------- tabs
document.querySelectorAll("#tabs a").forEach((a) =>
  a.addEventListener("click", () => {
    document.querySelectorAll("#tabs a").forEach((x) => x.classList.toggle("active", x === a));
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + a.dataset.tab));
    state.tab = a.dataset.tab;
    location.hash = `${state.cur || ""}/${state.tab}`;
    refreshTab();
  })
);

// ---------------------------------------------------------------- projects
async function loadProjects() {
  state.projects = await api("/api/projects");
  const sel = $("#projSel");
  sel.innerHTML = state.projects.map((p) => `<option>${p.name}</option>`).join("");
  if (!state.cur || !state.projects.find((p) => p.name === state.cur)) {
    const best = [...state.projects].sort((a, b) => b.mtime - a.mtime)[0];
    state.cur = best ? best.name : null;
  }
  sel.value = state.cur;
  renderCards();
  if (state.tab === "run") runPage.onProjects();
}
$("#projSel").addEventListener("change", (e) => selectProject(e.target.value));
function selectProject(n) {
  state.cur = n;
  state.sOffset = 0;
  state.sSel = null;
  $("#projSel").value = n;
  location.hash = `${n}/${state.tab}`;
  renderCards();
  runPage.syncProject(n);
  refreshTab();
}

function renderCards() {
  $("#projCards").innerHTML = state.projects
    .map((p) => {
      const [st, cls] = STAGE[p.stage] || [p.stage, ""];
      const ex = p.exam
        ? `关节误差 ${fmt(p.exam.joint_err_mean_pct_of_size, 2)}% · 骨骼角 ${fmt(p.exam.bone_angle_mean_deg)}°`
        : p.last_val ? `验证误差中位数 ${fmt(p.last_val.px_med, 2)}px` : "";
      return `<div class="card ${p.name === state.cur ? "sel" : ""}" data-n="${p.name}">
        <div class="n">${p.name}<span class="badge ${cls}">${st}</span></div>
        <div class="s">${p.bones} 骨骼 · ${p.active ?? "–"} 可动 · ${p.keypoints} 关键点</div>
        <div class="s">${p.samples} 训练姿势 · ${p.epochs} 轮 · ${p.outputs.length} 个结果</div>
        <div class="s">${ex}</div></div>`;
    })
    .join("");
  document.querySelectorAll(".card[data-n]").forEach((c) => c.addEventListener("click", () => selectProject(c.dataset.n)));
}

async function renderOverview() {
  if (!state.cur) return;
  const p = await api(`/api/projects/${state.cur}`);
  const ref = `/api/projects/${p.name}/file/reference_two_views.png`;
  $("#refImg").src = p.has_reference ? ref + "?t=" + Math.floor(p.mtime) : "";
  $("#refDl").href = ref;
  const rig = p.rig || { keypoints: [], bones: [] };
  const folded = rig.bones.filter((b) => b.folded).map((b) => b.name);
  $("#rigInfo").innerHTML = `
    <b>模型</b><span>${(p.model || "").split("/").pop()}</span>
    <b>摄影棚</b><span>${p.studio?.visual_profile === "soft-clean" ? "实验：柔光无地影 · 尚未证明改善识别，必须配套本项目模型" : "旧版带地影"}</span>
    <b>骨骼</b><span>${p.bones}（可动 ${p.active}）</span>
    <b>关键点</b><span>${p.keypoints}</span>
    <b>尺寸</b><span>${fmt(rig.size, 3)} · 朝向 ${rig.facing || "–"}</span>
    <b>并入父骨骼</b><span>${folded.join(", ") || "无"}</span>`;
  $("#kpList").innerHTML = rig.keypoints
    .map((k, i) => `<div class="${k.side}">${String(i).padStart(2)} ${k.name}${k.mirror >= 0 ? "  ↔ " + rig.keypoints[k.mirror].name : ""}</div>`)
    .join("");
}

// ---------------------------------------------------------------- samples
async function renderSamples() {
  if (!state.cur) return;
  const f = $("#sFilter").value;
  const r = await api(`/api/projects/${state.cur}/samples?offset=${state.sOffset}&limit=${state.sLimit}&fast=${f}`);
  $("#sPage").textContent = r.total
    ? `${state.sOffset + 1}–${Math.min(r.total, state.sOffset + state.sLimit)} / ${r.total}`
    : "还没有训练图";
  $("#sGrid").innerHTML = r.items
    .map((it) => `<div class="t ${it.id === state.sSel ? "sel" : ""}" data-id="${it.id}">
        <img loading="lazy" src="/api/projects/${state.cur}/thumb/${it.id}.jpg"><span>#${it.id}</span>${it.fast ? '<span class="f">模糊</span>' : ""}</div>`)
    .join("");
  document.querySelectorAll("#sGrid .t").forEach((t) => t.addEventListener("click", () => showSample(+t.dataset.id)));
  if (state.sSel === null && r.items.length) showSample(r.items[0].id);
  state._sTotal = r.total;
}
function showSample(id) {
  state.sSel = id;
  document.querySelectorAll("#sGrid .t").forEach((t) => t.classList.toggle("sel", +t.dataset.id === id));
  const lab = $("#sLabels").checked ? 1 : 0;
  const aug = $("#sAug").checked ? state.sAugSeed : 0;
  $("#sBig").src = `/api/projects/${state.cur}/sample/${id}.jpg?labels=${lab}&aug=${aug}&w=640`;
}
$("#sFilter").addEventListener("change", () => { state.sOffset = 0; state.sSel = null; renderSamples(); });
$("#sPrev").addEventListener("click", () => { state.sOffset = Math.max(0, state.sOffset - state.sLimit); renderSamples(); });
$("#sNext").addEventListener("click", () => {
  if (state.sOffset + state.sLimit < (state._sTotal || 0)) { state.sOffset += state.sLimit; renderSamples(); }
});
$("#sLabels").addEventListener("change", () => state.sSel !== null && showSample(state.sSel));
$("#sAug").addEventListener("change", () => state.sSel !== null && showSample(state.sSel));
$("#sReroll").addEventListener("click", () => { state.sAugSeed++; $("#sAug").checked = true; showSample(state.sSel); });
document.addEventListener("keydown", (e) => {
  if (state.tab !== "samples" || state.sSel === null || e.target.tagName === "INPUT") return;
  if (e.key === "ArrowRight") showSample(state.sSel + 1);
  if (e.key === "ArrowLeft" && state.sSel > 0) showSample(state.sSel - 1);
});

// ---------------------------------------------------------------- tiny chart
function chart(canvas, series, opt = {}) {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.height;
  canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.height = H + "px";
  const g = canvas.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, W, H);
  const pad = { l: 46, r: 12, t: 10, b: 24 };
  const pts = series.flatMap((s) => s.data.filter((p) => p[1] !== null && Number.isFinite(p[1])));
  if (!pts.length) { g.fillStyle = "#8a94a3"; g.fillText("暂无数据", W / 2 - 20, H / 2); return; }
  let x0 = Math.min(...pts.map((p) => p[0])), x1 = Math.max(...pts.map((p) => p[0]));
  let y0 = opt.y0 ?? Math.min(...pts.map((p) => p[1])), y1 = opt.y1 ?? Math.max(...pts.map((p) => p[1]));
  if (opt.log) { y0 = Math.log10(Math.max(1e-6, y0)); y1 = Math.log10(y1); }
  if (x1 === x0) x1 = x0 + 1; if (y1 === y0) y1 = y0 + 1;
  const X = (x) => pad.l + ((x - x0) / (x1 - x0)) * (W - pad.l - pad.r);
  const Y = (y) => { const v = opt.log ? Math.log10(Math.max(1e-6, y)) : y; return H - pad.b - ((v - y0) / (y1 - y0)) * (H - pad.t - pad.b); };
  g.strokeStyle = "#2a3039"; g.fillStyle = "#8a94a3"; g.font = "11px sans-serif"; g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const yv = y0 + ((y1 - y0) * i) / 4, yy = H - pad.b - ((H - pad.t - pad.b) * i) / 4;
    g.beginPath(); g.moveTo(pad.l, yy); g.lineTo(W - pad.r, yy); g.stroke();
    g.fillText(opt.log ? Math.pow(10, yv).toPrecision(2) : yv.toFixed(opt.dec ?? 1), 4, yy + 4);
  }
  g.fillText(opt.xlabel || "", W - pad.r - 60, H - 6);
  for (const s of series) {
    g.strokeStyle = s.color; g.lineWidth = 2; g.beginPath();
    let first = true;
    for (const [x, y] of s.data) {
      if (y === null || !Number.isFinite(y)) { first = true; continue; }
      if (first) g.moveTo(X(x), Y(y)); else g.lineTo(X(x), Y(y));
      first = false;
    }
    g.stroke();
    if (s.marks) for (const [x, y] of s.data) if (y !== null) { g.fillStyle = s.color; g.beginPath(); g.arc(X(x), Y(y), 2.5, 0, 7); g.fill(); }
  }
  let lx = pad.l + 8;
  for (const s of series) { g.fillStyle = s.color; g.fillRect(lx, pad.t + 2, 10, 3); g.fillStyle = "#d9dee6"; g.fillText(s.name, lx + 14, pad.t + 7); lx += g.measureText(s.name).width + 34; }
  if (opt.bands) for (const [a, b] of opt.bands) { g.fillStyle = "#e5534b33"; g.fillRect(X(a), pad.t, Math.max(1, X(b) - X(a)), H - pad.t - pad.b); }
}

// ---------------------------------------------------------------- training
async function renderTrain() {
  if (!state.cur) return;
  const r = await api(`/api/projects/${state.cur}/train`);
  const st = r.steps.map((s) => [s.step, s.hm]);
  const sd = r.steps.map((s) => [s.step, s.depth]);
  chart($("#cLoss"), [{ name: "热力图", color: "#e0a84a", data: st }, { name: "深度", color: "#3b8eea", data: sd }], { log: true, xlabel: "step" });
  const ep = r.epochs;
  chart($("#cVal"), [
    { name: "中位数", color: "#4cc38a", data: ep.map((e) => [e.epoch, e.px_med]), marks: true },
    { name: "可见关节中位数", color: "#3b8eea", data: ep.map((e) => [e.epoch, e.px_med_vis]), marks: true },
    { name: "p90", color: "#e5534b", data: ep.map((e) => [e.epoch, e.px_p90]), marks: true },
  ], { log: true, xlabel: "epoch" });
  chart($("#cDepth"), [{ name: "准确率", color: "#e0a84a", data: ep.map((e) => [e.epoch, e.depth_order]), marks: true }], { y0: 0.5, y1: 1, dec: 2, xlabel: "epoch" });
  const lg = await api(`/api/project-log/${state.cur}/train?tail=60`);
  $("#trainLog").textContent = lg.lines.filter((l) => l.startsWith("[v2r]")).join("\n");
}

// ---------------------------------------------------------------- exam
async function renderExam() {
  if (!state.cur) return;
  let r;
  try { r = await api(`/api/projects/${state.cur}/exam`); }
  catch { $("#examHead").innerHTML = '<div class="card">这个项目还没有考试结果</div>'; $("#boneBars").innerHTML = ""; $("#examVid").removeAttribute("src"); return; }
  const card = (t, v, u, ok) => `<div class="card"><div class="s">${t}</div><div class="metric" style="color:${ok === undefined ? "inherit" : ok ? "var(--ok)" : "var(--bad)"}">${v}<small>${u}</small></div></div>`;
  $("#examHead").innerHTML =
    card("结论", r.passed ? "通过" : "未通过", "", r.passed) +
    card("关节位置误差（均值）", fmt(r.joint_err_mean_pct_of_size, 2), "% 身体尺寸", r.joint_err_mean_pct_of_size < 3) +
    card("关节位置误差 p90", fmt(r.joint_err_p90_pct_of_size, 2), "%") +
    card("骨骼方向误差（均值）", fmt(r.bone_angle_mean_deg), "°", r.bone_angle_mean_deg < 10) +
    card("骨骼方向误差（中位数 / p90）", `${fmt(r.bone_angle_median_deg)} / ${fmt(r.bone_angle_p90_deg)}`, "°") +
    (r.twist_mean_deg !== undefined ? card("骨骼自转误差（均值，不计入结论）", fmt(r.twist_mean_deg), "°") : "") +
    card("整体朝向误差", fmt(r.root_angle_mean_deg), "°") +
    card("所有骨骼 <15° 的帧", fmt(100 * r.frames_all_bones_within_15deg, 0), "%");
  const bones = Object.entries(r.per_bone).sort((a, b) => b[1].mean_deg - a[1].mean_deg);
  const mx = Math.max(30, ...bones.map((b) => b[1].p90_deg));
  $("#boneBars").innerHTML = bones
    .map(([n, v]) => `<div class="bar"><span>${n}</span><div class="b"><i class="p" style="width:${(100 * v.p90_deg) / mx}%"></i><i class="m" style="width:${(100 * v.mean_deg) / mx}%"></i></div><span>${fmt(v.mean_deg)} / ${fmt(v.p90_deg)}${v.twist_mean_deg !== undefined ? ` · 自转 ${fmt(v.twist_mean_deg)}` : ""}</span></div>`)
    .join("");
  const vsrc = `/api/projects/${state.cur}/file/out/exam/lab.mp4`;
  if (r.has_video && !$("#examVid").src.endsWith(vsrc)) $("#examVid").src = vsrc;
}

// ---------------------------------------------------------------- outputs + 3D
let three = null;
function initThree() {
  if (three) return three;
  const el = $("#three");
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(el.clientWidth, el.clientHeight);
  el.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xc9c9c9);
  const cam = new THREE.PerspectiveCamera(35, el.clientWidth / el.clientHeight, 0.01, 200);
  cam.position.set(3, 1.6, 3);
  const ctl = new OrbitControls(cam, renderer.domElement);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x666666, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 2.2); sun.position.set(3, 6, 4); scene.add(sun);
  const grid = new THREE.GridHelper(20, 40, 0x888888, 0xaaaaaa); scene.add(grid);
  const clock = new THREE.Clock();
  three = { renderer, scene, cam, ctl, clock, mixer: null, action: null, root: null, playing: true, grid };
  const loop = () => {
    requestAnimationFrame(loop);
    const dt = clock.getDelta();
    if (three.mixer && three.playing) three.mixer.update(dt);
    if (three.action) {
      const d = three.action.getClip().duration;
      const t = three.action.time % d;
      if (!three.seeking) $("#tSeek").value = Math.round((1000 * t) / d);
      $("#tTime").textContent = `${t.toFixed(2)} / ${d.toFixed(2)} s`;
    }
    ctl.update();
    renderer.render(scene, cam);
  };
  loop();
  new ResizeObserver(() => {
    renderer.setSize(el.clientWidth, el.clientHeight);
    cam.aspect = el.clientWidth / el.clientHeight; cam.updateProjectionMatrix();
  }).observe(el);
  $("#tPlay").addEventListener("click", () => { three.playing = !three.playing; $("#tPlay").textContent = three.playing ? "暂停" : "播放"; });
  $("#tSeek").addEventListener("input", (e) => {
    if (!three.action) return;
    three.seeking = true;
    three.action.time = (e.target.value / 1000) * three.action.getClip().duration;
    three.mixer.update(0);
  });
  $("#tSeek").addEventListener("change", () => (three.seeking = false));
  return three;
}
function loadGlb(url) {
  const t = initThree();
  if (t.root) { t.scene.remove(t.root); t.root = null; t.mixer = null; t.action = null; }
  new GLTFLoader().load(url, (g) => {
    t.root = g.scene;
    t.scene.add(g.scene);
    // frame the animated model
    const box = new THREE.Box3().setFromObject(g.scene);
    const size = box.getSize(new THREE.Vector3()).length() || 1;
    const c = box.getCenter(new THREE.Vector3());
    t.grid.position.y = box.min.y;
    t.ctl.target.copy(c);
    t.cam.position.copy(c).add(new THREE.Vector3(size * 0.9, size * 0.45, size * 0.9));
    t.cam.near = size / 100; t.cam.far = size * 100; t.cam.updateProjectionMatrix();
    if (g.animations.length) {
      t.mixer = new THREE.AnimationMixer(g.scene);
      t.action = t.mixer.clipAction(g.animations[0]);
      t.action.play();
    }
  }, undefined, (e) => console.error(e));
}

async function renderOutputs() {
  if (!state.cur) return;
  const outs = await api(`/api/projects/${state.cur}/outputs`);
  if (!outs.length) { $("#outList").innerHTML = '<div class="hint">还没有结果。在"运行"页读一段视频。</div>'; return; }
  if (!state.outSel || !outs.find((o) => o.name === state.outSel)) state.outSel = outs[0].name;
  $("#outList").innerHTML = outs
    .map((o) => {
      const s = o.summary || {};
      const ok = o.check.same_move;
      return `<div class="item ${o.name === state.outSel ? "sel" : ""}" data-n="${o.name}"><b>${o.name}</b>
        <span class="badge ${ok === false ? "bad" : ok ? "ok" : ""}">${ok === false ? (s.forced ? "强制预览 · 未通过检查" : "需检查 · 有诊断视频") : ok ? "检查通过" : ""}</span>
        <div class="s">${s.frames ?? "–"} 帧 · 重投影 ${fmt(s.reproj_px_median, 2)}px · ${ago(o.mtime)}</div></div>`;
    })
    .join("");
  document.querySelectorAll("#outList .item").forEach((it) => it.addEventListener("click", () => { state.outSel = it.dataset.n; renderOutputs(); }));
  const o = outs.find((x) => x.name === state.outSel);
  const base = `/api/projects/${state.cur}/file/out/${o.name}/`;
  $("#outTitle").textContent = o.name;
  $("#outFiles").innerHTML = '<span class="files">' + o.files.filter((f) => /\.(fbx|glb|bvh|blend|mp4|json)$/.test(f))
    .map((f) => `<a href="${base}${f}" download>${f}</a>`).join("") + "</span>";
  const videoFile = o.files.includes("lab.mp4") ? "lab.mp4" : o.files.includes("diagnostic.mp4") ? "diagnostic.mp4" : null;
  const vs = base + videoFile;
  if (videoFile) { if (!$("#outVid").src.endsWith(vs)) $("#outVid").src = vs; } else { $("#outVid").removeAttribute("src"); $("#outVid").load(); }
  const glb = o.files.find((f) => f.endsWith(".glb"));
  if (glb && state._glb !== base + glb) { state._glb = base + glb; loadGlb(base + glb); }
  if (!glb) {
    state._glb = null;
    if (three && three.root) { three.scene.remove(three.root); three.root = null; three.mixer = null; three.action = null; }
    $("#tTime").textContent = "检查未通过，未生成新的 3D 动画";
  }
  const cv = await api(`/api/projects/${state.cur}/out/${o.name}/curve`);
  const res = (cv.residual || []).map((v, i) => [i, v === null ? null : 100 * v]);
  const bands = [];
  (cv.bad || []).forEach((b, i) => b && bands.push([i - 0.5, i + 0.5]));
  chart($("#cCheck"), [{ name: "射线偏差 %", color: "#e0a84a", data: res }], { y0: 0, xlabel: "frame", bands });
  const s = o.summary || {}, c = o.check || {};
  $("#stableRun").disabled = !o.files.includes("motion.npz");
  $("#outSummary").innerHTML = `
    <b>帧数 / fps</b><span>${s.frames ?? "–"} / ${fmt(s.fps, 2)}</span>
    <b>双视角几何检查</b><span>${c.same_move ? "通过" : "未通过（不等同于已确认动作不同）"} · 异常帧 ${c.bad_frames ?? "–"}</span>
    <b>近/远标签修正</b><span>${c.label_swaps_fixed ?? "–"} 次</span>
    <b>重投影误差</b><span>中位数 ${fmt(s.reproj_px_median, 2)}px · p90 ${fmt(s.reproj_px_p90, 2)}px</span>
    <b>处理耗时</b><span>${fmt(s.seconds, 0)} s</span>
    <b>求解模式</b><span>${s.solver === "temporal" ? "实验 · 可信度融合＋联合时序，无自动接触" : "旧版"}</span>`;
  if (o.stability) {
    const st = o.stability, before = st.before, after = st.after;
    const reduction = 100 * (1 - after.local_euler_accel_rms_deg_per_frame2 / before.local_euler_accel_rms_deg_per_frame2);
    $("#outSummary").insertAdjacentHTML("beforeend", `<b>稳定副本</b><span>源：${st.source} · ${st.cutoff_hz}Hz · 旋转二阶变化 RMS 减少 ${fmt(reduction)}%</span>
      <b>动作保留</b><span>根部高度幅度 ${fmt(100 * st.root_height_range_ratio)}% · 低位中心时刻变化 ${fmt(st.low_hold_centre_shift_seconds, 3)}s（相对原求解，不是真值）</span>`);
  }
  const d = o.diagnostics;
  if (d) {
    const ranges = (d.bad_ranges || []).map(r => `${fmt(r.start_seconds, 2)}–${fmt(r.end_seconds, 2)}s`).join(", ");
    const top = (d.keypoints || []).slice(0, 5).map(k => `${k.name} ${fmt(k.flagged_segment_median_ray_pct, 1)}%`).join(" · ");
    $("#outSummary").insertAdjacentHTML("beforeend", `<b>异常时间段</b><span>${ranges || "无"}</span>
      <b>异常段关键点偏差</b><span>${top}</span>
      <b>如何判断</b><span>诊断视频红圈为高偏差点，黄线为另一机位给出的极线约束。遮挡、识别错误和视频不一致都可能造成偏差；强制导出只供诊断。</span>`);
  }
}

// ---------------------------------------------------------------- run / jobs
async function renderRun() {
  await runPage.refresh();
  await renderJobs();
}
let jobSel = null;
async function renderJobs() {
  const jobs = await api("/api/jobs");
  const CMD = { pipeline: "全流程", prepare: "准备", gen: "生成训练图", train: "训练", exam: "考试", animate: "生成动画", stabilize: "生成稳定副本" };
  const ST = { queued: ["排队", ""], running: ["运行中", "run"], done: ["完成", "ok"], needs_review: ["需检查 · 诊断已保存", "run"], failed: ["失败", "bad"], cancelled: ["已取消", ""], interrupted: ["中断", "bad"] };
  $("#jobList").innerHTML = jobs.length
    ? jobs.map((j) => {
        const [s, c] = ST[j.status] || [j.status, ""];
        const dur = j.started ? ((j.ended || Date.now() / 1000) - j.started) / 60 : 0;
        return `<div class="item ${j.id === jobSel ? "sel" : ""}" data-id="${j.id}"><b>${CMD[j.cmd] || j.cmd}</b> · ${j.project}
          <span class="badge ${c}">${s}</span>${j.status === "running" || j.status === "queued" ? ` <button data-cancel="${j.id}">取消</button>` : ""}
          <div class="s">${ago(j.created)}${dur ? ` · ${dur.toFixed(1)} 分钟` : ""}</div></div>`;
      }).join("")
    : '<div class="hint">还没有任务</div>';
  document.querySelectorAll("#jobList .item").forEach((it) => it.addEventListener("click", (e) => {
    if (e.target.dataset.cancel) return;
    jobSel = it.dataset.id; renderJobs();
  }));
  document.querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", async () => { await api(`/api/jobs/${b.dataset.cancel}/cancel`, { method: "POST" }); renderJobs(); }));
  if (!jobSel && jobs.length) jobSel = jobs[0].id;
  if (jobSel) {
    const lg = await api(`/api/jobs/${jobSel}/log?tail=300`);
    const el = $("#jobLog");
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    el.textContent = lg.lines.join("\n");
    if (atBottom) el.scrollTop = el.scrollHeight;
  }
}
async function submit(project, cmd, args) {
  try {
    const j = await api("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project, cmd, args }) });
    jobSel = j.id;
    await renderJobs();
    return j;
  } catch (e) { alert("提交失败：" + e.message); return null; }
}
const runPage = initRunPage({api, submit, current: () => state.cur, projects: () => state.projects, selectProject});
$("#stableRun").addEventListener("click", async () => {
  if (!state.cur || !state.outSel) return;
  const name = prompt("新结果名称（不会覆盖原结果）", state.outSel + "_stable");
  if (!name) return;
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name)) return alert("请用字母、数字、_ - . 命名");
  await submit(state.cur, "stabilize", { source: state.outSel, name, cutoff: +$("#stableCutoff").value });
  document.querySelector('#tabs a[data-tab="run"]').click();
});

// ---------------------------------------------------------------- system + refresh
async function renderSys() {
  try {
    const s = await api("/api/system");
    const g = s.gpu[0];
    $("#sys").textContent = g ? `GPU ${g.util}% · ${(g.mem_used / 1024).toFixed(1)}/${(g.mem_total / 1024).toFixed(0)} GB · ${g.temp}°C · 磁盘剩余 ${s.disk_free_gb.toFixed(0)} GB` : "";
  } catch {}
}
async function refreshTab() {
  try {
    if (state.tab === "overview") await renderOverview();
    if (state.tab === "samples") await renderSamples();
    if (state.tab === "train") await renderTrain();
    if (state.tab === "exam") await renderExam();
    if (state.tab === "outputs") await renderOutputs();
    if (state.tab === "run") await renderRun();
  } catch (e) { console.error(e); }
}
(async () => {
  const [p, t] = location.hash.slice(1).split("/");
  if (p) state.cur = p;
  await loadProjects();
  if (t) document.querySelector(`#tabs a[data-tab="${t}"]`)?.click(); else refreshTab();
  renderSys();
  setInterval(renderSys, 5000);
  setInterval(() => { if (state.tab === "run") renderJobs(); if (state.tab === "train") renderTrain(); }, 4000);
  setInterval(loadProjects, 20000);
})();
