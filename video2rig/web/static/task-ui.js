// Presentation of observed tasks. No task mutations or inferred success states.
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const taskActive = t => ['queued','running'].includes(t?.status)||(t?.status==='unknown'&&(t?.source==='cli'||t?.read_only===true));
export function taskStatus(t) {
  const states={queued:['排队中',''],running:['处理中','run'],done:['执行完成','ok'],needs_review:['需检查','warning'],failed:['执行失败','bad'],cancelled:['已取消',''],interrupted:['已中断','warning'],ended_unknown:['进程已结束 · 结果待核实','warning'],unknown:['状态待核实','warning']};
  return states[t?.status]||[String(t?.status||'状态未知'),'warning'];
}
export const taskReadOnly = t => t?.source==='cli'||t?.read_only===true;
export const taskCanCancel = t => taskActive(t)&&!taskReadOnly(t)&&t?.can_cancel!==false;
export function taskPhaseHTML(t) {
  const p=t?.phase;if(!p)return '';
  const current=Number(p.current),total=Number(p.total);
  const real=typeof p.current==='number'&&typeof p.total==='number'&&Number.isFinite(current)&&Number.isFinite(total)&&total>0&&current>=0&&current<=total;
  const counter=real?`${current} / ${total}${p.unit?' '+p.unit:''}`:'';
  return `<div class="task-phase"><strong>${escapeHTML(p.label||'正在处理')}</strong>${real?`<span class="hint">${escapeHTML(counter)}</span><progress class="task-progress" max="${total}" value="${current}" aria-label="${escapeHTML(p.label||'阶段进度')}"></progress>`:''}${t.progress_updated_at?`<small class="hint">按最近日志更新，不代表整个流程百分比</small>`:''}</div>`;
}
export function taskSource(t) {return taskReadOnly(t)?'命令行 · 只读观测':'Web 任务';}
export function taskResultName(t) {return t?.result_name||t?.args?.name||(t?.args?.video||'').split(/[\\/]/).pop()?.replace(/\.[^.]+$/,'');}
export function taskAdvice(t) {
  if(taskReadOnly(t))return taskActive(t)?'观测到命令行任务。可查看阶段与日志；Web 不接管、取消或自动重试。':'进程已不再被观测到，不能据此判断成功或失败，请检查产物与日志。';
  return ({queued:'任务已排队；若存在命令行计算任务，将等待其结束后执行。',running:'正在服务端处理，离开或刷新页面不会停止。',done:'程序已执行完成。请检查自动结论并人工预览结果。',needs_review:'输入一致性需要关注。诊断已保存，可定位异常时间段。',failed:'执行失败。可以载入原输入和参数，使用新名称重新核对后提交。',cancelled:'本次执行已取消。中间文件不会自动清除；重试将使用新名称。',interrupted:'任务记录为中断状态。请先检查已有产物，再载入参数重新核对。'})[t?.status]||'当前状态尚不能形成执行或质量结论。';
}
