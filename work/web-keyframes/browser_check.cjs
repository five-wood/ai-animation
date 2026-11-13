// Drive the local preview in headless Chrome over CDP (Node 24 global WebSocket; no packages).
// node work/web-keyframes/browser_check.cjs   (preview_server.cjs must be running on 18600)
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = "http://127.0.0.1:18600";
const OUT = __dirname;
const RESULT = path.join(OUT, "fixture/projects/m11101_tex/out/ladingwu_temporal");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  // start from a clean annotation state in the fixture copy
  for (const f of ["keyframes_artist.json"]) fs.rmSync(path.join(RESULT, f), { force: true });
  fs.rmSync(path.join(RESULT, "keyframes_artist_history"), { recursive: true, force: true });
  const autoBefore = fs.readFileSync(path.join(RESULT, "keyframes_auto.json"));
  const profile = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "kf-cdp-"));
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars", "--remote-debugging-port=9333",
    `--user-data-dir=${profile}`, "--autoplay-policy=no-user-gesture-required", "about:blank"], { stdio: "ignore" });
  let ws;
  try {
    let targets;
    for (let i = 0; i < 50; i++) { try { targets = await (await fetch("http://127.0.0.1:9333/json/list")).json(); if (targets.some((t) => t.type === "page")) break; } catch { /* starting */ } await sleep(200); }
    const page = targets.find((t) => t.type === "page");
    ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((r) => ws.addEventListener("open", r, { once: true }));
    let id = 0;
    const pending = new Map();
    const errors = [];
    ws.addEventListener("message", (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
      if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") errors.push(msg.params.args.map((a) => a.value || a.description).join(" "));
    });
    const cdp = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, (msg) => msg.error ? reject(new Error(`${method}: ${msg.error.message}`)) : resolve(msg.result)); ws.send(JSON.stringify({ id: i, method, params })); });
    const evaluate = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };
    const waitFor = async (expr, ms = 15000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await evaluate(expr).catch(() => false)) return true; await sleep(150); } throw new Error(`timeout: ${expr}`); };
    const shot = async (name, w, h) => {
      await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
      await sleep(900);
      const overflow = await evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
      const full = await evaluate("Math.ceil(document.documentElement.scrollHeight)");
      const r = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: w, height: Math.min(full, 2600), scale: 1 } });
      fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64"));
      return { overflow, full };
    };
    await cdp("Runtime.enable");
    await cdp("Page.enable");
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    await cdp("Page.navigate", { url: `${BASE}/#m11101_tex/keyframes/ladingwu_temporal` });
    await waitFor("document.querySelector('#kfBody') && !document.querySelector('#kfBody').hidden");
    await waitFor("document.querySelector('#kfVideo').readyState >= 2");
    await waitFor("[...document.querySelectorAll('#kfStrip img')].filter(i => i.src.startsWith('data:image')).length >= 6", 30000);
    const facts = await evaluate(`(() => {
      const strip = [...document.querySelectorAll('#kfStrip .kf-card')];
      return { status: document.querySelector('#kfStatus').textContent, summary: document.querySelector('#kfSummary').textContent,
        cards: strip.length, thumbs: strip.filter(c => c.querySelector('img')?.src.startsWith('data:image')).length,
        ladderValue: document.querySelector('#kfLadderRange')?.value, ladderMax: document.querySelector('#kfLadderRange')?.max,
        ladderStats: document.querySelector('#kfLadderStats')?.textContent, frameText: document.querySelector('#kfFrameText').textContent,
        legend: document.querySelector('#kfLegend').textContent, hash: location.hash };
    })()`);
    console.log("initial", JSON.stringify(facts));
    assert.equal(facts.cards, 34);
    assert.equal(facts.ladderValue, "16");
    assert.equal(facts.ladderMax, "30");
    assert.match(facts.status, /初稿/);
    assert.equal(facts.hash, "#m11101_tex/keyframes/ladingwu_temporal");
    // keyboard: go to frame 52 via input, Shift+→ to the next key, K adds a key, Delete removes it again
    await evaluate("(() => { const i = document.querySelector('#kfFrameInput'); i.value = '52'; i.dispatchEvent(new Event('change')); i.blur(); document.body.focus(); })()");
    await sleep(400);
    const key = async (k, shift = false) => { await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: k, code: k === " " ? "Space" : k.length === 1 ? `Key${k.toUpperCase()}` : k, modifiers: shift ? 8 : 0, windowsVirtualKeyCode: { ArrowRight: 39, ArrowLeft: 37, Delete: 46, k: 75, " ": 32 }[k] }); await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: k, modifiers: shift ? 8 : 0 }); await sleep(250); };
    await key("ArrowRight", true);
    const afterShift = await evaluate("document.querySelector('#kfFrameInput').value");
    console.log("Shift+→ from 52 ->", afterShift);
    assert.equal(afterShift, "57");
    await key("ArrowRight"); await key("ArrowRight");
    assert.equal(await evaluate("document.querySelector('#kfFrameInput').value"), "59");
    await key("k");
    await waitFor("document.querySelector('#kfSummary').textContent.includes('新增 1')");
    // drag-free move: choose the new key, go to 61, press 移到当前帧
    await evaluate("(() => { const i = document.querySelector('#kfFrameInput'); i.value = '61'; i.dispatchEvent(new Event('change')); })()");
    await sleep(300);
    await evaluate("document.querySelector('#kfMoveHere')?.click()");
    await sleep(300);
    // remove candidate at frame 64 (k011), adopt the suggested ladder
    await evaluate("(() => { const i = document.querySelector('#kfFrameInput'); i.value = '64'; i.dispatchEvent(new Event('change')); })()");
    await sleep(300);
    await evaluate("document.querySelector('#kfCurDelete').click()");
    await sleep(200);
    await evaluate("document.querySelector('#kfLadderAdopt').click()");
    await sleep(300);
    await evaluate("(() => { const a = document.querySelector('#kfAuthor'); a.value = '测试美术'; a.dispatchEvent(new Event('input')); const n = document.querySelector('#kfNotes'); n.value = '浏览器自动化测试'; n.dispatchEvent(new Event('input')); })()");
    const edited = await evaluate("document.querySelector('#kfSummary').textContent + ' | ' + document.querySelector('#kfStatus').textContent");
    console.log("edited", edited);
    assert.match(edited, /删除 1/); assert.match(edited, /新增 1/); assert.match(edited, /采纳过渡键 16/); assert.match(edited, /未保存/);
    const s1 = await shot("shot_1920.png", 1920, 1080);
    // save -> r1
    await evaluate("document.querySelector('#kfSave').click()");
    await waitFor("document.querySelector('#kfStatus').textContent.includes('已保存 r1')");
    // simulate another user saving r2 on the server, then save from the stale page -> 409 -> overwrite on latest
    const cur = JSON.parse(fs.readFileSync(path.join(RESULT, "keyframes_artist.json"), "utf8"));
    const other = { author: "美术B", base_revision: 1, reviewed: false, notes: "B 的版本", keyframes: cur.keyframes.slice(0, 5), removed_auto_ids: [] };
    const put = await fetch(`${BASE}/api/projects/m11101_tex/out/ladingwu_temporal/keyframes/artist`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(other) });
    assert.equal(put.status, 200);
    await evaluate("(() => { const n = document.querySelector('#kfNotes'); n.value = '浏览器自动化测试（第二次）'; n.dispatchEvent(new Event('input')); })()");
    await evaluate("document.querySelector('#kfSave').click()");
    await waitFor("!document.querySelector('#kfConflict').hidden && document.querySelector('#kfConflict').textContent.includes('r2')");
    const conflictText = await evaluate("document.querySelector('#kfConflict').textContent");
    console.log("conflict:", conflictText.slice(0, 120));
    await shot("shot_1920_conflict.png", 1920, 1080);
    await evaluate("document.querySelector('[data-kf-conflict=overwrite]').click()");
    await waitFor("document.querySelector('#kfStatus').textContent.includes('已保存 r3')");
    const s2 = await shot("shot_1366.png", 1366, 768);
    // no-candidate result is still usable
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1366, height: 768, deviceScaleFactor: 1, mobile: false });
    await evaluate("(() => { const s = document.querySelector('#kfResult'); s.value = 'no_candidates'; s.dispatchEvent(new Event('change')); })()");
    await waitFor("document.querySelector('#kfNotice').textContent.includes('尚未生成候选关键帧')");
    const nc = await evaluate("({ ladder: document.querySelector('#kfLadder').textContent, cards: document.querySelectorAll('#kfStrip .kf-card').length })");
    console.log("no candidates:", JSON.stringify(nc));
    await shot("shot_1366_nocandidates.png", 1366, 768);
    // files on disk
    const files = fs.readdirSync(path.join(RESULT, "keyframes_artist_history")).sort();
    const final = JSON.parse(fs.readFileSync(path.join(RESULT, "keyframes_artist.json"), "utf8"));
    console.log("history", files.join(","), "final r", final.revision, "keys", final.keyframes.length, "removed", final.removed_auto_ids.join(","),
      "ladder", final.keyframes.filter((k) => k.origin === "ladder").length, "artist", final.keyframes.filter((k) => k.origin === "artist").map((k) => `${k.id}@${k.frame}`).join(","));
    assert.deepEqual(files, ["rev0001.json", "rev0002.json", "rev0003.json"]);
    assert.equal(final.revision, 3);
    assert.deepEqual(final.removed_auto_ids, ["k011"]);
    assert(final.keyframes.some((k) => k.origin === "artist" && k.frame === 61));
    assert(fs.readFileSync(path.join(RESULT, "keyframes_auto.json")).equals(autoBefore), "auto file must not change");
    console.log("overflow px: 1920 =", s1.overflow, " 1366 =", s2.overflow);
    assert(s1.overflow <= 0 && s2.overflow <= 0, "horizontal overflow");
    console.log("page errors:", errors.length ? errors : "none");
    assert.equal(errors.length, 0);
    console.log("BROWSER CHECK PASSED");
  } finally {
    try { ws?.close(); } catch { /* ignore */ }
    chrome.kill();
    await sleep(500);
    fs.rmSync(profile, { recursive: true, force: true });
  }
}
main().catch((e) => { console.error("FAILED:", e); process.exit(1); });
