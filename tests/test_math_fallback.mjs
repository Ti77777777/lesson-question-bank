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
