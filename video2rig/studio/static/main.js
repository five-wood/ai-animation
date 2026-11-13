import { api, upload, h, clear, toast, errorText, modal, confirmBox, statusBadge, fmtTime, fmtSize, dropZone, lightbox, ICON } from "./util.js?v=8";
import { createViewer } from "./viewer-safe.js?v=8";
import { openBench } from "./bench.js?v=8";

const app = document.getElementById("app");
const S = { user: null, config: null, cleanup: null };
window.addEventListener("studio:logout", () => { S.user = null; route(); });

window.__studioBooted = true;
async function boot() {
  try {
    const [me, cfg] = await Promise.all([api("/api/auth/me"), api("/api/config")]);
    S.user = me.user;
    S.config = cfg;
  } catch (e) {
    clear(app, h("div.empty", { style: { margin: "80px auto", maxWidth: "420px" } }, h("h3", "服务暂时无法访问"), h("p", errorText(e))));
    return;
  }
  window.addEventListener("hashchange", route);
  route();
}

function route() {
  S.cleanup?.();
  S.cleanup = null;
  if (!S.user) return loginPage();
  const parts = (location.hash.replace(/^#\/?/, "") || "").split("/").filter(Boolean);
  const [page, a, b] = parts;
  if (page === "bench" && a) return shell("bench", (el) => openBench(el, { characterId: Number(a), workId: b ? Number(b) : null, user: S.user, config: S.config }));
  if (page === "works") return shell("works", worksPage);
  if (page === "admin" && S.user.role === "admin") return shell("admin", adminPage);
  return shell("library", libraryPage);
}

// ============================================================ shell
function shell(active, render) {
  const nav = [["library", "#/", "角色库"], ["works", "#/works", "我的作品"]];
  if (S.user.role === "admin") nav.push(["admin", "#/admin", "管理"]);
  const menu = h("div.menu", { hidden: true },
    h("button", { onclick: changePassword }, "修改密码"),
    h("button", { onclick: logout }, "退出登录"));
  const chip = h("button.user-chip", { "aria-haspopup": "true", onclick: (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; } },
    h("span.avatar", S.user.username.slice(0, 1).toUpperCase()), h("span", S.user.username));
  const closeMenu = () => { menu.hidden = true; };
  document.addEventListener("click", closeMenu);
  const top = h("header.topbar",
    h("a.brand", { href: "#/" }, h("span.brand-mark", { html: ICON.logo }), "女娲-动作工坊"),
    h("nav.top-nav", nav.map(([k, href, label]) => h(`a${k === active ? ".on" : ""}`, { href }, label))),
    h("div.top-right", { style: { position: "relative" } }, chip, menu));
  const body = active === "bench" ? h("div", { style: { flex: "1", minHeight: "0", display: "flex" } }) : h("main.page");
  clear(app, h("div.shell", top, body));
  const inner = render(body);
  S.cleanup = () => { document.removeEventListener("click", closeMenu); inner?.(); };
}

async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* ignore */ }
  S.user = null;
  location.hash = "#/";
  route();
}

function changePassword() {
  const old = h("input.input", { type: "password", autocomplete: "current-password" });
  const nw = h("input.input", { type: "password", autocomplete: "new-password", placeholder: "至少 6 位" });
  const err = h("div.form-error");
  const m = modal({
    title: "修改密码",
    body: [h("div.field", h("label", "原密码"), old), h("div.field", h("label", "新密码"), nw), err],
    foot: [h("button.btn.ghost", { onclick: () => m.close() }, "取消"), h("button.btn.primary", {
      onclick: async () => {
        try { await api("/api/auth/password", { method: "POST", body: { old: old.value, new: nw.value } }); m.close(); toast("密码已修改", "ok"); }
        catch (e) { err.textContent = errorText(e); }
      },
    }, "保存")],
  });
}

