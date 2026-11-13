// Pure keyframe-annotation logic (no DOM). Contract: video2rig/docs/关键帧数据与网页接口约定_20261111.md
// Tested by video2rig/tools/test_keyframes.cjs.

export const TYPES = ["start", "end", "contact", "extreme", "hold_start", "hold_end", "impact",
  "anticipation", "rebound", "technical", "breakdown", "custom"];
export const TYPE_LABEL = {
  start: "起始", end: "结束", contact: "接触", extreme: "极值", hold_start: "停顿开始", hold_end: "停顿结束",
  impact: "发力点", anticipation: "预备", rebound: "回弹", technical: "技术键", breakdown: "过渡键", custom: "自定义",
};
export const TYPE_COLOR = {
  start: "#9aa8bb", end: "#9aa8bb", contact: "#65cba0", extreme: "#e3b66d", hold_start: "#7cb4f8", hold_end: "#4f86c6",
  impact: "#f18b80", anticipation: "#c99cf0", rebound: "#d47fc0", technical: "#5fd0d6", breakdown: "#b8c4d4", custom: "#ffd36b",
};
export const PART_LABEL = { body: "身体", head: "头", L_arm: "左臂", R_arm: "右臂", L_foot: "左脚", R_foot: "右脚", ears: "耳朵", tail: "尾巴" };
export const LIMITS = { keys: 500, note: 500, notes: 2000, author: 40 };

// ------------------------------------------------------------------ frames and time
export function clampFrame(frame, frames) {
  const n = Math.max(1, Math.floor(Number(frames) || 1));
  const f = Math.round(Number(frame) || 0);
  return Math.min(n - 1, Math.max(0, f));
}
// Seek to the middle of the frame so the decoder does not land on the previous one.
export function frameToTime(frame, fps) {
  return (Number(frame) + 0.5) / fps;
}
export function timeToFrame(time, fps, frames) {
  return clampFrame(Math.floor(Number(time) * fps + 1e-6), frames);
}

// ------------------------------------------------------------------ data access
export function autoKeys(auto) {
  return Array.isArray(auto?.keyframes) ? auto.keyframes.filter((k) => k && typeof k.id === "string" && Number.isInteger(k.frame)) : [];
}
export function ladderInfo(auto) {
  const ladder = auto?.breakdown_ladder;
  if (ladder == null) return { ok: false, reason: auto ? "候选文件没有过渡键阶梯（breakdown_ladder）。" : "" };
  if (typeof ladder !== "object") return { ok: false, reason: "过渡键阶梯格式无效。" };
  if (ladder.error) return { ok: false, reason: `过渡键阶梯计算失败：${ladder.error}` };
  const breakdowns = Array.isArray(ladder.breakdowns) ? ladder.breakdowns.filter((b) => Number.isInteger(b?.rank) && Number.isInteger(b?.frame)) : [];
  if (!breakdowns.length) return { ok: false, reason: "过渡键阶梯为空：结构键插值已经足够贴合。" };
  breakdowns.sort((a, b) => a.rank - b.rank);
  const curve = Array.isArray(ladder.error_curve) ? ladder.error_curve : [];
  const suggested = Number.isInteger(ladder.suggested_count) ? Math.min(Math.max(0, ladder.suggested_count), breakdowns.length) : breakdowns.length;
  return { ok: true, breakdowns, curve, suggested, rule: ladder.suggestion_rule || "", spinFrames: ladder.spin_frames || null, units: ladder.error_curve_units || "" };
}
export function curveAt(info, k) {
  if (!info?.ok) return null;
  return info.curve.find((c) => c?.n === k) || null;
}

