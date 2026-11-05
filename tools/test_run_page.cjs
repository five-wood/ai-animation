#!/usr/bin/env node
'use strict';
// Run from any working directory: node tools/test_run_page.cjs
// No browser, network, packages or server mutations. This DOM intentionally models
// only the native elements used by run-page.js; layout/media decoding need a browser.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const sourcePath = path.join(__dirname, '..', 'video2rig', 'web', 'static', 'run-page.js');
const source = fs.readFileSync(sourcePath, 'utf8');
const executable = source.replace(/export\s+function\s+initRunPage\b/, 'function initRunPage');
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { await tick(); await tick(); await tick(); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
class Element {
  constructor(id = '', tag = 'DIV') {
    Object.assign(this, {id, tagName:tag, checked:false, hidden:false, disabled:false, attrs:{}, handlers:{}, children:[], textContent:'', className:'', files:[], open:false, _value:'', focused:false});
  }
  set value(value) {
    const text = String(value);
    this._value = this.tagName === 'SELECT' && !this.children.some(option => option.value === text) ? '' : text;
    if (this.type === 'file' && !text) this.files = [];
  }
  get value() { return this._value; }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); }
  fire(type) { return Promise.all((this.handlers[type] || []).map(fn => fn({target:this, preventDefault(){}}))); }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return this.attrs[key] ?? null; }
  hasAttribute(key) { return key in this.attrs; }
  removeAttribute(key) { delete this.attrs[key]; }
  replaceChildren(...children) { this.children = children; if (this.tagName === 'SELECT') this._value = children[0]?.value || ''; }
  focus() { this.focused = true; }
  pause() {}
  load() {}
  set src(value) { this.attrs.src = value; }
  get src() { return this.attrs.src || ''; }
}
class Root extends Element {
  constructor() { super(); this.nodes = {}; }
  set innerHTML(html) {
    this.nodes = {};
    for (const match of html.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
      const el = new Element(match[3], match[1].toUpperCase());
      el.hidden = /\bhidden\b/.test(match[2]);
      el.checked = /\bchecked\b/.test(match[2]);
      el.type = /\btype="([^"]+)"/.exec(match[2])?.[1];
      this.nodes[el.id] = el;
    }
    for (const match of html.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)) {
      const select = this.nodes[match[1]];
      for (const optionMatch of match[2].matchAll(/<option([^>]*)>([^<]*)<\/option>/g)) {
        const option = new Element('', 'OPTION');
        option.value = /\bvalue="([^"]*)"/.exec(optionMatch[1])?.[1] ?? optionMatch[2];
        option.textContent = optionMatch[2]; select.children.push(option);
      }
      select.value = select.children[0]?.value || '';
    }
  }
  querySelector(query) { return query === 'form' ? Object.values(this.nodes).find(el => el.tagName === 'FORM') : this.nodes[query.slice(1)] || null; }
}
function harness({initial = 'A', storage = {}} = {}) {
  const run = new Root(), create = new Root(), dialog = new Element('createDialog', 'DIALOG');
  dialog.setAttribute('aria-labelledby', 'createTitle');
  dialog.querySelector = query => query === '#createContent' ? create : null;
  const events = [];
  dialog.showModal = () => { dialog.open = true; events.push('open'); };
  dialog.close = () => { dialog.open = false; events.push('close'); };
  const requests = [], submissions = [], navigations = [], notifications = [], confirmations = [], uploads = [];
  const localData = {...storage};
  let current = initial, apiHook = null, submitHook = null, selectHook = null, navigateHook = null;
  const projects = [
    {name:'A', has_reader:true, has_reference:true, outputs:[], stage:'trained'},
    {name:'B', has_reader:true, has_reference:false, outputs:[], stage:'trained'},
    {name:'Empty', has_reader:false, has_reference:false, outputs:[], stage:'empty'},
  ];
  const listing = {models:[{name:'m.glb', size:100}], videos:[
    {name:'u.mp4', size:100, mtime:1},
    {name:'a.mp4', size:100, mtime:2, project:'A'},
    {name:'b.mp4', size:100, mtime:3, project:'B'},
  ]};
  class XHR {
    constructor() { this.upload = {}; }
    open(method, url) { this.method = method; this.url = url; }
    send(data) { this.data = data.values; uploads.push(this); }
  }
  const context = {
    console, Map, Set, JSON, String, Number, Array, Object, RegExp, Promise, encodeURIComponent,
    document:{
      querySelector:query => query === '#tab-run #generateContent' ? run : query === 'dialog#createDialog' ? dialog : null,
      createElement:tag => new Element('', tag.toUpperCase()),
    },
    localStorage:{getItem:key => localData[key] || null, setItem:(key,value) => { localData[key] = value; }},
    window:{confirm:text => { confirmations.push(text); return true; }},
    FormData:class { constructor(){ this.values = {}; } append(key, value){ this.values[key] = value; } },
    XMLHttpRequest:XHR,
  };
  vm.createContext(context);
  vm.runInContext(executable + '\nthis.initRunPage = initRunPage;', context, {filename:sourcePath});
  const page = context.initRunPage({
    current:() => current,
    projects:() => projects,
    api:async(url, options) => {
      requests.push({url, options});
      if (apiHook) { const value = apiHook(url, options); if (value !== undefined) return value; }
      if (url === '/api/inputs') return clone(listing);
      if (url === '/api/video-binding') {
        const body = JSON.parse(options.body);
        listing.videos.find(video => video.name === body.video).project = body.project;
        return {ok:true};
      }
      const name = decodeURIComponent(url.split('/').at(-1));
      const project = projects.find(row => row.name === name);
      if (!project) { const error = new Error('404 project not yet prepared'); error.status = 404; throw error; }
      return clone(project);
    },
    submit:async(project, command, args) => {
      submissions.push({project, command, args});
      return submitHook ? submitHook(project, command, args) : {id:'job-' + submissions.length};
    },
    selectProject:async name => {
      events.push('select:start:' + name);
      if (selectHook) await selectHook(name);
      current = name; page.syncProject(name); events.push('select:end:' + name);
    },
    navigate:async tab => { events.push('navigate:' + tab); navigations.push(tab); if (navigateHook) await navigateHook(tab); },
    notify:(text,type) => notifications.push({text,type}),
  });
  const $ = id => run.nodes[id] || create.nodes[id];
  async function choose(id, value, type = 'change') { $(id).value = value; await $(id).fire(type); }
  return {
    page, $, choose, run, create, dialog, projects, listing, localData, requests, submissions,
    navigations, notifications, confirmations, uploads, events,
    get current(){ return current; },
    switchProject(name){ current = name; page.syncProject(name); },
    setApiHook(fn){ apiHook = fn; }, setSubmitHook(fn){ submitHook = fn; },
    setSelectHook(fn){ selectHook = fn; }, setNavigateHook(fn){ navigateHook = fn; },
  };
}
const tests = [];
function test(name, fn) { tests.push({name, fn}); }

