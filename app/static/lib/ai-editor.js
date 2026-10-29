import {api} from './api.js';
import {esc} from './dom.js';
import {openModal, flash, confirmDialog} from './ui.js';
import {richToHtml, loadKatex} from './math.js';
import {draftStore} from './drafts.js';

const DIMENSIONS={knowledge:'知识点',method:'解题方法',idea:'数学思想',prereq:'前置知识',purpose:'教学用途',error:'易错点',textbook:'教材关联'};
export function setEditorValue(data, path, value) {
  const parts=path.split('.');let target=data;
  if(parts.some(k=>['__proto__','constructor','prototype'].includes(k)))throw Error('无效字段');
  for(const key of parts.slice(0,-1)){if(!Object.hasOwn(target,key))target[key]={};target=target[key];}
  target[parts.at(-1)]=value;
}
export function editorPreview(data, kind) {
  const rich=value=>`<div class="editor-preview-text">${richToHtml(value||'')}</div>`;
  if(kind==='source')return `<h3>题面预览</h3>${rich(data.stem)}${(data.options||[]).map(o=>`<p><b>${esc(o.key)}.</b> ${rich(o.text)}</p>`).join('')}${(data.subquestions||[]).map(s=>`<p>（${esc(s.key)}）${rich(s.text)}</p>`).join('')}`;
  if(kind==='review')return `<h3>分类与评分预览</h3>${rich(data.review.reason)}<p>运算 ${esc(data.review.difficulty.calc)}/5 · 思维 ${esc(data.review.difficulty.think)}/5 · 跨度 ${esc(data.review.difficulty.scope)}/5</p><div class="q-tags">${data.review.tags.map(t=>`<span class="badge badge-neutral">${esc(DIMENSIONS[t.dimension]||t.dimension)} · ${esc(t.name)}</span>`).join('')}</div>${rich(data.review.difficulty.note)}`;
  return `<h3>答案与解析预览</h3>${rich(data.solve.answer)}${(data.solve.subanswers||[]).map(s=>`<p>（${esc(s.key)}）${rich(s.answer)}</p>`).join('')}${(data.solve.solutions||[]).map(s=>`<h4>${esc(s.label)}</h4>${rich(s.approach)}${rich(s.steps)}${rich(s.conditions)}`).join('')}`;
}

