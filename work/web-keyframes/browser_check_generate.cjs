// "生成候选关键帧" end to end on the local preview (real run_keyframes.py through the preview job runner).
// node work/web-keyframes/browser_check_generate.cjs   (preview_server.cjs must be running on 18600)
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const assert = require("node:assert/strict");

const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://127.0.0.1:18600";
const OUT = __dirname;
const REGEN = path.join(OUT, "fixture/projects/m11101_tex/out/ladingwu_regen");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  for (const f of fs.readdirSync(REGEN)) if (/^(keyframes_|contacts_)/.test(f)) fs.rmSync(path.join(REGEN, f), { recursive: true, force: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "kf-gen-"));
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars", "--remote-debugging-port=9335", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  let ws;
  try {
    let targets;
    for (let i = 0; i < 50; i++) { try { targets = await (await fetch("http://127.0.0.1:9335/json/list")).json(); if (targets.some((t) => t.type === "page")) break; } catch { /* starting */ } await sleep(200); }
    ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0;
    const pending = new Map(), errors = [];
    ws.addEventListener("message", (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
      if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") errors.push(msg.params.args.map((a) => a.value || a.description).join(" "));
    });
    const cdp = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, (msg) => msg.error ? reject(new Error(`${method}: ${msg.error.message}`)) : resolve(msg.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const evaluate = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };
    const waitFor = async (expr, ms = 20000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await evaluate(expr).catch(() => false)) return; await sleep(250); } throw new Error(`timeout: ${expr} :: ${await evaluate("document.querySelector('#kfGenState')?.textContent + ' | ' + document.querySelector('#kfNotice')?.textContent").catch(() => "")}`); };
    const shot = async (name) => { const r = await cdp("Page.captureScreenshot", { format: "png", clip: { x: 0, y: 0, width: 1366, height: 820, scale: 1 } }); fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64")); };
    await cdp("Runtime.enable"); await cdp("Page.enable");
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1366, height: 820, deviceScaleFactor: 1, mobile: false });

    // 1) result without candidates but with inputs -> button "生成候选关键帧"
    await cdp("Page.navigate", { url: `${BASE}/#m11101_tex/keyframes/ladingwu_regen` });
    await waitFor("document.querySelector('#kfBody') && !document.querySelector('#kfBody').hidden");
    await waitFor("!document.querySelector('#kfGenerate').hidden");
    let s = await evaluate("({ text: document.querySelector('#kfGenerate').textContent, disabled: document.querySelector('#kfGenerate').disabled, notice: document.querySelector('#kfNotice').textContent, cards: document.querySelectorAll('#kfStrip .kf-card').length })");
    console.log("before:", JSON.stringify(s));
    assert.equal(s.text, "生成候选关键帧"); assert.equal(s.disabled, false); assert.equal(s.cards, 0);
    await shot("gen_before.png");
    await evaluate("document.querySelector('#kfGenerate').click()");
    await waitFor("document.querySelector('#kfGenState').textContent.includes('正在') || document.querySelector('#kfGenState').textContent.includes('排队') || document.querySelector('#kfGenState').textContent.includes('已提交')");
    console.log("submitted:", await evaluate("document.querySelector('#kfGenState').textContent"));
    await waitFor("document.querySelector('#kfGenState').textContent.includes('正在生成')", 15000);
    await shot("gen_running.png");
    console.log("running:", await evaluate("document.querySelector('#kfGenState').textContent"));
    await waitFor("document.querySelector('#kfGenState').textContent.includes('已生成并载入')", 90000);
    await waitFor("document.querySelectorAll('#kfStrip .kf-card').length === 34");
    s = await evaluate("({ text: document.querySelector('#kfGenerate').textContent, cards: document.querySelectorAll('#kfStrip .kf-card').length, ladder: document.querySelector('#kfLadderRange')?.value, status: document.querySelector('#kfStatus').textContent })");
    console.log("after:", JSON.stringify(s));
    assert.equal(s.text, "重新生成候选"); assert.equal(s.ladder, "16");
    const opt = await evaluate("document.querySelector('#kfResult').selectedOptions[0].textContent");
    console.log("result option:", opt);
    assert.match(opt, /有候选/, "result list label updated after generation");
    await shot("gen_after.png");
    assert(fs.existsSync(path.join(REGEN, "keyframes_auto.json")));

    // 2) save an annotation, regenerate (confirm dialog), annotation gets rebased only if the candidate file changes
    await evaluate("(() => { const a = document.querySelector('#kfAuthor'); a.value = '测试'; a.dispatchEvent(new Event('input')); })()");
    await evaluate("(() => { const i = document.querySelector('#kfFrameInput'); i.value = '60'; i.dispatchEvent(new Event('change')); })()");
    await sleep(300);
    await evaluate("document.querySelector('#kfAdd').click()");
    await evaluate("document.querySelector('#kfSave').click()");
    await waitFor("document.querySelector('#kfStatus').textContent.includes('已保存 r1')");
    await evaluate("document.querySelector('#kfGenerate').click()");
    await waitFor("document.querySelector('#confirmDialog').open");
    console.log("confirm:", (await evaluate("document.querySelector('#confirmText').textContent")).slice(0, 60));
    await evaluate("(() => { const d = document.querySelector('#confirmDialog'); d.returnValue = 'ok'; d.close('ok'); })()");
    await waitFor("!document.querySelector('#kfGenState').textContent.includes('已生成并载入')", 15000);
    await waitFor("document.querySelector('#kfGenState').textContent.includes('已生成并载入')", 90000);
    const hist = fs.readdirSync(path.join(REGEN, "keyframes_auto_history"));
    console.log("history:", hist);
    assert.equal(hist.length, 1);
    s = await evaluate("({ status: document.querySelector('#kfStatus').textContent, summary: document.querySelector('#kfSummary').textContent })");
    console.log("after regenerate:", JSON.stringify(s));
    assert.match(s.status, /已保存 r1/, "identical candidates: annotation stays saved, no rebase");

    // 3) the rebase path: the stored annotation references a candidate that moved in the new file
    const art = JSON.parse(fs.readFileSync(path.join(REGEN, "keyframes_artist.json"), "utf8"));
    art.base_auto_sha256 = "0".repeat(64);
    const k = art.keyframes.find((x) => x.origin === "auto" && x.frame > 0);
    k.auto_frame = k.frame + 1;   // pretend it sat one frame later in the old candidate file
    fs.writeFileSync(path.join(REGEN, "keyframes_artist.json"), JSON.stringify(art));
    console.log("step 3: reload");
    await evaluate("setTimeout(() => document.querySelector('#kfReload').click(), 0)");
    await waitFor("document.querySelector('#kfNotice').textContent.includes('基于旧候选')");
    console.log("rebase notice:", (await evaluate("document.querySelector('#kfNotice').textContent")).slice(0, 90));
    assert.match(await evaluate("document.querySelector('#kfStatus').textContent"), /未保存/);

    // 4) non-Biped project: button disabled with reason (fresh page load; the rebased page is dirty, so skip beforeunload)
    console.log("step 4: lizard");
    await cdp("Page.navigate", { url: "about:blank" });
    await sleep(300);
    await cdp("Page.navigate", { url: `${BASE}/#lizard/keyframes/walk` });
    await waitFor("document.querySelector('#kfGenState').textContent.includes('Biped')", 20000);
    s = await evaluate("({ disabled: document.querySelector('#kfGenerate').disabled, hidden: document.querySelector('#kfGenerate').hidden, msg: document.querySelector('#kfGenState').textContent })");
    console.log("lizard:", JSON.stringify(s));
    assert.equal(s.disabled, true); assert.equal(s.hidden, false);
    await shot("gen_lizard.png");

    console.log("page errors:", errors.length ? errors : "none");
    assert.equal(errors.length, 0);
    console.log("GENERATE CHECK PASSED");
  } finally {
    try { ws?.close(); } catch { /* ignore */ }
    chrome.kill();
    await sleep(500);
    fs.rmSync(profile, { recursive: true, force: true });
  }
}
main().catch((e) => { console.error("FAILED:", e); process.exit(1); });
