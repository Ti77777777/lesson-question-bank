import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {normaliseMathBody} from '../app/static/lib/math-notation.js';
import {richToHtml} from '../app/static/lib/math.js';

test('browser and export use the same notation cases and leave protected text intact',()=>{
  const cases=JSON.parse(readFileSync(new URL('../examples/notation-cases.json',import.meta.url),'utf8'));
  const spans=/(?<!\\)(?:\$(?!\$)([^$\n]+)\$|\$\$([\s\S]+?)\$\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\])/g;
  for(const row of cases){
    const actual=row.source.replace(spans,(whole,...groups)=>{
      const body=groups.slice(0,4).find(v=>v!==undefined),start=whole.indexOf(body);
      return whole.slice(0,start)+normaliseMathBody(body)+whole.slice(start+body.length);
    });
    assert.equal(actual,row.expected);
  }
});

test('all rich render callers receive a superscript derivative and escaped ordinary text',()=>{
  const saved=globalThis.window,calls=[];
  try{
    globalThis.window={katex:{renderToString:tex=>{calls.push(tex);return '<span class="katex">formula</span>';}}};
    const html=richToHtml(String.raw`<b>原文</b> $f\prime(x)=−2$`);
    assert.deepEqual(calls,[String.raw`f^{\prime}(x)=-2`]);assert.match(html,/&lt;b&gt;/);
  }finally{if(saved===undefined)delete globalThis.window;else globalThis.window=saved;}
});
