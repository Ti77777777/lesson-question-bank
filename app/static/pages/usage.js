/* 使用与反馈：班级、小组或学生、练习安排、使用记录与反馈，以及查询已用题目。 */

import { api } from '../lib/api.js';
import {
  esc, badge, fmtDate, todayISO, $, $$, emptyState, errorCard, loading, num, truncate,
} from '../lib/dom.js';
import {
  AUDIENCE_KINDS, ASSIGNMENT_STATUS, ASSIGNMENT_TONE, USAGE_PURPOSES,
} from '../lib/constants.js';
import { flash, openModal, runSubmit } from '../lib/ui.js';
import { refreshMeta } from '../lib/bus.js';
import { mountRoster } from '../lib/roster.js';
import { usageOperations, latestSelection, snapshotUsageItems } from '../lib/usage-state.js';

export default async function mount(root, ctx) {
  let audiences = [];
  let assignments = [];
  let usages = [];
  let collections = [];
  let releases = [];
  let rosterPanel, selectedTeacher="", teacherContext, recentRun=0;
  const usageReads = latestSelection(), usageOps = usageOperations();
  let usageReadyFor = '', usageSaving = false;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">使用与反馈</h1>
        <p class="page-sub">管理班级、小组与学生，记录练习安排、讲义使用情况和课堂反馈。</p>
      </div>
    </div>

    <div style="display:grid;gap:28px;">
      <section>
        <div class="spread" style="margin-bottom:10px;">
          <h2 class="section-title">班级、小组与学生</h2>
          <span class="hint">登记授课班级、学习小组或学生代号</span>
        </div>
        <div class="panel-grid">
          <section class="card"><div id="audience-list">${loading()}</div></section>
          <aside class="card panel-form">
            <div class="card-head"><h3 id="audience-form-title">添加班级</h3></div>
            <form id="audience-form" class="card-body">
              <label class="field"><span>添加类型</span>
                <select name="kind">${Object.entries(AUDIENCE_KINDS).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select>
              </label>
              <label class="field"><span data-audience-name-label>班级名称<span class="req">*</span></span>
                <input type="text" name="name" placeholder="如 高一（3）班">
              </label>
              <label class="field"><span>负责教师</span><select id="audience-teacher"><option value="">暂不分配</option></select></label>
              <label class="field"><span>代号</span><input type="text" name="code" placeholder="如 G1-3"></label>
              <label class="field"><span>备注</span><textarea name="note" rows="2" placeholder="如 分层 B 组，每周一次"></textarea></label>
              <button type="submit" class="btn btn-primary">添加班级</button>
            </form>
          </aside>
        </div>
      </section>

      <section>
        <div class="spread" style="margin-bottom:10px;">
          <h2 class="section-title">练习安排</h2>
          <span class="hint">记录发给谁、用哪份题单以及计划日期</span>
        </div>
        <div class="panel-grid">
          <section class="card"><div id="assignment-list">${loading()}</div></section>
          <aside class="card panel-form">
            <div class="card-head"><h3>新建练习安排</h3></div>
            <form id="assignment-form" class="card-body">
              <label class="field"><span>练习名称<span class="req">*</span></span>
                <input type="text" name="title" placeholder="如 单调性课后巩固">
              </label>
              <label class="field"><span>题单</span>
                <select name="collection_id" data-collection-select></select>
              </label>
              <label class="field"><span>班级、小组或学生</span>
                <select name="audience_id" data-audience-select></select>
              </label>
              <label class="field"><span>计划日期</span>
                <input type="date" name="plan_date" value="${todayISO()}">
              </label>
              <label class="field"><span>完成要求</span>
                <textarea name="requirements" rows="2" placeholder="如 必做 1–5 题，6、7 选做"></textarea>
              </label>
              <button type="submit" class="btn btn-primary">保存安排</button>
            </form>
          </aside>
        </div>
      </section>

      <section>
        <div class="spread" style="margin-bottom:10px;">
          <h2 class="section-title">讲义使用与反馈</h2>
          <span class="hint">讲义导出后，可登记使用情况并补充课堂反馈；使用记录按班级、小组或学生分别统计，不会互相继承</span>
        </div>
        <div class="panel-grid">
          <section class="card"><div id="usage-list">${loading()}</div></section>
          <aside class="card panel-form">
            <div class="card-head"><h3>登记讲义使用</h3></div>
            <form id="usage-form" class="card-body">
              <label class="field"><span>班级、小组或学生<span class="req">*</span></span>
                <select name="audience_id" data-audience-select></select>
              </label>
              <label class="field"><span>已生成讲义<span class="req">*</span></span>
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
                <span class="field-label">实际使用的题目</span>
                <div id="usage-items" class="stack-sm"><span class="hint">选择已生成讲义后列出导出时的题目。</span></div>
              </div>
              <label class="field"><span>备注</span><textarea name="note" rows="2" placeholder="如 只讲了例题，练习留作作业"></textarea></label>
              <button type="submit" class="btn btn-primary" disabled>登记使用</button>
            </form>
          </aside>
        </div>
      </section>

      <section>
        <div class="spread" style="margin-bottom:10px;">
          <h2 class="section-title">查询已用题目</h2>
          <span class="hint">按班级、小组或学生查看近期使用记录</span>
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
              <button type="submit" class="btn btn-sm">查询</button>
            </form>
          </div>
          <div class="card-body" id="recent-result">
            <div class="hint">选择一个班级、小组或学生，查看近期用过的题目与题单。</div>
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
      const options = ['<option value="">选择班级、小组或学生</option>', ...audiences.map((row) => (
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
      releaseSelect.innerHTML = ['<option value="">选择已生成讲义</option>', ...usable.map((row) => {
        const release = row.release || row;
        const id = release.id || row.id;
        const title = release.collection_title || row.collection_title || collectionTitle(release.collection_id || row.collection_id);
        return `<option value="${esc(id)}">${esc(id)}，${esc(title)}</option>`;
      })].join('');
      if (previous && usable.some((row) => (row.release || row).id === previous || row.id === previous)) {
        releaseSelect.value = previous;
      }
    }
  }

  // ------------------------------------------------------------ 班级、小组或学生

  function updateAudienceForm() {
    const form = $('#audience-form', root), kind = form.elements.kind.value;
    const fields = {
      class: ['添加班级', '班级名称', '如 高一(3)班'],
      group: ['添加小组', '小组名称', '如 函数专题小组'],
      student: ['添加学生', '学生姓名', '如 张同学'],
    };
    const [title, label, placeholder] = fields[kind] || fields.class;
    $('#audience-form-title', root).textContent = title;
    $('[data-audience-name-label]', form).innerHTML = `${label}<span class="req">*</span>`;
    // 只在输入框为空时更新示例文案，已填写的内容不因切换类型而丢失。
    if (!form.elements.name.value) form.elements.name.placeholder = placeholder;
    form.querySelector('button[type="submit"]').textContent = title;
  }
  $('#audience-form select[name="kind"]', root).addEventListener('change', updateAudienceForm);
  updateAudienceForm();

  function renderAudiences() { rosterPanel.setData(audiences); }

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
      flash('请填写班级名称、小组名称或学生姓名。');
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
          teacher_ids: $('#audience-teacher',root).value ? [$('#audience-teacher',root).value] : [],
          name,
          code: String(data.get('code') || '').trim() || null,
          note: String(data.get('note') || '').trim() || null,
        },
      });
      form.reset();
      $('#audience-teacher',root).value=selectedTeacher==='_unassigned'?'':selectedTeacher;
      flash('已添加。', 'ok');
      await loadAudiences();
      refreshMeta();
    } catch (err) {
      flash(err.message);
    } finally {
      button.disabled = false;
      updateAudienceForm();
    }
  });

  // ------------------------------------------------------------ 练习安排

  function renderAssignments() {
    const shownAssignments=assignments.filter(row=>!selectedTeacher||audiences.some(a=>a.id===row.audience_id));
    if (!shownAssignments.length) {
      assignmentList.innerHTML = `<div class="card-body">${emptyState('还没有练习安排', '选择题单、班级或学生，记录计划发放日期。')}</div>`;
      return;
    }
    assignmentList.innerHTML = `<div class="table-wrap"><table class="table">
      <thead><tr><th>编号</th><th>练习</th><th>题单</th><th>班级、小组或学生</th><th>计划日期</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${shownAssignments.map((row) => {
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
      flash('练习安排已更新。', 'ok');
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
      flash('请填写练习名称。');
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
      flash('练习安排已保存。', 'ok');
      syncSelects();
      await loadAssignments();
      refreshMeta();
    } catch (err) {
      flash(err.message);
    } finally {
      button.disabled = false;
      button.textContent = '保存安排';
    }
  });

  // ------------------------------------------------------------ 使用记录

  function usageItemsText(row) {
    if (row.items === null || row.items === undefined) return '<span class="hint">整份讲义</span>';
    const list = Array.isArray(row.items) ? row.items : [];
    if (!list.length) return '<span class="hint">未登记题目</span>';
    return `<span class="mono" style="font-size:11px;">${esc(list.join('、'))}</span>`;
  }

  function renderUsages() {
    const shownUsages=usages.filter(row=>!selectedTeacher||audiences.some(a=>a.id===row.audience_id));
    if (!shownUsages.length) {
      usageList.innerHTML = `<div class="card-body">${emptyState('还没有讲义使用记录', '讲义导出后，在这里登记发给谁、使用日期和实际用到的题目。')}</div>`;
      return;
    }
    usageList.innerHTML = `<div class="table-wrap"><table class="table">
      <thead><tr><th>编号</th><th>班级、小组或学生</th><th>日期</th><th>用途</th><th>已生成讲义</th><th>实际题目</th><th>反馈</th></tr></thead>
      <tbody>${shownUsages.map((row) => `<tr class="usage-row">
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
          <div class="meta">${esc(fmtDate(fb.created_at))}${fb.sample_size ? `，参与 ${esc(fb.sample_size)} 人` : ''}</div>
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
      title: `登记反馈：${usageId}`,
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

  // Read the immutable exported items, not the editable collection.
  function syncUsageSubmit() {
    if (!ctx.current()) return;
    $('#usage-form button[type="submit"]', root).disabled = usageSaving
      || !usageReadyFor || usageReadyFor !== $('#usage-release', root).value;
  }
  $('#usage-release', root).addEventListener('change', async (event) => {
    const releaseId = event.target.value;
    const currentRead = usageReads.start();
    usageReadyFor = '';
    syncUsageSubmit();
    usageItemsEl.innerHTML = '<span class="hint">正在读取题目…</span>';
    if (!releaseId) {
      usageItemsEl.innerHTML = '<span class="hint">选择已生成讲义后列出导出时的题目。</span>';
      return;
    }
    try {
      const detail = await api(`/api/releases/${encodeURIComponent(releaseId)}`);
      if (!ctx.current() || !currentRead()) return;
      const release = detail.release || detail;
      if (release.status === 'failed') throw new Error('编译失败的讲义不能登记使用。');
      const list = snapshotUsageItems(release.snapshot);
      if (!list.length) {
        usageItemsEl.innerHTML = '<span class="hint">这份讲义没有题目，无法登记使用。</span>';
        return;
      }
      usageItemsEl.innerHTML = list.map((item) => `<label class="check" style="display:flex;">
        <input type="checkbox" data-usage-item value="${esc(item.id)}" checked>
        <span>${esc(item.question_id || '')}，${esc(truncate(item.label || '', 40))}</span>
      </label>`).join('');
      usageReadyFor = releaseId;
      syncUsageSubmit();
    } catch (err) {
      if (!ctx.current() || !currentRead()) return;
      usageItemsEl.innerHTML = `<div class="modal-error">${esc(err.message)}</div>`;
    }
  });

  $('#usage-form', root).addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (usageSaving) return;
    if (!data.get('audience_id')) {
      flash('请选择班级、小组或学生。');
      return;
    }
    if (!data.get('release_id')) {
      flash('请选择已生成讲义。已生成 PDF 后才能登记实际使用。');
      return;
    }
    if (!data.get('used_on')) {
      flash('请填写实际使用日期。');
      return;
    }
    const items = $$('[data-usage-item]:checked', usageItemsEl).map((input) => input.value);
    if (usageReadyFor !== data.get('release_id')) {
      flash('请等待这份讲义的题目读取完成，失败时可重新选择。');
      return;
    }
    if (!items.length) {
      flash('至少勾选一道这次实际使用的题目。');
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    usageSaving = true;
    button.disabled = true;
    button.textContent = '登记中…';
    try {
      const payload = {
          audience_id: data.get('audience_id'),
          release_id: data.get('release_id'),
          used_on: data.get('used_on'),
          purpose: data.get('purpose'),
          items,
          note: String(data.get('note') || '').trim() || null,
      };
      const out = await api('/api/usages', {method: 'POST', body: {
        ...payload, operation_id: usageOps.forPayload(payload),
      }});
      usageOps.clear();
      if (!ctx.current()) return;
      flash(out.replayed ? '这次登记之前已保存过，没有重复添加。' : '使用记录已登记。', 'ok');
      form.querySelector('[name="audience_id"]').value = '';
      form.querySelector('[name="note"]').value = '';
      await loadUsages();
      refreshMeta();
    } catch (err) {
      if (!ctx.current()) return;
      flash(err.message);
    } finally {
      usageSaving = false;
      syncUsageSubmit();
      if (ctx.current()) button.textContent = '登记使用';
    }
  });

  // ------------------------------------------------------------ 近期回查

  $('#recent-form', root).addEventListener('change', () => {
    recentRun++;
    $('#recent-result', root).innerHTML = '<p class="hint">条件已更改，请重新查询。</p>';
  });

  $('#recent-form', root).addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const audienceId = data.get('audience_id');
    const days = data.get('days') || 30;
    const result = $('#recent-result', root);
    if (!audienceId) {
      flash('请选择要查询的班级、小组或学生。');
      return;
    }
    result.innerHTML = loading('正在回查…');
    const ticket=++recentRun;
    try {
      const payload = await api(`/api/audiences/${encodeURIComponent(audienceId)}/recent`, {
        query: { days },
      });
      if(ctx.current()&&ticket===recentRun)renderRecent(result, payload, days);
    } catch (err) {
      if(!ctx.current()||ticket!==recentRun)return;
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
            <div class="meta">${used ? `最近 ${esc(fmtDate(used))}` : '日期未记录'}${count ? `，使用 ${esc(count)} 次` : ''}${row.collection_title ? `，${esc(row.collection_title)}` : ''}${row.purpose ? `，${esc(USAGE_PURPOSES[row.purpose] || row.purpose)}` : ''}</div>
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
            <div class="meta">${used ? `最近 ${esc(fmtDate(used))}` : ''}${row.use_count ? `，${esc(row.use_count)} 题` : ''}</div>
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

  const meta=await api('/api/meta');if(!ctx.current())return;
  rosterPanel=mountRoster(audienceList,{scope:meta.draft_scope,current:()=>ctx.current(),onChange:({items,teacherId,teachers})=>{
    const changedScope=selectedTeacher!==teacherId;
    audiences=items;selectedTeacher=teacherId;syncSelects();renderAssignments();renderUsages();
    const owner=$('#audience-teacher',root),previous=owner.value;
    owner.innerHTML='<option value="">暂不分配</option>'+teachers.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
    owner.value=teacherContext!==teacherId?(teachers.some(t=>t.id===teacherId)?teacherId:''):previous;
    teacherContext=teacherId;
    if(changedScope){recentRun++;$('#recent-result',root).innerHTML='<p class="hint">请重新选择班级、小组或学生进行查询。</p>';}
  }});
  ctx.onCleanup(()=>rosterPanel.destroy());
  try{await rosterPanel.refresh();}catch(e){flash(e.message);}
  await loadBase();
}