// ============================================================ login
function loginPage() {
  let mode = "login";
  const user = h("input.input", { autocomplete: "username", placeholder: "用户名" });
  const pass = h("input.input", { type: "password", autocomplete: "current-password", placeholder: "密码" });
  const pass2 = h("input.input", { type: "password", autocomplete: "new-password", placeholder: "再输入一次密码" });
  const invite = h("input.input", { placeholder: "邀请码" });
  const err = h("div.form-error");
  const btn = h("button.btn.primary.lg.block", { type: "submit" });
  const title = h("h2"), sub = h("p.sub"), sw = h("div.auth-switch");
  const f2 = h("div.field", pass2), fi = h("div.field", invite);
  const form = h("form.auth-card", { onsubmit: submit }, title, sub, h("div.field", user), h("div.field", pass), f2, fi, err, btn, sw);
  function render() {
    const reg = mode === "register";
    title.textContent = reg ? "创建账号" : "欢迎回来";
    sub.textContent = reg ? "注册后即可用视频给角色做动画" : "登录后继续制作你的角色动画";
    btn.textContent = reg ? "注册并登录" : "登录";
    f2.hidden = !reg;
    fi.hidden = !(reg && S.config.invite_required);
    pass.autocomplete = reg ? "new-password" : "current-password";
    err.textContent = "";
    clear(sw, S.config.signup
      ? [reg ? "已经有账号？" : "还没有账号？", h("button", { type: "button", onclick: () => { mode = reg ? "login" : "register"; render(); } }, reg ? "去登录" : "注册一个")]
      : "需要账号请联系管理员开通");
  }
  async function submit(e) {
    e.preventDefault();
    err.textContent = "";
    if (mode === "register" && pass.value !== pass2.value) { err.textContent = "两次输入的密码不一致"; return; }
    btn.disabled = true;
    try {
      const r = await api(`/api/auth/${mode}`, { method: "POST", body: { username: user.value.trim(), password: pass.value, invite: invite.value.trim() } });
      S.user = r.user;
      route();
    } catch (ex) { err.textContent = errorText(ex); }
    finally { btn.disabled = false; }
  }
  render();
  clear(app, h("div.auth",
    h("section.auth-hero",
      h("div.brand", { style: { position: "absolute", top: "40px", left: "56px", fontSize: "18px" } }, h("span.brand-mark", { html: ICON.logo }), "女娲-动作工坊"),
      h("h1", "一段视频，", h("br"), "让你的角色", h("em", "动起来")),
      h("p", "选择角色、下载参考图，用任意 AI 视频工具生成一段双视角视频，上传后一键得到可用于游戏引擎的骨骼动画。"),
      h("div.auth-steps", ["选角色", "下参考图", "传视频", "导出动画"].map((s, i) => h("div", h("b", i + 1), s)))),
    h("section.auth-card-wrap", form)));
  setTimeout(() => user.focus(), 30);
}

// ============================================================ library
function libraryPage(main) {
  let scope = "all", chars = [], query = "", timer = 0, alive = true;
  const grid = h("div.grid");
  const search = h("input.input.search", { placeholder: "搜索角色", oninput: () => { query = search.value.trim(); draw(); } });
  const seg = h("div.seg");
  const setScope = (s) => { scope = s; load(); };
  const head = h("div.page-pad",
    h("div.hero-row", h("div", h("h1", "选择一个角色，开始做动作"), h("p", "角色库里的角色都可以直接使用；也可以上传你自己的 FBX 角色。")),
      h("button.btn.primary.lg", { onclick: () => uploadCharacter(() => { scope = "mine"; load(); }) }, h("span", { html: ICON.upload }), "上传角色")),
    h("div.how",
      howStep(1, "选角色", "从角色库选一个，或上传带骨骼的 FBX"),
      howStep(2, "下载参考图", "把参考图和提示词交给 AI 视频工具"),
      howStep(3, "上传视频", "上传生成好的视频，点“生成动画”"),
      howStep(4, "调整并导出", "预览效果、标记关键帧，导出 FBX")),
    h("div.toolbar", seg, h("span", { style: { flex: 1 } }), search),
    grid);
  main.append(head);
  async function load() {
    clear(seg, [["all", "全部角色"], ["mine", "我上传的"]].map(([k, l]) => h("button", { "aria-pressed": String(k === scope), onclick: () => setScope(k) }, l)));
    try { chars = await api(`/api/characters?scope=${scope}`); }
    catch (e) { toast(errorText(e), "err"); chars = []; }
    if (!alive) return;
    draw();
    clearTimeout(timer);
    if (chars.some((c) => ["preparing", "pending_review"].includes(c.status))) timer = setTimeout(load, 8000);
  }
  function draw() {
    const list = chars.filter((c) => !query || c.name.toLowerCase().includes(query.toLowerCase()));
    const cards = list.map((c) => charCard(c, () => openCharacter(c.id, load)));
    if (scope === "mine") cards.unshift(h("button.card.add", { onclick: () => uploadCharacter(load) }, h("span", { html: ICON.plus }), h("b", "上传新角色"), h("small", "FBX，带骨骼绑定")));
    if (!cards.length) clear(grid, h("div.empty", { style: { gridColumn: "1/-1" } }, h("h3", query ? "没有找到匹配的角色" : "角色库还是空的"), h("p", "可以先上传一个自己的角色。")));
    else clear(grid, cards);
  }
  load();
  return () => { alive = false; clearTimeout(timer); };
}
function howStep(n, t, d) { return h("div.how-step", h("span.num", n), h("div", h("h4", t), h("p", d))); }

