// "关键帧" page: candidate keyframe preview + artist annotation for one animation result.
// Contract: video2rig/docs/关键帧数据与网页接口约定_20261111.md. Pure logic lives in keyframes-core.js.
import * as K from "./keyframes-core.js?v=kf-v1";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const urlPart = (s) => encodeURIComponent(String(s));
const fmt = (n, d = 1) => n == null || !Number.isFinite(Number(n)) ? "–" : Number(n).toFixed(d);
const errorText = (e) => e?.message || String(e || "未知错误");
const CURVE_COLORS = ["#e3b66d", "#7cb4f8", "#65cba0", "#f18b80", "#c99cf0", "#5fd0d6", "#d47fc0", "#b8c4d4"];
const DEFAULT_CURVES = ["body_speed", "spin_yaw_deg", "L_foot_contact", "R_foot_contact"];
const AUTHOR_KEY = "v2r-kf-author";
const draftKey = (p, name) => `v2r-kf-draft:${p}/${name}`;

const TEMPLATE = `
<div class="kf-page">
  <div class="panel kf-head">
    <div class="toolbar">
      <label class="field kf-result-field" for="kfResult">动画结果<select id="kfResult" aria-label="选择要标注的动画结果"></select></label>
      <span id="kfStatus" class="badge"></span>
      <span id="kfSummary" class="hint kf-summary" role="status"></span>
      <span class="kf-spacer"></span>
      <button id="kfGenerate" type="button" hidden>生成候选关键帧</button>
      <button id="kfReload" type="button">重新读取</button>
    </div>
  </div>
  <div id="kfGenState" class="run-notice" role="status" hidden></div>
  <div id="kfNotice" class="run-notice" role="status" hidden></div>
  <div id="kfConflict" class="run-notice warning" role="alert" hidden></div>
  <div id="kfEmpty" class="panel empty-state" role="status">选择项目后标注关键帧。</div>
  <div id="kfBody" class="kf-body" hidden>
    <section class="panel kf-player" aria-label="视频与时间轴">
      <div class="toolbar kf-player-bar">
        <div id="kfViews" class="segmented" role="group" aria-label="画面范围"><button type="button" data-kf-view="full" aria-pressed="true">双视角</button><button type="button" data-kf-view="front" aria-pressed="false">只看正面</button><button type="button" data-kf-view="side" aria-pressed="false">只看侧面</button></div>
        <span id="kfVideoName" class="hint"></span>
      </div>
      <div id="kfStage" class="kf-stage"><div id="kfFrameBox" class="kf-framebox"><video id="kfVideo" playsinline muted preload="auto" aria-label="源视频"></video></div><div id="kfVideoState" class="empty-state kf-video-state" role="status" hidden></div></div>
      <div class="toolbar kf-controls">
        <button id="kfPlay" type="button" title="空格">播放</button>
        <button id="kfPrevKey" type="button" title="上一个关键帧（Shift+←）" aria-label="上一个关键帧">⇤ 上一键</button>
        <button id="kfPrev" type="button" title="前一帧（←）" aria-label="前一帧">◀ 前一帧</button>
        <button id="kfNext" type="button" title="后一帧（→）" aria-label="后一帧">后一帧 ▶</button>
        <button id="kfNextKey" type="button" title="下一个关键帧（Shift+→）" aria-label="下一个关键帧">下一键 ⇥</button>
        <label for="kfSpeed">速度 <select id="kfSpeed"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1" selected>1×</option></select></label>
        <label for="kfFrameInput">帧 <input id="kfFrameInput" class="kf-frame-input" type="number" min="0" step="1" inputmode="numeric"></label>
        <output id="kfFrameText" class="kf-frame-text"></output>
      </div>
      <canvas id="kfTimeline" class="kf-timeline" role="img" aria-label="关键帧时间轴：点击跳帧，拖动标注可改帧"></canvas>
      <div id="kfLegend" class="kf-legend"></div>
      <details id="kfCurvesBox" class="advanced kf-curves" open>
        <summary>判断依据曲线</summary>
        <div id="kfCurvePicks" class="kf-curve-picks"></div>
        <canvas id="kfCurves" class="kf-curve-canvas" role="img" aria-label="候选关键帧的判断依据曲线"></canvas>
      </details>
      <p class="hint kf-keys-help">快捷键：空格 播放/暂停 · ←/→ 逐帧 · Shift+←/→ 上/下一个关键帧 · K 在当前帧添加 · Delete 删除当前帧的关键帧</p>
    </section>
    <aside class="kf-side">
      <section class="panel" aria-labelledby="kfCurTitle"><h3 id="kfCurTitle">当前帧</h3><div id="kfCurrent"></div></section>
      <section class="panel" aria-labelledby="kfLadderTitle"><h3 id="kfLadderTitle">过渡键数量</h3><div id="kfLadder"></div></section>
      <section class="panel" aria-labelledby="kfSaveTitle"><h3 id="kfSaveTitle">保存标注</h3>
        <label class="field" for="kfAuthor">作者<input id="kfAuthor" maxlength="40" autocomplete="name" placeholder="例如：美术A"></label>
        <label class="field" for="kfNotes">总备注 <span id="kfNotesCount" class="hint"></span><textarea id="kfNotes" rows="3" maxlength="2000" placeholder="例如：转圈段想再加两个键"></textarea></label>
        <label class="kf-check" for="kfReviewed"><input id="kfReviewed" type="checkbox"> 已审核完毕</label>
        <div class="toolbar"><button id="kfSave" class="primary" type="button">保存</button><button id="kfExport" type="button">导出 JSON</button></div>
        <p id="kfSaveState" class="hint" role="status"></p>
      </section>
    </aside>
    <section class="panel kf-strip-panel" aria-labelledby="kfStripTitle">
      <div class="toolbar"><h3 id="kfStripTitle">关键帧预览</h3><select id="kfStripFilter" aria-label="筛选关键帧预览"></select><span id="kfStripCount" class="hint"></span></div>
      <div id="kfStrip" class="kf-strip"></div>
    </section>
  </div>
  <video id="kfThumbVideo" class="kf-thumb-video" muted playsinline preload="auto" aria-hidden="true" tabindex="-1"></video>
  <canvas id="kfThumbCanvas" hidden></canvas>
</div>`;