// ------------------------------------------------------------------ annotation state
// state = { keyframes: [{id, frame, type, note, origin, auto_id, auto_frame, ladder_rank?}], removed: [auto ids], author, notes, reviewed }
export function initialState(view) {
  const auto = view?.auto || null;
  const artist = view?.artist || null;
  if (artist && Array.isArray(artist.keyframes)) {
    const st = {
      fromArtist: true,
      keyframes: artist.keyframes.map((k) => normalizeKey(k)).sort(byFrame),
      removed: Array.isArray(artist.removed_auto_ids) ? [...artist.removed_auto_ids] : [],
      author: typeof artist.author === "string" ? artist.author : "",
      notes: typeof artist.notes === "string" ? artist.notes : "",
      reviewed: artist.reviewed === true,
    };
    const stale = artist.base_auto_sha256 && view?.auto_sha256 && artist.base_auto_sha256 !== view.auto_sha256;
    return stale ? rebase(st, auto) : st;
  }
  return {
    fromArtist: false,
    keyframes: autoKeys(auto).map((k) => ({ id: k.id, frame: k.frame, type: TYPES.includes(k.type) ? k.type : "custom", note: "", origin: "auto", auto_id: k.id, auto_frame: k.frame })).sort(byFrame),
    removed: [], author: "", notes: "", reviewed: false,
  };
}
function normalizeKey(k) {
  const out = { id: String(k.id), frame: Number(k.frame), type: TYPES.includes(k.type) ? k.type : "custom", note: typeof k.note === "string" ? k.note : "",
    origin: ["auto", "artist", "ladder"].includes(k.origin) ? k.origin : "artist", auto_id: k.auto_id ?? null, auto_frame: k.auto_frame ?? null };
  if (out.origin === "ladder") out.ladder_rank = Number(k.ladder_rank);
  return out;
}
const byFrame = (a, b) => a.frame - b.frame;
// The candidate file was regenerated after this annotation was saved: ids may point at different keys.
// Keep every annotated frame; references that no longer match become plain artist keys (a+ id), and
// candidates that were deleted are dropped from removed_auto_ids when they no longer exist.
export function rebase(state, auto) {
  const cand = new Map(autoKeys(auto).map((k) => [k.id, k]));
  const ladder = ladderInfo(auto);
  const ranks = new Map(ladder.ok ? ladder.breakdowns.map((b) => [b.rank, b]) : []);
  const s = { ...state, keyframes: state.keyframes.map((k) => ({ ...k })), removed: [], rebased: { converted: 0, removedDropped: 0, kept: 0 } };
  const toArtist = (k) => {
    k.origin = "artist"; k.auto_id = null; k.auto_frame = null; delete k.ladder_rank;
    k.id = nextArtistId(s);
    s.rebased.converted++;
  };
  for (const k of s.keyframes) {
    if (k.origin === "auto") {
      const c = cand.get(k.auto_id);
      // same id, same original frame -> still the same candidate
      if (c && c.frame === k.auto_frame) { k.id = c.id; s.rebased.kept++; } else toArtist(k);
    } else if (k.origin === "ladder") {
      const b = ranks.get(k.ladder_rank);
      if (b && b.frame === k.auto_frame) s.rebased.kept++; else toArtist(k);
    }
  }
  for (const id of state.removed) {
    if (cand.has(id) && !s.keyframes.some((k) => k.auto_id === id)) s.removed.push(id); else s.rebased.removedDropped++;
  }
  s.keyframes.sort(byFrame);
  return s;
}
export function keyAt(state, frame, exceptId = null) {
  return state.keyframes.find((k) => k.frame === frame && k.id !== exceptId) || null;
}
export function nextArtistId(state) {
  let max = 0;
  for (const k of state.keyframes) {
    const m = /^a(\d+)$/.exec(k.id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  let n = max + 1, id;
  do { id = `a${String(n++).padStart(3, "0")}`; } while (state.keyframes.some((k) => k.id === id));
  return id;
}
// Every mutation returns {ok, state, message}; the input state is never modified.
const clone = (s) => ({ ...s, keyframes: s.keyframes.map((k) => ({ ...k })), removed: [...s.removed] });
export function addKey(state, frame, type = "custom", frames = Infinity) {
  if (!Number.isInteger(frame) || frame < 0 || frame >= frames) return { ok: false, state, message: `帧号 ${frame} 超出范围。` };
  const hit = keyAt(state, frame);
  if (hit) return { ok: false, state, message: `第 ${frame} 帧已有关键帧（${TYPE_LABEL[hit.type] || hit.type}），同一帧只能有一个。` };
  if (state.keyframes.length >= LIMITS.keys) return { ok: false, state, message: `关键帧不能超过 ${LIMITS.keys} 个。` };
  const s = clone(state);
  const id = nextArtistId(s);
  s.keyframes.push({ id, frame, type: TYPES.includes(type) ? type : "custom", note: "", origin: "artist", auto_id: null, auto_frame: null });
  s.keyframes.sort(byFrame);
  return { ok: true, state: s, id, message: `已在第 ${frame} 帧添加关键帧。` };
}
export function removeKey(state, id) {
  const k = state.keyframes.find((x) => x.id === id);
  if (!k) return { ok: false, state, message: "找不到这个关键帧。" };
  const s = clone(state);
  s.keyframes = s.keyframes.filter((x) => x.id !== id);
  if (k.origin === "auto" && k.auto_id && !s.removed.includes(k.auto_id)) s.removed.push(k.auto_id);
  return { ok: true, state: s, message: k.origin === "auto" ? `已删除候选 ${k.auto_id}，可在“已删除的候选”里恢复。` : `已删除第 ${k.frame} 帧的关键帧。` };
}
export function restoreAuto(state, auto, autoId) {
  const src = autoKeys(auto).find((k) => k.id === autoId);
  if (!src) return { ok: false, state, message: `候选 ${autoId} 不存在。` };
  if (state.keyframes.some((k) => k.auto_id === autoId)) return { ok: false, state, message: `候选 ${autoId} 已在标注里。` };
  const hit = keyAt(state, src.frame);
  if (hit) return { ok: false, state, message: `第 ${src.frame} 帧已有关键帧，先移走它再恢复候选 ${autoId}。` };
  const s = clone(state);
  s.removed = s.removed.filter((x) => x !== autoId);
  s.keyframes.push({ id: src.id, frame: src.frame, type: TYPES.includes(src.type) ? src.type : "custom", note: "", origin: "auto", auto_id: src.id, auto_frame: src.frame });
  s.keyframes.sort(byFrame);
  return { ok: true, state: s, message: `已恢复候选 ${autoId}（第 ${src.frame} 帧）。` };
}
export function moveKey(state, id, frame, frames = Infinity) {
  const k = state.keyframes.find((x) => x.id === id);
  if (!k) return { ok: false, state, message: "找不到这个关键帧。" };
  if (!Number.isInteger(frame) || frame < 0 || frame >= frames) return { ok: false, state, message: `帧号 ${frame} 超出范围。` };
  if (k.frame === frame) return { ok: true, state, message: "" };
  const hit = keyAt(state, frame, id);
  if (hit) return { ok: false, state, message: `第 ${frame} 帧已有关键帧（${TYPE_LABEL[hit.type] || hit.type}），不能移过去。` };
  const s = clone(state);
  s.keyframes.find((x) => x.id === id).frame = frame;
  s.keyframes.sort(byFrame);
  return { ok: true, state: s, message: `已移到第 ${frame} 帧。` };
}
export function setKeyField(state, id, field, value) {
  if (!["type", "note"].includes(field)) return { ok: false, state, message: "不能修改这个字段。" };
  if (field === "type" && !TYPES.includes(value)) return { ok: false, state, message: "类型无效。" };
  if (field === "note" && String(value).length > LIMITS.note) return { ok: false, state, message: `备注不能超过 ${LIMITS.note} 字。` };
  const s = clone(state);
  const k = s.keyframes.find((x) => x.id === id);
  if (!k) return { ok: false, state, message: "找不到这个关键帧。" };
  k[field] = field === "note" ? String(value) : value;
  return { ok: true, state: s, message: "" };
}
// Adopt the first k ladder breakdowns; frames already holding a key are skipped and counted.
export function adoptLadder(state, info, k) {
  if (!info?.ok) return { ok: false, state, message: "没有可用的过渡键阶梯。", added: 0, skipped: 0 };
  const s = clone(state);
  let added = 0, skipped = 0, already = 0;
  for (const b of info.breakdowns.slice(0, Math.max(0, k))) {
    if (s.keyframes.some((x) => x.origin === "ladder" && x.ladder_rank === b.rank)) { already++; continue; }
    if (keyAt(s, b.frame)) { skipped++; continue; }
    if (s.keyframes.length >= LIMITS.keys) { skipped++; continue; }
    s.keyframes.push({ id: `b${String(b.rank).padStart(3, "0")}`, frame: b.frame, type: "breakdown", note: "", origin: "ladder", auto_id: null, auto_frame: b.frame, ladder_rank: b.rank });
    added++;
  }
  s.keyframes.sort(byFrame);
  const parts = [`已采纳 ${added} 个过渡键`];
  if (already) parts.push(`${already} 个之前已采纳`);
  if (skipped) parts.push(`${skipped} 个因同帧已有关键帧而跳过`);
  return { ok: added > 0, state: added ? s : state, message: parts.join("，") + "。", added, skipped };
}

// ------------------------------------------------------------------ comparison with the candidates
export function diffSummary(state, auto) {
  const cand = autoKeys(auto);
  let kept = 0, moved = 0, shift = 0, added = 0, ladder = 0, retyped = 0;
  for (const k of state.keyframes) {
    if (k.origin === "auto") {
      kept++;
      if (k.auto_frame != null && k.frame !== k.auto_frame) { moved++; shift += Math.abs(k.frame - k.auto_frame); }
      const src = cand.find((c) => c.id === k.auto_id);
      if (src && src.type !== k.type) retyped++;
    } else if (k.origin === "ladder") ladder++;
    else added++;
  }
  const reviewedIds = new Set([...state.keyframes.map((k) => k.auto_id).filter(Boolean), ...state.removed]);
  return { candidates: cand.length, kept, moved, meanShift: moved ? shift / moved : 0, removed: state.removed.length,
    added, ladder, retyped, unseen: cand.filter((c) => !reviewedIds.has(c.id)).length, total: state.keyframes.length };
}
export function summaryText(d) {
  if (!d.candidates) return `共 ${d.total} 个关键帧（无候选，全部为手动标注）`;
  const parts = [`保留候选 ${d.kept}/${d.candidates}`];
  if (d.moved) parts.push(`挪动 ${d.moved} 个（平均 ${d.meanShift.toFixed(1)} 帧）`);
  if (d.retyped) parts.push(`改类型 ${d.retyped}`);
  parts.push(`删除 ${d.removed}`, `新增 ${d.added}`);
  if (d.ladder) parts.push(`采纳过渡键 ${d.ladder}`);
  if (d.unseen) parts.push(`未处理候选 ${d.unseen}`);
  return parts.join(" · ");
}

// ------------------------------------------------------------------ save payload / dirty check
export function payload(state, baseRevision) {
  return {
    author: String(state.author || "").trim().slice(0, LIMITS.author),
    base_revision: baseRevision,
    reviewed: state.reviewed === true,
    notes: String(state.notes || ""),
    keyframes: state.keyframes.map((k) => {
      const out = { id: k.id, frame: k.frame, type: k.type, note: k.note || "", origin: k.origin, auto_id: k.origin === "auto" ? k.auto_id : null, auto_frame: k.origin === "artist" ? null : k.auto_frame };
      if (k.origin === "ladder") out.ladder_rank = k.ladder_rank;
      return out;
    }),
    removed_auto_ids: [...state.removed].sort(),
  };
}
export function validateLocal(state, frames) {
  const problems = [];
  const seen = new Map();
  if (String(state.author || "").trim().length > LIMITS.author) problems.push(`作者不能超过 ${LIMITS.author} 字。`);
  if (String(state.notes || "").length > LIMITS.notes) problems.push(`总备注不能超过 ${LIMITS.notes} 字。`);
  if (state.keyframes.length > LIMITS.keys) problems.push(`关键帧不能超过 ${LIMITS.keys} 个。`);
  for (const k of state.keyframes) {
    if (!Number.isInteger(k.frame) || k.frame < 0 || (Number.isFinite(frames) && k.frame >= frames)) problems.push(`关键帧 ${k.id} 的帧号 ${k.frame} 超出范围。`);
    if (seen.has(k.frame)) problems.push(`第 ${k.frame} 帧同时有 ${seen.get(k.frame)} 和 ${k.id}。`);
    seen.set(k.frame, k.id);
    if ((k.note || "").length > LIMITS.note) problems.push(`关键帧 ${k.id} 的备注超过 ${LIMITS.note} 字。`);
  }
  return problems;
}
export function signature(state) {
  return JSON.stringify(payload(state, 0));
}
// "生成候选关键帧" button state for the page. gen = view.generation from the server.
export function generationState(view, tasks) {
  const gen = view?.generation;
  const running = (tasks || []).find((t) => t?.cmd === "keyframes" && ["queued", "running"].includes(t?.status));
  if (running) return { show: true, enabled: false, label: running.status === "queued" ? "排队中…" : "正在生成…", reason: "", task: running };
  if (!gen) return { show: false, enabled: false, label: "", reason: "" };
  if (!gen.available) return { show: true, enabled: false, label: view?.auto ? "重新生成候选" : "生成候选关键帧", reason: gen.reason || "这个结果不能生成候选。" };
  return { show: true, enabled: true, label: view?.auto ? "重新生成候选" : "生成候选关键帧", reason: "", replace: Boolean(view?.auto) };
}
// 409 handling: decide what to do with the server's answer.
export function conflictInfo(error) {
  const detail = error?.detail;
  if (error?.status !== 409 || !detail || typeof detail !== "object") return null;
  return { message: detail.message || "已有人保存了新版本。", revision: Number.isInteger(detail.current_revision) ? detail.current_revision : null, artist: detail.artist || null };
}
export function exportDocument(state, view, baseRevision) {
  return { schema: "v2r.keyframes.artist/1", exported_at: new Date().toISOString(), source_video: view?.video_name || null,
    fps: view?.fps ?? null, frames: view?.frames ?? null, base_auto_sha256: view?.auto_sha256 ?? null, ...payload(state, baseRevision) };
}
