import {api} from './api.js';
import {esc} from './dom.js';
import {confirmDialog, textDialog, flash} from './ui.js';

const BUILTINS = [
  {id:'class', name:'课堂练习', options:{cover:false,format:'standard',theme:'blue',options_layout:'auto'},space_mm:35},
  {id:'homework', name:'课后作业', options:{cover:false,format:'compact',theme:'green',options_layout:'auto'},space_mm:45},
  {id:'topic', name:'专题讲义', options:{cover:true,format:'loose',theme:'purple',options_layout:'auto'},space_mm:25},
];
export async function mountLayoutTemplates(root, {read, apply, current = () => true}) {
  let custom = [];
  function render() {
    if (!current()) return;
    root.innerHTML = `<h2>常用讲义样式</h2><label class="field"><span>选择样式</span><select data-template><optgroup label="内置样式">${BUILTINS.map(t=>`<option value="${t.id}">${t.name}</option>`).join('')}</optgroup>${custom.length?`<optgroup label="我的样式">${custom.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</optgroup>`:''}</select></label><label class="field"><span>套用后的每题留白（毫米）</span><input data-template-space type="number" min="0" max="200" step="5" value="35"></label><p class="hint">套用通用版式和每题留白，保留讲义标题与逐题分页。</p><div class="row-actions"><button class="btn btn-sm" data-apply>套用</button><button class="btn btn-sm" data-create>保存当前样式</button><button class="btn btn-sm" data-delete disabled>删除样式</button></div>`;
    const $ = s=>root.querySelector(s);
    const selected = ()=>[...BUILTINS,...custom].find(t=>t.id===$('[data-template]').value);
    $('[data-template]').onchange = ()=>{ $('[data-template-space]').value=selected().space_mm; $('[data-delete]').disabled=BUILTINS.some(t=>t.id===selected().id); };
    $('[data-apply]').onclick = async()=>{
      const space = Number($('[data-template-space]').value);
      if (!Number.isFinite(space)||space<0||space>200) return flash('留白须为0至200毫米');
      if (!await confirmDialog({title:'套用讲义样式',message:'将调整整份讲义的通用版式，并统一每题留白。题前换页等逐题设置会保留。',confirmText:'套用样式'})||!current()) return;
      apply({...selected(),space_mm:space}); flash('样式已套用，可继续逐题微调。','ok');
    };
    $('[data-create]').onclick = async()=>{
      const name=await textDialog({title:'保存讲义样式',label:'样式名称',placeholder:'例如：高三专题练习',confirmText:'保存样式'});
      if (!name||!current()) return;
      const options={...read().options};delete options.title;delete options.subtitle;
      try { await api('/api/layout-templates',{method:'POST',body:{name,options,space_mm:Number($('[data-template-space]').value)}}); await load();flash('样式已保存到当前题库。','ok'); }catch(e){flash(e.message);}
    };
    $('[data-delete]').onclick = async()=>{
      const t=selected();if (BUILTINS.some(x=>x.id===t.id))return;
      if (!await confirmDialog({title:'删除样式',message:`删除“${t.name}”？已套用的讲义不会改变。`,confirmText:'删除',danger:true})||!current())return;
      try {await api(`/api/layout-templates/${t.id}`,{method:'DELETE'});await load();}catch(e){flash(e.message);}
    };
  }
  async function load(){const d=await api('/api/layout-templates');custom=d.items;render();}
  render();try{await load();}catch{if(current())flash('自定义样式暂未读取，仍可使用内置样式。');}
}
