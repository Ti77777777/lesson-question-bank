import assert from 'node:assert/strict';
import test from 'node:test';
import {usageOperations, latestSelection, snapshotUsageItems} from '../app/static/lib/usage-state.js';

test('an uncertain submission keeps its ID through retries and temporary edits', () => {
  let counter=0;
  const ops=usageOperations(()=>String(++counter));
  const payload={release_id:'RLS1', items:['OLD'], note:null};
  const original=ops.forPayload(payload);
  assert.equal(ops.forPayload({...payload}),original);
  assert.notEqual(ops.forPayload({...payload, items:['OTHER']}),original);
  assert.equal(ops.forPayload(payload),original);
  ops.clear();
  assert.notEqual(ops.forPayload(payload),original);
});

test('a slower old release response cannot replace the current release', async () => {
  const selection=latestSelection();
  let first,second,rendered;
  async function read(deferred) {
    const current=selection.start();
    const result=await deferred;
    if(current())rendered=result;
  }
  const old=read(new Promise(resolve=>first=resolve));
  const next=read(new Promise(resolve=>second=resolve));
  second('second release');await next;
  first('first release');await old;
  assert.equal(rendered,'second release');
  const previous=selection.start();selection.invalidate();assert.equal(previous(),false);
});

test('usage entries resolve historical versions and reject conflicting legacy versions', () => {
  const snapshot={items:[{id:'OLD',question_id:'Q1',question_version_id:'V1'}],
    question_versions:{V1:{question:{short_name:'导出时的题目'},version:{id:'V1'}}}};
  assert.deepEqual(snapshotUsageItems(snapshot).map(i=>[i.id,i.label]),[['OLD','导出时的题目']]);
  const legacy={items:snapshot.items,questions:{Q1:snapshot.question_versions.V1}};
  assert.equal(snapshotUsageItems(legacy)[0].label,'导出时的题目');
  assert.throws(()=>snapshotUsageItems({...legacy,questions:{Q1:{version:{id:'V2'}}}}),/版本不匹配/);
  assert.throws(()=>snapshotUsageItems({}),/历史题目/);
  assert.deepEqual(snapshotUsageItems({items:[]}),[]);
});
