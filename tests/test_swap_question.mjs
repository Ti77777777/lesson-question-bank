import assert from 'node:assert/strict';
import test from 'node:test';
import {originalVersion,comparisonContent} from '../app/static/lib/swap-question.js';
import {contextHelp} from '../app/static/lib/ui.js';

test('comparison reads the exact collection version even when a newer version exists',async()=>{
  const calls=[];
  const detail={question:{id:'Q1'},current_version:{id:'QV2',version_no:2,stem:'新版'}};
  const read=async path=>{calls.push(path);return path.endsWith('/versions/1')?{id:'QV1',version_no:1,stem:'原题内容'}:detail;};
  const out=await originalVersion({question_id:'Q1',question_version_id:'QV1',question:{version_no:1}},read);
  assert.equal(out.current_version.stem,'原题内容');assert.equal(out.question.id,'Q1');
  assert.deepEqual(calls,['/api/questions/Q1','/api/questions/Q1/versions/1']);
  const current=await originalVersion({question_id:'Q1',question_version_id:'QV2'},async()=>detail);
  assert.equal(current.current_version.id,'QV2');
  await assert.rejects(originalVersion({question_id:'Q1',question_version_id:'MISSING',question:{version_no:1}},read),/版本不匹配/);
});

test('comparison includes full text, options and subquestions with answers initially collapsed',()=>{
  const tail='长题最后一段不可截断';
  const html=comparisonContent({question:{id:'Q1',short_name:'对照题'},current_version:{id:'QV1',version_no:1,qtype:'single',stem:'题干'.repeat(100)+tail,options:[{key:'A',text:'<img onerror=bad>'}],subquestions:[{key:'1',text:'小问全文',answer:'小问答案'}],answer:'答案内容',analysis:'完整分析',solutions:[{label:'解法一',steps:'完整过程'}]},occurrences:[{question_version_id:'QV1',source_name:'测试卷',qno:'3'},{question_version_id:'OTHER',source_name:'别版来源'}]});
  for(const text of [tail,'小问全文','小问答案','答案内容','完整分析','完整过程','测试卷'])assert.ok(html.includes(text));
  assert.match(html,/<details class="swap-answer">/);assert.doesNotMatch(html,/<details[^>]+open|<img onerror|别版来源/);
  assert.match(html,/&lt;img onerror=bad&gt;/);
});

test('supplementary help is collapsed by default and its label is escaped',()=>{
  const html=contextHelp('<说明>','<p>必要说明</p>');
  assert.match(html,/<summary>&lt;说明&gt;<\/summary>/);assert.doesNotMatch(html,/<details[^>]+open/);
});
