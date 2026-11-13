import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const CHART_HEIGHT = 160;
const positive = (n) => Number.isFinite(Number(n)) && Number(n) > 0 ? Number(n) : 0;
const fmt = (n, digits = 2) => n == null || !Number.isFinite(Number(n)) ? "–" : Number(n).toFixed(digits);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const errorText = (e) => e?.message || String(e || "未知错误");
const urlPart = (s) => encodeURIComponent(String(s));
const safeFiles = (files) => (Array.isArray(files) ? files : []).filter((f) => typeof f === "string" && f && !/[\\/]/.test(f) && f !== "." && f !== "..");

function checkLabel(o) {
  if (o.check?.same_move === false) return [o.summary?.forced ? "强制预览 · 检查未通过" : "需复核 · 检查未通过", "bad"];
  if (o.summary?.status === "needs_review") return ["需复核 · 已保存诊断", "bad"];
  if (o.check?.same_move === true) return ["几何检查通过 · 非质量保证", "ok"];
  return ["尚无检查结论", ""];
}

// The module owns only the contents of #outputsContent. No global player or scene is shared.
export function initResults({ api, submit, current, notify, chart, openKeyframes }) {
  const host = document.querySelector("#tab-outputs #outputsContent");
  if (!host) throw new Error("Results needs #outputsContent inside #tab-outputs");
  const tab = document.getElementById("tab-outputs");
  host.innerHTML = `
    <div class="results-layout">
      <aside class="panel result-sidebar" aria-labelledby="outListTitle">
        <div class="toolbar"><h2 id="outListTitle">动画结果</h2><button id="outRefresh" type="button" aria-label="刷新动画结果">刷新</button></div>
        <label class="field" for="outSearch">搜索结果<input id="outSearch" type="search" placeholder="按名称搜索" autocomplete="off"></label>
        <label class="field" for="outFilter">筛选<select id="outFilter"><option value="all">全部结果</option><option value="review">需要复核</option><option value="passed">几何检查通过</option><option value="delivery">有交付文件</option><option value="stable">稳定副本</option></select></label>
        <p id="outCount" class="hint" role="status" aria-live="polite"></p>
        <div id="outList" class="list" aria-label="动画结果列表"></div>
      </aside>
      <div class="result-main">
        <div id="outNotice" class="run-notice" role="status" hidden></div>
        <div id="outEmpty" class="panel empty-state" role="status">选择项目后查看动画结果。</div>
        <article id="outDetail" hidden>
          <section class="panel" aria-labelledby="outTitle">
            <div class="toolbar result-title"><h2 id="outTitle"></h2><span id="outBadge" class="badge"></span><a id="outMainExport" class="primary" download hidden>下载 FBX</a><button id="outKeyframes" type="button" hidden>关键帧标注 →</button></div>
            <p id="outConclusion" class="run-notice"></p>
            <div id="outFiles" class="export-menu"></div>
            <div class="toolbar"><div id="outModes" class="segmented" role="group" aria-label="预览模式"><button type="button" data-out-mode="video" aria-pressed="true">视频</button><button type="button" data-out-mode="3d" aria-pressed="false">3D</button><button type="button" data-out-mode="compare" aria-pressed="false">对照</button></div><span id="outPreviewHint" class="hint"></span></div>
            <div id="outStage" class="preview-stage" data-mode="video" style="height:clamp(240px,38vw,460px);min-width:0;overflow:hidden;display:grid;grid-template-columns:minmax(0,1fr);gap:12px">
              <div id="outVideoPane" class="preview-pane" style="position:relative;min-width:0;min-height:0;height:100%;overflow:hidden">
                <video id="outVideo" playsinline muted preload="metadata" aria-label="结果诊断与动作对照视频" style="width:100%;height:100%;object-fit:contain"></video>
                <div id="outVideoState" class="empty-state" role="status" style="position:absolute;inset:0;display:grid;place-content:center;pointer-events:none"></div>
              </div>
              <div id="outThreePane" class="preview-pane" hidden style="position:relative;min-width:0;min-height:0;height:100%;overflow:hidden">
                <div id="tViewport" role="img" aria-label="可旋转和缩放的三维动画预览" style="width:100%;height:100%"></div>
                <div id="tState" class="empty-state" role="status" style="position:absolute;inset:0;display:grid;place-content:center;pointer-events:none"></div>
              </div>
            </div>
            <div class="preview-controls toolbar">
              <button id="tPlay" type="button" aria-label="播放预览" disabled>播放</button>
              <button id="tPrev" type="button" aria-label="向前一帧" disabled>前一帧</button>
              <button id="tNext" type="button" aria-label="向后一帧" disabled>后一帧</button>
              <label for="tSpeed">速度 <select id="tSpeed" aria-label="预览播放速度"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label>
              <input id="tSeek" type="range" min="0" max="0" step="0.001" value="0" disabled aria-label="预览时间轴（秒）" aria-describedby="tTime" style="flex:1;min-width:100px">
              <output id="tTime" for="tSeek">0.00 / – s</output>
            </div>
            <p id="tSync" class="run-notice" role="status" hidden></p>
            <p id="outAssetNotice" class="run-notice" role="status" hidden></p>
            <p id="tFrameHint" class="hint"></p>
          </section>
          <section class="panel" aria-labelledby="outCheckTitle">
            <h3 id="outCheckTitle">检查与异常时间段</h3>
            <div id="outRanges" class="issue-ranges"></div>
            <details id="outDiagnostics" class="advanced">
              <summary>技术诊断与逐帧曲线</summary>
              <p class="hint">每帧射线偏差，占身体尺寸 %。几何一致不等于动作准确；任务完成也不代表质量通过。</p>
              <p id="outCurveState" class="hint" role="status"></p>
              <canvas id="outCurve" height="160" data-height="160" aria-label="逐帧射线偏差曲线，红色区域为异常帧" role="img" style="display:block;width:100%;height:160px" hidden></canvas>
              <div id="outSummary" class="kv"></div>
            </details>
          </section>
          <details id="outStableDetails" class="panel advanced">
            <summary id="outStableTitle">减少抖动 · 生成稳定副本</summary>
            <p class="hint">只在新结果中减少抖动，保留源结果；不保证锁脚、修正识别错误或恢复动作真值。</p>
            <form id="outStableForm" class="form-grid">
              <label class="field" for="outStableName">副本名称<input id="outStableName" required maxlength="120" pattern="[A-Za-z0-9][A-Za-z0-9_.\\-]*" autocomplete="off" aria-describedby="outStableHelp"></label>
              <label class="field" for="outStableCutoff">平滑截止频率（Hz）<input id="outStableCutoff" type="number" min="0.25" max="6" step="0.05" value="1.5" required></label>
              <p id="outStableHelp" class="hint">名称以字母或数字开头，仅用字母、数字、_、-、.；0.25–6 Hz，越低平滑越强。</p>
              <button id="outStableRun" class="primary" type="submit" disabled>生成稳定副本</button>
            </form>
            <p id="outStableState" class="run-notice" role="status"></p>
          </details>
        </article>
      </div>
    </div>`;
  const el = Object.fromEntries([...host.querySelectorAll("[id]")].map((node) => [node.id, node]));
  const desired = new Map();
  const pendingCopies = new Set();
  let project = null;
  let outputs = [];
  let selected = null;
  let result = null;
  let listVersion = 0;
  let viewVersion = 0;
  let glbVersion = 0;
  let listAbort = null;
  let curveAbort = null;
  let refreshPending = null;
  let sceneState = null;
  let glbFile = null;
  let videoFile = null;
  let glbStatus = "none";
  let videoStatus = "none";
  let modelDuration = 0;
  let videoDuration = 0;
  let clockTime = 0;
  let playing = false;
  let playbackRate = 1;
  let mode = "video";
  let animationFrame = 0;
  let previousTime = 0;
  let curve = null;
  let assetMessages = [];
  let stableBusy = false;

  const visible = () => !document.hidden && !tab.hidden && getComputedStyle(tab).display !== "none" && host.getBoundingClientRect().width > 0;
  const activeView = (version, p = project, name = selected) => version === viewVersion && p === project && p === current() && name === selected;
  const baseUrl = () => `/api/projects/${urlPart(project)}/file/out/${urlPart(selected)}/`;
  const fps = () => positive(result?.summary?.fps) || positive(result?.diagnostics?.fps) || positive(result?.stability?.fps);
  const frameStep = () => 1 / (fps() || 30);
  const videoMaster = () => videoStatus === "ready" && videoDuration > 0;
  const duration = () => videoMaster() ? videoDuration : videoStatus === "loading" ? 0 : modelDuration;
  const position = () => videoMaster() ? Math.min(videoDuration, Math.max(0, el.outVideo.currentTime || 0)) : clockTime;
  const tolerance = () => Math.max(fps() ? 2 / fps() : 0, 0.15);
  const syncAgrees = () => modelDuration > 0 && videoMaster() && Math.abs(videoDuration - modelDuration) <= tolerance();

  function setNotice(node, message, kind = "") {
    node.textContent = message;
    node.hidden = !message;
    node.dataset.kind = kind;
  }
  function reportAsset(message) {
    if (!assetMessages.includes(message)) assetMessages.push(message);
    setNotice(el.outAssetNotice, assetMessages.join(" · "), "error");
  }
  function emit(message, kind) {
    if (typeof notify === "function") notify(message, kind);
  }
  function videoState(message) {
    el.outVideoState.textContent = message;
    el.outVideoState.hidden = !message;
    el.outVideoState.style.display = message ? "grid" : "none";
  }
  function threeState(message) {
    el.tState.textContent = message;
    el.tState.hidden = !message;
    el.tState.style.display = message ? "grid" : "none";
  }
  function updateTimeline() {
    const total = duration();
    const time = Math.min(total || 0, position());
    el.tSeek.max = String(total || 0);
    el.tSeek.value = String(time);
    el.tSeek.setAttribute("aria-valuetext", `${fmt(time)} 秒，共 ${total ? fmt(total) : "未知"} 秒`);
    el.tTime.textContent = `${fmt(time)} / ${total ? fmt(total) : "–"} s`;
    const enabled = total > 0;
    el.tPlay.disabled = !enabled;
    el.tSeek.disabled = !enabled;
    el.tPrev.disabled = !enabled;
    el.tNext.disabled = !enabled;
    el.tPlay.textContent = playing ? "暂停" : "播放";
    el.tPlay.setAttribute("aria-label", playing ? "暂停预览" : "播放预览");
    el.tPlay.setAttribute("aria-pressed", String(playing));
    el.tFrameHint.textContent = fps() ? `逐帧步长：1/${fmt(fps(), 2)} 秒。${videoMaster() ? "视频时间为主时钟。" : modelDuration ? "无可用视频，使用 3D 动画时钟。" : ""}` : "未提供 fps；逐帧按钮按 30 fps 近似步进。";
  }
  function updateSyncNotice() {
    let message = "";
    let kind = "";
    if (modelDuration && videoMaster()) {
      if (syncAgrees()) message = `视频主时钟 · 1 秒对应 3D 的 1 秒；片尾将 3D 限定在自身范围。视频 ${fmt(videoDuration)}s / 3D ${fmt(modelDuration)}s。`;
      else {
        message = `时长不一致：视频 ${fmt(videoDuration, 3)}s / 3D ${fmt(modelDuration, 3)}s，差值超过 ${fmt(tolerance(), 3)}s 容差。按秒定位仅供参考，不保证动作对应；未缩放或归一化时间，超过 3D 末尾时保持其末帧。`;
        kind = "warning";
      }
    } else if (modelDuration && videoStatus !== "loading") message = "3D 独立时钟 · 无可用视频，按动画原始秒数播放。";
    setNotice(el.tSync, message, kind);
  }
  function setModelTime(time) {
    const s = sceneState;
    if (!s?.mixer || !s.action) return;
    // Never normalize/stretch a clip. A mismatch is explicitly labeled approximate,
    // not synchronized; its raw-second position remains useful for manual inspection.
    const t = Math.max(0, Math.min(modelDuration, time));
    s.action.paused = false;
    s.action.enabled = true;
    s.mixer.setTime(t);
  }
  function drawScene() {
    const s = sceneState;
    if (!s || !visible() || el.outThreePane.hidden) return;
    const rect = el.tViewport.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;
    s.controls.update();
    s.renderer.render(s.scene, s.camera);
  }
  function stopLoop() {
    if (animationFrame) cancelAnimationFrame(animationFrame);
    animationFrame = 0;
    previousTime = 0;
  }
  function pausePlayback() {
    playing = false;
    el.outVideo.pause();
    stopLoop();
    updateTimeline();
    setModelTime(position());
    drawScene();
  }
  function pause() {
    if (project !== (current() || null)) {
      ++listVersion;
      listAbort?.abort();
      listAbort = null;
      refreshPending = null;
      project = current() || null;
      outputs = [];
      selected = null;
      clearResult(project ? "项目已切换，进入结果页后加载。" : "尚未选择项目。选择项目后查看结果。");
      el.outList.innerHTML = '<p class="empty-state">尚未加载当前项目的结果。</p>';
      el.outCount.textContent = "";
      el.outSearch.value = "";
      el.outFilter.value = "all";
      el.outRefresh.disabled = false;
      host.setAttribute("aria-busy", "false");
      setNotice(el.outNotice, "");
      return;
    }
    pausePlayback();
  }
  function tick(now) {
    animationFrame = 0;
    if (!playing || !visible() || project !== current()) { pause(); return; }
    if (!videoMaster()) {
      const dt = previousTime ? Math.min(0.25, (now - previousTime) / 1000) : 0;
      clockTime = Math.min(duration(), clockTime + dt * playbackRate);
    }
    previousTime = now;
    setModelTime(position());
    updateTimeline();
    drawScene();
    if (!videoMaster() && clockTime >= duration()) { pause(); return; }
    animationFrame = requestAnimationFrame(tick);
  }
  function beginLoop() {
    if (!animationFrame && playing) animationFrame = requestAnimationFrame(tick);
  }
  async function play() {
    if (!duration() || !visible()) return;
    if (position() >= duration() - 0.001) seek(0);
    const version = viewVersion;
    playing = true;
    previousTime = 0;
    updateTimeline();
    if (videoMaster()) {
      try {
        el.outVideo.playbackRate = playbackRate;
        await el.outVideo.play();
        if (!activeView(version)) return;
        if (!playing || !visible()) { el.outVideo.pause(); return; }
      } catch (e) {
        if (!activeView(version)) return;
        playing = false;
        reportAsset(`视频无法开始播放：${errorText(e)}`);
        updateTimeline();
        return;
      }
    }
    beginLoop();
  }
  function seek(time) {
    const next = Math.max(0, Math.min(duration() || 0, Number(time) || 0));
    clockTime = next;
    if (videoMaster()) {
      try { el.outVideo.currentTime = next; }
      catch (e) { reportAsset(`视频定位失败：${errorText(e)}`); }
    }
    setModelTime(next);
    updateTimeline();
    drawScene();
  }

  function disposeObject(root, extraRoots = []) {
    const roots = new Set([root, ...extraRoots].filter(Boolean));
    if (!roots.size) return;
    const geometries = new Set();
    const materials = new Set();
    const textures = new Set();
    const skeletons = new Set();
    for (const scene of roots) scene.traverse((node) => {
      if (node.geometry) geometries.add(node.geometry);
      if (node.skeleton) skeletons.add(node.skeleton);
      for (const material of (Array.isArray(node.material) ? node.material : [node.material])) {
        if (!material) continue;
        materials.add(material);
        for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
        for (const uniform of Object.values(material.uniforms || {})) if (uniform?.value?.isTexture) textures.add(uniform.value);
      }
    });
    for (const skeleton of skeletons) skeleton.dispose?.();
    for (const geometry of geometries) geometry.dispose();
    const bitmaps = new Set();
    for (const texture of textures) {
      texture.dispose();
      for (const image of (Array.isArray(texture.source?.data) ? texture.source.data : [texture.source?.data])) {
        if (image && typeof image.close === "function" && !bitmaps.has(image)) { image.close(); bitmaps.add(image); }
      }
    }
    for (const material of materials) material.dispose();
  }
  function disposeThree() {
    ++glbVersion;
    const s = sceneState;
    sceneState = null;
    if (s) {
      s.mixer?.stopAllAction();
      if (s.root) s.mixer?.uncacheRoot(s.root);
      s.controls.dispose();
      disposeObject(s.scene, s.extraRoots);
      s.renderer.renderLists?.dispose();
      s.renderer.dispose();
      s.renderer.domElement.remove();
      s.renderer.forceContextLoss?.();
    }
    el.tViewport.replaceChildren();
    modelDuration = 0;
    glbStatus = "none";
  }
  function clearResult(message) {
    ++viewVersion;
    curveAbort?.abort();
    curveAbort = null;
    pausePlayback();
    disposeThree();
    el.outVideo.onloadedmetadata = null;
    el.outVideo.ondurationchange = null;
    el.outVideo.onloadeddata = null;
    el.outVideo.onerror = null;
    el.outVideo.onended = null;
    el.outVideo.ontimeupdate = null;
    el.outVideo.onpause = null;
    el.outVideo.onplay = null;
    el.outVideo.removeAttribute("src");
    el.outVideo.load();
    videoDuration = 0;
    videoStatus = "none";
    clockTime = 0;
    videoFile = null;
    glbFile = null;
    result = null;
    curve = null;
    assetMessages = [];
    stableBusy = false;
    el.outDetail.hidden = true;
    el.outEmpty.hidden = false;
    el.outEmpty.textContent = message || "选择一个结果查看。";
    el.outTitle.textContent = "";
    el.outSummary.replaceChildren();
    el.outFiles.replaceChildren();
    el.outRanges.replaceChildren();
    el.outCurve.hidden = true;
    el.outCurveState.textContent = "";
    el.outDiagnostics.open = false;
    el.outStableDetails.open = false;
    el.outStableState.textContent = "";
    el.outMainExport.removeAttribute("href");
    el.outMainExport.hidden = true;
    setNotice(el.outAssetNotice, "");
    setNotice(el.tSync, "");
    updateTimeline();
  }

  function resizeThree() {
    const s = sceneState;
    if (!s || !visible() || el.outThreePane.hidden) return;
    const { width, height } = el.tViewport.getBoundingClientRect();
    if (!Number.isFinite(width) || !Number.isFinite(height) || width < 2 || height < 2) return;
    const w = Math.floor(width), h = Math.floor(height);
    if (s.width !== w || s.height !== h) {
      s.width = w; s.height = h;
      s.renderer.setSize(w, h, false);
      s.camera.aspect = w / h;
      s.camera.updateProjectionMatrix();
    }
    drawScene();
  }
  function ensureGlb() {
    if (!result || !glbFile || glbStatus !== "idle" || !visible() || el.outThreePane.hidden) return;
    const { width, height } = el.tViewport.getBoundingClientRect();
    if (width < 2 || height < 2) return;
    const version = viewVersion;
    const loadVersion = ++glbVersion;
    const p = project, name = selected;
    glbStatus = "loading";
    threeState("正在加载 3D 动画…");
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(Math.floor(width), Math.floor(height), false);
      renderer.domElement.style.cssText = "display:block;width:100%;height:100%";
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0xd4d8dd);
      const camera = new THREE.PerspectiveCamera(35, width / height, 0.01, 200);
      camera.position.set(3, 1.6, 3);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = false;
      scene.add(new THREE.HemisphereLight(0xffffff, 0x667080, 2.2));
      const sun = new THREE.DirectionalLight(0xffffff, 2.2);
      sun.position.set(3, 6, 4);
      scene.add(sun);
      const grid = new THREE.GridHelper(20, 40, 0x89909a, 0xb0b7bf);
      scene.add(grid);
      const s = { renderer, scene, camera, controls, grid, root: null, extraRoots: [], mixer: null, action: null, width: Math.floor(width), height: Math.floor(height) };
      sceneState = s;
      el.tViewport.append(renderer.domElement);
      controls.addEventListener("change", () => {
        if (!playing && sceneState === s && visible() && !el.outThreePane.hidden) s.renderer.render(s.scene, s.camera);
      });
      renderer.domElement.addEventListener("webglcontextlost", (event) => {
        event.preventDefault();
        if (sceneState !== s || !activeView(version, p, name)) return;
        glbStatus = "error";
        pause();
        threeState("3D 上下文已丢失，请刷新结果重试。视频仍可使用。");
        reportAsset("WebGL 上下文丢失，3D 预览不可用。");
      });
      const manager = new THREE.LoadingManager();
      manager.onError = (url) => {
        if (activeView(version, p, name) && loadVersion === glbVersion) reportAsset(`3D 资源加载失败：${url}`);
      };
      const requestedUrl = baseUrl() + urlPart(glbFile) + `?v=${encodeURIComponent(result.mtime || 0)}`;
      new GLTFLoader(manager).load(requestedUrl, (gltf) => {
        if (!activeView(version, p, name) || loadVersion !== glbVersion || sceneState !== s) {
          disposeObject(gltf.scene, gltf.scenes || []);
          return;
        }
        try {
          s.root = gltf.scene;
          s.extraRoots = (gltf.scenes || []).filter((root) => root !== gltf.scene);
          s.scene.add(gltf.scene);
          const box = new THREE.Box3().setFromObject(gltf.scene);
          if (!box.isEmpty()) {
            const size = box.getSize(new THREE.Vector3()).length() || 1;
            const centre = box.getCenter(new THREE.Vector3());
            s.grid.position.y = box.min.y;
            s.controls.target.copy(centre);
            s.camera.position.copy(centre).add(new THREE.Vector3(size * 0.9, size * 0.45, size * 0.9));
            s.camera.near = Math.max(size / 1000, 0.0001);
            s.camera.far = Math.max(size * 100, 100);
            s.camera.updateProjectionMatrix();
          }
          const clip = gltf.animations.find((animation) => positive(animation.duration));
          if (clip) {
            modelDuration = clip.duration;
            s.mixer = new THREE.AnimationMixer(gltf.scene);
            s.action = s.mixer.clipAction(clip);
            s.action.setLoop(THREE.LoopOnce, 1);
            s.action.clampWhenFinished = true;
            s.action.play();
            threeState("");
          } else {
            modelDuration = 0;
            threeState("此 GLB 没有可播放动画（可查看静态模型）。");
          }
          glbStatus = "ready";
          updateSyncNotice();
          setModelTime(position());
          updateTimeline();
          resizeThree();
        } catch (e) {
          disposeThree();
          glbStatus = "error";
          threeState(`3D 预览失败：${errorText(e)}`);
          reportAsset(`3D 预览失败：${errorText(e)}`);
          updateTimeline();
        }
      }, undefined, (e) => {
        if (!activeView(version, p, name) || loadVersion !== glbVersion) return;
        disposeThree();
        glbStatus = "error";
        threeState("3D 文件加载失败。可继续观看视频或下载文件。");
        reportAsset(`3D 文件加载失败：${errorText(e)}`);
        updateSyncNotice();
        updateTimeline();
      });
    } catch (e) {
      if (sceneState) disposeThree(); else renderer?.dispose();
      glbStatus = "error";
      threeState("此浏览器无法使用 WebGL，3D 预览不可用；视频和文件下载仍可使用。");
      reportAsset(`无法创建 WebGL 预览：${errorText(e)}`);
      updateTimeline();
    }
  }
  function setMode(next) {
    mode = next;
    el.outStage.dataset.mode = mode;
    el.outVideoPane.hidden = mode === "3d";
    el.outThreePane.hidden = mode === "video";
    // Inline grid/size contract keeps the two panes equal-height even before app CSS loads.
    el.outStage.style.gridTemplateColumns = mode === "compare" ? "repeat(2,minmax(0,1fr))" : "minmax(0,1fr)";
    for (const button of el.outModes.querySelectorAll("button")) button.setAttribute("aria-pressed", String(button.dataset.outMode === mode));
    el.outPreviewHint.textContent = mode === "compare" ? "左：诊断/对照视频；右：可旋转的 3D 模型。" : mode === "3d" ? "拖动旋转，滚轮缩放；播放与定位使用下方统一时间轴。" : "诊断/对照视频；使用下方统一时间轴。";
    resize();
  }
  function setupVideo(version) {
    if (!videoFile) {
      videoStatus = "none";
      videoState("此结果没有诊断或对照视频。");
      return;
    }
    videoStatus = "loading";
    videoState("正在加载视频…");
    const p = project, name = selected;
    el.outVideo.onloadedmetadata = el.outVideo.ondurationchange = () => {
      if (!activeView(version, p, name)) return;
      videoDuration = positive(el.outVideo.duration);
      if (!videoDuration) return;
      videoStatus = "ready";
      clockTime = el.outVideo.currentTime || 0;
      updateSyncNotice();
      setModelTime(position());
      updateTimeline();
      drawScene();
    };
    el.outVideo.onloadeddata = () => {
      if (activeView(version, p, name)) videoState("");
    };
    el.outVideo.onerror = () => {
      if (!activeView(version, p, name)) return;
      pause();
      videoStatus = "error";
      videoDuration = 0;
      clockTime = 0;
      const code = el.outVideo.error?.code;
      videoState("视频无法加载或解码。可尝试下载文件，或切换到 3D。");
      reportAsset(`视频加载失败${code ? `（媒体错误 ${code}）` : ""}：${videoFile}`);
      updateSyncNotice();
      updateTimeline();
    };
    el.outVideo.ontimeupdate = () => {
      if (!activeView(version, p, name)) return;
      setModelTime(position());
      updateTimeline();
      if (!playing) drawScene();
    };
    el.outVideo.onended = () => { if (activeView(version, p, name)) pause(); };
    el.outVideo.onpause = () => {
      if (activeView(version, p, name) && playing) {
        playing = false;
        stopLoop();
        updateTimeline();
      }
    };
    el.outVideo.onplay = () => {
      if (!activeView(version, p, name) || !visible()) { el.outVideo.pause(); return; }
      playing = true;
      beginLoop();
      updateTimeline();
    };
    el.outVideo.src = baseUrl() + urlPart(videoFile) + `?v=${encodeURIComponent(result.mtime || 0)}`;
    el.outVideo.load();
  }

  function renderList() {
    const query = el.outSearch.value.trim().toLocaleLowerCase();
    const filter = el.outFilter.value;
    const shown = outputs.filter((o) => {
      if (!o.name.toLocaleLowerCase().includes(query)) return false;
      if (filter === "review") return o.check?.same_move === false || o.summary?.status === "needs_review";
      if (filter === "passed") return o.check?.same_move === true && o.summary?.status !== "needs_review";
      if (filter === "delivery") return safeFiles(o.files).some((f) => /\.(fbx|bvh|blend)$/i.test(f));
      if (filter === "stable") return Boolean(o.stability || o.summary?.stabilized_from);
      return true;
    });
    el.outCount.textContent = `${outputs.length} 个结果${shown.length !== outputs.length ? ` · 筛选后 ${shown.length} 个` : ""}`;
    el.outList.innerHTML = shown.length ? shown.map((o) => {
      const [label, kind] = checkLabel(o);
      return `<button type="button" class="item ${o.name === selected ? "sel" : ""}" data-out-name="${esc(o.name)}" aria-pressed="${o.name === selected}" style="display:block;width:100%;text-align:left"><strong>${esc(o.name)}</strong><span class="badge ${kind}">${esc(label)}</span><span class="hint" style="display:block">${esc(o.summary?.frames ?? "–")} 帧 · 重投影 ${fmt(o.summary?.reproj_px_median)} px${o.stability ? " · 稳定副本" : ""}</span></button>`;
    }).join("") : '<p class="empty-state">没有符合筛选的结果。</p>';
    for (const button of el.outList.querySelectorAll("button[data-out-name]")) button.addEventListener("click", () => select(button.dataset.outName));
  }
  function renderExports(o) {
    const files = safeFiles(o.files);
    const main = files.find((f) => f.toLowerCase() === `${o.name}.fbx`.toLowerCase()) || files.find((f) => /\.fbx$/i.test(f));
    const base = baseUrl();
    if (main) {
      el.outMainExport.href = base + urlPart(main);
      el.outMainExport.download = main;
      el.outMainExport.hidden = false;
      el.outMainExport.textContent = o.summary?.forced ? "下载 FBX（强制预览）" : "下载 FBX";
    }
    const delivery = files.filter((f) => f !== main && /\.(fbx|glb|bvh|blend)$/i.test(f));
    const diagnostic = files.filter((f) => f !== main && !delivery.includes(f));
    const links = (group) => group.map((f) => `<a href="${esc(base + urlPart(f))}" download="${esc(f)}">${esc(f)}</a>`).join(" · ");
    el.outFiles.innerHTML = `${!main ? '<span class="hint">此结果没有 FBX 交付文件。</span>' : ""}${delivery.length ? `<details><summary>其他交付格式（${delivery.length}）</summary><div class="toolbar">${links(delivery)}</div></details>` : ""}${diagnostic.length ? `<details><summary>诊断与数据文件（${diagnostic.length}）</summary><div class="toolbar">${links(diagnostic)}</div></details>` : ""}${!files.length ? '<p class="hint">此目录尚无可下载文件，可能仍在写入；请稍后刷新。</p>' : ""}`;
  }
  function renderSummary(o) {
    const s = o.summary || {}, c = o.check || {}, st = o.stability;
    const rows = [
      ["任务产物状态", s.status || (s.frames ? "已保存结果" : "未提供（目录可能仍在生成）")],
      ["帧数 / fps", `${s.frames ?? "–"} / ${fmt(s.fps)}`],
      ["双视角几何检查", c.same_move === true ? `通过 · 异常帧 ${c.bad_frames ?? "–"}；不代表动作真值正确` : c.same_move === false ? `未通过 · 异常帧 ${c.bad_frames ?? "–"}；不等同于已确认动作不同` : "尚无检查结论；不能视作通过"],
      ["近 / 远标签修正", `${c.label_swaps_fixed ?? "–"} 次`],
      ["重投影误差", `中位数 ${fmt(s.reproj_px_median)} px · p90 ${fmt(s.reproj_px_p90)} px`],
      ["处理耗时", `${fmt(s.seconds, 0)} s`],
      ["求解模式", s.solver === "temporal" ? "实验：可信度融合 + 联合时序；无自动接触" : s.solver === "legacy" ? "旧版" : "未提供"],
    ];
    if (st) {
      const before = positive(st.before?.local_euler_accel_rms_deg_per_frame2);
      const after = Number(st.after?.local_euler_accel_rms_deg_per_frame2);
      rows.push(["稳定副本", `源：${st.source ?? "–"} · ${fmt(st.cutoff_hz)} Hz · 旋转二阶变化 RMS 减少 ${before && Number.isFinite(after) ? fmt(100 * (1 - after / before), 1) : "–"}%`]);
      rows.push(["动作保留（相对原求解）", `根部高度幅度 ${st.root_height_range_ratio == null ? "–" : fmt(100 * st.root_height_range_ratio, 1)}% · 低位中心时刻变化 ${fmt(st.low_hold_centre_shift_seconds, 3)} s；不是与真值比较`]);
      rows.push(["稳定性指标说明", "衡量平滑度，不衡量动作真值精度；未增加脚锁地。"]);
    }
    const d = o.diagnostics;
    if (d) {
      rows.push(["异常段关键点偏差", (d.keypoints || []).slice(0, 5).map((k) => `${k.name} ${fmt(k.flagged_segment_median_ray_pct, 1)}%`).join(" · ") || "暂无数据"]);
      rows.push(["如何判断", "诊断视频的红圈为高偏差点，黄线为另一机位的极线约束。遮挡、识别错误和视频不一致都可能造成偏差；强制导出只供诊断。"]);
    }
    el.outSummary.innerHTML = rows.map(([name, value]) => `<b>${esc(name)}</b><span>${esc(value)}</span>`).join("");
    const ranges = Array.isArray(d?.bad_ranges) ? d.bad_ranges : [];
    el.outRanges.innerHTML = ranges.length ? `<h4>异常时间段 · 点击定位</h4>${ranges.map((r) => {
      const start = Number(r.start_seconds), end = Number(r.end_seconds);
      return Number.isFinite(start) && Number.isFinite(end) ? `<a href="#tSeek" data-out-time="${start}" aria-label="跳到异常时间段 ${fmt(start)} 至 ${fmt(end)} 秒">${fmt(start)}–${fmt(end)} s${r.start_frame != null ? `（帧 ${esc(r.start_frame)}–${esc(r.end_frame)}）` : ""}</a>` : "";
    }).join(" · ")}` : `<p class="hint">${d ? "诊断未标记异常时间段（不等于质量已通过）。" : "尚无异常时间段诊断。"}</p>`;
    for (const anchor of el.outRanges.querySelectorAll("a[data-out-time]")) anchor.addEventListener("click", (event) => {
      event.preventDefault();
      pause();
      if (duration()) {
        seek(Number(anchor.dataset.outTime));
        el.tSeek.focus();
      } else reportAsset("暂无可定位的预览时间轴；请等待视频/3D 加载，或下载诊断文件查看该时间段。");
    });
  }
  function drawCurve() {
    if (!curve || el.outDetail.hidden || !el.outDiagnostics.open || !visible()) return;
    const residual = (curve.residual || []).map((value, index) => [index, value == null || !Number.isFinite(Number(value)) ? null : 100 * Number(value)]);
    if (!residual.some((point) => point[1] != null)) {
      el.outCurve.hidden = true;
      el.outCurveState.textContent = "此结果没有逐帧射线偏差数据，不能据此判断通过。";
      return;
    }
    const bands = [];
    (curve.bad || []).forEach((bad, index) => { if (bad) bands.push([index - 0.5, index + 0.5]); });
    el.outCurve.hidden = false;
    if (el.outCurve.clientWidth < 2) return;
    el.outCurve.style.height = `${CHART_HEIGHT}px`;
    el.outCurve.dataset.height = String(CHART_HEIGHT);
    // Reset the intrinsic height before the parent's DPR-aware chart. It must not compound.
    el.outCurve.height = CHART_HEIGHT;
    try {
      chart(el.outCurve, [{ name: "射线偏差 %", color: "#e0a84a", data: residual }], { y0: 0, xlabel: "frame", bands, height: CHART_HEIGHT });
      el.outCurveState.textContent = "";
    } catch (e) {
      el.outCurve.hidden = true;
      el.outCurveState.textContent = `曲线绘制失败：${errorText(e)}`;
    }
  }
  async function loadCurve(version) {
    const controller = new AbortController();
    curveAbort = controller;
    const p = project, name = selected;
    el.outCurveState.textContent = "正在加载逐帧检查曲线…";
    try {
      const data = await api(`/api/projects/${urlPart(p)}/out/${urlPart(name)}/curve`, { signal: controller.signal });
      if (!activeView(version, p, name)) return;
      curve = data || {};
      drawCurve();
    } catch (e) {
      if (!activeView(version, p, name) || controller.signal.aborted) return;
      curve = null;
      el.outCurve.hidden = true;
      el.outCurveState.textContent = `检查曲线加载失败：${errorText(e)}`;
    }
  }
  function uniqueCopyName(source) {
    let name = `${source}_stable`, index = 2;
    while (outputs.some((o) => o.name === name) || pendingCopies.has(`${project}/${name}`)) name = `${source}_stable_${index++}`;
    return name;
  }
  function showResult(o) {
    clearResult();
    selected = o.name;
    result = o;
    const version = viewVersion;
    el.outEmpty.hidden = true;
    el.outDetail.hidden = false;
    el.outTitle.textContent = o.name;
    const [label, kind] = checkLabel(o);
    el.outBadge.textContent = label;
    el.outBadge.className = `badge ${kind}`;
    const failed = o.check?.same_move === false || o.summary?.status === "needs_review";
    el.outConclusion.textContent = failed ? (o.summary?.forced ? "检查未通过；这是强制生成的诊断预览，不应当作已验收动作交付。" : "检查需复核。已保存诊断不代表生成了可交付动作；先查看异常时间段和诊断视频。") : o.check?.same_move === true ? "双视角几何检查通过，但不代表动作质量、脚接触或识别精度已验收。请人工检查视频与 3D。" : "尚无几何检查结论。文件存在、任务完成或稳定副本生成均不能代替动作质量检查。";
    el.outConclusion.dataset.kind = failed ? "warning" : "info";
    renderExports(o);
    renderSummary(o);
    if (el.outKeyframes) {
      const kf = o.has_keyframes || {};
      el.outKeyframes.hidden = !openKeyframes;
      el.outKeyframes.textContent = kf.artist ? "关键帧标注（已有标注）→" : kf.auto ? "关键帧候选与标注 →" : "关键帧标注 →";
    }
    const files = safeFiles(o.files);
    videoFile = files.includes("lab.mp4") ? "lab.mp4" : files.includes("diagnostic.mp4") ? "diagnostic.mp4" : files.find((f) => /\.mp4$/i.test(f)) || null;
    glbFile = files.find((f) => f.toLowerCase() === `${o.name}.glb`.toLowerCase()) || files.find((f) => /\.glb$/i.test(f)) || null;
    glbStatus = glbFile ? "idle" : "none";
    threeState(glbFile ? "切换到 3D 或对照模式后加载模型。" : failed ? "检查未通过，未生成可用的 3D 动画。" : "此结果没有 GLB 三维预览文件。");
    setupVideo(version);
    const initialMode = videoFile && glbFile ? "compare" : glbFile ? "3d" : "video";
    for (const button of el.outModes.querySelectorAll("button")) button.disabled = button.dataset.outMode === "video" ? !videoFile : button.dataset.outMode === "3d" ? !glbFile : !(videoFile && glbFile);
    el.outStableName.value = uniqueCopyName(o.name);
    el.outStableRun.disabled = !files.includes("motion.npz");
    el.outStableName.disabled = el.outStableCutoff.disabled = !files.includes("motion.npz");
    el.outStableState.textContent = files.includes("motion.npz") ? `源结果：${o.name}。副本不会覆盖源结果。` : "此结果没有 motion.npz 求解动作，不能生成稳定副本。";
    setMode(initialMode);
    updateTimeline();
    loadCurve(version);
  }

  async function refresh() {
    const p = current() || null;
    if (refreshPending && p === project) return refreshPending;
    const version = ++listVersion;
    listAbort?.abort();
    const controller = new AbortController();
    listAbort = controller;
    if (p !== project) {
      el.outSearch.value = "";
      el.outFilter.value = "all";
    }
    project = p;
    outputs = [];
    selected = null;
    clearResult(p ? "正在加载结果…" : "尚未选择项目。选择项目后查看结果。");
    el.outList.innerHTML = `<p class="empty-state">${p ? "正在加载结果…" : "尚未选择项目。"}</p>`;
    el.outCount.textContent = "";
    setNotice(el.outNotice, "");
    el.outRefresh.disabled = Boolean(p);
    host.setAttribute("aria-busy", p ? "true" : "false");
    if (!p) { refreshPending = null; return; }
    const work = (async () => {
      try {
        const response = await api(`/api/projects/${urlPart(p)}/outputs`, { signal: controller.signal });
        if (version !== listVersion || p !== current()) return;
        if (!Array.isArray(response)) throw new Error("结果列表格式无效");
        outputs = response.filter((o) => o && typeof o.name === "string").map((o) => ({ ...o, files: safeFiles(o.files) }));
        const preferred = desired.get(p);
        const o = outputs.find((entry) => entry.name === preferred) || outputs[0];
        if (!o) {
          clearResult("这个项目还没有结果。在“生成动画”页处理对应视频后，回到这里查看。");
          el.outList.innerHTML = '<p class="empty-state">还没有动画结果。</p>';
          el.outCount.textContent = "0 个结果";
          if (preferred) setNotice(el.outNotice, `尚未找到结果“${preferred}”，任务可能仍在运行。请稍后刷新。`, "info");
          return;
        }
        if (preferred && preferred !== o.name) setNotice(el.outNotice, `尚未找到指定结果“${preferred}”；当前显示“${o.name}”。保留目标选择，刷新后会再次查找。`, "info");
        selected = o.name;
        if (!preferred) desired.set(p, selected);
        renderList();
        showResult(o);
      } catch (e) {
        if (version !== listVersion || p !== current() || controller.signal.aborted) return;
        outputs = [];
        selected = null;
        clearResult("结果加载失败。请使用左侧“刷新”重试。");
        el.outList.innerHTML = '<p class="empty-state">无法获取结果列表。</p>';
        el.outCount.textContent = "";
        setNotice(el.outNotice, `加载结果失败：${errorText(e)}`, "error");
      } finally {
        if (version === listVersion) {
          refreshPending = null;
          el.outRefresh.disabled = false;
          host.setAttribute("aria-busy", "false");
        }
      }
    })();
    refreshPending = work;
    return work;
  }
  function select(name) {
    const p = current() || null;
    if (!p) return Promise.resolve();
    if (typeof name !== "string" || !name) return refresh();
    desired.set(p, name);
    if (project !== p || !outputs.length) return refresh();
    const o = outputs.find((entry) => entry.name === name);
    if (!o) return refresh();
    setNotice(el.outNotice, "");
    if (selected === name && result) return Promise.resolve();
    selected = name;
    renderList();
    showResult(o);
    return Promise.resolve();
  }
  function resize() {
    if (!visible()) { pause(); return; }
    if (project !== current()) { pause(); return; }
    ensureGlb();
    resizeThree();
    drawCurve();
  }

  el.outRefresh.addEventListener("click", refresh);
  el.outKeyframes?.addEventListener("click", () => { if (selected && openKeyframes) { pause(); openKeyframes(selected); } });
  el.outSearch.addEventListener("input", renderList);
  el.outFilter.addEventListener("change", renderList);
  el.outDiagnostics.addEventListener("toggle", () => { if (el.outDiagnostics.open) drawCurve(); });
  el.outModes.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-out-mode]");
    if (button && !button.disabled) setMode(button.dataset.outMode);
  });
  el.tPlay.addEventListener("click", () => playing ? pause() : play());
  el.tPrev.addEventListener("click", () => { const time = position(); pause(); seek(time - frameStep()); });
  el.tNext.addEventListener("click", () => { const time = position(); pause(); seek(time + frameStep()); });
  el.tSeek.addEventListener("input", () => { const time = Number(el.tSeek.value); pause(); seek(time); });
  el.tSpeed.addEventListener("change", () => {
    playbackRate = positive(el.tSpeed.value) || 1;
    el.outVideo.playbackRate = playbackRate;
  });
  el.outStableForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!result || !safeFiles(result.files).includes("motion.npz") || stableBusy) return;
    const p = project, source = selected, version = viewVersion;
    const name = el.outStableName.value.trim(), cutoff = Number(el.outStableCutoff.value);
    let message = "";
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name)) message = "副本名称须以字母或数字开头，只能包含字母、数字、_、-、.。";
    else if (name === source) message = "请使用新名称，不能覆盖源结果。";
    else if (outputs.some((o) => o.name === name) || pendingCopies.has(`${p}/${name}`)) message = "该结果名称已存在或已提交，请换一个名称以保留原结果。";
    else if (!Number.isFinite(cutoff) || cutoff < 0.25 || cutoff > 6) message = "截止频率必须介于 0.25 和 6 Hz 之间。";
    else if (fps() && cutoff >= fps() / 2) message = `截止频率必须低于 fps/2（${fmt(fps() / 2)} Hz）。`;
    if (message) { setNotice(el.outStableState, message, "error"); return; }
    stableBusy = true;
    el.outStableRun.disabled = true;
    setNotice(el.outStableState, `正在提交副本“${name}”…源“${source}”将保留。`, "info");
    try {
      const job = await submit(p, "stabilize", { source, name, cutoff });
      if (!job) throw new Error("任务未提交，请检查任务区或重试。");
      pendingCopies.add(`${p}/${name}`);
      // Remember it for a later refresh, but never switch project or replace the current source.
      if (activeView(version, p, source)) setNotice(el.outStableState, `已提交副本“${name}”${job.id ? `（任务 ${job.id}）` : ""}。源结果不变；完成后刷新列表查看。`, "success");
      emit(`已提交稳定副本 ${name}，保留源结果 ${source}。`, "success");
    } catch (e) {
      if (activeView(version, p, source)) setNotice(el.outStableState, `提交失败：${errorText(e)}`, "error");
    } finally {
      if (activeView(version, p, source)) {
        stableBusy = false;
        el.outStableRun.disabled = false;
      }
    }
  });

  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver((entries) => {
      if (!entries.some((entry) => entry.contentRect.width > 1 && entry.contentRect.height > 1)) return;
      resize();
    });
    observer.observe(el.outStage);
    observer.observe(host);
  }
  const visibilityObserver = new MutationObserver(() => {
    if (!visible()) pause();
    else resize();
  });
  visibilityObserver.observe(tab, { attributes: true, attributeFilter: ["class", "hidden", "style"] });
  document.addEventListener("visibilitychange", () => { if (document.hidden) pause(); else resize(); });
  window.addEventListener("resize", resize);
  return { refresh, pause, select, resize };
}
