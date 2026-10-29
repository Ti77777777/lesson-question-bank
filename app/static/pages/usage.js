/* 使用与反馈：使用对象、出题任务、使用记录与反馈，以及按班级回查近期用过的题。 */

import { api } from '../lib/api.js';
import {
  esc, badge, fmtDate, todayISO, $, $$, emptyState, errorCard, loading, num, truncate,
} from '../lib/dom.js';
import {
  AUDIENCE_KINDS, ASSIGNMENT_STATUS, ASSIGNMENT_TONE, USAGE_PURPOSES,
} from '../lib/constants.js';
import { flash, openModal, runSubmit } from '../lib/ui.js';
import { refreshMeta } from '../lib/bus.js';

export default async function mount(root, ctx) {
  let audiences = [];
  let assignments = [];
  let usages = [];
  let collections = [];
  let releases = [];

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">使用与反馈</h1>
        <p class="page-sub">生成 PDF 只形成发布物；确认发给某个班、课堂实际讲了哪些题，再分别登记。反馈用于下一次选题与讲评，不改写题目的永久属性。</p>
      </div>
      <div class="page-head-actions">
        <span class="hint">对象、日期、用途与题目版本都可回查</span>
      </div>
    </div>

    <div style="display:grid;gap:28px;">
      <section>
        <div class="spread" style="margin-bottom:10px;">
          <h2 class="section-title">使用对象管理</h2>
          <span class="hint">班级、分组或学生代号；名称可改，ID 保持稳定</span>
        </div>
        <div class="panel-grid">
          <section class="card"><div id="audience-list">${loading()}</div></section>
          <aside class="card panel-form">
            <div class="card-head"><h3>新建使用对象</h3></div>
            <form id="audience-form" class="card-body">
              <label class="field"><span>类型</span>
                <select name="kind">${Object.entries(AUDIENCE_KINDS).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select>
              </label>
              <label class="field"><span>名称<span class="req">*</span></span>
                <input type="text" name="name" placeholder="如 高一（3）班">
              </label>
              <label class="field"><span>代号</span><input type="text" name="code" placeholder="如 G1-3"></label>
              <label class="field"><span>备注</span><textarea name="note" rows="2" placeholder="如 分层 B 组，每周一次"></textarea></label>
              <button type="submit" class="btn btn-primary">保存对象</button>
            </form>
          </aside>
        </div>
      </section>

      <section>
        <div class="spread" style="margin-bottom:10px;">
          <h2 class="section-title">出题任务</h2>
          <span class="hint">计划与实际发放分开记录</span>
        </div>
        <div class="panel-grid">
          <section class="card"><div id="assignment-list">${loading()}</div></section>
          <aside class="card panel-form">
            <div class="card-head"><h3>新建出题任务</h3></div>
            <form id="assignment-form" class="card-body">
              <label class="field"><span>任务名称<span class="req">*</span></span>
                <input type="text" name="title" placeholder="如 单调性课后巩固">
              </label>
              <label class="field"><span>题单</span>
                <select name="collection_id" data-collection-select></select>
              </label>
              <label class="field"><span>使用对象</span>
                <select name="audience_id" data-audience-select></select>
              </label>
              <label class="field"><span>计划日期</span>
                <input type="date" name="plan_date" value="${todayISO()}">
              </label>
              <label class="field"><span>完成要求</span>
                <textarea name="requirements" rows="2" placeholder="如 必做 1–5 题，6、7 选做"></textarea>
              </label>
              <button type="submit" class="btn btn-primary">保存任务</button>
            </form>
          </aside>
        </div>
      </section>

      <section>
        <div class="spread" style="margin-bottom:10px;">
          <h2 class="section-title">使用记录</h2>
          <span class="hint">只有已生成的发布物才能登记实际使用</span>
        </div>
        <div class="panel-grid">
          <section class="card"><div id="usage-list">${loading()}</div></section>
          <aside class="card panel-form">
            <div class="card-head"><h3>登记一次使用</h3></div>
            <form id="usage-form" class="card-body">
              <label class="field"><span>使用对象<span class="req">*</span></span>
                <select name="audience_id" data-audience-select></select>
              </label>
              <label class="field"><span>发布物<span class="req">*</span></span>
                <select name="release_id" id="usage-release"></select>
              </label>
              <div class="inline-fields two">
                <label class="field"><span>实际使用日期<span class="req">*</span></span>
                  <input type="date" name="used_on" value="${todayISO()}">
                </label>
                <label class="field"><span>用途</span>
                  <select name="purpose">${Object.entries(USAGE_PURPOSES).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select>
                </label>
              </div>
              <div>
                <span class="field-label">实际使用的条目</span>
                <div id="usage-items" class="stack-sm"><span class="hint">选择发布物后列出该题单的条目。</span></div>
              </div>
              <label class="field"><span>备注</span><textarea name="note" rows="2" placeholder="如 只讲了例题，练习留作作业"></textarea></label>
              <button type="submit" class="btn btn-primary">登记使用</button>
            </form>
          </aside>
        </div>
      </section>

      <section>
        <div class="spread" style="margin-bottom:10px;">
          <h2 class="section-title">按班级回查近期用过的题</h2>
          <span class="hint">用于避免无意重复；有记录才显示，历史缺失显示无记录</span>
        </div>
        <section class="card">
          <div class="card-head">
            <form class="row" id="recent-form" style="gap:8px;">
              <select name="audience_id" data-audience-select style="width:200px;"></select>
              <select name="days" style="width:120px;">
                <option value="7">近 7 天</option>
                <option value="30" selected>近 30 天</option>
                <option value="90">近 90 天</option>
              </select>
              <button type="submit" class="btn btn-sm">回查</button>
            </form>
          </div>
          <div class="card-body" id="recent-result">
            <div class="hint">选择一个使用对象，查看近期用过的题目与题单。</div>
          </div>
        </section>
      </section>
    </div>
  `;

  const audienceList = $('#audience-list', root);
  const assignmentList = $('#assignment-list', root);
  const usageList = $('#usage-list', root);
  const usageItemsEl = $('#usage-items', root);

  const audienceName = (idOrRow) => {
    if (idOrRow && typeof idOrRow === 'object') {
      return idOrRow.audience_name || idOrRow.audience?.name || idOrRow.name || idOrRow.audience_id || '—';
    }
    const found = audiences.find((row) => row.id === idOrRow);
    return found?.name || idOrRow || '—';
  };
  const collectionTitle = (id) => {
    const found = collections.find((row) => row.id === id);
    return found?.title || id || '—';
  };

  // ------------------------------------------------------------ 下拉选项

  function syncSelects() {
    $$('select[data-audience-select]', root).forEach((select) => {
      const previous = select.value;
      const options = ['<option value="">选择使用对象</option>', ...audiences.map((row) => (
        `<option value="${esc(row.id)}">${esc(row.name)}（${esc(AUDIENCE_KINDS[row.kind] || row.kind || '')}）</option>`
      ))];
      select.innerHTML = options.join('');
      if (previous && audiences.some((row) => row.id === previous)) select.value = previous;
    });
    $$('select[data-collection-select]', root).forEach((select) => {
      const previous = select.value;
      select.innerHTML = ['<option value="">选择题单</option>', ...collections.map((row) => (
        `<option value="${esc(row.id)}">${esc(row.title)}</option>`
      ))].join('');
      if (previous) select.value = previous;
    });
    const releaseSelect = $('#usage-release', root);
    if (releaseSelect) {
      const previous = releaseSelect.value;
      const usable = releases.filter((row) => {
        const release = row.release || row;
        return (release.status || row.status || 'built') !== 'failed';
      });
      releaseSelect.innerHTML = ['<option value="">选择发布物</option>', ...usable.map((row) => {
        const release = row.release || row;
        const id = release.id || row.id;
        const title = release.collection_title || row.collection_title || collectionTitle(release.collection_id || row.collection_id);
        return `<option value="${esc(id)}">${esc(id)} · ${esc(title)}</option>`;
      })].join('');
      if (previous && usable.some((row) => (row.release || row).id === previous || row.id === previous)) {
        releaseSelect.value = previous;
      }
    }
  }

  // ------------------------------------------------------------ 使用对象

  function renderAudiences() {
    if (!audiences.length) {
      audienceList.innerHTML = `<div class="card-body">${emptyState('还没有使用对象', '先建班级或分组，之后出题任务与使用记录都挂在它下面。')}</div>`;
      return;
    }
    audienceList.innerHTML = `<div class="table-wrap"><table class="table">
      <thead><tr><th>编号</th><th>类型</th><th>名称</th><th>代号</th><th>备注</th></tr></thead>
      <tbody>${audiences.map((row) => `<tr>
        <td class="mono">${esc(row.id)}</td>
        <td>${esc(AUDIENCE_KINDS[row.kind] || row.kind || '—')}</td>
        <td>${esc(row.name)}</td>
        <td class="mono">${esc(row.code || '—')}</td>
        <td class="hint">${esc(row.note || '')}</td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  }

  async function loadAudiences() {
    try {
      const data = await api('/api/audiences');
      audiences = Array.isArray(data.items) ? data.items : [];
      renderAudiences();
      syncSelects();
    } catch (err) {
      audienceList.replaceChildren(errorCard(err.message, loadAudiences));
    }
  }

  $('#audience-form', root).addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const name = String(data.get('name') || '').trim();
    if (!name) {
      flash('请填写对象名称。');
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = '保存中…';
    try {
      await api('/api/audiences', {
        method: 'POST',
        body: {
          kind: data.get('kind'),
          name,
          code: String(data.get('code') || '').trim() || null,
          note: String(data.get('note') || '').trim() || null,
        },
      });
      form.reset();
      flash('使用对象已保存。', 'ok');
      await loadAudiences();
      refreshMeta();
    } catch (err) {
      flash(err.message);
    } finally {
      button.disabled = false;
      button.textContent = '保存对象';
    }
  });

  // ------------------------------------------------------------ 出题任务

  function renderAssignments() {
    if (!assignments.length) {
      assignmentList.innerHTML = `<div class="card-body">${emptyState('还没有出题任务', '任务用来记录计划：给哪个班、用哪份题单、计划哪天发。实际使用另记。')}</div>`;
      return;
    }
    assignmentList.innerHTML = `<div class="table-wrap"><table class="table">
      <thead><tr><th>编号</th><th>任务</th><th>题单</th><th>对象</th><th>计划日期</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${assignments.map((row) => {
        const status = row.status || 'planned';
        return `<tr>
          <td class="mono">${esc(row.id)}</td>
          <td>${esc(row.title || '—')}</td>
          <td>${esc(row.collection_title || collectionTitle(row.collection_id))}</td>
          <td>${esc(audienceName(row))}</td>
          <td class="num">${esc(fmtDate(row.plan_date))}</td>
          <td>${badge(ASSIGNMENT_STATUS[status] || status, ASSIGNMENT_TONE[status] || 'plain')}</td>
          <td>
            <div class="row-actions">
              ${status === 'planned' ? `<button type="button" class="btn-link" data-assign="${esc(row.id)}" data-status="distributed">标记已发放</button>` : ''}
              ${status === 'distributed' ? `<button type="button" class="btn-link" data-assign="${esc(row.id)}" data-status="closed">结束</button>` : ''}
              ${status !== 'cancelled' ? `<button type="button" class="btn-link danger" data-assign="${esc(row.id)}" data-status="cancelled">取消</button>` : ''}
            </div>
          </td>
        </tr>`;
      }).join('')}</tbody>
    </table></div>`;
  }

  async function loadAssignments() {
    try {
      const data = await api('/api/assignments');
      assignments = Array.isArray(data.items) ? data.items : [];
      renderAssignments();
    } catch (err) {
      assignmentList.replaceChildren(errorCard(err.message, loadAssignments));
    }
  }

  assignmentList.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-assign]');
    if (!btn) return;
    btn.disabled = true;
    try {
      await api(`/api/assignments/${encodeURIComponent(btn.dataset.assign)}`, {
        method: 'PATCH',
        body: { status: btn.dataset.status },
      });
      flash('任务状态已更新。', 'ok');
      await loadAssignments();
    } catch (err) {
      flash(err.message);
      btn.disabled = false;
    }
  });

  $('#assignment-form', root).addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const title = String(data.get('title') || '').trim();
    if (!title) {
      flash('请填写任务名称。');
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = '保存中…';
    try {
      await api('/api/assignments', {
        method: 'POST',
        body: {
          title,
          collection_id: data.get('collection_id') || null,
          audience_id: data.get('audience_id') || null,
          plan_date: data.get('plan_date') || null,
          requirements: String(data.get('requirements') || '').trim() || null,
        },
      });
      form.reset();
      flash('出题任务已保存。', 'ok');
      syncSelects();
      await loadAssignments();
      refreshMeta();
    } catch (err) {
      flash(err.message);
    } finally {
      button.disabled = false;
      button.textContent = '保存任务';
    }
  });

  // ------------------------------------------------------------ 使用记录

  function usageItemsText(row) {
    const list = Array.isArray(row.items) ? row.items : [];
    if (!list.length) return '<span class="hint">未记录条目（按全部处理）</span>';
    return `<span class="mono" style="font-size:11px;">${esc(list.join('、'))}</span>`;
  }

  function renderUsages() {
    if (!usages.length) {
      usageList.innerHTML = `<div class="card-body">${emptyState('还没有使用记录', '生成发布物并发给学生后，在这里登记日期、对象与本次实际使用的条目。')}</div>`;
      return;
    }
    usageList.innerHTML = `<div class="table-wrap"><table class="table">
      <thead><tr><th>编号</th><th>对象</th><th>日期</th><th>用途</th><th>发布物</th><th>实际条目</th><th>反馈</th></tr></thead>
      <tbody>${usages.map((row) => `<tr class="usage-row">
        <td class="mono">${esc(row.id)}</td>
        <td>${esc(audienceName(row))}</td>
        <td class="num">${esc(fmtDate(row.used_on))}</td>
        <td>${esc(USAGE_PURPOSES[row.purpose] || row.purpose || '—')}</td>
        <td class="mono" style="font-size:11.5px;">${esc(row.release_id || '—')}</td>
        <td>${usageItemsText(row)}</td>
        <td><button type="button" class="btn btn-sm" data-feedback="${esc(row.id)}">反馈${Number(row.feedback_count) ? ` ${esc(row.feedback_count)}` : ''}</button></td>
      </tr>
      <tr hidden data-fb-row="${esc(row.id)}"><td colspan="7" style="background:#fcfbf9;">
        <div data-fb-list="${esc(row.id)}"></div>
      </td></tr>`).join('')}</tbody>
    </table></div>`;
  }

  async function loadUsages() {
    try {
      const data = await api('/api/usages', { query: { days: 365 } });
      usages = Array.isArray(data.items) ? data.items : [];
      renderUsages();
    } catch (err) {
      usageList.replaceChildren(errorCard(err.message, loadUsages));
    }
  }

  usageList.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-feedback]');
    if (!btn) return;
    const usageId = btn.dataset.feedback;
    const row = usageList.querySelector(`[data-fb-row="${CSS.escape(usageId)}"]`);
    if (!row) return;
    if (!row.hidden) {
      row.hidden = true;
      btn.textContent = '反馈';
      return;
    }
    row.hidden = false;
    btn.textContent = '收起';
    await loadFeedback(usageId);
  });

  async function loadFeedback(usageId) {
    const container = usageList.querySelector(`[data-fb-list="${CSS.escape(usageId)}"]`);
    if (!container) return;
    container.innerHTML = loading('正在读取反馈…');
    try {
      const data = await api('/api/feedback', { query: { usage_id: usageId } });
      const list = Array.isArray(data.items) ? data.items : [];
      container.innerHTML = `<div style="padding:10px 4px;">
        ${list.length ? `<div class="fb-list">${list.map((fb) => `<div class="fb">
          ${fb.common_errors ? `<div>常见错误：${esc(fb.common_errors)}</div>` : ''}
          ${fb.actual_minutes !== null && fb.actual_minutes !== undefined ? `<div>实际用时：<span class="mono">${esc(num(fb.actual_minutes))}</span> 分钟</div>` : ''}
          ${fb.effect ? `<div>效果：${esc(fb.effect)}</div>` : ''}
          ${fb.suggestion ? `<div>下次建议：${esc(fb.suggestion)}</div>` : ''}
          <div class="meta">${esc(fmtDate(fb.created_at))}${fb.sample_size ? ` · 参与 ${esc(fb.sample_size)} 人` : ''}</div>
        </div>`).join('')}</div>` : '<div class="hint" style="margin-bottom:8px;">还没有反馈记录。</div>'}
        <div class="row-actions" style="margin-top:8px;">
          <button type="button" class="btn btn-sm" data-add-feedback="${esc(usageId)}">登记反馈</button>
        </div>
      </div>`;
    } catch (err) {
      container.innerHTML = `<div class="modal-error">${esc(err.message)}</div>`;
    }
  }

  usageList.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-add-feedback]');
    if (!btn) return;
    const usageId = btn.dataset.addFeedback;
    const modal = openModal({
      title: `登记反馈 · ${usageId}`,
      body: `<div class="modal-error" hidden></div>
        <label class="field"><span>常见错误</span>
          <textarea data-errors rows="3" placeholder="如 只检查判别式，漏掉两根为正的条件"></textarea>
        </label>
        <label class="field"><span>实际用时（分钟）</span>
          <input type="number" min="0" step="1" data-minutes placeholder="如 35">
        </label>
        <label class="field"><span>课堂效果</span>
          <textarea data-effect rows="2" placeholder="如 例题讲完多数人能独立完成跟练"></textarea>
        </label>
        <label class="field"><span>下次建议</span>
          <textarea data-suggestion rows="2" placeholder="如 先复习根的符号，再讲判别式"></textarea>
        </label>`,
      footer: `<button type="button" class="btn" data-close>取消</button>
               <button type="button" class="btn btn-primary" data-save>保存反馈</button>`,
    });
    const errEl = modal.body.querySelector('.modal-error');
    modal.foot.querySelector('[data-save]').addEventListener('click', async (event) => {
      await runSubmit(event.currentTarget, errEl, async () => {
        const val = (selector) => modal.body.querySelector(selector).value.trim();
        const errors = val('[data-errors]');
        const effect = val('[data-effect]');
        const suggestion = val('[data-suggestion]');
        const minutes = val('[data-minutes]');
        if (!errors && !effect && !suggestion && !minutes) {
          throw new Error('至少填写一项反馈内容。');
        }
        await api('/api/feedback', {
          method: 'POST',
          body: {
            usage_id: usageId,
            common_errors: errors || null,
            actual_minutes: minutes ? Number(minutes) : null,
            effect: effect || null,
            suggestion: suggestion || null,
          },
        });
        modal.close();
        flash('反馈已登记。', 'ok');
        await loadFeedback(usageId);
      });
    });
  });

  // 发布物切换 → 读取题单条目
  $('#usage-release', root).addEventListener('change', async (event) => {
    const releaseId = event.target.value;
    usageItemsEl.innerHTML = '<span class="hint">正在读取题单条目…</span>';
    if (!releaseId) {
      usageItemsEl.innerHTML = '<span class="hint">选择发布物后列出该题单的条目。</span>';
      return;
    }
    try {
      let release = releases.map((row) => row.release || row).find((row) => row.id === releaseId);
      let collectionId = release?.collection_id;
      if (!collectionId) {
        const detail = await api(`/api/releases/${encodeURIComponent(releaseId)}`);
        release = detail.release || detail;
        collectionId = release.collection_id || detail.collection_id;
      }
      if (!collectionId) throw new Error('该发布物没有关联题单。');
      const data = await api(`/api/collections/${encodeURIComponent(collectionId)}`);
      const list = Array.isArray(data.items) ? data.items : [];
      if (!list.length) {
        usageItemsEl.innerHTML = '<span class="hint">该题单没有条目。</span>';
        return;
      }
      usageItemsEl.innerHTML = list.map((item) => `<label class="check" style="display:flex;">
        <input type="checkbox" data-usage-item value="${esc(item.id)}" checked>
        <span>${esc(item.question?.id || item.question_id || '')} · ${esc(truncate(item.question?.short_name || item.question?.stem_preview || '', 40))}</span>
      </label>`).join('');
    } catch (err) {
      usageItemsEl.innerHTML = `<div class="modal-error">${esc(err.message)}</div>`;
    }
  });

  $('#usage-form', root).addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (!data.get('audience_id')) {
      flash('请选择使用对象。');
      return;
    }
    if (!data.get('release_id')) {
      flash('请选择发布物。已生成 PDF 后才能登记实际使用。');
      return;
    }
    if (!data.get('used_on')) {
      flash('请填写实际使用日期。');
      return;
    }
    const items = $$('[data-usage-item]:checked', usageItemsEl).map((input) => input.value);
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = '登记中…';
    try {
      await api('/api/usages', {
        method: 'POST',
        body: {
          audience_id: data.get('audience_id'),
          release_id: data.get('release_id'),
          used_on: data.get('used_on'),
          purpose: data.get('purpose'),
          items,
          note: String(data.get('note') || '').trim() || null,
        },
      });
      flash('使用记录已登记。', 'ok');
      form.querySelector('[name="note"]').value = '';
      await loadUsages();
      refreshMeta();
    } catch (err) {
      flash(err.message);
    } finally {
      button.disabled = false;
      button.textContent = '登记使用';
    }
  });

  // ------------------------------------------------------------ 近期回查

  $('#recent-form', root).addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const audienceId = data.get('audience_id');
    const days = data.get('days') || 30;
    const result = $('#recent-result', root);
    if (!audienceId) {
      flash('请选择要回查的使用对象。');
      return;
    }
    result.innerHTML = loading('正在回查…');
    try {
      const payload = await api(`/api/audiences/${encodeURIComponent(audienceId)}/recent`, {
        query: { days },
      });
      renderRecent(result, payload, days);
    } catch (err) {
      flash(err.message);
      result.innerHTML = `<div class="modal-error">${esc(err.message)}</div>`;
    }
  });

  function renderRecent(container, data, days) {
    const questions = Array.isArray(data.questions) ? data.questions : (Array.isArray(data.items) ? data.items : []);
    const usageRows = Array.isArray(data.usages) ? data.usages : [];
    let recentCollections = Array.isArray(data.collections) ? data.collections : [];

    // 后端 /recent 目前返回按题目去重的 items，题单从条目的 collection_title 归并。
    if (!recentCollections.length && questions.length) {
      const grouped = new Map();
      questions.forEach((row) => {
        const title = row.collection_title || row.collection?.title;
        const key = row.collection_id || title;
        if (!key) return;
        const existing = grouped.get(key);
        if (existing) {
          existing.use_count += 1;
          if (row.used_on && (!existing.last_used || row.used_on > existing.last_used)) {
            existing.last_used = row.used_on;
          }
        } else {
          grouped.set(key, {
            collection: { id: row.collection_id || '', title: title || '未标题单' },
            last_used: row.used_on,
            use_count: 1,
          });
        }
      });
      recentCollections = [...grouped.values()];
    }

    if (!questions.length && !recentCollections.length && !usageRows.length) {
      container.innerHTML = `<div class="hint">近 ${esc(days)} 天没有记录。历史缺失时显示无记录，不代表学生从未做过这些题。</div>`;
      return;
    }

    container.innerHTML = `<div class="recent-grid">
      <div>
        <div class="mini-title"><span>近期用过的题</span><span class="hint" style="font-size:10.5px;">${questions.length} 道</span></div>
        ${questions.length ? `<ul class="recent-list">${questions.map((row) => {
          const q = row.question || row;
          const qid = q.id || row.question_id;
          const used = row.last_used || row.used_on || row.used_at;
          const count = row.use_count || row.count;
          return `<li>
            ${qid ? `<a href="#/question/${esc(qid)}" class="mono" style="font-size:11.5px;">${esc(qid)}</a>` : ''}
            ${esc(q.short_name || truncate(q.stem_preview || row.stem_preview, 48) || '')}
            <div class="meta">${used ? `最近 ${esc(fmtDate(used))}` : '日期未记录'}${count ? ` · 使用 ${esc(count)} 次` : ''}${row.collection_title ? ` · ${esc(row.collection_title)}` : ''}${row.purpose ? ` · ${esc(USAGE_PURPOSES[row.purpose] || row.purpose)}` : ''}</div>
          </li>`;
        }).join('')}</ul>` : `<div class="check-empty">近 ${esc(days)} 天没有题目使用记录。</div>`}
      </div>
      <div>
        <div class="mini-title"><span>近期用过的题单</span><span class="hint" style="font-size:10.5px;">${recentCollections.length} 份</span></div>
        ${recentCollections.length ? `<ul class="recent-list">${recentCollections.map((row) => {
          const c = row.collection || row;
          const cid = c.id || row.collection_id
            || collections.find((entry) => entry.title === c.title)?.id;
          const used = row.last_used || row.used_on;
          return `<li>
            ${cid ? `<a href="#/collection/${esc(cid)}">${esc(c.title || cid)}</a>` : esc(c.title || '')}
            <div class="meta">${used ? `最近 ${esc(fmtDate(used))}` : ''}${row.use_count ? ` · ${esc(row.use_count)} 题` : ''}</div>
          </li>`;
        }).join('')}</ul>` : `<div class="check-empty">近 ${esc(days)} 天没有题单使用记录。</div>`}
      </div>
    </div>`;
  }

  // ------------------------------------------------------------ 初始化

  async function loadBase() {
    const [audienceRes, collectionRes, releaseRes] = await Promise.allSettled([
      api('/api/audiences'),
      api('/api/collections'),
      api('/api/releases'),
    ]);
    if (!ctx.current()) return;

    if (audienceRes.status === 'fulfilled') {
      audiences = Array.isArray(audienceRes.value.items) ? audienceRes.value.items : [];
      renderAudiences();
    } else {
      audienceList.replaceChildren(errorCard(audienceRes.reason.message, loadBase));
    }
    if (collectionRes.status === 'fulfilled') {
      collections = Array.isArray(collectionRes.value.items) ? collectionRes.value.items : [];
    } else {
      flash(collectionRes.reason.message);
    }
    if (releaseRes.status === 'fulfilled') {
      releases = Array.isArray(releaseRes.value.items) ? releaseRes.value.items : [];
    } else {
      flash(releaseRes.reason.message);
    }
    syncSelects();
    await Promise.all([loadAssignments(), loadUsages()]);
  }

  await loadBase();
}
