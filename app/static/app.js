/* 应用外壳：hash 路由、题库规模计数、全局提示与选题篮。 */

import { api } from './lib/api.js';
import {
  esc, errorCard, emptyState, loading, $, truncate,
} from './lib/dom.js';
import { flash, clearFlash, consumePendingFlash, flashAfterNavigation, openModal, textDialog } from './lib/ui.js';
import {
  readBasket, writeBasket, removeFromBasket, updateBasketBadge,
} from './lib/basket.js';
import { upgradeMath } from './lib/math.js';

import inboxPage from './pages/inbox.js';
import searchPage from './pages/search.js';
import questionPage from './pages/question.js';
import questionNewPage from './pages/question-new.js';
import collectionsPage from './pages/collections.js';
import collectionPage from './pages/collection.js';
import layoutPage from './pages/layout.js';
import releasesPage from './pages/releases.js';
import usagePage from './pages/usage.js';
import aiPage from './pages/ai.js';
import sourcesPage from './pages/sources.js';

const ROUTES = [
  { pattern: /^\/sources\/?$/, page: sourcesPage, nav: 'search', title: '试卷信息管理' },
  { pattern: /^\/ai\/?$/, page: aiPage, nav: 'ai', title: 'AI 题目工作台' },
  { pattern: /^\/inbox\/?$/, page: inboxPage, nav: 'inbox', title: '收题箱' },
  { pattern: /^\/search\/?$/, page: searchPage, nav: 'search', title: '题库检索' },
  { pattern: /^\/question\/new\/?$/, page: questionNewPage, nav: 'search', title: '新建题目' },
  { pattern: /^\/question\/([^/]+)\/?$/, page: questionPage, nav: 'search', title: '题目详情', keys: ['id'] },
  { pattern: /^\/layout\/([^/]+)\/?$/, page: layoutPage, nav: 'collections', title: '讲义排版预览', keys: ['id'] },
  { pattern: /^\/collections\/?$/, page: collectionsPage, nav: 'collections', title: '题单' },
  { pattern: /^\/collection\/([^/]+)\/?$/, page: collectionPage, nav: 'collections', title: '题单编辑', keys: ['id'] },
  { pattern: /^\/releases\/?$/, page: releasesPage, nav: 'releases', title: '导出与归档' },
  { pattern: /^\/usage\/?$/, page: usagePage, nav: 'usage', title: '使用与反馈' },
];

const view = document.getElementById('view');
let renderToken = 0;
let cleanups = [];

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/inbox';
  const [pathPart, queryPart = ''] = raw.split('?');
  const path = pathPart.startsWith('/') ? pathPart : `/${pathPart}`;
  const query = {};
  const params = new URLSearchParams(queryPart);
  params.forEach((value, key) => { query[key] = value; });
  return { path, query };
}

function decode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function setActiveNav(key) {
  document.querySelectorAll('#main-nav a').forEach((link) => {
    link.classList.toggle('active', link.dataset.nav === key);
  });
}

function runCleanups() {
  [...cleanups].reverse().forEach((fn) => {
    try { fn(); } catch { /* 忽略清理异常 */ }
  });
  cleanups = [];
}

function closeModals() {
  document.querySelectorAll('#modal-root .modal-mask').forEach((node) => node.remove());
  document.body.classList.remove('modal-open');
}

async function route() {
  const { path, query } = parseHash();
  const match = ROUTES.map((entry) => ({ entry, result: path.match(entry.pattern) }))
    .find((row) => row.result);

  runCleanups();
  closeModals();
  clearFlash();
  setActiveNav(match ? match.entry.nav : '');
  document.title = match ? `习题课题库 · ${match.entry.title}` : '习题课题库';

  const token = ++renderToken;
  const pageRoot = document.createElement('div');
  pageRoot.className = 'page';
  pageRoot.innerHTML = '<div class="loading">正在载入…</div>';
  view.replaceChildren(pageRoot);

  const ctx = {
    query,
    params: {},
    current: () => token === renderToken,
    onCleanup: (fn) => { if (typeof fn === 'function') cleanups.push(fn); },
  };

  if (!match) {
    pageRoot.replaceChildren(errorCard(`没有这个页面：${path}`, () => { location.hash = '#/inbox'; }));
    return;
  }

  (match.entry.keys || []).forEach((key, index) => {
    ctx.params[key] = decode(match.result[index + 1] || '');
  });

  try {
    await match.entry.page(pageRoot, ctx);
    if (token === renderToken) {
      upgradeMath(pageRoot);
      consumePendingFlash();
      window.scrollTo(0, 0);
    }
  } catch (err) {
    if (token !== renderToken) return;
    flash(err.message || '页面渲染失败');
    pageRoot.replaceChildren(errorCard(err.message || '页面渲染失败', () => route()));
  }
}

// ---------------------------------------------------------------- 题库规模

async function loadMeta() {
  const el = document.getElementById('meta-counts');
  const foot = document.getElementById('foot-meta');
  if (!el) return;
  try {
    const meta = await api('/api/meta');
    const counts = meta.counts || {};
    const parts = [
      ['题目', counts.questions ?? 0],
      ['题单', counts.collections ?? 0],
      ['待整理', counts.inbox_pending ?? 0],
    ];
    el.innerHTML = parts
      .map(([label, value]) => `${esc(label)} <b>${esc(value)}</b>`)
      .join('<span class="sep">·</span>');
    el.title = `题目 ${counts.questions ?? 0} · 版本 ${counts.versions ?? 0} · 资料 ${counts.sources ?? 0} · 题单 ${counts.collections ?? 0} · 发布物 ${counts.releases ?? 0} · 待整理 ${counts.inbox_pending ?? 0} · 标签 ${counts.tags ?? 0}`;
    if (foot) {
      foot.textContent = `结构 v${meta.schema_version ?? '—'} · ${meta.today ?? ''}`;
      foot.title = meta.root ? `根目录：${meta.root}` : '';
    }
  } catch (err) {
    el.textContent = '题库计数未就绪';
    el.title = err?.message || 'GET /api/meta 暂不可用';
    if (foot) foot.textContent = '';
  }
}

