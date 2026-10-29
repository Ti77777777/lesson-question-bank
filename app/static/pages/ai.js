import { mountProviderSettings } from '../lib/provider-settings.js';
import { api, fileUrl, pdfUrl } from '../lib/api.js';
import { esc } from '../lib/dom.js';
import { flash } from '../lib/ui.js';
import { upgradeMath } from '../lib/math.js';
import { EXAM_TYPES, SOURCE_GRADES } from '../lib/constants.js';
import { openRegionEditor } from '../lib/region-editor.js';
import { openAIEditor } from '../lib/ai-editor.js';

const STATES={queued:'等待处理',running:'处理中',paused:'已暂停',budget_paused:'预算用尽',completed:'处理完成',partial:'部分待处理',failed:'处理失败',cancelled:'已取消',recognized:'已识题',solved:'已解答',ready:'待教师确认',adopted:'已采纳',rejected:'已舍弃'};
const QUALITY={pending:'待复核',no_issue_found:'AI自复核未发现问题',needs_review:'需重点复核',needs_input:'需补充信息',conflict:'存在分歧'};
const TYPE={single:'单选',multi:'多选',blank:'填空',solve:'解答'};
const rich=t=>`<div data-rich>${esc(t||'')}</div>`;
const json=t=>JSON.stringify(t,null,2);

