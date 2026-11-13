// 动作制作台: reference + video upload + generate (left), preview / keyframes timeline (centre),
// works / keyframes / export (right).
import { api, upload, h, clear, toast, errorText, modal, confirmBox, statusBadge, fmtTime, fmtSize, dropZone, lightbox, ICON } from "./util.js?v=9";
import { createViewer } from "./viewer-safe.js?v=9";
import * as K from "/kf-core.js?v=9";

const ACTIVE = ["waiting", "queued", "running"];
const ORIGIN_LABEL = { auto: "自动", ladder: "过渡", artist: "手动" };
const USER_TYPES = ["custom", "extreme", "contact", "impact", "anticipation", "hold_start", "hold_end", "rebound", "breakdown", "start", "end", "technical"];
// Plain-language names for ordinary users (the data keeps the standard type values).
const FRIENDLY_TYPE = { custom: "手动标记", start: "开始", end: "结束", contact: "落脚", extreme: "动作到位", hold_start: "停住",
  hold_end: "再次动起来", impact: "发力", anticipation: "蓄力", rebound: "回弹", technical: "转身", breakdown: "过渡" };
const typeLabel = (t) => FRIENDLY_TYPE[t] || K.TYPE_LABEL[t] || t;
const keyColor = (k) => (k.origin === "artist" ? "#f9cf00" : k.origin === "ladder" ? "#9aa3ad" : "#7a6cff");
// Number keys 1–6 set the selected keyframe's type (most common types first).
const QUICK_TYPES = ["extreme", "contact", "impact", "hold_start", "anticipation", "custom"];
const SHORTCUTS = [
  ["空格", "播放 / 暂停"],
  ["← →", "上一帧 / 下一帧"],
  ["Ctrl + ← →", "后退 / 前进 10 帧"],
  ["Shift + ← →", "跳到上一个 / 下一个关键帧"],
  ["Home / End", "跳到开头 / 结尾"],
  ["K 或 Enter", "在当前帧添加关键帧"],
  ["双击时间轴", "在该位置添加关键帧"],
  ["Delete", "删除当前帧（或选中）的关键帧"],
  ["右键关键帧", "删除这个关键帧"],
  [", .", "选中的关键帧前移 / 后移 1 帧"],
  ["拖动菱形", "移动关键帧"],
  ["1 – 6", "设置类型：" + QUICK_TYPES.map((t, i) => `${i + 1} ${FRIENDLY_TYPE[t]}`).join("  ")],
  ["Ctrl + Z", "撤销"],
  ["Ctrl + Shift + Z / Ctrl + Y", "重做"],
  ["?", "显示这个快捷键列表"],
];

