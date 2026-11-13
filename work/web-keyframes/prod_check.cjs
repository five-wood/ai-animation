// Read-mostly browser check of the deployed keyframe page (headless Chrome over CDP, Node 24, no packages).
// node work/web-keyframes/prod_check.cjs [base]
// Loads the page, checks candidates/ladder/thumbnails/keyboard, other tabs still render, no JS errors, no overflow.
// Does NOT save: production annotation files are left to the artists.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const assert = require("node:assert/strict");

const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.argv[2] || "http://10.0.3.117:8600";
const OUT = path.join(__dirname, "prod");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "kf-prod-"));
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars", "--remote-debugging-port=9334", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  let ws;
  try {
    let targets;
    for (let i = 0; i < 50; i++) { try { targets = await (await fetch("http://127.0.0.1:9334/json/list")).json(); if (targets.some((t) => t.type === "page")) break; } catch { /* starting */ } await sleep(200); }
    ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0;
    const pending = new Map(), errors = [], requests = [];
    ws.addEventListener("message", (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
      if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") errors.push(msg.params.args.map((a) => a.value || a.description).join(" "));
      if (msg.method === "Network.requestWillBeSent" && msg.params.request.method !== "GET") requests.push(`${msg.params.request.method} ${msg.params.request.url}`);
      if (msg.method === "Network.responseReceived" && msg.params.response.status >= 400 && !msg.params.response.url.endsWith("/favicon.ico")) errors.push(`HTTP ${msg.params.response.status} ${msg.params.response.url}`);
    });
    const cdp = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, (msg) => msg.error ? reject(new Error(`${method}: ${msg.error.message}`)) : resolve(msg.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const evaluate = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };
    const waitFor = async (expr, ms = 20000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await evaluate(expr).catch(() => false)) return; await sleep(200); } throw new Error(`timeout: ${expr}`); };
    const key = async (k, shift = false) => { await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: k, code: k, modifiers: shift ? 8 : 0, windowsVirtualKeyCode: { ArrowRight: 39, ArrowLeft: 37 }[k] }); await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: k, modifiers: shift ? 8 : 0 }); await sleep(250); };
    await cdp("Runtime.enable"); await cdp("Page.enable"); await cdp("Network.enable");
    for (const [w, h] of [[1920, 1080], [1366, 768]]) {
      await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
      await cdp("Page.navigate", { url: `${BASE}/#m11101_tex/keyframes/ladingwu_temporal` });
      await waitFor("document.querySelector('#kfBody') && !document.querySelector('#kfBody').hidden");
      await waitFor("document.querySelector('#kfVideo').readyState >= 2");
      await waitFor("[...document.querySelectorAll('#kfStrip img')].filter(i => i.src.startsWith('data:image')).length >= 5", 40000);
      const f = await evaluate(`({ cards: document.querySelectorAll('#kfStrip .kf-card').length, ladder: document.querySelector('#kfLadderRange')?.value,
        status: document.querySelector('#kfStatus').textContent, video: document.querySelector('#kfVideoName').textContent,
        thumbs: [...document.querySelectorAll('#kfStrip img')].filter(i => i.src.startsWith('data:image')).length,
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth })`);
      console.log(`${w}x${h}`, JSON.stringify(f));
      assert.equal(f.cards, 34); assert.equal(f.ladder, "16"); assert(f.overflow <= 0, "overflow");
      if (w === 1920) {
        await evaluate("document.activeElement?.blur(); document.body.focus()");
        await key("ArrowRight", true); await key("ArrowRight", true);
        const fr = await evaluate("document.querySelector('#kfFrameInput').value");
        console.log("Shift+→ twice from 0 ->", fr);
        assert.equal(fr, "14");
      }
      const full = await evaluate("Math.min(2600, document.documentElement.scrollHeight)");
      const shot = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: w, height: full, scale: 1 } });
      fs.writeFileSync(path.join(OUT, `prod_${w}.png`), Buffer.from(shot.data, "base64"));
    }
    // other tabs still work
    for (const tab of ["overview", "outputs", "run"]) {
      await cdp("Page.navigate", { url: `${BASE}/#m11101_tex/${tab}` });
      await waitFor(`document.querySelector('#tab-${tab}').classList.contains('active') && !document.querySelector('#pageError').textContent`);
      await sleep(1500);
      console.log("tab", tab, "ok");
    }
    await waitFor("document.querySelector('#outKeyframes') !== null", 5000).catch(() => {});
    await cdp("Page.navigate", { url: `${BASE}/#m11101_tex/outputs` });
    await waitFor("document.querySelector('#outDetail') && !document.querySelector('#outDetail').hidden", 20000);
    const btn = await evaluate("({ hidden: document.querySelector('#outKeyframes').hidden, text: document.querySelector('#outKeyframes').textContent, title: document.querySelector('#outTitle').textContent })");
    console.log("results page keyframe button:", JSON.stringify(btn));
    assert.equal(btn.hidden, false);
    console.log("non-GET requests:", requests.length ? requests : "none");
    assert.equal(requests.filter((r) => !r.includes("/api/tasks")).length, 0, "page must not write");
    console.log("page errors:", errors.length ? errors : "none");
    assert.equal(errors.length, 0);
    console.log("PROD CHECK PASSED");
  } finally {
    try { ws?.close(); } catch { /* ignore */ }
    chrome.kill();
    await sleep(500);
    fs.rmSync(profile, { recursive: true, force: true });
  }
}
main().catch((e) => { console.error("FAILED:", e); process.exit(1); });
