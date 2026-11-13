// Deployed server, /vendor/* blocked: login, library, character dialog and an existing work must still work.
// node work/studio-dev/server_no3d.cjs <admin_password>
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://10.0.3.117:8700";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const profile = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "srvno3d-"));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9337", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  const errors = [];
  try {
    let t; for (let i = 0; i < 50; i++) { try { t = await (await fetch("http://127.0.0.1:9337/json/list")).json(); if (t.some((x) => x.type === "page")) break; } catch {} await sleep(200); }
    const ws = new WebSocket(t.find((x) => x.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0; const pend = new Map();
    ws.addEventListener("message", (m) => { const msg = JSON.parse(m.data); if (msg.id && pend.has(msg.id)) { pend.get(msg.id)(msg); pend.delete(msg.id); }
      if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description); });
    const cdp = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (e) => (await cdp("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true })).result?.value;
    const waitFor = async (e, ms = 40000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(e)) return; await sleep(300); } throw new Error("timeout " + e); };
    await cdp("Runtime.enable"); await cdp("Network.enable"); await cdp("Page.enable");
    await cdp("Network.setBlockedURLs", { urls: ["*/vendor/*"] });
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
    await cdp("Page.navigate", { url: BASE + "/" });
    await waitFor("document.querySelector('.auth-card')");
    await ev(`(() => { const i = [...document.querySelectorAll('.auth-card input')]; i[0].value = 'admin'; i[1].value = ${JSON.stringify(process.argv[2])}; document.querySelector('.auth-card button[type=submit]').click(); return 1; })()`);
    await waitFor("document.querySelectorAll('.grid .card').length >= 1");
    console.log("library ok");
    const w = await ev("fetch('/api/works/1').then(r => r.json())");
    await ev(`(() => { location.hash = '#/bench/${w.character.id}/1'; return 1; })()`);
    await waitFor("document.querySelectorAll('.tl-track .kf').length > 3");
    await waitFor("document.querySelector('.central').textContent.includes('3D 预览暂时无法加载')");
    console.log("bench ok, keys:", await ev("document.querySelectorAll('.tl-track .kf').length"), "notice shown");
    const r = await cdp("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(__dirname, "shots", "server_no3d.png"), Buffer.from(r.data, "base64"));
    console.log("exceptions:", errors);
    if (errors.length) process.exit(1);
    console.log("SERVER NO-3D CHECK PASSED");
  } finally { chrome.kill(); }
})().catch((e) => { console.error("FAILED", e.message); process.exit(1); });
