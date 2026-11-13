// Shared helpers: API calls, DOM building, toasts, modals, icons.

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : detail?.message || `请求失败（${status}）`);
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, { method = "GET", body, form, signal } = {}) {
  const opt = { method, headers: { "X-Studio": "1" }, signal, credentials: "same-origin" };
  if (form) opt.body = form;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch(path, opt); }
  catch (e) { if (e.name === "AbortError") throw e; throw new ApiError(0, "网络连接失败，请检查网络后重试"); }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    if (r.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new CustomEvent("studio:logout"));
    throw new ApiError(r.status, data?.detail ?? (typeof data === "string" && data ? data : `请求失败（${r.status}）`));
  }
  return data;
}

// Upload with progress (fetch has no upload progress).
export function upload(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", path);
    x.setRequestHeader("X-Studio", "1");
    x.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let data = null;
      try { data = JSON.parse(x.responseText); } catch { data = x.responseText; }
      if (x.status >= 200 && x.status < 300) resolve(data);
      else reject(new ApiError(x.status, data?.detail || `上传失败（${x.status}）`));
    };
    x.onerror = () => reject(new ApiError(0, "网络连接失败，上传没有完成"));
    x.send(form);
  });
}

// h("div.card#id", {onclick, attrs}, ...children)
export function h(tag, props, ...children) {
  const m = /^([a-z0-9]+)?((?:[.#][^.#]+)*)$/i.exec(tag);
  const el = document.createElement(m?.[1] || "div");
  for (const part of (m?.[2] || "").match(/[.#][^.#]+/g) || []) {
    if (part[0] === ".") el.classList.add(part.slice(1)); else el.id = part.slice(1);
  }
  if (props && (typeof props !== "object" || props instanceof Node || Array.isArray(props))) { children.unshift(props); props = null; }
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = [el.className, v].filter(Boolean).join(" ");
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}
function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}
export function clear(el, ...children) { el.replaceChildren(); append(el, children); return el; }

export function toast(message, kind = "") {
  const t = h(`div.toast${kind ? "." + kind : ""}`, message);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 5200 : 3000);
}
export function errorText(e) { return e?.message || String(e); }

let modalStack = [];
export function modal({ title, body, foot, wide = false, onClose } = {}) {
  const box = h(`div.modal${wide ? ".wide" : ""}`, { role: "dialog", "aria-modal": "true", "aria-label": title || "对话框" });
  const back = h("div.modal-back", box);
  const close = () => {
    back.remove();
    modalStack = modalStack.filter((m) => m !== api);
    document.removeEventListener("keydown", onKey);
    onClose?.();
  };
  const onKey = (e) => { if (e.key === "Escape" && modalStack[modalStack.length - 1] === api) close(); };
  if (title) box.append(h("div.modal-head", h("h3", title), h("button.icon-btn", { "aria-label": "关闭", onclick: close, html: ICON.x })));
  if (body) box.append(body instanceof Node && body.classList?.contains("modal-body") ? body : h("div.modal-body", body));
  if (foot) box.append(h("div.modal-foot", foot));
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  document.addEventListener("keydown", onKey);
  document.getElementById("modalRoot").append(back);
  const api = { close, box };
  modalStack.push(api);
  setTimeout(() => box.querySelector("input:not([type=file]),button.primary")?.focus(), 30);
  return api;
}

export function confirmBox(title, text, { ok = "确定", danger = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const m = modal({
      title, body: h("p", { style: { color: "var(--w80)" } }, text),
      foot: [h("button.btn.ghost", { onclick: () => { done = true; m.close(); resolve(false); } }, "取消"),
        h(`button.btn.${danger ? "accent" : "primary"}`, { onclick: () => { done = true; m.close(); resolve(true); } }, ok)],
      onClose: () => { if (!done) resolve(false); },
    });
  });
}

export function lightbox(src) {
  const lb = h("div.lightbox", { onclick: () => lb.remove() }, h("img", { src, alt: "" }));
  document.body.append(lb);
}

export function fmtSize(n) {
  if (n > 1 << 30) return (n / (1 << 30)).toFixed(1) + " GB";
  if (n > 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
  return Math.max(1, Math.round(n / 1024)) + " KB";
}
export function fmtTime(t) {
  if (!t) return "";
  const d = new Date(t * 1000), now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400 && d.getDate() === now.getDate()) return `今天 ${d.toTimeString().slice(0, 5)}`;
  return `${d.getMonth() + 1}月${d.getDate()}日 ${d.toTimeString().slice(0, 5)}`;
}
export function statusBadge(status, text) {
  const cls = { ready: "ok", done: "ok", running: "run", queued: "run", waiting: "run", preparing: "run", pending_review: "warn",
    needs_review: "warn", uploaded: "", failed: "bad", cancelled: "" }[status] ?? "";
  return h(`span.badge${cls ? "." + cls : ""}`, text);
}

export function pickFiles({ accept = "", multiple = false } = {}) {
  return new Promise((resolve) => {
    const i = h("input", { type: "file", accept, multiple, style: { display: "none" } });
    i.addEventListener("change", () => { resolve([...i.files]); i.remove(); });
    document.body.append(i);
    i.click();
  });
}
export function dropZone({ title, hint, accept, multiple = false, onFiles }) {
  const z = h("div.drop", { tabindex: "0", role: "button" }, h("span", { html: ICON.upload }), h("b", title), hint && h("small", hint));
  const pick = async () => { const f = await pickFiles({ accept, multiple }); if (f.length) onFiles(f); };
  z.addEventListener("click", pick);
  z.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  z.addEventListener("dragover", (e) => { e.preventDefault(); z.classList.add("over"); });
  z.addEventListener("dragleave", () => z.classList.remove("over"));
  z.addEventListener("drop", (e) => { e.preventDefault(); z.classList.remove("over"); const f = [...e.dataTransfer.files]; if (f.length) onFiles(multiple ? f : f.slice(0, 1)); });
  return z;
}

const svg = (p, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" ${extra}>${p}</svg>`;
export const ICON = {
  logo: `<svg viewBox="0 0 32 32"><path d="M7 23l6-14 5 9 2.5-4.5L25 23" stroke="#f9cf00" stroke-width="2.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  x: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
  upload: svg('<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>'),
  download: svg('<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 18v1a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1"/>'),
  play: svg('<path d="M8 5v14l11-7z" fill="currentColor"/>'),
  pause: svg('<path d="M8 5v14M16 5v14" stroke-width="3"/>'),
  prev: svg('<path d="M15 6l-6 6 6 6"/>'),
  next: svg('<path d="M9 6l6 6-6 6"/>'),
  prevKey: svg('<path d="M18 6l-6 6 6 6M8 6v12"/>'),
  nextKey: svg('<path d="M6 6l6 6-6 6M16 6v12"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  trash: svg('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-12M9 7V4h6v3"/>'),
  user: svg('<circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6"/>'),
  cube: svg('<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>'),
  film: svg('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4"/>'),
  wand: svg('<path d="M4 20l11-11M14 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1zM19 11l.6 1.4L21 13l-1.4.6L19 15l-.6-1.4L17 13l1.4-.6z"/>'),
  key: svg('<path d="M12 3l3 9-3 9-3-9z"/>'),
  export: svg('<path d="M12 15V3M8 7l4-4 4 4"/><rect x="4" y="13" width="16" height="8" rx="2"/>'),
  image: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>'),
  reset: svg('<path d="M4 12a8 8 0 1 0 3-6.3M4 4v4h4"/>'),
  copy: svg('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/>'),
  back: svg('<path d="M15 6l-6 6 6 6"/>'),
  grid: svg('<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>'),
  edit: svg('<path d="M4 20h4L19 9l-4-4L4 16z"/>'),
  check: svg('<path d="M5 12l5 5 9-10"/>'),
  monster: svg('<path d="M6 20v-6a6 6 0 0 1 12 0v6M9 20v-3M15 20v-3M8 8L6 4M16 8l2-4"/><circle cx="10" cy="12" r="1" fill="currentColor"/><circle cx="14" cy="12" r="1" fill="currentColor"/>'),
  split: svg('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M12 5v14"/>'),
  undo: svg('<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>'),
  redo: svg('<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>'),
  keyboard: svg('<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>'),
  eye: svg('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
};
