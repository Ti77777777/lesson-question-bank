import {api} from './api.js';
import {esc} from './dom.js';
import {QTYPES} from './constants.js';
import {openModal} from './ui.js';
import {upgradeMath,loadKatex,richToHtml} from './math.js';
import {latestSelection, usageOperations} from './usage-state.js';

// The original must be the version used by this collection, not today's latest.
export async function originalVersion(item, read=api) {
  const path=`/api/questions/${encodeURIComponent(item.question_id)}`;
  const detail=await read(path);
  const versionNo=item.question?.version_no || detail.versions?.find(v=>v.id===item.question_version_id)?.version_no;
  if(detail.current_version?.id!==item.question_version_id&&!versionNo)throw new Error('找不到题单所用的原题版本，请重新打开题单。');
  const version=detail.current_version?.id===item.question_version_id
    ? detail.current_version
    : await read(`${path}/versions/${encodeURIComponent(versionNo)}`);
  if(version?.id!==item.question_version_id)throw new Error('原题版本不匹配，请重新打开题单。');
  return {...detail,current_version:version};
}

export function comparisonContent(detail,render=null) {
  const q=detail.current_version || {}, question=detail.question || {};
  const rich=text=>render?`<div>${render(text || '')}</div>`:`<div data-rich>${esc(text || '')}</div>`;
  const sources=(detail.occurrences || []).filter(o=>!o.question_version_id||o.question_version_id===q.id);
  return `<div class="swap-question-heading"><h3>${esc(question.short_name || question.id || '未命名题目')}</h3>
    <div class="swap-meta"><span>${esc(QTYPES[q.qtype] || q.qtype || '题型未知')}</span><span>第 ${esc(q.version_no || 1)} 版</span><span>${esc(question.id || '')}</span></div></div>
    <div class="swap-question-text">${rich(q.stem || '题面待补充')}
      ${(q.options || []).map((o,index)=>`<div class="swap-option"><b>${esc(o.key || String.fromCharCode(65+index))}.</b>${rich(o.text)}</div>`).join('')}
      ${(q.subquestions || []).map(s=>`<div class="swap-subquestion"><b>（${esc(s.key)}）</b>${rich(s.text)}</div>`).join('')}
    </div>
    <details class="swap-answer"><summary>答案与解析</summary><div class="swap-question-text"><h4>答案</h4>${rich(q.answer || (q.answer_keys || []).join('、') || '答案待补充')}
      ${(q.subquestions || []).filter(s=>s.answer).map(s=>`<div class="swap-subquestion"><b>（${esc(s.key)}）</b>${rich(s.answer)}</div>`).join('')}
      ${(q.solutions || []).map(s=>`<h4>${esc(s.label || '解析')}</h4>${rich(s.approach)}${rich(s.steps)}${s.conditions?rich('条件：'+s.conditions):''}`).join('')}
      ${q.analysis && !(q.solutions || []).some(s=>(s.steps || '').trim()===q.analysis.trim())?`<h4>分析</h4>${rich(q.analysis)}`:''}</div></details>
    ${sources.length?`<div class="swap-source">${sources.map(o=>esc(o.source_name || '来源待补充')+(o.qno?`，第 ${esc(o.qno)} 题`:'')).join('<br>')}</div>`:''}`;
}

// Prepare formulas before changing the visible pane, avoiding a plain-TeX frame.
export async function prepareComparison(detail,{ready=loadKatex,render=richToHtml}={}) {
  await ready();
  return comparisonContent(detail,render);
}

