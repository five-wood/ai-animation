// Explicit project/video pairing. Never infer a creature's identity from a filename.
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const modelFile = n => /\.(fbx|glb|blend)$/i.test(n);
const basename = s => (s || '').split(/[\\/]/).pop();
const bytes = s => s > 1048576 ? `${(s / 1048576).toFixed(1)} MB` : `${Math.round(s / 1024)} KB`;
const key = 'v2r.run.v2';
let saved = {};
try { saved = JSON.parse(localStorage.getItem(key) || '{}'); } catch {}
let inputs = {models:[], videos:[]};
let projects = [];
let opts;
let initialized = false;
let generation = 0;
let detail = null;
let busy = false;
let uploading = 0;
let explicitConfirmation = false;
let loadSequence = 0;
let lastVideoURL = '';
let latestRefresh = 0;
function remember() {
  const fields=['newName','newModel','newFacing','newUp','newProfile','newProc','newDropProps','aniProject','aniVideo','aniName','aniSolver','aniSwap','aniForce'];
  for (const id of fields) {
    const el=$('#'+id); if (el) saved[id]=el.type==='checkbox'?el.checked:el.value;
  }
  try { localStorage.setItem(key,JSON.stringify(saved)); } catch {}
}
function fill(select, rows, preferred, placeholder) {
  const keep = preferred !== undefined ? preferred : select.value;
  const options = rows.map(row => { const o=document.createElement('option');o.value=row.value;o.textContent=row.label;return o; });
  const empty=document.createElement('option');empty.value='';empty.textContent=placeholder;
  select.replaceChildren(empty,...options);
  select.value=rows.some(r=>r.value===keep)?keep:'';
}
function notice(text,type='hint') {
  const el=$('#runStatus');el.className=type==='error'?'run-notice error':type==='success'?'run-notice success':'run-notice';
  el.textContent=text;el.hidden=!text;
}
function readyProject(p) { return !!p?.has_reader; }
function selectedProject() { return projects.find(p=>p.name===$('#aniProject').value); }
function selectedVideo() { return inputs.videos.find(v=>v.name===$('#aniVideo').value); }
function pairing() {
  const p=selectedProject(),v=selectedVideo();
  return {p,v,mismatch:!!(p&&v?.project&&v.project!==p.name)};
}
function resetMismatch() { explicitConfirmation=false;$('#aniMismatch').checked=false; }
function summary() {
  const {p,v,mismatch}=pairing();
  const profile=p?.visual_profile==='soft-clean'?'实验柔光无地影':'旧版摄影棚';
  $('#pairSummary').textContent=p&&v?`将使用 ${p.name} 的识别器（${p.model_name||basename(p.model)||'未知模型'} / ${profile}），处理视频 ${v.name}。输出保存到 ${p.name}。`:'请明确选择已训练项目和视频。';
  const warning=$('#pairWarning');
  warning.textContent=!p?'未选择项目。':!readyProject(p)?'此项目尚无训练完成的识别器，不能生成动画。请先完成模型准备和训练。':!v?'未选择视频。':mismatch?`注意：视频人工标记属于 ${v.project}，当前选择的是 ${p.name}。如果是有意做跨模型对照，请勾选下方确认；不要误当成自动匹配。`:!v.project?'此视频尚未标记所属项目。系统无法仅凭文件名判断模型是否匹配，请对照下方首帧与项目参考图。':'已按人工标记匹配。标记不代表几何或动作质量检查通过。';
  warning.className='run-notice '+(mismatch||!readyProject(p)?'error':'');
  $('#mismatchWrap').hidden=!mismatch;
  $('#aniRun').disabled=busy||uploading>0||!readyProject(p)||!v||(mismatch&&!explicitConfirmation);
  $('#bindVideo').disabled=!p||!v||busy||uploading>0;
  $('#newRun').disabled=busy||uploading>0||!$('#newModel').value;
  $('#stepProject').textContent=p?`单步操作作用于：${p.name}（${profile}）`:'先选择目标项目';
  document.querySelectorAll('[data-step]').forEach(b=>b.disabled=!p||busy);
  $('#videoBinding').textContent=v?(v.project?`人工关联：${v.project}`:'尚未关联项目'):'未选择视频';
}
function previewVideo() {
  const v=selectedVideo();
  const el=$('#runVideo');
  if (!v) {
    el.removeAttribute('src');el.removeAttribute('poster');el.load();lastVideoURL='';$('#runVideoMeta').textContent='请选一个已上传的视频';summary();return;
  }
  const url=`/api/videos/${encodeURIComponent(v.name)}/file`;
  if(lastVideoURL!==url) {
    el.src=url;el.poster=`/api/videos/${encodeURIComponent(v.name)}/thumbnail.jpg`;
    el.preload='metadata';lastVideoURL=url;
  }
  $('#runVideoMeta').textContent=`${v.name} · ${bytes(v.size)} · ${v.project?'人工关联 '+v.project:'未关联'}`;
  el.onloadedmetadata=()=>{
    if(selectedVideo()?.name!==v.name)return;
    $('#runVideoMeta').textContent=`${v.name} · ${el.videoWidth}×${el.videoHeight} · ${Number.isFinite(el.duration)?el.duration.toFixed(2):'–'}s · ${bytes(v.size)}`;
  };
  summary();
}
async function previewProject() {
  const name=$('#aniProject').value,seq=++generation;
  const p=selectedProject();
  detail=null;
  const ref=$('#runReference');
  if (!p) {ref.removeAttribute('src');$('#runProjectMeta').textContent='请选择使用哪一个已训练项目';summary();return;}
  ref.src=p.has_reference?`/api/projects/${encodeURIComponent(name)}/file/reference_two_views.png`:'';
  $('#runProjectMeta').textContent=`${name} · ${p.model_name||basename(p.model)||'模型未准备'} · ${p.has_reader?'识别器已就绪':'识别器未就绪'}`;
  summary();
  try {
    const r=await opts.api(`/api/projects/${encodeURIComponent(name)}`);
    if(seq!==generation||name!==$('#aniProject').value)return;
    detail=r;
    const profile=r.studio?.visual_profile==='soft-clean'?'实验柔光无地影':'旧版摄影棚';
    $('#runProjectMeta').textContent=`${r.name} · ${r.model_name||basename(r.model)||'未准备模型'} · ${profile} · ${r.bones}骨 / ${r.keypoints}点 · ${r.has_reader?'识别器已就绪':'识别器未就绪'}`;
  } catch(e) { if(seq===generation)notice('项目详情加载失败：'+e.message,'error'); }
}
function setProject(name,notify=false) {
  if (!initialized) return;
  if (!projects.some(p=>p.name===name))name='';
  $('#aniProject').value=name;
  resetMismatch();remember();previewProject();
  if(notify&&name)opts.selectProject(name);
}
async function refresh({current,force=false}={}) {
  const sequence=++loadSequence;
  const response=await opts.api('/api/inputs');
  if(sequence!==loadSequence)return;
  inputs=response;projects=opts.projects();
  const initial=!initialized;
  const previousProject=$('#aniProject').value;
  const preferredProject=current ?? (initial?(opts.current()||saved.aniProject):previousProject);
  fill($('#newModel'),inputs.models.filter(m=>modelFile(m.name)).map(m=>({value:m.name,label:`${m.name} · ${bytes(m.size)}`})),initial?saved.newModel:$('#newModel').value,'请选择已上传的模型（仅用于新建项目）');
  fill($('#aniProject'),projects.map(p=>({value:p.name,label:`${p.name} · ${p.visual_profile==='soft-clean'?'柔光无影':'旧版'} · ${p.model_name||basename(p.model)||'无模型'}${p.has_reader?'':' · 未训练'}`})),preferredProject,'请选择用于本视频的项目 / 识别器');
  fill($('#aniVideo'),[...inputs.videos].sort((a,b)=>b.mtime-a.mtime).map(v=>({value:v.name,label:`${v.name} · ${v.project?'关联 '+v.project:'未关联'} · ${bytes(v.size)}`})),initial?saved.aniVideo:$('#aniVideo').value,'请选择已上传的视频');
  if(initial) {
    for(const id of ['newName','newFacing','newUp','newProfile','newProc','newDropProps','aniName','aniSolver','aniSwap','aniForce']) {
      const el=$('#'+id);if(saved[id]!==undefined) {if(el.type==='checkbox')el.checked=!!saved[id];else el.value=saved[id];}
    }
    initialized=true;
  }
  if(previousProject!==$('#aniProject').value) resetMismatch();
  previewVideo();await previewProject();remember();latestRefresh=Date.now();
}
async function upload(kind,files) {
  if(!files.length)return;
  const input=kind==='model'?$('#upModel'):$('#upVideo');
  const successes=[],failures=[];
  uploading++;summary();notice('');
  try {
    for(const file of files) {
      const target=kind==='model'?inputs.models:inputs.videos;
      if(target.some(x=>x.name===file.name)&&!confirm(`服务器已有 ${file.name}，覆盖上传会影响使用该文件的项目。继续覆盖此文件吗？`))continue;
      const fd=new FormData();fd.append('kind',kind);fd.append('file',file);
      try {
        await new Promise((resolve,reject)=>{
          const x=new XMLHttpRequest();x.open('POST','/api/upload');
          x.upload.onprogress=e=>{$('#upStatus').textContent=`上传 ${file.name}${e.lengthComputable?' '+Math.round(100*e.loaded/e.total)+'%':''}`;};
          x.onload=()=>x.status<300?resolve():reject(new Error(x.responseText));
          x.onerror=()=>reject(new Error('网络连接中断'));x.send(fd);
        });
        successes.push(file.name);
      }catch(e){failures.push(`${file.name}: ${e.message}`);}
    }
    await refresh();
    const candidate=kind==='model'?successes.find(modelFile):successes[successes.length-1];
    if(candidate) {
      if(kind==='model') {
        $('#newModel').value=candidate;
        if(!$('#newName').value)$('#newName').value=candidate.replace(/\.[^.]+$/,'').replace(/[^A-Za-z0-9_.-]/g,'_');
      }else {$('#aniVideo').value=candidate;resetMismatch();previewVideo();}
      remember();summary();
    }
    $('#upStatus').textContent=`上传成功 ${successes.length} 个${failures.length?`，失败 ${failures.length} 个`:''}${candidate?'；已选中 '+candidate:''}`;
    if(failures.length)notice(failures.join('\n'),'error');
  } finally {uploading--;input.value='';summary();}
}
async function submitAnimate() {
  summary();const {p,v,mismatch}=pairing();
  if(!readyProject(p)||!v||busy||uploading||mismatch&&!explicitConfirmation)return;
  const name=$('#aniName').value.trim();
  if(name&&!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name))return notice('结果名称请使用字母、数字、_ - .','error');
  const mode=$('#aniSolver').value;
  const msg=`确认生成动画？\n\n目标项目：${p.name}\n绑定模型：${p.model_name||basename(p.model)}\n摄影棚：${p.visual_profile==='soft-clean'?'实验柔光无地影':'旧版'}\n视频：${v.name}\n求解：${mode==='temporal'?'实验时序':'旧版'}\n结果名：${name||'(按视频名生成)'}\n\n请确认视频来自该项目参考图。${mismatch?'\n这是明确的跨项目对照运行。':''}`;
  if(!confirm(msg))return;
  busy=true;summary();notice('正在提交…');
  try {
    const j=await opts.submit(p.name,'animate',{video:v.name,name:name||undefined,swap:$('#aniSwap').checked,force:$('#aniForce').checked,solver:mode,confirm_mismatch:mismatch&&explicitConfirmation});
    if(j)notice(`任务已提交：${j.id}。目标项目 ${p.name}，视频 ${v.name}。`,'success');
  }finally{busy=false;summary();}
}
async function submitNew() {
  const n=$('#newName').value.trim(),model=$('#newModel').value;
  if(!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(n))return notice('项目名请使用字母、数字、_ - .，且以字母或数字开头','error');
  if(!model)return notice('请先选择模型','error');
  if(projects.some(p=>p.name===n&&p.bones>0))return notice('此项目已有模型，不能把新模型静默覆盖进去。请使用新项目名。','error');
  if(!confirm(`新建 / 准备项目：${n}\n模型：${model}\n将生成训练图、训练识别器并考试。\n这不会自动改变“视频生成动画”的目标项目。`))return;
  const extra=['--facing='+$('#newFacing').value,'--up='+$('#newUp').value,'--visual-profile',$('#newProfile').value];
  if($('#newProc').checked)extra.push('--procedural-material');
  extra.push($('#newDropProps').checked?'--drop-props':'--no-drop-props');
  busy=true;summary();
  try {const j=await opts.submit(n,'pipeline',{model,extra});if(j)notice(`项目任务已提交：${n}。训练完成后，请在下方选择此项目。`,'success');}
  finally{busy=false;summary();}
}
export function initRunPage(options) {
  opts=options;
  const fields=['newName','newModel','newFacing','newUp','newProfile','newProc','newDropProps','aniName','aniSolver','aniSwap','aniForce'];
  fields.forEach(id=>$('#'+id).addEventListener('change',()=>{remember();summary();}));
  fields.filter(id=>$('#'+id).tagName==='INPUT').forEach(id=>$('#'+id).addEventListener('input',remember));
  $('#aniProject').addEventListener('change',()=>setProject($('#aniProject').value,true));
  $('#aniVideo').addEventListener('change',()=>{resetMismatch();remember();previewVideo();});
  $('#aniMismatch').addEventListener('change',()=>{explicitConfirmation=$('#aniMismatch').checked;summary();});
  $('#refreshInputs').addEventListener('click',()=>refresh().catch(e=>notice(e.message,'error')));
  $('#bindVideo').addEventListener('click',async()=>{
    const {p,v}=pairing();if(!p||!v)return;
    if(!confirm(`将视频 ${v.name} 人工关联到 ${p.name}？\n只有你确认它由该项目参考图生成时才应关联。此标记不是自动识别。`))return;
    try{await opts.api('/api/video-binding',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video:v.name,project:p.name})});resetMismatch();await refresh();notice('人工关联已保存。','success');}catch(e){notice(e.message,'error');}
  });
  $('#upModel').addEventListener('change',e=>upload('model',[...e.target.files]).catch(e=>notice(e.message,'error')));
  $('#upVideo').addEventListener('change',e=>upload('video',[...e.target.files]).catch(e=>notice(e.message,'error')));
  $('#newRun').addEventListener('click',submitNew);
  $('#aniRun').addEventListener('click',submitAnimate);
  document.querySelectorAll('[data-step]').forEach(b=>b.addEventListener('click',()=>{
    const p=selectedProject();if(!p)return;
    if(confirm(`对项目 ${p.name} 执行“${b.textContent}”？\n模型：${p.model_name||basename(p.model)}`))opts.submit(p.name,b.dataset.step,{});
  }));
  return {
    refresh,
    syncProject(name) {if(initialized)setProject(name,false);},
    onProjects() {if(initialized&&Date.now()-latestRefresh>10000)refresh().catch(console.error);},
  };
}