test('accessible dialog and matching gates; same-project binding avoids routine confirm', async () => {
  const h = harness(); await h.page.refresh();
  assert.equal(h.dialog.getAttribute('aria-labelledby'), 'createHeading');
  assert.equal(source.includes('考试'), false, 'use user-facing 验证 consistently');
  assert.equal(source.includes('aniProject'), false, 'no removed project selector');
  await h.choose('aniVideo', 'u.mp4'); assert.equal(h.$('aniRun').disabled, true);
  h.$('aniUnbound').checked = true; await h.$('aniUnbound').fire('change');
  assert.equal(h.$('aniRun').disabled, false, 'unbound requires acknowledgement but not saved binding');
  await h.choose('aniViewOrder', 'side-front'); await h.$('generateForm').fire('submit');
  assert.equal(h.submissions.at(-1).args.swap, true);
  assert.equal(h.submissions.at(-1).args.confirm_mismatch, false);
  assert.equal(h.requests.some(request => request.url === '/api/video-binding'), false);
  await h.choose('aniVideo', 'a.mp4'); assert.equal(h.$('aniRun').disabled, false);
  await h.$('generateForm').fire('submit'); assert.equal(h.confirmations.length, 0);
  await h.choose('aniVideo', 'b.mp4'); assert.equal(h.$('aniRun').disabled, true);
  assert.equal(h.$('runAdvanced').open, true);
  h.$('aniMismatch').checked = true; await h.$('aniMismatch').fire('change');
  await h.$('generateForm').fire('submit'); assert.equal(h.submissions.at(-1).args.confirm_mismatch, true);
  assert.equal(h.confirmations.length, 0, 'explicit mismatch checkbox is the confirmation');
});

test('per-project drafts and diagnostic reset/nonpersistence', async () => {
  const h = harness(); await h.page.refresh();
  await h.choose('aniVideo', 'u.mp4'); await h.choose('aniName', 'draft_A', 'input');
  h.$('aniUnbound').checked = true; h.$('aniForce').checked = true; h.$('aniMismatch').checked = true;
  await h.choose('aniSolver', 'temporal');
  h.switchProject('B'); assert.equal(h.$('aniName').value, ''); assert.equal(h.$('aniForce').checked, false);
  await h.choose('aniVideo', 'b.mp4'); await h.choose('aniName', 'draft_B', 'input');
  h.switchProject('A'); assert.equal(h.$('aniName').value, 'draft_A'); assert.equal(h.$('aniVideo').value, 'u.mp4');
  assert.equal(h.$('aniSolver').value, 'temporal');
  for (const id of ['aniForce','aniMismatch','aniUnbound']) assert.equal(h.$(id).checked, false, id + ' reset');
  const saved = JSON.parse(h.localData['v2r.run.v3']);
  assert.deepEqual(Object.keys(saved.projects.A).sort(), ['name','solver','video','viewOrder']);
  const resumed = harness({storage:h.localData}); await resumed.page.refresh();
  assert.equal(resumed.$('aniName').value, 'draft_A'); assert.equal(resumed.$('aniForce').checked, false);
});

