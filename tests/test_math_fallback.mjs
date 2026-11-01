import assert from 'node:assert/strict';
import test from 'node:test';

// The loader keeps a module-level promise, so this test imports a fresh instance.
test('math loading falls back to the next CDN when the primary host is blocked', async () => {
  const saved = { document: globalThis.document, window: globalThis.window };
  const nodes = [];
  try {
    globalThis.window = {};
    globalThis.document = {
      createElement: (tag) => ({ tag }),
      head: {
        appendChild(node) {
          nodes.push(node);
          setTimeout(() => {
            if (node.tag === 'script') {
              if (node.src.includes('cdn.jsdelivr.net') && !node.src.includes('fastly')) {
                node.onerror && node.onerror();
                return;
              }
              globalThis.window.katex = { renderToString: () => '<span class="katex"></span>' };
              node.onload && node.onload();
              return;
            }
            if (node.href.includes('cdn.jsdelivr.net') && !node.href.includes('fastly')) {
              node.onerror && node.onerror();
              return;
            }
            node.onload && node.onload();
          }, 0);
          return node;
        },
      },
    };
    const { loadKatex } = await import('../app/static/lib/math.js?fallback-test');
    assert.equal(await loadKatex(), true);
    assert.equal(Boolean(globalThis.window.katex), true);
    const scripts = nodes.filter((node) => node.tag === 'script').map((node) => node.src);
    assert.equal(scripts.length, 2);
    assert.match(scripts[0], /cdn\.jsdelivr\.net/);
    assert.match(scripts[1], /fastly\.jsdelivr\.net/);
    const styles = nodes.filter((node) => node.tag === 'link').map((node) => node.href);
    assert.ok(styles.some((href) => href.includes('fastly.jsdelivr.net')), 'stylesheet should also fall back');
  } finally {
    if (saved.document === undefined) delete globalThis.document; else globalThis.document = saved.document;
    if (saved.window === undefined) delete globalThis.window; else globalThis.window = saved.window;
  }
});


async function loaderFixture(name, run) {
  const saved={document:globalThis.document,window:globalThis.window,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout,warn:console.warn};
  const nodes=[],timers=new Map();let serial=0;
  try {
    globalThis.window={};
    globalThis.document={createElement:tag=>({tag,remove(){this.removed=true;}}),head:{appendChild(node){nodes.push(node);}}};
    globalThis.setTimeout=fn=>{const id=++serial;timers.set(id,fn);return id;};
    globalThis.clearTimeout=id=>timers.delete(id);
    console.warn=()=>{};
    const module=await import('../app/static/lib/math.js?'+name);
    await run({module,nodes,timers,flush:async()=>{for(let i=0;i<12;i++)await Promise.resolve();}});
  } finally {
    console.warn=saved.warn;
    for(const key of ['document','window','setTimeout','clearTimeout']) {
      if(saved[key]===undefined)delete globalThis[key];else globalThis[key]=saved[key];
    }
  }
}

test('math readiness waits for styles before comparison or pagination can measure it',async()=>{
  await loaderFixture('css-readiness',async({module,nodes,timers,flush})=>{
    let complete=false;const pending=module.loadKatex().then(ok=>{complete=true;return ok;});
    window.katex={renderToString:()=>'<span class="katex">ready</span>'};
    nodes.find(n=>n.tag==='script').onload();await flush();
    assert.equal(complete,false);
    nodes.find(n=>n.tag==='link').onload();assert.equal(await pending,true);
    assert.equal(timers.size,0);
  });
});

test('stylesheet timeout is a failure and advances to the next source',async()=>{
  await loaderFixture('css-timeout',async({module,nodes,timers,flush})=>{
    const pending=module.loadKatex();
    window.katex={};nodes.find(n=>n.tag==='script').onload();
    const first=nodes.find(n=>n.tag==='link');
    const [id,timeout]=[...timers.entries()][0];timers.delete(id);timeout();await flush();
    assert.equal(first.removed,true);
    const styles=nodes.filter(n=>n.tag==='link');assert.equal(styles.length,2);
    assert.match(styles[1].href,/fastly/);styles[1].onload();
    assert.equal(await pending,true);assert.equal(timers.size,0);
  });
});

test('style exhaustion falls back to escaped source and can recover without reloading the app',async()=>{
  await loaderFixture('css-retry',async({module,nodes,timers,flush})=>{
    const pending=module.loadKatex();
    window.katex={renderToString:()=>'<span class="katex">ready</span>'};
    nodes.find(n=>n.tag==='script').onload();
    for(let i=0;i<4;i++){nodes.filter(n=>n.tag==='link')[i].onerror();await flush();}
    assert.equal(await pending,false);assert.equal(module.katexState(),false);
    assert.match(module.richToHtml('$x<1$'),/tex-src/);
    assert.doesNotMatch(module.richToHtml('$x<1$'),/<span class="katex">/);
    const start=nodes.length,retry=module.loadKatex();
    nodes.slice(start).find(n=>n.tag==='script').onload();nodes.slice(start).find(n=>n.tag==='link').onload();
    assert.equal(await retry,true);assert.equal(module.katexState(),true);
    assert.match(module.richToHtml('$x$'),/class="katex"/);assert.equal(timers.size,0);
  });
});
