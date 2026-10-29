import { api } from './api.js';
import { esc } from './dom.js';
import { flash, textDialog, confirmDialog } from './ui.js';

export function searchConditions(query) {
  return Object.fromEntries(Object.entries(query).filter(([k,v]) => !['status','limit','offset'].includes(k) && v !== undefined && v !== null && v !== '').map(([k,v])=>[k,String(v)]));
}
export function savedSearchUrl(query) {
  const params=new URLSearchParams(searchConditions(query));
  return '#/search'+(params.size?'?'+params.toString():'');
}

export function mountSavedSearches(root,getQuery) {
  let items=[],disposed=false,selected='';
  function paint() {
    if(disposed)return;
    root.innerHTML=`<span class="hint">常用检索</span><select data-presets aria-label="常用检索方案"><option value="">选择已保存的方案…</option>${items.map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(p.name)}</option>`).join('')}</select><button class="btn btn-sm" data-load>使用</button><button class="btn btn-sm" data-save>保存当前条件</button><button class="btn-link" data-update>更新方案</button><button class="btn-link" data-rename>改名</button><button class="btn-link danger" data-delete>删除</button>`;
    const select=root.querySelector('[data-presets]');select.onchange=()=>{selected=select.value;};
    root.querySelector('[data-load]').onclick=()=>{const item=items.find(x=>x.id===selected);if(item)location.hash=savedSearchUrl(item.query);else flash('请先选择一个检索方案。');};
    root.querySelector('[data-save]').onclick=async()=>{
      const name=await textDialog({title:'保存常用检索',label:'方案名称',placeholder:'例如 高三导数中档模拟题',confirmText:'保存'});if(name===null||disposed)return;
      try{const item=await api('/api/search/presets',{method:'POST',body:{name,query:searchConditions(getQuery())}});selected=item.id;await load();flash('检索方案已保存。','ok');}catch(e){flash(e.message);}
    };
    for(const action of ['update','rename','delete'])root.querySelector(`[data-${action}]`).onclick=async()=>{
      const item=items.find(x=>x.id===selected);if(!item){flash('请先选择一个方案。');return;}
      try{
        if(action==='delete'){
          if(!await confirmDialog({title:'删除检索方案',message:`删除“${item.name}”？题库中的题目不受影响。`,confirmText:'删除'}))return;
          await api(`/api/search/presets/${item.id}`,{method:'DELETE'});selected='';
        }else{
          let name=item.name,query=item.query;
          if(action==='rename'){name=await textDialog({title:'修改方案名称',label:'名称',value:name});if(name===null)return;}
          else {if(!await confirmDialog({title:'更新检索方案',message:`用当前筛选条件更新“${name}”？`,confirmText:'更新'}))return;query=searchConditions(getQuery());}
          await api(`/api/search/presets/${item.id}`,{method:'PATCH',body:{name,query}});
        }
        await load();flash('检索方案已更新。','ok');
      }catch(e){flash(e.message);}
    };
  }
  async function load(){try{const data=await api('/api/search/presets');items=data.items;paint();}catch(e){if(!disposed){root.innerHTML='<span class="hint">常用检索暂未载入</span><button class="btn-link">重试</button>';root.querySelector('button').onclick=load;}}}
  load();return ()=>{disposed=true;};
}