function charThumb(c) {
  return c.thumb ? h("img", { src: c.thumb, alt: "", loading: "lazy" }) : h("span.ph", { html: ICON.monster });
}
function charCard(c, onclick) {
  return h("button.card", { onclick },
    c.status !== "ready" && h("span.corner", statusBadge(c.status, c.progress ? `${c.status_text} ${c.status === "preparing" ? c.progress.percent + "%" : ""}` : c.status_text)),
    h("div.thumb", charThumb(c)),
    h("div.meta", h("h3", c.name), h("div.row", h("span", c.mine ? "我上传的" : "角色库"), c.status === "ready" && h("span", { style: { color: "var(--yellow)" } }, "开始制作 →"))));
}

async function openCharacter(id, refresh) {
  let c;
  try { c = await api(`/api/characters/${id}`); } catch (e) { toast(errorText(e), "err"); return; }
  const viewerBox = h("div.viewer");
  const side = h("div.side");
  const body = h("div.char-detail", viewerBox, side);
  let viewer = null;
  const m = modal({ wide: true, body: h("div", { class: "modal-body", style: { padding: 0 } }, body), onClose: () => viewer?.dispose() });
  m.box.prepend(h("div", { style: { position: "absolute", right: "12px", top: "12px", zIndex: 5 } }, h("button.icon-btn", { "aria-label": "关闭", html: ICON.x, onclick: () => m.close() })));
  m.box.style.position = "relative";
  // A freshly uploaded model is first loaded on the server exactly as training will load it.
  const inspecting = c.inspect && ["waiting", "running"].includes(c.inspect.status);
  let orientTool = null;
  if (inspecting) {
    viewerBox.append(h("div.v-state", h("div", h("div.spinner"), "正在读取模型（约半分钟），读取完成后可以预览和调整朝向…")));
    const t = setInterval(async () => {
      if (!viewerBox.isConnected) { clearInterval(t); return; }
      try { const n = await api(`/api/characters/${id}`); if (!["waiting", "running"].includes(n.inspect?.status)) { clearInterval(t); m.close(); openCharacter(id, refresh); } } catch { clearInterval(t); }
    }, 3000);
  } else if (c.preview) {
    viewer = createViewer(viewerBox, { fallbackImage: c.has_reference ? `/api/characters/${c.id}/reference` : c.thumb });
    const loading = viewer.load(c.preview.url, c.preview.kind).then((r) => { if (r && !r.hasAnimation) viewer.message(""); return r; });
    loading.catch(() => {});
    if (c.orient_editable && c.preview.inspected) orientTool = orientationTool(c, viewer, loading);
  } else {
    viewerBox.append(h("div.v-state", c.inspect?.status === "failed" ? "模型无法读取" : "这个角色暂时没有可预览的模型"));
  }
  const actions = [];
  if (c.status === "ready") {
    actions.push(h("button.btn.primary.lg.block", { onclick: () => { m.close(); location.hash = `#/bench/${c.id}`; } }, "开始制作动作"));
    if (c.has_reference) actions.push(h("a.btn.ghost.block", { href: `/api/characters/${c.id}/reference?download=1` }, h("span", { html: ICON.download }), "下载参考图"));
  }
  const info = [];
  const inspectFailed = c.inspect?.status === "failed";
  if (inspectFailed) info.push(h("div.note.bad", c.inspect.error || "模型无法读取"));
  if (orientTool) info.push(orientTool.panel);
  const submit = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      if (orientTool) {
        if (!await orientTool.confirmIfNeeded()) { btn.disabled = false; return; }
        await orientTool.save();
      }
      await api(`/api/characters/${c.id}/prepare`, { method: "POST" });
      toast("已提交", "ok"); m.close(); refresh?.();
    } catch (ex) { toast(errorText(ex), "err"); btn.disabled = false; }
  };
  if (c.status === "uploaded") {
    info.push(h("div.note", "确认角色站立、正面朝向黄色箭头后，提交制作。系统需要先“认识”这个角色（大约 30–60 分钟），之后才能用视频给它做动作。"));
    if (!inspectFailed && !inspecting) actions.push(h("button.btn.primary.lg.block", { onclick: submit }, "朝向没问题，提交制作"));
  }
  if (c.status === "pending_review") info.push(h("div.note.warn", "已提交，等待管理员审核后开始制作。"));
  if (c.status === "preparing") info.push(h("div.note", `正在制作：${c.progress?.text || ""}`), h("div.progress", h("i", { style: { width: (c.progress?.percent || 2) + "%" } })), h("p", { style: { color: "var(--dim)", fontSize: "12px" } }, "制作完成后会出现在“角色库”，可以先关闭这个窗口。"));
  if (c.status === "failed" && !inspectFailed) {
    info.push(h("div.note.bad", c.error || "制作失败"));
    actions.push(h("button.btn.ghost.block", { onclick: submit }, "调整后重新提交"));
  }
  if (c.mine && ["uploaded", "failed"].includes(c.status)) {
    actions.push(h("button.btn.danger.block", { onclick: async () => {
      if (!await confirmBox("删除角色", `确定删除“${c.name}”吗？上传的文件也会删除。`, { ok: "删除", danger: true })) return;
      try { await api(`/api/characters/${c.id}`, { method: "DELETE" }); m.close(); refresh?.(); toast("已删除", "ok"); } catch (ex) { toast(errorText(ex), "err"); }
    } }, "删除"));
  }
  clear(side,
    h("div.row", statusBadge(c.status, c.status_text), c.mine && h("span.badge", "我上传的")),
    h("h2", c.name),
    c.status === "ready" && h("p", { style: { color: "var(--muted)" } }, c.works ? `你已经用它做了 ${c.works} 个动作。` : "还没有用它做过动作，试试吧。"),
    info,
    h("div", { style: { flex: 1 } }),
    actions);
}

