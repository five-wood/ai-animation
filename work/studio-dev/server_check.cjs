// Open the deployed studio, log in as admin, open real work #1, verify keyframes auto-merge + save, preview, screenshot.
// node work/studio-dev/server_check.cjs <admin_password>
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://10.0.3.117:8700";
const OUT = path.join(__dirname, "shots");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const profile = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "studio-srv-"));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9335", "--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  const errors = [];
  try {
    let t; for (let i = 0; i < 50; i++) { try { t = await (await fetch("http://127.0.0.1:9335/json/list")).json(); if (t.some((x) => x.type === "page")) break; } catch {} await sleep(200); }
    const ws = new WebSocket(t.find((x) => x.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0; const pend = new Map();
    ws.addEventListener("message", (m) => { const msg = JSON.parse(m.data); if (msg.id && pend.has(msg.id)) { pend.get(msg.id)(msg); pend.delete(msg.id); } if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description); });
    const cdp = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (e) => (await cdp("Runtime.evaluate", { expression: e, awaitPromise: true, returnByValue: true })).result?.value;
    const waitFor = async (e, ms = 60000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(e)) return; await sleep(300); }
      const r = await cdp("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(OUT, "server_timeout.png"), Buffer.from(r.data, "base64"));
      throw new Error("timeout " + e + "\n" + await ev("location.hash + ' | ' + document.body.innerText.slice(0, 600) + ' | toasts: ' + document.getElementById('toasts').innerText") + "\nerrors " + errors.join("\n")); };
    await cdp("Runtime.enable");
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false });
    await cdp("Page.navigate", { url: BASE + "/" });
    await waitFor("document.querySelector('.auth-card')");
    await ev(`(() => { const i = [...document.querySelectorAll('.auth-card input')]; i[0].value = 'admin'; i[1].value = ${JSON.stringify(process.argv[2])}; document.querySelector('.auth-card button[type=submit]').click(); return 1; })()`);
    await waitFor("document.querySelector('.topbar')");
    const w = await ev("fetch('/api/works/1').then(r => r.json())");
    await ev(`(() => { location.hash = '#/bench/${w.character.id}/1'; return 1; })()`);
    await waitFor("document.querySelectorAll('.tl-track .kf').length > 3");
    await waitFor("document.querySelector('.panel.right') && true");
    await ev("[...document.querySelectorAll('.tabs button')].find(b => b.textContent === '关键帧').click()");
    await waitFor("document.querySelector('.panel.right').textContent.includes('已自动保存')", 20000);
    const info = await ev("({ keys: document.querySelectorAll('.tl-track .kf').length, head: document.querySelector('.panel.right b')?.textContent })");
    const saved = await ev("fetch('/api/works/1/keyframes').then(r => r.json()).then(v => ({revision: v.revision, n: v.artist?.keyframes.length, removed: v.artist?.removed_auto_ids}))");
    console.log("ui", JSON.stringify(info), "server", JSON.stringify(saved));
    await sleep(4000);
    let r = await cdp("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(OUT, "server_bench.png"), Buffer.from(r.data, "base64"));
    await ev("[...document.querySelectorAll('.stage-top .seg button')].find(b => b.textContent === '拟合效果').click()");
    await sleep(2500);
    r = await cdp("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(OUT, "server_fit.png"), Buffer.from(r.data, "base64"));
    await cdp("Page.navigate", { url: `${BASE}/#/admin` });
    await waitFor("document.querySelectorAll('.table').length >= 3");
    await sleep(800);
    r = await cdp("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(OUT, "server_admin.png"), Buffer.from(r.data, "base64"));
    console.log("errors", errors);
    if (!saved.revision || errors.length) process.exit(1);
    console.log("SERVER CHECK PASSED");
  } finally { chrome.kill(); }
})().catch((e) => { console.error(e); process.exit(1); });