test('manual association replacement requires explicit global risk confirmation', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'b.mp4');
  await h.$('bindVideo').fire('click'); await settle();
  assert.equal(h.confirmations.length, 1);
  assert.match(h.confirmations[0], /全局人工关联/);
  assert.equal(h.listing.videos.find(video => video.name === 'b.mp4').project, 'A');
  assert.equal(h.$('bindVideo').disabled, true);
  const request = h.requests.find(row => row.url === '/api/video-binding');
  assert.deepEqual(JSON.parse(request.options.body), {video:'b.mp4', project:'A'});
});

test('busy lock prevents double submit and rejected jobs preserve inputs', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4');
  await h.choose('aniName', 'keep_on_failure', 'input');
  const waiting = deferred(); h.setSubmitHook(() => waiting.promise);
  const first = h.$('generateForm').fire('submit'); await tick();
  assert.equal(h.$('aniRun').disabled, true);
  await h.$('generateForm').fire('submit'); assert.equal(h.submissions.length, 1);
  waiting.resolve(null); await first;
  assert.equal(h.$('aniName').value, 'keep_on_failure'); assert.match(h.$('runStatus').textContent, /未被接受/);
  h.setSubmitHook(async () => { throw new Error('offline'); }); await h.$('generateForm').fire('submit');
  assert.equal(h.$('aniName').value, 'keep_on_failure'); assert.match(h.$('runStatus').textContent, /offline/);
});

test('latest refresh wins, current-project drafts win, stale fetch errors stay scoped', async () => {
  const h = harness(); await h.page.refresh();
  const first = deferred(), second = deferred(); let calls = 0;
  h.setApiHook(url => url === '/api/inputs' ? (++calls === 1 ? first.promise : second.promise) : undefined);
  const r1 = h.page.refresh(), r2 = h.page.refresh();
  const newest = clone(h.listing); newest.videos.push({name:'newest.mp4', size:1, mtime:8});
  second.resolve(newest); await r2; first.resolve(clone(h.listing)); await r1;
  assert.equal(h.$('aniVideo').children.some(option => option.value === 'newest.mp4'), true);
  const pending = deferred(); h.setApiHook(url => url === '/api/inputs' ? pending.promise : undefined);
  const switched = h.page.refresh(); h.switchProject('B'); await h.choose('aniVideo', 'b.mp4');
  pending.resolve(clone(h.listing)); await switched; assert.equal(h.$('aniVideo').value, 'b.mp4');
  const failing = deferred(); h.setApiHook(url => url === '/api/inputs' ? failing.promise : undefined);
  const oldFailure = h.page.refresh().catch(() => {}); h.switchProject('A');
  failing.reject(new Error('old project failed')); await oldFailure;
  assert.equal(h.$('runStatus').hidden, true, 'old request must not overwrite new project status');
});

test('project detail response cannot overwrite a newly selected project', async () => {
  const h = harness(); const oldDetail = deferred();
  h.setApiHook(url => url === '/api/projects/A' ? oldDetail.promise : undefined);
  const pending = h.page.refresh(); await tick(); h.switchProject('B'); await settle();
  oldDetail.resolve({...h.projects[0], model_name:'old_A.glb'}); await pending;
  assert.match(h.$('runProjectMeta').textContent, /^B ·/);
  assert.equal(h.$('runProjectMeta').textContent.includes('old_A.glb'), false);
});

test('upload progress and completion never select a video in a different project', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4');
  h.$('upVideo').files = [{name:'fresh.mp4'}]; await h.$('upVideo').fire('change'); await tick();
  const xhr = h.uploads[0]; assert.equal(xhr.method, 'POST'); assert.equal(xhr.url, '/api/upload');
  xhr.upload.onprogress({loaded:5, total:10, lengthComputable:true});
  assert.equal(h.$('videoUploadProgress').value, '50');
  h.switchProject('B'); await h.choose('aniVideo', 'b.mp4');
  assert.equal(h.$('videoUploadStatus').hidden, true);
  h.listing.videos.push({name:'fresh.mp4', size:10, mtime:9}); xhr.status = 200; xhr.onload(); await settle();
  assert.equal(h.$('aniVideo').value, 'b.mp4'); assert.equal(h.$('videoUploadStatus').hidden, true);
  h.switchProject('A'); assert.equal(h.$('aniVideo').value, 'fresh.mp4');
  assert.match(h.$('videoUploadStatus').textContent, /上传成功/);
});