// ------------------------------------------------------------ orientation fix (90-degree steps)
// Matrices are row-major 3x3 integer rotations in three.js coordinates (+Y up, +Z = towards the front camera).
const I3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];
function mul3(a, b) {
  const r = [];
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) r.push(a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j]);
  return r;
}
function rot3(axis, q) {
  const c = [1, 0, -1, 0][((q % 4) + 4) % 4], s = [0, 1, 0, -1][((q % 4) + 4) % 4];
  if (axis === "x") return [1, 0, 0, 0, c, -s, 0, s, c];
  if (axis === "y") return [c, 0, s, 0, 1, 0, -s, 0, c];
  return [c, -s, 0, s, c, 0, 0, 0, 1];
}
function orientationTool(c, viewer, loading) {
  let M = c.orient?.matrix?.length === 9 ? [...c.orient.matrix] : [...I3];
  let changed = false;
  const status = h("div.orient-status");
  const apply = (R) => {
    M = mul3(R, M);           // rotate in world space (as the user sees it)
    changed = true;
    viewer.setOrientation(M);
    viewer.viewFront();
    updateStatus();
    panel.dataset.matrix = M.join(",");
  };
  function updateStatus() {
    const s = viewer.size();
    if (!s) { clear(status); return; }
    const lying = s[1] < 0.75 * Math.max(s[0], s[2]);
    // Height vs width is only a hint (a wide creature lying on its side can look "tall enough"), so the
    // positive case asks the user to check rather than claiming the model is upright.
    clear(status, lying
      ? h("div.note.warn", "模型看起来是躺着的（比站立时矮很多）。用“前后翻转”或“左右侧倒”把它立起来。")
      : h("div.note", "请确认三点：", h("b", "头朝上"), "、", h("b", "脚踩在网格地面上"), "、", h("b", "脸朝黄色箭头"), "。都对了再提交。"));
  }
  const btn = (label, title, icon, R) => h("button.orient-btn", { title, onclick: () => apply(R) }, h("span", { html: icon }), label);
  const skel = h("label.orient-check", h("input", { type: "checkbox", onchange: (e) => { if (!viewer.showSkeleton(e.target.checked) && e.target.checked) toast("这个预览里没有找到骨骼"); } }), "显示骨骼");
  const panel = h("div.orient-panel",
    h("div.orient-title", h("b", "调整朝向"), h("button", { class: "orient-reset", onclick: () => { M = [...I3]; changed = true; viewer.setOrientation(M); viewer.viewFront(); updateStatus(); } }, "还原")),
    h("p.orient-hint", "让角色", h("b", "站在地面上"), "，", h("b", "脸朝黄色箭头"), "（正面相机）。每次转 90°。"),
    h("div.orient-grid",
      btn("前后翻转", "绕左右方向轴转 90°（躺着/趴着时用）", ICON.flipX, rot3("x", -1)),
      btn("左右侧倒", "绕前后方向轴转 90°（侧躺时用）", ICON.flipZ, rot3("z", 1)),
      btn("水平转身", "绕竖直轴转 90°（正面不对时用）", ICON.turnY, rot3("y", 1)),
      btn("上下颠倒", "转 180°（头朝下时用）", ICON.upside, rot3("x", 2))),
    h("div.row", skel, h("button.btn.sm.ghost", { style: { marginLeft: "auto" }, onclick: () => viewer.viewFront() }, "正面视角")),
    status);
  loading.then(() => {
    viewer.setOrientation(M);
    viewer.showFront(true);
    viewer.viewFront();
    updateStatus();
  }).catch(() => clear(panel, h("div.note.warn", "3D 预览没有加载出来，暂时无法调整朝向。可以先提交，制作出来的参考图如果是躺着的，再联系管理员。")));
  return {
    panel,
    async confirmIfNeeded() {
      const s = viewer.size();
      if (s && s[1] < 0.75 * Math.max(s[0], s[2])) {
        return confirmBox("模型看起来是躺着的", "按现在的朝向制作，参考图里的角色会是躺着的，生成的动画也会不对。确定要这样提交吗？", { ok: "仍然提交" });
      }
      return true;
    },
    async save() {
      if (!changed && !c.orient) return;
      await api(`/api/characters/${c.id}/orient`, { method: "PUT", body: { matrix: M } });
    },
  };
}

