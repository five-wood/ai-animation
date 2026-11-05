// Dependency-free tests against the actual workspace routing functions.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/static/app.js'), 'utf8');
function functionSource(name) {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  // Functions under test have balanced braces, including template-expression braces.
  let i = source.indexOf(' {', start) + 1, depth = 1;
  for (i++; depth; i++) { if (source[i] === '{') depth++; if (source[i] === '}') depth--; }
  return (source.slice(start - 6, start) === 'async ' ? 'async ' : '') + source.slice(start, i);
}
const handler = source.match(/window\.addEventListener\('hashchange',([\s\S]*?)\);\nfunction empty/)[1];
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; };
function harness() {
  const nodes = new Map();
  const node = key => nodes.get(key) || (nodes.set(key, {textContent:'',className:'',innerHTML:'',open:false,disabled:false,value:'',classList:{toggle(){}},setAttribute(){},removeAttribute(){},add(){}}),nodes.get(key));
  const state = {cur:'A',tab:'overview',pageSequence:0,projects:[{name:'A'},{name:'B'},{name:'C'}],jobs:[],pending:new Set()};
  const context = vm.createContext({state,location:{hash:'#A/overview'},history:{},routeSequence:0,selectionSequence:0,projectsSequence:0,enc:encodeURIComponent,esc:String,decodeURIComponent,Set,Option:function(t,v){this.text=t;this.value=v;},STAGE:{},PAGES:{overview:['Overview',''],train:['Train',''],run:['Generate',''],outputs:['Results','']},results:{pause(){}},runPage:{syncProject(){},onProjects(){}},resetProjectViews(){},context(){},error(e){if(e)throw Error(e);},notify(){},refreshTab:async()=>{},renderJobs(){},openTasks(){},api:async()=>state.projects,$:node,document:{querySelectorAll:()=>[]},window:{scrollTo(){}}});
  context.history.replaceState = context.history.pushState = (_,__,hash) => {context.location.hash=hash;};
  vm.runInContext(functionSource('loadProjects')+'\n'+functionSource('selectProject')+'\n'+functionSource('navigate')+'\nglobalThis.routeHandler='+handler,context);
  return context;
}
(async()=>{
  let h=harness();
  h.location.hash='#B/train'; await h.routeHandler();
  assert.equal(h.state.cur,'B');assert.equal(h.state.tab,'train');assert.equal(h.location.hash,'#B/train');
  console.log('PASS cross-project route keeps requested tab and URL');
  h=harness(); const a=deferred(), b=deferred();let n=0;h.api=()=>++n===1?a.promise:b.promise;
  const p1=h.selectProject('B'),p2=h.selectProject('C');b.resolve([{name:'A'},{name:'B'},{name:'C'}]);await p2;a.resolve([{name:'A'},{name:'B'},{name:'C'}]);await p1;
  assert.equal(h.state.cur,'C');assert.equal(h.location.hash,'#C/overview');console.log('PASS latest project selection wins stale requests');
  h=harness();const d=deferred();h.api=()=>d.promise;
  h.location.hash='#B/train';const old=h.routeHandler();h.location.hash='#C/run';const latest=h.routeHandler();d.resolve([{name:'A'},{name:'B'},{name:'C'}]);await Promise.all([old,latest]);
  assert.equal(h.state.cur,'C');assert.equal(h.state.tab,'run');assert.equal(h.location.hash,'#C/run');console.log('PASS latest route wins overlapping hash handlers');
  h=harness();h.state.cur='queued';h.state.jobs=[{cmd:'pipeline',project:'queued',status:'queued'}];h.active=j=>['queued','running'].includes(j.status); // actual loadProjects prerequisite
  // Function references active even if no pending existed before reload.
  vm.runInContext('globalThis.active=j=>["queued","running"].includes(j.status)',h);
  await h.loadProjects();assert.equal(h.state.cur,'queued');assert(h.state.pending.has('queued'));
  h.state.jobs[0].status='failed';await h.loadProjects();assert.equal(h.state.cur,'A');assert(!h.state.pending.has('queued'));console.log('PASS queued project survives reload and terminal pending is reconciled');
  console.log('4/4 workspace route tests passed. No network or job submissions.');
})().catch(e=>{console.error(e);process.exitCode=1;});
