// Three.js model viewer: FBX / GLB, orbit camera, optional own playback clock or external time.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { TGALoader } from "three/addons/loaders/TGALoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { h, ICON } from "./util.js?v=4";

// Missing or mislabelled .tga textures must not break a model: fall back to a plain grey texel.
class SafeTGALoader extends TGALoader {
  parse(buffer) {
    try { return super.parse(buffer); }
    catch { return { data: new Uint8Array([200, 200, 200, 255]), width: 1, height: 1, flipY: true, generateMipmaps: false }; }
  }
}

export function createViewer(container, { autoplay = true, hint = "拖动旋转 · 滚轮缩放 · 右键平移" } = {}) {
  const root = h("div.viewer", { style: { width: "100%", height: "100%" } });
  const state = h("div.v-state");
  const tools = h("div.v-tools", h("button.icon-btn", { title: "重置视角", "aria-label": "重置视角", html: ICON.reset, onclick: () => frame() }));
  root.append(state, h("div.v-hint", hint), tools);
  container.append(root);

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch {
    state.textContent = "当前浏览器不支持 3D 预览（WebGL 不可用）。";
    return { load: async () => { throw new Error("WebGL 不可用"); }, setTime() {}, dispose() { root.remove(); }, duration: 0, el: root };
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  root.prepend(renderer.domElement);
  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x50555e, 2.2));
  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const fill = new THREE.DirectionalLight(0xffffff, 0.9);
  fill.position.set(-4, 3, -3);
  scene.add(fill);
  const sun = new THREE.DirectionalLight(0xffffff, 2.2);
  sun.position.set(3, 6, 4);
  scene.add(sun);
  const grid = new THREE.GridHelper(10, 20, 0x8a8f96, 0x55595e);
  grid.material.transparent = true;
  grid.material.opacity = 0.35;
  scene.add(grid);

  let model = null, mixer = null, action = null, duration = 0, clipStart = 0;
  let box = null, disposed = false, raf = 0, needsRender = true, last = performance.now();
  const clock = { playing: autoplay, t: 0 };

  function size() {
    const w = Math.max(1, root.clientWidth), hgt = Math.max(1, root.clientHeight);
    renderer.setSize(w, hgt, false);
    camera.aspect = w / hgt;
    camera.updateProjectionMatrix();
    needsRender = true;
  }
  const ro = new ResizeObserver(size);
  ro.observe(root);
  size();
  controls.addEventListener("change", () => { needsRender = true; });

  function frame() {
    if (!box || box.isEmpty()) return;
    const s = box.getSize(new THREE.Vector3());
    const c = box.getCenter(new THREE.Vector3());
    const r = Math.max(s.x, s.y, s.z) || 1;
    const dist = r / (2 * Math.tan((camera.fov * Math.PI) / 360)) * 1.35;
    camera.near = Math.max(r / 1000, 0.001);
    camera.far = r * 100;
    camera.position.set(c.x + dist * 0.55, c.y + r * 0.25, c.z + dist * 0.85);
    camera.updateProjectionMatrix();
    controls.target.copy(c);
    controls.update();
    grid.scale.setScalar(Math.max(r / 4, 0.01));
    grid.position.set(c.x, box.min.y, c.z);
    needsRender = true;
  }

  function loop(now) {
    if (disposed) return;
    raf = requestAnimationFrame(loop);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (mixer && clock.playing && duration > 0) {
      clock.t = (clock.t + dt) % duration;
      mixer.setTime(clipStart + clock.t);
      needsRender = true;
    }
    if (controls.update()) needsRender = true;
    if (needsRender) { renderer.render(scene, camera); needsRender = false; }
  }
  raf = requestAnimationFrame(loop);

  function disposeModel() {
    if (!model) return;
    scene.remove(model);
    model.traverse((o) => {
      o.geometry?.dispose?.();
      const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
      for (const m of mats) { for (const v of Object.values(m)) if (v?.isTexture) v.dispose(); m.dispose(); }
    });
    model = null; mixer = null; action = null; duration = 0;
  }

  async function load(url, kind = "glb") {
    disposeModel();
    state.replaceChildren(h("div", h("div.spinner"), "正在加载模型…"));
    const manager = new THREE.LoadingManager();
    manager.addHandler(/\.tga$/i, new SafeTGALoader(manager));
    const loader = kind === "fbx" ? new FBXLoader(manager) : new GLTFLoader(manager);
    let obj, clips;
    try {
      const res = await new Promise((resolve, reject) => loader.load(url, resolve, undefined, reject));
      obj = kind === "fbx" ? res : res.scene;
      clips = (kind === "fbx" ? res.animations : res.animations) || [];
    } catch (e) {
      if (disposed) return null;
      state.textContent = "模型加载失败，请稍后重试。";
      throw e;
    }
    if (disposed) return null;
    obj.traverse((o) => {
      if (o.isMesh) {
        o.frustumCulled = false;
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of mats) {
          if (!m) continue;
          m.side = THREE.DoubleSide;
          // Many exported FBX materials carry a bogus transparency factor: render opaque, keep cut-out alpha.
          if (m.transparent || m.opacity < 1) {
            m.transparent = false;
            m.opacity = 1;
            m.alphaTest = m.alphaMap || m.map?.format === THREE.RGBAFormat ? 0.4 : 0;
            m.depthWrite = true;
          }
          if (m.isMeshPhongMaterial) m.shininess = Math.min(m.shininess ?? 30, 20);
          m.needsUpdate = true;
        }
      }
    });
    model = obj;
    scene.add(obj);
    const clip = clips.find((c) => c.duration > 0);
    if (clip) {
      mixer = new THREE.AnimationMixer(obj);
      action = mixer.clipAction(clip);
      action.play();
      // Exported clips start at scene frame 1 (= first video frame); find the real first key time.
      clipStart = Math.min(...clip.tracks.map((t) => t.times[0] ?? 0));
      duration = clip.duration - clipStart;
      mixer.setTime(clipStart);
    }
    obj.updateMatrixWorld(true);
    box = new THREE.Box3().setFromObject(obj, true);
    frame();
    state.replaceChildren();
    needsRender = true;
    return { duration, clipStart, hasAnimation: Boolean(clip) };
  }

  return {
    el: root,
    load,
    frame,
    get duration() { return duration; },
    get clipStart() { return clipStart; },
    setPlaying(p) { clock.playing = p; },
    // Absolute clip time in seconds (e.g. (frame + 1) / fps for v2r exports).
    setClipTime(t) {
      if (!mixer) return;
      clock.playing = false;
      mixer.setTime(Math.max(0, t));
      needsRender = true;
    },
    message(text) { state.textContent = text || ""; },
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf);
      ro.disconnect();
      disposeModel();
      controls.dispose();
      pmrem.dispose();
      renderer.dispose();
      renderer.forceContextLoss?.();
      root.remove();
    },
  };
}
