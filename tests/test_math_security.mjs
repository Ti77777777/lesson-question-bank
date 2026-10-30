import assert from 'node:assert/strict';
import test from 'node:test';
import {loadKatex, richToHtml} from '../app/static/lib/math.js';

test('math loading pins its release and requires browser integrity checks for JS and CSS', async () => {
  const saved={document:globalThis.document,window:globalThis.window,setTimeout:globalThis.setTimeout};
  const elements=[];
  try {
    globalThis.document={createElement:tag=>({tag}),head:{appendChild:node=>elements.push(node)}};
    globalThis.window={katex:{}};
    globalThis.setTimeout=()=>0;
    const loading=loadKatex();
    const script=elements.find(e=>e.tag==='script'),css=elements.find(e=>e.tag==='link');
    for(const element of [script,css]) {
      assert.match(element.src||element.href,/katex@0\.19\.1\/dist\/katex\.min\.(?:js|css)$/);
      assert.match(element.integrity,/^sha384-[A-Za-z0-9+/]{64}$/);
      assert.equal(element.crossOrigin,'anonymous');
    }
    script.onload();
    assert.equal(await loading,true);
  } finally {
    for(const [key,value] of Object.entries(saved)) {
      if(value===undefined)delete globalThis[key];else globalThis[key]=value;
    }
  }
});

test('math rendering disables trusted commands and bounds expansion and oversized rules', () => {
  const saved=globalThis.window;
  const options=[];
  try {
    globalThis.window={katex:{renderToString:(_tex,opts)=>{options.push(opts);return '<span>fixture</span>';}}};
    richToHtml('$x+1$ $$\\rule{999999em}{1em}$$');
    assert.equal(options.length,2);
    for(const opts of options) {
      assert.equal(opts.trust,false);
      assert.equal(opts.maxExpand,1000);
      assert.equal(opts.maxSize,50);
    }
  } finally {
    if(saved===undefined)delete globalThis.window;else globalThis.window=saved;
  }
});
