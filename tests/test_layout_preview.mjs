import assert from 'node:assert/strict';
import test from 'node:test';
import { paginateBlocks } from '../app/static/lib/layout-preview.js';

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
