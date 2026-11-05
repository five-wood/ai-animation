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
    Object.assign(this, {id, tagName:tag, checked:false, hidden:false, disabled:false, attrs:{}, handlers:{}, children:[], _textContent:'', textWrites:0, className:'', files:[], open:false, _value:'', focused:false});
  }
  set textContent(value) { this._textContent = String(value); this.textWrites++; }
  get textContent() { return this._textContent; }
  set value(value) {
    const text = String(value);
    this._value = this.tagName === 'SELECT' && !this.children.some(option => option.value === text) ? '' : text;
    if (this.tagName === 'PROGRESS') this.attrs.value = text;
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
  const requests = [], submissions = [], navigations = [], notifications = [], confirmations = [], uploads = [], openedResults = [];
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
    openResult:async(project,name) => { openedResults.push({project,name}); },
  });
  const $ = id => run.nodes[id] || create.nodes[id];
  async function choose(id, value, type = 'change') { $(id).value = value; await $(id).fire(type); }
  return {
    page, $, choose, run, create, dialog, projects, listing, localData, requests, submissions,
    navigations, notifications, confirmations, uploads, events, openedResults,
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
  h.page.onTasks([{id:'job-1',project:'A',cmd:'animate',args:h.submissions[0].args,status:'done'}]);
  await h.choose('aniVideo', 'a.mp4'); assert.equal(h.$('aniRun').disabled, false);
  await h.$('generateForm').fire('submit'); assert.equal(h.confirmations.length, 0);
  h.page.onTasks([{id:'job-2',project:'A',cmd:'animate',args:h.submissions[1].args,status:'done'}]);
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

const generationTask = (patch = {}) => ({id:'task-1', project:'A', cmd:'animate', source:'web', read_only:false, can_cancel:true, status:'running', created:10, args:{video:'/srv/videos/a.mp4',name:'walk',solver:'legacy',swap:false}, ...patch});

test('accepted generation immediately creates a stable scoped task card and blocks duplicate work', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4');
  assert.equal(h.$('generationTaskCard').hidden, true);
  await h.$('generateForm').fire('submit');
  const card = h.$('generationTaskCard');
  assert.equal(card.hidden, false); assert.match(h.$('generationTaskStatus').textContent, /排队/);
  assert.match(h.$('generationTaskMeta').textContent, /项目：A.*a\.mp4.*输出：a/);
  assert.equal(h.$('aniRun').disabled, true);
  await h.$('generateForm').fire('submit'); assert.equal(h.submissions.length, 1);
  h.page.onTasks([]); assert.equal(h.$('generationTaskCard'), card); assert.equal(card.hidden, false);
  assert.equal(h.$('aniRun').disabled, true, 'an accepted task cannot disappear during a racing poll');
  h.switchProject('B'); await h.choose('aniVideo', 'b.mp4');
  assert.equal(card.hidden, true); assert.equal(h.$('aniRun').disabled, false);
  h.switchProject('A'); assert.equal(card.hidden, false); assert.equal(h.current, 'A');
  assert.equal(h.navigations.length, 0, 'acceptance and polling must not switch views');
});

test('accepted task records preserve server normalization and cannot switch a project chosen while submitting', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4'); await h.choose('aniSolver', 'temporal');
  const waiting = deferred(); h.setSubmitHook(() => waiting.promise);
  const pending = h.$('generateForm').fire('submit'); await tick(); h.switchProject('B');
  waiting.resolve({id:'normalized',status:'queued',args:{video:'/srv/a.mp4',name:'a_temporal'}}); await pending;
  assert.equal(h.current, 'B'); assert.equal(h.$('generationTaskCard').hidden, true); assert.equal(h.$('runStatus').hidden, true);
  h.switchProject('A'); assert.match(h.$('generationTaskMeta').textContent, /输出：a_temporal/);
  h.page.onTasks([generationTask({id:'normalized',status:'done',args:{video:'/srv/a.mp4',name:'a_temporal',solver:'temporal'}})]);
  await h.$('generationTaskResult').fire('click'); assert.deepEqual(h.openedResults.at(-1), {project:'A',name:'a_temporal'});
  assert.equal(h.navigations.length, 0);
});

