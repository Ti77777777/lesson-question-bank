import test from 'node:test';
import assert from 'node:assert/strict';
import { chooseFilter, singleFilter, selectionValues, matchesFilter } from '../app/static/lib/search-selection.js';
import { savedSearchUrl } from '../app/static/lib/saved-searches.js';

test('filter groups switch independently and single mode keeps the latest remaining choice',()=>{
  let category=chooseFilter('monthly','mock',true),grade='senior3';
  assert.equal(category,'monthly,mock');assert.equal(grade,'senior3');
  category=singleFilter(category);assert.equal(category,'mock');
  assert.equal(chooseFilter(category,'final',false),'final');
  assert.equal(chooseFilter('monthly,mock','mock',true),'monthly');
  assert.equal(chooseFilter('monthly,mock','',true),'');
  assert.deepEqual(selectionValues('monthly,monthly,mock'),['monthly','mock']);
  assert.equal(matchesFilter('monthly,mock','mock'),true);
  assert.equal(matchesFilter('monthly,mock','final'),false);
});
test('shared searches retain selection modes, nonadjacent levels and multiple source filters',()=>{
  const query={multi:'category,levels,year',source_category:'mock,monthly',think_levels:'basic,advanced',source_year:'2025,2026'};
  const url=savedSearchUrl(query),params=new URLSearchParams(url.split('?')[1]);
  for(const [key,value] of Object.entries(query))assert.equal(params.get(key),value);
});
