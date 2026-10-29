import { api } from './api.js';
import { esc } from './dom.js';
import { flash, confirmDialog } from './ui.js';

export function mountBackupPanel(root){
  let disposed=false;
  root.innerHTML=`<div class="workspace-toolbar"><div><h2>完整备份与恢复</h2><p class="hint">保存题库、原卷、图片、模板及讲义。恢复时创建独立副本，便于核对。API 密钥需单独重新配置。</p></div><button class="btn btn-primary" data-create>创建完整备份</button><button class="btn" data-import>导入备份</button><input type="file" accept=".zip" data-archive hidden><button class="btn" data-refresh>刷新列表</button></div><p data-status role="status"></p><div data-backups></div>`;
  const $=s=>root.querySelector(s);
  async function load(){try{const data=await api('/api/backups');if(disposed)return;$('[data-backups]').innerHTML=data.items.length?data.items.map(b=>`<div class="workspace-history"><span><b>${esc(b.created_at?.replace('T',' ')||b.id)}</b><br><span class="hint">${(b.bytes/1048576).toFixed(1)} MB${Number.isInteger(b.questions)?`，${b.questions} 题`:''}</span></span><div class="row-actions"><a class="btn btn-sm" href="/api/backups/${b.id}/download" download="${esc(b.id)}.zip">下载</a><button class="btn btn-sm" data-check="${b.id}">校验备份</button><button class="btn btn-sm" data-restore="${b.id}">恢复为独立副本</button></div></div>`).join(''):'<p class="hint">尚无完整备份。</p>';}catch(e){if(!disposed)$('[data-status]').textContent=e.message;}}
  $('[data-create]').onclick=async e=>{const btn=e.target;btn.disabled=true;$('[data-status]').textContent='正在整理文件并备份，请稍候…';try{const b=await api('/api/backups',{method:'POST',body:{}});if(disposed)return;$('[data-status]').textContent=`已备份 ${b.questions} 题、${b.files} 个文件。`;await load();}catch(err){if(!disposed)$('[data-status]').textContent=err.message;}finally{btn.disabled=false;}};
  $('[data-refresh]').onclick=load;
  $('[data-import]').onclick=()=>$('[data-archive]').click();
  $('[data-archive]').onchange=async e=>{
    const file=e.target.files[0];if(!file)return;
    if(file.size>512*1048576){$('[data-status]').textContent='备份须在512MB以内。';e.target.value='';return;}
    $('[data-import]').disabled=true;$('[data-status]').textContent='正在导入并校验备份…';
    try{const response=await fetch('/api/backups/import',{method:'POST',headers:{'Content-Type':'application/zip'},body:file});const b=await response.json();if(!response.ok)throw Error(b.error?.message||'备份导入失败');if(!disposed){$('[data-status]').textContent=`导入成功：${b.questions} 题，${b.files} 个文件。可在下方选择恢复。`;await load();}}catch(err){if(!disposed)$('[data-status]').textContent=err.message;}finally{$('[data-import]').disabled=false;e.target.value='';}
  };
  $('[data-backups]').onclick=async e=>{
    const button=e.target.closest('[data-check],[data-restore]');if(!button)return;const bid=button.dataset.check||button.dataset.restore;
    if(button.dataset.restore&&!await confirmDialog({title:'创建独立恢复副本',message:'将校验备份并恢复到新的文件夹。当前题库继续使用原数据；恢复副本的 AI 后台默认关闭。',confirmText:'创建恢复副本'}))return;
    button.disabled=true;$('[data-status]').textContent='正在校验文件…';
    try{
      if(button.dataset.check){const b=await api(`/api/backups/${bid}/check`,{method:'POST',body:{}});if(!disposed)$('[data-status]').textContent=`校验通过：${b.questions} 题、${b.files} 个文件，备份于 ${b.created_at}。`;}
      else{const b=await api(`/api/backups/${bid}/restore`,{method:'POST',body:{confirmed:true}});if(!disposed){$('[data-status]').textContent=`已恢复 ${b.questions} 题。目录：${b.directory}。双击目录内的“恢复启动.bat”核对，浏览端口为 ${b.port}。`;flash('独立恢复副本已生成。','ok');}}
    }catch(err){if(!disposed)$('[data-status]').textContent=err.message;}finally{button.disabled=false;}
  };
  load();return ()=>{disposed=true;};
}