test('failed upload preserves selection and offers explicit retry', async () => {
  const h = harness(); await h.page.refresh();
  const file = {name:'retry.mp4'}; h.$('upVideo').files = [file]; h.$('upVideo')._value = 'retry.mp4';
  await h.$('upVideo').fire('change'); await tick(); h.uploads[0].onerror(); await settle();
  assert.equal(h.$('upVideo').files[0], file); assert.equal(h.$('retryVideoUpload').hidden, false);
  assert.match(h.$('videoUploadStatus').textContent, /网络连接中断/);
  await h.$('retryVideoUpload').fire('click'); await tick(); assert.equal(h.uploads.length, 2);
  h.listing.videos.push({name:'retry.mp4', size:1, mtime:10}); h.uploads[1].status = 200; h.uploads[1].onload(); await settle();
  assert.equal(h.$('aniVideo').value, 'retry.mp4'); assert.equal(h.$('retryVideoUpload').hidden, true);
  assert.equal(h.$('upVideo').files.length, 0);
});

test('has_reader is required and the untrained CTA opens training', async () => {
  const h = harness({initial:'Empty'}); await h.page.refresh(); await h.choose('aniVideo', 'u.mp4');
  h.$('aniUnbound').checked = true; await h.$('aniUnbound').fire('change');
  assert.equal(h.$('aniRun').disabled, true); assert.equal(h.$('runUntrained').hidden, false);
  await h.$('runGoTrain').fire('click'); assert.equal(h.navigations.at(-1), 'train');
});

test('creation is independent, validates nonempty projects, sends exact flags and awaits selection', async () => {
  const h = harness(); await h.page.refresh(); await h.page.openCreate();
  await h.choose('newModel', 'm.glb'); await h.choose('newName', 'Fresh', 'input');
  await h.choose('newProfile', 'soft-clean'); h.$('newProc').checked = true; h.$('newDropProps').checked = false;
  const selected = deferred(); h.setSelectHook(() => selected.promise);
  const pending = h.$('createForm').fire('submit'); await tick();
  assert.equal(h.dialog.open, true); assert.equal(h.navigations.length, 0, 'navigation must await project selection');
  const submitted = h.submissions.at(-1);
  assert.equal(submitted.project, 'Fresh'); assert.equal(submitted.command, 'pipeline');
  assert.deepEqual(Array.from(submitted.args.extra), ['--facing=-Y','--up=+Z','--visual-profile','soft-clean','--procedural-material','--no-drop-props']);
  selected.resolve(); await pending; assert.equal(h.current, 'Fresh'); assert.equal(h.dialog.open, false);
  assert.deepEqual(h.events.slice(-3), ['select:end:Fresh','navigate:train','close']);
  assert.equal(h.confirmations.length, 0);
  await h.page.openCreate(); await h.choose('newName', 'A', 'input'); assert.equal(h.$('newRun').disabled, true);
  await h.choose('newName', 'Fresh', 'input'); assert.equal(h.$('newRun').disabled, true, 'accepted names cannot be resubmitted');
});

test('accepted creation navigation failure exposes retry without resubmission', async () => {
  const h = harness(); await h.page.refresh(); await h.page.openCreate();
  await h.choose('newModel', 'm.glb'); await h.choose('newName', 'RetrySelect', 'input');
  h.setSelectHook(async () => { throw new Error('selection failed'); });
  await h.$('createForm').fire('submit');
  assert.equal(h.dialog.open, true); assert.equal(h.$('newRun').disabled, true);
  assert.equal(h.$('createViewTrain').hidden, false); assert.match(h.$('createStatus').textContent, /任务已接受/);
  h.setSelectHook(null); await h.$('createViewTrain').fire('click');
  assert.equal(h.submissions.length, 1); assert.equal(h.dialog.open, false); assert.equal(h.navigations.at(-1), 'train');
});

test('dynamic project URL segments are encoded and server strings stay text-only', async () => {
  const h = harness({initial:'role #1'});
  h.projects.push({name:'role #1', has_reader:true, has_reference:true, outputs:[], model_name:'<img onerror=bad>'});
  await h.page.refresh();
  assert.equal(h.requests.some(request => request.url === '/api/projects/role%20%231'), true);
  assert.match(h.$('runReference').src, /role%20%231/);
  assert.equal(h.$('runProjectMeta').textContent.includes('<img onerror=bad>'), true);
});

(async () => {
  let passed = 0;
  for (const {name, fn} of tests) {
    try { await fn(); passed++; console.log('PASS ' + name); }
    catch (error) { console.error('FAIL ' + name); console.error(error); process.exitCode = 1; }
  }
  console.log(`\n${passed}/${tests.length} run-page tests passed. No remote operations performed.`);
})();
