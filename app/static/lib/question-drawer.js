import {api, pdfUrl} from './api.js';
import {esc} from './dom.js';
import {openModal} from './ui.js';
import {richToHtml, loadKatex} from './math.js';
import {readBasket, toggleBasket} from './basket.js';
import {openCorrections} from './corrections.js';
import {QTYPES} from './constants.js';

export function drawerContent(detail) {
  const q=detail.current_version, rich=t=>richToHtml(t||'');
  return `<div class="drawer-meta"><span>${esc(QTYPES[q.qtype]||q.qtype)}</span><span>${detail.eligibility?.usable?'审校通过':'待核对'}</span></div><section><h3>题面</h3>${rich(q.stem)}${(q.options||[]).map(o=>`<p><b>${esc(o.key)}.</b> ${rich(o.text)}</p>`).join('')}${(q.subquestions||[]).map(s=>`<p>（${esc(s.key)}）${rich(s.text)}</p>`).join('')}</section>
    <details class="drawer-answer"><summary>查看答案与解析</summary><h3>答案</h3>${rich(q.answer||(q.answer_keys||[]).join('、'))}${(q.subquestions||[]).filter(s=>s.answer).map(s=>`<p>（${esc(s.key)}）${rich(s.answer)}</p>`).join('')}${(q.solutions||[]).map(s=>`<h3>${esc(s.label||'解析')}</h3>${rich(s.approach)}${rich(s.steps)}${s.conditions?`<p>条件：${rich(s.conditions)}</p>`:''}`).join('')}${q.analysis && !(q.solutions||[]).some(s=>(s.steps||"").trim()===q.analysis.trim())?`<h3>分析</h3>${rich(q.analysis)}`:''}</details>
    <section><h3>分类标签</h3><div class="q-tags">${(q.tags||[]).map(t=>`<span class="badge">${esc(t.name)}</span>`).join('')||'未标注'}</div></section><section><h3>来源</h3>${(detail.occurrences||[]).map(o=>`<p>${esc(o.source_name||'待确认')}${o.qno?` · 第${esc(o.qno)}题`:''}${o.pdf_page?` · PDF第${Number(o.pdf_page)}页`:''}${o.file?` <a target="_blank" rel="noopener" href="${esc(pdfUrl(o.file,o.pdf_page))}">查看原卷</a>`:''}</p>`).join('')||'<p>待补充</p>'}</section>`;
}

export function openQuestionDrawer({id, ids, onCorrection, onClosed=()=>{}}) {
  let current=id, detail=null, ticket=0, closed=false, correction=null;
  const modal=openModal({title:'题目详情',width:'modal-question-drawer',body:'正在读取…',footer:'<button class="btn" data-prev>上一题</button><button class="btn" data-next>下一题</button><button class="btn btn-primary" data-drawer-basket>加入选题篮</button><button class="btn" data-drawer-correct>纠错</button><a class="btn" data-full>完整编辑页</a>',onClose:()=>{closed=true;ticket++;correction?.close();document.removeEventListener('basket:change',syncBasket);onClosed();}});
  modal.mask.classList.add('drawer-mask');
  const $=s=>modal.modal.querySelector(s);
  function syncBasket(){if(!closed)$('[data-drawer-basket]').textContent=readBasket().includes(current)?'移出选题篮':'加入选题篮';}
  function paint(){if(!closed&&detail){const expanded=modal.body.querySelector('.drawer-answer')?.open;modal.body.innerHTML=drawerContent(detail);if(expanded)modal.body.querySelector('.drawer-answer').open=true;}}
  async function load(qid) {
    current=qid;detail=null;const run=++ticket;
    const index=ids.indexOf(current);$('[data-prev]').disabled=index<=0;$('[data-next]').disabled=index<0||index>=ids.length-1;
    $('[data-full]').href=`#/question/${encodeURIComponent(current)}`;
    $('[data-drawer-correct]').disabled=true;$('[data-drawer-basket]').disabled=true;syncBasket();
    modal.modal.querySelector('.modal-head h2').textContent=`${current} · ${index+1}/${ids.length}`;
    modal.body.textContent='正在读取题目…';modal.body.scrollTop=0;
    try {
      const data=await api(`/api/questions/${encodeURIComponent(current)}`);
      if(closed||ticket!==run)return;
      detail=data;paint();$('[data-drawer-correct]').disabled=false;$('[data-drawer-basket]').disabled=false;
      loadKatex().then(()=>{if(!closed&&ticket===run)paint();});
    }catch(e){if(!closed&&ticket===run){modal.body.innerHTML=`<p class="modal-error">${esc(e.message)}</p><button class="btn" data-retry>重新读取</button>`;$('[data-retry]').onclick=()=>load(current);}}
  }
  $('[data-prev]').onclick=()=>load(ids[ids.indexOf(current)-1]);
  $('[data-next]').onclick=()=>load(ids[ids.indexOf(current)+1]);
  $('[data-drawer-basket]').onclick=()=>toggleBasket(current);
  $('[data-drawer-correct]').onclick=()=>{if(!detail)return;const qid=current;correction=openCorrections({questionId:qid,versionId:detail.current_version.id,onChanged:counts=>onCorrection?.(qid,counts)});};
  document.addEventListener('basket:change',syncBasket);
  load(id);
  return modal;
}