test('task phase uses real counters only and unchanged polls do not rewrite live regions', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4');
  const card = h.$('generationTaskCard');
  const task = generationTask({phase:{key:'solve',label:'求解骨骼',current:25,total:100,unit:'帧',percent:97}});
  h.page.onTasks([task]);
  assert.equal(h.$('generationTaskPhase').textContent, '求解骨骼 · 25 / 100 帧');
  assert.equal(h.$('generationTaskProgress').hidden, false); assert.equal(h.$('generationTaskProgress').value, '25');
  assert.equal(card.getAttribute('aria-live'), null); assert.equal(card.getAttribute('role'), null);
  const live = h.$('generationTaskAnnouncement'), reason = h.$('runDisabledReason'), pair = h.$('pairWarning');
  const counts = [live.textWrites,reason.textWrites,pair.textWrites];
  h.page.onTasks([clone(task)]);
  assert.deepEqual([live.textWrites,reason.textWrites,pair.textWrites], counts);
  h.page.onTasks([{...task,phase:{key:'solve',label:'求解骨骼',percent:55}}]);
  assert.equal(h.$('generationTaskPhase').textContent, '求解骨骼'); assert.equal(h.$('generationTaskProgress').hidden, true);
  assert.equal(h.$('generationTaskProgress').hasAttribute('value'), false);
  for (const phase of [{current:1,total:0},{current:101,total:100},{current:-1,total:100},{current:'5',total:10},{current:NaN,total:10}]) {
    h.page.onTasks([{...task,phase:{label:'无可信计数',...phase}}]); assert.equal(h.$('generationTaskProgress').hidden, true);
  }
  h.page.onTasks([{...task,status:'done',phase:null}]);
  assert.match(live.textContent, /执行完成/); const completedWrites = live.textWrites;
  h.page.onTasks([{...task,status:'done',phase:null}]); assert.equal(live.textWrites, completedWrites);
  assert.equal(h.$('generationTaskCard'), card, 'polling must retain the exact card DOM node');
});

test('completion and review open the exact result, not an automatic navigation', async () => {
  const h = harness(); await h.page.refresh();
  const task = generationTask(); h.page.onTasks([task]); h.page.onTasks([{...task,status:'done'}]);
  assert.equal(h.$('generationTaskResult').hidden, false); assert.equal(h.$('generationTaskResult').textContent, '查看结果');
  assert.equal(h.openedResults.length, 0); await h.$('generationTaskResult').fire('click');
  assert.deepEqual(h.openedResults.at(-1), {project:'A',name:'walk'});
  const review = generationTask({id:'review',status:'needs_review',created:11,result_name:'diagnostic'});
  h.page.onTasks([review]); assert.equal(h.$('generationTaskResult').textContent, '查看诊断');
  assert.equal(h.$('generationTaskRestore').hidden, true); await h.$('generationTaskResult').fire('click');
  assert.deepEqual(h.openedResults.at(-1), {project:'A',name:'diagnostic'});
  assert.equal(h.current, 'A'); assert.equal(h.navigations.length, 0);
  h.page.onTasks([generationTask({id:'fallback',status:'completed',created:12,args:{video:'C:\\video\\clip.mp4',solver:'temporal'}})]);
  await h.$('generationTaskResult').fire('click'); assert.equal(h.openedResults.at(-1).name, 'clip_temporal', 'result path follows existing temporal POST normalization');
  h.page.onTasks([generationTask({id:'cli-fallback',source:'cli',read_only:true,status:'done',created:13,args:{video:'/srv/clip.mp4',solver:'temporal'}})]);
  await h.$('generationTaskResult').fire('click'); assert.equal(h.openedResults.at(-1).name, 'clip', 'external CLI fallback does not use Web POST normalization');
});