export function openSwapQuestion({item,existingIds,baseRevision,current=()=>true,onSwapped}) {
  const searchReads=latestSelection(),detailReads=latestSelection(),operations=usageOperations();
  const already=new Set(existingIds);
  let closed=false,saving=false,originalReady=false,picked=null,rows=[],page=0,total=0,keyword='';
  const limit=12;
  const modal=openModal({title:'换一道题',width:'modal-swap',
    body:`<form class="swap-search" role="search" aria-label="查找替换题目"><label for="swap-keyword">查找题目</label><input id="swap-keyword" type="search" placeholder="输入关键词，如单调性" autocomplete="off"><button class="btn btn-primary" type="submit">查找</button></form>
      <div class="swap-workspace">
        <aside class="swap-candidates" aria-label="候选题列表"><div class="swap-panel-heading"><h3>候选题</h3><span data-count role="status" aria-live="polite"></span></div><div class="swap-candidate-list" data-results></div><div class="swap-pagination"><button type="button" class="btn btn-sm" data-prev disabled>上一页</button><span data-page></span><button type="button" class="btn btn-sm" data-next disabled>下一页</button></div></aside>
        <section class="swap-preview swap-original" aria-label="原题"><div class="swap-panel-heading"><h3>原题</h3><span>题单所用版本</span></div><div class="swap-preview-content" data-original></div></section>
        <section class="swap-preview swap-selected" aria-label="候选题全文"><div class="swap-panel-heading"><h3>候选题全文</h3><span data-selected-status role="status" aria-live="polite"></span></div><div class="swap-preview-content" data-selected><div class="swap-placeholder">选择一道候选题，与原题对照。</div></div></section>
      </div>`,
    footer:`<details class="context-help swap-help"><summary>换题说明</summary><div><p>题目在题单中的位置、分节和教学角色保留。</p><p>换题会清空本条的指定解法、小问范围、提示、留白及题前换页设置。</p></div></details><span class="swap-error" data-error role="alert" hidden></span><button type="button" class="btn" data-close>取消</button><button type="button" class="btn btn-primary" data-apply disabled>替换原题</button>`,
    onClose(){closed=true;searchReads.invalidate();detailReads.invalidate();},
  });
  const alive=()=>!closed&&current(),$=selector=>modal.modal.querySelector(selector);
  const apply=$('[data-apply]'),results=$('[data-results]'),original=$('[data-original]'),selected=$('[data-selected]'),error=$('[data-error]');
  const showError=message=>{error.textContent=message;error.hidden=!message;};
  function selectedStatus(text,blocked=false){
    const status=$('[data-selected-status]');
    status.textContent=text;
    status.classList.toggle('swap-status-blocked',blocked);
    status.parentElement.classList.toggle('is-blocked',blocked);
  }
  const canApply=()=>originalReady&&picked&&!already.has(picked.question.id)&&!saving;
  function updateApply(){apply.disabled=!canApply();}
  async function paintDetail(target,detail,valid=alive){
    const markup=await prepareComparison(detail);
    if(!alive()||!valid())return false;
    target.innerHTML=markup;target.scrollTop=0;
    target.removeAttribute('aria-busy');
    return true;
  }
  async function loadOriginal(){
    original.innerHTML='<div class="swap-placeholder" role="status">正在读取原题…</div>';
    try{const detail=await originalVersion(item);if(!await paintDetail(original,detail))return;originalReady=true;updateApply();}
    catch(e){if(!alive())return;original.innerHTML=`<div class="swap-placeholder"><p>${esc(e.message)}</p><button type="button" class="btn" data-retry-original>重新读取原题</button></div>`;$('[data-retry-original]').onclick=loadOriginal;}
  }
  async function selectQuestion(id){
    if(saving||picked?.question.id===id)return;
    const valid=detailReads.start();picked=null;updateApply();showError('');
    results.querySelectorAll('[data-pick]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.pick===id)));
    selectedStatus('读取中…');
    selected.setAttribute('aria-busy','true');
    if(!selected.querySelector('.swap-question-heading'))selected.innerHTML='<div class="swap-placeholder" role="status">正在读取候选题全文…</div>';
    try{
      const detail=await api(`/api/questions/${encodeURIComponent(id)}`);if(!alive()||!valid())return;
      if(!detail.current_version?.id||detail.question?.id!==id)throw new Error('题目内容不完整，请重新读取。');
      if(!await paintDetail(selected,detail,valid))return;
      picked=detail;
      selectedStatus(already.has(id)?'已在题单中，不能重复替换':'已选择',already.has(id));updateApply();
    }catch(e){if(!alive()||!valid())return;selected.removeAttribute('aria-busy');selectedStatus('读取失败');selected.innerHTML=`<div class="swap-placeholder"><p>${esc(e.message)}</p><button type="button" class="btn" data-retry-detail>重新读取候选题</button></div>`;$('[data-retry-detail]').onclick=()=>selectQuestion(id);}
  }
  async function search(nextPage=0){
    if(saving)return;
    const valid=searchReads.start();detailReads.invalidate();page=nextPage;picked=null;rows=[];updateApply();showError('');
    selected.innerHTML='<div class="swap-placeholder">选择一道候选题，与原题对照。</div>';selected.removeAttribute('aria-busy');selectedStatus('');
    results.innerHTML='<div class="swap-placeholder" role="status">正在查找…</div>';$('[data-count]').textContent='';$('[data-page]').textContent='';$('[data-prev]').disabled=true;$('[data-next]').disabled=true;
    try{
      const data=await api('/api/questions',{query:{keyword:keyword||undefined,limit,offset:page*limit}});if(!alive()||!valid())return;
      total=data.total ?? data.items.length;rows=(data.items||[]).filter(row=>row.id!==item.question_id);
      $('[data-count]').textContent=`${rows.length} 道`;$('[data-page]').textContent=`${page+1} / ${Math.max(1,Math.ceil(total/limit))}`;
      $('[data-prev]').disabled=page===0;$('[data-next]').disabled=(page+1)*limit>=total;
      results.innerHTML=rows.length?rows.map(row=>`<button type="button" class="swap-candidate" data-pick="${esc(row.id)}" aria-pressed="false"><span class="swap-candidate-type">${esc(QTYPES[row.qtype] || row.qtype || '题目')}${already.has(row.id)?'，已在题单':''}</span><strong>${esc(row.short_name || row.id)}</strong><span class="swap-candidate-stem" data-rich>${esc(row.stem || row.stem_preview || '')}</span></button>`).join(''):'<div class="swap-placeholder">本页没有其他题目，可换个关键词或翻页查找。</div>';
      results.scrollTop=0;upgradeMath(results);
    }catch(e){if(!alive()||!valid())return;results.innerHTML=`<div class="swap-placeholder"><p>查找失败：${esc(e.message)}</p><button type="button" class="btn" data-retry-search>重新查找</button></div>`;$('[data-retry-search]').onclick=()=>search(page);}
  }
  modal.body.querySelector('form').onsubmit=event=>{event.preventDefault();if(saving)return;keyword=$('#swap-keyword').value.trim();search();};
  results.onclick=event=>{const button=event.target.closest('[data-pick]');if(button&&results.contains(button))selectQuestion(button.dataset.pick);};
  $('[data-prev]').onclick=()=>search(page-1);$('[data-next]').onclick=()=>search(page+1);
  apply.onclick=async()=>{
    if(!canApply())return;
    saving=true;updateApply();showError('');apply.textContent='正在替换…';
    const payload={base_revision:baseRevision,question_id:picked.question.id,question_version_id:picked.current_version.id};
    try{
      const out=await api(`/api/items/${encodeURIComponent(item.id)}/swap`,{method:'POST',body:{...payload,operation_id:operations.forPayload(payload)}});
      modal.close();if(current())await onSwapped(out);
    }catch(e){if(!alive())return;showError(e.code==='revision_conflict'?'题单已被修改，请关闭后重新打开。':e.message);}
    finally{saving=false;if(alive()){apply.textContent='替换原题';updateApply();}}
  };
  loadOriginal();search();
  return modal;
}
