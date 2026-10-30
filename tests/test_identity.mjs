import assert from 'node:assert/strict';
import test from 'node:test';
import {identityLabel} from '../app/static/lib/identity.js';
import {api} from '../app/static/lib/api.js';

test('homonyms without manual codes remain distinguishable by stable IDs',()=>{
  const a={id:'AUD1',name:'同名学生'},b={id:'AUD2',name:'同名学生'};
  assert.notEqual(identityLabel(a),identityLabel(b));
  assert.match(identityLabel({...a,code:'2027-01'}),/2027-01/);
  assert.equal(identityLabel({...a,display_name:'服务端身份标签'}),'服务端身份标签');
});

test('duplicate identity details survive the API error wrapper',async()=>{
  const previous=globalThis.fetch;
  const candidates=[{id:'TCH1',name:'同名老师',code:'T01'}];
  try {
    globalThis.fetch=async()=>({ok:false,status:409,text:async()=>JSON.stringify({error:{code:'same_name',message:'核对同名',detail:{candidates}}})});
    await assert.rejects(api('/api/teachers'),error=>{
      assert.equal(error.code,'same_name');assert.deepEqual(error.detail.candidates,candidates);return true;
    });
  } finally {globalThis.fetch=previous;}
});