test('only current-project animation tasks enter the card; any active project task blocks generation', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4');
  h.page.onTasks([generationTask({project:'B'})]);
  assert.equal(h.$('generationTaskCard').hidden, true); assert.equal(h.$('aniRun').disabled, false);
  const train = generationTask({id:'training',cmd:'train'});
  h.page.onTasks([train]); assert.equal(h.$('generationTaskCard').hidden, true); assert.equal(h.$('aniRun').disabled, true);
  h.page.onTasks([{...train,status:'done'},generationTask({id:'old-animation',created:2,status:'done'})]);
  assert.equal(h.$('generationTaskCard').hidden, false); assert.match(h.$('generationTaskMeta').textContent, /old-animation/);
  assert.equal(h.$('aniRun').disabled, false);
  const active = generationTask({id:'active-old',created:1});
  h.page.onTasks([generationTask({id:'new-terminal',created:100,status:'done'}),active]);
  assert.match(h.$('generationTaskMeta').textContent, /active-old/);
  h.page.onTasks([{...active,status:'done'},generationTask({id:'new-terminal',created:100,status:'done'})]);
  assert.match(h.$('generationTaskMeta').textContent, /new-terminal/);
});

test('CLI task observations are read-only, block conflicts, and ended_unknown never becomes completion', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4');
  const cli = generationTask({id:'cli-1',source:'cli',read_only:true,can_cancel:true});
  h.page.onTasks([cli],{available:false,warning:'外部监视器降级'});
  assert.equal(h.$('aniRun').disabled, true); assert.match(h.$('runDisabledReason').textContent, /外部只读/);
  assert.match(h.$('generationTaskMeta').textContent, /外部只读观察/);
  assert.match(h.$('generationTaskMonitor').textContent, /降级/);
  assert.equal(Object.keys(h.run.nodes).some(id => /cancel/i.test(id)), false, 'generation card has no cancellation endpoint or control');
  h.page.onTasks([{...cli,status:'unknown'}],{available:false,warning:'进程可见性不足'});
  assert.equal(h.$('aniRun').disabled, true, 'loss of process visibility must not unlock conflicting work');
  assert.match(h.$('runDisabledReason').textContent, /不能确认已结束/);
  h.page.onTasks([{...cli,status:'ended_unknown',ended:20}],{available:true,warning:''});
  assert.match(h.$('generationTaskStatus').textContent, /结果未知/);
  assert.equal(h.$('generationTaskResult').hidden, true); assert.equal(h.$('generationTaskRestore').hidden, true);
  assert.equal(h.$('generationTaskMonitor').hidden, true); assert.equal(h.openedResults.length, 0);
  assert.equal(h.$('aniRun').disabled, false); assert.equal(h.submissions.length, 0);
});

test('missing observed active tasks remain unknown and cannot silently unlock conflicting generation', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4');
  const task = generationTask(); h.page.onTasks([task]); h.page.onTasks([]);
  assert.match(h.$('generationTaskStatus').textContent, /待确认/); assert.equal(h.$('generationTaskResult').hidden, true);
  assert.equal(h.$('aniRun').disabled, true); assert.match(h.$('runDisabledReason').textContent, /状态尚未确认/);
  h.page.onTasks([{...task,status:'interrupted'}]); assert.equal(h.$('aniRun').disabled, false);
});