function uploadCharacter(done) {
  let files = [];
  const name = h("input.input", { placeholder: "例如：石头巨人", maxlength: 40 });
  const list = h("div.file-list");
  const err = h("div.form-error");
  const bar = h("div.progress", { hidden: true }, h("i", { style: { width: "0%" } }));
  const zone = dropZone({ title: "点击或拖入 FBX 模型（可以连同贴图一起选）", hint: `模型需要带骨骼绑定 · 单个模型不超过 ${S.config.max_model_mb} MB`, accept: ".fbx,.glb,.png,.jpg,.jpeg,.tga,.tif,.tiff,.bmp,.dds", multiple: true,
    onFiles: (f) => {
      files = f;
      clear(list, f.map((x) => h("div", h("span", x.name), h("span", fmtSize(x.size)))));
      const m = f.find((x) => /\.(fbx|glb)$/i.test(x.name));
      if (m && !name.value) name.value = m.name.replace(/\.[^.]+$/, "").slice(0, 40);
    } });
  const btn = h("button.btn.primary", { onclick: send }, "上传");
  const m = modal({
    title: "上传角色",
    body: [zone, list, h("div.field", h("label", "角色名"), name),
      h("div.note", "上传后需要提交制作：系统会花 30–60 分钟学习这个角色的外观，完成后就能用视频给它做动作。"), bar, err],
    foot: [h("button.btn.ghost", { onclick: () => m.close() }, "取消"), btn],
  });
  async function send() {
    err.textContent = "";
    if (!files.some((x) => /\.(fbx|glb)$/i.test(x.name))) { err.textContent = "请选择一个 FBX 模型文件"; return; }
    if (!name.value.trim()) { err.textContent = "请给角色起个名字"; return; }
    const form = new FormData();
    form.append("name", name.value.trim());
    for (const f of files) form.append("files", f, f.name);
    btn.disabled = true; bar.hidden = false;
    try {
      const c = await upload("/api/characters", form, (p) => { bar.firstChild.style.width = Math.round(p * 100) + "%"; });
      m.close();
      toast("上传成功", "ok");
      done?.();
      openCharacter(c.id, done);
    } catch (e) { err.textContent = errorText(e); btn.disabled = false; bar.hidden = true; }
  }
}

