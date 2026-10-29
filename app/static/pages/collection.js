/* 题单编辑：题目与分节排序、跨节移动、教学角色与留白设置；右侧组题检查；顶部生成讲义。 */

import { api, fileUrl } from '../lib/api.js';
import {
  esc, badge, fmtDate, truncate, $, emptyState, errorCard, loading, num, debounce,
} from '../lib/dom.js';
import {
  QTYPES, ROLES, SECTION_KINDS, reviewLabel, reviewTone,
  COLLECTION_PURPOSES, HINT_LEVELS, BUILD_THEMES, BUILD_FORMATS, RELEASE_STATUS, RELEASE_TONE,
} from '../lib/constants.js';
import {
  flash, openModal, runSubmit, confirmDialog,
} from '../lib/ui.js';
import { refreshMeta } from '../lib/bus.js';
import { upgradeMath } from '../lib/math.js';
import { createHistory, sendCommand, applyAll } from '../lib/collection-history.js';
import { rememberCollection } from '../lib/recent-work.js';

export default async function mount(root, ctx) {
  const cid = ctx.params.id;
  let collection = {};
  let sections = [];
  let items = [];
  let check = null;
  let itemNumbers = new Map();
  const history = createHistory(20);
  let revision = '';
  let busyStep = false;
  let staleItems = [];

  function sortedSections() {
    return [...sections].sort((a, b) => Number(a.order_no ?? 0) - Number(b.order_no ?? 0));
  }

  function sectionExists(id) {
    return sections.some((section) => section.id === id);
  }

  function itemsOf(sectionId) {
    return items
      .filter((item) => (sectionId === null ? !item.section_id || !sectionExists(item.section_id) : item.section_id === sectionId))
      .sort((a, b) => Number(a.order_no ?? 0) - Number(b.order_no ?? 0));
  }

  function orderedGroups() {
    const groups = sortedSections().map((section) => ({ sectionId: section.id, items: itemsOf(section.id) }));
    const loose = itemsOf(null);
    if (loose.length) groups.push({ sectionId: null, items: loose });
    return groups;
  }

  function itemLabel(item) {
    const q = item.question || {};
    return `${q.id || item.question_id || ''} ${q.short_name || truncate(q.stem_preview, 24)}`.trim();
  }

  async function load({ silent = false } = {}) {
    if (!silent) root.innerHTML = loading('正在读取题单…');
    try {
      const data = await api(`/api/collections/${encodeURIComponent(cid)}`);
      if (!ctx.current()) return;
      collection = data.collection || {};
      sections = Array.isArray(data.sections) ? data.sections : [];
      items = Array.isArray(data.items) ? data.items : [];
      check = data.check || null;
      revision = data.revision || revision;
      loadVersionUpdates();
      rememberCollection(cid, collection.title, 'edit');
      paint();
      upgradeMath(root);
      if (!check) loadCheck();
    } catch (err) {
      if (!ctx.current()) return;
      flash(err.message);
      root.replaceChildren(errorCard(err.message, () => load()));
    }
  }

  async function reloadPreserve() {
    const y = window.scrollY;
    await load({ silent: true });
    window.scrollTo(0, y);
  }

  function renderItem(item, index, groupLength) {
    const q = item.question || {};
    const role = ROLES[item.role] || item.role || '未设角色';
    const warnings = Array.isArray(item.warnings) ? item.warnings : [];
    const timeText = `作答 ${num(item.time_solve_min)} 分，讲解 ${num(item.time_explain_min)} 分`;
    return `<li class="item-row" data-item="${esc(item.id)}">
      <div class="item-index">${String(itemNumbers.get(item.id) ?? index + 1).padStart(2, '0')}</div>
      <div class="item-main">
        <a class="item-title" href="#/question/${esc(q.id || item.question_id || '')}">${esc(q.short_name || truncate(q.stem_preview, 40) || '未命名题目')}</a>
        ${q.source_label && q.source_label !== q.short_name ? `<div class="source-caption">来源：${esc(q.source_label)}</div>` : ''}
        <div class="item-badges">
          ${badge(QTYPES[q.qtype] || q.qtype || '题型未知', 'plain')}
          ${badge(role, 'info')}
          ${badge(reviewLabel(item.review_state), reviewTone(item.review_state))}
          <span class="hint">${esc(timeText)}</span>
          ${item.space_mm ? `<span class="hint">留白 ${esc(num(item.space_mm))} mm</span>` : ''}
          ${Number(item.required) === 0 ? badge('选做', 'plain') : ''}
          ${Number(item.page_break_before) ? badge('另起页', 'plain') : ''}
        </div>
        ${q.stem_preview ? `<div class="item-stem" data-rich>${esc(q.stem_preview)}</div>` : ''}
        ${warnings.map((text) => `<div class="item-warn">${esc(text)}</div>`).join('')}
      </div>
      <div class="item-actions">
        <button type="button" class="order-btn" data-move="up" data-item-id="${esc(item.id)}" title="上移" ${index === 0 ? 'disabled' : ''}>↑</button>
        <button type="button" class="order-btn" data-move="down" data-item-id="${esc(item.id)}" title="下移" ${index === groupLength - 1 ? 'disabled' : ''}>↓</button>
        <button type="button" class="btn btn-sm" data-settings="${esc(item.id)}">设置</button>
        <button type="button" class="btn-link danger" data-del-item="${esc(item.id)}">删除</button>
      </div>
    </li>`;
  }

  function renderSection(section, index) {
    const groupItems = itemsOf(section.id);
    return `<div class="section-block" data-section="${esc(section.id)}">
      <div class="section-head">
        <span class="mono faint" style="font-size:11px;">${String(index + 1).padStart(2, '0')}</span>
        <h3>${esc(section.title || '未命名分节')}</h3>
        <span class="sec-kind">${esc(SECTION_KINDS[section.kind] || section.kind || '')}</span>
        ${badge(`${groupItems.length} 题`, 'plain')}
        <div class="sec-actions">
          <button type="button" class="btn-link" data-edit-section="${esc(section.id)}">改分节</button>
          <button type="button" class="btn-link danger" data-del-section="${esc(section.id)}">删除</button>
        </div>
      </div>
      ${section.intro ? `<div class="section-intro" data-rich>${esc(section.intro)}</div>` : ''}
      ${groupItems.length
        ? `<ol class="item-list">${groupItems.map((item, idx) => renderItem(item, idx, groupItems.length)).join('')}</ol>`
        : '<div class="card-body hint" style="padding:12px 16px;">此节还没有题目。</div>'}
    </div>`;
  }

  function renderCheck() {
    const el = $('#check-body', root);
    if (!el) return;
    const count = check?.count ?? items.length;
    const solve = Number(check?.time_solve ?? 0);
    const explain = Number(check?.time_explain ?? 0);
    const budget = Number(check?.budget ?? collection.duration_min ?? 0);
    const total = solve + explain;
    const ratio = budget > 0 ? total / budget : 0;
    const over = budget > 0 && total > budget * 0.9;

    const spread = check?.difficulty_spread || {};
    const maxCount = Math.max(1, ...[1, 2, 3, 4, 5].map((n) => Number(spread[String(n)] ?? spread[n] ?? 0)));

    const duplicates = Array.isArray(check?.duplicates) ? check.duplicates : [];
    const unreviewed = Array.isArray(check?.unreviewed) ? check.unreviewed : [];
    const missing = Array.isArray(check?.missing) ? check.missing : [];
    const coverage = check?.coverage || {};
    const knowledge = Array.isArray(coverage.knowledge) ? coverage.knowledge : [];
    const methods = Array.isArray(coverage.method) ? coverage.method : [];

    const itemById = (id) => items.find((row) => row.id === id);

    el.innerHTML = `
      <div class="check-stat-row">
        <div class="check-stat"><span class="k">题量</span><span class="v">${esc(num(count))}</span></div>
        <div class="check-stat"><span class="k">做题</span><span class="v">${esc(num(solve))}<small>分</small></span></div>
        <div class="check-stat"><span class="k">讲解</span><span class="v">${esc(num(explain))}<small>分</small></span></div>
      </div>

      <div>
        <div class="mini-title"><span>时间预算</span><span class="mono" style="font-size:10.5px;color:${over ? 'var(--ochre)' : 'var(--faint)'};">${esc(num(total))} / ${esc(num(budget))} 分</span></div>
        <div class="time-bar"><div class="fill ${over ? 'over' : ''}" style="width:${Math.min(100, Math.round(ratio * 100))}%;"></div></div>
        <div class="hint" style="margin-top:5px;">预算包含练习、讲解、过渡与预留，不只看做题时间。${over ? '已超过预算的 90%。' : ''}</div>
      </div>

      <div>
        <div class="mini-title"><span>难度分布</span><span class="hint" style="font-size:10.5px;">思维难度 1–5</span></div>
        ${[1, 2, 3, 4, 5].map((n) => {
          const value = Number(spread[String(n)] ?? spread[n] ?? 0);
          return `<div class="bar-row">
            <span class="mono">${n}</span>
            <div class="bar-track"><div class="bar-fill" style="width:${Math.round((value / maxCount) * 100)}%;"></div></div>
            <span class="n">${esc(value)}</span>
          </div>`;
        }).join('')}
      </div>

      <div>
        <div class="mini-title"><span>重复提示</span><span class="hint" style="font-size:10.5px;">${duplicates.length} 组</span></div>
        ${duplicates.length ? `<ul class="check-list warn">${duplicates.map((dup) => {
          const a = itemById(dup.a);
          const b = itemById(dup.b);
          return `<li>${esc(a ? itemLabel(a) : dup.a)} 与 ${esc(b ? itemLabel(b) : dup.b)}：${esc(dup.reason || '题面相似')}</li>`;
        }).join('')}</ul>` : '<div class="check-empty">未发现疑似重复。重复只提示，是否保留由教师决定。</div>'}
      </div>

      <div>
        <div class="mini-title"><span>未审校条目</span><span class="hint" style="font-size:10.5px;">${unreviewed.length} 条</span></div>
        ${unreviewed.length ? `<ul class="check-list warn">${unreviewed.map((id) => {
          const item = itemById(typeof id === 'string' ? id : id.id || id.item_id);
          const qid = item?.question?.id || item?.question_id;
          return `<li>${qid ? `<a href="#/question/${esc(qid)}">${esc(itemLabel(item))}</a>` : esc(String(id))}</li>`;
        }).join('')}</ul>` : '<div class="check-empty">题单内条目均已通过审校。</div>'}
      </div>

      <div>
        <div class="mini-title"><span>覆盖统计</span></div>
        <div class="coverage-block">
          <div class="hint" style="margin-bottom:3px;">知识点</div>
          ${knowledge.length ? knowledge.map((tag) => `<div class="coverage-item"><span>${esc(tag.name)}</span><span class="n">${esc(tag.count)} 题</span></div>`).join('') : '<div class="check-empty">暂无知识点统计。</div>'}
        </div>
        <div class="coverage-block">
          <div class="hint" style="margin-bottom:3px;">方法</div>
          ${methods.length ? methods.map((tag) => `<div class="coverage-item"><span>${esc(tag.name)}</span><span class="n">${esc(tag.count)} 题</span></div>`).join('') : '<div class="check-empty">暂无方法统计。</div>'}
        </div>
      </div>

      <div>
        <div class="mini-title"><span>缺项提示</span></div>
        ${missing.length ? `<ul class="check-list warn">${missing.map((text) => `<li>${esc(text)}</li>`).join('')}</ul>` : '<div class="check-empty">未发现缺项。</div>'}
      </div>
    `;
  }

  function paint() {
    const purpose = COLLECTION_PURPOSES[collection.purpose] || collection.purpose || '题单';
    const groups = orderedGroups();
    const loose = itemsOf(null);
    itemNumbers = new Map();
    let counter = 1;
    groups.forEach((group) => {
      group.items.forEach((item) => {
        itemNumbers.set(item.id, counter);
        counter += 1;
      });
    });

    root.innerHTML = `
      <div class="detail-top">
        <div>
          <div class="breadcrumb"><a href="#/collections">题单</a> <span class="faint">/</span> <span class="mono">${esc(cid)}</span></div>
          <h1 class="detail-title">${esc(collection.title || '未命名题单')}<span class="qid">${esc(cid)}</span></h1>
          <div class="detail-badges">
            ${badge(purpose, 'plain')}
            ${badge(collection.status === 'ready' ? '已就绪' : collection.status === 'archived' ? '已归档' : '草稿', collection.status === 'ready' ? 'pass' : 'plain')}
            ${badge(`课时 ${esc(num(collection.duration_min))} 分钟`, 'info')}
            ${badge(`${items.length} 题`, 'neutral')}
          </div>
          <div class="collection-head-info">
            ${collection.topic ? `<span>主题：${esc(collection.topic)}</span>` : ''}
            ${collection.level ? `<span>层次：${esc(collection.level)}</span>` : ''}
            ${collection.objectives ? `<span>目标：${esc(truncate(collection.objectives, 70))}</span>` : ''}
            <span>更新 ${esc(fmtDate(collection.updated_at))}</span>
          </div>
        </div>
        <div class="detail-actions">
          <button type="button" class="btn" data-act="undo" ${history.canUndo() ? '' : 'disabled'} title="${esc(history.peekUndo()?.label || '没有可撤销的操作')}">撤销${history.canUndo() ? '：' + esc(history.peekUndo().label) : ''}</button>
          <button type="button" class="btn" data-act="redo" ${history.canRedo() ? '' : 'disabled'} title="${esc(history.peekRedo()?.label || '没有可重做的操作')}">重做${history.canRedo() ? '：' + esc(history.peekRedo().label) : ''}</button>
          <button type="button" class="btn" data-act="edit-collection">改题单信息</button>
          <a class="btn btn-primary" href="#/layout/${encodeURIComponent(cid)}">排版与导出</a>
          <details class="compact-actions"><summary class="btn">更多操作</summary><div><button type="button" class="btn" data-act="build">快速导出</button><a class="btn" href="#/releases">查看归档</a></div></details>
        </div>
      </div>

      ${staleItems.length ? `<div class="notice notice-warn" id="stale-banner">
        <b>${staleItems.length} 道题有新版本</b>　当前题单引用的还是旧版，更新后才会用新题面。
        <button type="button" class="btn btn-sm" data-act="version-updates">查看并更新</button>
      </div>` : ''}

      <div class="collection-grid">
        <section class="card">
          <div class="card-head">
            <h2>题目与分节</h2>
            <div class="row-actions">
              <button type="button" class="btn btn-sm" data-act="add-question">从题库加题</button>
              <button type="button" class="btn btn-sm" data-act="add-section">新增分节</button>
            </div>
          </div>
          ${groups.length ? `<div class="sections">
            ${sortedSections().map((section, index) => renderSection(section, index)).join('')}
            ${loose.length ? `<div class="section-block">
              <div class="unsectioned-label">未分节，${loose.length} 题</div>
              <ol class="item-list">${loose.map((item, idx) => renderItem(item, idx, loose.length)).join('')}</ol>
            </div>` : ''}
          </div>` : `<div class="card-body">${emptyState('题单还是空的', '先新增分节，再用「从题库加题」把题目放进来；也可以到题库检索，用选题篮一键成单。')}</div>`}
        </section>

        <aside class="check-col">
          <section class="card">
            <div class="card-head">
              <h2>组题检查</h2>
              <button type="button" class="btn btn-sm" data-act="check-refresh">刷新检查</button>
            </div>
            <div class="check-body" id="check-body">${loading('正在检查…')}</div>
          </section>
        </aside>
      </div>
    `;

    renderCheck();
    upgradeMath(root);
  }

  async function loadVersionUpdates() {
    try {
      const data = await api(`/api/collections/${encodeURIComponent(cid)}/version-updates`);
      staleItems = Array.isArray(data.items) ? data.items : [];
    } catch {
      staleItems = [];
    }
  }

  function openVersionUpdates() {
    if (!staleItems.length) return;
    const rows = staleItems.map((row, index) => `
      <label class="check usage-item">
        <input type="checkbox" data-update="${esc(row.item_id)}" ${index === 0 ? 'checked' : ''}>
        <span>
          第 ${esc(row.order_no + 1)} 题　${esc(row.short_name || row.question_id)}
          ：v${esc(row.used.version_no)} → <b>v${esc(row.new.version_no)}</b>
          ${row.new.change_note ? `（${esc(row.new.change_note)}）` : ''}
          ${row.needs_review.length ? `<br><span class="hint">换版后需重选：${esc(row.needs_review.join('、'))}</span>` : ''}
        </span>
      </label>`).join('');

    const modal = openModal({
      title: `更新到新版本（${staleItems.length} 条）`,
      width: 'modal-wide',
      body: `<div class="modal-error" hidden data-error></div>
        <p class="hint">更新只改题单引用的版本；题目本身、旧版本与已归档的讲义都不受影响。
        旧解法与小问范围不会套到新版本上，失配的直接清空，需要你在题目里重选。</p>
        <div class="usage-items">${rows}</div>`,
      footer: `<button type="button" class="btn" data-close>取消</button>
               <button type="button" class="btn btn-primary" data-apply>更新选中条目</button>`,
    });

    const err = modal.body.querySelector('[data-error]');
    modal.foot.querySelector('[data-apply]').addEventListener('click', async (event) => {
      const picked = [...modal.body.querySelectorAll('[data-update]')]
        .filter((box) => box.checked).map((box) => ({ item_id: box.dataset.update }));
      if (!picked.length) { err.hidden = false; err.textContent = '先选要更新的条目。'; return; }
      const btn = event.currentTarget;
      btn.disabled = true;
      try {
        const res = await api(`/api/collections/${encodeURIComponent(cid)}/version-updates/apply`, {
          method: 'POST',
          body: { operation_id: (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())),
                  base_revision: revision, updates: picked },
        });
        revision = res.revision || revision;
        const dropped = (res.results || []).flatMap((row) => row.dropped || []);
        modal.close();
        flash(`已更新 ${res.updated} 条${dropped.length ? '；' + dropped.join('、') + '已清空，请到题目里重选' : ''}`, 'ok');
        await reloadPreserve();
        loadCheck();
      } catch (e) {
        err.hidden = false;
        err.textContent = e.code === 'revision_conflict'
          ? '题单在操作期间被改过，请关闭后重新打开本窗口。'
          : (e.message || String(e));
        btn.disabled = false;
      }
    });
  }

  /** 撤销／重做：把上一步的逆动作依次发回服务端，并把结果反过来压到另一侧。 */
  async function step(direction) {
    if (busyStep) return;
    const entry = direction === 'undo' ? history.peekUndo() : history.peekRedo();
    if (!entry) return;
    busyStep = true;
    try {
      // 撤销按逆序回放：最后做的先撤；重做则按记录顺序。
      const inverses = direction === 'undo' ? [...entry.inverses].reverse() : [...entry.inverses];
      const result = await applyAll(cid, revision, inverses);
      revision = result.revision || revision;
      if (direction === 'undo') {
        history.takeUndo();
        history.pushRedo(entry.label, result.inverses);
        flash(`已撤销：${entry.label}`, 'ok');
      } else {
        history.takeRedo();
        history.pushUndo(entry.label, result.inverses);
        flash(`已重做：${entry.label}`, 'ok');
      }
      await reloadPreserve();
      loadCheck();
    } catch (err) {
      // 冲突时保留操作栈但提示核对，不自动覆盖别人的修改
      flash(err.code === 'revision_conflict'
        ? '题单已被其他页面修改，请刷新后再决定是否撤销。'
        : err.message);
    } finally {
      busyStep = false;
    }
  }

  async function loadCheck() {
    try {
      check = await api(`/api/collections/${encodeURIComponent(cid)}/check`);
      renderCheck();
    } catch (err) {
      const el = $('#check-body', root);
      if (el) {
        el.innerHTML = `<div class="modal-error">${esc(err.message)}</div>`;
      }
      flash(err.message);
    }
  }

  // ------------------------------------------------------------ 保存排序

  async function persistOrder(groups) {
    const payload = [];
    let order = 0;
    groups.forEach((group) => {
      group.items.forEach((item) => {
        payload.push({ id: item.id, order_no: order, section_id: group.sectionId });
        order += 1;
      });
    });
    const out = await sendCommand(cid, revision, 'reorder', '', { items: payload });
    revision = out.revision || revision;
    history.push(out.label || '调整顺序', [out.inverse]);
    await reloadPreserve();
  }

  async function moveItem(itemId, direction) {
    const groups = orderedGroups();
    const group = groups.find((g) => g.items.some((item) => item.id === itemId));
    if (!group) return;
    const index = group.items.findIndex((item) => item.id === itemId);
    const target = direction === 'up' ? index - 1 : index + 1;
    if (target < 0 || target >= group.items.length) return;
    const [row] = group.items.splice(index, 1);
    group.items.splice(target, 0, row);
    try {
      await persistOrder(groups);
    } catch (err) {
      flash(err.message);
    }
  }

  // ------------------------------------------------------------ 条目设置

  function openItemSettings(itemId) {
    const item = items.find((row) => row.id === itemId);
    if (!item) return;
    const sectionOptions = [
      `<option value="" ${!item.section_id ? 'selected' : ''}>未分节</option>`,
      ...sortedSections().map((section) => `<option value="${esc(section.id)}" ${section.id === item.section_id ? 'selected' : ''}>${esc(section.title)}</option>`),
    ].join('');

    const modal = openModal({
      title: `题目设置： ${item.question?.id || item.question_id || ''}`,
      body: `<div class="modal-error" hidden></div>
        <p class="hint">${esc(item.question?.short_name || truncate(item.question?.stem_preview, 60) || '')}</p>
        <div class="inline-fields two">
          <label class="field"><span>本次教学角色</span>
            <select data-role>${Object.entries(ROLES).map(([value, label]) => `<option value="${value}" ${value === item.role ? 'selected' : ''}>${label}</option>`).join('')}</select>
          </label>
          <label class="field"><span>所在分节</span><select data-section>${sectionOptions}</select></label>
        </div>
        <div class="inline-fields three">
          <label class="field"><span>独立作答（分钟）</span><input type="number" min="0" step="0.5" data-solve placeholder="留空用题目估计" value="${item.time_solve_min ?? ''}"></label>
          <label class="field"><span>讲解时间（分钟）</span><input type="number" min="0" step="0.5" data-explain placeholder="留空用题目估计" value="${item.time_explain_min ?? ''}"></label>
          <label class="field"><span>作答留白（mm）</span><input type="number" min="0" step="1" data-space placeholder="如 40" value="${item.space_mm ?? ''}"></label>
        </div>
        <div class="inline-fields two">
          <label class="field"><span>提示层级</span>
            <select data-hint>${Object.entries(HINT_LEVELS).map(([value, label]) => `<option value="${value}" ${value === (item.hint_level || 'none') ? 'selected' : ''}>${label}</option>`).join('')}</select>
          </label>
          <div class="field"><span>其他</span>
            <div class="row" style="padding-top:5px;">
              <label class="check"><input type="checkbox" data-required ${Number(item.required) === 0 ? '' : 'checked'}> 必做</label>
              <label class="check"><input type="checkbox" data-break ${Number(item.page_break_before) ? 'checked' : ''}> 此题前分页</label>
            </div>
          </div>
        </div>
        <label class="field"><span>本次讲评备注</span>
          <textarea rows="2" data-note placeholder="只记本班讲法，不改公共解析">${esc(item.note || '')}</textarea>
        </label>`,
      footer: `<button type="button" class="btn" data-close>取消</button>
               <button type="button" class="btn btn-primary" data-save>保存设置</button>`,
    });

    const errEl = modal.body.querySelector('.modal-error');
    modal.foot.querySelector('[data-save]').addEventListener('click', async (event) => {
      await runSubmit(event.currentTarget, errEl, async () => {
        const value = (selector) => modal.body.querySelector(selector).value;
        const targetSection = value('[data-section]') || null;
        const sectionChanged = (item.section_id || null) !== targetSection;
        const body = {
          role: value('[data-role]'),
          time_solve_min: value('[data-solve]') ? Number(value('[data-solve]')) : null,
          time_explain_min: value('[data-explain]') ? Number(value('[data-explain]')) : null,
          space_mm: value('[data-space]') ? Number(value('[data-space]')) : null,
          hint_level: value('[data-hint]'),
          required: modal.body.querySelector('[data-required]').checked ? 1 : 0,
          page_break_before: modal.body.querySelector('[data-break]').checked ? 1 : 0,
          note: value('[data-note]').trim() || null,
        };
        const inverses = [];
        const settingsOut = await sendCommand(cid, revision, 'settings', item.id, body);
        revision = settingsOut.revision || revision;
        inverses.push(settingsOut.inverse);

        // 条目 PATCH 不接受 section_id；跨节移动走题单排序接口。
        if (sectionChanged) {
          const groups = orderedGroups();
          let moved = null;
          groups.forEach((group) => {
            const index = group.items.findIndex((row) => row.id === item.id);
            if (index >= 0) [moved] = group.items.splice(index, 1);
          });
          if (moved) {
            let target = groups.find((group) => group.sectionId === targetSection);
            if (!target) {
              target = { sectionId: targetSection, items: [] };
              groups.push(target);
            }
            target.items.push({ ...moved, section_id: targetSection });
          }
          const payload = [];
          let order = 0;
          groups.forEach((group) => {
            group.items.forEach((row) => {
              payload.push({ id: row.id, order_no: order, section_id: group.sectionId });
              order += 1;
            });
          });
          if (payload.length) {
            const moveOut = await sendCommand(cid, revision, 'reorder', '', { items: payload });
            revision = moveOut.revision || revision;
            inverses.push(moveOut.inverse);
          }
        }

        modal.close();
        history.push('修改条目设置', inverses);
        flash('条目设置已保存，可撤销。', 'ok');
        await reloadPreserve();
        loadCheck();
      });
    });
  }

  // ------------------------------------------------------------ 分节

  function openSectionModal(section = null) {
    const isEdit = Boolean(section);
    const modal = openModal({
      title: isEdit ? '修改分节' : '新增分节',
      body: `<div class="modal-error" hidden></div>
        <label class="field"><span>分节标题<span class="req">*</span></span>
          <input type="text" data-title value="${esc(section?.title || '')}" placeholder="如 一、单调性的定义">
        </label>
        <div class="inline-fields two">
          <label class="field"><span>分节类型</span>
            <select data-kind>${Object.entries(SECTION_KINDS).map(([value, label]) => `<option value="${value}" ${value === (section?.kind || 'question') ? 'selected' : ''}>${label}</option>`).join('')}</select>
          </label>
          <label class="field"><span>顺序</span>
            <input type="number" min="0" step="1" data-order value="${section?.order_no ?? sections.length}">
          </label>
        </div>
        <label class="field"><span>导语 / 内容</span>
          <textarea rows="3" data-intro placeholder="概念回顾、方法小结或教师提示；题单内专用，不影响公共题目">${esc(section?.intro || '')}</textarea>
        </label>`,
      footer: `<button type="button" class="btn" data-close>取消</button>
               <button type="button" class="btn btn-primary" data-save>${isEdit ? '保存' : '新增'}</button>`,
    });
    const errEl = modal.body.querySelector('.modal-error');
    modal.foot.querySelector('[data-save]').addEventListener('click', async (event) => {
      await runSubmit(event.currentTarget, errEl, async () => {
        const title = modal.body.querySelector('[data-title]').value.trim();
        if (!title) throw new Error('请填写分节标题。');
        const body = {
          title,
          kind: modal.body.querySelector('[data-kind]').value,
          intro: modal.body.querySelector('[data-intro]').value.trim() || null,
          order_no: Number(modal.body.querySelector('[data-order]').value) || 0,
        };
        if (isEdit) {
          await api(`/api/sections/${encodeURIComponent(section.id)}`, { method: 'PATCH', body });
        } else {
          await api(`/api/collections/${encodeURIComponent(cid)}/sections`, { method: 'POST', body });
        }
        modal.close();
        flash(isEdit ? '分节已修改。' : '分节已新增。', 'ok');
        await reloadPreserve();
        loadCheck();
      });
    });
  }

  // ------------------------------------------------------------ 加题

  function openAddQuestion() {
    const sectionOptions = [
      '<option value="">未分节</option>',
      ...sortedSections().map((section) => `<option value="${esc(section.id)}">${esc(section.title)}</option>`),
    ].join('');
    const modal = openModal({
      title: '从题库加题',
      width: 'modal-wide',
      body: `<div class="inline-fields two">
          <label class="field"><span>加入分节</span><select data-section>${sectionOptions}</select></label>
          <label class="field"><span>本次角色</span>
            <select data-role>${Object.entries(ROLES).map(([value, label]) => `<option value="${value}" ${value === 'example' ? 'selected' : ''}>${label}</option>`).join('')}</select>
          </label>
        </div>
        <label class="field"><span>关键词</span><input type="search" data-keyword placeholder="按题面、简名或答案检索" autocomplete="off"></label>
        <div class="addq-results" data-results>${loading('正在检索…')}</div>
        <div class="modal-error" hidden></div>`,
      footer: '<button type="button" class="btn" data-close>关闭</button>',
    });

    const resultsEl = modal.body.querySelector('[data-results]');
    const errEl = modal.body.querySelector('.modal-error');
    let token = 0;

    const search = debounce(async () => {
      const keyword = modal.body.querySelector('[data-keyword]').value.trim();
      const current = ++token;
      resultsEl.innerHTML = loading('正在检索…');
      try {
        const data = await api('/api/questions', {
          query: { keyword: keyword || undefined, limit: 10, review: 'any', status: 'active', sort: 'recent' },
        });
        if (current !== token) return;
        const list = Array.isArray(data.items) ? data.items : [];
        if (!list.length) {
          resultsEl.innerHTML = '<div class="check-empty" style="padding:10px 2px;">没有找到题目。</div>';
          return;
        }
        resultsEl.innerHTML = list.map((row) => `<div class="addq-row">
          <div class="t">
            <span class="id">${esc(row.id)}</span>
            ${esc(truncate(row.short_name || row.stem_preview || '', 70))}
            ${row.source_label && row.source_label !== row.short_name ? `<div class="source-caption">来源：${esc(row.source_label)}</div>` : ''}
          </div>
          ${badge(QTYPES[row.qtype] || row.qtype, 'plain')}
          <button type="button" class="btn btn-sm" data-add-q="${esc(row.id)}">加入</button>
        </div>`).join('');
      } catch (err) {
        if (current !== token) return;
        resultsEl.innerHTML = `<div class="modal-error">${esc(err.message)}</div>`;
      }
    }, 260);

    resultsEl.addEventListener('click', async (event) => {
      const btn = event.target.closest('[data-add-q]');
      if (!btn) return;
      const questionId = btn.dataset.addQ;
      btn.disabled = true;
      btn.textContent = '加入中…';
      try {
        await api(`/api/collections/${encodeURIComponent(cid)}/items`, {
          method: 'POST',
          body: {
            question_id: questionId,
            section_id: modal.body.querySelector('[data-section]').value || null,
            role: modal.body.querySelector('[data-role]').value,
          },
        });
        modal.close();
        flash(`已加入 ${questionId}。`, 'ok');
        await reloadPreserve();
        loadCheck();
      } catch (err) {
        errEl.hidden = false;
        errEl.textContent = err.message;
        flash(err.message);
        btn.disabled = false;
        btn.textContent = '加入';
      }
    });

    modal.body.querySelector('[data-keyword]').addEventListener('input', search);
    search();
  }

  // ------------------------------------------------------------ 题单信息

  function openCollectionInfo() {
    const modal = openModal({
      title: '题单信息',
      width: 'modal-wide',
      body: `<div class="modal-error" hidden></div>
        <label class="field"><span>题单名称<span class="req">*</span></span>
          <input type="text" data-title value="${esc(collection.title || '')}">
        </label>
        <div class="inline-fields three">
          <label class="field"><span>用途</span>
            <select data-purpose>${Object.entries(COLLECTION_PURPOSES).map(([value, label]) => `<option value="${value}" ${value === collection.purpose ? 'selected' : ''}>${label}</option>`).join('')}</select>
          </label>
          <label class="field"><span>课时（分钟）</span><input type="number" min="1" step="5" data-duration value="${collection.duration_min ?? ''}"></label>
          <label class="field"><span>学生层次</span><input type="text" data-level value="${esc(collection.level || '')}" placeholder="如 高一提高"></label>
        </div>
        <label class="field"><span>主题</span><input type="text" data-topic value="${esc(collection.topic || '')}" placeholder="如 函数单调性"></label>
        <label class="field"><span>前置知识</span><input type="text" data-prereqs value="${esc(collection.prereqs || '')}" placeholder="如 函数定义、不等式变形"></label>
        <label class="field"><span>学习目标</span><textarea rows="3" data-objectives>${esc(collection.objectives || '')}</textarea></label>
        <label class="field"><span>备注</span><textarea rows="2" data-note>${esc(collection.note || '')}</textarea></label>`,
      footer: `<button type="button" class="btn" data-close>取消</button>
               <button type="button" class="btn btn-primary" data-save>保存</button>`,
    });
    const errEl = modal.body.querySelector('.modal-error');
    modal.foot.querySelector('[data-save]').addEventListener('click', async (event) => {
      await runSubmit(event.currentTarget, errEl, async () => {
        const val = (selector) => modal.body.querySelector(selector).value.trim();
        if (!val('[data-title]')) throw new Error('题单名称不能为空。');
        await api(`/api/collections/${encodeURIComponent(cid)}`, {
          method: 'PATCH',
          body: {
            title: val('[data-title]'),
            purpose: modal.body.querySelector('[data-purpose]').value,
            duration_min: val('[data-duration]') ? Number(val('[data-duration]')) : null,
            level: val('[data-level]') || null,
            topic: val('[data-topic]') || null,
            prereqs: val('[data-prereqs]') || null,
            objectives: val('[data-objectives]') || null,
            note: val('[data-note]') || null,
          },
        });
        modal.close();
        flash('题单信息已保存。', 'ok');
        await reloadPreserve();
        loadCheck();
      });
    });
  }

  // ------------------------------------------------------------ 生成讲义

  function openBuildModal() {
    const defaultTitle = collection.title || '';
    const modal = openModal({
      title: '生成讲义',
      width: 'modal-wide',
      body: `<div class="modal-error" hidden></div>
        <div class="inline-fields two">
          <label class="field"><span>标题<span class="req">*</span></span><input type="text" data-title value="${esc(defaultTitle)}"></label>
          <label class="field"><span>副标题</span><input type="text" data-subtitle value="习题课讲义"></label>
        </div>
        <div class="inline-fields three">
          <label class="field"><span>前置语</span><input type="text" data-pretitle value="${esc(collection.topic || '高一数学专题')}"></label>
          <label class="field"><span>主题色</span>
            <select data-theme>${BUILD_THEMES.map(([value, label]) => `<option value="${value}" ${value === 'blue' ? 'selected' : ''}>${label}</option>`).join('')}</select>
          </label>
          <label class="field"><span>版面</span>
            <select data-format>${BUILD_FORMATS.map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select>
          </label>
        </div>
        <label class="field"><span>座右铭</span><input type="text" data-motto placeholder="如 先看定义，再动手"></label>
        <div class="inline-fields two">
          <div class="field"><span>版本</span>
            <div class="row" style="padding-top:5px;">
              <label class="check"><input type="checkbox" data-student checked> 学生版</label>
              <label class="check"><input type="checkbox" data-answer checked> 解答版</label>
              <label class="check"><input type="checkbox" data-teacher checked> 解析版</label>
            </div>
          </div>
          <label class="check" style="margin-bottom:6px;"><input type="checkbox" data-toc checked> 生成目录</label>
        </div>
        <div data-result></div>`,
      footer: `<button type="button" class="btn" data-close>关闭</button>
               <button type="button" class="btn btn-primary" data-build>生成 PDF</button>`,
    });

    const errEl = modal.body.querySelector('.modal-error');
    const resultEl = modal.body.querySelector('[data-result]');
    const buildBtn = modal.foot.querySelector('[data-build]');
    const savedLayout = collection.layout_options?.options || {};
    for (const key of ['title','subtitle','pretitle','motto','theme','format']) {
      const input = modal.body.querySelector('[data-' + key + ']');
      if (input && savedLayout[key] != null) input.value = savedLayout[key];
    }
    if (savedLayout.toc != null) modal.body.querySelector('[data-toc]').checked = savedLayout.toc;

    buildBtn.addEventListener('click', async () => {
      const versions = [];
      if (modal.body.querySelector('[data-student]').checked) versions.push('student');
      if (modal.body.querySelector('[data-answer]').checked) versions.push('answer');
      if (modal.body.querySelector('[data-teacher]').checked) versions.push('teacher');
      if (!versions.length) {
        errEl.hidden = false;
        errEl.textContent = '请至少选择一个版本。';
        return;
      }
      const title = modal.body.querySelector('[data-title]').value.trim();
      if (!title) {
        errEl.hidden = false;
        errEl.textContent = '请填写讲义标题。';
        return;
      }
      buildBtn.disabled = true;
      buildBtn.textContent = '生成中…';
      errEl.hidden = true;
      resultEl.innerHTML = '<div class="hint" style="padding-top:6px;">正在生成 LaTeX 并编译，可能需要一会儿。</div>';
      try {
        const data = await api(`/api/collections/${encodeURIComponent(cid)}/build`, {
          method: 'POST',
          body: {
            title,
            subtitle: modal.body.querySelector('[data-subtitle]').value.trim() || null,
            pretitle: modal.body.querySelector('[data-pretitle]').value.trim() || null,
            motto: modal.body.querySelector('[data-motto]').value.trim() || null,
            theme: modal.body.querySelector('[data-theme]').value,
            versions,
            template: 'exbook',
            format: modal.body.querySelector('[data-format]').value,
            toc: modal.body.querySelector('[data-toc]').checked,
          },
        });
        resultEl.innerHTML = buildResultHtml(data);
        flash(`已生成 ${data?.release?.id || '讲义'}。`, data?.release?.status === 'failed' ? 'error' : 'ok');
        refreshMeta();
      } catch (err) {
        errEl.hidden = false;
        errEl.textContent = err.message;
        resultEl.innerHTML = '';
        flash(err.message);
      } finally {
        buildBtn.disabled = false;
        buildBtn.textContent = '重新生成';
      }
    });
  }

  function buildResultHtml(data) {
    const release = data?.release || {};
    const files = data?.files || {};
    const tex = data?.tex || {};
    const checks = data?.checks || {};
    const failed = release.status === 'failed';
    const warnings = Array.isArray(checks.warnings)
      ? checks.warnings.join('\n')
      : (checks.warnings || '');

    const fileChip = (entry, label) => {
      if (!entry) return `<span class="file-chip missing">${label}未生成</span>`;
      const url = entry.url || fileUrl(entry.path || '');
      if (!url) return `<span class="file-chip missing">${label}没有文件路径</span>`;
      return `<a class="file-chip" href="${esc(url)}" target="_blank" rel="noopener">${label} PDF
        ${entry.pages ? `<span class="pg">${esc(entry.pages)} 页</span>` : ''}</a>`;
    };

    const pill = (label, value, bad = false) => `<span class="check-pill ${bad ? 'bad' : 'good'}">${label} ${esc(value ?? 0)}</span>`;
    const leak = checks.leak || 'clean';
    const leakBad = leak !== 'clean';

    return `<div class="build-result">
      <div class="spread" style="margin-bottom:8px;">
        <h3 style="margin:0;">${esc(release.id || '讲义')}</h3>
        ${badge(RELEASE_STATUS[release.status] || release.status || '已生成', RELEASE_TONE[release.status] || (failed ? 'fail' : 'pass'))}
      </div>
      <div class="release-files" style="margin-bottom:8px;">
        ${fileChip(files.student, '学生版')}
        ${files.answer ? fileChip(files.answer, '解答版') : ''}
        ${fileChip(files.teacher, '解析版')}
      </div>
      <div class="checks-inline">
        ${pill('学生版错误', checks.student_errors, Number(checks.student_errors) > 0)}
        ${pill('解答版错误', checks.answer_errors, Number(checks.answer_errors) > 0)}
        ${pill('解析版错误', checks.teacher_errors, Number(checks.teacher_errors) > 0)}
        ${pill('溢出', checks.overfull, Number(checks.overfull) > 0)}
        ${pill('缺字', checks.missing_char, Number(checks.missing_char) > 0)}
        <span class="check-pill ${leakBad ? 'bad' : 'good'}">答案泄露 ${esc(leak === 'clean' ? '未发现' : leak)}</span>
      </div>
      ${tex.student || tex.teacher ? `<div class="hint" style="margin-top:8px;">TeX 源文件：<span class="mono">${esc(tex.student || '')}</span>${tex.answer ? `，<span class="mono">${esc(tex.answer)}</span>` : ''}${tex.teacher ? `，<span class="mono">${esc(tex.teacher)}</span>` : ''}</div>` : ''}
      ${warnings ? `<div class="build-warn">${esc(warnings)}</div>` : ''}
      ${failed ? '<div class="hint" style="margin-top:8px;color:var(--ochre);">构建失败。上面是编译器输出尾部，请据此检查缺项或环境。</div>' : ''}
      <div class="row-actions" style="margin-top:10px;">
        <a class="btn btn-sm" href="#/releases">到导出与归档查看</a>
        ${release.id ? `<span class="hint">快照与文件版本已登记。</span>` : ''}
      </div>
    </div>`;
  }

  // ------------------------------------------------------------ 事件

  // 只有在非文本编辑区域才接管撤销快捷键，输入框里的撤销仍由浏览器处理
  function onHistoryKey(event) {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const tag = (event.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || event.target.isContentEditable) return;
    if (event.key === 'z' && !event.shiftKey) {
      event.preventDefault(); step('undo');
    } else if ((event.key === 'z' && event.shiftKey) || event.key === 'y') {
      event.preventDefault(); step('redo');
    }
  }
  document.addEventListener('keydown', onHistoryKey);
  if (ctx.onCleanup) ctx.onCleanup(() => document.removeEventListener('keydown', onHistoryKey));

  root.addEventListener('click', async (event) => {
    const act = event.target.closest('[data-act]')?.dataset.act;
    if (act === 'add-section') {
      openSectionModal();
      return;
    }
    if (act === 'add-question') {
      openAddQuestion();
      return;
    }
    if (act === 'edit-collection') {
      openCollectionInfo();
      return;
    }
    if (act === 'build') {
      openBuildModal();
      return;
    }
    if (act === 'check-refresh') {
      $('#check-body', root).innerHTML = loading('正在检查…');
      await loadCheck();
      return;
    }
    if (act === 'version-updates') {
      openVersionUpdates();
      return;
    }
    if (act === 'undo' || act === 'redo') {
      await step(act);
      return;
    }

    const editSection = event.target.closest('[data-edit-section]');
    if (editSection) {
      const section = sections.find((row) => row.id === editSection.dataset.editSection);
      if (section) openSectionModal(section);
      return;
    }

    const delSection = event.target.closest('[data-del-section]');
    if (delSection) {
      const section = sections.find((row) => row.id === delSection.dataset.delSection);
      const ok = await confirmDialog({
        title: '删除分节',
        message: `删除「${section?.title || '此分节'}」后，其中的条目会移入「未分节」，题目本身不受影响。`,
        confirmText: '删除分节',
        danger: true,
      });
      if (!ok) return;
      try {
        await api(`/api/sections/${encodeURIComponent(delSection.dataset.delSection)}`, { method: 'DELETE' });
        flash('分节已删除，条目已移入未分节。', 'ok');
        await reloadPreserve();
        loadCheck();
      } catch (err) {
        flash(err.message);
      }
      return;
    }

    const move = event.target.closest('[data-move]');
    if (move) {
      await moveItem(move.dataset.itemId, move.dataset.move);
      return;
    }

    const settings = event.target.closest('[data-settings]');
    if (settings) {
      openItemSettings(settings.dataset.settings);
      return;
    }

    const delItem = event.target.closest('[data-del-item]');
    if (delItem) {
      const item = items.find((row) => row.id === delItem.dataset.delItem);
      const ok = await confirmDialog({
        title: '移除条目',
        message: `从本题单移除「${itemLabel(item || {})}」？公共题目不会被删除。`,
        confirmText: '移除',
        danger: true,
      });
      if (!ok) return;
      try {
        const out = await sendCommand(cid, revision, 'remove', delItem.dataset.delItem);
        revision = out.revision || revision;
        history.push(out.label || '移除题目', [out.inverse]);
        flash('条目已移除，可撤销。', 'ok');
        await reloadPreserve();
        loadCheck();
      } catch (err) {
        flash(err.message);
      }
    }
  });

  await load();
}
