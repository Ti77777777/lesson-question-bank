import {api} from './api.js';
import {esc} from './dom.js';
import {openModal,flash,textDialog,confirmDialog} from './ui.js';

export function mountRoster(root,{scope,onChange,current=()=>true}) {
  const storageKey='qbank-teacher:'+scope;
  let teachers=[],all=[],view='classes',teacherId='',disposed=false,run=0;
  try{teacherId=localStorage.getItem(storageKey)||'';}catch{}
  const alive=()=>!disposed&&current();
  const dialogs=new Set();
  function modal(options){const m=openModal({...options,onClose:()=>{dialogs.delete(m);options.onClose?.();}});dialogs.add(m);return m;}
  function scoped(){return all.filter(a=>!teacherId || (teacherId==='_unassigned'?!a.teachers?.length:a.teachers?.some(t=>t.id===teacherId)));}
  function paint(notify=true){
    if(!alive())return;
    if(teacherId&&teacherId!=='_unassigned'&&!teachers.some(t=>t.id===teacherId))teacherId='';
    const rows=scoped(),visible=rows.filter(a=>view==='classes'?a.kind!=='student':view==='students'?a.kind==='student':a.kind==='student'&&a.direct_teachers?.some(t=>!teacherId||t.id===teacherId));
    root.innerHTML=`<div class="roster-toolbar"><label>当前教师<select data-teacher aria-label="当前教师"><option value="">全部教师</option><option value="_unassigned">尚未分配教师</option>${teachers.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select></label><button class="btn btn-sm" data-manage>管理教师</button></div><div class="roster-views">${[['classes',teacherId&&teacherId!=='_unassigned'?'我的班级':'班级与小组'],['students','学生名单'],['direct','一对一学生']].map(([id,label])=>`<button class="btn btn-sm ${view===id?'btn-primary':''}" data-view="${id}" aria-pressed="${view===id}">${label}</button>`).join('')}</div><p class="hint roster-caption">教师切换用于归属筛选，当前没有账号登录或访问权限隔离。</p>${visible.length?`<div class="table-wrap"><table class="table"><thead><tr><th>名称</th><th>负责教师</th><th>${view==='classes'?'学生人数':'所在班级 / 小组'}</th><th>操作</th></tr></thead><tbody>${visible.map(a=>`<tr><td>${esc(a.name)}<div class="hint">${{class:'班级',group:'学习小组',student:'学生'}[a.kind]}</div></td><td>${esc((a.teachers||[]).map(t=>t.name).join('、')||'待分配')}</td><td>${view==='classes'?`${Number(a.member_count)||0} 人`:esc((a.classes||[]).map(c=>c.name).join('、')||'未加入班级')}</td><td><div class="row-actions">${a.kind!=='student'?`<button class="btn-link" data-members="${esc(a.id)}">查看名单</button>`:''}<button class="btn-link" data-assign="${esc(a.id)}">分配教师</button><button class="btn-link" data-rename="${esc(a.id)}">改名</button></div></td></tr>`).join('')}</tbody></table></div>`:'<div class="card-body hint">暂无记录。已有未分配资料可切换到“尚未分配教师”后补充分配。</div>'}`;
    root.querySelector('[data-teacher]').value=teacherId;
    root.querySelector('[data-teacher]').onchange=e=>{teacherId=e.target.value;try{localStorage.setItem(storageKey,teacherId);}catch{}paint();};
    root.querySelector('[data-manage]').onclick=()=>manage().catch(e=>flash(e.message));
    root.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;paint(false);});
    root.querySelectorAll('[data-members]').forEach(b=>b.onclick=()=>members(b.dataset.members).catch(e=>flash(e.message)));
    root.querySelectorAll('[data-assign]').forEach(b=>b.onclick=()=>assign(b.dataset.assign).catch(e=>flash(e.message)));
    root.querySelectorAll('[data-rename]').forEach(b=>b.onclick=async()=>{const a=all.find(a=>a.id===b.dataset.rename);const name=await textDialog({title:'修改名称',label:'名称',value:a.name});if(name===null||!alive())return;try{await api('/api/audiences/'+a.id,{method:'PATCH',body:{name}});await refresh();}catch(e){flash(e.message);}});
    if(notify)onChange({items:rows,teacherId,teachers});
  }
  async function refresh(){
    const ticket=++run;
    const [t,a]=await Promise.all([api('/api/teachers'),api('/api/audiences')]);
    if(!alive()||ticket!==run)return;
    teachers=t.items;all=a.items;paint();
  }
  async function manage(){
    const m=modal({title:'管理教师',body:'',footer:'<button class="btn" data-close>关闭</button>'});
    function render(){
      if(!alive()||!m.mask.isConnected)return;
      m.body.innerHTML=`<p class="hint">这里只登记教师名称和归属，不创建登录账号。</p><div class="roster-teachers">${teachers.map(t=>`<div><span>${esc(t.name)}</span><button class="btn-link" data-edit="${esc(t.id)}">改名</button></div>`).join('')||'<p>尚未添加教师。</p>'}</div><form data-add><label class="field"><span>教师姓名或称呼</span><input name="name" maxlength="80" required></label><button class="btn btn-primary">添加教师</button><p role="status" data-error></p></form>`;
      m.body.querySelector('[data-add]').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{await api('/api/teachers',{method:'POST',body:{name:e.target.elements.name.value}});await refresh();render();}catch(err){if(m.mask.isConnected){m.body.querySelector('[data-error]').textContent=err.message;button.disabled=false;}}};
      m.body.querySelectorAll('[data-edit]').forEach(b=>b.onclick=async()=>{const t=teachers.find(t=>t.id===b.dataset.edit),name=await textDialog({title:'修改教师称呼',label:'教师称呼',value:t.name});if(name===null||!alive())return;try{await api('/api/teachers/'+t.id,{method:'PATCH',body:{name}});await refresh();render();}catch(e){flash(e.message);}});
    }
    render();
  }
  async function assign(aid){
    const data=await api('/api/audiences/'+aid+'/teachers');if(!alive())return;
    const selected=new Set(data.audience.direct_teachers.map(t=>t.id));
    const m=modal({title:'分配教师：'+data.audience.name,body:`<p class="hint">可选择多位老师。学生还会关联所在班级的老师；直接勾选老师可用于一对一归属。</p><div class="roster-checklist">${teachers.map(t=>`<label class="check"><input type="checkbox" value="${esc(t.id)}" ${selected.has(t.id)?'checked':''}>${esc(t.name)}</label>`).join('')||'<p>请先添加教师。</p>'}</div><p data-error role="alert"></p>`,footer:'<button class="btn" data-close>取消</button><button class="btn btn-primary" data-save>保存归属</button>'});
    m.foot.querySelector('[data-save]').onclick=async e=>{e.target.disabled=true;try{await api('/api/audiences/'+aid+'/teachers',{method:'PUT',body:{base_revision:data.revision,teacher_ids:[...m.body.querySelectorAll('input:checked')].map(i=>i.value)}});m.close();await refresh();}catch(err){if(m.mask.isConnected){m.body.querySelector('[data-error]').textContent=err.message;e.target.disabled=false;}}};
  }
  async function members(aid){
    let data=await api('/api/audiences/'+aid+'/members');if(!alive())return;
    const m=modal({title:'学生名单：'+data.audience.name,body:'',footer:'<button class="btn" data-close>关闭</button>'});
    function render(){
      if(!alive()||!m.mask.isConnected)return;
      m.body.innerHTML=`<p class="hint">${data.total} 名学生。移出名单会保留学生档案和历史使用记录。</p><div class="roster-members">${data.items.map(s=>`<div><span>${esc(s.name)}</span><button class="btn-link" data-remove="${esc(s.id)}">移出名单</button></div>`).join('')||'<p>名单为空。</p>'}</div><form data-new><label class="field"><span>新学生姓名或代号</span><input name="name" maxlength="150" required></label><button class="btn" data-create>添加学生并加入本班</button></form><button class="btn" data-existing>从已有学生中选择</button><p data-error role="alert"></p>`;
      m.body.querySelector('[data-new]').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{await api('/api/audiences/'+aid+'/members',{method:'POST',body:{name:e.target.elements.name.value}});await reload();}catch(err){if(m.mask.isConnected){m.body.querySelector('[data-error]').textContent=err.message;b.disabled=false;}}};
      m.body.querySelectorAll('[data-remove]').forEach(b=>b.onclick=async()=>{
        if(!await confirmDialog({title:'移出学生名单',message:'仅移出这个班级或小组，学生档案和历史记录继续保留。',confirmText:'移出名单'}))return;
        try{data=await api('/api/audiences/'+aid+'/members',{method:'PUT',body:{base_revision:data.revision,student_ids:data.items.filter(s=>s.id!==b.dataset.remove).map(s=>s.id)}});await refresh();render();}catch(e){flash(e.message);}
      });
      m.body.querySelector('[data-existing]').onclick=()=>existing().catch(e=>flash(e.message));
    }
    async function reload(){data=await api('/api/audiences/'+aid+'/members');await refresh();render();}
    async function existing(){
      const [list,snapshot]=await Promise.all([api('/api/audiences',{query:{kind:'student'}}),api('/api/audiences/'+aid+'/members')]);
      if(!alive()||!m.mask.isConnected)return;
      const chosen=new Set(snapshot.items.map(s=>s.id));
      const picker=modal({title:'选择学生',body:`<div class="roster-checklist">${list.items.map(s=>`<label class="check"><input type="checkbox" value="${esc(s.id)}" ${chosen.has(s.id)?'checked':''}>${esc(s.name)} <span class="hint">${esc(s.code||s.id)}</span></label>`).join('')||'<p>请先添加学生。</p>'}</div><p data-error role="alert"></p>`,footer:'<button class="btn" data-close>取消</button><button class="btn btn-primary" data-save>保存名单</button>'});
      picker.foot.querySelector('[data-save]').onclick=async e=>{e.target.disabled=true;try{await api('/api/audiences/'+aid+'/members',{method:'PUT',body:{base_revision:snapshot.revision,student_ids:[...picker.body.querySelectorAll('input:checked')].map(i=>i.value)}});picker.close();await reload();}catch(err){if(picker.mask.isConnected){picker.body.querySelector('[data-error]').textContent=err.message;e.target.disabled=false;}}};
    }
    render();
  }
  return {refresh,setData(rows){all=rows;paint();},destroy(){disposed=true;run++;dialogs.forEach(m=>m.close());},get teacherId(){return teacherId;},get teachers(){return teachers;}};
}
