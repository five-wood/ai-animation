// Project-scoped generation and an independent create-and-train dialog.
// Manual video associations are declarations, never automatic identity checks.
const modelFile = name => /\.(fbx|glb|blend)$/i.test(name);
const validName = name => /^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(name);
const basename = value => String(value || '').split(/[\\/]/).pop();
const bytes = size => Number(size) >= 1048576 ? `${(Number(size) / 1048576).toFixed(1)} MB` : `${Math.round((Number(size) || 0) / 1024)} KB`;
const storageKey = 'v2r.run.v3';
const generationDefaults = () => ({video:'', name:'', viewOrder:'front-side', solver:'legacy'});
const creationDefaults = () => ({name:'', model:'', facing:'-Y', up:'+Z', profile:'legacy', procedural:false, dropProps:true});
const errorText = error => error?.message || String(error || '未知错误');

export function initRunPage(options) {
  const runRoot = document.querySelector('#tab-run #generateContent');
  const dialog = document.querySelector('dialog#createDialog');
  const createRoot = dialog?.querySelector('#createContent');
  if (!runRoot || !createRoot) throw new Error('运行页需要 #tab-run #generateContent 和 #createDialog #createContent');

  runRoot.innerHTML = `
    <form id="generateForm" class="form run-form" novalidate>
      <div class="toolbar run-scope"><p id="runProjectScope" class="hint"></p><button id="refreshInputs" class="text-button" type="button">刷新文件列表</button></div>
      <div id="runUntrained" class="run-notice warning" hidden><span id="runUntrainedText"></span> <button id="runGoTrain" class="text-button" type="button">前往训练</button></div>
      <div id="runStatus" class="run-notice" role="status" aria-live="polite" hidden></div>
      <div class="video-inputs"><div class="field"><label for="upVideo">上传动作视频</label><input id="upVideo" type="file" accept="video/*" aria-describedby="videoUploadHelp"><p id="videoUploadHelp" class="hint">同一角色的双视角视频；同名覆盖前将要求确认。</p></div><div class="field"><label for="aniVideo">或选择已上传的视频</label><select id="aniVideo"><option value="">请选择视频</option></select></div></div>
      <div id="videoUploadStatus" class="run-notice" role="status" aria-live="polite" hidden></div><progress id="videoUploadProgress" class="run-upload-progress" max="100" value="0" aria-label="视频上传进度" hidden></progress><button id="retryVideoUpload" class="secondary" type="button" hidden>重试失败的视频上传</button>
      <div class="run-previews">
        <figure><figcaption>项目参考图 · 核对角色与双视角</figcaption><div class="run-preview-media"><img id="runReference" alt="当前项目的正面与侧面参考图" hidden><div id="runReferenceEmpty" class="empty-state">准备模型后生成参考图</div></div><p id="runProjectMeta" class="hint"></p><a id="runReferenceDownload" class="text-button" download hidden>下载参考图</a></figure>
        <figure><figcaption>动作视频 · 首帧与播放预览</figcaption><div class="run-preview-media"><video id="runVideo" controls muted playsinline preload="metadata" hidden></video><div id="runVideoEmpty" class="empty-state">选择视频后显示预览</div></div><p id="runVideoMeta" class="hint"></p></figure>
      </div>
      <div class="toolbar"><span id="videoBinding" class="badge"></span><button id="bindVideo" class="text-button" type="button">保存人工关联（可选）</button></div>
      <div id="pairWarning" class="run-notice" role="status" hidden></div>
      <label id="unboundWrap" class="check-row" hidden><input id="aniUnbound" type="checkbox"> 我已核对参考图和视频，确认视频使用当前项目的角色（不保存服务器关联）</label>
      <div class="form-grid"><div class="field"><label for="aniName">结果名称 <span class="hint">可选</span></label><input id="aniName" autocomplete="off" placeholder="留空使用视频文件名" aria-describedby="runNameHelp"><p id="runNameHelp" class="hint">字母、数字、下划线、短横线或点；不能以点开头。</p></div><div class="field"><label for="aniViewOrder">视频画面顺序</label><select id="aniViewOrder"><option value="front-side">左：正面 · 右：侧面</option><option value="side-front">左：侧面 · 右：正面</option></select><p class="hint">按视频实际左右画面选择，不会自动识别顺序。</p></div></div>
      <details id="runAdvanced" class="advanced"><summary>高级选项</summary><div class="form"><div class="field"><label for="aniSolver">动作求解器</label><select id="aniSolver"><option value="legacy">标准求解（默认）</option><option value="temporal">实验：可信度融合与时序求解</option></select><p class="hint">实验模式不提供自动锁脚；同名实验结果已存在时，服务器会拒绝覆盖。</p></div><label class="check-row"><input id="aniForce" type="checkbox"> 诊断：两画面不一致仍尝试继续</label><p class="hint">可能生成不可信动作；本次选择不记忆，不绕过项目匹配检查。</p><div id="mismatchWrap" hidden><label class="check-row"><input id="aniMismatch" type="checkbox"> 我确认有意进行跨项目诊断，接受模型不匹配的风险</label><p id="mismatchHelp" class="hint"></p></div></div></details>
      <p id="pairSummary" class="hint"></p>
      <div class="action-bar"><div><strong id="runActionTitle">生成动画</strong><p id="runDisabledReason" class="hint" aria-live="polite"></p></div><button id="aniRun" class="primary" type="submit" aria-describedby="runDisabledReason" disabled>生成动画</button><button id="runViewTasks" class="secondary" type="button" hidden>查看任务</button></div>
    </form>`;
  createRoot.innerHTML = `
    <form id="createForm" class="form create-form" novalidate>
      <div class="dialog-header"><div><p class="eyebrow">NEW PROJECT</p><h2 id="createHeading">创建项目并训练</h2></div><button id="closeCreate" class="text-button" type="button" aria-label="关闭创建项目对话框">关闭</button></div>
      <p class="hint">上传带骨骼的模型，完成准备 → 生成训练图 → 训练识别器 → 验证。提交后可在训练页查看进度。</p>
      <div id="createStatus" class="run-notice" role="status" aria-live="polite" hidden></div>
      <div class="field"><label for="upModel">上传模型与贴图</label><div class="upload-zone"><input id="upModel" type="file" multiple aria-describedby="modelUploadHelp"><p id="modelUploadHelp" class="hint">模型支持 FBX、GLB、.blend；可同时上传贴图等本地配套文件。服务器不会解压压缩包，也不会在此预览 3D 模型。同名文件上传会覆盖。</p></div></div>
      <div id="modelUploadStatus" class="run-notice" role="status" aria-live="polite" hidden></div><progress id="modelUploadProgress" class="run-upload-progress" max="100" value="0" aria-label="模型上传进度" hidden></progress><button id="retryModelUpload" class="secondary" type="button" hidden>重试失败的模型或贴图上传</button>
      <div class="field"><label for="newModel">使用模型</label><select id="newModel" required><option value="">请选择已上传的模型</option></select></div>
      <div class="field"><label for="newName">项目名称</label><input id="newName" required autocomplete="off" placeholder="例如 creature_01" aria-describedby="createNameHelp"><p id="createNameHelp" class="hint">字母、数字、下划线、短横线或点；不能以点开头。已有骨架、识别器或训练数据的项目不可覆盖，请用新名称。</p></div>
      <div class="run-notice">参考图将在“准备模型”步骤完成后生成。此处无法预先显示实际参考图；若朝向不正确，请停止后使用新项目名调整方向重新准备。</div>
      <details class="advanced"><summary>高级：方向与外观</summary><div class="form-grid"><div class="field"><label for="newFacing">正面朝向</label><select id="newFacing"><option>-Y</option><option>+Y</option><option>-X</option><option>+X</option></select></div><div class="field"><label for="newUp">模型头顶朝向</label><select id="newUp"><option value="+Z">+Z（默认已站立）</option><option>-Y</option><option>+Y</option><option>-X</option><option>+X</option><option>-Z</option></select></div></div><div class="field"><label for="newProfile">摄影棚配置</label><select id="newProfile"><option value="legacy">标准摄影棚（默认）</option><option value="soft-clean">实验：柔光无地影</option></select><p class="hint">实验外观尚无已证明的精度收益，必须与本项目训练的识别器配套使用。</p></div><label class="check-row"><input id="newProc" type="checkbox"> 贴图缺失时使用程序材质</label><label class="check-row"><input id="newDropProps" type="checkbox" checked> 移除独立道具（如枪械）</label><p class="hint">移除独立根骨骼上的道具网格与关键点，不改变骨架；取消勾选则保留。</p></details>
      <div class="action-bar"><div><strong>创建项目并训练</strong><p id="createDisabledReason" class="hint" aria-live="polite"></p></div><button id="newRun" class="primary" type="submit" aria-describedby="createDisabledReason" disabled>创建并开始训练</button><button id="createViewTrain" class="secondary" type="button" hidden>前往训练</button></div>
    </form>`;
  dialog.setAttribute('aria-labelledby', 'createHeading');
  const $ = id => runRoot.querySelector(`#${id}`) || createRoot.querySelector(`#${id}`);
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(storageKey) || '{}') || {}; } catch { /* private browsing / invalid drafts */ }
  const drafts = Object.create(null);
  if (stored.projects && typeof stored.projects === 'object' && !Array.isArray(stored.projects)) {
    for (const [name, value] of Object.entries(stored.projects)) {
      if (value && typeof value === 'object') drafts[name] = cleanDraft(value);
    }
  }
  let creation = cleanCreation(stored.create);
  let activeName = '';
  let projectEpoch = 0;
  let projectDetail = null;
  let detailSequence = 0;
  let inputSequence = 0;
  let inputs = {models:[], videos:[]};
  let inputsLoaded = false;
  let refreshPending = 0;
  let actionBusy = '';
  let acceptedCreation = '';
  const acceptedCreationNames = new Set();
  let lastVideoURL = '';
  let projectFingerprint = '';
  const videoUploads = new Map();
  let modelUpload = null;

  function cleanDraft(value = {}) {
    return {video:typeof value.video === 'string' ? value.video : '', name:typeof value.name === 'string' ? value.name : '', viewOrder:value.viewOrder === 'side-front' ? 'side-front' : 'front-side', solver:value.solver === 'temporal' ? 'temporal' : 'legacy'};
  }
  function cleanCreation(value) {
    const v = value && typeof value === 'object' ? value : {};
    const defaults = creationDefaults();
    return {name:typeof v.name === 'string' ? v.name : '', model:typeof v.model === 'string' ? v.model : '', facing:['-Y','+Y','-X','+X'].includes(v.facing) ? v.facing : defaults.facing, up:['+Z','-Z','+Y','-Y','+X','-X'].includes(v.up) ? v.up : defaults.up, profile:v.profile === 'soft-clean' ? 'soft-clean' : 'legacy', procedural:v.procedural === true, dropProps:v.dropProps !== false};
  }
  function persist() {
    try { localStorage.setItem(storageKey, JSON.stringify({projects:drafts, create:creation})); } catch { /* storage is optional */ }
  }
  function allProjects() { const rows = options.projects(); return Array.isArray(rows) ? rows : []; }
  function currentProject() {
    const summary = allProjects().find(p => p.name === activeName);
    return projectDetail?.name === activeName ? {...summary, ...projectDetail} : summary;
  }
  function selectedVideo() { return inputs.videos.find(v => v.name === $('aniVideo').value); }
  function draft(name = activeName) { return drafts[name] || (drafts[name] = generationDefaults()); }
  function saveGeneration() {
    if (!activeName) return;
    drafts[activeName] = cleanDraft({video:$('aniVideo').value || draft().video, name:$('aniName').value, viewOrder:$('aniViewOrder').value, solver:$('aniSolver').value});
    persist();
  }
  function saveCreation() {
    creation = cleanCreation({name:$('newName').value, model:inputsLoaded ? $('newModel').value : ($('newModel').value || creation.model), facing:$('newFacing').value, up:$('newUp').value, profile:$('newProfile').value, procedural:$('newProc').checked, dropProps:$('newDropProps').checked});
    persist();
  }
  function setNotice(id, text = '', type = '') {
    const el = $(id);
    el.className = `run-notice${type ? ' ' + type : ''}`;
    el.textContent = text;
    el.hidden = !text;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
  }
  function fill(select, rows, preferred, placeholder) {
    const empty = document.createElement('option');
    empty.value = ''; empty.textContent = placeholder;
    const nodes = rows.map(row => {
      const option = document.createElement('option'); option.value = row.value; option.textContent = row.label; return option;
    });
    select.replaceChildren(empty, ...nodes);
    select.value = rows.some(row => row.value === preferred) ? preferred : '';
  }
  function fillModels() {
    fill($('newModel'), inputs.models.filter(m => modelFile(m.name)).map(m => ({value:m.name, label:`${m.name} · ${bytes(m.size)}`})), $('newModel').value || creation.model, '请选择已上传的 FBX / GLB / .blend 模型');
  }
  function fillVideos() {
    fill($('aniVideo'), [...inputs.videos].sort((a,b) => (b.mtime || 0) - (a.mtime || 0)).map(v => ({value:v.name, label:`${v.name} · ${v.project ? '关联 ' + v.project : '未关联'} · ${bytes(v.size)}`})), draft().video, '请选择已上传的视频');
  }
  function resetMatching() { $('aniUnbound').checked = false; $('aniMismatch').checked = false; $('aniForce').checked = false; }
  function pairing() {
    const p = currentProject(), v = selectedVideo();
    return {p, v, mismatch:!!(p && v?.project && v.project !== p.name), unbound:!!(v && !v.project)};
  }
  function generationReason() {
    const {p,v,mismatch,unbound} = pairing();
    if (actionBusy) return actionBusy === 'animate' ? '正在提交动画任务，请勿重复操作。' : '另一项操作正在提交，请稍候。';
    if (!activeName) return '请先在工作台选择一个项目。';
    if (!p) return '正在读取当前项目，或项目已不存在。';
    if (!p.has_reader) return '当前项目尚无训练完成的识别器，请先完成训练。';
    if (videoUploads.get(activeName)?.busy) return '当前项目的视频正在上传，请等待完成。';
    if (!v) return '请选择或上传一个动作视频。';
    if ($('aniName').value.trim() && !validName($('aniName').value.trim())) return '结果名称不合法，请修改名称。';
    if (mismatch && !$('aniMismatch').checked) return '视频关联了其他项目：请在高级选项中明确确认跨项目诊断，或换一个视频。';
    if (unbound && !$('aniUnbound').checked) return '请勾选上方核对确认；无需保存服务器关联即可生成。';
    return '';
  }
  function creationReason() {
    if (actionBusy) return actionBusy === 'pipeline' ? '正在提交创建任务，请勿重复操作。' : '另一项操作正在提交，请稍候。';
    if (acceptedCreation) return `项目 ${acceptedCreation} 已提交，勿重复创建。可前往训练查看。`;
    if (modelUpload?.busy) return '模型或配套文件正在上传，请等待完成。';
    if (!$('newModel').value) return '请选择或上传一个带骨骼的模型。';
    const name = $('newName').value.trim();
    if (!name) return '请输入项目名称。';
    if (!validName(name)) return '项目名称不合法，请使用字母、数字、下划线、短横线或点，且不能以点开头。';
    if (acceptedCreationNames.has(name)) return '此名称的创建任务已接受，请使用其他名称或前往训练查看，勿重复提交。';
    const existing = allProjects().find(p => p.name === name);
    if (existing && (existing.bones > 0 || existing.model || existing.has_reader || existing.samples > 0 || (existing.stage && existing.stage !== 'empty'))) return '同名项目已有模型或训练数据，不能覆盖。请使用新项目名称。';
    return '';
  }
  function updateState() {
    const {p,v,mismatch,unbound} = pairing();
    const reason = generationReason();
    $('runProjectScope').textContent = activeName ? `当前项目：${activeName} · 使用该项目的模型与识别器，输出也保存在该项目。` : '项目由工作台顶部选择器统一管理。';
    $('runActionTitle').textContent = activeName ? `生成到 ${activeName}` : '生成动画';
    $('runDisabledReason').textContent = reason || '已就绪。提交后在任务中心跟踪，完成后查看结果。';
    $('aniRun').disabled = !!reason;
    $('aniRun').textContent = actionBusy === 'animate' ? '正在提交…' : '生成动画';
    $('runUntrained').hidden = !p || !!p.has_reader;
    $('runUntrainedText').textContent = p && !p.has_reader ? '当前项目尚未训练完成，暂不能生成动画。' : '';
    $('upVideo').disabled = !activeName || !!videoUploads.get(activeName)?.busy || !!actionBusy;
    $('aniVideo').disabled = !activeName || !!actionBusy;
    $('bindVideo').disabled = !p || !v || !!actionBusy || !!videoUploads.get(activeName)?.busy || v.project === p.name;
    $('bindVideo').textContent = v?.project === p?.name && p ? '已关联当前项目' : '保存人工关联（可选）';
    $('videoBinding').textContent = v ? (v.project ? `人工关联：${v.project}` : '未关联项目') : '尚未选择视频';
    $('unboundWrap').hidden = !unbound;
    $('mismatchWrap').hidden = !mismatch;
    $('mismatchHelp').textContent = mismatch ? `视频标记属于 ${v.project}，当前项目为 ${p.name}。此确认不修改服务器关联，也不能证明视频角色匹配。` : '';
    setNotice('pairWarning', mismatch ? `项目不匹配：视频人工关联到 ${v.project}。如非有意跨模型诊断，请换用正确项目或视频。` : unbound ? '此视频尚无人工关联。请核对两侧预览，并勾选确认；关联保存是可选操作。' : v ? '人工关联与当前项目一致；此标记不代表几何或动作质量检查通过。' : '', mismatch ? 'warning' : '');
    $('pairSummary').textContent = p && v ? `将使用 ${p.name} 的识别器处理 ${v.name}；画面顺序：${$('aniViewOrder').value === 'side-front' ? '左侧面 / 右正面' : '左正面 / 右侧面'}。` : '';
    const createReason = creationReason();
    $('newRun').disabled = !!createReason;
    $('newRun').textContent = actionBusy === 'pipeline' ? '正在提交…' : '创建并开始训练';
    $('createDisabledReason').textContent = createReason || '将依次准备模型、生成训练图、训练并验证；提交即进入任务队列。';
    $('upModel').disabled = !!modelUpload?.busy || !!actionBusy || !!acceptedCreation;
    $('retryModelUpload').disabled = !!actionBusy || !!acceptedCreation;
    $('retryVideoUpload').disabled = !!actionBusy;
    $('createViewTrain').hidden = !acceptedCreation;
    $('refreshInputs').disabled = refreshPending > 0;
    $('refreshInputs').textContent = refreshPending > 0 ? '正在刷新…' : '刷新文件列表';
    runRoot.querySelector('form').setAttribute('aria-busy', String(actionBusy === 'animate'));
    createRoot.querySelector('form').setAttribute('aria-busy', String(actionBusy === 'pipeline'));
  }
  function renderUpload(kind) {
    const upload = kind === 'model' ? modelUpload : videoUploads.get(activeName);
    const prefix = kind === 'model' ? 'model' : 'video';
    setNotice(prefix + 'UploadStatus', upload?.text || '', upload?.type || '');
    const progress = $(prefix + 'UploadProgress');
    progress.hidden = !upload?.busy;
    if (upload?.percent == null) progress.removeAttribute('value'); else progress.value = upload.percent;
    const retry = $(kind === 'model' ? 'retryModelUpload' : 'retryVideoUpload');
    retry.hidden = !!upload?.busy || !upload?.retryFiles?.length;
    retry.disabled = !!actionBusy;
  }
  function renderVideo() {
    const v = selectedVideo(), el = $('runVideo');
    $('runVideoEmpty').hidden = !!v; el.hidden = !v;
    if (!v) {
      el.onloadedmetadata = null; el.onerror = null;
      if (lastVideoURL) { el.pause(); el.removeAttribute('src'); el.removeAttribute('poster'); el.load(); }
      lastVideoURL = ''; $('runVideoMeta').textContent = '上传或选择视频后，可与项目参考图并排核对。';
      updateState(); return;
    }
    const token = projectEpoch, name = v.name;
    const url = `/api/videos/${encodeURIComponent(name)}/file?t=${encodeURIComponent(v.mtime || 0)}`;
    $('runVideoMeta').textContent = `${name} · ${bytes(v.size)}`;
    el.onloadedmetadata = () => {
      if (token !== projectEpoch || selectedVideo()?.name !== name) return;
      $('runVideoMeta').textContent = `${name} · ${el.videoWidth}×${el.videoHeight} · ${Number.isFinite(el.duration) ? el.duration.toFixed(2) : '–'} 秒 · ${bytes(v.size)}`;
    };
    el.onerror = () => {
      if (token === projectEpoch && selectedVideo()?.name === name) $('runVideoMeta').textContent = `${name} · 浏览器无法播放此编码，可尝试查看首帧或换用兼容视频。`;
    };
    if (lastVideoURL !== url) {
      el.src = url; el.poster = `/api/videos/${encodeURIComponent(name)}/thumbnail.jpg?t=${encodeURIComponent(v.mtime || 0)}`; lastVideoURL = url;
    }
    updateState();
  }
  function renderProject() {
    const p = currentProject(), image = $('runReference'), download = $('runReferenceDownload');
    const hasReference = !!p?.has_reference;
    image.hidden = !hasReference; $('runReferenceEmpty').hidden = hasReference; download.hidden = !hasReference;
    if (hasReference) {
      const url = `/api/projects/${encodeURIComponent(activeName)}/file/${encodeURIComponent('reference_two_views.png')}?t=${encodeURIComponent(p.mtime || 0)}`;
      image.onload = () => {
        if (image.getAttribute('src') !== url) return;
        image.hidden = false; $('runReferenceEmpty').hidden = true;
      };
      image.onerror = () => {
        if (image.getAttribute('src') !== url) return;
        image.hidden = true; $('runReferenceEmpty').hidden = false; $('runReferenceEmpty').textContent = '参考图暂时无法加载，请刷新重试。';
      };
      if (image.getAttribute('src') !== url) image.src = url;
      download.href = url;
    } else {
      image.onload = null; image.onerror = null; image.removeAttribute('src'); download.removeAttribute('href');
      $('runReferenceEmpty').textContent = activeName ? '参考图将在准备模型完成后生成' : '选择项目后显示参考图';
    }
    const profile = (p?.studio?.visual_profile || p?.visual_profile) === 'soft-clean' ? '实验柔光无地影' : '标准摄影棚';
    $('runProjectMeta').textContent = p ? `${p.name} · ${p.model_name || basename(p.model) || '模型尚未准备'} · ${profile} · ${p.has_reader ? '识别器已就绪' : '识别器未就绪'}` : activeName ? '正在读取项目详情…' : '未选择项目';
    updateState();
  }
  async function loadProject() {
    const name = activeName, epoch = projectEpoch, sequence = ++detailSequence;
    if (!name) return;
    try {
      const result = await options.api(`/api/projects/${encodeURIComponent(name)}`);
      if (sequence !== detailSequence || epoch !== projectEpoch || name !== activeName || name !== (options.current() || '')) return;
      projectDetail = result; renderProject();
    } catch (error) {
      if (sequence === detailSequence && epoch === projectEpoch && name === activeName) setNotice('runStatus', '项目详情加载失败：' + errorText(error), 'error');
    }
  }
  function applyGeneration() {
    const value = activeName ? draft() : generationDefaults();
    $('aniName').value = value.name; $('aniViewOrder').value = value.viewOrder; $('aniSolver').value = value.solver;
    fillVideos(); resetMatching();
    $('runAdvanced').open = !!pairing().mismatch;
    renderVideo(); renderProject(); renderUpload('video');
  }
  function syncProject() {
    // options.current() is the only authoritative project selection, including after awaits.
    const name = options.current() || '';
    if (name === activeName) { renderProject(); return; }
    saveGeneration(); activeName = name; projectEpoch++; detailSequence++; projectDetail = null;
    setNotice('runStatus'); $('runViewTasks').hidden = true;
    // Never retain a FileList captured for another project.
    $('upVideo').value = '';
    applyGeneration();
    void loadProject();
  }
  async function refresh({reportErrors = true} = {}) {
    syncProject();
    const requestEpoch = projectEpoch;
    const sequence = ++inputSequence;
    refreshPending++; updateState();
    try {
      const response = await options.api('/api/inputs');
      if (sequence !== inputSequence) return;
      syncProject();
      const previous = selectedVideo();
      inputs = {models:Array.isArray(response.models) ? response.models : [], videos:Array.isArray(response.videos) ? response.videos : []};
      inputsLoaded = true;
      fillModels(); fillVideos();
      const next = selectedVideo();
      if (previous?.name !== next?.name || previous?.project !== next?.project || previous?.mtime !== next?.mtime) resetMatching();
      renderVideo(); renderProject();
      await loadProject();
    } catch (error) {
      if (reportErrors && sequence === inputSequence) {
        if (requestEpoch === projectEpoch) setNotice('runStatus', '文件列表刷新失败，已保留输入：' + errorText(error), 'error');
        if (dialog.open) setNotice('createStatus', '文件列表刷新失败，已保留输入：' + errorText(error), 'error');
      }
      throw error;
    } finally { refreshPending--; updateState(); }
  }
  function onProjects() {
    syncProject();
    const p = allProjects().find(row => row.name === activeName);
    const fingerprint = JSON.stringify(p || null);
    if (fingerprint !== projectFingerprint) {
      projectFingerprint = fingerprint; projectDetail = null; renderProject(); void loadProject();
    }
    updateState();
  }
  function xhrUpload(kind, file, state, render) {
    return new Promise((resolve, reject) => {
      const data = new FormData(); data.append('kind', kind); data.append('file', file);
      const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/upload');
      xhr.upload.onprogress = event => {
        state.percent = event.lengthComputable ? Math.round(event.loaded * 100 / event.total) : null;
        state.text = `正在上传 ${file.name}${state.percent == null ? '' : ' · ' + state.percent + '%'}`; render();
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else {
          let detail = xhr.responseText;
          try { detail = JSON.parse(detail).detail || detail; } catch { /* non-JSON server error */ }
          reject(new Error(`${xhr.status} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`));
        }
      };
      xhr.onerror = () => reject(new Error('网络连接中断，请重试'));
      xhr.onabort = () => reject(new Error('上传已取消'));
      xhr.send(data);
    });
  }
  async function upload(kind, files) {
    if (!files.length) return;
    syncProject();
    const originName = activeName, originEpoch = projectEpoch;
    const originalVideo = draft(originName).video;
    const originalModel = creation.model;
    const originalCreationName = creation.name;
    if ((kind === 'video' && (!originName || videoUploads.get(originName)?.busy)) || (kind === 'model' && modelUpload?.busy) || actionBusy) return;
    const input = $(kind === 'model' ? 'upModel' : 'upVideo');
    const state = {busy:true, percent:0, text:'正在检查上传…', type:'', retryFiles:[]};
    if (kind === 'model') modelUpload = state; else videoUploads.set(originName, state);
    const render = () => { if (kind === 'model' || activeName === originName) renderUpload(kind); };
    const successes = [], failures = [], skipped = [];
    render(); updateState();
    try {
      // Fetch a fresh collision list before any overwrite, not a possibly stale page snapshot.
      const listing = await options.api('/api/inputs');
      const names = new Set((kind === 'model' ? listing.models || [] : listing.videos || []).map(row => row.name));
      for (const file of files) {
        if (!validName(file.name)) { failures.push(`${file.name}：文件名只能包含字母、数字、_ - .，且不能以点开头，请在本地重命名`); continue; }
        if (names.has(file.name) && !window.confirm(`服务器已有同名文件 ${file.name}。覆盖会影响使用它的所有项目${kind === 'video' ? '，且原人工关联仍会保留' : ''}。确认覆盖此文件？`)) { skipped.push(file.name); continue; }
        try {
          state.text = `正在上传 ${file.name}`; state.percent = 0; render();
          await xhrUpload(kind, file, state, render);
          successes.push(file.name); names.add(file.name);
        } catch (error) { failures.push(`${file.name}：${errorText(error)}`); state.retryFiles.push(file); }
      }
      // Global input lists may refresh, but only the captured project's draft may adopt this upload.
      let listRefreshFailed = false;
      try { await refresh({reportErrors:false}); } catch { listRefreshFailed = true; }
      const candidate = kind === 'model' ? successes.find(modelFile) : successes.at(-1);
      let adopted = false;
      if (candidate && kind === 'model' && creation.model === originalModel) {
        creation.model = candidate;
        if (!creation.name.trim() && creation.name === originalCreationName) creation.name = candidate.replace(/\.[^.]+$/, '');
        $('newModel').value = candidate; $('newName').value = creation.name; persist(); adopted = true;
      } else if (candidate && kind === 'video' && draft(originName).video === originalVideo && (activeName !== originName || projectEpoch === originEpoch)) {
        draft(originName).video = candidate; persist(); adopted = true;
        if (activeName === originName && projectEpoch === originEpoch) {
          fillVideos(); resetMatching(); renderVideo();
        }
      }
      state.text = `上传成功 ${successes.length} 个${skipped.length ? `，跳过 ${skipped.length} 个` : ''}${failures.length ? `；失败 ${failures.length} 个：${failures.join('；')}` : ''}${listRefreshFailed ? '；文件列表刷新失败，请手动刷新。' : adopted ? `；已为${kind === 'video' ? '项目 ' + originName : '新建项目'}选择 ${candidate}` : candidate ? '；保留了你在上传期间修改的选择。' : ''}`;
      state.type = failures.length || listRefreshFailed ? 'error' : successes.length ? 'success' : '';
      // Preserve the FileList after any failure so the user can retry without re-selecting files.
      if (!failures.length && !listRefreshFailed && (kind === 'model' || (activeName === originName && projectEpoch === originEpoch))) input.value = '';
    } catch (error) { state.text = '上传失败，已保留输入：' + errorText(error); state.type = 'error'; state.retryFiles = files; }
    finally { state.busy = false; render(); updateState(); }
  }
  function showGenerationError(text, focusId) {
    setNotice('runStatus', text, 'error');
    if (focusId) $(focusId).focus();
  }
  async function submitAnimate(event) {
    event.preventDefault(); syncProject(); saveGeneration();
    const reason = generationReason();
    if (reason) { showGenerationError(reason); return; }
    const {p,v,mismatch} = pairing();
    const name = $('aniName').value.trim(), solver = $('aniSolver').value;
    const resultName = name || v.name.replace(/\.[^.]+$/, '') + (solver === 'temporal' ? '_temporal' : '');
    if (!validName(resultName)) { showGenerationError('视频默认结果名不合法，请手动填写结果名称。', 'aniName'); return; }
    if (p.outputs?.includes(resultName)) {
      if (solver === 'temporal') { showGenerationError('同名实验结果已存在，请填写新的结果名称。', 'aniName'); return; }
      if (!window.confirm(`项目 ${p.name} 已有结果 ${resultName}。标准求解可能覆盖其中的结果文件。确认继续？建议填写新名称保留旧结果。`)) return;
    }
    const originEpoch = projectEpoch;
    const args = {video:v.name, name:name || undefined, swap:$('aniViewOrder').value === 'side-front', solver, force:$('aniForce').checked, confirm_mismatch:mismatch && $('aniMismatch').checked};
    actionBusy = 'animate'; updateState(); setNotice('runStatus', '正在提交动画任务…');
    try {
      const job = await options.submit(p.name, 'animate', args);
      if (!job) {
        if (originEpoch === projectEpoch) setNotice('runStatus', '任务未被接受，请检查任务提示后重试；输入已保留。', 'error');
        return;
      }
      if (originEpoch === projectEpoch) { setNotice('runStatus', `任务已接受${job.id ? '：' + job.id : ''}。目标项目 ${p.name}，视频 ${v.name}。`, 'success'); $('runViewTasks').hidden = false; }
      options.notify?.(`动画任务已接受：${p.name} / ${v.name}。可在任务中心查看。`, 'success');
    } catch (error) {
      if (originEpoch === projectEpoch) setNotice('runStatus', '提交失败，输入已保留：' + errorText(error), 'error');
    } finally { actionBusy = ''; updateState(); }
  }
  async function bindVideo() {
    syncProject();
    const {p,v,mismatch,unbound} = pairing();
    if (!p || !v || actionBusy || videoUploads.get(activeName)?.busy) return;
    if (v.project === p.name) { setNotice('runStatus', '此视频已关联当前项目，无需重复保存。', 'success'); return; }
    if (unbound && !$('aniUnbound').checked) { showGenerationError('保存关联前，请先核对两侧预览并勾选角色匹配确认。', 'aniUnbound'); return; }
    if (mismatch && !window.confirm(`此视频当前人工关联到 ${v.project}。确定将 ${v.name} 的全局人工关联改为 ${p.name}？这会影响所有使用该视频的项目；只有核对它确实来自当前项目参考图后才应更改。`)) return;
    const originEpoch = projectEpoch;
    actionBusy = 'binding'; updateState();
    try {
      await options.api('/api/video-binding', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({video:v.name, project:p.name})});
      const row = inputs.videos.find(video => video.name === v.name); if (row) row.project = p.name;
      if (originEpoch === projectEpoch) { resetMatching(); renderVideo(); setNotice('runStatus', '人工关联已保存；这是手动标记，不是自动身份验证。', 'success'); }
      // A pending pre-binding input request must never restore the old binding.
      inputSequence++;
      try { await refresh(); } catch { /* refresh reports an in-scope error */ }
    } catch (error) { if (originEpoch === projectEpoch) setNotice('runStatus', '关联保存失败：' + errorText(error), 'error'); }
    finally { actionBusy = ''; updateState(); }
  }
  async function finishCreation(name) {
    await options.selectProject(name);
    await options.navigate('train');
    dialog.close();
  }
  async function submitNew(event) {
    event.preventDefault(); saveCreation();
    const reason = creationReason();
    if (reason) { setNotice('createStatus', reason, 'error'); return; }
    const name = $('newName').value.trim(), model = $('newModel').value;
    const extra = [`--facing=${$('newFacing').value}`, `--up=${$('newUp').value}`, '--visual-profile', $('newProfile').value];
    if ($('newProc').checked) extra.push('--procedural-material');
    extra.push($('newDropProps').checked ? '--drop-props' : '--no-drop-props');
    actionBusy = 'pipeline'; updateState(); setNotice('createStatus', '正在提交创建与训练任务…');
    try {
      const job = await options.submit(name, 'pipeline', {model, extra});
      if (!job) { setNotice('createStatus', '创建任务未被接受，请检查名称或任务提示后重试；输入已保留。', 'error'); return; }
      acceptedCreation = name; acceptedCreationNames.add(name); updateState();
      setNotice('createStatus', `项目 ${name} 的创建与训练任务已接受。参考图在准备步骤完成后生成。`, 'success');
      options.notify?.(`项目 ${name} 已提交创建与训练。`, 'success');
      try { await finishCreation(name); }
      catch (error) { setNotice('createStatus', `任务已接受，勿重复提交。切换到训练页失败：${errorText(error)}。请点击“前往训练”重试。`, 'warning'); }
    } catch (error) { setNotice('createStatus', '创建失败，输入已保留：' + errorText(error), 'error'); }
    finally { actionBusy = ''; updateState(); }
  }
  async function openCreate() {
    if (!dialog.open) dialog.showModal();
    $('newName').focus();
    if (!inputsLoaded) { try { await refresh(); } catch { /* refresh reports inside the dialog */ } }
    updateState();
  }

  $('generateForm').addEventListener('submit', submitAnimate);
  $('createForm').addEventListener('submit', submitNew);
  $('aniVideo').addEventListener('change', () => {
    if (activeName) { draft().video = $('aniVideo').value; persist(); }
    resetMatching(); setNotice('runStatus'); $('runViewTasks').hidden = true;
    if (pairing().mismatch) $('runAdvanced').open = true;
    renderVideo();
  });
  for (const id of ['aniName','aniViewOrder','aniSolver']) {
    $(id).addEventListener(id === 'aniName' ? 'input' : 'change', () => { saveGeneration(); updateState(); });
  }
  for (const id of ['aniUnbound','aniMismatch','aniForce']) $(id).addEventListener('change', updateState);
  for (const id of ['newName','newModel','newFacing','newUp','newProfile','newProc','newDropProps']) {
    $(id).addEventListener(id === 'newName' ? 'input' : 'change', () => {
      if (acceptedCreation && $('newName').value.trim() !== acceptedCreation) acceptedCreation = '';
      saveCreation(); updateState();
    });
  }
  $('refreshInputs').addEventListener('click', () => { void refresh().catch(() => {}); });
  $('upModel').addEventListener('change', event => { void upload('model', Array.from(event.target.files || [])); });
  $('upVideo').addEventListener('change', event => { void upload('video', Array.from(event.target.files || [])); });
  $('retryModelUpload').addEventListener('click', () => { void upload('model', modelUpload?.retryFiles || []); });
  $('retryVideoUpload').addEventListener('click', () => { void upload('video', videoUploads.get(activeName)?.retryFiles || []); });
  $('bindVideo').addEventListener('click', () => { void bindVideo(); });
  $('closeCreate').addEventListener('click', () => dialog.close());
  $('runGoTrain').addEventListener('click', () => { options.navigate('train'); });
  $('runViewTasks').addEventListener('click', () => { options.navigate('tasks'); });
  $('createViewTrain').addEventListener('click', async () => {
    if (!acceptedCreation || actionBusy) return;
    try { await finishCreation(acceptedCreation); }
    catch (error) { setNotice('createStatus', '切换到训练页失败：' + errorText(error), 'error'); }
  });
  $('newName').value = creation.name; $('newFacing').value = creation.facing; $('newUp').value = creation.up;
  $('newProfile').value = creation.profile; $('newProc').checked = creation.procedural; $('newDropProps').checked = creation.dropProps;
  activeName = options.current() || '';
  fillModels(); applyGeneration();

  return {refresh, syncProject, onProjects, openCreate};
}
