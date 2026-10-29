/* 题单列表：查看题量、课时与发布次数，新建题单后进入编辑。 */

import { api } from '../lib/api.js';
import { esc, badge, fmtDate, $, emptyState, errorCard, loading, num } from '../lib/dom.js';
import { COLLECTION_PURPOSES } from '../lib/constants.js';
import { flash, flashAfterNavigation, openModal, runSubmit } from '../lib/ui.js';
import { refreshMeta } from '../lib/bus.js';

export default async function mount(root, ctx) {
  let items = [];
  let total = 0;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">题单</h1>
        <p class="page-sub">同一道题可以在不同题单里承担不同角色；题单只调整顺序、教学角色、时间与留白，不改公共题面。</p>
      </div>
      <div class="page-head-actions">
        <button type="button" class="btn btn-primary" id="new-collection">新建题单</button>
      </div>
    </div>
    <div class="results-head" style="margin-top:0;">
      <span class="total" id="collection-total"></span>
    </div>
    <div class="collection-list" id="collection-list">${loading()}</div>
  `;

  const listEl = $('#collection-list', root);
  const totalEl = $('#collection-total', root);

  function render() {
    if (!items.length) {
      listEl.innerHTML = emptyState('还没有题单', '新建一份题单，从检索结果加入题目，再用右侧「组题检查」核对时间与覆盖。');
      return;
    }
    listEl.innerHTML = items.map((row) => {
      const count = row.item_count ?? row.items_count ?? row.item_total
        ?? (Array.isArray(row.items) ? row.items.length : '—');
      const releaseCount = row.release_count ?? row.releases_count
        ?? (Array.isArray(row.releases) ? row.releases.length : '—');
      const purpose = COLLECTION_PURPOSES[row.purpose] || row.purpose || '题单';
      const duration = row.duration_min ?? row.duration ?? '—';
      return `<article class="collection-row">
        <div>
          <a class="collection-name" href="#/collection/${esc(row.id)}">${esc(row.title || '未命名题单')}</a>
          <div class="collection-meta">
            ${badge(purpose, 'plain')}
            <span>课时 <b class="mono">${esc(num(duration))}</b> 分钟</span>
            ${row.level ? `<span>${esc(row.level)}</span>` : ''}
            ${row.topic ? `<span>${esc(row.topic)}</span>` : ''}
            ${row.status && row.status !== 'draft' ? badge(row.status === 'ready' ? '已就绪' : '已归档', row.status === 'ready' ? 'pass' : 'na') : ''}
          </div>
        </div>
        <div class="collection-stat"><span class="k">条目</span><span class="v">${esc(num(count))}</span></div>
        <div class="collection-stat"><span class="k">发布</span><span class="v">${esc(num(releaseCount))}</span></div>
        <div class="updated muted" style="font-size:12px;">更新 ${esc(fmtDate(row.updated_at))}</div>
        <div style="text-align:right;"><a class="btn btn-sm" href="#/collection/${esc(row.id)}">编辑题单</a></div>
      </article>`;
    }).join('');
  }

  async function load() {
    listEl.innerHTML = loading();
    try {
      const data = await api('/api/collections');
      items = Array.isArray(data.items) ? data.items : [];
      total = Number(data.total ?? items.length);
      totalEl.innerHTML = `共 <b>${esc(total)}</b> 份题单`;
      render();
    } catch (err) {
      if (!ctx.current()) return;
      flash(err.message);
      listEl.replaceChildren(errorCard(err.message, load));
      totalEl.textContent = '';
    }
  }

  $('#new-collection', root).addEventListener('click', () => {
    const modal = openModal({
      title: '新建题单',
      body: `<div class="modal-error" hidden></div>
        <label class="field"><span>题单名称<span class="req">*</span></span>
          <input type="text" data-title placeholder="如 函数的单调性（习题课）">
        </label>
        <div class="inline-fields two">
          <label class="field"><span>用途</span>
            <select data-purpose>${Object.entries(COLLECTION_PURPOSES).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select>
          </label>
          <label class="field"><span>课时长度（分钟）</span>
            <input type="number" min="1" step="5" data-duration value="90">
          </label>
        </div>
        <label class="field"><span>主题</span>
          <input type="text" data-topic placeholder="如 函数性质 · 单调性">
        </label>
        <label class="field"><span>备注</span>
          <textarea rows="2" data-note placeholder="如 面向高一（3）班，需保留概念回顾"></textarea>
        </label>`,
      footer: `<button type="button" class="btn" data-close>取消</button>
               <button type="button" class="btn btn-primary" data-create>建立题单</button>`,
    });

    const errEl = modal.body.querySelector('.modal-error');
    modal.foot.querySelector('[data-create]').addEventListener('click', async (event) => {
      await runSubmit(event.currentTarget, errEl, async () => {
        const title = modal.body.querySelector('[data-title]').value.trim();
        if (!title) throw new Error('请填写题单名称。');
        const created = await api('/api/collections', {
          method: 'POST',
          body: {
            title,
            purpose: modal.body.querySelector('[data-purpose]').value,
            duration_min: Number(modal.body.querySelector('[data-duration]').value) || null,
            topic: modal.body.querySelector('[data-topic]').value.trim() || null,
            note: modal.body.querySelector('[data-note]').value.trim() || null,
          },
        });
        const id = created?.collection?.id || created?.id;
        modal.close();
        refreshMeta();
        if (id) {
          flashAfterNavigation(`已建立题单 ${id}。`, 'ok');
          location.hash = `#/collection/${encodeURIComponent(id)}`;
        } else {
          flash('题单已建立。', 'ok');
          await load();
        }
      });
    });
  });

  await load();
}
