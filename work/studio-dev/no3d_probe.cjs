// With the 3D library (/vendor/*) blocked: the page must still work and show a clear notice in the 3D area.
// node work/studio-dev/no3d_probe.cjs [base]   (fake server on :8711; screenshots in shots/no3d_*.png)
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.argv[2] || "http://127.0.0.1:8711";
const OUT = path.join(__dirname, "shots");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const profile = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "no3d-"));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9336", "--use-angle=d3d11", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  const errors = [];
  try {
    let t; for (let i = 0; i < 50; i++) { try { t = await (await fetch("http://127.0.0.1:9336/json/list")).json(); if (t.some((x) => x.type === "page")) break; } catch {} await sleep(200); }
    const ws = new WebSocket(t.find((x) => x.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0; const pend = new Map();
    ws.addEventListener("message", (m) => { const msg = JSON.parse(m.data); if (msg.id && pend.has(msg.id)) { pend.get(msg.id)(msg); pend.delete(msg.id); }
      if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text); });
    const cdp = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (e) => (await cdp("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true })).result?.value;
    const waitFor = async (e, ms = 30000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(e)) return; await sleep(250); } throw new Error("timeout " + e + "\n" + await ev("document.body.innerText.slice(0, 500)")); };
    const shot = async (n) => { const r = await cdp("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, n), Buffer.from(r.data, "base64")); console.log("shot", n); };
    await cdp("Runtime.enable"); await cdp("Network.enable"); await cdp("Page.enable");
    await cdp("Network.setBlockedURLs", { urls: ["*/vendor/*"] });
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
    await cdp("Page.navigate", { url: BASE + "/" });
    await waitFor("document.querySelector('.auth-card')");
    console.log("login page shown with /vendor blocked");
    // register + library
    await ev(`(async () => { const r = await fetch('/api/auth/register', { method: 'POST', headers: { 'X-Studio': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'n3d' + Date.now().toString().slice(-6), password: 'secret1' }) }); return r.status; })()`);
    await cdp("Page.reload");
    await waitFor("document.querySelectorAll('.grid .card').length >= 1");
    // character dialog
    await ev("[...document.querySelectorAll('.grid .card')].find(c => c.textContent.includes('11101')).click()");
    await waitFor("document.querySelector('.char-detail') && document.querySelector('.char-detail').textContent.includes('3D 预览暂时无法加载')");
    await sleep(500);
    await shot("no3d_character.png");
    assert.ok(await ev("!!document.querySelector('.char-detail .btn.primary')"), "start button still there");
    // bench with an existing work (create one through the API)
    await ev("document.querySelector('.char-detail .btn.primary').click()");
    await waitFor("document.querySelector('.bench') && document.querySelector('.step-card')");
    await waitFor("document.querySelector('.central').textContent.includes('3D 预览暂时无法加载')");
    const wid = await ev(`(async () => {
      const ch = (await (await fetch('/api/characters')).json()).find(c => c.name.includes('11101'));
      const vid = await (await fetch('/static/app.css')).blob();  // any bytes; replaced below by the real upload
      return ch.id; })()`);
    // upload the real video via the drop zone input
    await ev("(() => { HTMLInputElement.prototype.click = function () { if (this.type === 'file') { document.body.append(this); this.id = 'pickme'; } }; return 1; })()");
    await ev("document.querySelector('.step-card .drop').click()");
    await waitFor("document.querySelector('#pickme')");
    const doc = await cdp("DOM.getDocument", { depth: -1 });
    const q = await cdp("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#pickme" });
    await cdp("DOM.setFileInputFiles", { nodeId: q.nodeId, files: [path.resolve(__dirname, "../../ladingwu.mp4")] });
    await waitFor("document.querySelector('.video-chip')", 90000);
    await ev("document.querySelector('.panel-cta .btn.primary').click()");
    await waitFor("document.querySelector('.timeline:not([hidden]) .tl-track')", 120000);
    await waitFor("document.querySelectorAll('.tl-track .kf').length > 3", 60000);
    await sleep(1500);
    await shot("no3d_bench_compare.png");
    assert.ok(await ev("document.querySelector('.central').textContent.includes('3D 预览暂时无法加载')"), "notice in 3D pane");
    // keyboard keyframe add still works, video plays
    await ev("document.activeElement?.blur()");
    await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 }); await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowRight", code: "ArrowRight" });
    const n0 = await ev("document.querySelectorAll('.tl-track .kf').length");
    await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "k", code: "KeyK", windowsVirtualKeyCode: 75 }); await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "k", code: "KeyK" });
    await sleep(300);
    assert.equal(await ev("document.querySelectorAll('.tl-track .kf').length"), n0 + 1, "K adds a key without 3D");
    // 3D mode -> switches to fit video automatically? (only when load fails while in 3D mode); click 3D then check notice
    await ev("[...document.querySelectorAll('.stage-top .seg button')].find(b => b.textContent === '3D 模型').click()");
    await sleep(800);
    await shot("no3d_bench_3dmode.png");
    // export tab still fine
    await ev("[...document.querySelectorAll('.tabs button')].find(b => b.textContent === '导出').click()");
    await sleep(400);
    assert.ok(await ev("!!document.querySelector('.panel.right .btn.primary')"), "export button present");
    // retry button exists
    assert.ok(await ev("[...document.querySelectorAll('.central button')].some(b => b.textContent.includes('重试'))"), "retry button");
    // Case 2: library OK but the model file fails (block only *.glb / asset downloads)
    await cdp("Network.setBlockedURLs", { urls: ["*/media/model.glb*", "*/asset/*"] });
    await cdp("Page.reload");
    await waitFor("document.querySelectorAll('.tl-track .kf').length > 3", 60000);
    await waitFor("document.querySelector('.central').textContent.includes('模型加载失败')", 30000);
    await sleep(500);
    await shot("no3d_model_failed.png");
    console.log("model-file failure: notice shown");
    // Case 3: WebGL unavailable
    await cdp("Network.setBlockedURLs", { urls: [] });
    await cdp("Page.addScriptToEvaluateOnNewDocument", { source: "HTMLCanvasElement.prototype.getContext = function () { return null; };" });
    await cdp("Page.reload");
    await waitFor("document.querySelectorAll('.tl-track .kf').length > 3", 60000);
    await sleep(2500);
    console.log("central text:", (await ev("document.querySelector('.central .viewer')?.innerText")) || "(none)",
      "| canvas:", await ev("!!document.querySelector('.central canvas')"), "| errors:", errors.slice(-3));
    await waitFor("document.querySelector('.central').textContent.includes('无法显示 3D')", 30000);
    await sleep(500);
    await shot("no3d_webgl_off.png");
    console.log("no WebGL: notice shown");
    console.log("page exceptions:", errors);
    assert.deepEqual(errors.filter((e) => !/WebGL|getContext|context/i.test(e)), []);
    console.log("NO-3D CHECK PASSED", wid);
  } finally { chrome.kill(); }
})().catch((e) => { console.error("FAILED", e.message); process.exit(1); });