test('failed cancelled and interrupted animation actions only restore safe inputs with collision-free new names', async () => {
  for (const status of ['failed','cancelled','interrupted']) {
    const h = harness(); h.projects[0].outputs = ['walk','walk_retry','walk_retry_2']; await h.page.refresh();
    const failed = generationTask({status,args:{video:'C:\\server\\videos\\u.mp4',name:'walk',solver:'temporal',swap:true,force:true,confirm_mismatch:true}});
    const reserved = generationTask({id:'other-failed',status:'failed',created:1,args:{video:'a.mp4',name:'walk_retry_3'}});
    h.page.onTasks([failed,reserved]);
    h.$('aniForce').checked = h.$('aniUnbound').checked = h.$('aniMismatch').checked = true;
    assert.equal(h.$('generationTaskRestore').hidden, false); await h.$('generationTaskRestore').fire('click');
    assert.equal(h.$('aniVideo').value, 'u.mp4'); assert.equal(h.$('aniName').value, 'walk_retry_4');
    assert.equal(h.$('aniSolver').value, 'temporal'); assert.equal(h.$('aniViewOrder').value, 'side-front');
    for (const id of ['aniForce','aniUnbound','aniMismatch']) assert.equal(h.$(id).checked, false, status + ': ' + id);
    assert.match(h.$('runStatus').textContent, /未提交任何任务/); assert.equal(h.submissions.length, 0);
    await h.$('generateForm').fire('submit'); assert.equal(h.submissions.length, 0, 'explicit video matching acknowledgement still gates restored task');
    h.$('aniUnbound').checked = true; await h.$('aniUnbound').fire('change'); await h.$('generateForm').fire('submit');
    assert.equal(h.submissions.length, 1); assert.equal(h.submissions[0].args.name, 'walk_retry_4');
    assert.equal(h.submissions[0].args.force, false); assert.equal(h.submissions[0].args.confirm_mismatch, false);
    assert.equal(h.confirmations.length, 0, 'restored names must not overwrite prior or failed directories');
  }
});

test('restore requires explicit current project, idle uploads/actions, and resets confirmations even on refusal', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4'); await h.choose('aniName', 'keep', 'input');
  const failed = generationTask({status:'failed'});
  h.switchProject('B'); assert.equal(h.page.restoreTask(failed), false); assert.equal(h.current, 'B');
  assert.equal(h.$('aniName').value, '', 'wrong-project restore never injects another project draft');
  h.switchProject('A');
  h.$('upVideo').files = [{name:'pending.mp4'}]; await h.$('upVideo').fire('change'); await tick();
  h.$('aniForce').checked = h.$('aniMismatch').checked = h.$('aniUnbound').checked = true;
  assert.equal(h.page.restoreTask(failed), false); assert.equal(h.$('aniName').value, 'keep');
  for (const id of ['aniForce','aniMismatch','aniUnbound']) assert.equal(h.$(id).checked, false);
  h.uploads[0].onerror(); await settle();
  const active = generationTask({id:'busy'}); h.page.onTasks([active,failed]); assert.equal(h.page.restoreTask(failed), false);
  h.page.onTasks([{...active,status:'done'},failed]);
  const waiting = deferred(); h.setSubmitHook(() => waiting.promise);
  const submission = h.$('generateForm').fire('submit'); await tick(); assert.equal(h.page.restoreTask(failed), false);
  waiting.resolve(null); await submission; assert.equal(h.page.restoreTask(failed), true);
  assert.equal(h.submissions.length, 1, 'loading must not submit another job');
});

test('restore does not bypass mismatched or missing video checks and fresh output reservations', async () => {
  const h = harness(); await h.page.refresh();
  const failed = generationTask({status:'failed',args:{video:'/srv/b.mp4',name:'walk',solver:'legacy',swap:false,force:true,confirm_mismatch:true}});
  assert.equal(h.page.restoreTask(failed), true); assert.equal(h.$('aniVideo').value, 'b.mp4'); assert.equal(h.$('aniRun').disabled, true);
  assert.equal(h.$('aniMismatch').checked, false); assert.equal(h.$('runAdvanced').open, true);
  await h.$('generateForm').fire('submit'); assert.equal(h.submissions.length, 0);
  h.$('aniMismatch').checked = true; await h.$('aniMismatch').fire('change');
  const reserved = generationTask({id:'new-reservation',status:'failed',args:{video:'b.mp4',name:'walk_retry'}});
  h.page.onTasks([failed,reserved]); assert.equal(h.$('aniRun').disabled, true); assert.match(h.$('runDisabledReason').textContent, /已被.*占用/);
  const missing = {...failed,id:'missing',args:{video:'/gone/missing.mp4',name:'missing-result'}};
  assert.equal(h.page.restoreTask(missing), true); assert.equal(h.$('aniVideo').value, ''); assert.match(h.$('runStatus').textContent, /原视频未在上传列表/);
  await h.$('generateForm').fire('submit'); assert.equal(h.submissions.length, 0);
});

