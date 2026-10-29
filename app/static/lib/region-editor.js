import { api } from './api.js';
import { esc } from './dom.js';
import { openModal, flash } from './ui.js';
import { upgradeMath } from './math.js';

export function normalBox(start,end) {
  const c=v=>Math.max(0,Math.min(1,v));
  return [c(Math.min(start[0],end[0])),c(Math.min(start[1],end[1])),c(Math.max(start[0],end[0])),c(Math.max(start[1],end[1]))];
}

export async function openRegionEditor({job,question,onSaved}) {
  const q=question,s=q.source||{},readonly=q.state==='adopted'||['running','queued'].includes(job.state);
  let regions=(s.manual_regions||q.evidence?.filter(e=>e.role==='statement'&&(s.statement_images||[]).some(i=>i.path===e.rel_path)).map(e=>({page:e.page_no,bbox:JSON.parse(e.bbox_json)}))||[]).map(r=>({page:r.page,bbox:[...r.bbox]}));
  let pages=[],page=s.pages?.[0]||1,start=null,drag=null,loaded=false;
  const modal=openModal({title:`第${q.original_qno}题：原卷对照与题面区域`,width:'modal-region',body:'正在读取原卷页面…',footer:`<span class="hint">${readonly?'查看原卷与识别结果':'保存不会发起 AI 请求；继续任务时按原预算处理'}</span><button class="btn" data-close>关闭</button>${readonly?'':'<button class="btn btn-primary" data-save>保存修订</button>'}`});
  try{pages=(await api(`/api/ai/jobs/${job.id}/pages`)).pages;}catch(e){modal.body.textContent=e.message;return modal;}
  if(!modal.mask.isConnected)return modal;
  if(!pages.some(p=>p.page===page))page=pages[0].page;
  modal.body.innerHTML=`<div class="region-workspace"><div class="region-original"><div class="workspace-toolbar"><label>原卷页码 <select data-page>${pages.map(p=>`<option value="${p.page}" ${p.page===page?'selected':''}>第 ${p.page} 页</option>`).join('')}</select></label><label>缩放 <select data-zoom><option value="100">适应宽度</option><option value="150">150%</option><option value="200">200%</option></select></label></div><p class="hint">${readonly?'已采纳题目请到题目详情修订；正在处理的任务需先暂停。':'在左侧原卷拖动框选完整题面。跨页题可切换页码继续添加；按列表顺序读取。'}</p><div class="region-scroll"><div class="region-sheet"><img data-page-image alt="原卷页面"><svg data-overlay viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="题面框选区域"></svg></div></div></div>
  <div class="region-result"><h3>识别题面</h3><div class="region-preview" data-rich>${esc(s.stem||'')}${(s.options||[]).map(o=>'\n'+o.key+'. '+o.text).map(esc).join('')}${(s.subquestions||[]).map(o=>'\n（'+o.key+'）'+o.text).map(esc).join('')}</div>
  <h3>题面区域</h3><ol data-regions></ol>${readonly?'':`<label class="check"><input type="checkbox" data-reextract checked>按新区域重新识别题面</label><p class="hint">取消勾选后，可直接修订下面的文字；仍会重新解答和复核。</p><fieldset data-text disabled><label class="field"><span>题干 TeX</span><textarea rows="6" data-stem>${esc(s.stem||'')}</textarea></label>${(s.options||[]).map((o,i)=>`<label class="field"><span>选项 ${esc(o.key)}</span><textarea rows="2" data-option="${i}">${esc(o.text)}</textarea></label>`).join('')}${(s.subquestions||[]).map((o,i)=>`<label class="field"><span>第（${esc(o.key)}）问</span><textarea rows="3" data-sub="${i}">${esc(o.text)}</textarea></label>`).join('')}</fieldset><label class="check"><input type="checkbox" data-confirm>已核对所选区域：题面完整，未包含答案或解析</label>`}<p data-message role="status"></p></div></div>`;
  const $=selector=>modal.body.querySelector(selector),svg=$('[data-overlay]'),img=$('[data-page-image]');
  function paint(){
    svg.innerHTML=regions.map((r,i)=>r.page===page?`<g><rect x="${r.bbox[0]*1000}" y="${r.bbox[1]*1000}" width="${(r.bbox[2]-r.bbox[0])*1000}" height="${(r.bbox[3]-r.bbox[1])*1000}"/><text x="${r.bbox[0]*1000+5}" y="${r.bbox[1]*1000+25}">${i+1}</text></g>`:'').join('');
    if(drag)svg.innerHTML+=`<rect class="region-draft" x="${drag[0]*1000}" y="${drag[1]*1000}" width="${(drag[2]-drag[0])*1000}" height="${(drag[3]-drag[1])*1000}"/>`;
    $('[data-regions]').innerHTML=regions.length?regions.map((r,i)=>`<li>区域 ${i+1}（第 ${r.page} 页） <button class="btn-link" data-jump="${i}">定位</button>${readonly?'':` <button class="btn-link" data-up="${i}" ${i?'':'disabled'}>上移</button> <button class="btn-link danger" data-remove="${i}">删除</button>`}</li>`).join(''):'<li class="hint">尚未框选题面。</li>';
  }
  function show(){loaded=false;svg.style.visibility='hidden';img.src=pages.find(p=>p.page===page).url;paint();}
  img.onload=()=>{loaded=true;svg.style.visibility='visible';};img.onerror=()=>{$('[data-message]').textContent='页面图片读取失败，请切换页码重试。';};
  $('[data-page]').onchange=e=>{page=Number(e.target.value);show();};
  $('[data-zoom]').onchange=e=>{$('.region-sheet').style.width=e.target.value+'%';};
  const point=e=>{const b=svg.getBoundingClientRect();return [(e.clientX-b.left)/b.width,(e.clientY-b.top)/b.height];};
  if(!readonly){
    svg.onpointerdown=e=>{if(!loaded||e.button!==0)return;start=point(e);svg.setPointerCapture(e.pointerId);e.preventDefault();};
    svg.onpointermove=e=>{if(start){drag=normalBox(start,point(e));paint();}};
    svg.onpointerup=e=>{if(!start)return;const bbox=normalBox(start,point(e));start=null;drag=null;if(bbox[2]-bbox[0]>=.005&&bbox[3]-bbox[1]>=.005&&regions.length<20){regions.push({page,bbox});$('[data-confirm]').checked=false;}paint();};
    svg.onpointercancel=()=>{start=null;drag=null;paint();};
    $('[data-reextract]').onchange=e=>{$('[data-text]').disabled=e.target.checked;};
    modal.foot.querySelector('[data-save]').onclick=async e=>{
      const button=e.target;button.disabled=true;$('[data-message]').textContent='正在保存…';
      const reextract=$('[data-reextract]').checked;
      const source=reextract?undefined:{...s,stem:$('[data-stem]').value,options:(s.options||[]).map((o,i)=>({...o,text:$(`[data-option="${i}"]`).value})),subquestions:(s.subquestions||[]).map((o,i)=>({...o,text:$(`[data-sub="${i}"]`).value}))};
      try{await api(`/api/ai/questions/${q.id}/regions`,{method:'POST',body:{regions,revision:q.current_revision,confirmed:$('[data-confirm]').checked,reextract,source}});modal.close();await onSaved?.();flash('题面修订已保存。点击继续任务后，将按原预算重新处理。','ok');}catch(err){$('[data-message]').textContent=err.message;button.disabled=false;}
    };
  }
  $('[data-regions]').onclick=e=>{
    if(e.target.dataset.jump!==undefined){page=regions[Number(e.target.dataset.jump)].page;$('[data-page]').value=page;show();}
    if(e.target.dataset.remove!==undefined){regions.splice(Number(e.target.dataset.remove),1);$('[data-confirm]').checked=false;paint();}
    if(e.target.dataset.up!==undefined){const i=Number(e.target.dataset.up);if(i>0){[regions[i-1],regions[i]]=[regions[i],regions[i-1]];$('[data-confirm]').checked=false;paint();}}
  };
  paint();show();upgradeMath(modal.body);return modal;
}
