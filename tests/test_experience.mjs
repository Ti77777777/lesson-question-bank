import assert from 'node:assert/strict';
import test from 'node:test';
import {draftStore, autosaver} from '../app/static/lib/drafts.js';
import {setEditorValue, editorPreview} from '../app/static/lib/ai-editor.js';
import {drawerContent} from '../app/static/lib/question-drawer.js';

function memory(){const entries=new Map();return {getItem:k=>entries.get(k)||null,setItem:(k,v)=>entries.set(k,v),removeItem:k=>entries.delete(k)};}
test('drafts are database scoped; old tab cannot clear newer edits',()=>{
  const storage=memory(),a=draftStore('db1','question',storage),b=draftStore('db1','question',storage);
  a.write({stem:'first'},'v1');assert.equal(draftStore('db2','question',storage).read(),null);
  b.write({stem:'second'},'v1');a.clear();assert.equal(b.read().data.stem,'second');
  b.clear();assert.equal(b.read(),null);
  const blocked=draftStore('x','x',{getItem(){throw Error();},setItem(){throw Error();}});
  assert.equal(blocked.read(),null);assert.equal(blocked.write({x:1}),false);
});
test('serialized autosave sends newer edits after slow response and advances revision',async()=>{
  let value={title:'one'},resolve;const calls=[];const store=draftStore('db','layout',memory());
  const saver=autosaver({read:()=>value,store,base:'v0',delay:10000,save:async(data,base)=>{
    calls.push({data,base});if(calls.length===1)await new Promise(r=>resolve=r);return {revision:`v${calls.length}`};
  }});
  saver.change();const pending=saver.flush();value={title:'two'};saver.change();resolve();await pending;
  assert.deepEqual(calls.map(c=>[c.data.title,c.base]),[['one','v0'],['two','v1']]);
  assert.equal(saver.dirty,false);assert.equal(store.read(),null);saver.dispose();
});
test('failed save retains draft and error; retry uses the same base',async()=>{
  const store=draftStore('db','layout',memory());let fail=true;const states=[];
  const saver=autosaver({read:()=>({title:'draft'}),store,base:'original',delay:10000,onState:s=>states.push(s),save:async(_,base)=>{assert.equal(base,'original');if(fail)throw Error('conflict');return {revision:'next'};}});
  saver.change();await assert.rejects(saver.flush(),/conflict/);assert.equal(store.read().data.title,'draft');assert.equal(states.at(-1),'error');
  fail=false;await saver.flush();assert.equal(saver.dirty,false);saver.dispose();
});
test('leaving during a save does not overwrite drafts created by a reopened page',async()=>{
  const storage=memory(),oldStore=draftStore('db','layout',storage),newStore=draftStore('db','layout',storage);
  let value={title:'old'},resolve;
  const saver=autosaver({read:()=>value,store:oldStore,base:'v0',delay:10000,save:()=>new Promise(r=>resolve=r)});
  saver.change();const pending=saver.flush();value={title:'pending old edit'};saver.change();saver.dispose();
  newStore.write({title:'reopened edit'},'v1');resolve({revision:'v1'});await pending;
  assert.equal(newStore.read().data.title,'reopened edit');
});
test('editing a visible field preserves hidden image metadata and classification details',()=>{
  const data={stem:'old',statement_images:[{path:'assets/demo.png'}],options:[{key:'A',text:'old'}]};
  setEditorValue(data,'options.0.text','$x+1$');assert.equal(data.options[0].text,'$x+1$');assert.equal(data.statement_images[0].path,'assets/demo.png');
  assert.throws(()=>setEditorValue(data,'__proto__.polluted',true));
  data.stem='<script>bad</script>';assert.doesNotMatch(editorPreview(data,'source'),/<script>/);
});
test('drawer presents full options, subquestion answers and sources with escaped text',()=>{
  const html=drawerContent({current_version:{qtype:'single',stem:'题目',answer:'A',options:[{key:'A',text:'<img onerror=bad>'}],subquestions:[{key:'1',text:'小问',answer:'2'}],solutions:[],tags:[]},occurrences:[{source_name:'演示卷',qno:'3',pdf_page:2}],eligibility:{usable:false}});
  assert.match(html,/&lt;img/);assert.match(html,/演示卷/);assert.match(html,/第3题/);assert.match(html,/查看答案与解析/);assert.doesNotMatch(html,/<img onerror/);
});
