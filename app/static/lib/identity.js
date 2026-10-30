import {api} from './api.js';
import {esc} from './dom.js';
import {openModal} from './ui.js';

export function identityLabel(row) {
  if (!row) return '未指定';
  if (row.display_name) return row.display_name;
  return (row.name || row.id || '未命名') + (row.code ? `（${row.code}）` : '') + (row.id ? `［${row.id}］` : '');
}

export function chooseHomonym(candidates, {modal=openModal, reuseLabel='选用已有档案', canCreate=true}={}) {
  return new Promise(resolve => {
    let result=null;
    const m=modal({title:canCreate?'发现同名档案':'编号已被使用',body:`<p>${canCreate?'请先核对编号、备注和班级，选择已有档案，或确认这是另一位同名人员／另一个班级。':'这个编号已经属于以下档案。可选用已有档案，或返回更换编号。'}</p><div class="identity-candidates">${candidates.map((row,index)=>`<div><strong>${esc(identityLabel(row))}</strong><p class="hint">${esc(row.note || '未填写备注')}${row.classes?.length?`；班级：${esc(row.classes.map(identityLabel).join('、'))}`:''}</p><button type="button" class="btn btn-sm" data-use="${index}">${esc(reuseLabel)}</button></div>`).join('')}</div>`,footer:'<button type="button" class="btn" data-close>返回核对</button>'+(canCreate?'<button type="button" class="btn btn-primary" data-new>确认不同，继续新建</button>':''),onClose:()=>resolve(result)});
    m.body.querySelectorAll('[data-use]').forEach(b=>b.onclick=()=>{result={candidate:candidates[Number(b.dataset.use)]};m.close();});
    if(canCreate)m.foot.querySelector('[data-new]').onclick=()=>{result={create:true};m.close();};
  });
}

export async function createIdentity(path, payload, {operations, current=()=>true, modal=openModal, reuseLabel}={}) {
  const body={...payload,operation_id:operations.forPayload(payload)};
  try { return await api(path,{method:'POST',body}); }
  catch(error) {
    if(!['same_name','duplicate_code'].includes(error.code)||!current())throw error;
    const choice=await chooseHomonym(error.detail?.candidates || [],{modal,reuseLabel,canCreate:error.code==='same_name'});
    if(!choice||!current())return null;
    if(choice.candidate)return {reused:true,candidate:choice.candidate};
    return api(path,{method:'POST',body:{...body,allow_same_name:true}});
  }
}