test('stabilize retains result/source links and never restores its arguments into animation', async () => {
  const h = harness(); await h.page.refresh(); await h.choose('aniVideo', 'a.mp4'); await h.choose('aniName', 'untouched', 'input');
  const task = generationTask({cmd:'stabilize',args:{source:'original',name:'smoothed'},status:'done'});
  h.page.onTasks([task]); assert.equal(h.$('generationTaskTitle').textContent, '减少抖动任务');
  assert.match(h.$('generationTaskMeta').textContent, /来源结果：original.*输出：smoothed/);
  await h.$('generationTaskResult').fire('click'); await h.$('generationTaskSource').fire('click');
  assert.deepEqual(h.openedResults, [{project:'A',name:'smoothed'},{project:'A',name:'original'}]);
  h.page.onTasks([{...task,status:'failed'}]); assert.equal(h.$('generationTaskRestore').hidden, true);
  assert.equal(h.page.restoreTask({...task,status:'failed'}), false); assert.equal(h.$('aniName').value, 'untouched');
  assert.equal(h.submissions.length, 0);
});

test('overwrite flag exists only after an explicit legacy collision confirmation and never survives restore', async () => {
  const h = harness(); h.projects[0].outputs = ['walk']; await h.page.refresh(); await h.choose('aniVideo', 'a.mp4');
  await h.choose('aniName', 'walk', 'input'); await h.$('generateForm').fire('submit');
  assert.equal(h.confirmations.length, 1); assert.equal(h.submissions[0].args.overwrite, true);
  h.page.onTasks([generationTask({id:'job-1',status:'failed',args:{video:'a.mp4',name:'walk',solver:'legacy',overwrite:true}})]);
  const failed = generationTask({id:'job-1',status:'failed',args:{video:'a.mp4',name:'walk',solver:'legacy',overwrite:true}});
  assert.equal(h.page.restoreTask(failed), true); await h.$('generateForm').fire('submit');
  assert.equal(h.submissions[1].args.overwrite, undefined); assert.equal(h.confirmations.length, 1);
  h.page.onTasks([generationTask({id:'job-2',status:'done',args:h.submissions[1].args})]);
  await h.choose('aniName', 'walk', 'input'); await h.choose('aniSolver', 'temporal');
  await h.$('generateForm').fire('submit'); assert.equal(h.submissions.length, 2); assert.equal(h.confirmations.length, 1);
  assert.match(h.$('runStatus').textContent, /同名实验结果已存在/);
});

test('external failed restore is input-only, monitor warnings survive without a generation task, and server strings remain inert', async () => {
  const h = harness(); await h.page.refresh();
  h.page.onTasks([],{available:false}); assert.equal(h.$('generationMonitorWarning').hidden, false);
  assert.match(h.$('generationMonitorWarning').textContent, /暂不可用/);
  const task = generationTask({source:'cli',read_only:true,status:'failed',args:{video:'a.mp4',name:'<img onerror=bad>',force:true},phase:{label:'<svg onload=bad>',current:1,total:2,unit:'帧'}});
  h.page.onTasks({tasks:[task],monitor:{available:true,warning:'<b>warning</b>'}});
  assert.match(h.$('generationTaskMeta').textContent, /<img onerror=bad>/); assert.match(h.$('generationTaskPhase').textContent, /<svg onload=bad>/);
  assert.equal(h.page.restoreTask(task), true); assert.equal(h.$('aniName').value, 'a_retry');
  assert.match(h.$('runStatus').textContent, /不会自动重放 CLI/); assert.equal(h.submissions.length, 0);
  const listing = h.$('generationTaskCard'); await h.page.refresh(); assert.equal(h.$('generationTaskCard'), listing);
});

(async () => {
  let passed = 0;
  for (const {name, fn} of tests) {
    try { await fn(); passed++; console.log('PASS ' + name); }
    catch (error) { console.error('FAIL ' + name); console.error(error); process.exitCode = 1; }
  }
  console.log(`\n${passed}/${tests.length} run-page tests passed. No remote operations performed.`);
})();
