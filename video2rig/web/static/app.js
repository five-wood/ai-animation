import { initRunPage } from "./run-page.js?v=workspace-v1";
import { initResults } from "./results.js?v=workspace-v1";
import { initKeyframes } from "./keyframes.js?v=kf-v1";
import { taskActive, taskStatus, taskReadOnly, taskCanCancel, taskPhaseHTML, taskSource, taskAdvice, taskResultName } from './task-ui.js?v=workspace-v2';

const $ = s => document.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const enc = encodeURIComponent;
const fmt = (n,d=1) => n == null || !Number.isFinite(Number(n)) ? "–" : Number(n).toFixed(d);
const ago = t => {const s=Math.max(0,Date.now()/1000-(t||0));return s<60?"刚刚":s<3600?`${Math.floor(s/60)} 分钟前`:s<86400?`${Math.floor(s/3600)} 小时前`:`${Math.floor(s/86400)} 天前`;};
async function api(url, options) {
  let r;
  try {r=await fetch(url,options);} catch {throw new Error(options?.method==='POST'?'网络中断，无法确认提交结果，请先查看任务中心，避免重复提交。':'网络连接中断，请检查连接后重试。');}
  if(!r.ok) {let text=await r.text();try {text=JSON.parse(text).detail||text;}catch{}const shown=typeof text==='string'?text:text?.message||JSON.stringify(text);const e=new Error(`${r.status} · ${shown}`);e.status=r.status;e.detail=text;throw e;}
  return r.json();
}
const STAGE={empty:['尚未准备',''],prepared:['待训练',''],data:['训练数据已就绪',''],trained:['已训练 · 待验证','run'],exam_passed:['已就绪','ok'],exam_failed:['验证需关注','warning']};
const PAGES={overview:['项目概览','角色状态与下一步操作'],run:['生成动画','选择对应视频，为当前角色生成新动作'],outputs:['动画结果','预览、检查与导出角色动画'],keyframes:['关键帧','预览候选关键帧，在视频上标注美术认定的关键帧'],train:['训练情况','模型准备、训练与验证均在服务端持续执行'],samples:['训练数据','浏览当前角色的训练姿势与关节标注'],exam:['验证报告','自动验证衡量已知动作的还原误差，不替代人工质量判断']};
const CMD={pipeline:'准备与训练',prepare:'准备模型',gen:'生成训练数据',train:'训练',exam:'自动验证',animate:'生成动画',stabilize:'减少抖动',keyframes:'生成候选关键帧'};
const STATUS={queued:['排队中',''],running:['处理中','run'],done:['执行完成','ok'],needs_review:['需检查','warning'],failed:['执行失败','bad'],cancelled:['已取消',''],interrupted:['已中断','warning']};
const state={projects:[],cur:null,tab:'overview',jobs:[],jobSel:null,sOffset:0,sLimit:48,sSel:null,sAugSeed:1,detail:null,pending:new Set(),pageSequence:0,jobsSequence:0};
const active=taskActive;
const current=()=>state.projects.find(p=>p.name===state.cur);
function notify(text,type='') {const el=$('#notification');el.textContent=text;el.className='run-notice '+type;el.hidden=!text;}
function error(text) {const el=$('#pageError');el.textContent=text;el.hidden=!text;if(text){const retry=document.createElement('button');retry.textContent='重试加载';retry.className='text-button';retry.onclick=()=>loadProjects().then(refreshTab).catch(e=>error(e.message));el.append(' ',retry);}}
const badge=(text,cls='')=>`<span class="badge ${cls}">${esc(text)}</span>`;
const purl=(p=state.cur)=>`/api/projects/${enc(p)}`;
function bindNavigation(root=document) {root.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>navigate(b.dataset.nav));}
function context() {
  const p=current();const [label,cls]=STAGE[p?.stage]||[state.cur?'正在准备项目':'未选择角色',''];
  const running=state.jobs.find(j=>j.project===state.cur&&active(j));
  $('#projectStatus').textContent=running?`${taskSource(running)} · ${running.phase?.label||taskStatus(running)[0]}`:label;$('#projectStatus').className='badge '+(running?'run':cls);
  document.querySelectorAll('[data-step]').forEach(b=>{const busy=state.jobs.some(j=>j.project===state.cur&&active(j));b.disabled=busy||!p||(b.dataset.step==='gen'?!p.bones:b.dataset.step==='train'?!p.samples:!p.has_reader);if(b.dataset.step==='train')b.textContent=p?.has_reader?'重新训练':'开始训练';});
  $('#contextName').textContent=state.cur||'尚无项目';$('#outputCount').textContent=p?.outputs?.length||0;
  $('#pageEyebrow').textContent=state.cur?'角色 / '+state.cur:'角色工作区';
}
let projectsSequence=0, selectionSequence=0, routeSequence=0;
let projectsLoadedAt=0, projectsPending=null;
async function loadProjects(force=false) {
  if(!force&&Date.now()-projectsLoadedAt<5000&&state.projects.length){context();runPage.onProjects();return;}
  if(projectsPending)return projectsPending;
  const work=fetchProjects();projectsPending=work;
  try{return await work;}finally{if(projectsPending===work)projectsPending=null;}
}
async function fetchProjects() {
  const sequence=++projectsSequence;
  const rows=await api('/api/projects');if(sequence!==projectsSequence)return;
  projectsLoadedAt=Date.now();
  state.projects=rows;
  for(const j of state.jobs)if(j.cmd==='pipeline'&&active(j)&&!rows.some(p=>p.name===j.project))state.pending.add(j.project);
  for(const name of state.pending)if(rows.some(p=>p.name===name)||!state.jobs.some(j=>j.project===name&&j.cmd==='pipeline'&&active(j)))state.pending.delete(name);
  if(!state.cur)state.cur=[...rows].sort((a,b)=>b.mtime-a.mtime)[0]?.name||null;
  if(state.cur&&!rows.some(p=>p.name===state.cur)&&!state.pending.has(state.cur))state.cur=rows[0]?.name||null;
  const sel=$('#projSel');sel.innerHTML=rows.map(p=>`<option value="${esc(p.name)}">${esc(p.name)} · ${esc((STAGE[p.stage]||[p.stage])[0])}</option>`).join('');
  if(state.cur&&!rows.some(p=>p.name===state.cur)){const o=new Option(state.cur+' · 等待准备',state.cur);sel.add(o);}
  if(!state.cur)sel.add(new Option('尚无项目',''));
  sel.value=state.cur||'';context();runPage.onProjects();
}
function resetProjectViews(){const loading='<div class="panel empty-state" role="status">正在读取当前项目…</div>';$('#overviewContent').innerHTML=loading;$('#trainConclusion').innerHTML=loading;$('#examConclusion').innerHTML=loading;$('#examHead').innerHTML='';$('#boneBars').innerHTML='';$('#sGrid').innerHTML=loading;$('#sBig').hidden=true;$('#examVid').pause();$('#examVid').removeAttribute('src');$('#examVid').load();$('#examVid').hidden=true;$('#examVideoEmpty').hidden=false;state.trainData=null;['cLoss','cVal','cDepth'].forEach(id=>chart($('#'+id),[]));$('#trainLog').textContent='正在读取当前项目…';renderProjectTask();}
async function selectProject(name,{route=false}={}) {
  if(name===state.cur)return;
  if(!keyframes.canLeave()){$('#projSel').value=state.cur||'';if(route)history.replaceState(null,'',`#${enc(state.cur||'')}/${state.tab}`);return;}
  keyframes.discard();
  const sequence=++selectionSequence;state.pageSequence++;results.pause();keyframes.pause();state.cur=name;results.pause();runPage.syncProject(name);state.sOffset=0;state.sSel=null;state.detail=null;resetProjectViews();context();
  if(!state.projects.some(p=>p.name===name)&&state.jobs.some(j=>j.project===name&&j.cmd==='pipeline'&&active(j)))state.pending.add(name);
  await loadProjects(!state.projects.some(p=>p.name===name));if(sequence!==selectionSequence)return;
  await runPage.syncProject(state.cur);if(sequence!==selectionSequence)return;context();
  if(!route)history.replaceState(null,'',`#${enc(state.cur||'')}/${state.tab}`);if(!route)await refreshTab();
  if($('#tasksDialog').open)renderJobs();
}
$('#projSel').addEventListener('change',e=>selectProject(e.target.value).catch(e=>error(e.message)));
function navigate(tab,update=true) {
  if(tab==='tasks'){openTasks();return;}
  if(update){routeSequence++;selectionSequence++;}
  if(!PAGES[tab])tab='overview';
  if(state.tab==='outputs'&&tab!=='outputs')results.pause();
  if(state.tab==='keyframes'&&tab!=='keyframes')keyframes.pause();
  document.querySelectorAll('#tabs [data-tab]').forEach(b=>{const on=b.dataset.tab===tab;b.classList.toggle('active',on);if(on)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('active',e.id==='tab-'+tab));
  state.tab=tab;$('#pageTitle').textContent=PAGES[tab][0];$('#pageDescription').textContent=PAGES[tab][1];
  if(update)history.pushState(null,'',`#${enc(state.cur||'')}/${tab}`);
  error('');notify('');const refreshed=refreshTab();window.scrollTo({top:0});return refreshed;
}
document.querySelectorAll('#tabs [data-tab]').forEach(b=>b.onclick=()=>navigate(b.dataset.tab));
bindNavigation();
window.addEventListener('hashchange',async()=>{const sequence=++routeSequence;const hash=location.hash;try{const [raw,t,sub]=hash.slice(1).split('/');const p=decodeURIComponent(raw||'');if(p&&p!==state.cur)await selectProject(p,{route:true});if(sequence!==routeSequence||hash!==location.hash)return;if(t==='keyframes'&&sub)keyframes.select(decodeURIComponent(sub));await navigate(t,false);if(sequence===routeSequence&&state.tab!=='keyframes')history.replaceState(null,'',`#${enc(state.cur||'')}/${state.tab}`);}catch(e){if(sequence===routeSequence)error(e.message);}});
function empty(message,action='新建角色',nav='create') {return `<div class="empty-state"><div class="empty-icon">◇</div><h2>${esc(message)}</h2><p class="hint">先准备角色，再用对应的双视角视频生成动作。</p><button class="primary" ${nav==='create'?'data-create':`data-nav="${nav}"`}>${esc(action)}</button></div>`;}
function nextAction(p) {return p?.has_reader?['生成动画','run']:p?.samples?['开始训练','train']:['准备训练','train'];}
async function renderOverview() {
  const name=state.cur,seq=state.pageSequence;const el=$('#overviewContent');
  if(!name){el.innerHTML=empty('还没有角色项目');el.querySelector('[data-create]').onclick=()=>runPage.openCreate();return;}
  let p;try {p=await api(purl(name));}catch(e){if(name!==state.cur||seq!==state.pageSequence)return;if(e.status===404&&state.pending.has(name)){el.innerHTML=empty('项目已排队，等待准备','查看训练进度','train');bindNavigation(el);return;}throw e;}
  if(name!==state.cur||seq!==state.pageSequence)return;state.detail=p;
  const [status,cls]=STAGE[p.stage]||[p.stage,''];const [action,nav]=nextAction(p);
  const steps=[['模型准备',p.bones>0],['训练数据',p.samples>0],['角色训练',p.has_reader],['自动验证',p.exam?.passed===true]];
  const ref=`${purl(name)}/file/reference_two_views.png`;
  el.innerHTML=`<div class="overview-hero panel"><div class="hero-copy"><div class="eyebrow">当前角色</div><h2>${esc(name)} ${badge(status,cls)}</h2><p>${p.has_reader?'角色识别器已可用，可以开始处理对应视频。':'完成模型准备与训练后，即可持续生成动画。'}</p><p class="hint">${esc(p.model_name||'等待模型准备')} · ${p.bones} 骨骼 · ${p.keypoints} 关键点</p><button class="primary" data-nav="${nav}">${action} →</button> <button data-nav="exam" class="text-button">查看验证报告</button></div><div class="hero-reference">${p.has_reference?`<img src="${ref}?t=${Math.floor(p.mtime)}" alt="${esc(name)} 的正面与侧面参考图"><a href="${ref}" download>下载双视角参考图 ↗</a>`:'<div class="empty-state">参考图将在模型准备后生成</div>'}</div></div>
  <div class="workflow">${steps.map(([t,done],i)=>`<div class="workflow-step ${done?'complete':''}"><span>${done?'✓':i+1}</span><div>${t}<small>${done?'已完成':'待完成'}</small></div></div>`).join('')}</div>
  ${p.exam?.passed===false?'<div class="run-notice warning">自动验证部分指标未达标。可以检查报告后调整，或谨慎进行诊断生成；不代表本次训练程序执行失败。</div>':''}
  <div class="section-heading"><h2>最近动画</h2><button class="text-button" data-nav="outputs">查看全部 ${p.outputs.length} 个 →</button></div><div id="recentOutputs" class="recent-grid"><p class="hint">正在读取结果…</p></div>
  <details class="panel advanced"><summary>项目资产与技术信息</summary><div class="asset-grid"><div><h3>模型信息</h3><div class="kv"><b>模型</b><span>${esc(p.model_name||'–')}</span><b>摄影棚</b><span>${p.visual_profile==='soft-clean'?'实验 · 柔光无地影':'标准摄影棚'}</span><b>骨骼 / 可动</b><span>${p.bones} / ${p.active??'–'}</span><b>训练姿势 / 轮次</b><span>${p.samples} / ${p.epochs}</span><b>朝向 / 尺寸</b><span>${esc(p.rig?.facing||'–')} / ${fmt(p.rig?.size,3)}</span></div></div><div><h3>骨骼关键点 · 左右镜像</h3><div class="kplist">${(p.rig?.keypoints||[]).map((k,i)=>`<div class="${['L','R','M'].includes(k.side)?k.side:'M'}">${i} ${esc(k.name)}${k.mirror>=0?' ↔ '+esc(p.rig?.keypoints[k.mirror]?.name):''}</div>`).join('')}</div></div></div></details>`;
  bindNavigation(el);el.querySelectorAll('details').forEach(d=>d.ontoggle=()=>{});
  const outs=await api(`${purl(name)}/outputs`);if(name!==state.cur||seq!==state.pageSequence)return;
  $('#recentOutputs').innerHTML=outs.slice(0,3).map(o=>`<button class="recent-card" data-result="${esc(o.name)}"><span class="eyebrow">动画结果</span><strong>${esc(o.name)}</strong>${badge(o.check?.same_move===true?'自动检查通过':o.check?.same_move===false?'需要检查':'未形成检查结论',o.check?.same_move===true?'ok':'warning')}<span class="hint">${o.summary?.frames??'–'} 帧 · ${ago(o.mtime)}　查看 →</span></button>`).join('')||'<div class="empty-inline">还没有动画。角色就绪后，前往“生成动画”创建第一个动作。</div>';
  el.querySelectorAll('[data-result]').forEach(b=>b.onclick=()=>{results.select(b.dataset.result);navigate('outputs');});
}
// Fixed logical height prevents cumulative DPR scaling on every poll.
function chart(canvas,series,opt={}) {
  const W=canvas.clientWidth,H=Number(canvas.dataset.height||220),dpr=Math.min(devicePixelRatio||1,2);if(!W)return;
  canvas.width=Math.round(W*dpr);canvas.height=Math.round(H*dpr);canvas.style.height=H+'px';const g=canvas.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,W,H);
  const pts=series.flatMap(s=>s.data.filter(p=>p[1]!==null&&Number.isFinite(p[1])));g.font='11px sans-serif';g.fillStyle='#9aa7b8';
  if(!pts.length){g.fillText('暂无数据',Math.max(10,W/2-24),H/2);return;}
  const pad={l:46,r:12,t:30,b:24};let x0=Math.min(...pts.map(p=>p[0])),x1=Math.max(...pts.map(p=>p[0]));let y0=opt.y0??Math.min(...pts.map(p=>p[1])),y1=opt.y1??Math.max(...pts.map(p=>p[1]));
  if(opt.log){y0=Math.log10(Math.max(1e-6,y0));y1=Math.log10(Math.max(1e-6,y1));}if(x0===x1)x1=x0+1;if(y0===y1)y1=y0+1;
  const X=x=>pad.l+(x-x0)/(x1-x0)*(W-pad.l-pad.r);const Y=y=>H-pad.b-((opt.log?Math.log10(Math.max(1e-6,y)):y)-y0)/(y1-y0)*(H-pad.t-pad.b);
  for(let i=0;i<=4;i++){const v=y0+(y1-y0)*i/4,y=H-pad.b-(H-pad.t-pad.b)*i/4;g.strokeStyle='#2a3544';g.beginPath();g.moveTo(pad.l,y);g.lineTo(W-pad.r,y);g.stroke();g.fillText(opt.log?Math.pow(10,v).toPrecision(2):v.toFixed(opt.dec??1),3,y+4);}
  for(const [a,b] of opt.bands||[]){g.fillStyle='#ed756533';g.fillRect(X(a),pad.t,Math.max(1,X(b)-X(a)),H-pad.t-pad.b);}
  let lx=pad.l;
  for(const s of series){g.strokeStyle=s.color;g.lineWidth=2;g.beginPath();let first=true;for(const [x,y] of s.data){if(y==null||!Number.isFinite(y)){first=true;continue;}if(first)g.moveTo(X(x),Y(y));else g.lineTo(X(x),Y(y));first=false;}g.stroke();g.fillStyle=s.color;g.fillRect(lx,10,10,3);g.fillStyle='#dce5ef';g.fillText(s.name,lx+14,16);lx+=g.measureText(s.name).width+32;}
  g.fillStyle='#9aa7b8';g.fillText(opt.xlabel||'',W-64,H-5);
}
async function renderTrain() {
  const name=state.cur,seq=state.pageSequence,p=current();const target=$('#trainConclusion');
  if(!name){target.innerHTML=empty('请选择或新建角色');target.querySelector('[data-create]').onclick=()=>runPage.openCreate();return;}
  const job=state.jobs.find(j=>j.project===name&&active(j));
  target.innerHTML=`<div class="conclusion panel"><div><h2>${job?esc((job.phase?.label||CMD[job.cmd]||'任务')+'进行中'):p?.has_reader?'已有训练模型':p?.samples?'训练数据已就绪':state.pending.has(name)?'项目已排队':'角色尚未完成训练'}</h2><p class="hint">${job?'离开或刷新页面不会停止服务端任务。':p?.has_reader?'查看验证报告，确认还原精度；也可以生成动画进行人工预览。':'先生成训练数据，再训练角色识别器。新建项目将自动依次执行。'}</p></div><button data-nav="exam">查看验证报告 →</button></div>`;bindNavigation(target);
  document.querySelectorAll('[data-step]').forEach(b=>{b.disabled=!!job||!p||b.dataset.step==='train'&&!p.samples||b.dataset.step==='gen'&&!p.bones||b.dataset.step==='exam'&&!p.has_reader;});
  renderProjectTask();
  if(!p){['cLoss','cVal','cDepth'].forEach(id=>chart($('#'+id),[]));$('#trainLog').textContent='等待项目准备…';return;}
  const r=await api(`${purl(name)}/train`);if(name!==state.cur||seq!==state.pageSequence)return;
  state.trainData=r;drawTrain(r);
  const lg=await api(`/api/project-log/${enc(name)}/train?tail=60`);if(name===state.cur&&seq===state.pageSequence)$('#trainLog').textContent=lg.lines.join('\n')||'暂无训练日志';
}
function drawTrain(r) {
  chart($('#cLoss'),[{name:'热力图',color:'#ddb46b',data:r.steps.map(s=>[s.step,s.hm])},{name:'深度',color:'#68a7f5',data:r.steps.map(s=>[s.step,s.depth])}],{log:true,xlabel:'step'});
  chart($('#cVal'),[{name:'中位数',color:'#60c79e',data:r.epochs.map(e=>[e.epoch,e.px_med])},{name:'p90',color:'#ed827c',data:r.epochs.map(e=>[e.epoch,e.px_p90])}],{log:true,xlabel:'epoch'});
  chart($('#cDepth'),[{name:'准确率',color:'#ddb46b',data:r.epochs.map(e=>[e.epoch,e.depth_order])}],{y0:.5,y1:1,dec:2,xlabel:'epoch'});
}
async function renderSamples() {
  const name=state.cur,seq=state.pageSequence;if(!name){$('#sGrid').innerHTML='<div class="empty-state">请先创建角色</div>';return;}
  const r=await api(`${purl(name)}/samples?offset=${state.sOffset}&limit=${state.sLimit}&fast=${$('#sFilter').value}`);if(name!==state.cur||seq!==state.pageSequence)return;
  state.sampleItems=r.items;state.sampleTotal=r.total;
  $('#sPage').textContent=r.total?`${state.sOffset+1}–${Math.min(r.total,state.sOffset+state.sLimit)} / ${r.total}`:'暂无训练数据';
  $('#sPrev').disabled=!state.sOffset;$('#sNext').disabled=state.sOffset+state.sLimit>=r.total;
  $('#sGrid').innerHTML=r.items.map(it=>`<button class="t ${it.id===state.sSel?'sel':''}" data-id="${it.id}" aria-label="查看样本 ${it.id}${it.fast?'，快速运动':''}" aria-pressed="${it.id===state.sSel}"><img loading="lazy" src="${purl(name)}/thumb/${it.id}.jpg" alt="样本 ${it.id}"><span>#${it.id}</span>${it.fast?'<span class="f">模糊</span>':''}</button>`).join('')||'<div class="empty-state">还没有训练数据。前往训练情况生成数据。</div>';
  $('#sGrid').querySelectorAll('button').forEach(b=>b.onclick=()=>showSample(+b.dataset.id));
  if(!r.items.some(it=>it.id===state.sSel))state.sSel=r.items[0]?.id??null;
  $('#sBig').hidden=state.sSel===null;$('#sReroll').disabled=state.sSel===null;if(state.sSel!==null)showSample(state.sSel);
}
function showSample(id) {state.sSel=id;$('#sBig').hidden=false;$('#sBig').src=`${purl()}/sample/${id}.jpg?labels=${+$('#sLabels').checked}&aug=${$('#sAug').checked?state.sAugSeed:0}&w=640`;$('#sGrid').querySelectorAll('button').forEach(b=>{b.classList.toggle('sel',+b.dataset.id===id);b.setAttribute('aria-pressed',String(+b.dataset.id===id));});}
$('#sFilter').onchange=()=>{state.sOffset=0;state.sSel=null;refreshTab();};$('#sPrev').onclick=()=>{state.sOffset=Math.max(0,state.sOffset-state.sLimit);refreshTab();};$('#sNext').onclick=()=>{state.sOffset+=state.sLimit;state.sSel=null;refreshTab();};
['sLabels','sAug'].forEach(id=>$('#'+id).onchange=()=>state.sSel!==null&&showSample(state.sSel));$('#sReroll').onclick=()=>{state.sAugSeed++;$('#sAug').checked=true;showSample(state.sSel);};
document.addEventListener('keydown',e=>{if(state.tab!=='samples'||/INPUT|SELECT|TEXTAREA/.test(e.target.tagName))return;const items=state.sampleItems||[],i=items.findIndex(it=>it.id===state.sSel),next=items[i+(e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0)];if(next&&['ArrowRight','ArrowLeft'].includes(e.key)){e.preventDefault();showSample(next.id);}});
async function renderExam() {
  const name=state.cur,seq=state.pageSequence;$('#examVid').pause();$('#examVid').hidden=true;$('#examVideoEmpty').hidden=false;$('#examHead').innerHTML='';$('#boneBars').innerHTML='';
  if(!name){$('#examConclusion').innerHTML='<div class="empty-state">请选择一个项目</div>';return;}
  let r;try {r=await api(`${purl(name)}/exam`);}catch(e){if(name!==state.cur||seq!==state.pageSequence)return;if(e.status===404){$('#examConclusion').innerHTML='<div class="conclusion panel"><div><h2>尚无验证报告</h2><p class="hint">训练完成后运行自动验证，了解已知动作的还原误差。</p></div><button data-nav="train">前往训练与验证 →</button></div>';bindNavigation($('#examConclusion'));return;}throw e;}
  if(name!==state.cur||seq!==state.pageSequence)return;
  $('#examConclusion').innerHTML=`<div class="conclusion panel"><div><h2>${r.passed?'✓ 自动验证通过':'! 部分验证指标未达标'}</h2><p class="hint">${r.passed?'已知动作还原达到当前验证标准。真实视频仍可能存在遮挡、抖动等问题，需要人工预览。':'训练已完成，但还原误差需要关注。请检查方向、参考图与逐骨骼误差，再决定是否重训。'}</p></div><button data-nav="${r.passed?'run':'train'}" class="primary">${r.passed?'生成动画':'查看训练情况'} →</button></div>`;bindNavigation($('#examConclusion'));
  const cards=[['关节位置误差',fmt(r.joint_err_mean_pct_of_size,2),'% 身体尺寸'],['骨骼方向误差',fmt(r.bone_angle_mean_deg),'° 均值'],['位置误差 p90',fmt(r.joint_err_p90_pct_of_size,2),'%'],['整体朝向误差',fmt(r.root_angle_mean_deg),'°']];
  $('#examHead').innerHTML=cards.map(([t,v,u])=>`<div class="metric-card"><span class="hint">${t}</span><strong>${v}<small>${u}</small></strong></div>`).join('');
  const bones=Object.entries(r.per_bone||{}).sort((a,b)=>b[1].mean_deg-a[1].mean_deg),max=Math.max(30,...bones.map(b=>b[1].p90_deg));
  $('#boneBars').innerHTML=bones.map(([n,v])=>`<div class="bar"><span>${esc(n)}</span><div class="b"><i class="p" style="width:${100*v.p90_deg/max}%"></i><i class="m" style="width:${100*v.mean_deg/max}%"></i></div><span>${fmt(v.mean_deg)} / ${fmt(v.p90_deg)}</span></div>`).join('');
  if(r.has_video){$('#examVid').src=`${purl(name)}/file/out/exam/lab.mp4`;$('#examVid').hidden=false;$('#examVideoEmpty').hidden=true;}
}
function confirmAction(title,text,button='确认执行') {return new Promise(resolve=>{const d=$('#confirmDialog');$('#confirmTitle').textContent=title;$('#confirmText').textContent=text;$('#confirmAction').textContent=button;d.returnValue='';d.addEventListener('close',()=>resolve(d.returnValue==='ok'),{once:true});d.showModal();});}
let submitting=false;
async function submit(project,cmd,args) {
  if(submitting)throw new Error('另一个任务正在提交，请稍候。');submitting=true;
  try {const j=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project,cmd,args})});state.jobSel=j.id;state.jobs=[j,...state.jobs.filter(x=>x.id!==j.id)];notify(`${CMD[cmd]||cmd}任务已提交 · ${project}。可在任务中心查看进度。`,'success');renderProjectTask();runPage.onTasks?.(state.jobs,monitorInfo);pollJobs().catch(e=>notify(e.message,'error'));return j;}finally{submitting=false;}
}
let taskActionSequence=0;
async function openResult(project,name){const action=++taskActionSequence,route=routeSequence;await selectProject(project);if(action!==taskActionSequence||route!==routeSequence||state.cur!==project)return false;const selection=selectionSequence;await results.select(name);if(action!==taskActionSequence||route!==routeSequence||selection!==selectionSequence||state.cur!==project)return false;await navigate('outputs');return true;}
const runPage=initRunPage({api,submit,current:()=>state.cur,projects:()=>state.projects,selectProject,navigate,notify,openResult});
const results=initResults({api,submit,current:()=>state.cur,notify,chart,openKeyframes:name=>{keyframes.select(name);navigate('keyframes');}});
const keyframes=initKeyframes({api,current:()=>state.cur,notify,submit,tasks:()=>state.jobs,confirm:(text)=>confirmAction('重新生成候选关键帧',text,'重新生成')});
$('#openCreate').onclick=()=>runPage.openCreate();$('#openTasks').onclick=openTasks;$('#closeTasks').onclick=()=>$('#tasksDialog').close();$('#refreshJobs').onclick=()=>pollJobs().catch(e=>jobError(e.message));$('#jobScope').onchange=()=>{state.jobSel=null;renderJobs();};
function openTasks(){if(!$('#tasksDialog').open)$('#tasksDialog').showModal();pollJobs().catch(e=>jobError(e.message));}
function jobError(text){$('#jobError').textContent=text;$('#jobError').hidden=!text;}
function duration(j){return j.started?`${fmt(((j.ended||Date.now()/1000)-j.started)/60,1)} 分钟`:'等待执行';}
function resultName(j){return taskResultName(j);}
function jobAdvice(j){return taskAdvice(j);}
let projectTaskSignature='';
function renderProjectTask(){const el=$('#projectTask');const j=state.jobs.find(x=>x.project===state.cur&&active(x))||state.jobs.find(x=>x.project===state.cur);const signature=JSON.stringify([state.cur,j?.id,j?.status,j?.phase,j?.progress_updated_at]);if(signature===projectTaskSignature&&el.innerHTML)return;projectTaskSignature=signature;if(!j){el.innerHTML='';return;}const [st,cl]=taskStatus(j);el.innerHTML=`<div class="task-inline panel"><div><strong>${CMD[j.cmd]||esc(j.cmd)}</strong> ${badge(st,cl)}<p class="hint">${esc(j.project)} · ${esc(taskSource(j))} · ${duration(j)}</p>${taskPhaseHTML(j)}<p class="hint">${jobAdvice(j)}</p></div><button id="inlineTaskDetail">查看任务 →</button></div>`;$('#inlineTaskDetail').onclick=()=>{state.jobSel=j.id;openTasks();};}
let unifiedTasks=true, monitorInfo={available:true,warning:null};
async function fetchTaskSnapshot(){if(unifiedTasks){try{return await api('/api/tasks');}catch(e){if(e.status!==404)throw e;unifiedTasks=false;}}return {tasks:await api('/api/jobs'),monitor:{available:false,warning:'当前服务仅提供 Web 队列。命令行任务未纳入统计；后端更新后刷新页面启用统一监控。'}};}
async function pollJobs(){const seq=++state.jobsSequence;let snapshot;try{snapshot=await fetchTaskSnapshot();}catch(e){monitorInfo={available:false,warning:'任务监控暂不可用，保留上次观测状态：'+e.message};runPage.onTasks?.(state.jobs,monitorInfo);$('#taskCount').textContent='?';showMonitorWarning();throw e;}if(seq!==state.jobsSequence)return;const jobs=snapshot.tasks||[];const completed=jobs.some(j=>!active(j)&&active(state.jobs.find(x=>x.id===j.id)||{}));state.jobs=jobs;monitorInfo=snapshot.monitor||{available:true};$('#taskCount').textContent=monitorInfo.available===false?'?':jobs.filter(active).length;$('#openTasks').title=monitorInfo.warning||'查看 Web 与命令行任务';context();renderProjectTask();runPage.onTasks?.(jobs,monitorInfo);keyframes.onTasks?.();showMonitorWarning();if($('#tasksDialog').open)await renderJobs();if(completed){await loadProjects(true);if(state.tab==='train')await renderTrain();}}
function showMonitorWarning(){const el=$('#taskMonitorWarning');if(!el)return;el.hidden=!monitorInfo.warning;if(el.textContent!==(monitorInfo.warning||''))el.textContent=monitorInfo.warning||'';}
let jobViewSignature='';
async function renderJobs(){jobError('');const rows=state.jobs.filter(j=>$('#jobScope').value==='all'||j.project===state.cur);if(!rows.some(j=>j.id===state.jobSel))state.jobSel=rows[0]?.id||null;
  const signature=JSON.stringify([state.cur,$('#jobScope').value,state.jobSel,rows.map(j=>[j.id,j.status,j.ended,j.phase,j.progress_updated_at])]);
  if(signature===jobViewSignature&&$('#jobLog')){const id=state.jobSel;try{const lg=await api(`${unifiedTasks?'/api/tasks':'/api/jobs'}/${enc(id)}/log?tail=300`);if(state.jobSel===id&&$('#jobLog')){const el=$('#jobLog'),bottom=el.scrollTop+el.clientHeight>=el.scrollHeight-20;el.textContent=lg.lines.join('\n')||'任务尚无日志';if(bottom)el.scrollTop=el.scrollHeight;}}catch(e){jobError(e.message);}return;}
  jobViewSignature=signature;
  $('#jobList').innerHTML=rows.map(j=>{const [t,c]=taskStatus(j);return `<button class="item ${j.id===state.jobSel?'sel':''}" data-job="${esc(j.id)}" aria-pressed="${j.id===state.jobSel}"><strong>${CMD[j.cmd]||esc(j.cmd)}</strong>${badge(t,c)}<span class="s">${esc(j.project)} · ${esc(taskSource(j))}</span><span class="s">${esc(j.phase?.label||resultName(j)||j.args?.model?.split(/[\\/]/).pop()||'角色处理')}</span><span class="s">${ago(j.created)} · ${duration(j)}</span></button>`;}).join('')||'<div class="empty-state">此范围暂无任务</div>';
  $('#jobList').querySelectorAll('button').forEach(b=>b.onclick=()=>{state.jobSel=b.dataset.job;renderJobs().catch(e=>jobError(e.message));});const j=rows.find(x=>x.id===state.jobSel);if(!j){$('#jobDetail').innerHTML='<div class="empty-state">选择任务查看详情</div>';return;}
  const id=j.id,[st,cl]=taskStatus(j);const d=$('#jobDetail');const detailsWasOpen=d.querySelector('details')?.open;d.innerHTML=`<div class="section-heading"><h3>${CMD[j.cmd]||esc(j.cmd)}</h3>${badge(st,cl)}</div><p class="context-label">项目：${esc(j.project)} · ${esc(taskSource(j))}</p><p>${jobAdvice(j)}</p>${taskPhaseHTML(j)}<div class="kv"><b>任务</b><span>${esc(j.id)}</span><b>输入 / 输出</b><span>${esc(resultName(j)||j.args?.model||'–')}</span><b>耗时</b><span>${duration(j)}</span></div><div class="toolbar">${taskCanCancel(j)?'<button id="cancelJob">取消任务</button>':''}${!taskReadOnly(j)&&['failed','cancelled','interrupted'].includes(j.status)&&j.cmd==='animate'?'<button id="restoreJob" class="primary">载入参数并重新核对</button>':''}${['done','needs_review'].includes(j.status)?'<button class="primary" id="viewJobResult">'+(['animate','stabilize'].includes(j.cmd)?'查看动画结果':j.cmd==='keyframes'?'查看关键帧':'查看项目')+' →</button>':''}</div><details class="advanced" ${detailsWasOpen?'open':''}><summary>技术详情 · 完整执行日志</summary><pre id="jobLog" class="log tall" tabindex="0">正在读取日志…</pre></details>`;
  if($('#cancelJob'))$('#cancelJob').onclick=async()=>{if(!await confirmAction('取消任务',`即将取消 ${j.project} 的${CMD[j.cmd]}。已写入的中间文件不会自动清除。`,'取消该任务'))return;try{await api(`/api/jobs/${enc(id)}/cancel`,{method:'POST'});await pollJobs();}catch(e){jobError(e.message);}};
  if($('#restoreJob'))$('#restoreJob').onclick=async()=>{try{const action=++taskActionSequence,route=routeSequence;await selectProject(j.project);if(action!==taskActionSequence||route!==routeSequence||state.cur!==j.project)return;await navigate('run');const selection=selectionSequence;await runPage.refresh();if(action!==taskActionSequence||state.cur!==j.project||selection!==selectionSequence)return;if(runPage.restoreTask(j)){$('#tasksDialog').close();$('#aniName')?.focus();}else jobError('暂时不能载入，请等待上传或文件列表刷新完成。不会自动提交任务。');}catch(e){jobError(e.message);}};
  if($('#viewJobResult'))$('#viewJobResult').onclick=async()=>{try{if(['animate','stabilize'].includes(j.cmd))await openResult(j.project,resultName(j));else if(j.cmd==='keyframes'){await selectProject(j.project);if(state.cur===j.project){keyframes.select(j.args?.name);await navigate('keyframes');}}else {await selectProject(j.project);await navigate(j.cmd==='exam'?'exam':'train');}$('#tasksDialog').close();}catch(e){jobError(e.message);}};
  try{const lg=await api(`${unifiedTasks?'/api/tasks':'/api/jobs'}/${enc(id)}/log?tail=300`);if(state.jobSel!==id||!$('#jobLog'))return;$('#jobLog').textContent=lg.lines.join('\n')||'任务尚无日志';}catch(e){if(state.jobSel===id)jobError(e.message);}
}
document.querySelectorAll('[data-step]').forEach(b=>b.onclick=async()=>{const p=current(),cmd=b.dataset.step;if(!p)return;if(state.jobs.some(j=>j.project===p.name&&active(j))){error('当前项目已有排队或运行中的任务，请在任务中心查看，完成后再提交。');return;}const title=CMD[cmd];const impact=cmd==='gen'?'将重新写入训练数据，后续应重新训练与验证。':cmd==='train'?'将写入当前项目的训练模型。已有模型不承诺可回退。':'将更新当前项目的验证报告和验证产物。';if(!await confirmAction(`${title} · ${p.name}`,impact+'\n此操作作用于当前项目，不会替换项目模型。','确认'+title))return;if(state.jobs.some(j=>j.project===p.name&&active(j))){error('此项目刚刚加入了新任务，请等待当前任务完成。');return;}b.disabled=true;try{await submit(p.name,cmd,{});await refreshTab();}catch(e){error(e.message);}finally{context();}});
async function refreshTab(){const seq=++state.pageSequence;error('');context();try {if(state.tab!=='exam')$('#examVid').pause();if(state.tab!=='run')$('#runVideo')?.pause();if(state.tab==='overview')await renderOverview();if(state.tab==='run')await runPage.refresh();if(state.tab==='outputs')await results.refresh();if(state.tab==='keyframes')await keyframes.refresh();if(state.tab==='train')await renderTrain();if(state.tab==='samples')await renderSamples();if(state.tab==='exam')await renderExam();}catch(e){if(seq===state.pageSequence)error(e.message);}}
async function renderSys(){try{const s=await api('/api/system'),g=s.gpu?.[0];$('#sys').textContent=(g?`GPU ${g.util}% · 显存 ${fmt(g.mem_used/1024)}/${fmt(g.mem_total/1024,0)} GB · ${g.temp}°C\n`:'GPU 状态暂不可用\n')+`磁盘剩余 ${fmt(s.disk_free_gb,0)} GB`;$('.system-menu').classList.toggle('resource-warning',s.disk_free_gb<10||g?.temp>85);}catch{$('#sys').textContent='暂时无法读取系统状态';}}
let polling=false;setInterval(async()=>{if(polling||document.hidden)return;polling=true;try{await pollJobs();if(state.tab==='train')await renderTrain();}catch(e){if($('#tasksDialog').open)jobError(e.message);}finally{polling=false;}},5000);
setInterval(()=>{if(!document.hidden){loadProjects().catch(e=>error(e.message));renderSys();}},20000);
window.addEventListener('resize',()=>{results.resize();keyframes.resize();if(state.tab==='train'&&state.trainData)drawTrain(state.trainData);});
$('#tab-train').querySelector('details').addEventListener('toggle',()=>{if(state.trainData)drawTrain(state.trainData);});
document.addEventListener('visibilitychange',()=>{if(document.hidden){results.pause();keyframes.pause();$('#examVid').pause();$('#runVideo')?.pause();}});
$('#overviewContent').innerHTML='<div class="panel empty-state" role="status">正在读取角色工作区…</div>';$('#projSel').add(new Option('正在读取项目…',''));
(async()=>{try{const [p,t,sub]=location.hash.slice(1).split('/');if(p)state.cur=decodeURIComponent(p);if(t==='keyframes'&&sub&&p)keyframes.select(decodeURIComponent(sub));try{const initial=await fetchTaskSnapshot();state.jobs=initial.tasks||[];monitorInfo=initial.monitor||{available:true};}catch(e){monitorInfo={available:false,warning:'暂时无法读取任务监控，项目仍可浏览：'+e.message};}await loadProjects();$('#taskCount').textContent=monitorInfo.available===false?'?':state.jobs.filter(active).length;runPage.onTasks?.(state.jobs,monitorInfo);showMonitorWarning();await navigate(t||'overview',false);renderSys();}catch(e){error('初始化失败：'+e.message);}})();