export function openAIEditor({question, kind, scope, onSaved=()=>{}, current=()=>true}) {
  const q=question, candidate=q.candidate;
  let data=structuredClone(kind==='source'?q.source:{solve:candidate.content.solve,review:candidate.content.review});
  const base=kind==='source'?q.current_revision:candidate.content_hash;
  const store=draftStore(scope,`ai:${q.id}:${kind}`), recovered=store.read();
  let closed=false, tab='solve';
  const modal=openModal({title:kind==='source'?'修订题面':'修订答案、解析与分类',width:'modal-ai-editor',
    body:'<div data-editor-recovery></div><p class="hint">修改会保留在本机浏览器草稿中，点击保存后写入题库。支持 $…$ 数学公式。</p><div class="visual-editor"><form data-editor-form></form><aside data-editor-preview class="editor-preview"></aside></div><p data-editor-status role="status"></p>',
    footer:`<button class="btn" data-close>暂存并关闭</button><button class="btn btn-primary" data-editor-save>${kind==='source'?'保存题面（需重新解答）':'保存候选修订'}</button>`,onClose:()=>{closed=true;}});
  const $=s=>modal.modal.querySelector(s);
  const value=path=>path.split('.').reduce((obj,k)=>obj?.[k],data);
  const field=(path,label,type='text',choices=null)=>`<label class="field"><span>${esc(label)}</span>${choices?`<select data-path="${path}">${Object.entries(choices).map(([v,t])=>`<option value="${esc(v)}" ${v===String(value(path))?'selected':''}>${esc(t)}</option>`).join('')}</select>`:type==='area'?`<textarea rows="4" data-path="${path}">${esc(value(path)||'')}</textarea>`:`<input data-path="${path}" type="${type}" ${type==='number'?'min="0" max="150" step="0.5"':''} value="${esc(value(path)??'')}">`}</label>`;
  const array=(path,title,fields,limit)=>`<section class="editor-group"><div class="editor-group-head"><h3>${title}</h3>${limit?`<button type="button" class="btn btn-sm" data-array-add="${path}" ${value(path)?.length>=limit?'disabled':''}>添加</button>`:''}</div>${(value(path)||[]).map((row,i)=>`<div class="editor-row"><div class="editor-group-head"><b>${esc(row.key||row.label||i+1)}</b>${limit?`<button type="button" class="btn-link" data-array-remove="${path}" data-index="${i}">移除</button>`:''}</div>${fields.map(([key,label,type,choices])=>field(`${path}.${i}.${key}`,label,type,choices)).join('')}</div>`).join('')}</section>`;
  function render() {
    if(kind==='source'){
      data.options||=[];data.subquestions||=[];
      $('[data-editor-form]').innerHTML=field('qtype','题型','text',{single:'单选',multi:'多选',blank:'填空',solve:'解答'})+field('total_points','分值','number')+field('stem','题面','area')+
        array('options','选项',[['text','选项内容','area']],6)+array('subquestions','小问',[['text','小问内容','area']],20);
    }else{
      data.review.tags||=[];
      $('[data-editor-form]').innerHTML=`<div class="editor-tabs"><button type="button" class="btn ${tab==='solve'?'active':''}" data-editor-tab="solve">答案与解析</button><button type="button" class="btn ${tab==='review'?'active':''}" data-editor-tab="review">分类与评分</button></div><div ${tab!=='solve'?'hidden':''}>`+field('solve.status','解答状态','text',{solved:'已解答',uncertain:'有疑问',needs_input:'需补充信息'})+field('solve.answer','答案','area')+
        field('solve.answer_keys','正确选项（用逗号分隔）')+array('solve.subanswers','各小问答案',[['answer','小问答案','area']],0)+
        array('solve.solutions','解析与解法',[['label','解法名称'],['approach','思路','area'],['steps','解答步骤','area'],['conditions','适用条件','area'],['teacher_tip','讲评提示','area']],4)+
        `</div><div ${tab!=='review'?'hidden':''}><h3>复核与评分</h3>`+field('review.verdict','复核结论','text',{needs_review:'需人工复核',no_issue_found:'未发现问题',conflict:'存在分歧',needs_input:'需补充信息'})+field('review.reason','复核理由','area')+
        ['calc','think','scope'].map((key,i)=>field(`review.difficulty.${key}`,['运算难度','思维难度','知识跨度'][i],'text',Object.fromEntries([1,2,3,4,5].map(n=>[n,String(n)])))).join('')+
        field('review.difficulty.note','难度说明','area')+field('review.rec_level','推荐等级','text',{S:'S',A:'A',B:'B',C:'C'})+field('review.rec_reason','推荐理由','area')+field('review.time_solve_min','预计作答时间（分钟）','number')+
        array('review.tags','分类标签',[['dimension','分类','text',DIMENSIONS],['name','标签名称'],['reason','分类理由']],30)+'</div>';
    }
    preview();
  }
  function preview(){if(!closed)$('[data-editor-preview]').innerHTML=editorPreview(data,kind==='candidate'&&tab==='review'?'review':kind);}
  function keep(){const ok=store.write(data,base);$('[data-editor-recovery]').replaceChildren();$('[data-editor-status]').textContent=ok?'修改已暂存到本机浏览器，尚未提交':'浏览器无法保存草稿，请及时提交或复制内容';}
  function onInput(e){const el=e.target.closest('[data-path]');if(!el)return;
    let v=el.value;const path=el.dataset.path;
    if(path==='solve.answer_keys')v=v.split(/[,，、\s]+/).filter(Boolean);
    else if(path.startsWith('review.difficulty.')&&['calc','think','scope'].includes(path.split('.').at(-1)))v=Number(v);
    else if(el.type==='number')v=v===''?null:Number(v);
    setEditorValue(data,path,v);keep();preview();
  }
  $('[data-editor-form]').onsubmit=e=>e.preventDefault();
  $('[data-editor-form]').addEventListener('input',onInput);
  $('[data-editor-form]').addEventListener('change',onInput);
  $('[data-editor-form]').onclick=e=>{
    const switchTab=e.target.closest('[data-editor-tab]');if(switchTab){tab=switchTab.dataset.editorTab;render();return;}
    const add=e.target.closest('[data-array-add]'),remove=e.target.closest('[data-array-remove]');
    if(!add&&!remove)return;
    const path=add?.dataset.arrayAdd||remove.dataset.arrayRemove,rows=value(path);
    const limits={options:6,subquestions:20,'solve.solutions':4,'review.tags':30};
    if(add){
      if(rows.length>=limits[path])return;
      let row={};
      if(path==='options')row={key:[...'ABCDEF'].find(k=>!rows.some(r=>r.key===k)),text:''};
      if(path==='subquestions'){let n=1;while(rows.some(r=>r.key===String(n)))n++;row={key:String(n),text:'',depends_on:[]};}
      if(path==='solve.solutions')row={label:`解法${rows.length+1}`,approach:'',steps:'',conditions:'',teacher_tip:''};
      if(path==='review.tags')row={dimension:'knowledge',name:'',reason:''};rows.push(row);
    }else rows.splice(Number(remove.dataset.index),1);
    keep();render();
  };
  if(recovered){
    $('[data-editor-recovery]').innerHTML=`<div class="draft-banner"><span>${recovered.base===base?'发现未提交草稿':'发现旧草稿，题目结果已更新，请核对后恢复'}</span><button class="btn btn-sm" data-recover>恢复</button><button class="btn btn-sm" data-discard>丢弃</button></div>`;
    $('[data-recover]').onclick=async()=>{
      if(recovered.base!==base&&!await confirmDialog({title:'恢复旧草稿',message:'当前结果已更新。恢复后请逐项核对，保存将提交为当前结果的新修订。',confirmText:'恢复并核对'}))return;
      if(closed)return;data=structuredClone(recovered.data);$('[data-editor-recovery]').replaceChildren();render();keep();
    };
    $('[data-discard]').onclick=()=>{store.clear();$('[data-editor-recovery]').replaceChildren();};
  }
  $('[data-editor-save]').onclick=async()=>{
    if(!current()||!$('[data-editor-form]').reportValidity())return;
    const controls=modal.modal.querySelectorAll('input,select,textarea,button:not([data-close])');controls.forEach(e=>e.disabled=true);
    try{
      const body=kind==='source'?{source:data,base_revision:q.current_revision}:{...data,content_hash:candidate.content_hash};
      await api(`/api/ai/questions/${q.id}/${kind==='source'?'edit':'candidate'}`,{method:'POST',body});
      store.clear();modal.close();if(current())await onSaved();flash(kind==='source'?'题面已修订，继续任务后重新解答。':'候选修订已保存。','ok');
    }catch(e){if(!closed){$('[data-editor-status]').textContent=e.message;controls.forEach(x=>x.disabled=false);}}
  };
  render();loadKatex().then(()=>{if(!closed)preview();});
  return modal;
}
