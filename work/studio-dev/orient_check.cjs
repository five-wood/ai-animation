// Orientation tool in the character dialog (local fake server on :8711).
// Uploads a model named "lying …" (fake inspect reports flat_in_z), rotates it in the UI, checks the saved --up/--facing.
// node work/studio-dev/orient_check.cjs
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.BASE || "http://127.0.0.1:8711";
const OUT = path.join(__dirname, "shots");
const FBX = path.resolve(__dirname, "../../11101_nogun_exam.fbx");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const profile = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "orient-"));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9339", "--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  const errors = [];
  try {
    let t; for (let i = 0; i < 50; i++) { try { t = await (await fetch("http://127.0.0.1:9339/json/list")).json(); if (t.some((x) => x.type === "page")) break; } catch {} await sleep(200); }
    const ws = new WebSocket(t.find((x) => x.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0; const pend = new Map();
    ws.addEventListener("message", (m) => { const msg = JSON.parse(m.data); if (msg.id && pend.has(msg.id)) { pend.get(msg.id)(msg); pend.delete(msg.id); }
      if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text); });
    const cdp = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (e) => { const r = await cdp("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description); return r.result?.value; };
    const waitFor = async (e, ms = 30000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(e).catch(() => false)) return; await sleep(250); } throw new Error("timeout " + e + "\n" + await ev("document.body.innerText.slice(0, 600)")); };
    const shot = async (n) => { const r = await cdp("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, n), Buffer.from(r.data, "base64")); console.log("shot", n); };
    await cdp("Runtime.enable"); await cdp("Page.enable"); await cdp("DOM.enable");
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
    await cdp("Page.navigate", { url: BASE + "/" });
    await waitFor("document.querySelector('.auth-card')");
    await ev(`fetch('/api/auth/register', { method: 'POST', headers: { 'X-Studio': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'or' + Date.now().toString().slice(-6), password: 'secret1' }) }).then(r => r.status)`);
    await cdp("Page.reload");
    await waitFor("document.querySelector('.hero-row .btn.primary')");
    // upload via the dialog
    await ev("(() => { window.__oc = HTMLInputElement.prototype.click; HTMLInputElement.prototype.click = function () { if (this.type === 'file') { document.body.append(this); this.id = 'pickme'; return; } return window.__oc.call(this); }; document.querySelector('.hero-row .btn.primary').click(); return 1; })()");
    await waitFor("document.querySelector('.modal .drop')");
    await ev("document.querySelector('.modal .drop').click()");
    await waitFor("document.querySelector('#pickme')");
    const doc = await cdp("DOM.getDocument", { depth: -1 });
    const q = await cdp("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#pickme" });
    await cdp("DOM.setFileInputFiles", { nodeId: q.nodeId, files: [FBX] });
    await ev("(() => { const i = document.querySelector('.modal input.input'); i.value = 'lying 躺着的怪物'; return 1; })()");
    await ev("[...document.querySelectorAll('.modal .btn.primary')].find(b => b.textContent === '上传').click()");
    // dialog reopens with "reading model" then the orientation tool
    await waitFor("document.querySelector('.orient-panel')", 90000);
    await waitFor("document.querySelector('.orient-status .note')", 30000);
    await sleep(1200);
    await shot("orient_1_initial.png");
    const s0 = await ev("document.querySelector('.orient-status').textContent");
    console.log("status at start:", s0);
    // The fixture GLB stands upright; tip it over once so it lies, then fix it back with the tool.
    await ev("[...document.querySelectorAll('.orient-btn')].find(b => b.textContent.includes('前后翻转')).click()");
    await sleep(800);
    const s1 = await ev("document.querySelector('.orient-status').textContent");
    console.log("after one flip:", s1);
    assert.match(s1, /躺着/, "lying detected after a 90-degree flip");
    await shot("orient_2_lying.png");
    // show skeleton
    await ev("document.querySelector('.orient-check input').click()");
    await sleep(500);
    console.log("skeleton:", await ev("JSON.stringify({ checked: document.querySelector('.orient-check input').checked, toast: document.getElementById('toasts').innerText })"));
    await shot("orient_3_skeleton.png");
    // flip three more times -> back upright (4 x 90 = 360) then turn around twice (180) to face the other way
    for (let i = 0; i < 3; i++) { await ev("[...document.querySelectorAll('.orient-btn')].find(b => b.textContent.includes('前后翻转')).click()"); await sleep(250); }
    const s2 = await ev("document.querySelector('.orient-status').textContent");
    assert.match(s2, /站着/, "upright again");
    for (let i = 0; i < 2; i++) { await ev("[...document.querySelectorAll('.orient-btn')].find(b => b.textContent.includes('水平转身')).click()"); await sleep(250); }
    await sleep(600);
    await shot("orient_4_turned.png");
    // submit -> saves orientation (+Z up, facing +Y) and asks for review
    await ev("[...document.querySelectorAll('.char-detail .btn.primary')].find(b => b.textContent.includes('提交制作')).click()");
    await sleep(1500);
    const mine = await ev("fetch('/api/characters?scope=mine').then(r => r.json())");
    const c = await ev(`fetch('/api/characters/${mine[0].id}').then(r => r.json())`);
    console.log("saved:", JSON.stringify(c.orient), c.status);
    assert.deepEqual([c.orient.up, c.orient.facing], ["+Z", "+Y"]);
    assert.equal(c.status, "pending_review");
    // Lying-down submit asks for confirmation: upload a second model, flip once, try to submit
    console.log("page exceptions:", errors);
    assert.deepEqual(errors, []);
    console.log("ORIENT CHECK PASSED");
  } finally { chrome.kill(); }
})().catch((e) => { console.error("FAILED", e.message); process.exit(1); });
