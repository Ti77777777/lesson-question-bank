import test from 'node:test';
import assert from 'node:assert/strict';
import {updateEntry,updateMarkup} from '../app/static/lib/version-updates.js';
import {usageOperations} from '../app/static/lib/usage-state.js';
const row={item_id:'ITM1',question_id:'Q1',short_name:'<script>题</script>',order_no:0,
 used:{version_id:'V1',version_no:1,subq_keys:['old'],subquestions:[{key:'old',text:'原问'}]},
 new:{version_id:'V2',version_no:2,content_digest:'digest',content:{id:'V2',version_no:2,stem:'$x$ <img>',subquestions:[{key:'a',text:'新版问'}]},solutions:[]}};

test('version update binds preview and requires explicit subquestion and solution intent',()=>{
 assert.throws(()=>updateEntry(row,{mode:'',solution:'all'}));
 assert.throws(()=>updateEntry(row,{mode:'selected',keys:[],solution:'all'}));
 assert.throws(()=>updateEntry(row,{mode:'all',solution:''}));
 const out=updateEntry(row,{mode:'selected',keys:['a'],solution:'all'});
 assert.equal(out.from_version_id,'V1');assert.equal(out.question_version_id,'V2');assert.equal(out.content_digest,'digest');
 assert.deepEqual(out.subquestions,{mode:'selected',keys:['a']});assert.equal(out.solution_id,null);
 const operations=usageOperations(()=>Math.random().toString());
 assert.equal(operations.forPayload(out),operations.forPayload(structuredClone(out)));
 assert.notEqual(operations.forPayload(out),operations.forPayload({...out,content_digest:'changed'}));
});

test('version preview escapes content and offers old questions and full new content for review',()=>{
 const html=updateMarkup(row,0);
 assert.ok(html.includes('原先选用的小问'));assert.ok(html.includes('原问'));assert.ok(html.includes('新版问'));
 assert.ok(html.includes('查看新版题面与解析'));assert.ok(html.includes('请选择范围'));
 assert.ok(html.includes('data-subselection hidden'));assert.ok(html.includes('data-rich'));
 assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('&lt;img&gt;'));assert.ok(!html.includes('<script>'));
});