// ---------------------------------------------------------------- 选题篮

async function openBasketModal() {
  const ids = readBasket();
  const modal = openModal({
    title: `选题篮（${ids.length}）`,
    width: 'modal-wide',
    body: ids.length ? loading('正在读取题目…') : emptyState('选题篮是空的', '到题库检索里把候选题目加进来，之后可一键建成新题单。'),
    footer: `${ids.length ? '<button type="button" class="btn btn-quiet" data-clear>清空</button>' : ''}
      <button type="button" class="btn" data-close>关闭</button>
      ${ids.length ? '<button type="button" class="btn btn-primary" data-build>一键建成新题单</button>' : ''}`,
  });

  let details = [];

  const render = () => {
    const currentIds = readBasket();
    modal.modal.querySelector('.modal-head h2').textContent = `选题篮（${currentIds.length}）`;
    if (!currentIds.length) {
      modal.body.innerHTML = emptyState('选题篮是空的', '到题库检索里把候选题目加进来，之后可一键建成新题单。');
      const buildBtn = modal.foot.querySelector('[data-build]');
      if (buildBtn) buildBtn.disabled = true;
      return;
    }
    modal.body.innerHTML = `<div class="basket-list">${currentIds.map((id, index) => {
      const detail = details.find((row) => row && row.id === id);
      const q = detail?.question || {};
      const version = detail?.current_version || {};
      return `<div class="basket-row">
        <span class="idx">${String(index + 1).padStart(2, '0')}</span>
        <div class="main">
          <a class="mono" href="#/question/${esc(id)}" style="font-size:12px;">${esc(id)}</a>
          <span class="name"> ${esc(q.short_name || '')}</span>
          <div class="stem">${esc(truncate(version.stem || q.stem_preview || '', 90) || '（题目详情暂不可用）')}</div>
        </div>
        <button type="button" class="rm" data-remove="${esc(id)}">移除</button>
      </div>`;
    }).join('')}</div>`;
  };

  if (ids.length) {
    details = await Promise.all(ids.map(async (id) => {
      try {
        const data = await api(`/api/questions/${encodeURIComponent(id)}`);
        return { id, ...data };
      } catch {
        return { id, question: {}, current_version: {} };
      }
    }));
    render();
  }

  modal.body.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-remove]');
    if (!btn) return;
    removeFromBasket(btn.dataset.remove);
    render();
  });

  const clearBtn = modal.foot.querySelector('[data-clear]');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      writeBasket([]);
      render();
    });
  }

  const buildBtn = modal.foot.querySelector('[data-build]');
  if (buildBtn) {
    buildBtn.addEventListener('click', async () => {
      const currentIds = readBasket();
      if (!currentIds.length) return;
      const title = await textDialog({
        title: '建成新题单',
        label: '题单名称',
        value: `选题篮 · ${new Date().toLocaleDateString('zh-CN')}`,
        placeholder: '如 单调性 · 候选题',
        confirmText: '建立',
        hint: `将把 ${currentIds.length} 道题按当前顺序放入新题单，教学角色默认「例题」。`,
      });
      if (title === null) return;

      buildBtn.disabled = true;
      buildBtn.textContent = '建立中…';
      try {
        const created = await api('/api/collections', {
          method: 'POST',
          body: {
            title,
            purpose: 'handout',
            duration_min: 90,
            note: '来自选题篮',
          },
        });
        const collectionId = created?.collection?.id || created?.id;
        if (!collectionId) throw new Error('题单已建立，但后端没有返回题单编号。');

        let failed = 0;
        for (const id of currentIds) {
          try {
            await api(`/api/collections/${encodeURIComponent(collectionId)}/items`, {
              method: 'POST',
              body: { question_id: id, role: 'example', section_id: null },
            });
          } catch {
            failed += 1;
          }
        }
        modal.close();
        writeBasket([]);
        if (failed) {
          flashAfterNavigation(`题单 ${collectionId} 已建立，其中 ${failed} 道题加入失败，请到题单编辑页补齐。`, 'error');
        } else {
          flashAfterNavigation(`已建立题单 ${collectionId}，包含 ${currentIds.length} 道题。`, 'ok');
        }
        document.dispatchEvent(new Event('meta:refresh'));
        location.hash = `#/collection/${encodeURIComponent(collectionId)}`;
      } catch (err) {
        flash(err.message);
        buildBtn.disabled = false;
        buildBtn.textContent = '一键建成新题单';
      }
    });
  }
}

// ---------------------------------------------------------------- 初始化

function init() {
  updateBasketBadge();
  document.addEventListener('basket:change', () => updateBasketBadge());
  document.addEventListener('meta:refresh', loadMeta);
  document.addEventListener('basket:open', openBasketModal);

  const basketBtn = document.getElementById('basket-btn');
  if (basketBtn) basketBtn.addEventListener('click', openBasketModal);

  window.addEventListener('hashchange', route);
  loadMeta();
  route();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
