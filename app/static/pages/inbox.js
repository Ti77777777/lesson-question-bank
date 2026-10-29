/* 收题箱：登记原始资料、查看处理进度、整理为题目、标记不采用。 */

import { api, fileUrl } from '../lib/api.js';
import {
  esc, badge, fmtDateTime, $, emptyState, errorCard, loading,
} from '../lib/dom.js';
import { INBOX_STATUS, INBOX_TONE } from '../lib/constants.js';
import { flash } from '../lib/ui.js';
import { refreshMeta } from '../lib/bus.js';
import { upgradeMath } from '../lib/math.js';

const FILTERS = [
  ['', '全部'],
  ['pending', '待提取'],
  ['extracted', '已提取'],
  ['organized', '已整理'],
  ['rejected', '不采用'],
];

export default async function mount(root, ctx) {
  let status = '';
  let items = [];
  let total = 0;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">收题箱</h1>
        <p class="page-sub">先存下来，再慢慢整理。原始文字、文件名与来源线索按原样保留；来源不清楚时明确记为「待确认」。</p>
      </div>
      <div class="page-head-actions">
        <span class="hint" id="inbox-total"></span>
      </div>
    </div>

    <div class="inbox-grid">
      <section class="card">
        <div class="card-head">
          <h2>收题条目</h2>
          <div class="tabs tabs-sm status-tabs" id="status-tabs">
            ${FILTERS.map(([value, label]) => `<button type="button" data-status="${value}" class="${value === '' ? 'active' : ''}">${label}</button>`).join('')}
          </div>
        </div>
        <div id="inbox-list">${loading()}</div>
      </section>

      <aside class="card inbox-form">
        <div class="card-head">
          <h2>登记一条资料</h2>
          <span class="hint">此时不必补全题目</span>
        </div>
        <form id="inbox-form" novalidate>
          <div class="card-body">
            <label class="field">
              <span>原始文字</span>
              <textarea name="raw_text" rows="7" placeholder="把题干、答案或截图里的文字原样贴进来，公式可用 $...$ 写 LaTeX。"></textarea>
              <span class="field-hint">识别不清的地方照原样保留；只有文件时，至少填下面的文件名。</span>
            </label>
            <label class="field">
              <span>文件名</span>
              <input type="text" name="file_name" placeholder="如 2024学年高一期末.pdf">
            </label>
            <label class="field">
              <span>来源线索</span>
              <input type="text" name="source_hint" placeholder="如 2024 学年期末 · 第 12 题">
              <span class="field-hint">不清楚时留空，系统会记为「待确认」。</span>
            </label>
            <label class="field">
              <span>备注</span>
              <textarea name="note" rows="2" placeholder="如截图不清、需问备课组、图形待补"></textarea>
            </label>
            <label class="field">
              <span>录入人</span>
              <input type="text" name="operator" value="教师">
            </label>
          </div>
          <div class="card-foot">
            <button type="submit" class="btn btn-primary" id="inbox-submit">登记到收题箱</button>
          </div>
        </form>
      </aside>
    </div>
  `;

  const listEl = $('#inbox-list', root);
  const totalEl = $('#inbox-total', root);
  const form = $('#inbox-form', root);
  const submitBtn = $('#inbox-submit', root);

  function setTotal(value) {
    total = value;
    totalEl.textContent = value ? `共 ${value} 条` : '';
  }

  function renderItems() {
    if (!items.length) {
      const title = status ? `没有「${INBOX_STATUS[status]}」的条目` : '收题箱是空的';
      const hint = status
        ? '换个状态看看，或在右侧登记新的原始资料。'
        : '先在右侧贴入一段原文或登记一个文件名，不必等到题目整理完整。';
      listEl.innerHTML = emptyState(title, hint);
      return;
    }

    listEl.innerHTML = items.map((item) => {
      const raw = String(item.raw_text || '');
      const canExpand = raw.length > 150 || raw.split('\n').length > 4;
      const link = item.file_rel_path ? fileUrl(item.file_rel_path) : '';
      const isOrganized = item.status === 'organized';
      const questionId = item.question_id || item.question?.id || '';
      return `<article class="inbox-item" data-id="${esc(item.id)}">
        <div class="inbox-item-top">
          <span class="inbox-id">${esc(item.id)}</span>
          ${badge(INBOX_STATUS[item.status] || item.status, INBOX_TONE[item.status] || 'neutral')}
          ${item.fail_reason && item.status === 'rejected' ? badge('已记录原因', 'ochre') : ''}
          <span class="when">${esc(fmtDateTime(item.created_at))}</span>
        </div>

        <div class="inbox-raw ${canExpand ? 'clamped' : ''}" data-rich>${esc(raw || '（没有原始文字，仅登记了文件）')}</div>

        <div class="inbox-meta">
          <span><span class="k">来源线索</span>${esc(item.source_hint || '待确认')}</span>
          <span><span class="k">文件</span>${esc(item.file_name || '未登记')}</span>
          ${item.operator ? `<span><span class="k">录入</span>${esc(item.operator)}</span>` : ''}
          ${item.updated_at ? `<span><span class="k">更新</span>${esc(fmtDateTime(item.updated_at))}</span>` : ''}
        </div>

        ${item.note ? `<div class="inbox-note">${esc(item.note)}</div>` : ''}
        ${item.status === 'rejected' && item.fail_reason ? `<div class="inbox-note rejected">不采用原因：${esc(item.fail_reason)}</div>` : ''}

        <div class="row-actions inbox-actions">
          ${isOrganized && questionId
            ? `<a class="btn btn-sm" href="#/question/${esc(questionId)}">查看题目 ${esc(questionId)}</a>`
            : item.status === 'rejected'
              ? ''
              : `<button type="button" class="btn btn-sm btn-primary" data-act="organize">整理为题目</button>`}
          ${link ? `<a class="btn btn-sm" href="${esc(link)}" target="_blank" rel="noopener">打开原文件</a>` : ''}
          ${canExpand ? '<button type="button" class="btn-link" data-act="expand">展开全文</button>' : ''}
          ${item.status !== 'rejected'
            ? '<button type="button" class="btn-link danger" data-act="reject">不采用</button>'
            : '<button type="button" class="btn-link" data-act="restore">撤销不采用</button>'}
        </div>

        <div class="reject-form" hidden>
          <label class="field">
            <span>不采用原因<span class="req">*</span></span>
            <textarea data-reason rows="2" placeholder="如重复题、条件有误、超出本讲范围"></textarea>
          </label>
          <div class="row-actions">
            <button type="button" class="btn btn-sm btn-danger" data-act="confirm-reject">确认不采用</button>
            <button type="button" class="btn btn-sm btn-quiet" data-act="cancel-reject">取消</button>
          </div>
        </div>
      </article>`;
    }).join('');

    upgradeMath(listEl);
  }

  async function loadList() {
    listEl.innerHTML = loading();
    try {
      const data = await api('/api/inbox', { query: { status: status || undefined, limit: 200 } });
      items = Array.isArray(data.items) ? data.items : [];
      setTotal(Number(data.total ?? items.length));
      renderItems();
    } catch (err) {
      if (!ctx.current()) return;
      listEl.replaceChildren(errorCard(err.message, loadList));
    }
  }

  function patchItem(id, body) {
    return api(`/api/inbox/${encodeURIComponent(id)}`, { method: 'PATCH', body });
  }

  listEl.addEventListener('click', async (event) => {
    const action = event.target.closest('[data-act]')?.dataset.act;
    if (!action) return;
    const article = event.target.closest('.inbox-item');
    if (!article) return;
    const id = article.dataset.id;
    const item = items.find((row) => row.id === id);

    if (action === 'expand') {
      const rawEl = article.querySelector('.inbox-raw');
      const expanded = rawEl.classList.toggle('expanded');
      rawEl.classList.toggle('clamped', !expanded);
      const btn = article.querySelector('[data-act="expand"]');
      if (btn) btn.textContent = expanded ? '收起' : '展开全文';
      return;
    }

    if (action === 'organize') {
      location.hash = `#/question/new?inbox=${encodeURIComponent(id)}`;
      return;
    }

    if (action === 'reject') {
      const box = article.querySelector('.reject-form');
      box.hidden = false;
      box.querySelector('[data-reason]').focus();
      return;
    }

    if (action === 'cancel-reject') {
      article.querySelector('.reject-form').hidden = true;
      return;
    }

    if (action === 'confirm-reject') {
      const box = article.querySelector('.reject-form');
      const reason = box.querySelector('[data-reason]').value.trim();
      if (!reason) {
        flash('请填写不采用的原因。');
        box.querySelector('[data-reason]').focus();
        return;
      }
      const btn = article.querySelector('[data-act="confirm-reject"]');
      btn.disabled = true;
      btn.textContent = '提交中…';
      try {
        await patchItem(id, { status: 'rejected', fail_reason: reason });
        flash(`${id} 已标记为不采用。`, 'ok');
        await loadList();
        refreshMeta();
      } catch (err) {
        flash(err.message);
        btn.disabled = false;
        btn.textContent = '确认不采用';
      }
      return;
    }

    if (action === 'restore') {
      const btn = article.querySelector('[data-act="restore"]');
      btn.disabled = true;
      try {
        await patchItem(id, { status: 'pending', fail_reason: '' });
        flash(`${id} 已恢复为待提取。`, 'ok');
        await loadList();
        refreshMeta();
      } catch (err) {
        flash(err.message);
        btn.disabled = false;
      }
    }
  });

  $('#status-tabs', root).addEventListener('click', (event) => {
    const btn = event.target.closest('[data-status]');
    if (!btn) return;
    status = btn.dataset.status;
    root.querySelectorAll('#status-tabs button').forEach((node) => {
      node.classList.toggle('active', node === btn);
    });
    loadList();
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const rawText = String(data.get('raw_text') || '').trim();
    const fileName = String(data.get('file_name') || '').trim();
    const sourceHint = String(data.get('source_hint') || '').trim();
    if (!rawText && !fileName) {
      flash('请先贴入原始文字，或至少登记文件名；来源线索可稍后补。');
      form.querySelector('[name="raw_text"]').focus();
      return;
    }
    const payload = {
      raw_text: rawText || null,
      file_name: fileName || null,
      source_hint: sourceHint || null,
      note: String(data.get('note') || '').trim() || null,
      operator: String(data.get('operator') || '').trim() || null,
    };

    submitBtn.disabled = true;
    submitBtn.textContent = '登记中…';
    try {
      const created = await api('/api/inbox', { method: 'POST', body: payload });
      flash(`已登记 ${created?.id || '这条资料'}，可以在左侧继续整理。`, 'ok');
      form.reset();
      form.querySelector('[name="operator"]').value = payload.operator || '教师';
      form.querySelector('[name="raw_text"]').focus();
      await loadList();
      refreshMeta();
    } catch (err) {
      flash(err.message);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = '登记到收题箱';
    }
  });

  await loadList();
}
