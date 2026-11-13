// 3D preview that never takes the page down: three.js and its loaders are imported on demand.
// If they cannot be loaded (network, proxy, old browser), only the preview area shows a notice;
// login, library, upload, generation, keyframes and export keep working.
import { h, ICON } from "./util.js?v=8";

let modPromise = null;
function loadModule() {
  if (!modPromise) modPromise = import("./viewer.js?v=8").catch((e) => { modPromise = null; throw e; });
  return modPromise;
}

const NOOP_VIEWER = { duration: 0, clipStart: 0, setClipTime() {}, setPlaying() {}, frame() {}, message() {},
  setOrientation() {}, size() { return null; }, showFront() {}, showSkeleton() { return false; }, viewFront() {} };

// Same API as viewer.js createViewer(), returned synchronously.
export function createViewer(container, opts = {}) {
  let real = null, disposed = false, failed = false;
  const holder = h("div.viewer", { style: { width: "100%", height: "100%" } }, h("div.v-state", h("div", h("div.spinner"), "正在加载 3D 预览…")));
  container.append(holder);

  function showFailure() {
    const retry = h("button.btn.sm", { style: { pointerEvents: "auto", marginTop: "12px" }, onclick: () => { holder.remove(); start(); } }, h("span", { html: ICON.reset }), "重试");
    holder.replaceChildren(
      opts.fallbackImage ? h("img.v-fallback", { src: opts.fallbackImage, alt: "" }) : null,
      h("div.v-state", h("div",
        h("div", { style: { fontSize: "14px", color: "var(--w80)", marginBottom: "4px" } }, "3D 预览暂时无法加载"),
        h("div", "可能是网络或浏览器的问题。不影响生成动画、标记关键帧和导出。"),
        retry)));
    if (!holder.isConnected) container.append(holder);
  }

  const ready = new Promise((resolve) => {
    start._resolve = resolve;
  });
  function start() {
    failed = false;
    if (!holder.isConnected) {
      holder.replaceChildren(h("div.v-state", h("div", h("div.spinner"), "正在加载 3D 预览…")));
      container.append(holder);
    }
    loadModule().then((mod) => {
      if (disposed) return;
      holder.remove();
      real = mod.createViewer(container, opts);
      start._resolve?.(true);
      if (facade._pending) { const [url, kind, res, rej] = facade._pending; facade._pending = null; real.load(url, kind).then(res, rej); }
    }).catch(() => {
      if (disposed) return;
      failed = true;
      showFailure();
      start._resolve?.(false);
      if (facade._pending) { const p = facade._pending; facade._pending = null; p[3](new Error("3D 预览不可用")); }
    });
  }

  const facade = {
    _pending: null,
    ready,
    get el() { return real ? real.el : holder; },
    get duration() { return (real || NOOP_VIEWER).duration; },
    get clipStart() { return (real || NOOP_VIEWER).clipStart; },
    load(url, kind = "glb") {
      if (real) return real.load(url, kind);
      if (failed) return Promise.reject(new Error("3D 预览不可用"));
      return new Promise((res, rej) => { facade._pending = [url, kind, res, rej]; });
    },
    frame() { (real || NOOP_VIEWER).frame(); },
    setPlaying(p) { (real || NOOP_VIEWER).setPlaying(p); },
    setClipTime(t) { (real || NOOP_VIEWER).setClipTime(t); },
    message(text) { (real || NOOP_VIEWER).message(text); },
    setOrientation(m, reframe) { (real || NOOP_VIEWER).setOrientation?.(m, reframe); },
    size() { return (real || NOOP_VIEWER).size?.() ?? null; },
    showFront(on) { (real || NOOP_VIEWER).showFront?.(on); },
    showSkeleton(on) { return (real || NOOP_VIEWER).showSkeleton?.(on) ?? false; },
    viewFront() { (real || NOOP_VIEWER).viewFront?.(); },
    get ok() { return Boolean(real); },
    dispose() {
      disposed = true;
      facade._pending = null;
      real?.dispose();
      holder.remove();
    },
  };
  start();
  return facade;
}
