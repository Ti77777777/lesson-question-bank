import assert from 'node:assert/strict';
import test from 'node:test';
import { searchConditions, savedSearchUrl } from '../app/static/lib/saved-searches.js';
import { normalBox } from '../app/static/lib/region-editor.js';

test('saved searches omit paging but retain exclusions, grade and correction status',()=>{
  const q={keyword:'导数 & 分类讨论',source_grade:'senior3',tags:'A,B',tag_mode:'exclude',think_min:'2',review:'pass',correction:'pending',offset:30,limit:30,status:'active',source_id:undefined};
  const saved=searchConditions(q);
  assert.equal(saved.offset,undefined);assert.equal(saved.source_id,undefined);
  const url=savedSearchUrl(q);const restored=Object.fromEntries(new URLSearchParams(url.split('?')[1]));
  assert.deepEqual(restored,saved);assert.equal(restored.keyword,'导数 & 分类讨论');assert.equal(restored.tag_mode,'exclude');
});
test('empty saved search restores an unfiltered search page',()=>assert.equal(savedSearchUrl({offset:30,status:'active'}),'#/search'));
test('dragging backwards or beyond page edges yields normalized coordinates',()=>{
  assert.deepEqual(normalBox([.8,.9],[.2,.1]),[.2,.1,.8,.9]);
  assert.deepEqual(normalBox([-.2,.1],[1.3,1.4]),[0,.1,1,1]);
});
