import { api } from '../lib/api.js';
import { esc, loading } from '../lib/dom.js';
import { EXAM_TYPES, SOURCE_GRADES } from '../lib/constants.js';
import { flash, openModal, confirmDialog } from '../lib/ui.js';

const LABELS={name:'卷名',school:'学校',year:'年份',region:'地区 / 卷别',exam_type:'考试类别',grade:'年级'};
const FIELDS=Object.keys(LABELS);
const choices=(values,value,empty='按卷名识别')=>`<option value="">${empty}</option>`+Object.entries(values).map(([k,n])=>`<option value="${k}" ${k===value?'selected':''}>${esc(n)}</option>`).join('');
const display=(key,value)=>value?(key==='exam_type'?EXAM_TYPES[value]:key==='grade'?SOURCE_GRADES[value]:value):'未填写';

export default async function mount(root,ctx){
  let rows=[],drafts=new Map(),selected=new Set(),history=[],keyword='';
  root.innerHTML=`<div class="page-head"><div><h1 class="page-title">试卷信息管理</h1><p class="page-sub">校正来源信息，保存前预览受影响的题目；历史讲义保留原快照。</p></div><a class="btn" href="#/search">返回检索</a></div>
  <section class="card workspace-card"><div class="workspace-toolbar"><input type="search" data-keyword placeholder="按卷名、学校查找" aria-label="查找试卷"><span data-count></span><button class="btn btn-primary" data-preview>预览并保存修改</button></div>
  <details class="workspace-bulk"><summary>为勾选试卷统一填写</summary><div class="workspace-toolbar">${['school','year','region','exam_type','grade'].map(k=>`<label class="field"><span>${LABELS[k]}</span>${['exam_type','grade'].includes(k)?`<select data-bulk="${k}"><option value="__keep">不修改</option>${choices(k==='exam_type'?EXAM_TYPES:SOURCE_GRADES,'')}</select>`:`<input data-bulk="${k}" placeholder="留空不修改">`}</label>`).join('')}<button class="btn" data-fill>填入选中行</button></div></details>
  <p class="hint">勾选需要修改的资料，再编辑各行。年份填写考试年份，学校可与完整卷名分别保存。最多一次保存100份。</p><div class="table-wrap" data-table>${loading()}</div></section>
  <section class="card workspace-card"><h2>最近修改与撤销</h2><div data-history></div></section>`;
  const $=s=>root.querySelector(s);
  function filtered(){return rows.filter(s=>`${s.name||''} ${s.school||''}`.toLowerCase().includes(keyword.toLowerCase()));}
  function count(){$('[data-count]').textContent=`共 ${rows.length} 份 · 已选 ${selected.size} 份`;}
  function paint(){
    if(!ctx.current())return;count();
    $('[data-table]').innerHTML=`<table class="table source-editor"><thead><tr><th><input type="checkbox" data-all aria-label="全选当前列表" ${filtered().length&&filtered().every(r=>selected.has(r.id))?'checked':''}></th>${FIELDS.map(k=>`<th>${LABELS[k]}</th>`).join('')}<th>题目数</th></tr></thead><tbody>${filtered().map(row=>{
      const d=drafts.get(row.id)||row,auto=row.classification||{};
      return `<tr data-id="${esc(row.id)}"><td><input type="checkbox" data-select aria-label="选择${esc(row.name)}" ${selected.has(row.id)?'checked':''}></td>${FIELDS.map(k=>`<td>${['exam_type','grade'].includes(k)?`<select data-field="${k}" aria-label="${LABELS[k]}">${choices(k==='exam_type'?EXAM_TYPES:SOURCE_GRADES,d[k],`按卷名识别${auto[k==='exam_type'?'category':k]?`（${esc((k==='exam_type'?EXAM_TYPES:SOURCE_GRADES)[auto[k==='exam_type'?'category':k]]||'未标注')}）`:''}`)}</select>`:`<input data-field="${k}" aria-label="${LABELS[k]}" value="${esc(d[k]||'')}" ${k==='year'?`inputmode="numeric" maxlength="4" placeholder="${esc(auto.year||'')}"`:''} title="${esc(d[k]||'')}">`}</td>`).join('')}<td>${esc(row.occurrence_count)}<br><a href="#/search?source_id=${row.id}">查看题目</a></td></tr>`;
    }).join('')}</tbody></table>`;
    $('[data-history]').innerHTML=history.length?history.map(h=>`<div class="workspace-history"><div><b>${h.after.length} 份资料</b> · ${esc(h.created_at)}<p class="hint">${esc(h.after.map(s=>s.name).join('；'))}</p></div>${h.state==='applied'?`<button class="btn btn-sm" data-undo="${h.id}">撤销这次修改</button>`:'<span class="hint">已撤销</span>'}</div>`).join(''):'<p class="hint">尚无批量校正记录。</p>';
  }
  async function load(){try{const [s,h]=await Promise.all([api('/api/sources'),api('/api/source-changes')]);if(!ctx.current())return;rows=s.items;history=h.items;paint();}catch(e){flash(e.message);$('[data-table]').textContent=e.message;}}
  $('[data-keyword]').oninput=e=>{keyword=e.target.value;paint();};
  $('[data-table]').oninput=e=>{
    const input=e.target.closest('[data-field]'),tr=e.target.closest('[data-id]');if(!input||!tr)return;
    const row=rows.find(s=>s.id===tr.dataset.id);drafts.set(row.id,{...(drafts.get(row.id)||row),[input.dataset.field]:input.value});selected.add(row.id);tr.querySelector('[data-select]').checked=true;count();
  };
  $('[data-table]').onchange=e=>{
    if(e.target.matches('[data-select]')){const id=e.target.closest('[data-id]').dataset.id;if(e.target.checked)selected.add(id);else selected.delete(id);count();}
    if(e.target.matches('[data-all]')){filtered().forEach(s=>e.target.checked?selected.add(s.id):selected.delete(s.id));paint();}
  };
  $('[data-fill]').onclick=()=>{
    if(!selected.size){flash('请先勾选试卷。');return;}
    for(const id of selected){const d={...(drafts.get(id)||rows.find(s=>s.id===id))};root.querySelectorAll('[data-bulk]').forEach(el=>{if(el.value!==''&&el.value!=='__keep'||el.tagName==='SELECT'&&el.value==='')d[el.dataset.bulk]=el.value;});drafts.set(id,d);}paint();
  };
  $('[data-preview]').onclick=async()=>{
    const items=[...selected].map(id=>{const row=rows.find(s=>s.id===id),d=drafts.get(id)||row;return {id,changes:Object.fromEntries(FIELDS.filter(k=>(d[k]||'')!==(row[k]||'')).map(k=>[k,d[k]||'']))};}).filter(x=>Object.keys(x.changes).length);
    if(!items.length){flash('请先编辑需要修改的试卷信息。');return;}
    try{
      const p=await api('/api/sources/batch-preview',{method:'POST',body:{items}});if(!ctx.current())return;
      const modal=openModal({title:`核对 ${p.after.length} 份资料的修改`,width:'modal-wide',body:`<p>以下修改会同步到题库检索与来源显示。历史发布物保持原快照。</p>${p.after.map((s,i)=>`<section class="workspace-preview"><h3>${esc(p.before[i].name)} · 涉及 ${p.affected[i].questions} 题</h3>${FIELDS.filter(k=>s[k]!==p.before[i][k]).map(k=>`<p><b>${LABELS[k]}</b>：${esc(display(k,p.before[i][k]))} → <strong>${esc(display(k,s[k]))}</strong></p>`).join('')}</section>`).join('')}<p data-error role="alert"></p>`,footer:'<button class="btn" data-close>返回修改</button><button class="btn btn-primary" data-apply>确认保存</button>'});
      modal.foot.querySelector('[data-apply]').onclick=async e=>{e.target.disabled=true;try{await api('/api/sources/batch-apply',{method:'POST',body:{items,preview_hash:p.preview_hash}});modal.close();drafts.clear();selected.clear();await load();flash('试卷信息已保存，可在下方撤销。','ok');document.dispatchEvent(new Event('meta:refresh'));}catch(err){modal.body.querySelector('[data-error]').textContent=err.message;e.target.disabled=false;}};
    }catch(e){flash(e.message);}
  };
  $('[data-history]').onclick=async e=>{const btn=e.target.closest('[data-undo]');if(!btn)return;if(!await confirmDialog({title:'撤销来源校正',message:'将恢复这次修改前的来源信息；若资料已被再次修改，会提示你手动核对。',confirmText:'撤销修改'}))return;btn.disabled=true;try{await api(`/api/source-changes/${btn.dataset.undo}/undo`,{method:'POST',body:{}});drafts.clear();await load();flash('已撤销。','ok');}catch(err){flash(err.message);btn.disabled=false;}};
  await load();
}
