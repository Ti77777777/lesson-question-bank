import assert from 'node:assert/strict';
import test from 'node:test';
import { paginateBlocks } from '../app/static/lib/layout-preview.js';
import { groupedItems, movePreviewItem, resizedSpace } from '../app/static/lib/layout-interactions.js';

test('explicit page breaks and measured overflow create separate pages', () => {
  assert.deepEqual(paginateBlocks([{height:30},{height:40,breakBefore:true},{height:90}],100).map(p=>p.length),[1,1,1]);
});
test('section heading follows its first question when both fit on an empty page', () => {
  const pages=paginateBlocks([{height:80},{height:15,heading:true},{height:30}],100);
  assert.deepEqual(pages.map(p=>p.length),[1,2]);
});
test('long questions remain visible and do not produce an empty page', () => {
  const long={height:200,breakBefore:true};
  assert.deepEqual(paginateBlocks([long],100),[[long]]);
  assert.deepEqual(paginateBlocks([],100),[]);
});
test('a section heading follows an explicit break on its first question', () => {
  const pages=paginateBlocks([{height:30},{height:15,heading:true},{height:30,breakBefore:true}],100);
  assert.deepEqual(pages.map(p=>p.length),[1,2]);
  assert.deepEqual(paginateBlocks([{height:15,heading:true},{height:30,breakBefore:true}],100).map(p=>p.length),[2]);
});

test('direct sorting can move between sections without losing settings or mutating source',()=>{
  const sections=[{id:'s1'},{id:'s2'}],rows=[{id:'a',section_id:'s1',space_mm:35},{id:'b',section_id:'s1'},{id:'c',section_id:'s2'}];
  const moved=movePreviewItem(rows,sections,'a','c',false);
  assert.deepEqual(moved.map(i=>[i.id,i.section_id]),[['b','s1'],['c','s2'],['a','s2']]);
  assert.equal(moved[2].space_mm,35);assert.equal(rows[0].section_id,'s1');
  assert.deepEqual(movePreviewItem(rows,sections,'b','a',true).map(i=>i.id),['b','a','c']);
  assert.equal(movePreviewItem(rows,sections,'a','missing'),rows);
  assert.deepEqual(groupedItems([{id:'z',section_id:null},...rows],sections).map(i=>i.id),['a','b','c','z']);
});

test('resizing uses the displayed scale and stays within printable blank-space limits',()=>{
  assert.equal(resizedSpace(30,40,4),40);
  assert.equal(resizedSpace(30,20,2),40);
  assert.equal(resizedSpace(0,-400,4),0);
  assert.equal(resizedSpace(190,400,4),200);
  assert.equal(resizedSpace(35,10,0),35);
});
