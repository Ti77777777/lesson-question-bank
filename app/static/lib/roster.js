import {api} from './api.js';
import {esc} from './dom.js';
import {openModal,flash,confirmDialog} from './ui.js';
import {identityLabel,createIdentity} from './identity.js';
import {usageOperations as creationOperations} from './usage-state.js';

export function mountRoster(root,{scope,onChange,current=()=>true}) {
  const storageKey='qbank-teacher:'+scope;
  let teachers=[],all=[],view='classes',teacherId='',disposed=false,run=0;
  try{teacherId=localStorage.getItem(storageKey)||'';}catch{}
  const alive=()=>!disposed&&current();
  const dialogs=new Set(),audienceOps=creationOperations();
  function modal(options){const m=openModal({...options,onClose:()=>{dialogs.delete(m);options.onClose?.();}});dialogs.add(m);return m;}
  const names=rows=>rows.map(identityLabel).join('、');
  function scoped(){return all.filter(a=>!teacherId || (teacherId==='_unassigned'?!a.teachers?.length:a.teachers?.some(t=>t.id===teacherId)));}
  function paint(notify=true){
    if(!alive())return;
    if(teacherId&&teacherId!=='_unassigned'&&!teachers.some(t=>t.id===teacherId))teacherId='';
    const rows=scoped(),visible=rows.filter(a=>view==='classes'?a.kind!=='student':view==='students'?a.kind==='student':a.kind==='student'&&a.direct_teachers?.some(t=>!teacherId||t.id===teacherId));
    root.innerHTML=`<div class="roster-toolbar"><label>当前教师<select data-teacher aria-label="当前教师"><option value="">全部教师</option><option value="_unassigned">尚未分配教师</option>${teachers.map(t=>`<option value="${esc(t.id)}">${esc(identityLabel(t))}</option>`).join('')}</select></label><button class="btn btn-sm" data-manage>管理教师</button></div><div class="roster-views">${[['classes',teacherId&&teacherId!=='_unassigned'?'我的班级':'班级与小组'],['students','学生名单'],['direct','一对一学生']].map(([id,label])=>`<button class="btn btn-sm ${view===id?'btn-primary':''}" data-view="${id}" aria-pressed="${view===id}">${label}</button>`).join('')}</div><p class="hint roster-caption">姓名允许重复，系统编号用于区分档案。教师切换仅筛选归属。</p>${visible.length?`<div class="table-wrap"><table class="table"><thead><tr><th>名称与编号</th><th>负责教师</th><th>${view==='classes'?'学生人数':'所在班级 / 小组'}</th><th>操作</th></tr></thead><tbody>${visible.map(a=>`<tr><td>${esc(a.name)}<div class="hint">${esc([a.code,a.id].filter(Boolean).join('，'))}</div>${a.note?`<div class="hint">${esc(a.note)}</div>`:''}</td><td>${esc(names(a.teachers||[])||'待分配')}</td><td>${view==='classes'?`${Number(a.member_count)||0} 人`:esc(names(a.classes||[])||'未加入班级')}</td><td><div class="row-actions">${a.kind!=='student'?`<button class="btn-link" data-members="${esc(a.id)}">查看名单</button>`:''}<button class="btn-link" data-assign="${esc(a.id)}">分配教师</button><button class="btn-link" data-edit="${esc(a.id)}">编辑资料</button></div></td></tr>`).join('')}</tbody></table></div>`:'<div class="card-body hint">暂无记录。已有未分配资料可切换到“尚未分配教师”后补充分配。</div>'}`;
    root.querySelector('[data-teacher]').value=teacherId;
    root.querySelector('[data-teacher]').onchange=e=>{teacherId=e.target.value;try{localStorage.setItem(storageKey,teacherId);}catch{}paint();};
    root.querySelector('[data-manage]').onclick=()=>manage();
    root.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;paint(false);});
    root.querySelectorAll('[data-members]').forEach(b=>b.onclick=()=>members(b.dataset.members).catch(e=>flash(e.message)));
    root.querySelectorAll('[data-assign]').forEach(b=>b.onclick=()=>assign(b.dataset.assign).catch(e=>flash(e.message)));
    root.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>edit(all.find(a=>a.id===b.dataset.edit)));
    if(notify)onChange({items:rows,teacherId,teachers});
  }
  async function refresh(){
    const ticket=++run;
    const [t,a]=await Promise.all([api('/api/teachers'),api('/api/audiences')]);
    if(!alive()||ticket!==run)return;
    teachers=t.items;all=a.items;paint();
  }
  function fields({name='',code='',note='',kind='student'}={},isTeacher=false){return `<label class="field"><span>${isTeacher?'教师姓名或称呼':({student:'学生姓名',class:'班级名称',group:'小组名称'}[kind]||'名称')}</span><input name="name" value="${esc(name)}" maxlength="${isTeacher?80:150}" required></label><label class="field"><span>${isTeacher?'工号（选填）':'学号或代号（选填）'}</span><input name="code" value="${esc(code||'')}" maxlength="80" placeholder="${isTeacher?'如 T-MATH-01':'如 2027-S01'}"></label><label class="field"><span>区分备注（选填）</span><textarea name="note" maxlength="${isTeacher?500:1000}" placeholder="如 校区、年级或其他区分说明">${esc(note||'')}</textarea></label>`;}
  const values=form=>Object.fromEntries(['name','code','note'].map(k=>[k,form.elements[k].value.trim()||null]));
  function edit(row,isTeacher=false,after=()=>{}){
    const m=modal({title:'编辑资料：'+row.name,body:`<p class="hint">系统编号：${esc(row.id)}。修改姓名或代号会保留归属与历史记录。</p><form data-profile>${fields(row,isTeacher)}<p data-error role="alert"></p></form>`,footer:'<button type="button" class="btn" data-close>取消</button><button type="button" class="btn btn-primary" data-save>保存资料</button>'});
    async function save(e){e.preventDefault();const form=m.body.querySelector('form');if(!form.reportValidity())return;const button=m.foot.querySelector('[data-save]');button.disabled=true;
      try{await api((isTeacher?'/api/teachers/':'/api/audiences/')+row.id,{method:'PATCH',body:{...values(form),base_revision:row.profile_revision}});m.close();await refresh();after();}
      catch(err){if(m.mask.isConnected){m.body.querySelector('[data-error]').textContent=err.message;button.disabled=false;}}
    }
    m.foot.querySelector('[data-save]').onclick=save;m.body.querySelector('form').onsubmit=save;
  }
  function manage(){
    const operations=creationOperations(),m=modal({title:'管理教师',body:'',footer:'<button class="btn" data-close>关闭</button>'});
    function render(){
      if(!alive()||!m.mask.isConnected)return;
      m.body.innerHTML=`<p class="hint">同名教师可用工号和校区备注区分，系统编号保持不变。</p><div class="roster-teachers">${teachers.map(t=>`<div><span>${esc(identityLabel(t))}</span><button class="btn-link" data-edit="${esc(t.id)}">编辑资料</button></div>`).join('')||'<p>尚未添加教师。</p>'}</div><form data-add>${fields({},true)}<button class="btn btn-primary">添加教师</button><p role="status" data-error></p></form>`;
      m.body.querySelector('[data-add]').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;
        try{const out=await createIdentity('/api/teachers',values(e.target),{operations,current:()=>alive()&&m.mask.isConnected,modal});if(!out){button.disabled=false;return;}if(out.reused){teacherId=out.candidate.id;try{localStorage.setItem(storageKey,teacherId);}catch{}}operations.clear();await refresh();render();if(out.reused)flash('已选用已有教师档案，没有重复创建。','ok');}
        catch(err){if(m.mask.isConnected){m.body.querySelector('[data-error]').textContent=err.message;button.disabled=false;}}
      };
      m.body.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>edit(teachers.find(t=>t.id===b.dataset.edit),true,render));
    }
    render();
  }
  async function assign(aid){
    const data=await api('/api/audiences/'+aid+'/teachers');if(!alive())return;
    const selected=new Set(data.audience.direct_teachers.map(t=>t.id));
    const m=modal({title:'分配教师：'+identityLabel(data.audience),body:`<p class="hint">请核对教师的工号与系统编号。学生还会关联班级教师；直接分配可用于一对一归属。</p><div class="roster-checklist">${teachers.map(t=>`<label class="check"><input type="checkbox" value="${esc(t.id)}" ${selected.has(t.id)?'checked':''}>${esc(identityLabel(t))}</label>`).join('')||'<p>请先添加教师。</p>'}</div><p data-error role="alert"></p>`,footer:'<button class="btn" data-close>取消</button><button class="btn btn-primary" data-save>保存归属</button>'});
    m.foot.querySelector('[data-save]').onclick=async e=>{e.target.disabled=true;try{await api('/api/audiences/'+aid+'/teachers',{method:'PUT',body:{base_revision:data.revision,teacher_ids:[...m.body.querySelectorAll('input:checked')].map(i=>i.value)}});m.close();await refresh();}catch(err){if(m.mask.isConnected){m.body.querySelector('[data-error]').textContent=err.message;e.target.disabled=false;}}};
  }
  async function members(aid){
    let data=await api('/api/audiences/'+aid+'/members');if(!alive())return;
    const operations=creationOperations(),m=modal({title:'学生名单：'+identityLabel(data.audience),body:'',footer:'<button class="btn" data-close>关闭</button>'});
    function render(){
      if(!alive()||!m.mask.isConnected)return;
      m.body.innerHTML=`<p class="hint">${data.total} 名学生。请核对系统编号，移出名单会保留学生档案与历史记录。</p><div class="roster-members">${data.items.map(s=>`<div><span>${esc(identityLabel(s))}</span><button class="btn-link" data-remove="${esc(s.id)}">移出名单</button></div>`).join('')||'<p>名单为空。</p>'}</div><form data-new>${fields()}<button class="btn" data-create>添加学生并加入本班</button></form><button class="btn" data-existing>从已有学生中选择</button><p data-error role="alert"></p>`;
      m.body.querySelector('[data-new]').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;
        try{const out=await createIdentity('/api/audiences/'+aid+'/members',values(e.target),{operations,current:()=>alive()&&m.mask.isConnected,modal,reuseLabel:'选用此学生并加入本班'});if(!out){b.disabled=false;return;}
          if(out.reused){const latest=await api('/api/audiences/'+aid+'/members');if(!alive()||!m.mask.isConnected)return;await api('/api/audiences/'+aid+'/members',{method:'PUT',body:{base_revision:latest.revision,student_ids:[...new Set([...latest.items.map(s=>s.id),out.candidate.id])]}});}
          operations.clear();await reload();
        }catch(err){if(m.mask.isConnected){m.body.querySelector('[data-error]').textContent=err.message;b.disabled=false;}}
      };
      m.body.querySelectorAll('[data-remove]').forEach(b=>b.onclick=async()=>{
        const student=data.items.find(s=>s.id===b.dataset.remove);
        if(!await confirmDialog({title:'移出学生名单',message:identityLabel(student)+'：仅移出这个班级或小组，保留档案与历史记录。',confirmText:'移出名单'})||!alive()||!m.mask.isConnected)return;
        try{data=await api('/api/audiences/'+aid+'/members',{method:'PUT',body:{base_revision:data.revision,student_ids:data.items.filter(s=>s.id!==b.dataset.remove).map(s=>s.id)}});await refresh();render();}catch(e){flash(e.message);}
      });
      m.body.querySelector('[data-existing]').onclick=()=>existing().catch(e=>flash(e.message));
    }
    async function reload(){data=await api('/api/audiences/'+aid+'/members');await refresh();render();}
    async function existing(){
      const [list,snapshot]=await Promise.all([api('/api/audiences',{query:{kind:'student'}}),api('/api/audiences/'+aid+'/members')]);
      if(!alive()||!m.mask.isConnected)return;
      const chosen=new Set(snapshot.items.map(s=>s.id));
      const picker=modal({title:'选择学生',body:`<div class="roster-checklist">${list.items.map(s=>`<label class="check"><input type="checkbox" value="${esc(s.id)}" ${chosen.has(s.id)?'checked':''}>${esc(identityLabel(s))}${s.note?` <span class="hint">${esc(s.note)}</span>`:''}</label>`).join('')||'<p>请先添加学生。</p>'}</div><p data-error role="alert"></p>`,footer:'<button class="btn" data-close>取消</button><button class="btn btn-primary" data-save>保存名单</button>'});
      picker.foot.querySelector('[data-save]').onclick=async e=>{e.target.disabled=true;try{await api('/api/audiences/'+aid+'/members',{method:'PUT',body:{base_revision:snapshot.revision,student_ids:[...picker.body.querySelectorAll('input:checked')].map(i=>i.value)}});picker.close();await reload();}catch(err){if(picker.mask.isConnected){picker.body.querySelector('[data-error]').textContent=err.message;e.target.disabled=false;}}};
    }
    render();
  }
  async function createAudience(payload){
    const out=await createIdentity('/api/audiences',payload,{operations:audienceOps,current:alive,modal});
    if(out){audienceOps.clear();if(out.reused){teacherId='';view=out.candidate.kind==='student'?'students':'classes';try{localStorage.setItem(storageKey,'');}catch{}return {audience:out.candidate,reused:true};}}
    return out;
  }
  return {refresh,createAudience,setData(rows){all=rows;paint();},destroy(){disposed=true;run++;dialogs.forEach(m=>m.close());},get teacherId(){return teacherId;},get teachers(){return teachers;}};
}
