// Production check of "生成候选关键帧" on ONE result that has no candidates yet (adds files only).
// node work/web-keyframes/prod_check_generate.cjs <project> <result>
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const assert = require("node:assert/strict");

const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://10.0.3.117:8600";
const [PROJECT, RESULT] = process.argv.slice(2);
const OUT = path.join(__dirname, "prod");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  assert(PROJECT && RESULT, "usage: prod_check_generate.cjs <project> <result>");
  fs.mkdirSync(OUT, { recursive: true });
  const before = await (await fetch(`${BASE}/api/projects/${PROJECT}/out/${RESULT}/keyframes`)).json();
  assert.equal(before.auto, null, "this check only runs on a result without candidates");
  assert.equal(before.generation?.available, true, before.generation?.reason);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "kf-prodgen-"));
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars", "--remote-debugging-port=9336", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  let ws;
  try {
    let targets;
    for (let i = 0; i < 50; i++) { try { targets = await (await fetch("http://127.0.0.1:9336/json/list")).json(); if (targets.some((t) => t.type === "page")) break; } catch { /* starting */ } await sleep(200); }
    ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0;
    const pending = new Map(), errors = [], writes = [];
    ws.addEventListener("message", (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
      if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") errors.push(msg.params.args.map((a) => a.value || a.description).join(" "));
      if (msg.method === "Network.requestWillBeSent" && msg.params.request.method !== "GET") writes.push(`${msg.params.request.method} ${msg.params.request.url} ${msg.params.request.postData || ""}`);
      if (msg.method === "Network.responseReceived" && msg.params.response.status >= 400 && !msg.params.response.url.endsWith("/favicon.ico")) errors.push(`HTTP ${msg.params.response.status} ${msg.params.response.url}`);
    });
    const cdp = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, (msg) => msg.error ? reject(new Error(`${method}: ${msg.error.message}`)) : resolve(msg.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const evaluate = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };
    const waitFor = async (expr, ms = 20000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await evaluate(expr).catch(() => false)) return; await sleep(300); } throw new Error(`timeout: ${expr} :: ${await evaluate("document.querySelector('#kfGenState')?.textContent").catch(() => "")}`); };
    await cdp("Runtime.enable"); await cdp("Page.enable"); await cdp("Network.enable");
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    await cdp("Page.navigate", { url: `${BASE}/#${PROJECT}/keyframes/${RESULT}` });
    await waitFor("document.querySelector('#kfBody') && !document.querySelector('#kfBody').hidden");
    await waitFor("!document.querySelector('#kfGenerate').hidden && !document.querySelector('#kfGenerate').disabled");
    console.log("button:", await evaluate("document.querySelector('#kfGenerate').textContent"), "| notice:", await evaluate("document.querySelector('#kfNotice').textContent"));
    const t0 = Date.now();
    await evaluate("document.querySelector('#kfGenerate').click()");
    await waitFor("document.querySelector('#kfGenState').textContent.includes('已生成并载入')", 180000);
    console.log(`generated in ${((Date.now() - t0) / 1000).toFixed(1)} s (incl. queue poll)`);
    const s = await evaluate("({ cards: document.querySelectorAll('#kfStrip .kf-card').length, ladder: document.querySelector('#kfLadderRange')?.value, ladderMax: document.querySelector('#kfLadderRange')?.max, button: document.querySelector('#kfGenerate').textContent, status: document.querySelector('#kfStatus').textContent, stats: document.querySelector('#kfLadderStats')?.textContent })");
    console.log("after:", JSON.stringify(s));
    assert(s.cards > 0); assert.equal(s.button, "重新生成候选");
    const opt = await evaluate("document.querySelector('#kfResult').selectedOptions[0].textContent");
    console.log("result option:", opt);
    assert.match(opt, /有候选/);
    await waitFor("[...document.querySelectorAll('#kfStrip img')].filter(i => i.src.startsWith('data:image')).length >= 4", 40000);
    const full = await evaluate("Math.min(2400, document.documentElement.scrollHeight)");
    const shot = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: 1920, height: full, scale: 1 } });
    fs.writeFileSync(path.join(OUT, `prod_generate_${PROJECT}_${RESULT}.png`), Buffer.from(shot.data, "base64"));
    // task center shows it with the right label and "查看关键帧"
    await evaluate("document.querySelector('#openTasks').click()");
    await waitFor("document.querySelector('#jobList')?.textContent.includes('生成候选关键帧')");
    console.log("task center:", (await evaluate("document.querySelector('#jobList .item.sel')?.textContent || document.querySelector('#jobList').textContent")).slice(0, 80));
    console.log("writes:", writes);
    assert.equal(writes.filter((w) => !w.includes('"cmd":"keyframes"')).length, 0, "only the keyframes job may be posted");
    console.log("page errors:", errors.length ? errors : "none");
    assert.equal(errors.length, 0);
    console.log("PROD GENERATE CHECK PASSED");
  } finally {
    try { ws?.close(); } catch { /* ignore */ }
    chrome.kill();
    await sleep(500);
    fs.rmSync(profile, { recursive: true, force: true });
  }
}
main().catch((e) => { console.error("FAILED:", e); process.exit(1); });
