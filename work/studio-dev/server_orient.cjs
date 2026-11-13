// Live server: upload a lying FBX, stand it up with the web tool, submit (admin -> pipeline starts right away).
// node work/studio-dev/server_orient.cjs <admin_password> <fbx>
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://10.0.3.117:8700";
const OUT = path.join(__dirname, "shots");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const profile = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "srvor-"));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9340", "--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  const errors = [];
  try {
    let t; for (let i = 0; i < 50; i++) { try { t = await (await fetch("http://127.0.0.1:9340/json/list")).json(); if (t.some((x) => x.type === "page")) break; } catch {} await sleep(200); }
    const ws = new WebSocket(t.find((x) => x.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0; const pend = new Map();
    ws.addEventListener("message", (m) => { const msg = JSON.parse(m.data); if (msg.id && pend.has(msg.id)) { pend.get(msg.id)(msg); pend.delete(msg.id); }
      if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text); });
    const cdp = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (e) => { const r = await cdp("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description); return r.result?.value; };
    const waitFor = async (e, ms = 60000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(e).catch(() => false)) return; await sleep(400); } throw new Error("timeout " + e + "\n" + await ev("document.body.innerText.slice(0, 500)")); };
    const shot = async (n) => { const r = await cdp("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, n), Buffer.from(r.data, "base64")); console.log("shot", n); };
    const clickBtn = (text) => ev(`[...document.querySelectorAll('.orient-btn')].find(b => b.textContent.includes('${text}')).click()`);
    const status = () => ev("document.querySelector('.orient-status').textContent");
    await cdp("Runtime.enable"); await cdp("Page.enable"); await cdp("DOM.enable");
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
    await cdp("Page.navigate", { url: BASE + "/" });
    await waitFor("document.querySelector('.auth-card')");
    await ev(`(() => { const i = [...document.querySelectorAll('.auth-card input')]; i[0].value = 'admin'; i[1].value = ${JSON.stringify(process.argv[2])}; document.querySelector('.auth-card button[type=submit]').click(); return 1; })()`);
    await waitFor("document.querySelector('.hero-row .btn.primary')");
    await ev("(() => { window.__oc = HTMLInputElement.prototype.click; HTMLInputElement.prototype.click = function () { if (this.type === 'file') { document.body.append(this); this.id = 'pickme'; return; } return window.__oc.call(this); }; document.querySelector('.hero-row .btn.primary').click(); return 1; })()");
    await waitFor("document.querySelector('.modal .drop')");
    await ev("document.querySelector('.modal .drop').click()");
    await waitFor("document.querySelector('#pickme')");
    const doc = await cdp("DOM.getDocument", { depth: -1 });
    const q = await cdp("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#pickme" });
    await cdp("DOM.setFileInputFiles", { nodeId: q.nodeId, files: [path.resolve(process.argv[3])] });
    await ev("(() => { const i = document.querySelector('.modal input.input'); i.value = '转正测试-躺着的兔子'; return 1; })()");
    await ev("[...document.querySelectorAll('.modal .btn.primary')].find(b => b.textContent === '上传').click()");
    await waitFor("document.querySelector('.char-detail')", 300000);
    console.log("uploaded; waiting for the server to read the model in Blender...");
    await waitFor("document.querySelector('.orient-status .note')", 300000);
    await sleep(1500);
    await shot("srv_orient_1_uploaded.png");
    console.log("initial:", await status());
    // The test file was made by rotating the upright model with Blender Rz(90)·Rx(-90).
    // Undo it the way a user would: 水平转身 x3 (= -90° about the vertical), then 前后翻转 x3 (= +90° about left-right).
    for (let i = 0; i < 3; i++) { await clickBtn("水平转身"); await sleep(400); }
    await shot("srv_orient_2_turned.png");
    for (let i = 0; i < 3; i++) { await clickBtn("前后翻转"); await sleep(400); }
    console.log("after fixing:", await status());
    await sleep(800);
    await shot("srv_orient_3_standing.png");
    const cid = await ev("fetch('/api/characters?scope=mine').then(r => r.json()).then(l => l.find(c => c.name.includes('转正测试')).id)");
    console.log("character id", cid);
    // Save the orientation exactly as the submit button does (same API), without starting the 35-minute training yet.
    const saved = await ev("(async () => { const m = document.querySelector('.orient-panel').dataset.matrix.split(',').map(Number);"
      + " const r = await fetch('/api/characters/" + cid + "/orient', { method: 'PUT', headers: { 'X-Studio': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ matrix: m }) });"
      + " return JSON.stringify(await r.json()); })()");
    console.log("saved orientation:", saved);
    console.log("exceptions:", errors);
  } finally { chrome.kill(); }
})().catch((e) => { console.error("FAILED", e.message); process.exit(1); });