export function openBench(container, { characterId, workId, config }) {
  const S = {
    char: null, works: [], work: null, video: null, tab: "works", mode: "compare",
    view: null, st: null, rev: 0, saved: null, sel: null, saveTimer: 0, saving: false,
    viewer: null, idleViewer: null, rendered: "", alive: true, poll: 0, exportFmt: "fbx", exportRange: "full", interp: "smooth",
    undo: [], redo: [],
  };
  const P = { t: 0, playing: false, fps: 24, frames: 0, raf: 0, last: 0, clipStart: 0, modelDur: 0 };

  // ------------------------------------------------------------------ layout
  const railBtn = (key, icon, label, on) => h("button", { "data-k": key, "aria-current": "false", title: label, onclick: on }, h("span", { html: ICON[icon] }), label);
  const rail = h("nav.rail",
    railBtn("lib", "back", "角色库", () => { location.hash = "#/"; }),
    railBtn("new", "plus", "新动作", () => { selectWork(null); setTab("works"); }),
    railBtn("works", "film", "作品", () => setTab("works")),
    railBtn("keys", "key", "关键帧", () => setTab("keys")),
    railBtn("export", "export", "导出", () => setTab("export")));
  const leftBody = h("div.panel-body");
  const genBtn = h("button.btn.primary.lg.block", { onclick: generate, disabled: true }, h("span", { html: ICON.wand }), "生成动画");
  const genNote = h("div.fine", "上传视频后即可生成，大约需要 2–5 分钟");
  const left = h("aside.panel.left", h("div.panel-title", "新建动作"), leftBody, h("div.panel-cta", genBtn, genNote));
  const stageTop = h("div.stage-top");
  const stage = h("div.stage");
  const timeline = h("div.timeline", { hidden: true });
  const central = h("main.central", stageTop, stage, timeline);
  const tabs = h("div.tabs", { role: "tablist" });
  const rightBody = h("div.panel-body");
  const right = h("aside.panel.right", h("div.panel-title", h("span", "制作台")), tabs, rightBody);
  container.append(h("div.bench", rail, left, central, right));

  // ------------------------------------------------------------------ load
  (async () => {
    try { S.char = await api(`/api/characters/${characterId}`); }
    catch (e) { clear(stage, h("div.stage-empty", h("div", h("div.big", "找不到这个角色"), h("p", errorText(e)), h("a.btn.primary", { href: "#/", style: { marginTop: "16px" } }, "返回角色库")))); return; }
    if (S.char.status !== "ready") {
      clear(stage, h("div.stage-empty", h("div", h("div.big", "这个角色还不能用来做动作"), h("p", `当前状态：${S.char.status_text}`), h("a.btn.primary", { href: "#/", style: { marginTop: "16px" } }, "返回角色库"))));
      return;
    }
    drawLeft();
    await refreshWorks();
    const w = workId && S.works.find((x) => x.id === workId);
    if (workId && !w) toast("找不到这个作品", "err");
    selectWork(w ? w.id : null);
    setTab("works");
  })();

  // ------------------------------------------------------------------ left panel: reference + video
  let promptAction = "";
  function promptText() { return (config.prompt || "").replace("{动作描述}", promptAction.trim() || "（在这里写动作，例如：向前挥拳攻击）"); }
  function drawLeft() {
    const c = S.char;
    const promptBox = h("div.prompt-box", promptText());
    const actionInput = h("input.input", { placeholder: "想做什么动作？例如：挥拳攻击", value: promptAction, maxlength: 60,
      oninput: () => { promptAction = actionInput.value; promptBox.textContent = promptText(); } });
    const charPick = h("a.char-pick", { href: "#/", title: "换一个角色" },
      c.thumb ? h("img", { src: c.thumb, alt: "" }) : h("span.ph"),
      h("div.grow", h("b", c.name), h("small", "点击更换角色")));
    const step1 = h("div.step-card",
      h("div.step-head", h("span.step-n", "1"), h("h4", "下载参考图")),
      h("p", "把参考图交给 AI 视频工具（如即梦、可灵），配上下面的提示词，生成一段左正面、右侧面的视频。"),
      c.has_reference && h("button.ref-img", { title: "点击放大", onclick: () => lightbox(`/api/characters/${c.id}/reference`) }, h("img", { src: `/api/characters/${c.id}/reference`, alt: "参考图" })),
      h("div.row",
        h("a.btn.sm.accent.grow", { href: `/api/characters/${c.id}/reference?download=1` }, h("span", { html: ICON.download }), "下载参考图"),
        h("button.btn.sm", { onclick: copyPrompt }, h("span", { html: ICON.copy }), "复制提示词")),
      actionInput, promptBox);
    const step2 = h("div.step-card" + (S.video ? ".done" : ".active"),
      h("div.step-head", h("span.step-n", S.video ? h("span", { html: ICON.check, style: { width: "12px", display: "inline-flex" } }) : "2"), h("h4", "上传生成好的视频")),
      S.video ? videoChip() : dropZone({ title: "点击或拖入视频", hint: `MP4 / MOV · 建议 3–10 秒 · 不超过 ${config.max_video_mb} MB`, accept: "video/*,.mp4,.mov,.webm,.mkv,.m4v", onFiles: (f) => uploadVideo(f[0]) }),
      h("p", "要求：左右分屏、镜头不动、背景干净，角色全身始终在画面内。"));
    const title = h("input.input", { placeholder: "给这个动作起个名字（可不填）", maxlength: 40, value: S.video?.title || "", oninput: (e) => { if (S.video) S.video.title = e.target.value; } });
    const step3 = h("div.step-card" + (S.video ? ".active" : ""),
      h("div.step-head", h("span.step-n", "3"), h("h4", "生成动画")),
      S.video ? title : h("p", "上传视频后，点下方“生成动画”。"));
    clear(leftBody, charPick, step1, step2, step3);
    genBtn.disabled = !S.video;
  }
  async function copyPrompt() {
    const text = promptText();
    try { await navigator.clipboard.writeText(text); toast("提示词已复制", "ok"); }
    catch {
      const ta = h("textarea", { style: { position: "fixed", opacity: 0 } }, text);
      document.body.append(ta); ta.select();
      try { document.execCommand("copy"); toast("提示词已复制", "ok"); } catch { toast("复制失败，请手动选中复制", "err"); }
      ta.remove();
    }
  }
  function videoChip() {
    const v = S.video;
    return h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
      h("div.video-chip", h("video", { src: v.url, muted: true, playsInline: true, preload: "metadata", onmouseenter: (e) => e.target.play().catch(() => {}), onmouseleave: (e) => e.target.pause() }),
        h("div.grow", h("div.name", v.name), h("div.sub", `${fmtSize(v.size)}${v.info?.frames && v.info?.fps ? ` · ${(v.info.frames / v.info.fps).toFixed(1)} 秒` : ""}`)),
        h("button.icon-btn", { title: "换一个视频", "aria-label": "换一个视频", html: ICON.x, onclick: () => { S.video = null; drawLeft(); } })),
      (v.warnings || []).map((w) => h("div.note.warn", w)));
  }
  async function uploadVideo(file) {
    if (!file) return;
    const bar = h("div.progress", h("i", { style: { width: "0%" } }));
    const label = h("p", `正在上传 ${file.name}…`);
    const step2 = leftBody.children[2];
    step2?.replaceChildren(h("div.step-head", h("span.step-n", "2"), h("h4", "上传生成好的视频")), label, bar);
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const v = await upload(`/api/characters/${S.char.id}/videos`, form, (p) => { bar.firstChild.style.width = Math.round(p * 100) + "%"; if (p >= 1) label.textContent = "上传完成，正在检查视频…"; });
      S.video = { ...v, title: file.name.replace(/\.[^.]+$/, "").slice(0, 40) };
      toast("视频已上传", "ok");
    } catch (e) { toast(errorText(e), "err"); }
    if (S.alive) drawLeft();
  }
  async function generate() {
    if (!S.video) return;
    genBtn.disabled = true;
    try {
      const w = await api("/api/works", { method: "POST", body: { character_id: S.char.id, video_id: S.video.id, title: (S.video.title || "").trim() } });
      S.video = null;
      drawLeft();
      await refreshWorks();
      selectWork(w.id);
      setTab("works");
      toast("已提交，正在生成动画", "ok");
    } catch (e) { toast(errorText(e), "err"); genBtn.disabled = false; }
  }

  // ------------------------------------------------------------------ right panel
  function setTab(t) {
    S.tab = t;
    for (const b of rail.querySelectorAll("button[data-k]")) b.setAttribute("aria-current", String(b.dataset.k === t || (b.dataset.k === "new" && !S.work && t === "works")));
    clear(tabs, [["works", "作品"], ["keys", "关键帧"], ["export", "导出"]].map(([k, l]) => h("button", { role: "tab", "aria-selected": String(k === t), onclick: () => setTab(k) }, l)));
    drawRight();
  }
  function drawRight() {
    if (S.tab === "works") return drawWorks();
    if (S.tab === "keys") return drawKeysPanel();
    return drawExport();
  }
  function needReady(text) {
    const w = S.work;
    if (w && w.status === "ready") return null;
    return h("div.empty", { style: { padding: "36px 16px" } }, h("h3", w ? "动画还没生成好" : "先选择一个作品"), h("p", text));
  }

  function drawWorks() {
    const items = S.works.map((w) => h(`button.work-item${S.work?.id === w.id ? ".on" : ""}`, { onclick: () => selectWork(w.id) },
      h("span.wthumb", w.thumb ? h("img", { src: w.thumb, alt: "" }) : h("span", { html: ICON.film })),
      h("span.info", h("b", w.title), h("span.row", statusBadge(w.status, w.progress ? `${w.status_text} ${w.progress.percent}%` : w.status_text), h("small", fmtTime(w.created))))));
    clear(rightBody,
      h("button.btn.ghost.block", { onclick: () => selectWork(null) }, h("span", { html: ICON.plus }), "新建动作"),
      items.length ? items : h("div.empty", { style: { padding: "36px 16px" } }, h("h3", "还没有作品"), h("p", "在左边上传视频，生成第一个动作。")));
  }

  // ---------------------------------------------------------------- keyframes panel
  function drawKeysPanel() {
    const nr = needReady("动画生成完成后，可以在这里自动或手动标记关键帧。");
    if (nr) return clear(rightBody, nr);
    const w = S.work;
    const running = ["waiting", "running"].includes(w.kf_status);
    const autoBtn = h("button.btn.accent.block", { disabled: running, onclick: autoMark }, h("span", { html: ICON.wand }), running ? "正在自动标记…" : "自动标记关键帧");
    if (!S.st) return clear(rightBody, autoBtn, h("div.empty", { style: { padding: "24px 12px" } }, running ? [h("div.spinner"), h("p", "正在分析动作，通常几十秒…")] : h("p", "加载中…")));
    const st = S.st;
    const frames = P.frames;
    const cur = curFrame();
    const here = K.keyAt(st, cur);
    const addBtn = h("button.btn.primary.block", { disabled: !!here, onclick: () => addKey(cur) }, h("span", { html: ICON.plus }), here ? `第 ${cur} 帧已有关键帧` : `在第 ${cur} 帧添加关键帧`, !here && h("span.kbd", { style: { marginLeft: "6px", background: "#0002", color: "#000" } }, "K"));
    const list = h("div.kf-list", st.keyframes.map((k) => h(`div.kf-row${S.sel === k.id ? ".sel" : ""}`, { onclick: () => { S.sel = k.id; seek(k.frame); drawKeysPanel(); drawTimelineKeys(); } },
      h("span.dot", { style: { background: keyColor(k) } }),
      h("span.fr", `第 ${k.frame} 帧`),
      h("span.ty", typeLabel(k.type), h("span", { style: { color: "var(--dim)", marginLeft: "6px" } }, ORIGIN_LABEL[k.origin] || "")),
      h("button.icon-btn", { title: "删除", "aria-label": "删除关键帧", html: ICON.trash, onclick: (e) => { e.stopPropagation(); removeKey(k.id); } }))));
    const selKey = st.keyframes.find((k) => k.id === S.sel);
    const detail = selKey && keyDetail(selKey);
    const ladder = K.ladderInfo(S.view?.auto);
    let ladderBox = null;
    if (ladder.ok) {
      const adopted = st.keyframes.filter((k) => k.origin === "ladder").length;
      const out = h("b", String(ladder.suggested));
      const range = h("input", { type: "range", min: 0, max: ladder.breakdowns.length, value: Math.max(ladder.suggested, adopted), oninput: () => { out.textContent = range.value; } });
      ladderBox = h("div.kf-detail",
        h("b", { style: { fontSize: "13px" } }, "补充过渡帧"),
        h("p.reason", "在关键姿势之间补几个过渡帧，导出“只保留关键帧”时动作会更贴近原视频。"),
        h("div.slider-row", h("span", "数量"), range, out),
        h("button.btn.sm.block", { onclick: () => { const r = K.adoptLadder(S.st, ladder, Number(range.value)); toast(r.message, r.ok ? "ok" : ""); if (r.ok) commit(r, `加入 ${r.added} 个过渡帧`); } }, "加入过渡帧"));
    }
    clear(rightBody,
      h("div.row", h("div.grow", h("b", `${st.keyframes.length} 个关键帧`), h("div", { style: { fontSize: "11px", color: "var(--dim)" } }, saveStateText())),
        h("button.icon-btn", { title: "撤销（Ctrl+Z）", "aria-label": "撤销", disabled: !S.undo.length, html: ICON.undo, onclick: () => undoRedo(-1) }),
        h("button.icon-btn", { title: "重做（Ctrl+Shift+Z）", "aria-label": "重做", disabled: !S.redo.length, html: ICON.redo, onclick: () => undoRedo(1) }),
        h("button.btn.sm.ghost", { onclick: clearManual, title: "删除所有关键帧，从头手动标记" }, "清空")),
      autoBtn, addBtn,
      h("div.row", { style: { fontSize: "11px", color: "var(--dim)", flexWrap: "wrap", gap: "4px 6px" } },
        h("span.kbd", "K"), "添加", h("span.kbd", "Del"), "删除", h("span.kbd", "1–6"), "改类型", h("span.kbd", "Ctrl+Z"), "撤销", h("span.kbd", "双击"), "时间轴添加",
        h("button", { style: { color: "var(--purple4)", fontSize: "11px", marginLeft: "auto" }, onclick: showShortcuts }, "全部快捷键 ?")),
      detail, list, ladderBox,
      h("div.kf-detail",
        h("b", { style: { fontSize: "13px" } }, "导出关键帧"),
        h("p.reason", "关键帧列表（帧号、时间、类型、备注），可在 Excel 里打开，或交给 3ds Max / Maya 的脚本使用。"),
        h("div.row", h("button.btn.sm.grow", { onclick: () => exportKeyList("csv") }, h("span", { html: ICON.download }), "表格 CSV"),
          h("button.btn.sm.grow", { onclick: () => exportKeyList("json") }, h("span", { html: ICON.download }), "JSON")),
        h("button.btn.sm.ghost", { onclick: () => setTab("export") }, "导出只含关键帧的 FBX →")));
    void frames;
  }
  function keyDetail(k) {
    const auto = K.autoKeys(S.view?.auto).find((a) => a.id === k.auto_id);
    const typeSel = h("select.input", { style: { height: "34px" }, onchange: () => commit(K.setKeyField(S.st, k.id, "type", typeSel.value), `第 ${k.frame} 帧 → ${typeLabel(typeSel.value)}`) },
      USER_TYPES.map((t) => h("option", { value: t, selected: t === k.type }, typeLabel(t))));
    const note = h("input.input", { style: { height: "34px" }, placeholder: "备注（可不填）", value: k.note || "", maxlength: 500,
      onchange: () => commit(K.setKeyField(S.st, k.id, "note", note.value), "修改备注"),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === "Escape") e.target.blur(); } });
    const quick = h("div.quick-types", QUICK_TYPES.map((t, i) => h(`button${k.type === t ? ".on" : ""}`, { title: `快捷键 ${i + 1}`, onclick: () => setSelType(t) }, h("span.kbd", i + 1), typeLabel(t))));
    return h("div.kf-detail",
      h("div.row", h("b.grow", `第 ${k.frame} 帧`),
        h("button.icon-btn", { title: "前移一帧（,）", html: ICON.prev, onclick: () => nudge(k, -1) }),
        h("button.icon-btn", { title: "后移一帧（.）", html: ICON.next, onclick: () => nudge(k, 1) }),
        h("button.icon-btn", { title: "删除（Delete）", html: ICON.trash, onclick: () => removeKey(k.id) })),
      auto?.reason && h("p.reason", auto.reason),
      k.auto_frame != null && k.origin === "auto" && k.auto_frame !== k.frame && h("p.reason", `原来自动标在第 ${k.auto_frame} 帧`),
      quick,
      h("div.field", h("label", "全部类型"), typeSel), note);
  }
  function nudge(k, d) { const r = K.moveKey(S.st, k.id, k.frame + d, P.frames); if (commit(r, `移到第 ${k.frame + d} 帧`)) seek(k.frame + d); }
  function addKey(f, type = "custom") {
    const hit = K.keyAt(S.st, f);
    if (hit) { S.sel = hit.id; flash(`第 ${f} 帧已有关键帧`); drawTimelineKeys(); if (S.tab === "keys") drawKeysPanel(); return null; }
    const r = K.addKey(S.st, f, type, P.frames);
    S.sel = r.ok ? r.id : S.sel;
    if (commit(r, `已在第 ${f} 帧添加关键帧`)) { if (S.tab !== "keys") setTab("keys"); return r.id; }
    return null;
  }
  function removeKey(id) {
    const k = S.st.keyframes.find((x) => x.id === id);
    const r = K.removeKey(S.st, id);
    if (commit(r, k ? `已删除第 ${k.frame} 帧的关键帧（Ctrl+Z 撤销）` : "已删除")) { if (S.sel === id) S.sel = null; drawTimelineKeys(); }
  }
  function setSelType(type) {
    const k = selectedKey();
    if (!k) { flash("先选中一个关键帧（点时间轴上的菱形，或停在关键帧上）"); return; }
    if (k.type === type) return;
    commit(K.setKeyField(S.st, k.id, "type", type), `第 ${k.frame} 帧 → ${typeLabel(type)}`);
  }
  // Key under the playhead wins; otherwise the clicked/selected one.
  function selectedKey() { return (S.st && (K.keyAt(S.st, curFrame()) || S.st.keyframes.find((x) => x.id === S.sel))) || null; }
  function undoRedo(dir) {
    const from = dir < 0 ? S.undo : S.redo, to = dir < 0 ? S.redo : S.undo;
    if (!from.length) { flash(dir < 0 ? "没有可以撤销的操作" : "没有可以重做的操作"); return; }
    const item = from.pop();
    to.push({ st: S.st, label: item.label });
    S.st = item.st;
    if (S.sel && !S.st.keyframes.some((k) => k.id === S.sel)) S.sel = null;
    scheduleSave();
    drawTimelineKeys();
    if (S.tab === "keys") drawKeysPanel();
    flash(`${dir < 0 ? "已撤销" : "已重做"}：${item.label}`);
  }
  let flashTimer = 0;
  function flash(text) {
    let el = central.querySelector(".kf-flash");
    if (!el) { el = h("div.kf-flash"); central.append(el); }
    el.textContent = text;
    el.classList.add("on");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => el.classList.remove("on"), 1400);
  }
  function showShortcuts() {
    modal({ title: "关键帧快捷键", body: h("div.shortcut-list", SHORTCUTS.map(([k, d]) => h("div.shortcut", h("span.keys", k.split(" / ").map((p, i) => [i ? " / " : "", h("span.kbd", p)])), h("span", d)))) });
  }
  async function clearManual() {
    if (!S.st?.keyframes.length) return;
    if (!await confirmBox("清空关键帧", "删除全部关键帧，之后可以手动重新标记，或再次点“自动标记”。", { ok: "清空", danger: true })) return;
    let st = S.st;
    for (const k of [...st.keyframes]) st = K.removeKey(st, k.id).state;
    commit({ ok: true, state: st }, "清空全部关键帧（Ctrl+Z 撤销）");
  }
  // Keyframe list as a file, built from the current (saved or unsaved) state.
  function exportKeyList(kind) {
    const fps = P.fps || 24;
    const rows = S.st.keyframes.map((k) => ({ frame: k.frame, time: +(k.frame / fps).toFixed(4), type: k.type, type_label: typeLabel(k.type),
      origin: { auto: "自动", ladder: "过渡", artist: "手动" }[k.origin] || k.origin, note: k.note || "" }));
    const base = (S.work.title || "keyframes").replace(/[\\/:*?"<>|\s]+/g, "_");
    let blob;
    if (kind === "csv") {
      const esc = (v) => /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
      const lines = ["帧号,时间(秒),类型,类型(代码),来源,备注", ...rows.map((r) => [r.frame, r.time, r.type_label, r.type, r.origin, r.note].map(esc).join(","))];
      blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    } else {
      const doc = { ...K.exportDocument(S.st, S.view, S.rev), title: S.work.title, fps, frames: P.frames,
        // Frame numbers are video frames from 0; at the video fps they are whole frames in Max/Maya (key time = frame / fps).
        frame_list: rows.map((r) => r.frame), keys: rows };
      blob = new Blob([JSON.stringify(doc, null, 1)], { type: "application/json" });
    }
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: `${base}_关键帧.${kind}` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast(`已导出 ${rows.length} 个关键帧`, "ok");
  }
  async function autoMark() {
    if (S.st?.keyframes.some((k) => k.origin === "artist" || k.origin === "ladder" || (k.origin === "auto" && k.frame !== k.auto_frame))
      && !await confirmBox("重新自动标记", "自动标记会替换你当前的关键帧（包括手动添加和调整过的）。确定继续吗？", { ok: "重新标记" })) return;
    try {
      await api(`/api/works/${S.work.id}/keyframes/auto`, { method: "POST" });
      S.work.kf_status = "waiting";
      S.replaceWithAuto = true;
      drawKeysPanel();
      schedulePoll(1500);
    } catch (e) { toast(errorText(e), "err"); }
  }

  function saveStateText() {
    if (S.saving) return "正在保存…";
    if (S.st && S.saved !== K.signature(S.st)) return "有修改，即将自动保存";
    return S.rev ? "已自动保存" : "自动标记结果（修改后自动保存）";
  }
  function commit(res, label = "修改关键帧") {
    if (!res?.ok) { if (res?.message) toast(res.message, "err"); return false; }
    if (res.state === S.st) return true;
    S.undo.push({ st: S.st, label });
    if (S.undo.length > 100) S.undo.shift();
    S.redo = [];
    S.st = res.state;
    if (label && label !== "修改关键帧") flash(label);
    scheduleSave();
    drawTimelineKeys();
    if (S.tab === "keys") drawKeysPanel();
    return true;
  }
  function scheduleSave(delay = 700) {
    clearTimeout(S.saveTimer);
    S.saveTimer = setTimeout(saveKeys, delay);
  }
  async function saveKeys(retry = true) {
    if (!S.st || !S.work || S.saving) return;
    const sig = K.signature(S.st);
    if (sig === S.saved) return;
    const wid = S.work.id;
    S.saving = true;
    try {
      const r = await api(`/api/works/${wid}/keyframes`, { method: "PUT", body: K.payload(S.st, S.rev) });
      if (S.work?.id === wid) { S.rev = r.revision; S.saved = sig; }
    } catch (e) {
      const c = K.conflictInfo(e);
      if (c && retry && S.work?.id === wid) { S.rev = c.revision ?? S.rev; S.saving = false; return saveKeys(false); }
      toast(`关键帧保存失败：${errorText(e)}`, "err");
    } finally { S.saving = false; }
    if (S.st && K.signature(S.st) !== S.saved) scheduleSave();
    if (S.tab === "keys" && S.alive) drawKeysPanel();
  }
  // The extractor may put two candidates on one frame (e.g. left and right foot contact); one key per frame
  // is allowed, so keep the first and record the others as removed candidates. Returns the number merged.
  function mergeSameFrame(st) {
    const seen = new Set();
    let n = 0;
    st.keyframes = st.keyframes.filter((k) => {
      if (!seen.has(k.frame)) { seen.add(k.frame); return true; }
      if (k.origin === "auto" && k.auto_id && !st.removed.includes(k.auto_id)) st.removed.push(k.auto_id);
      n++;
      return false;
    });
    return n;
  }
  async function loadKeys() {
    const w = S.work;
    try {
      const v = await api(`/api/works/${w.id}/keyframes`);
      if (S.work?.id !== w.id) return;
      S.view = v;
      S.rev = v.revision;
      const replace = S.replaceWithAuto && v.auto;
      S.replaceWithAuto = false;
      S.st = K.initialState(replace ? { ...v, artist: null } : v);
      S.undo = []; S.redo = [];
      S.saved = v.artist ? K.signature(K.initialState({ ...v, auto_sha256: null })) : K.signature(S.st);
      const merged = mergeSameFrame(S.st);
      if (replace || S.st.rebased || merged) { S.saved = null; scheduleSave(100); }
      if (v.frames) P.frames = v.frames;
      if (v.fps) P.fps = v.fps;
    } catch (e) { toast(errorText(e), "err"); }
    drawTimelineKeys();
    drawCurve();
    if (S.tab === "keys") drawKeysPanel();
  }

  // ---------------------------------------------------------------- export panel
  function drawExport() {
    const nr = needReady("动画生成完成后，可以在这里导出 FBX 等格式。");
    if (nr) return clear(rightBody, nr);
    const w = S.work;
    const fmts = [["fbx", "FBX", "推荐 · 3ds Max / Maya / Unity / UE，带模型和贴图"], ["glb", "GLB", "网页、Blender、通用 3D 查看器"], ["bvh", "BVH", "只有骨骼，每一帧都是关键帧（格式限制）"]];
    const nKeys = S.st?.keyframes.length || 0;
    if (S.exportFmt === "bvh" && S.exportRange === "keys") S.exportRange = "full";
    const radio = (name, value, cur, title, sub, on) => h("label.radio-line", h("input", { type: "radio", name, value, checked: value === cur, onchange: on }), h("span", h("b", title), h("small", sub)));
    const doBtn = h("button.btn.primary.lg.block", { onclick: doExport }, h("span", { html: ICON.download }), "导出并下载");
    const recent = h("div", { style: { display: "flex", flexDirection: "column", gap: "6px" } });
    clear(rightBody,
      h("div.mini-label", "文件格式"),
      fmts.map(([k, t, s]) => h(`button.export-opt${S.exportFmt === k ? ".on" : ""}`, { disabled: !w.files?.[k], onclick: () => { S.exportFmt = k; drawExport(); } },
        h("span.fmt", t), h("span", h("b", t + (w.files?.[k] ? "" : "（暂无）")), h("small", s)))),
      h("div.mini-label", "动画内容"),
      radio("range", "full", S.exportRange, "完整动画", "每一帧都有动作，最贴近原视频，可直接使用", () => { S.exportRange = "full"; drawExport(); }),
      S.exportFmt !== "bvh"
        ? radio("range", "keys", S.exportRange, `只保留关键帧（${nKeys} 个）`, "只在关键帧上打键，导入 3ds Max / Maya 后就是这些关键帧，方便动画师继续修改", () => { S.exportRange = "keys"; drawExport(); })
        : h("div.note", "BVH 格式本身要求每一帧都写完整姿势，导入 3ds Max 后每一帧都是关键帧。需要“只有关键帧”的文件请选 FBX。"),
      S.exportRange === "keys" && h("div.seg", { style: { alignSelf: "flex-start" } },
        [["smooth", "平滑过渡"], ["linear", "直线过渡"]].map(([k, l]) => h("button", { "aria-pressed": String(S.interp === k), onclick: () => { S.interp = k; drawExport(); } }, l))),
      S.exportRange === "keys" && nKeys < 2 && h("div.note.warn", "至少需要 2 个关键帧。请先到“关键帧”里自动标记或手动添加。"),
      w.forced && h("div.note.warn", "这是预览版动画（原视频没有通过检查），动作可能不准确。"),
      doBtn, recent);
    if (S.exportRange === "keys") loadExports(recent);
  }
  async function doExport(e) {
    const w = S.work;
    if (S.exportRange === "full") {
      triggerDownload(`/api/works/${w.id}/download/${S.exportFmt}`);
      toast("开始下载", "ok");
      return;
    }
    e.currentTarget.disabled = true;
    try {
      clearTimeout(S.saveTimer);
      await saveKeys();
      await api(`/api/works/${w.id}/exports`, { method: "POST", body: { format: S.exportFmt, interp: S.interp } });
      toast("正在导出，完成后会自动下载", "ok");
      S.autoDownload = true;
      drawExport();
      schedulePoll(1500);
    } catch (ex) { toast(errorText(ex), "err"); drawExport(); }
  }
  async function loadExports(box) {
    let list = [];
    try { list = await api(`/api/works/${S.work.id}/exports`); } catch { return; }
    const fmtName = { fbx: "FBX", glb: "GLB", bvh: "BVH" };
    const running = list.some((x) => ["waiting", "running"].includes(x.status));
    if (S.autoDownload && list[0]?.status === "done") { S.autoDownload = false; triggerDownload(list[0].url); }
    if (S.autoDownload && list[0]?.status === "failed") { S.autoDownload = false; toast(list[0].error || "导出失败", "err"); }
    if (list.length) clear(box, h("div.mini-label", "最近的关键帧导出"), list.slice(0, 5).map((x) => h("div.list-line",
      h("span.grow", `${fmtName[x.options.format] || ""} · ${x.options.frames?.length || 0} 个关键帧 · ${fmtTime(x.created)}`),
      x.status === "done" ? h("a.btn.sm", { href: x.url }, "下载") : x.status === "failed" ? h("span.badge.bad", "失败") : h("span.badge.run", "导出中…"))));
    if (running) schedulePoll(2500);
  }
  function triggerDownload(url) {
    const a = h("a", { href: url, download: "" });
    document.body.append(a); a.click(); a.remove();
  }

  // ---------------------------------------------------------------- works
  async function refreshWorks() {
    try { S.works = await api(`/api/works?character=${S.char.id}`); } catch (e) { toast(errorText(e), "err"); }
  }
  async function selectWork(id) {
    clearTimeout(S.saveTimer);
    if (S.st && S.saved !== K.signature(S.st)) await saveKeys();
    S.work = null; S.view = null; S.st = null; S.sel = null; S.rendered = ""; S.undo = []; S.redo = [];
    history.replaceState(null, "", id ? `#/bench/${S.char.id}/${id}` : `#/bench/${S.char.id}`);
    if (id) {
      try { S.work = await api(`/api/works/${id}`); } catch (e) { toast(errorText(e), "err"); }
    }
    drawCentral();
    setTab(S.tab);
    schedulePoll(3000);
  }
  function schedulePoll(ms) {
    clearTimeout(S.poll);
    S.poll = setTimeout(poll, ms);
  }
  async function poll() {
    if (!S.alive || !S.char) return;
    const before = S.work;
    await refreshWorks();
    if (before) {
      try {
        const w = await api(`/api/works/${before.id}`);
        if (S.work?.id === w.id) {
          const kfDone = ["waiting", "running"].includes(S.work.kf_status) && w.kf_status === "done";
          const kfFail = ["waiting", "running"].includes(S.work.kf_status) && w.kf_status === "failed";
          S.work = w;
          if (`${w.id}:${w.status}` !== S.rendered) drawCentral();
          else if (ACTIVE.includes(w.status)) updateGenStatus();
          if (kfDone) { await loadKeys(); toast("关键帧已自动标记", "ok"); }
          if (kfFail) toast("自动标记没有成功，可以手动标记关键帧", "err");
        }
      } catch { /* deleted */ }
    }
    if (S.tab === "works") drawWorks();
    else if (S.tab === "keys" && !document.activeElement?.closest?.(".kf-detail")) drawKeysPanel();
    else if (S.tab === "export" && S.work?.status === "ready") {
      const box = rightBody.lastElementChild;
      if (S.exportRange === "keys" && box) loadExports(box);
    }
    const busy = S.works.some((w) => ACTIVE.includes(w.status)) || ["waiting", "running"].includes(S.work?.kf_status);
    if (busy) schedulePoll(3000);
  }

  // ---------------------------------------------------------------- centre
  function disposeStage() {
    pause();
    S.viewer?.dispose(); S.viewer = null;
    S.idleViewer?.dispose(); S.idleViewer = null;
    S.srcVideo = S.labVideo = null;
    timeline.hidden = true;
  }
  function drawCentral() {
    disposeStage();
    clear(stageTop); clear(stage);
    stage.className = "stage";
    const w = S.work;
    S.rendered = w ? `${w.id}:${w.status}` : "";
    if (!w) {
      stageTop.append(h("div.stage-title", h("span", S.char.name)));
      const pane = h("div.pane");
      stage.append(pane);
      if (S.char.preview) {
        S.idleViewer = createViewer(pane, { fallbackImage: S.char.has_reference ? `/api/characters/${S.char.id}/reference` : null });
        S.idleViewer.load(S.char.preview.url, S.char.preview.kind).catch(() => {});
      }
      stage.append(h("div", { style: { position: "absolute", left: "50%", bottom: "28px", transform: "translateX(-50%)", zIndex: 3 } },
        h("div.note", { style: { background: "#000a", backdropFilter: "blur(6px)", fontSize: "13px", padding: "10px 18px", borderRadius: "100px" } },
          "① 下载参考图 → ② 用 AI 生成视频 → ③ 上传视频 → ④ 生成动画")));
      return;
    }
    const titleEl = h("span", w.title);
    stageTop.append(h("div.stage-title", titleEl,
      h("button.icon-btn", { title: "重命名", "aria-label": "重命名", html: ICON.edit, onclick: renameWork }),
      h("button.icon-btn", { title: "删除作品", "aria-label": "删除作品", html: ICON.trash, onclick: deleteWork }),
      w.forced && h("span.badge.warn", "预览版")));
    if (ACTIVE.includes(w.status)) return drawGenerating();
    if (w.status !== "ready") return drawProblem();
    drawReady();
  }
  function drawGenerating() {
    const box = h("div.gen-status",
      h("div.spinner"),
      h("h3", "正在生成动画"),
      h("div.pct", "0%"),
      h("div.progress", h("i", { style: { width: "0%" } })),
      h("p.gtext", { style: { color: "var(--w60)" } }, ""),
      h("p", { style: { color: "var(--dim)", fontSize: "12px" } }, "可以离开这个页面，生成完成后在“我的作品”里查看。"),
      h("div.row", { style: { justifyContent: "center" } },
        h("button.btn.ghost.sm", { onclick: cancelWork }, "取消生成")));
    stage.append(h("div.stage-empty", box));
    S.genBox = box;
    updateGenStatus();
  }
  function updateGenStatus() {
    const box = S.genBox, p = S.work?.progress;
    if (!box || !p) return;
    box.querySelector(".pct").textContent = `${p.percent}%`;
    box.querySelector(".progress > i").style.width = `${Math.max(2, p.percent)}%`;
    box.querySelector(".gtext").textContent = p.text;
  }
  function drawProblem() {
    const w = S.work;
    const review = w.status === "needs_review";
    stage.append(h("div.stage-empty", h("div.gen-status",
      h("h3", review ? "这段视频不太适合" : w.status === "cancelled" ? "已取消" : "生成没有成功"),
      h("p", { style: { color: "var(--w80)", textAlign: "left" } }, w.message || ""),
      review && h("ul", { style: { textAlign: "left", color: "var(--w60)", fontSize: "12px", margin: 0, paddingLeft: "18px" } },
        h("li", "左边是正面、右边是侧面，不要放反"), h("li", "镜头不能移动或缩放"), h("li", "不要有特效、扬尘、残影，四肢不要被挡住")),
      h("div.row", { style: { justifyContent: "center", flexWrap: "wrap" } },
        h("button.btn.primary", { onclick: () => selectWork(null) }, "换一个视频"),
        review ? h("button.btn", { onclick: () => retry(true) }, "仍然生成预览") : h("button.btn", { onclick: () => retry(false) }, "重试")))));
  }
  async function retry(force) {
    try { S.work = await api(`/api/works/${S.work.id}/retry`, { method: "POST", body: { force } }); drawCentral(); await refreshWorks(); drawRight(); schedulePoll(2000); }
    catch (e) { toast(errorText(e), "err"); }
  }
  async function cancelWork() {
    if (!await confirmBox("取消生成", "确定要取消这次生成吗？", { ok: "取消生成", danger: true })) return;
    try { S.work = await api(`/api/works/${S.work.id}/cancel`, { method: "POST" }); drawCentral(); await refreshWorks(); drawRight(); }
    catch (e) { toast(errorText(e), "err"); }
  }
  function renameWork() {
    const input = h("input.input", { value: S.work.title, maxlength: 40 });
    const m = modal({ title: "重命名", body: input, foot: [h("button.btn.ghost", { onclick: () => m.close() }, "取消"), h("button.btn.primary", { onclick: async () => {
      try { await api(`/api/works/${S.work.id}`, { method: "PATCH", body: { title: input.value } }); S.work.title = input.value.trim(); m.close(); stageTop.querySelector(".stage-title span").textContent = S.work.title; await refreshWorks(); drawRight(); }
      catch (e) { toast(errorText(e), "err"); }
    } }, "保存")] });
  }
  async function deleteWork() {
    if (!await confirmBox("删除作品", `确定删除“${S.work.title}”吗？删除后无法恢复。`, { ok: "删除", danger: true })) return;
    try { await api(`/api/works/${S.work.id}`, { method: "DELETE" }); toast("已删除", "ok"); await refreshWorks(); selectWork(null); }
    catch (e) { toast(errorText(e), "err"); }
  }

  // ---------------------------------------------------------------- ready: preview + timeline
  function drawReady() {
    const w = S.work;
    P.fps = w.fps || 24; P.frames = w.frames || 0; P.t = 0;
    const modes = [["compare", "并排对比"], ["3d", "3D 模型"]];
    if (w.files?.compare) modes.push(["fit", "拟合效果"]);
    if (!modes.some(([k]) => k === S.mode)) S.mode = "compare";
    const seg = h("div.seg", modes.map(([k, l]) => h("button", { "aria-pressed": String(k === S.mode), onclick: () => setMode(k, seg) }, l)));
    stageTop.append(seg);
    const srcPane = h("div.pane", h("div.video-pane", S.srcVideo = h("video", { src: w.video, muted: true, playsInline: true, preload: "auto" })), h("span.pane-label", "你上传的视频"));
    const modelPane = h("div.pane");
    const labPane = h("div.pane", h("div.video-pane", S.labVideo = w.files?.compare ? h("video", { src: `/api/works/${w.id}/media/compare.mp4`, muted: true, playsInline: true, preload: "metadata" }) : h("div")),
      h("span.pane-label", "上：视频里识别到的关节 · 下：角色做出的动作"));
    stage.append(srcPane, modelPane, labPane);
    S.panes = { srcPane, modelPane, labPane };
    for (const v of [S.srcVideo, S.labVideo]) {
      if (!(v instanceof HTMLVideoElement)) continue;
      v.addEventListener("loadedmetadata", () => { if (!P.frames && v === S.srcVideo) P.frames = Math.round(v.duration * P.fps); syncVideos(); drawTimelineKeys(); });
      v.addEventListener("ended", () => { if (P.playing && v === master()) { v.currentTime = 0; v.play().catch(() => {}); } });
    }
    S.viewer = createViewer(modelPane, { autoplay: false, fallbackImage: w.thumb });
    const wid = w.id;
    S.viewer.load(`/api/works/${w.id}/media/model.glb`, "glb").then((r) => {
      if (!r || S.work?.id !== wid) return;
      P.clipStart = r.clipStart; P.modelDur = r.duration;
      applyModelTime();
    }).catch(() => {
      // No 3D: the fit video (joints + the character's motion) still shows the result.
      if (S.work?.id === wid && S.mode === "3d" && w.files?.compare) { S.mode = "fit"; setMode("fit", seg); }
    });
    setMode(S.mode, seg);
    buildTimeline();
    loadKeys();
  }
  function setMode(m, seg) {
    pause();
    S.mode = m;
    for (const b of seg.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.textContent === { compare: "并排对比", "3d": "3D 模型", fit: "拟合效果" }[m]));
    const { srcPane, modelPane, labPane } = S.panes;
    srcPane.hidden = m !== "compare";
    modelPane.hidden = m === "fit";
    labPane.hidden = m !== "fit";
    stage.classList.toggle("two", m === "compare");
    syncVideos();
  }
  function master() {
    const v = S.mode === "fit" ? S.labVideo : S.mode === "compare" ? S.srcVideo : null;
    return v instanceof HTMLVideoElement ? v : null;
  }
  const duration = () => (P.frames ? P.frames / P.fps : (S.srcVideo?.duration || P.modelDur || 0));
  const curFrame = () => K.clampFrame(Math.floor(P.t * P.fps + 1e-6), P.frames || 1);
  function applyModelTime() {
    if (!S.viewer) return;
    const f = curFrame();
    S.viewer.setClipTime(P.clipStart + Math.min(f / P.fps, P.modelDur || Infinity));
  }
  function syncVideos() {
    const f = curFrame();
    for (const v of [S.srcVideo, S.labVideo]) {
      if (v instanceof HTMLVideoElement && v.readyState >= 1 && Math.abs(v.currentTime - K.frameToTime(f, P.fps)) > 0.6 / P.fps) {
        v.currentTime = Math.min(K.frameToTime(f, P.fps), Math.max(0, (v.duration || 1e9) - 0.001));
      }
    }
    applyModelTime();
    updateHead();
  }
  function play() {
    if (P.playing) return;
    if (curFrame() >= (P.frames || 1) - 1) P.t = 0, syncVideos();
    P.playing = true;
    master()?.play().catch(() => {});
    P.last = performance.now();
    cancelAnimationFrame(P.raf);
    P.raf = requestAnimationFrame(tick);
    updatePlayBtn();
  }
  function pause() {
    P.playing = false;
    cancelAnimationFrame(P.raf);
    for (const v of [S.srcVideo, S.labVideo]) if (v instanceof HTMLVideoElement && !v.paused) v.pause();
    updatePlayBtn();
  }
  function tick(now) {
    if (!P.playing || !S.alive) return;
    const dt = (now - P.last) / 1000;
    P.last = now;
    const mv = master();
    if (mv && !mv.paused && mv.readyState >= 2) P.t = mv.currentTime;
    else if (!mv) { P.t += dt; if (P.t >= duration()) P.t = 0; }
    applyModelTime();
    updateHead();
    P.raf = requestAnimationFrame(tick);
  }
  function seek(f) {
    pause();
    P.t = K.clampFrame(f, P.frames || 1) / P.fps + 0.25 / P.fps;
    syncVideos();
    if (S.tab === "keys") drawKeysPanel();
  }
  function step(d) { seek(curFrame() + d); }
  function jumpKey(dir) {
    const f = curFrame();
    const ks = (S.st?.keyframes || []).map((k) => k.frame);
    const t = dir > 0 ? ks.find((x) => x > f) : [...ks].reverse().find((x) => x < f);
    if (t != null) seek(t);
  }

  let T = {};
  function buildTimeline() {
    timeline.hidden = false;
    const playBtn = h("button.icon-btn.play", { title: "播放/暂停（空格）", "aria-label": "播放", html: ICON.play, onclick: () => (P.playing ? pause() : play()) });
    const time = h("span.time");
    const track = h("div.tl-track", { role: "slider", "aria-label": "时间轴", tabindex: "0" });
    const curve = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    curve.setAttribute("class", "curve"); curve.setAttribute("preserveAspectRatio", "none"); curve.setAttribute("viewBox", "0 0 1000 100");
    const head = h("div.head");
    const keys = h("div", { style: { position: "absolute", inset: "0 7px" } });
    track.append(curve, keys, head);
    T = { playBtn, time, track, keys, head, curve };
    clear(timeline,
      h("div.tl-controls",
        h("button.icon-btn", { title: "上一个关键帧（Shift+←）", html: ICON.prevKey, onclick: () => jumpKey(-1) }),
        h("button.icon-btn", { title: "上一帧（←）", html: ICON.prev, onclick: () => step(-1) }),
        playBtn,
        h("button.icon-btn", { title: "下一帧（→）", html: ICON.next, onclick: () => step(1) }),
        h("button.icon-btn", { title: "下一个关键帧（Shift+→）", html: ICON.nextKey, onclick: () => jumpKey(1) }),
        time,
        h("span.spacer"),
        h("div.tl-legend", h("span", h("i", { style: { background: "#7a6cff" } }), "自动标记"), h("span", h("i", { style: { background: "#f9cf00" } }), "手动标记"), h("span", h("i", { style: { background: "#9aa3ad" } }), "过渡帧")),
        h("button.icon-btn", { title: "快捷键（?）", "aria-label": "快捷键", html: ICON.keyboard, style: { marginLeft: "8px" }, onclick: showShortcuts }),
        h("button.btn.sm.primary", { style: { marginLeft: "6px" }, title: "快捷键 K", onclick: () => { if (S.st) addKey(curFrame()); } }, h("span", { html: ICON.plus }), "标记当前帧", h("span.kbd", { style: { marginLeft: "4px", background: "#0002", color: "#000" } }, "K"))),
      track);
    // double-click empty track = add a key there
    track.addEventListener("dblclick", (e) => {
      if (e.target.closest(".kf") || !S.st) return;
      const f = frameAt(e.clientX);
      seek(f);
      addKey(f);
    });
    // scrub on the track
    let scrubbing = false;
    const frameAt = (x) => {
      const r = keys.getBoundingClientRect();
      return K.clampFrame(Math.round(((x - r.left) / Math.max(1, r.width)) * Math.max(1, P.frames - 1)), P.frames || 1);
    };
    track.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".kf")) return;
      scrubbing = true; track.setPointerCapture(e.pointerId); seek(frameAt(e.clientX));
    });
    track.addEventListener("pointermove", (e) => { if (scrubbing) seek(frameAt(e.clientX)); });
    track.addEventListener("pointerup", () => { scrubbing = false; });
    T.frameAt = frameAt;
    drawTimelineKeys();
    drawCurve();
    updateHead();
  }
  function pct(f) { return P.frames > 1 ? (f / (P.frames - 1)) * 100 : 0; }
  function drawTimelineKeys() {
    if (!T.keys || !S.st) return;
    clear(T.keys, S.st.keyframes.map((k) => {
      const d = h(`div.kf${k.origin === "artist" ? ".manual" : ""}${S.sel === k.id ? ".sel" : ""}`, { title: `第 ${k.frame} 帧 · ${typeLabel(k.type)}（${ORIGIN_LABEL[k.origin]}）`, style: { left: pct(k.frame) + "%", background: keyColor(k) } });
      let moved = false, startX = 0, target = k.frame;
      d.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); removeKey(k.id); });
      d.addEventListener("dblclick", (e) => e.stopPropagation());
      d.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; e.stopPropagation(); d.setPointerCapture(e.pointerId); moved = false; startX = e.clientX; target = k.frame; });
      d.addEventListener("pointermove", (e) => {
        if (!d.hasPointerCapture(e.pointerId)) return;
        if (Math.abs(e.clientX - startX) > 3) moved = true;
        if (!moved) return;
        target = T.frameAt(e.clientX);
        d.style.left = pct(target) + "%";
      });
      d.addEventListener("pointerup", (e) => {
        if (!d.hasPointerCapture(e.pointerId)) return;
        S.sel = k.id;
        if (moved && target !== k.frame) { if (!commit(K.moveKey(S.st, k.id, target, P.frames), `第 ${k.frame} 帧 → 第 ${target} 帧`)) drawTimelineKeys(); else seek(target); }
        else { seek(k.frame); drawTimelineKeys(); }
        if (S.tab !== "keys") setTab("keys"); else drawKeysPanel();
      });
      return d;
    }));
  }
  function drawCurve() {
    if (!T.curve) return;
    const curves = S.view?.auto?.curves || {};
    const c = curves.body_speed || Object.values(curves).find((x) => Array.isArray(x) && x.some((v) => typeof v === "number" && v > 1));
    if (!Array.isArray(c) || c.length < 2) { T.curve.innerHTML = ""; return; }
    const vals = c.map((v) => (typeof v === "number" ? v : 0));
    const max = Math.max(...vals) || 1;
    const pts = vals.map((v, i) => `${(7 + (i / (vals.length - 1)) * 986).toFixed(1)},${(92 - (v / max) * 80).toFixed(1)}`).join(" ");
    T.curve.innerHTML = `<polyline points="7,92 ${pts} 993,92" fill="#503be333" stroke="#7a6cff" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
  }
  function updateHead() {
    if (!T.head) return;
    const f = curFrame();
    T.head.style.left = `calc(7px + (100% - 14px) * ${pct(f) / 100})`;
    T.time.textContent = `第 ${f} / ${Math.max(0, (P.frames || 1) - 1)} 帧 · ${(f / P.fps).toFixed(2)} 秒`;
  }
  function updatePlayBtn() { if (T.playBtn) { T.playBtn.innerHTML = P.playing ? ICON.pause : ICON.play; T.playBtn.setAttribute("aria-label", P.playing ? "暂停" : "播放"); } }

  // ---------------------------------------------------------------- keyboard
  function onKey(e) {
    if (S.work?.status !== "ready" || e.target.closest?.("input,select,textarea,[contenteditable]") || document.querySelector(".modal-back")) return;
    const ctrl = e.ctrlKey || e.metaKey;
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    let done = true;
    if (ctrl && key === "z" && S.st) undoRedo(e.shiftKey ? 1 : -1);
    else if (ctrl && key === "y" && S.st) undoRedo(1);
    else if (ctrl && key === "ArrowLeft") step(-10);
    else if (ctrl && key === "ArrowRight") step(10);
    else if (ctrl || e.altKey) done = false;
    else if (key === " ") { P.playing ? pause() : play(); }
    else if (key === "ArrowLeft") { e.shiftKey ? jumpKey(-1) : step(-1); }
    else if (key === "ArrowRight") { e.shiftKey ? jumpKey(1) : step(1); }
    else if (key === "Home") seek(0);
    else if (key === "End") seek((P.frames || 1) - 1);
    else if ((key === "k" || key === "Enter") && S.st) addKey(curFrame());
    else if ((key === "Delete" || key === "Backspace") && S.st) { const k = selectedKey(); if (k) removeKey(k.id); else flash("当前帧没有关键帧"); }
    else if ((key === "," || key === ".") && S.st) { const k = selectedKey(); if (k) nudge(k, key === "," ? -1 : 1); else flash("先选中一个关键帧"); }
    else if (/^[1-6]$/.test(key) && S.st) setSelType(QUICK_TYPES[Number(key) - 1]);
    else if (key === "?" || (key === "/" && e.shiftKey)) showShortcuts();
    else if (key === "Escape") { S.sel = null; drawTimelineKeys(); if (S.tab === "keys") drawKeysPanel(); }
    else done = false;
    if (done) e.preventDefault();
  }
  document.addEventListener("keydown", onKey);
  const beforeUnload = (e) => { if (S.st && S.saved !== K.signature(S.st)) { saveKeys(); e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", beforeUnload);

  return () => {
    S.alive = false;
    clearTimeout(S.poll);
    if (S.st && S.saved !== K.signature(S.st)) { clearTimeout(S.saveTimer); S.alive = true; saveKeys().finally(() => { S.alive = false; }); }
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("beforeunload", beforeUnload);
    disposeStage();
  };
}