// ============================================================ my works
function worksPage(main) {
  let alive = true, timer = 0;
  const grid = h("div.grid");
  main.append(h("div.page-pad", h("div.hero-row", h("div", h("h1", "我的作品"), h("p", "所有生成过的动作都在这里，点开可以继续调整和导出。"))), grid));
  async function load() {
    let works = [];
    try { works = await api("/api/works"); } catch (e) { toast(errorText(e), "err"); }
    if (!alive) return;
    if (!works.length) clear(grid, h("div.empty", { style: { gridColumn: "1/-1" } }, h("h3", "还没有作品"), h("p", "去角色库选一个角色，开始做第一个动作吧。"), h("a.btn.primary", { href: "#/", style: { marginTop: "16px" } }, "去角色库")));
    else clear(grid, works.map((w) => h("button.card", { onclick: () => { location.hash = `#/bench/${w.character.id}/${w.id}`; } },
      h("span.corner", statusBadge(w.status, w.progress ? `${w.status_text} ${w.progress.percent}%` : w.status_text)),
      h("div.thumb", w.thumb ? h("img", { src: w.thumb, alt: "" }) : h("span.ph", { html: ICON.film })),
      h("div.meta", h("h3", w.title), h("div.row", h("span", w.character.name), h("span", fmtTime(w.created)))))));
    clearTimeout(timer);
    if (works.some((w) => ["waiting", "queued", "running"].includes(w.status))) timer = setTimeout(load, 5000);
  }
  load();
  return () => { alive = false; clearTimeout(timer); };
}

