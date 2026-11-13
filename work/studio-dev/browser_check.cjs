// Drive the local fake-engine studio (run_local.ps1 on :8711) in headless Chrome over CDP and take screenshots.
// node work/studio-dev/browser_check.cjs
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.BASE || "http://127.0.0.1:8711";
const VIDEO = path.resolve(__dirname, "../../ladingwu.mp4");
const OUT = path.join(__dirname, "shots");
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "studio-cdp-"));
  const chrome = spawn(CHROME, ["--headless=new", "--no-first-run", "--hide-scrollbars", "--remote-debugging-port=9334", "--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist",
    `--user-data-dir=${profile}`, "--autoplay-policy=no-user-gesture-required", "about:blank"], { stdio: "ignore" });
  let ws;
  const errors = [];
  try {
    let targets;
    for (let i = 0; i < 50; i++) { try { targets = await (await fetch("http://127.0.0.1:9334/json/list")).json(); if (targets.some((t) => t.type === "page")) break; } catch { /* starting */ } await sleep(200); }
    const page = targets.find((t) => t.type === "page");
    ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0;
    const pending = new Map();
    ws.addEventListener("message", (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
      if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") errors.push(msg.params.args.map((a) => a.value || a.description).join(" "));
      if (process.env.NETLOG && msg.method === "Network.requestWillBeSent") console.log("  -> req", msg.params.request.method, msg.params.request.url.slice(0, 90));
      if (process.env.NETLOG && msg.method === "Network.loadingFailed") console.log("  !! fail", msg.params.errorText, msg.params.blockedReason || "");
      if (process.env.NETLOG && msg.method === "Network.responseReceived") console.log("  <- res", msg.params.response.status, msg.params.response.url.slice(0, 90));
    });
    const cdp = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, (msg) => msg.error ? reject(new Error(`${method}: ${msg.error.message}`)) : resolve(msg.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const evaluate = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };
    const waitFor = async (expr, ms = 20000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await evaluate(expr).catch(() => false)) return true; await sleep(200); }
      const dump = await evaluate("'PROGRESS=' + (document.querySelector('.step-card .progress i')?.style.width) + ' ' + (document.querySelector('.panel.left')?.innerText || '').slice(-300) + ' | TOASTS: ' + document.getElementById('toasts').innerText + ' | STAGE: ' + (document.querySelector('.central')?.innerText || '')").catch((e) => String(e));
      await shot("zz_timeout.png").catch(() => {});
      throw new Error(`timeout: ${expr}\n${dump}\nerrors: ${errors.join('\n')}`); };
    const shot = async (name, w = 1600, h = 900) => {
      await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
      await sleep(700);
      const overflow = await evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
      const r = await cdp("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64"));
      console.log("shot", name, "overflow", overflow);
      return overflow;
    };
    const click = (sel, text) => evaluate(`(() => { const els = [...document.querySelectorAll(${JSON.stringify(sel)})]; const el = ${text ? `els.find(e => e.textContent.includes(${JSON.stringify(text)}))` : "els[0]"}; if (!el) throw new Error('no element ${sel} ${text || ""}'); el.click(); return true; })()`);
    const setFile = async (sel, file) => {
      const doc = await cdp("DOM.getDocument", { depth: -1 });
      const q = await cdp("DOM.querySelector", { nodeId: doc.root.nodeId, selector: sel });
      await cdp("DOM.setFileInputFiles", { nodeId: q.nodeId, files: [file] });
    };
    await cdp("Runtime.enable"); await cdp("Page.enable"); await cdp("DOM.enable");
    if (process.env.NETLOG) await cdp("Network.enable");
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
    await cdp("Page.navigate", { url: BASE + "/" });
    await waitFor("document.querySelector('.auth-card')");
    await shot("01_login.png");
    // register
    await click(".auth-switch button");
    const user = "user" + Date.now().toString().slice(-5);
    await evaluate(`(() => { const i = [...document.querySelectorAll('.auth-card input')]; const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input')); }; set(i[0], '${user}'); set(i[1], 'secret1'); set(i[2], 'secret1'); return true; })()`);
    await click(".auth-card button[type=submit]");
    await waitFor("document.querySelectorAll('.grid .card').length >= 2");
    await sleep(800);
    await shot("02_library.png");
    // open character detail (FBX preview)
    await click(".grid .card", "11101");
    await waitFor("document.querySelector('.char-detail canvas')");
    await waitFor("!document.querySelector('.char-detail .v-state')?.textContent.includes('正在加载')", 60000);
    await sleep(1500);
    await shot("03_character.png");
    const viewerMsg = await evaluate("document.querySelector('.char-detail .v-state').textContent");
    assert.equal(viewerMsg, "", "fbx loaded");
    await click(".char-detail .btn.primary", "开始制作");
    await waitFor("document.querySelector('.bench') && document.querySelector('.step-card')");
    await sleep(1500);
    await shot("04_bench_empty.png");
    // upload video via the drop zone's hidden input
    await evaluate("(() => { window.__origClick = HTMLInputElement.prototype.click; HTMLInputElement.prototype.click = function () { if (this.type === 'file') { document.body.append(this); this.id = 'pickme'; return; } return window.__origClick.call(this); }; return 1; })()");
    await click(".step-card .drop");
    await waitFor("document.querySelector('#pickme')");
    await setFile("#pickme", VIDEO);
    await waitFor("document.querySelector('.video-chip')", 90000);
    await sleep(800);
    await shot("05_video_uploaded.png");
    assert.equal(await evaluate("document.querySelector('.panel-cta .btn.primary').disabled"), false);
    await click(".panel-cta .btn.primary");
    await waitFor("document.querySelector('.gen-status .pct') || document.querySelector('.timeline:not([hidden])')");
    if (await evaluate("!!document.querySelector('.gen-status .pct')")) await shot("06_generating.png");
    // wait for ready: timeline + glb
    await waitFor("document.querySelector('.timeline:not([hidden]) .tl-track')", 90000);
    await waitFor("document.querySelectorAll('.tl-track .kf').length > 3", 60000);
    await sleep(3000);
    await shot("07_ready_compare.png");
    // keyframes tab, add manual key at frame 60
    await click(".tabs button", "关键帧");
    await evaluate("(() => { document.activeElement?.blur(); return 1; })()");
    const key = async (k, code, vk, shift = false) => { await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, windowsVirtualKeyCode: vk, modifiers: shift ? 8 : 0 }); await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: vk }); await sleep(200); };
    for (let i = 0; i < 3; i++) await key("ArrowRight", "ArrowRight", 39);
    const fr = await evaluate("document.querySelector('.tl-controls .time').textContent");
    console.log("time after 3 steps:", fr);
    assert.match(fr, /第 3 \//);
    const before = await evaluate("document.querySelectorAll('.tl-track .kf').length");
    await key("ArrowRight", "ArrowRight", 39, true);
    const afterJump = await evaluate("document.querySelector('.tl-controls .time').textContent");
    console.log("after shift+right:", afterJump);
    await key("ArrowRight", "ArrowRight", 39);
    await key("k", "KeyK", 75);
    await sleep(300);
    const after = await evaluate("document.querySelectorAll('.tl-track .kf').length");
    assert.equal(after, before + 1, "K adds a key");
    await waitFor("document.querySelector('.panel.right').textContent.includes('已自动保存')", 10000);
    // number key 3 = "发力" on the key under the playhead
    await key("3", "Digit3", 51);
    assert.equal(await evaluate("document.querySelector('.quick-types button.on')?.textContent.replace(/^\\d/, '')"), "发力", "1-6 sets type");
    // undo (type) -> redo
    await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "z", code: "KeyZ", windowsVirtualKeyCode: 90, modifiers: 2 }); await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "z", code: "KeyZ", modifiers: 2 }); await sleep(250);
    assert.notEqual(await evaluate("document.querySelector('.quick-types button.on')?.textContent.replace(/^\\d/, '')"), "发力", "undo type");
    await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "y", code: "KeyY", windowsVirtualKeyCode: 89, modifiers: 2 }); await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "y", code: "KeyY", modifiers: 2 }); await sleep(250);
    assert.equal(await evaluate("document.querySelector('.quick-types button.on')?.textContent.replace(/^\\d/, '')"), "发力", "redo type");
    // double-click an empty spot on the track adds a key there
    const n0 = await evaluate("document.querySelectorAll('.tl-track .kf').length");
    const spot = await evaluate("(() => { const t = document.querySelector('.tl-track').getBoundingClientRect(); const xs = [...document.querySelectorAll('.tl-track .kf')].map(k => k.getBoundingClientRect().x + 7); for (let x = t.x + 30; x < t.right - 30; x += 3) if (xs.every(v => Math.abs(v - x) > 16)) return { x, y: t.y + t.height / 2 }; return null; })()");
    await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: spot.x, y: spot.y, button: "left", clickCount: 1 });
    await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: spot.x, y: spot.y, button: "left", clickCount: 1 });
    await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: spot.x, y: spot.y, button: "left", clickCount: 2 });
    await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: spot.x, y: spot.y, button: "left", clickCount: 2 });
    await sleep(400);
    assert.equal(await evaluate("document.querySelectorAll('.tl-track .kf').length"), n0 + 1, "double-click adds");
    // right-click the selected key deletes it, Ctrl+Z restores it
    const sel = await evaluate("(() => { const k = document.querySelector('.tl-track .kf.sel').getBoundingClientRect(); return { x: k.x + k.width / 2, y: k.y + k.height / 2 }; })()");
    await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: sel.x, y: sel.y, button: "right", clickCount: 1 });
    await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: sel.x, y: sel.y, button: "right", clickCount: 1 });
    await sleep(400);
    assert.equal(await evaluate("document.querySelectorAll('.tl-track .kf').length"), n0, "right-click deletes");
    await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "z", code: "KeyZ", windowsVirtualKeyCode: 90, modifiers: 2 }); await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "z", code: "KeyZ", modifiers: 2 }); await sleep(300);
    assert.equal(await evaluate("document.querySelectorAll('.tl-track .kf').length"), n0 + 1, "undo delete");
    // shortcut sheet
    await key("?", "Slash", 191, true);
    await waitFor("document.querySelector('.shortcut-list')");
    await shot("08b_shortcuts.png");
    await evaluate("document.querySelector('.modal-head .icon-btn').click()");
    // CSV export of the keyframe list (intercept the blob download)
    const csv = await evaluate(`(async () => { let blob; const orig = URL.createObjectURL; URL.createObjectURL = (b) => { blob = b; return orig(b); };
      [...document.querySelectorAll('.panel.right button')].find(b => b.textContent.includes('表格 CSV')).click(); URL.createObjectURL = orig; return await blob.text(); })()`);
    console.log("csv head:", csv.split("\\n").slice(0, 3).join(" | "));
    assert.ok(csv.startsWith("\ufeff帧号,时间(秒),类型") || csv.startsWith("帧号,时间(秒),类型"), "csv header");
    assert.match(csv, /\n12,0\.5,发力,impact,手动,/);
    await waitFor("document.querySelector('.panel.right').textContent.includes('已自动保存')", 10000);
    await shot("08_keyframes.png");
    // drag a manual key on the track
    const box = await evaluate("(() => { const k = document.querySelector('.tl-track .kf.manual').getBoundingClientRect(); return { x: k.x + k.width / 2, y: k.y + k.height / 2 }; })()");
    await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: box.x, y: box.y, button: "left", clickCount: 1 });
    await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: box.x + 40, y: box.y, button: "left" });
    await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: box.x + 80, y: box.y, button: "left" });
    await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: box.x + 80, y: box.y, button: "left", clickCount: 1 });
    await sleep(500);
    const moved = await evaluate("document.querySelector('.kf-detail b')?.textContent");
    console.log("dragged key ->", moved);
    // fit mode
    await click(".stage-top .seg button", "拟合效果");
    await sleep(1500);
    await shot("09_fit.png");
    await click(".stage-top .seg button", "3D 模型");
    await sleep(1500);
    await shot("10_3d.png");
    // export
    await click(".tabs button", "导出");
    await sleep(400);
    await click(".radio-line", "只保留关键帧");
    await sleep(300);
    await shot("11_export.png");
    await click(".panel.right .btn.primary", "导出并下载");
    await waitFor("[...document.querySelectorAll('.list-line a')].length > 0", 30000);
    await shot("12_export_done.png");
    // works page + narrow layout
    await cdp("Page.navigate", { url: BASE + "/#/works" });
    await waitFor("document.querySelectorAll('.grid .card').length >= 1");
    await sleep(800);
    await shot("13_works.png");
    await cdp("Page.navigate", { url: BASE + "/#/" });
    await waitFor("document.querySelectorAll('.grid .card').length >= 1");
    const o1 = await shot("14_library_1280.png", 1280, 800);
    assert.ok(o1 <= 0, "no horizontal overflow at 1280");
    console.log("page errors:", errors);
    assert.deepEqual(errors.filter((e) => !/favicon|ERR_ABORTED|net::/.test(e)), []);
    console.log("BROWSER CHECK PASSED");
  } finally {
    try { ws?.close(); } catch { /* ignore */ }
    chrome.kill();
  }
}
main().catch((e) => { console.error("FAILED", e); process.exit(1); });