export default async function mount(root,ctx){
  let selected=ctx.query.job||'', detail=null, selectedQ='', files=[], busy=false, disposed=false;
  root.classList.add('ai-page');
  ctx.onCleanup(()=>{disposed=true;root.classList.remove('ai-page');});
  root.innerHTML=`<div class="page-head ai-page-heading"><div><p class="ai-eyebrow">导入 · 识题 · 审核</p><h1 class="page-title">AI 题目工作台</h1><p class="page-sub">上传试卷，生成题面、解答与教学建议。核对后采纳到题库。</p></div></div>
    <details class="card ai-settings"><summary>模型与接口设置 <span id="ai-key-state"></span></summary><div data-provider-settings></div></details>
    <section class="card ai-create"><details class="ai-new-task" id="ai-new-task" ${!selected||ctx.query.qid?'open':''}><summary>导入试卷 / 新建任务</summary><div class="ai-form-row ai-create-fields"><label class="ai-upload">选择试卷或图片<input id="ai-upload" type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.webp"></label><label>适用学生<input id="ai-audience" value="高一学生"></label><label>本批预算上限（元）<input id="ai-budget" type="number" value="10" min="0.1" max="500" step="0.1"></label><label class="ai-auto"><input type="checkbox" id="ai-auto" checked>AI 自复核通过即入库</label></div><div id="ai-files" class="muted"></div>
    <details ${ctx.query.qid?'open':''}><summary>已有题目 / 指定页码</summary><div class="ai-form-row"><label>题目编号（逗号或换行分隔）<textarea id="ai-qids" placeholder="Q000001,Q000002">${esc(ctx.query.qid||'')}</textarea></label><label>仅处理这些PDF页（留空为全部）<input id="ai-pages" placeholder="例如 1,2,3"></label></div></details>
    <div class="ai-create-footer"><div class="ai-form-row"><button class="btn btn-primary" id="ai-start">开始处理</button><span id="ai-start-state" role="status"></span></div><p class="muted">每份最多50页、50MB。费用为估算，以服务商账单为准。</p></div></details></section>
    <div class="ai-workspace"><aside class="card ai-job-list"><h2>处理记录</h2><div id="ai-jobs"></div></aside><section id="ai-detail"><div class="card muted">选择一项处理记录，查看进度与题目。</div></section></div>`;
  const $=s=>root.querySelector(s);
  const draftScope=(await api('/api/meta')).draft_scope;
  if(disposed||!ctx.current())return;
  const setBusy=(value,text='')=>{busy=value;$('#ai-start').disabled=value;$('#ai-start-state').textContent=text;};
  async function refreshStatus(){if(disposed||!ctx.current())return;const s=await api('/api/ai/status');if(disposed||!ctx.current())return;$('#ai-key-state').textContent=s.configured?'· 已配置':'· 未配置';if(!s.configured&&!selected)root.querySelector('.ai-settings').open=true;}
  await mountProviderSettings($('[data-provider-settings]'), refreshStatus);
  if(disposed||!ctx.current())return;
  $('#ai-upload').onchange=()=>{files=Array.from($('#ai-upload').files);$('#ai-files').textContent=files.map(f=>`${f.name}（${Math.ceil(f.size/1024)} KB）`).join('；');};
  $('#ai-start').onclick=async()=>{if(busy)return;setBusy(true,'正在上传…');try{
    const ids=$('#ai-qids').value.split(/[\s,，;；]+/).filter(Boolean),fids=[];
    if(!files.length&&!ids.length)throw Error('请先选择文件或填写题目编号。');
    for(const f of files){if(f.size>50*1024*1024)throw Error(`${f.name}超过50MB。`);const r=await fetch('/api/ai/files',{method:'POST',headers:{'Content-Type':f.type||'application/octet-stream','X-Filename':encodeURIComponent(f.name)},body:f});const d=await r.json();if(!r.ok)throw Error(d.error?.message||'上传失败');fids.push(d.file.id);}
    const pages=$('#ai-pages').value.trim()?$('#ai-pages').value.split(/[,，\s]+/).map(Number):[];
    const d=await api('/api/ai/jobs',{method:'POST',body:{file_ids:fids,question_ids:ids,pages,audience:$('#ai-audience').value,budget_cny:Number($('#ai-budget').value),auto_accept:$('#ai-auto').checked}});
    selected=d.job_ids[0];selectedQ='';$('#ai-new-task').open=false;setBusy(false,'任务已加入后台队列');await refresh(true);
  }catch(e){setBusy(false,e.message);}};

  async function refresh(force=false){
    const list=await api('/api/ai/jobs');if(disposed||!ctx.current())return;
    $('#ai-jobs').innerHTML=list.items.length?list.items.map(j=>`<button type="button" class="ai-job ${j.id===selected?'selected':''}" data-job="${esc(j.id)}"><strong>${esc(j.title)}</strong><span>${STATES[j.state]||esc(j.state)} · ${Object.values(j.counts).reduce((a,b)=>a+b,0)}题</span><small>${esc(j.created_at.slice(0,16).replace('T',' '))} · 估算 ¥${j.usage.estimated_cny.toFixed(3)}</small></button>`).join(''):'<p class="muted">尚无处理记录</p>';
    $('#ai-jobs').querySelectorAll('[data-job]').forEach(b=>b.onclick=()=>{selected=b.dataset.job;selectedQ='';$('#ai-new-task').open=false;refresh(true).catch(e=>flash(e.message));});
    if(!selected)return;
    const data=await api(`/api/ai/jobs/${selected}`);if(disposed||!ctx.current())return;
    // Keep text selections and open editor stable while an unrelated question finishes.
      const stamp=json([data.job.state,data.job.stage,data.job.progress,data.job.counts,data.job.error,data.questions.map(q=>[q.id,q.state,q.current_revision])]);
    if(force||detail?.stamp!==stamp){detail={...data,stamp};renderDetail();}
  }
  function renderDetail(){
    const {job:j,questions:qs,source_file:f,source_document:paper}=detail;
    const sourceOptions=(values,current)=>Object.entries(values).map(([id,name])=>`<option value="${esc(id)}" ${id===current?'selected':''}>${esc(name)}</option>`).join('');
    if(!qs.some(q=>q.id===selectedQ))selectedQ=qs[0]?.id||'';
    const active=['running','queued'].includes(j.state);
    const readyCount=qs.filter(q=>q.state==='ready'&&q.candidate).length;
    $('#ai-detail').innerHTML=`<div class="card ai-job-summary"><div class="ai-title-row"><div><h2>${esc(j.title)}</h2><span class="ai-state">${STATES[j.state]||esc(j.state)}</span> <span class="muted">${esc({prepare:'准备页面',recognize:'识别题目',refine:'核对单题',solve:'独立解答',review:'复核与分类',done:'结果已保存'}[j.stage]||j.stage)} ${j.progress.page?`第${j.progress.page}/${j.progress.pages}页`:''}${j.progress.qno?` · 第${esc(j.progress.qno)}题`:''}</span></div><div class="ai-actions">${active?'<button class="btn" data-control="pause">暂停</button>':'<button class="btn" data-control="retry">继续 / 重试失败题</button>'}${readyCount?`<button class="btn" data-accept-all>批量采纳 ${readyCount} 题</button>`:''}<a class="btn" href="/api/ai/jobs/${j.id}/bundle" download="${j.id}.zip">导出结果与TeX</a>${f?`<a class="btn" target="_blank" rel="noopener" href="${pdfUrl(f.rel_path,1)}">查看原卷</a>`:''}</div></div>
      <p class="muted">${Object.entries(j.counts).map(([k,n])=>`${STATES[k]||k} ${n}题`).join(' · ')||'正在准备题目'} · 请求 ${j.usage.requests}次 · 估算 ¥${j.usage.estimated_cny.toFixed(3)} / 本批上限 ¥${j.budget.budget_cny}</p>${j.error?.message?`<p class="ai-error">${esc(j.error.message)}</p>`:''}<details class="compact-fold"><summary>处理与重试说明</summary><div class="compact-fold-body"><p class="muted">暂停在当前请求返回后生效；重试保留已完成步骤。含参考答案的试卷先分离题面再独立解答。AI 自复核通过的题按任务设置入库，需重点复核的题留待教师确认。</p></div></details></div>
      ${paper?.id?`<details class="card"><summary>试卷来源：${esc(paper.name)}${paper.classification?.grade?` · ${esc(SOURCE_GRADES[paper.classification.grade]||'')}`:''}${paper.classification?.category?` · ${esc(EXAM_TYPES[paper.classification.category]||'')}`:''}</summary><p class="muted">核对卷名、考试类别和年级，保存后会同步到题库检索与组题。类别和年级留空时按卷名识别。</p><div class="ai-form-row ai-source-fields"><label class="ai-source-title">来源名称<input id="ai-source-name" value="${esc(paper.name)}" maxlength="200"></label><label>考试类别<select id="ai-source-type"><option value="">按卷名识别${paper.classification?.category?`（${esc(EXAM_TYPES[paper.classification.category]||'未标注')}）`:''}</option>${sourceOptions(EXAM_TYPES,paper.exam_type)}</select></label><label>年级<select id="ai-source-grade"><option value="">按卷名识别${paper.classification?.grade?`（${esc(SOURCE_GRADES[paper.classification.grade])}）`:''}</option>${sourceOptions(SOURCE_GRADES,paper.grade)}</select></label><button class="btn" id="ai-save-source">保存来源信息</button></div><p><a href="#/search?source_id=${esc(paper.id)}">查看本卷已入库题目</a></p><p class="muted">原文件：${esc(f.filename)}</p></details>`:''}
      <div class="ai-results"><div class="card ai-question-list">${qs.map(q=>`<button class="ai-q ${selectedQ===q.id?'selected':''}" data-q="${q.id}"><strong>第${esc(q.original_qno)}题 · ${TYPE[q.qtype]||q.qtype}</strong><span>${STATES[q.state]||q.state}</span><small>${QUALITY[q.quality_state]||''}</small></button>`).join('')||'<p class="muted">识题完成后在此显示题目</p>'}</div><div id="ai-question"></div></div>`;
    if($('#ai-save-source'))$('#ai-save-source').onclick=async()=>{try{await api(`/api/sources/${paper.id}`,{method:'PATCH',body:{name:$('#ai-source-name').value.trim(),exam_type:$('#ai-source-type').value,grade:$('#ai-source-grade').value}});await refresh(true);flash('来源信息已更新，检索分类已同步。','ok');}catch(e){flash(e.message);}};
    $('#ai-detail').querySelectorAll('[data-control]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api(`/api/ai/jobs/${j.id}/control`,{method:'POST',body:{action:b.dataset.control,budget_cny:Number($('#ai-budget').value)}});await refresh(true);}catch(e){flash(e.message);b.disabled=false;}});
    $('#ai-detail').querySelectorAll('[data-q]').forEach(b=>b.onclick=()=>{selectedQ=b.dataset.q;renderDetail();});
    const batch=$('#ai-detail').querySelector('[data-accept-all]');
    if(batch)batch.onclick=async()=>{
      if(!confirm(`将采纳本任务 ${readyCount} 题：题面、答案与解析、分类标签、难度与推荐。采纳后仍需人工数学审校。继续？`))return;
      batch.disabled=true;
      try{
        const r=await api(`/api/ai/jobs/${j.id}/accept`,{method:'POST',body:{confirmed:true}});
        await refresh(true);document.dispatchEvent(new Event('meta:refresh'));
        const bad=r.items.filter(x=>!x.ok);
        flash(r.failed?`已采纳 ${r.accepted} 题，${r.failed} 题未能采纳：${bad.slice(0,2).map(x=>x.message).join('；')}`:`已采纳 ${r.accepted} 题。`,r.failed?'':'ok');
      }catch(e){flash(e.message);batch.disabled=false;}
    };
    renderQuestion();
  }
  function renderQuestion(){
    const q=detail.questions.find(x=>x.id===selectedQ),panel=$('#ai-question');if(!q){panel.innerHTML='';return;}
    const c=q.candidate?.content,s=q.source,r=c?.review,a=c?.solve;
    const active=['running','queued'].includes(detail.job.state);
    panel.innerHTML=`<div class="card ai-reading-card"><div class="ai-title-row"><h2>第${esc(q.original_qno)}题</h2><div class="ai-actions"><span class="ai-state">${QUALITY[q.quality_state]||'待处理'}</span>${q.state!=='adopted'?`<button class="btn btn-sm" data-retry-question ${active?'disabled':''} title="重新请求 AI 解答和复核，在本批预算内计费">重新生成此题</button>`:''}</div></div>${q.error?.message?`<p class="ai-error">${esc(q.error.message)}</p>`:''}
      <h3>题面</h3>${rich(s.stem)}${(s.options||[]).map(o=>`<div class="ai-option"><b>${esc(o.key)}.</b>${rich(o.text)}</div>`).join('')}${(s.subquestions||[]).map(x=>rich(`（${x.key}）${x.text}`)).join('')}
      ${s.uncertainties?.length?`<p class="ai-error">识别疑点：${esc(json(s.uncertainties))}</p>`:''}
      <details><summary>原始题面截图与页码</summary><div class="ai-crops">${(s.statement_images||[]).map(x=>`<a href="${fileUrl(x.path)}" target="_blank"><img src="${fileUrl(x.path)}" alt="第${x.page}页题面截图"><span>第${x.page}页 · ${x.answer_free?'已分离题面':'需检查答案泄漏'}</span></a>`).join('')}</div></details>
      ${a?`<h3>AI 独立解答</h3>${rich(a.answer)}${a.solutions.map(sol=>`<h4>${esc(sol.label)}</h4>${rich(sol.approach)}${rich(sol.steps)}${sol.conditions?rich('边界与条件：'+sol.conditions):''}`).join('')}`:'<p class="muted">解答完成后显示在这里。</p>'}
      ${s.reference?.answer||s.reference?.analysis?`<details><summary>原卷参考答案与解析</summary>${rich(s.reference.answer)}${rich(s.reference.analysis)}</details>`:'<p class="muted">原卷没有参考答案，将由 AI 独立生成。</p>'}
      ${r?`<h3>复核与教学建议</h3>${rich(r.reason)}${rich(r.reference_comparison)}<div class="ai-tags">${r.tags.map(t=>`<span title="${esc(t.reason)}">${esc(t.name)}</span>`).join('')}</div><p>运算 ${r.difficulty.calc}/5 · 思维 ${r.difficulty.think}/5 · 跨度 ${r.difficulty.scope}/5 · 推荐 ${esc(r.rec_level)} · 预计 ${r.time_solve_min}分钟</p><p class="muted">${esc(r.difficulty.note)} ${esc(r.rec_reason)}</p>`:''}
      <div class="editor-entry"><h3>核对与修订</h3><p class="hint">按字段修改，实时查看公式排版；未提交的修改会保留草稿。</p><div class="row-actions"><button class="btn" data-visual-source ${q.state==='adopted'||active?'disabled':''}>编辑题面与选项</button>${c?`<button class="btn" data-visual-candidate ${q.state!=='ready'||active?'disabled':''}>编辑答案、解析与分类</button>`:''}</div>${active?'<p class="hint">任务处理期间暂不可修订，请先暂停。</p>':''}</div>
      ${q.state==='adopted'?`<p class="ai-success">已入库为版本 ${esc(q.adopted_version_id)}；审校记录写入 review 表，来源可查。</p>`:c?`<div class="ai-accept"><h3>采纳到题库</h3><p class="muted">AI复核来自同一模型的再次检查。采纳后仍保留人工审校待办。</p><div class="ai-form-row">${[['question','题面'],['solution','答案与解析'],['tags','分类标签'],['rating','难度与推荐']].map(([k,t])=>`<label><input type="checkbox" data-part="${k}" checked>${t}</label>`).join('')}</div><label><input type="checkbox" id="ai-confirm">我已核对这道题的候选结果，确认采纳所选内容</label><p><button class="btn btn-primary" id="ai-accept">确认采纳此题</button> <span id="ai-accept-state"></span></p></div>`:''}${c&&q.state!=='adopted'?`<details class="ai-reject"><summary>不采用此题</summary><p class="muted">保留候选与理由，不写入题库；原题不受影响，可稍后重新生成。</p><label>舍弃理由<textarea id="ai-reject-reason" rows="2" placeholder="例如：题面识别有误，需要重新上传更清晰的图片"></textarea></label><p><button class="btn btn-danger" id="ai-reject">确认不采用</button> <span id="ai-reject-state"></span></p></details>`:''}</div>`;
    panel.querySelectorAll('[data-visual-source],[data-visual-candidate]').forEach(button=>button.onclick=()=>{const modal=openAIEditor({question:q,kind:button.hasAttribute('data-visual-source')?'source':'candidate',scope:draftScope,current:()=>!disposed&&ctx.current(),onSaved:()=>refresh(true)});ctx.onCleanup(modal.close);});
    if($('#ai-accept'))$('#ai-accept').onclick=async()=>{const button=$('#ai-accept');button.disabled=true;try{const result=await api(`/api/ai/candidates/${q.candidate.id}/accept`,{method:'POST',body:{content_hash:q.candidate.content_hash,confirmed:$('#ai-confirm').checked,parts:Array.from(panel.querySelectorAll('[data-part]:checked')).map(x=>x.dataset.part)}});await refresh(true);flash(`已采纳到题库，版本 ${result.version_id}。`,'ok');document.dispatchEvent(new Event('meta:refresh'));}catch(e){flash(e.message);button.disabled=false;}};
    const retryBtn=panel.querySelector('[data-retry-question]');
    if(retryBtn)retryBtn.onclick=async()=>{retryBtn.disabled=true;try{await api(`/api/ai/questions/${q.id}/retry`,{method:'POST',body:{}});await refresh(true);flash('该题已重新排队，重跑后旧候选保留。','ok');}catch(e){flash(e.message);retryBtn.disabled=false;}};
    const rejectBtn=panel.querySelector('#ai-reject');
    if(rejectBtn)rejectBtn.onclick=async()=>{rejectBtn.disabled=true;try{await api(`/api/ai/candidates/${q.candidate.id}/reject`,{method:'POST',body:{reason:panel.querySelector('#ai-reject-reason').value}});await refresh(true);flash('已记录舍弃理由，原题不受影响。','ok');}catch(e){flash(e.message);rejectBtn.disabled=false;}};
    if(detail.source_file){
      const button=document.createElement('button');button.type='button';button.className='btn btn-sm';button.textContent='原卷对照 / 调整题面区域';
      panel.querySelector('.ai-title-row').after(button);
      button.onclick=async()=>{button.disabled=true;try{const modal=await openRegionEditor({job:detail.job,question:q,onSaved:()=>refresh(true)});ctx.onCleanup(modal.close);}catch(e){flash(e.message);}finally{button.disabled=false;}};
    }
    upgradeMath(panel);
  }
  await refreshStatus();await refresh(true);
  const timer=setInterval(()=>{if(document.visibilityState==='visible')refresh().catch(()=>{});},5000);
  ctx.onCleanup(()=>{disposed=true;clearInterval(timer);});
}
