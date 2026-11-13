/* Dependency-free tests for the keyframe page logic (keyframes-core.js).
 * Run: node video2rig/tools/test_keyframes.cjs
 * Uses the real ladingwu candidate file when present (read-only).
 */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

(async () => {
  const K = await import(path.join(__dirname, "../web/static/keyframes-core.js").replace(/\\/g, "/").replace(/^([A-Za-z]):/, "file:///$1:"));
  const sample = path.join(__dirname, "../../work/maxkeys/m11101_tex_ladingwu/keyframes_auto.json");
  const auto = fs.existsSync(sample) ? JSON.parse(fs.readFileSync(sample, "utf8")) : {
    fps: 24, frames: 50, keyframes: [{ id: "k001", frame: 0, type: "start" }, { id: "k002", frame: 10, type: "contact" }, { id: "k003", frame: 49, type: "end" }],
    breakdown_ladder: { suggested_count: 1, breakdowns: [{ rank: 1, frame: 5 }, { rank: 2, frame: 10 }], error_curve: [{ n: 0, max_cm: 5, p95_cm: 3 }, { n: 1, max_cm: 2, p95_cm: 1 }, { n: 2, max_cm: 1, p95_cm: 1 }] },
  };
  const autoBefore = JSON.stringify(auto);
  const frames = auto.frames, fps = auto.fps;
  let n = 0;
  const pass = (name) => { n++; console.log("PASS", name); };

  // frame <-> time: seek to the frame centre; floor with epsilon; clamp to the clip
  for (const f of [0, 1, 37, frames - 1]) assert.equal(K.timeToFrame(K.frameToTime(f, fps), fps, frames), f);
  assert.equal(K.timeToFrame(1 / fps - 1e-9, fps, frames), 1, "epsilon keeps exact frame boundaries on the new frame");
  assert.equal(K.timeToFrame(-3, fps, frames), 0);
  assert.equal(K.timeToFrame(9999, fps, frames), frames - 1);
  assert.equal(K.clampFrame(2.6, frames), 3);
  pass("frame/time conversion (centre seek, floor+epsilon, clamp)");

  // first draft from candidates
  let st = K.initialState({ auto, artist: null });
  assert.equal(st.fromArtist, false);
  assert.equal(st.keyframes.length, auto.keyframes.length);
  assert(st.keyframes.every((k) => k.origin === "auto" && k.auto_id === k.id && k.auto_frame === k.frame && k.note === ""));
  assert.deepEqual(st.removed, []);
  const d0 = K.diffSummary(st, auto);
  assert.equal(d0.kept, auto.keyframes.length); assert.equal(d0.unseen, 0); assert.equal(d0.moved, 0);
  pass("first draft = all structural candidates, origin auto");

  // add / conflict / move / retype / note / delete + restore
  const free = (() => { for (let f = 1; f < frames; f++) if (!K.keyAt(st, f)) return f; })();
  let r = K.addKey(st, free, "custom", frames);
  assert(r.ok); assert.equal(r.id, "a001"); st = r.state;
  r = K.addKey(st, free, "custom", frames); assert.equal(r.ok, false); assert.match(r.message, /同一帧/);
  r = K.addKey(st, frames, "custom", frames); assert.equal(r.ok, false);
  const second = (() => { for (let f = free + 1; f < frames; f++) if (!K.keyAt(st, f)) return f; })();
  r = K.addKey(st, second, "bogus", frames); assert(r.ok); assert.equal(r.id, "a002"); assert.equal(r.state.keyframes.find((k) => k.id === "a002").type, "custom"); st = r.state;
  const first = st.keyframes.find((k) => k.origin === "auto" && k.frame > 0 && k.frame < frames - 1);
  const occupied = st.keyframes.find((k) => k.id !== first.id).frame;
  r = K.moveKey(st, first.id, occupied, frames); assert.equal(r.ok, false); assert.match(r.message, /已有关键帧/);
  const target = (() => { for (let f = first.frame + 1; f < frames; f++) if (!K.keyAt(st, f)) return f; })();
  r = K.moveKey(st, first.id, target, frames); assert(r.ok); st = r.state;
  assert.equal(st.keyframes.find((k) => k.id === first.id).auto_frame, first.frame);
  const newType = first.type === "anticipation" ? "rebound" : "anticipation";
  r = K.setKeyField(st, first.id, "type", newType); assert(r.ok); st = r.state;
  assert.equal(K.setKeyField(st, first.id, "type", "nope").ok, false);
  assert.equal(K.setKeyField(st, first.id, "note", "x".repeat(501)).ok, false);
  r = K.setKeyField(st, first.id, "note", "再晚两帧才到头"); assert(r.ok); st = r.state;
  const victim = st.keyframes.find((k) => k.origin === "auto" && k.id !== first.id);
  r = K.removeKey(st, victim.id); assert(r.ok); st = r.state;
  assert.deepEqual(st.removed, [victim.auto_id]);
  r = K.removeKey(st, "a001"); assert(r.ok); st = r.state;
  assert.deepEqual(st.removed, [victim.auto_id], "deleting an artist key is not a removed candidate");
  r = K.restoreAuto(st, auto, victim.auto_id); assert(r.ok); assert.deepEqual(r.state.removed, []);
  // keep it removed for the summary below
  const d = K.diffSummary(st, auto);
  assert.equal(d.removed, 1); assert.equal(d.moved, 1); assert.equal(d.added, 1); assert.equal(d.retyped, 1);
  assert.match(K.summaryText(d), /挪动 1 个/);
  pass("add / same-frame conflict / move / retype / note / delete / restore / diff summary");

  // restoring a candidate onto an occupied frame is refused
  const blocked = K.addKey(K.removeKey(K.initialState({ auto }), auto.keyframes[1].id).state, auto.keyframes[1].frame, "custom", frames).state;
  assert.equal(K.restoreAuto(blocked, auto, auto.keyframes[1].id).ok, false);
  pass("restore refuses an occupied frame");

  // ladder: info, curve lookup, adopt first k, skip same-frame, idempotent
  const info = K.ladderInfo(auto);
  assert(info.ok); assert.equal(info.suggested, auto.breakdown_ladder.suggested_count);
  assert.equal(K.curveAt(info, 0).n, 0);
  let base = K.initialState({ auto });
  const firstB = info.breakdowns[0];
  base = K.addKey(base, firstB.frame, "custom", frames).state;   // occupy rank 1's frame
  r = K.adoptLadder(base, info, info.suggested);
  assert.equal(r.skipped, 1); assert.equal(r.added, info.suggested - 1);
  const adopted = r.state.keyframes.filter((k) => k.origin === "ladder");
  assert(adopted.every((k) => k.type === "breakdown" && /^b\d{3}$/.test(k.id) && k.auto_id === null && Number.isInteger(k.ladder_rank) && k.auto_frame === info.breakdowns.find((b) => b.rank === k.ladder_rank).frame));
  const again = K.adoptLadder(r.state, info, info.suggested);
  assert.equal(again.added, 0); assert.equal(again.state, r.state);
  assert.equal(K.diffSummary(r.state, auto).ladder, adopted.length);
  for (const bad of [null, { breakdown_ladder: null }, { breakdown_ladder: { error: "boom" } }, { breakdown_ladder: { breakdowns: [] } }]) assert.equal(K.ladderInfo(bad).ok, false);
  assert.match(K.ladderInfo({ breakdown_ladder: { error: "boom" } }).reason, /boom/);
  pass("ladder: suggested count, adopt first k, same-frame skip, idempotent, error/empty states");

  // payload matches the contract (origin rules, ladder_rank only for ladder)
  const p = K.payload(r.state, 3);
  assert.equal(p.base_revision, 3);
  for (const k of p.keyframes) {
    if (k.origin === "auto") assert(k.auto_id && Number.isInteger(k.auto_frame));
    if (k.origin === "artist") { assert.equal(k.auto_id, null); assert.equal(k.auto_frame, null); assert(!("ladder_rank" in k)); }
    if (k.origin === "ladder") { assert.equal(k.auto_id, null); assert(Number.isInteger(k.ladder_rank)); }
  }
  assert.deepEqual(K.validateLocal(r.state, frames), []);
  const dup = { ...r.state, keyframes: [...r.state.keyframes, { ...r.state.keyframes[0], id: "zz" }] };
  assert(K.validateLocal(dup, frames).some((m) => /同时有/.test(m)));
  assert(K.validateLocal({ ...r.state, author: "x".repeat(41) }, frames).length);
  pass("save payload follows the contract; local validation catches duplicates and limits");

  // artist file round trip and dirty signature
  const saved = { revision: 2, author: "美术A", notes: "n", reviewed: true, keyframes: p.keyframes, removed_auto_ids: p.removed_auto_ids };
  const loaded = K.initialState({ auto, artist: saved });
  assert.equal(loaded.fromArtist, true); assert.equal(loaded.author, "美术A"); assert.equal(loaded.reviewed, true);
  assert.equal(K.signature(loaded), K.signature({ ...r.state, author: "美术A", notes: "n", reviewed: true }));
  assert.notEqual(K.signature(loaded), K.signature(K.moveKey(loaded, loaded.keyframes[1].id, loaded.keyframes[1].frame + 0, frames).state) + "x");
  pass("artist file loads back to the same signature");

  // 409 handling
  const e409 = Object.assign(new Error("409"), { status: 409, detail: { message: "已有人保存了新版本 r4", current_revision: 4, artist: { author: "B" } } });
  assert.deepEqual(K.conflictInfo(e409), { message: "已有人保存了新版本 r4", revision: 4, artist: { author: "B" } });
  assert.equal(K.conflictInfo(Object.assign(new Error("400"), { status: 400, detail: "bad" })), null);
  pass("409 detail parsed for reload / overwrite-on-latest");

  // no candidates: blank start, no removed ids, ladder hidden
  const blank = K.initialState({ auto: null, artist: null });
  assert.deepEqual(blank.keyframes, []);
  assert.equal(K.ladderInfo(null).ok, false);
  const blankAdd = K.addKey(blank, 3, "custom", 10);
  assert(blankAdd.ok); assert.match(K.summaryText(K.diffSummary(blankAdd.state, null)), /无候选/);
  pass("no candidate file: annotate from scratch");

  // export document
  const doc = K.exportDocument(r.state, { video_name: "ladingwu.mp4", fps, frames, auto_sha256: "abc" }, 3);
  assert.equal(doc.schema, "v2r.keyframes.artist/1"); assert.equal(doc.base_revision, 3); assert.equal(doc.source_video, "ladingwu.mp4");
  pass("export JSON");

  // candidate file regenerated after the annotation was saved -> rebase keeps every annotated frame
  {
    const oldAuto = { keyframes: [{ id: "k001", frame: 0, type: "start" }, { id: "k002", frame: 10, type: "contact" }, { id: "k003", frame: 20, type: "extreme" }, { id: "k004", frame: 30, type: "end" }],
      breakdown_ladder: { suggested_count: 2, breakdowns: [{ rank: 1, frame: 5 }, { rank: 2, frame: 15 }], error_curve: [] } };
    const newAuto = { keyframes: [{ id: "k001", frame: 0, type: "start" }, { id: "k002", frame: 12, type: "contact" }, { id: "k003", frame: 20, type: "extreme" }, { id: "k004", frame: 30, type: "end" }],
      breakdown_ladder: { suggested_count: 1, breakdowns: [{ rank: 1, frame: 5 }, { rank: 2, frame: 17 }], error_curve: [] } };
    let s0 = K.initialState({ auto: oldAuto });
    s0 = K.moveKey(s0, "k003", 21, 40).state;                         // still k003 (auto_frame 20)
    s0 = K.removeKey(s0, "k004").state;                               // removed candidate that still exists
    s0 = K.addKey(s0, 25, "custom", 40).state;                        // a001
    s0 = K.adoptLadder(s0, K.ladderInfo(oldAuto), 2).state;           // b001@5 (still valid), b002@15 (moved -> artist)
    const artist = { ...K.payload(s0, 0), base_auto_sha256: "old", revision: 1 };
    const same = K.initialState({ auto: oldAuto, artist, auto_sha256: "old" });
    assert.equal(same.rebased, undefined, "same candidate file: no rebase");
    const r = K.initialState({ auto: newAuto, artist, auto_sha256: "new" });
    assert(r.rebased);
    assert.deepEqual(r.keyframes.map((k) => k.frame), s0.keyframes.map((k) => k.frame), "every annotated frame kept");
    const byFrame = Object.fromEntries(r.keyframes.map((k) => [k.frame, k]));
    assert.equal(byFrame[0].origin, "auto"); assert.equal(byFrame[21].origin, "auto"); assert.equal(byFrame[21].auto_id, "k003");
    assert.equal(byFrame[10].origin, "artist", "k002 moved in the new file -> artist key");
    assert.equal(byFrame[5].origin, "ladder"); assert.equal(byFrame[15].origin, "artist");
    assert.equal(byFrame[25].id, "a001");
    assert.deepEqual(r.removed, ["k004"]);
    assert.equal(new Set(r.keyframes.map((k) => k.id)).size, r.keyframes.length, "ids unique after rebase");
    const pl = K.payload(r, 1);
    assert(pl.keyframes.filter((k) => k.origin === "artist").every((k) => k.auto_id === null && k.auto_frame === null && !("ladder_rank" in k)));
    assert.deepEqual(K.validateLocal(r, 40), []);
    const gone = K.initialState({ auto: { keyframes: [{ id: "k001", frame: 0, type: "start" }] }, artist, auto_sha256: "new2" });
    assert.deepEqual(gone.removed, [], "removed ids that no longer exist are dropped");
    assert.equal(gone.rebased.removedDropped, 1);
  }
  pass("rebase after candidates are regenerated keeps every annotated frame");

  // generation button state
  {
    const g0 = K.generationState({ auto: null, generation: { available: true, reason: "" } }, []);
    assert.deepEqual([g0.show, g0.enabled, g0.label, Boolean(g0.replace)], [true, true, "生成候选关键帧", false]);
    const g1 = K.generationState({ auto: {}, generation: { available: true } }, []);
    assert.deepEqual([g1.enabled, g1.label, g1.replace], [true, "重新生成候选", true]);
    const g2 = K.generationState({ auto: null, generation: { available: false, reason: "没有 Biped 关节" } }, []);
    assert.deepEqual([g2.show, g2.enabled, g2.reason], [true, false, "没有 Biped 关节"]);
    const g3 = K.generationState({ auto: null, generation: { available: true } }, [{ cmd: "keyframes", status: "running" }]);
    assert.deepEqual([g3.enabled, g3.label], [false, "正在生成…"]);
    assert.equal(K.generationState({ auto: null }, []).show, false, "old server without generation info: no button");
  }
  pass("generation button: new / regenerate / unavailable / running / old server");

  assert.equal(JSON.stringify(auto), autoBefore, "candidate data must never be mutated");
  pass("candidate data untouched");
  console.log(`${n}/${n} keyframe logic tests passed. No network.`);
})().catch((e) => { console.error(e); process.exit(1); });