// ============================================================ admin
function adminPage(main) {
  let alive = true;
  const box = h("div.page-pad");
  main.append(box);
  async function load() {
    let d;
    try { d = await api("/api/admin/overview"); } catch (e) { toast(errorText(e), "err"); return; }
    if (!alive) return;
    const act = (fn) => async (e) => { e.target.disabled = true; try { await fn(); await load(); } catch (ex) { toast(errorText(ex), "err"); e.target.disabled = false; } };
    clear(box,
      h("div.hero-row", h("div", h("h1", "管理"), h("p", `任务引擎：${d.engine === "fake" ? "本地模拟" : "训练服务器 GPU 队列"}`)),
        h("button.btn.primary", { onclick: addUser }, "新建用户")),
      h("h3.section-title", "角色"),
      h("div.panel-box", h("table.table",
        h("thead", h("tr", ["ID", "名称", "状态", "来源", "内部项目", "公开", "操作"].map((x) => h("th", x)))),
        h("tbody", d.characters.map((c) => h("tr",
          h("td", c.id), h("td", h("input.input", { value: c.name, style: { height: "30px", width: "160px" }, onchange: (e) => api(`/api/admin/characters/${c.id}`, { method: "PATCH", body: { name: e.target.value } }).then(() => toast("已改名", "ok")).catch((ex) => toast(errorText(ex), "err")) })),
          h("td", statusBadge(c.status, c.progress ? `${c.status_text} ${c.progress.percent}%` : c.status_text)),
          h("td", c.owner), h("td.mono", c.project || "-"),
          h("td", c.status === "ready" ? h("input", { type: "checkbox", checked: c.published, onchange: act(() => api(`/api/admin/characters/${c.id}`, { method: "PATCH", body: { published: !c.published } })) }) : "-"),
          h("td", h("div.row",
            ["pending_review", "failed", "uploaded"].includes(c.status) && h("button.btn.sm.primary", { onclick: act(() => api(`/api/admin/characters/${c.id}`, { method: "PATCH", body: { approve: true } })) }, c.status === "failed" ? "重新制作" : "开始制作"),
            c.status === "pending_review" && h("button.btn.sm", { onclick: act(() => api(`/api/admin/characters/${c.id}`, { method: "PATCH", body: { reject: true } })) }, "退回"),
            c.status === "failed" && c.error_raw && h("button.btn.sm.ghost", { onclick: () => modal({ title: "失败原因", wide: true, body: h("pre.log", c.error_raw) }) }, "原因")))))))),
      h("h3.section-title", "用户"),
      h("div.panel-box", h("table.table",
        h("thead", h("tr", ["ID", "用户名", "身份", "作品", "注册", "最近登录", "操作"].map((x) => h("th", x)))),
        h("tbody", d.users.map((u) => h("tr",
          h("td", u.id), h("td", u.username, u.disabled ? h("span.badge.bad", { style: { marginLeft: "6px" } }, "已停用") : null),
          h("td", u.role === "admin" ? "管理员" : "用户"), h("td", u.works), h("td", fmtTime(u.created)), h("td", fmtTime(u.last_login)),
          h("td", h("div.row",
            h("button.btn.sm", { onclick: act(() => api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { disabled: !u.disabled } })) }, u.disabled ? "启用" : "停用"),
            h("button.btn.sm", { onclick: act(() => api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { role: u.role === "admin" ? "user" : "admin" } })) }, u.role === "admin" ? "设为用户" : "设为管理员"),
            h("button.btn.sm.ghost", { onclick: () => resetPassword(u) }, "重置密码")))))))),
      h("h3.section-title", "最近作品"),
      h("div.panel-box", h("table.table",
        h("thead", h("tr", ["ID", "作品", "用户", "角色", "状态", "时间", "内部结果", ""].map((x) => h("th", x)))),
        h("tbody", d.works.map((w) => h("tr",
          h("td", w.id), h("td", w.title), h("td", w.owner), h("td", w.character.name),
          h("td", statusBadge(w.status, w.progress ? `${w.status_text} ${w.progress.percent}%` : w.status_text)),
          h("td", fmtTime(w.created)), h("td.mono", `${w.project}/${w.result}`),
          h("td", h("div.row",
            h("a.btn.sm", { href: `#/bench/${w.character.id}/${w.id}` }, "打开"),
            w.job && h("button.btn.sm.ghost", { onclick: async () => { const r = await api(`/api/admin/works/${w.id}/log`); modal({ title: "任务日志", wide: true, body: h("pre.log", r.log || "（无）") }); } }, "日志")))))))));
  }
  function addUser() {
    const u = h("input.input", { placeholder: "用户名" }), p = h("input.input", { placeholder: "初始密码（至少 6 位）" }), err = h("div.form-error");
    const m = modal({ title: "新建用户", body: [h("div.field", h("label", "用户名"), u), h("div.field", h("label", "初始密码"), p), err],
      foot: [h("button.btn.ghost", { onclick: () => m.close() }, "取消"), h("button.btn.primary", { onclick: async () => {
        try { await api("/api/admin/users", { method: "POST", body: { username: u.value.trim(), password: p.value } }); m.close(); toast("已创建", "ok"); load(); } catch (e) { err.textContent = errorText(e); }
      } }, "创建")] });
  }
  function resetPassword(user) {
    const p = h("input.input", { placeholder: "新密码（至少 6 位）" }), err = h("div.form-error");
    const m = modal({ title: `重置 ${user.username} 的密码`, body: [p, err],
      foot: [h("button.btn.ghost", { onclick: () => m.close() }, "取消"), h("button.btn.primary", { onclick: async () => {
        try { await api(`/api/admin/users/${user.id}`, { method: "PATCH", body: { password: p.value } }); m.close(); toast("已重置", "ok"); } catch (e) { err.textContent = errorText(e); }
      } }, "确定")] });
  }
  load();
  return () => { alive = false; };
}

boot();