export function initKeyframes({ api, current, notify, submit, tasks = () => [], confirm = (t) => Promise.resolve(window.confirm(t)) }) {
  const host = document.querySelector("#keyframesContent");
  if (!host) throw new Error("Keyframes needs #keyframesContent");
  host.innerHTML = TEMPLATE;
  const el = Object.fromEntries([...host.querySelectorAll("[id]")].map((n) => [n.id, n]));
  const tab = document.getElementById("tab-keyframes");
  const desired = new Map();
  let project = null, outputs = [], selected = null, view = null, st = null;
  let baseRevision = 0, savedSig = "", fps = 24, frames = 1, frame = 0, selectedId = null;
  let playing = false, raf = 0, lastTs = 0, clockFrame = 0, rate = 1, viewMode = "full";
  let ladder = { ok: false, reason: "" }, ladderK = 0, showLadder = true;
  let listVersion = 0, viewVersion = 0, listAbort = null, viewAbort = null, refreshPending = null;
  let saving = false, conflict = null, drag = null, stripFilter = "all", curSig = "";
  let genBusy = false, genTask = null;   // the keyframes job this page is waiting for
  let curvePicks = new Set(DEFAULT_CURVES);
  const thumbs = new Map();
  let thumbQueue = [], thumbBusy = false, thumbToken = 0;

  const isVisible = () => tab?.classList.contains("active") && !document.hidden;
  const hasVideo = () => Boolean(view?.video && el.kfVideo.getAttribute("src") && el.kfVideo.readyState >= 1 && !el.kfVideo.error);
  const dirty = () => Boolean(st) && K.signature(st) !== savedSig;
  const autoById = (id) => K.autoKeys(view?.auto).find((k) => k.id === id) || null;
  const keyById = (id) => st?.keyframes.find((k) => k.id === id) || null;
  const resultUrl = (p, name) => `/api/projects/${urlPart(p)}/out/${urlPart(name)}/keyframes`;
  const activeView = (version, p, name) => version === viewVersion && p === project && name === selected && p === current();

  function setNotice(node, text, kind = "info") {
    node.textContent = text || "";
    node.dataset.kind = kind;
    node.hidden = !text;
  }
  const notice = (text, kind) => setNotice(el.kfNotice, text, kind);

  // ---------------------------------------------------------------- drafts (local, per result)
  function persistDraft() {
    if (!st || !project || !selected) return;
    try {
      if (dirty()) localStorage.setItem(draftKey(project, selected), JSON.stringify({ base: baseRevision, at: Date.now(), state: { keyframes: st.keyframes, removed: st.removed, author: st.author, notes: st.notes, reviewed: st.reviewed } }));
      else localStorage.removeItem(draftKey(project, selected));
    } catch { /* storage full or disabled: drafts are a convenience only */ }
  }
  function readDraft(p, name) {
    try { const d = JSON.parse(localStorage.getItem(draftKey(p, name)) || "null"); return d && Array.isArray(d.state?.keyframes) ? d : null; } catch { return null; }
  }
  function clearDraft(p = project, name = selected) { try { localStorage.removeItem(draftKey(p, name)); } catch { /* ignore */ } }

  // ---------------------------------------------------------------- loading
  function clearView(message) {
    viewVersion++;
    viewAbort?.abort();
    pause();
    releaseVideo();
    view = null; st = null; conflict = null; selectedId = null; frame = 0; savedSig = ""; curSig = "";
    el.kfBody.hidden = true;
    el.kfEmpty.hidden = !message;
    el.kfEmpty.textContent = message || "";
    setNotice(el.kfConflict, "");
    el.kfGenerate.hidden = true;
    setNotice(el.kfGenState, "");
    el.kfStatus.textContent = ""; el.kfStatus.className = "badge"; el.kfSummary.textContent = "";
  }
  function releaseVideo() {
    thumbToken++;
    thumbQueue = [];
    thumbs.clear();
    thumbFails.clear();
    stripObserver?.disconnect();
    for (const v of [el.kfVideo, el.kfThumbVideo]) {
      v.pause?.();
      if (v.getAttribute("src")) { v.removeAttribute("src"); v.load?.(); }
    }
  }
  function renderResultOptions() {
    el.kfResult.innerHTML = outputs.map((o) => {
      const tag = o.has_keyframes?.artist ? "已有标注" : o.has_keyframes?.auto ? "有候选" : "无候选";
      return `<option value="${esc(o.name)}">${esc(o.name)} · ${tag}</option>`;
    }).join("") || '<option value="">（没有动画结果）</option>';
    el.kfResult.value = selected || "";
  }
  function hashResult() {
    const parts = location.hash.slice(1).split("/");
    if (parts[1] !== "keyframes" || !parts[2]) return "";
    try { return decodeURIComponent(parts[2]); } catch { return ""; }
  }
  function writeHash() {
    if (!project || !selected) return;
    try { history.replaceState(null, "", `#${urlPart(project)}/keyframes/${urlPart(selected)}`); } catch { /* ignore */ }
  }
  async function refresh() {
    const p = current() || null;
    if (refreshPending && p === project) return refreshPending;
    const fromHash = hashResult();
    if (p && fromHash) desired.set(p, fromHash);
    // Same project: keep the in-memory annotation (also when it has unsaved edits).
    if (p && p === project && outputs.length) {
      const want = desired.get(p);
      if (want && want !== selected && outputs.some((o) => o.name === want)) {
        if (canLeave()) return loadResult(want);
        desired.set(p, selected);
      }
      if (view) { writeHash(); resize(); return; }
      if (selected) return loadResult(selected);
    }
    const version = ++listVersion;
    listAbort?.abort();
    const controller = new AbortController();
    listAbort = controller;
    project = p; outputs = []; selected = null;
    clearView(p ? "正在读取动画结果…" : "尚未选择项目。选择项目后标注关键帧。");
    notice("");
    renderResultOptions();
    if (!p) return;
    const work = (async () => {
      try {
        const rows = await api(`/api/projects/${urlPart(p)}/outputs`, { signal: controller.signal });
        if (version !== listVersion || p !== current()) return;
        outputs = (Array.isArray(rows) ? rows : []).filter((o) => o && typeof o.name === "string");
        if (!outputs.length) { renderResultOptions(); clearView("这个项目还没有动画结果。先在“生成动画”里处理视频。"); return; }
        const want = desired.get(p);
        const o = outputs.find((x) => x.name === want) || outputs.find((x) => x.has_keyframes?.artist) || outputs.find((x) => x.has_keyframes?.auto) || outputs[0];
        if (want && o.name !== want) notice(`没有找到结果“${want}”，当前显示“${o.name}”。`, "info");
        selected = o.name;
        renderResultOptions();
        await loadResult(o.name);
      } catch (e) {
        if (version !== listVersion || controller.signal.aborted) return;
        clearView("结果列表读取失败。");
        notice(`读取失败：${errorText(e)}`, "error");
      } finally {
        if (version === listVersion) refreshPending = null;
      }
    })();
    refreshPending = work;
    return work;
  }
  async function loadResult(name) {
    const p = project;
    clearView("正在读取关键帧…");
    selected = name;
    desired.set(p, name);
    renderResultOptions();
    const version = viewVersion;
    const controller = new AbortController();
    viewAbort = controller;
    try {
      const data = await api(resultUrl(p, name), { signal: controller.signal });
      if (!activeView(version, p, name)) return;
      showView(data);
      if (isVisible()) writeHash();
    } catch (e) {
      if (!activeView(version, p, name) || controller.signal.aborted) return;
      clearView("关键帧读取失败。可以点“重新读取”重试。");
      notice(`读取 ${name} 的关键帧失败：${errorText(e)}`, "error");
    }
  }
  function showView(data) {
    view = data || {};
    fps = Number(view.fps) > 0 ? Number(view.fps) : 0;
    frames = Number.isInteger(view.frames) && view.frames > 0 ? view.frames : 0;
    if (!fps || !frames) {
      clearView("无法确定这个结果的帧率和帧数（keyframes_auto.json 和 summary.json 都没有），不能标注。");
      renderGeneration();
      return;
    }
    baseRevision = Number.isInteger(view.revision) ? view.revision : 0;
    st = K.initialState(view);
    if (st.rebased) {
      const r = st.rebased;
      if (r.converted || r.removedDropped) {
        // The annotation was made on an older candidate file and some references changed: mark dirty so it gets saved.
        savedSig = "rebased";
        setTimeout(() => notice(`候选关键帧已重新生成，这份标注（r${baseRevision}）基于旧候选。已保留全部 ${st.keyframes.length} 个标注帧：${r.kept} 个仍对应新候选，${r.converted} 个改记为手动关键帧${r.removedDropped ? `，${r.removedDropped} 个“已删除的候选”在新候选里不存在、已忽略` : ""}。确认无误后请保存。`, "warning"), 0);
      } else {
        // Regenerated but every reference still matches (same ids at the same frames): nothing to save.
        delete st.rebased;
      }
    }
    if (!st.author) st.author = (() => { try { return localStorage.getItem(AUTHOR_KEY) || ""; } catch { return ""; } })();
    // "Dirty" means changed on this page; an unsaved first draft is reported separately (status badge).
    if (!st.rebased) savedSig = K.signature(st);
    ladder = K.ladderInfo(view.auto);
    ladderK = ladder.ok ? ladder.suggested : 0;
    frame = 0; clockFrame = 0; selectedId = null; curSig = "";
    const notes = [];
    if (view.errors?.auto) notes.push(`候选文件无法读取：${view.errors.auto}`);
    else if (!view.auto && view.generation?.available) notes.push("尚未生成候选关键帧。点右上角“生成候选关键帧”，或直接在视频上从零标注。");
    else if (!view.auto) notes.push("尚未生成候选关键帧（keyframes_auto.json）。可以直接在视频上从零标注。");
    if (view.errors?.artist) notes.push(`已有标注文件无法读取：${view.errors.artist}。保存时会基于 r${baseRevision} 生成新版本。`);
    if (view.auto && !view.auto.contacts_reviewed) notes.push("着地判断还没有人工确认，接触类候选仅供参考。");
    notice(notes.join("\n"), view.errors?.auto || view.errors?.artist ? "warning" : "info");
    el.kfEmpty.hidden = true;
    el.kfBody.hidden = false;
    el.kfAuthor.value = st.author;
    el.kfNotes.value = st.notes;
    el.kfReviewed.checked = st.reviewed;
    el.kfFrameInput.max = String(frames - 1);
    setupVideo();
    renderLegend();
    renderCurvePicks();
    renderLadder();
    renderStripFilter();
    offerDraft();
    renderAll();
    renderGeneration();
  }

  // ---------------------------------------------------------------- "生成候选关键帧" (web job)
  function myTask() {
    const rows = (tasks() || []).filter((t) => t?.cmd === "keyframes" && t.project === project && t.args?.name === selected);
    if (genTask) {
      const latest = rows.find((t) => t.id === genTask.id);
      if (latest) genTask = latest;
      return genTask;
    }
    return rows.find((t) => ["queued", "running"].includes(t.status)) || null;
  }
  function renderGeneration() {
    if (!project || !selected || !view) { el.kfGenerate.hidden = true; return; }
    const t = myTask();
    const g = K.generationState(view, t && ["queued", "running"].includes(t.status) ? [t] : []);
    el.kfGenerate.hidden = !g.show;
    el.kfGenerate.textContent = g.label;
    el.kfGenerate.disabled = !g.enabled || genBusy;
    el.kfGenerate.title = g.reason || (g.replace ? "重新抽取候选关键帧；旧候选会保留为历史版本，已保存的标注不会被删除" : "从这个结果的求解动作和识别读数中抽取候选关键帧（约 10–30 秒）");
    el.kfGenerate.classList.toggle("primary", g.enabled && !view.auto);
    if (t && ["queued", "running"].includes(t.status)) {
      const phase = t.phase?.label ? ` · ${t.phase.label}${t.phase.current != null && t.phase.total ? `（${t.phase.current}/${t.phase.total}）` : ""}` : "";
      setNotice(el.kfGenState, `${t.status === "queued" ? "生成候选关键帧的任务正在排队（前面有任务时会等待）" : "正在生成候选关键帧"}${phase}。完成后自动刷新，可以离开这个页面。`, "info");
    } else if (t && genTask && t.status === "done") {
      genTask = null;
      setNotice(el.kfGenState, "候选关键帧已生成。", "success");
      reloadAfterGeneration();
    } else if (t && genTask && ["failed", "cancelled", "interrupted", "needs_review"].includes(t.status)) {
      genTask = null;
      setNotice(el.kfGenState, `生成候选关键帧失败：${t.error || "请在任务中心查看日志"}。`, "error");
    } else if (!view.auto && !g.enabled && g.reason) {
      setNotice(el.kfGenState, `不能生成候选关键帧：${g.reason}。仍可在视频上从零标注。`, "info");
    } else if (el.kfGenState.dataset.kind !== "success" && el.kfGenState.dataset.kind !== "error") {
      setNotice(el.kfGenState, "");
    }
  }
  async function reloadAfterGeneration() {
    const o = outputs.find((x) => x.name === selected);
    if (o) { o.has_keyframes = { ...(o.has_keyframes || {}), auto: true }; renderResultOptions(); }
    if (dirty()) {
      persistDraft();
      setNotice(el.kfGenState, "候选关键帧已生成。你有未保存的修改，已暂存为本机草稿；点“重新读取”载入新候选（草稿可以恢复）。", "success");
      return;
    }
    const keep = selected;
    if (project && keep) desired.set(project, keep);
    await loadResult(keep);
    setNotice(el.kfGenState, "候选关键帧已生成并载入。", "success");
  }
  async function generate() {
    if (!view || genBusy || !submit) return;
    const g = K.generationState(view, []);
    if (!g.enabled) return;
    if (g.replace && !(await confirm("重新生成会替换这个结果的候选关键帧。旧的候选文件会保留为历史版本；已保存的美术标注不会删除，但其中引用旧候选的条目会在下次打开时改记为手动关键帧。继续吗？"))) return;
    const p = project, name = selected;
    genBusy = true; renderGeneration();
    try {
      const job = await submit(p, "keyframes", { name, replace: g.replace === true });
      if (p !== project || name !== selected) return;
      genTask = { ...job, cmd: "keyframes", project: p, status: job?.status || "queued", args: { name } };
      setNotice(el.kfGenState, "已提交生成候选关键帧的任务，完成后自动刷新。", "info");
    } catch (e) {
      if (p === project && name === selected) setNotice(el.kfGenState, `提交失败：${errorText(e)}`, "error");
    } finally {
      genBusy = false;
      if (p === project && name === selected) renderGeneration();
    }
  }
  function onTasks() {
    if (!view || !isVisible()) return;
    renderGeneration();
  }
  function offerDraft() {
    const d = readDraft(project, selected);
    if (!d) return;
    const draftState = { ...st, ...d.state, removed: d.state.removed || [] };
    if (K.signature(draftState) === savedSig) { clearDraft(); return; }
    const when = new Date(d.at || 0).toLocaleString();
    const stale = d.base !== baseRevision;
    el.kfConflict.innerHTML = `<span>本机有 ${esc(when)} 未保存的修改${stale ? `（基于 r${esc(d.base)}，服务器现在是 r${esc(baseRevision)}）` : ""}。</span> <button type="button" data-kf-draft="restore">恢复这些修改</button> <button type="button" class="text-button" data-kf-draft="drop">丢弃</button>`;
    el.kfConflict.dataset.kind = "warning";
    el.kfConflict.hidden = false;
    el.kfConflict.onclick = (event) => {
      const b = event.target.closest("[data-kf-draft]");
      if (!b) return;
      if (b.dataset.kfDraft === "restore") {
        st = { ...st, keyframes: d.state.keyframes.map((k) => ({ ...k })), removed: [...(d.state.removed || [])], author: d.state.author ?? st.author, notes: d.state.notes ?? st.notes, reviewed: d.state.reviewed === true };
        el.kfAuthor.value = st.author; el.kfNotes.value = st.notes; el.kfReviewed.checked = st.reviewed;
        notice("已恢复本机草稿，记得保存。", "info");
      } else clearDraft();
      setNotice(el.kfConflict, ""); el.kfConflict.onclick = null;
      curSig = ""; renderAll();
    };
  }

  // ---------------------------------------------------------------- video
  function setupVideo() {
    const v = view.video;
    el.kfVideoName.textContent = v ? `源视频：${v.name} · ${fmt(fps, 0)} fps · ${frames} 帧` : `${frames} 帧 · ${fmt(fps, 0)} fps`;
    if (!v?.url) {
      el.kfVideo.hidden = true;
      el.kfVideoState.hidden = false;
      el.kfVideoState.innerHTML = `<h2>找不到源视频</h2><p>${view.video_name ? `服务器 videos 目录里没有 ${esc(view.video_name)}。` : "这个结果没有记录源视频。"}时间轴、曲线和标注仍可使用。</p>`;
      return;
    }
    el.kfVideo.hidden = false;
    el.kfVideoState.hidden = false;
    el.kfVideoState.innerHTML = "<p>正在加载视频…</p>";
    const token = thumbToken;
    el.kfVideo.onloadedmetadata = () => {
      if (token !== thumbToken) return;
      el.kfVideoState.hidden = true;
      const expect = frames / fps;
      if (Math.abs((el.kfVideo.duration || 0) - expect) > 2 / fps) notice(`${el.kfVideo.duration.toFixed(2)} s 的视频与 ${frames} 帧 / ${fps} fps（${expect.toFixed(2)} s）不一致，帧号按 ${fps} fps 换算，请留意对位。`, "warning");
      sizeStage();
      seek(frame);
    };
    el.kfVideo.onerror = () => {
      if (token !== thumbToken) return;
      el.kfVideoState.hidden = false;
      el.kfVideoState.innerHTML = "<h2>视频无法播放</h2><p>时间轴、曲线和标注仍可使用。</p>";
    };
    el.kfVideo.onseeked = () => { if (!playing) updateFrameUI(); };
    el.kfVideo.onended = () => { pause(); };
    el.kfVideo.playbackRate = rate;
    el.kfVideo.src = v.url;
    el.kfVideo.load?.();
    el.kfThumbVideo.onloadedmetadata = () => { if (token === thumbToken) pumpThumbs(); };
    el.kfThumbVideo.src = v.url;
    el.kfThumbVideo.load?.();
  }
  function sizeStage() {
    const v = el.kfVideo;
    const vw = v.videoWidth || 16, vh = v.videoHeight || 9;
    const half = viewMode !== "full";
    const aspect = (half ? vw / 2 : vw) / vh;
    const W = el.kfStage.clientWidth || 0, H = el.kfStage.clientHeight || 0;
    if (!W || !H) return;
    const w = Math.min(W, H * aspect), h = w / aspect;
    Object.assign(el.kfFrameBox.style, { width: `${Math.floor(w)}px`, height: `${Math.floor(h)}px` });
    Object.assign(v.style, { width: half ? "200%" : "100%", left: viewMode === "side" ? "-100%" : "0" });
  }
  function seek(f) {
    frame = K.clampFrame(f, frames);
    clockFrame = frame;
    if (hasVideo()) {
      const t = K.frameToTime(frame, fps);
      if (Math.abs((el.kfVideo.currentTime || 0) - t) > 0.25 / fps) el.kfVideo.currentTime = Math.min(t, Math.max(0, (el.kfVideo.duration || t) - 0.001));
    }
    updateFrameUI();
  }
  function play() {
    if (!st || playing) return;
    if (frame >= frames - 1) seek(0);
    playing = true;
    el.kfPlay.textContent = "暂停";
    lastTs = 0;
    if (hasVideo()) {
      el.kfVideo.playbackRate = rate;
      const r = el.kfVideo.play?.();
      r?.catch?.(() => pause());
    }
    raf = requestAnimationFrame(loop);
  }
  function pause() {
    if (!playing && !raf) return;
    playing = false;
    cancelAnimationFrame(raf); raf = 0;
    el.kfVideo.pause?.();
    el.kfPlay.textContent = "播放";
    if (st && hasVideo()) frame = K.timeToFrame(el.kfVideo.currentTime, fps, frames);
    if (st) updateFrameUI();
  }
  function loop(ts) {
    if (!playing) return;
    if (hasVideo()) {
      const f = K.timeToFrame(el.kfVideo.currentTime, fps, frames);
      if (f !== frame) { frame = f; updateFrameUI(); }
    } else {
      if (lastTs) clockFrame += (ts - lastTs) / 1000 * fps * rate;
      lastTs = ts;
      const f = K.clampFrame(Math.floor(clockFrame), frames);
      if (f !== frame) { frame = f; updateFrameUI(); }
      if (clockFrame >= frames - 1) { pause(); return; }
    }
    raf = requestAnimationFrame(loop);
  }
  function stepKey(dir) {
    if (!st?.keyframes.length) return;
    const list = st.keyframes.map((k) => k.frame);
    const target = dir > 0 ? list.find((f) => f > frame) : [...list].reverse().find((f) => f < frame);
    if (target != null) { pause(); seek(target); }
  }

  // ---------------------------------------------------------------- mutations
  function apply(result, { focusId } = {}) {
    if (result.message) notice(result.message, result.ok ? "success" : "error");
    if (!result.ok) return false;
    st = result.state;
    if (focusId !== undefined) selectedId = focusId;
    if (selectedId && !keyById(selectedId)) selectedId = null;
    persistDraft();
    curSig = "";
    renderAll();
    return true;
  }
  function addHere(type) {
    if (!st) return;
    const r = K.addKey(st, frame, type || el.kfAddType?.value || "custom", frames);
    apply(r, { focusId: r.id ?? selectedId });
  }
  function removeHere() {
    const k = st && K.keyAt(st, frame);
    if (k) apply(K.removeKey(st, k.id));
  }

  // ---------------------------------------------------------------- rendering
  function renderAll() {
    if (!st) return;
    renderStatus();
    renderStrip();
    updateFrameUI();
  }
  function renderStatus() {
    const d = K.diffSummary(st, view?.auto);
    el.kfSummary.textContent = K.summaryText(d);
    let text, kind;
    if (dirty()) [text, kind] = ["有未保存的修改", "warning"];
    else if (!st.fromArtist) [text, kind] = ["初稿 · 尚未保存", "warning"];
    else [text, kind] = [`已保存 r${baseRevision}${st.reviewed ? " · 已审核" : ""}`, st.reviewed ? "ok" : ""];
    el.kfStatus.textContent = text;
    el.kfStatus.className = `badge ${kind}`;
    el.kfSave.disabled = saving || !st;
    el.kfNotesCount.textContent = `${(st.notes || "").length}/${K.LIMITS.notes}`;
    const a = view?.artist;
    el.kfSaveState.textContent = a ? `服务器版本 r${baseRevision} · ${a.author || "未署名"} · ${a.saved_at || ""}` : "服务器上还没有标注。保存后生成 r1。";
  }
  function renderLegend() {
    const used = new Set(K.autoKeys(view?.auto).map((k) => k.type));
    for (const k of st?.keyframes || []) used.add(k.type);
    const items = K.TYPES.filter((t) => used.has(t)).map((t) => `<span><i style="background:${K.TYPE_COLOR[t]}"></i>${K.TYPE_LABEL[t]}</span>`);
    items.push('<span><i class="kf-legend-ladder"></i>建议过渡键（未采纳）</span>', '<span><i class="kf-legend-removed"></i>已删除的候选</span>', '<span><i class="kf-legend-moved"></i>挪动（原位置 → 新位置）</span>');
    el.kfLegend.innerHTML = items.join("");
  }
  function renderCurvePicks() {
    const curves = view?.auto?.curves || {};
    const labels = view?.auto?.curve_labels || {};
    const names = Object.keys(curves).filter((n) => Array.isArray(curves[n]) && curves[n].length);
    curvePicks = new Set([...curvePicks].filter((n) => names.includes(n)));
    if (!curvePicks.size) for (const n of DEFAULT_CURVES) if (names.includes(n)) curvePicks.add(n);
    el.kfCurvesBox.hidden = !names.length;
    el.kfCurvePicks.innerHTML = names.map((n) => `<label><input type="checkbox" data-kf-curve="${esc(n)}" ${curvePicks.has(n) ? "checked" : ""}> ${esc(labels[n] || n)}</label>`).join("");
  }
  function renderLadder() {
    if (!ladder.ok) {
      el.kfLadder.innerHTML = `<p class="hint">${esc(ladder.reason || "没有候选文件，无法给出过渡键建议。")}</p>`;
      return;
    }
    const n = ladder.breakdowns.length;
    el.kfLadder.innerHTML = `
      <p class="hint">结构键固定后，过渡键按“补上后插值偏差下降最多”的顺序排名。拖动滑杆只在末尾增减，前面的键不变。</p>
      <div class="kf-ladder-row"><input id="kfLadderRange" type="range" min="0" max="${n}" step="1" value="${ladderK}" aria-label="过渡键数量"><output id="kfLadderK" for="kfLadderRange"></output></div>
      <p id="kfLadderStats" class="kf-ladder-stats"></p>
      <label class="kf-check"><input id="kfLadderShow" type="checkbox" ${showLadder ? "checked" : ""}> 在时间轴显示建议过渡键</label>
      <div class="toolbar"><button id="kfLadderAdopt" type="button">采纳前 ${ladderK} 个过渡键</button><button id="kfLadderReset" type="button" class="text-button">回到建议值 ${ladder.suggested}</button></div>`;
    const range = el.kfLadder.querySelector("#kfLadderRange");
    range.addEventListener("input", () => { ladderK = Number(range.value); updateLadderText(); drawTimeline(); if (stripFilter === "ladder") renderStrip(); });
    el.kfLadder.querySelector("#kfLadderShow").addEventListener("change", (e) => { showLadder = e.target.checked; drawTimeline(); });
    el.kfLadder.querySelector("#kfLadderAdopt").addEventListener("click", () => {
      const r = K.adoptLadder(st, ladder, ladderK);
      apply({ ...r, ok: r.added > 0, message: r.message });
    });
    el.kfLadder.querySelector("#kfLadderReset").addEventListener("click", () => { ladderK = ladder.suggested; range.value = String(ladderK); updateLadderText(); drawTimeline(); if (stripFilter === "ladder") renderStrip(); });
    updateLadderText();
  }
  function updateLadderText() {
    if (!ladder.ok) return;
    const c = K.curveAt(ladder, ladderK), c0 = K.curveAt(ladder, 0);
    const out = el.kfLadder.querySelector("#kfLadderK");
    if (out) out.textContent = `${ladderK} / ${ladder.breakdowns.length}${ladderK === ladder.suggested ? "（建议）" : ""}`;
    const stats = el.kfLadder.querySelector("#kfLadderStats");
    if (stats) {
      const spin = c?.spin && ladder.spinFrames ? `<br>转圈段（第 ${esc(ladder.spinFrames[0])}–${esc(ladder.spinFrames[1])} 帧）最大 ${fmt(c.spin.max_cm)} cm，补过渡键也降不下来。` : "";
      stats.innerHTML = c ? `非转圈段身体偏差：最大 <b>${fmt(c.max_cm)}</b> cm · p95 <b>${fmt(c.p95_cm)}</b> cm${c0 ? `<span class="hint">（只有结构键时 ${fmt(c0.max_cm)} / ${fmt(c0.p95_cm)}）</span>` : ""}${spin}` : "没有这个数量的偏差数据。";
    }
    const adopt = el.kfLadder.querySelector("#kfLadderAdopt");
    if (adopt) { adopt.textContent = `采纳前 ${ladderK} 个过渡键`; adopt.disabled = !ladderK; }
  }
  function renderStripFilter() {
    const opts = [["all", "全部标注"], ["auto", "来自候选"], ["artist", "手动新增"], ["ladder_adopted", "已采纳的过渡键"], ["ladder", "建议过渡键（未采纳）"], ["removed", "已删除的候选"]];
    if (!opts.some(([v]) => v === stripFilter)) stripFilter = "all";
    el.kfStripFilter.innerHTML = opts.map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
    el.kfStripFilter.value = stripFilter;
  }
  function stripItems() {
    if (stripFilter === "removed") return st.removed.map((id) => autoById(id)).filter(Boolean).map((a) => ({ kind: "removed", id: a.id, frame: a.frame, type: a.type, auto: a }));
    if (stripFilter === "ladder") {
      if (!ladder.ok) return [];
      return ladder.breakdowns.slice(0, ladderK).filter((b) => !st.keyframes.some((k) => k.origin === "ladder" && k.ladder_rank === b.rank)).map((b) => ({ kind: "ladder", id: `rank${b.rank}`, frame: b.frame, type: "breakdown", step: b }));
    }
    const keys = st.keyframes.filter((k) => stripFilter === "all" || (stripFilter === "ladder_adopted" ? k.origin === "ladder" : k.origin === stripFilter));
    return keys.map((k) => ({ kind: "key", id: k.id, frame: k.frame, type: k.type, key: k, auto: k.auto_id ? autoById(k.auto_id) : null, step: k.origin === "ladder" && ladder.ok ? ladder.breakdowns.find((b) => b.rank === k.ladder_rank) : null }));
  }
  function originText(k) {
    if (!k) return "";
    if (k.origin === "auto") return `候选 ${k.auto_id}${k.auto_frame != null && k.auto_frame !== k.frame ? `，原第 ${k.auto_frame} 帧 → 第 ${k.frame} 帧` : ""}`;
    if (k.origin === "ladder") return `过渡键第 ${k.ladder_rank} 名${k.auto_frame != null && k.auto_frame !== k.frame ? `，原第 ${k.auto_frame} 帧` : ""}`;
    return `手动新增 ${k.id}`;
  }
  function partsText(parts) {
    return Array.isArray(parts) && parts.length ? parts.map((p) => K.PART_LABEL[p] || p).join(" · ") : "";
  }
  function renderStrip() {
    const items = stripItems();
    el.kfStripCount.textContent = `${items.length} 个`;
    if (!items.length) {
      const empty = { removed: "没有删除过候选。", ladder: ladder.ok ? "前 k 个建议过渡键都已采纳，或滑杆为 0。" : "没有过渡键阶梯。", artist: "还没有手动新增的关键帧。按 K 在当前帧添加。", ladder_adopted: "还没有采纳过渡键。" }[stripFilter] || "还没有关键帧。按 K 在当前帧添加。";
      el.kfStrip.innerHTML = `<p class="empty-inline">${empty}</p>`;
      return;
    }
    el.kfStrip.innerHTML = items.map((it) => {
      const color = K.TYPE_COLOR[it.type] || "#9aa8bb";
      const label = K.TYPE_LABEL[it.type] || it.type;
      const reason = it.auto?.reason || it.step?.reason || "";
      const conf = it.auto?.confidence != null ? ` · 置信 ${fmt(it.auto.confidence, 2)}` : "";
      const meta = it.kind === "removed" ? `已删除的候选 ${it.id}${conf}` : it.kind === "ladder" ? `建议过渡键第 ${it.step.rank} 名 · ${it.step.worst_part || ""} ${fmt(it.step.error_here_cm)} cm` : `${originText(it.key)}${conf}`;
      const parts = partsText(it.auto?.parts || it.step?.parts);
      const note = it.key?.note ? `<p class="kf-card-note">${esc(it.key.note)}</p>` : "";
      const action = it.kind === "removed" ? `<button type="button" data-kf-restore="${esc(it.id)}">恢复</button>` : it.kind === "ladder" ? `<button type="button" data-kf-adopt="${esc(it.step.rank)}">采纳</button>` : "";
      return `<div class="kf-card kf-${it.kind}${it.frame === frame ? " cur" : ""}${it.kind === "key" && it.id === selectedId ? " sel" : ""}" data-kf-card-frame="${it.frame}" style="--kf-color:${color}">
        <button type="button" class="kf-card-hit" data-kf-seek="${it.frame}" data-kf-id="${it.kind === "key" ? esc(it.id) : ""}" aria-label="跳到第 ${it.frame} 帧（${esc(label)}）"><img data-thumb-frame="${it.frame}" alt=""><span class="kf-card-frame">#${it.frame}</span></button>
        <div class="kf-card-meta"><span class="kf-type" style="color:${color}">${esc(label)}</span><span class="hint">${esc(meta)}</span></div>
        ${parts ? `<div class="hint">${esc(parts)}</div>` : ""}${note}
        ${reason ? `<details><summary>判断依据</summary><p>${esc(reason)}</p></details>` : ""}${action}
      </div>`;
    }).join("");
    // Thumbnails: cards in view first (the strip scrolls sideways), the rest afterwards.
    thumbQueue = [];
    stripObserver?.disconnect();
    const missing = [];
    for (const img of el.kfStrip.querySelectorAll("img[data-thumb-frame]")) {
      const f = Number(img.dataset.thumbFrame);
      const url = thumbs.get(thumbKey(f));
      if (url) img.src = url;
      else { missing.push(f); stripObserver?.observe(img); }
    }
    for (const f of missing) requestThumb(f);
  }
  function renderCurrent() {
    if (!st) return;
    const here = K.keyAt(st, frame);
    const sel = selectedId ? keyById(selectedId) : null;
    const sig = JSON.stringify([frame, here?.id, here?.type, sel?.id, sel?.frame, st.keyframes.length]);
    if (sig === curSig) return;
    curSig = sig;
    const t = (frame / fps).toFixed(2);
    let html = `<p class="kf-cur-frame">第 <b>${frame}</b> 帧 · ${t} s</p>`;
    if (here) {
      const a = here.auto_id ? autoById(here.auto_id) : null;
      const step = here.origin === "ladder" && ladder.ok ? ladder.breakdowns.find((b) => b.rank === here.ladder_rank) : null;
      const reason = a?.reason || step?.reason || "";
      html += `<div class="kf-cur-key" style="--kf-color:${K.TYPE_COLOR[here.type]}">
        <p><span class="kf-type" style="color:${K.TYPE_COLOR[here.type]}">${esc(K.TYPE_LABEL[here.type])}</span> <span class="hint">${esc(originText(here))}</span></p>
        <label class="field" for="kfCurType">类型<select id="kfCurType">${K.TYPES.map((x) => `<option value="${x}" ${x === here.type ? "selected" : ""}>${K.TYPE_LABEL[x]}</option>`).join("")}</select></label>
        <label class="field" for="kfCurNote">备注 <span id="kfCurNoteCount" class="hint">${(here.note || "").length}/${K.LIMITS.note}</span><textarea id="kfCurNote" rows="2" maxlength="${K.LIMITS.note}" placeholder="例如：再晚两帧才到头">${esc(here.note || "")}</textarea></label>
        <div class="toolbar"><button id="kfCurDelete" type="button">删除这个关键帧</button></div>
        ${reason ? `<details><summary>判断依据</summary><p class="hint">${esc(reason)}</p></details>` : ""}</div>`;
    } else {
      html += `<p class="hint">这一帧没有关键帧。</p><div class="kf-add-row"><label class="field" for="kfAddType">类型<select id="kfAddType">${K.TYPES.map((x) => `<option value="${x}" ${x === "custom" ? "selected" : ""}>${K.TYPE_LABEL[x]}</option>`).join("")}</select></label><button id="kfAdd" type="button" class="primary">在第 ${frame} 帧添加（K）</button></div>`;
    }
    if (sel && sel.frame !== frame) {
      html += `<div class="kf-move-row"><p class="hint">已选：${esc(K.TYPE_LABEL[sel.type])}，第 ${sel.frame} 帧</p><div class="toolbar"><button id="kfMoveHere" type="button" ${here ? "disabled" : ""}>移到当前帧（第 ${frame} 帧）</button><button id="kfDeselect" type="button" class="text-button">取消选择</button></div>${here ? '<p class="hint">当前帧已有关键帧，不能移过来。</p>' : ""}</div>`;
    }
    el.kfCurrent.innerHTML = html;
    const q = (id) => el.kfCurrent.querySelector(`#${id}`);
    q("kfCurType")?.addEventListener("change", (e) => { apply(K.setKeyField(st, here.id, "type", e.target.value)); renderLegend(); });
    q("kfCurNote")?.addEventListener("input", (e) => {
      const r = K.setKeyField(st, here.id, "note", e.target.value);
      if (r.ok) { st = r.state; persistDraft(); renderStatus(); q("kfCurNoteCount").textContent = `${e.target.value.length}/${K.LIMITS.note}`; }
    });
    q("kfCurNote")?.addEventListener("change", () => renderStrip());
    q("kfCurDelete")?.addEventListener("click", () => apply(K.removeKey(st, here.id)));
    q("kfAdd")?.addEventListener("click", () => addHere(q("kfAddType").value));
    q("kfMoveHere")?.addEventListener("click", () => apply(K.moveKey(st, sel.id, frame, frames)));
    q("kfDeselect")?.addEventListener("click", () => { selectedId = null; curSig = ""; renderAll(); });
  }
  function updateFrameUI() {
    if (!st) return;
    el.kfFrameText.textContent = `/ ${frames - 1} · ${(frame / fps).toFixed(2)} s`;
    if (document.activeElement !== el.kfFrameInput) el.kfFrameInput.value = String(frame);
    for (const c of el.kfStrip.querySelectorAll("[data-kf-card-frame]")) c.classList.toggle("cur", Number(c.dataset.kfCardFrame) === frame);
    renderCurrent();
    drawTimeline();
    drawCurves();
  }

  // ---------------------------------------------------------------- canvases
  function prepCanvas(canvas, height) {
    const W = canvas.clientWidth;
    if (!W) return null;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.height = `${height}px`;
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, height);
    return { g, W, H: height };
  }
  const PAD = 10;
  const xOf = (f, W) => PAD + (frames > 1 ? f / (frames - 1) : 0) * (W - 2 * PAD);
  const frameOf = (x, W) => K.clampFrame(Math.round(((x - PAD) / Math.max(1, W - 2 * PAD)) * (frames - 1)), frames);
  function drawTimeline() {
    if (!st || el.kfBody.hidden) return;
    const c = prepCanvas(el.kfTimeline, 92);
    if (!c) return;
    const { g, W, H } = c;
    g.font = "11px sans-serif";
    if (ladder.ok && ladder.spinFrames) {
      const [a, b] = ladder.spinFrames;
      g.fillStyle = "#ffffff0d";
      g.fillRect(xOf(a, W), 14, Math.max(2, xOf(b, W) - xOf(a, W)), H - 14);
      g.fillStyle = "#9aa8bb";
      g.fillText("快速转圈", xOf(a, W) + 3, H - 4);
    }
    g.strokeStyle = "#2d3a4b";
    g.fillStyle = "#9aa8bb";
    const labelEvery = fps >= 20 ? Math.round(fps) : 10;
    for (let f = 0; f < frames; f++) {
      const x = Math.round(xOf(f, W)) + 0.5;
      const big = f % labelEvery === 0, mid = f % Math.max(1, Math.round(labelEvery / 4)) === 0;
      if (!big && !mid && W / frames < 3) continue;
      g.beginPath(); g.moveTo(x, 0); g.lineTo(x, big ? 10 : mid ? 6 : 3); g.stroke();
      if (big) g.fillText(String(f), Math.min(W - 24, x + 2), 10);
    }
    // removed candidates and ladder preview (lane 2)
    for (const id of st.removed) {
      const a = autoById(id);
      if (!a) continue;
      const x = xOf(a.frame, W);
      g.strokeStyle = "#6b7a8f"; g.lineWidth = 1;
      g.strokeRect(x - 2.5, 62, 5, 10);
    }
    if (ladder.ok && showLadder) {
      for (const b of ladder.breakdowns.slice(0, ladderK)) {
        if (st.keyframes.some((k) => k.origin === "ladder" && k.ladder_rank === b.rank)) continue;
        const x = xOf(b.frame, W);
        g.strokeStyle = "#b8c4d4"; g.lineWidth = 1.2;
        g.beginPath(); g.moveTo(x, 60); g.lineTo(x - 4, 70); g.lineTo(x + 4, 70); g.closePath(); g.stroke();
      }
    }
    // annotation lane
    for (const k of st.keyframes) {
      const shown = drag?.id === k.id ? drag.frame : k.frame;
      const x = xOf(shown, W);
      if (k.auto_frame != null && k.auto_frame !== shown) {
        const x0 = xOf(k.auto_frame, W);
        g.strokeStyle = "#9aa8bb88"; g.lineWidth = 1;
        g.beginPath(); g.moveTo(x0, 56); g.lineTo(x, 48); g.stroke();
        g.beginPath(); g.arc(x0, 57, 2, 0, Math.PI * 2); g.stroke();
      }
      const color = K.TYPE_COLOR[k.type] || "#9aa8bb";
      const sel = k.id === selectedId;
      g.fillStyle = color;
      if (k.origin === "ladder") { g.globalAlpha = 0.75; g.fillRect(x - 1.5, 22, 3, 24); g.globalAlpha = 1; }
      else g.fillRect(x - 2, 18, 4, 30);
      if (k.origin === "artist") { g.beginPath(); g.moveTo(x, 12); g.lineTo(x + 4, 17); g.lineTo(x, 22); g.lineTo(x - 4, 17); g.closePath(); g.fill(); }
      if (sel) { g.strokeStyle = "#ffffff"; g.lineWidth = 1.5; g.strokeRect(x - 4, 15, 8, 36); }
    }
    // playhead
    const px = Math.round(xOf(frame, W)) + 0.5;
    g.strokeStyle = "#f18b80"; g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(px, 0); g.lineTo(px, H); g.stroke();
    if (drag && drag.frame !== drag.from) {
      g.fillStyle = "#ffffff";
      g.fillText(`→ ${drag.frame}`, Math.min(W - 40, xOf(drag.frame, W) + 6), 58);
    }
  }
  function drawCurves() {
    if (!st || el.kfBody.hidden || el.kfCurvesBox.hidden || !el.kfCurvesBox.open) return;
    const c = prepCanvas(el.kfCurves, 132);
    if (!c) return;
    const { g, W, H } = c;
    const curves = view?.auto?.curves || {}, labels = view?.auto?.curve_labels || {}, units = view?.auto?.curve_units || {};
    const picks = [...curvePicks].filter((n) => Array.isArray(curves[n]));
    const bands = picks.filter((n) => /_contact$/.test(n));
    const lines = picks.filter((n) => !bands.includes(n));
    const top = 18, bottom = H - 6 - bands.length * 8;
    g.font = "11px sans-serif";
    for (const k of st.keyframes) { g.fillStyle = "#ffffff12"; g.fillRect(xOf(k.frame, W) - 0.5, top, 1, bottom - top); }
    let lx = PAD;
    lines.forEach((n, i) => {
      const data = curves[n].map((v) => (v == null || !Number.isFinite(Number(v)) ? null : Number(v)));
      const vals = data.filter((v) => v != null);
      if (!vals.length) return;
      const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
      const color = CURVE_COLORS[i % CURVE_COLORS.length];
      g.strokeStyle = color; g.lineWidth = 1.4; g.beginPath();
      let first = true;
      data.forEach((v, f) => {
        if (v == null) { first = true; return; }
        const x = xOf(f, W), y = bottom - ((v - lo) / span) * (bottom - top);
        if (first) g.moveTo(x, y); else g.lineTo(x, y);
        first = false;
      });
      g.stroke();
      const text = `${labels[n] || n} ${fmt(lo, 0)}–${fmt(hi, 0)}${units[n] ? " " + units[n] : ""}`;
      g.fillStyle = color; g.fillRect(lx, 6, 10, 3);
      g.fillStyle = "#dce5ef"; g.fillText(text, lx + 14, 11);
      lx += g.measureText(text).width + 30;
    });
    bands.forEach((n, i) => {
      const y = bottom + 3 + i * 8;
      g.fillStyle = i % 2 ? "#4f9fd8" : "#65cba0";
      curves[n].forEach((v, f) => { if (Number(v) > 0) g.fillRect(xOf(f, W) - 0.5, y, Math.max(1, (W - 2 * PAD) / Math.max(1, frames - 1)) + 0.5, 5); });
    });
    if (bands.length) {
      const text = bands.map((n, i) => `${i % 2 ? "蓝" : "绿"}带：${labels[n] || n}`).join("　");
      const tw = g.measureText(text).width;
      g.fillStyle = "#121a24e6"; g.fillRect(W - PAD - tw - 8, top - 16, tw + 8, 14);
      g.fillStyle = "#9aa8bb"; g.fillText(text, W - PAD - tw - 4, top - 5);
    }
    const px = Math.round(xOf(frame, W)) + 0.5;
    g.strokeStyle = "#f18b80"; g.lineWidth = 1.2;
    g.beginPath(); g.moveTo(px, top - 4); g.lineTo(px, H); g.stroke();
  }

  // ---------------------------------------------------------------- thumbnails (hidden <video> + canvas, one at a time)
  const thumbKey = (f) => `${viewMode}|${f}`;
  const thumbFails = new Map();
  function requestThumb(f, urgent = false) {
    const key = thumbKey(f);
    if (thumbs.has(key) || (thumbFails.get(key) || 0) >= 2) return;
    const at = thumbQueue.indexOf(f);
    if (at >= 0) { if (!urgent) return; thumbQueue.splice(at, 1); }
    if (urgent) thumbQueue.unshift(f); else thumbQueue.push(f);
    pumpThumbs();
  }
  const stripObserver = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver((entries) => {
    for (const e of entries.filter((x) => x.isIntersecting).reverse()) {
      stripObserver.unobserve(e.target);
      requestThumb(Number(e.target.dataset.thumbFrame), true);
    }
  }, { root: el.kfStrip, rootMargin: "0px 400px" });
  async function pumpThumbs() {
    if (thumbBusy || !thumbQueue.length) return;
    const v = el.kfThumbVideo;
    if (!v.getAttribute("src") || v.readyState < 1) return;
    thumbBusy = true;
    const token = thumbToken;
    try {
      while (thumbQueue.length && token === thumbToken) {
        const f = thumbQueue.shift();
        const mode = viewMode;
        if (thumbs.has(`${mode}|${f}`)) continue;
        const url = await grab(f, token, mode);
        if (token !== thumbToken) break;
        if (url) {
          thumbs.set(`${mode}|${f}`, url);
          if (mode === viewMode) for (const img of el.kfStrip.querySelectorAll(`img[data-thumb-frame="${f}"]`)) img.src = url;
        } else {
          const n = (thumbFails.get(`${mode}|${f}`) || 0) + 1;
          thumbFails.set(`${mode}|${f}`, n);
          if (n < 2) thumbQueue.push(f);
          else for (const img of el.kfStrip.querySelectorAll(`img[data-thumb-frame="${f}"]`)) img.alt = "无法抓取画面";
        }
      }
    } finally {
      thumbBusy = false;
      if (token !== thumbToken && thumbQueue.length) pumpThumbs();
    }
  }
  function grab(f, token, mode) {
    return new Promise((resolve) => {
      const v = el.kfThumbVideo;
      let done = false;
      const finish = (ok) => {
        if (done) return;
        done = true;
        v.removeEventListener("seeked", onSeek);
        clearTimeout(timer);
        if (!ok || token !== thumbToken || !v.videoWidth) { resolve(null); return; }
        try {
          const vw = v.videoWidth, vh = v.videoHeight;
          const [sx, sw] = mode === "front" ? [0, vw / 2] : mode === "side" ? [vw / 2, vw / 2] : [0, vw];
          const th = 112, tw = Math.round(th * sw / vh);
          const canvas = el.kfThumbCanvas;
          canvas.width = tw; canvas.height = th;
          canvas.getContext("2d").drawImage(v, sx, 0, sw, vh, 0, 0, tw, th);
          resolve(canvas.toDataURL("image/jpeg", 0.72));
        } catch { resolve(null); }
      };
      const onSeek = () => finish(true);
      const timer = setTimeout(() => finish(false), 4000);
      v.addEventListener("seeked", onSeek);
      v.currentTime = Math.min(K.frameToTime(f, fps), Math.max(0, (v.duration || 1e9) - 0.001));
    });
  }

  // ---------------------------------------------------------------- save / export
  async function save(base = baseRevision) {
    if (!st || saving) return;
    const problems = K.validateLocal(st, frames);
    if (problems.length) { notice(problems.join("\n"), "error"); return; }
    if (!String(st.author || "").trim()) { notice("请先填写作者，用来区分不同美术的标注。", "error"); el.kfAuthor.focus(); return; }
    const d = K.diffSummary(st, view?.auto);
    if (st.reviewed && d.unseen && !window.confirm(`还有 ${d.unseen} 个候选既没保留也没删除，仍然标记为“已审核完毕”吗？`)) return;
    const p = project, name = selected, version = viewVersion;
    saving = true; renderStatus();
    el.kfSaveState.textContent = "正在保存…";
    try {
      const res = await api(`${resultUrl(p, name)}/artist`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(K.payload(st, base)) });
      if (!activeView(version, p, name)) return;
      baseRevision = res.revision;
      view.artist = res.artist;
      view.revision = res.revision;
      const keepSel = selectedId;
      st = K.initialState({ auto: view.auto, artist: res.artist });
      savedSig = K.signature(st);
      selectedId = keyById(keepSel) ? keepSel : null;
      clearDraft(p, name);
      conflict = null;
      setNotice(el.kfConflict, "");
      notice(`已保存 r${res.revision}（${res.saved_at || ""}）。`, "success");
      const o = outputs.find((x) => x.name === name);
      if (o) { o.has_keyframes = { ...(o.has_keyframes || {}), artist: true }; renderResultOptions(); }
      curSig = ""; renderAll();
    } catch (e) {
      if (!activeView(version, p, name)) return;
      const c = K.conflictInfo(e);
      if (c) showConflict(c);
      else notice(`保存失败：${errorText(e)}`, "error");
    } finally {
      if (activeView(version, p, name)) { saving = false; renderStatus(); }
    }
  }
  function showConflict(c) {
    conflict = c;
    el.kfConflict.innerHTML = `<span>${esc(c.message)}${c.artist ? `（${esc(c.artist.author || "未署名")} · ${esc(c.artist.saved_at || "")}）` : ""}。你的修改还在页面上，也已存为本机草稿。</span> <button type="button" data-kf-conflict="reload">载入最新版本（放弃我的修改）</button> <button type="button" data-kf-conflict="overwrite">基于最新版本覆盖保存</button>`;
    el.kfConflict.dataset.kind = "warning";
    el.kfConflict.hidden = false;
    el.kfConflict.onclick = async (event) => {
      const b = event.target.closest("[data-kf-conflict]");
      if (!b || !conflict) return;
      const rev = conflict.revision;
      if (b.dataset.kfConflict === "reload") {
        if (!window.confirm("放弃页面上的修改，载入服务器的最新版本？")) return;
        clearDraft();
        await loadResult(selected);
      } else if (rev != null) {
        setNotice(el.kfConflict, ""); el.kfConflict.onclick = null;
        await save(rev);
      }
    };
  }
  function exportJson() {
    if (!st) return;
    const doc = K.exportDocument(st, view, baseRevision);
    const blob = new Blob([JSON.stringify(doc, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `keyframes_artist_${selected}_r${baseRevision}${dirty() ? "_draft" : ""}.json`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ---------------------------------------------------------------- events
  el.kfResult.addEventListener("change", () => {
    const name = el.kfResult.value;
    if (!name || name === selected) return;
    if (!canLeave()) { el.kfResult.value = selected || ""; return; }
    loadResult(name);
  });
  el.kfGenerate.addEventListener("click", () => generate());
  el.kfReload.addEventListener("click", () => {
    if (!canLeave()) return;
    const keep = selected;
    if (project && keep) desired.set(project, keep);
    discard();
    refresh();
  });
  el.kfViews.addEventListener("click", (event) => {
    const b = event.target.closest("[data-kf-view]");
    if (!b) return;
    viewMode = b.dataset.kfView;
    for (const x of el.kfViews.querySelectorAll("[data-kf-view]")) x.setAttribute("aria-pressed", String(x === b));
    sizeStage();
    thumbQueue = [];
    if (st) renderStrip();
  });
  el.kfPlay.addEventListener("click", () => (playing ? pause() : play()));
  el.kfPrev.addEventListener("click", () => { pause(); seek(frame - 1); });
  el.kfNext.addEventListener("click", () => { pause(); seek(frame + 1); });
  el.kfPrevKey.addEventListener("click", () => stepKey(-1));
  el.kfNextKey.addEventListener("click", () => stepKey(1));
  el.kfSpeed.addEventListener("change", () => { rate = Number(el.kfSpeed.value) || 1; el.kfVideo.playbackRate = rate; });
  el.kfFrameInput.addEventListener("change", () => { pause(); seek(Number(el.kfFrameInput.value)); });
  el.kfAuthor.addEventListener("input", () => {
    if (!st) return;
    st = { ...st, author: el.kfAuthor.value };
    try { localStorage.setItem(AUTHOR_KEY, el.kfAuthor.value.trim()); } catch { /* ignore */ }
    persistDraft(); renderStatus();
  });
  el.kfNotes.addEventListener("input", () => { if (!st) return; st = { ...st, notes: el.kfNotes.value }; persistDraft(); renderStatus(); });
  el.kfReviewed.addEventListener("change", () => { if (!st) return; st = { ...st, reviewed: el.kfReviewed.checked }; persistDraft(); renderStatus(); });
  el.kfSave.addEventListener("click", () => save());
  el.kfExport.addEventListener("click", exportJson);
  el.kfStripFilter.addEventListener("change", () => { stripFilter = el.kfStripFilter.value; renderStrip(); });
  el.kfCurvePicks.addEventListener("change", (event) => {
    const n = event.target.dataset?.kfCurve;
    if (!n) return;
    if (event.target.checked) curvePicks.add(n); else curvePicks.delete(n);
    drawCurves();
  });
  el.kfCurvesBox.addEventListener("toggle", drawCurves);
  el.kfStrip.addEventListener("click", (event) => {
    const restore = event.target.closest("[data-kf-restore]");
    if (restore) { apply(K.restoreAuto(st, view?.auto, restore.dataset.kfRestore)); return; }
    const adopt = event.target.closest("[data-kf-adopt]");
    if (adopt) {
      const rank = Number(adopt.dataset.kfAdopt);
      const one = { ...ladder, breakdowns: ladder.breakdowns.filter((b) => b.rank === rank) };
      const r = K.adoptLadder(st, one, 1);
      apply({ ...r, ok: r.added > 0 });
      return;
    }
    const hit = event.target.closest("[data-kf-seek]");
    if (hit) {
      pause();
      if (hit.dataset.kfId) selectedId = hit.dataset.kfId;
      curSig = "";
      seek(Number(hit.dataset.kfSeek));
      for (const c of el.kfStrip.querySelectorAll(".kf-card.sel")) c.classList.remove("sel");
      hit.closest(".kf-card")?.classList.add("sel");
    }
  });
  // timeline: click to seek, drag an annotation to move it
  el.kfTimeline.addEventListener("pointerdown", (event) => {
    if (!st) return;
    const r = el.kfTimeline.getBoundingClientRect();
    const x = event.clientX - r.left, y = event.clientY - r.top, W = r.width;
    pause();
    let best = null;
    if (y >= 8 && y <= 56) for (const k of st.keyframes) { const d = Math.abs(xOf(k.frame, W) - x); if (d <= 6 && (!best || d < best.d)) best = { k, d }; }
    if (best) {
      selectedId = best.k.id; curSig = "";
      drag = { id: best.k.id, from: best.k.frame, frame: best.k.frame, moved: false };
      el.kfTimeline.setPointerCapture?.(event.pointerId);
      seek(best.k.frame);
    } else seek(frameOf(x, W));
  });
  el.kfTimeline.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const r = el.kfTimeline.getBoundingClientRect();
    const f = frameOf(event.clientX - r.left, r.width);
    if (f !== drag.frame) { drag.frame = f; drag.moved = true; drawTimeline(); }
  });
  const endDrag = (event) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    el.kfTimeline.releasePointerCapture?.(event.pointerId);
    if (d.moved && d.frame !== d.from) { if (apply(K.moveKey(st, d.id, d.frame, frames))) seek(d.frame); }
    else drawTimeline();
  };
  el.kfTimeline.addEventListener("pointerup", endDrag);
  el.kfTimeline.addEventListener("pointercancel", endDrag);
  document.addEventListener("keydown", (event) => {
    if (!st || !isVisible() || document.querySelector("dialog[open]")) return;
    const t = event.target;
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(t?.tagName || "") || t?.isContentEditable) return;
    if (t?.tagName === "BUTTON" && (event.key === " " || event.key === "Enter")) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    let handled = true;
    if (event.key === " ") playing ? pause() : play();
    else if (event.key === "ArrowLeft") event.shiftKey ? stepKey(-1) : (pause(), seek(frame - 1));
    else if (event.key === "ArrowRight") event.shiftKey ? stepKey(1) : (pause(), seek(frame + 1));
    else if (event.key === "k" || event.key === "K") { pause(); addHere(); }
    else if (event.key === "Delete" || event.key === "Backspace") removeHere();
    else if (event.key === "Home") { pause(); seek(0); }
    else if (event.key === "End") { pause(); seek(frames - 1); }
    else handled = false;
    if (handled) event.preventDefault();
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty()) { persistDraft(); event.preventDefault(); event.returnValue = ""; }
  });
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => resize());
    ro.observe(el.kfStage);
    ro.observe(el.kfTimeline);
  }

  function resize() {
    if (!st || !isVisible()) return;
    sizeStage();
    drawTimeline();
    drawCurves();
  }
  function canLeave() {
    if (!dirty()) return true;
    persistDraft();
    return window.confirm("关键帧标注有未保存的修改（已暂存为本机草稿，回到这个结果时可以恢复）。确定离开？");
  }
  // Remember which result to show; the next refresh() (tab navigation) loads it.
  function select(name) {
    const p = current() || null;
    if (p && typeof name === "string" && name) desired.set(p, name);
  }
  // Drop the in-memory annotation (after the user agreed to leave; a local draft is kept).
  function discard() {
    persistDraft();
    listVersion++;
    listAbort?.abort();
    refreshPending = null;
    project = null; outputs = []; selected = null;
    clearView("");
  }
  return { refresh, select, pause, resize, canLeave, dirty, discard, onTasks, _debug: () => ({ project, selected, frame, frames, fps, st, baseRevision, ladderK, view }) };
}
