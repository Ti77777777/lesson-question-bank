import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyTag, makeTagDirectory, filterTagDirectory} from '../app/static/lib/tag-taxonomy.js';

test('相近名称按标签维度浏览，具体教材册别优先', () => {
  assert.equal(classifyTag({dimension:'knowledge', name:'集合的交集与并集'}), '集合');
  assert.equal(classifyTag({dimension:'prereq', name:'集合的交集与并集'}), '集合');
  assert.equal(classifyTag({dimension:'method', name:'韦恩图法'}), '图形与直观');
  assert.equal(classifyTag({dimension:'knowledge', name:'三角函数的单调性'}), '三角函数与解三角形');
  assert.equal(classifyTag({dimension:'textbook', name:'人教A版选择性必修第一册'}), '选择性必修第一册');
  assert.equal(classifyTag({dimension:'error', name:'二次项系数符号与不等号方向'}), '运算与符号');
  assert.equal(classifyTag({dimension:'error', name:'量词或不等号否定错误'}), '逻辑与否定');
});

test('教师父级优先，缺失或循环的父级不造成丢标签和死循环', () => {
  const tags = [
    {id:'p',dimension:'knowledge',name:'教师自定专题'},
    {id:'c',dimension:'knowledge',name:'集合与函数',parent_id:'p'},
    {id:'a',dimension:'method',name:'自定方法A',parent_id:'b'},
    {id:'b',dimension:'method',name:'自定方法B',parent_id:'a'},
    {id:'missing',dimension:'knowledge',name:'分式不等式',parent_id:'gone'},
  ];
  const result = makeTagDirectory(tags);
  assert.equal(result.length, 5);
  assert.equal(result.find(e=>e.tag.id==='c').topic, '教师自定专题');
  assert.equal(result.find(e=>e.tag.id==='p').topic, '教师自定专题');
  assert.equal(result.find(e=>e.tag.id==='missing').topic, '不等式');
  assert.ok(result.filter(e=>['a','b'].includes(e.tag.id)).every(e=>e.topic==='其他解题方法'));
});

test('未知主题与维度保留入口，停用和已合并标签不进入候选目录', () => {
  const result = makeTagDirectory([
    {id:'x',dimension:'future',name:'新标签'},
    {id:'y',dimension:'knowledge',name:'新专题'},
    {id:'z',dimension:'method',name:'停用方法',status:'inactive'},
    {id:'m',dimension:'knowledge',name:'已合并',merged_into:'y'},
    {id:'y',dimension:'knowledge',name:'重复记录'},
  ]);
  assert.equal(result.length, 2);
  assert.equal(result.find(e=>e.tag.id==='x').dimension, 'other');
  assert.equal(result.find(e=>e.tag.id==='y').topic, '其他知识点');
});

test('分类、主题、说明搜索和只看已选可组合，筛选不更改跨类选择', () => {
  const directory = makeTagDirectory([
    {id:'k',dimension:'knowledge',name:'集合的并集运算',definition:'把两个集合合并去重'},
    {id:'m',dimension:'method',name:'韦恩图法'},
    {id:'i',dimension:'idea',name:'数形结合'},
  ]);
  const chosen = new Set(['k','m']);
  assert.deepEqual(filterTagDirectory(directory,{dimension:'knowledge',topic:'集合',keyword:'去重',selectedOnly:true,chosen}).map(e=>e.tag.id), ['k']);
  assert.equal(filterTagDirectory(directory,{dimension:'method',keyword:'集合'}).length, 0);
  assert.equal(filterTagDirectory(directory,{keyword:'图形与直观'}).length, 1);
  assert.equal(filterTagDirectory(directory,{selectedOnly:true,chosen}).length, 2);
  assert.deepEqual([...chosen], ['k','m']);
});
