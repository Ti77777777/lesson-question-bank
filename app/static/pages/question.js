/* 题目详情：左侧原始证据、中间当前内容、右侧逐项审校；底部标签、关系与使用历史。 */

import { api, apiOptional, pdfUrl } from '../lib/api.js';
import {
  esc, badge, fmtDate, fmtDateTime, $, emptyState, errorCard, loading,
  optionLetter, serialize,
} from '../lib/dom.js';
import {
  QTYPES, REVIEW_ITEMS, REVIEW_RESULTS, reviewLabel, reviewTone,
  ROLES, REL_TYPES, TAG_DIMENSIONS, SOURCE_KINDS, USAGE_PURPOSES,
} from '../lib/constants.js';
import {
  flash, flashAfterNavigation, openModal, runSubmit, confirmDialog, textDialog, pickTags,
} from '../lib/ui.js';
import { refreshMeta } from '../lib/bus.js';
import { upgradeMath } from '../lib/math.js';
import { openCorrections } from '../lib/corrections.js';

const FIELD_LABELS = {
  qtype: '题型',
  stem: '题干',
  options: '选项',
  answer: '简明答案',
  answer_keys: '正确选项',
  subquestions: '小问',
  analysis: '解析',
  teaching: '教学说明',
  material_refs: '素材引用',
  total_points: '总分',
  change_note: '修订说明',
  version_no: '版本号',
  created_at: '创建时间',
  review_state: '版本状态',
};

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

function asObject(value) {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  }
  return {};
}

function asKeys(value) {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.map(String);
    } catch {
      /* 不是 JSON 时按分隔符拆 */
    }
    return value.split(/[,，、\s]+/).filter(Boolean);
  }
  return [];
}

export default async function mount(root, ctx) {
  const qid = ctx.params.id;
  let detail = null;
  let question = {};
  let current = {};
  let versions = [];
  let occurrences = [];
  let reviews = [];
  let relations = { out: [], in: [] };
  let reviewStatus = {};
  let eligibility = { usable: false, missing: [] };
  let usage = [];
  let lastByItem = {};

  async function load({ silent = false } = {}) {
    if (!silent) root.innerHTML = loading('正在读取题目…');
    try {
      detail = await api(`/api/questions/${encodeURIComponent(qid)}`);
      if (!ctx.current()) return;
      question = detail.question || {};
      current = detail.current_version || {};
      versions = asArray(detail.versions);
      occurrences = asArray(detail.occurrences);
      reviews = asArray(detail.reviews);
      const rel = detail.relations || {};
      relations = { out: asArray(rel.out), in: asArray(rel.in) };
      reviewStatus = detail.review_status || {};
      eligibility = detail.eligibility || { usable: false, missing: [] };
      usage = asArray(detail.usage);
      lastByItem = {};
      const solutionIds = new Set(asArray(current.solutions).map((sol) => sol.id));
      reviews.forEach((row) => {
        if (!row || !row.item) return;
        if (row.target_type === 'solution' && row.item === 'solution') {
          if (solutionIds.has(row.target_id) && !lastByItem.solution) lastByItem.solution = row;
          return;
        }
        if (row.target_id && row.target_id !== current.id) return;
        if (!lastByItem[row.item]) lastByItem[row.item] = row;
      });
      paint();
      upgradeMath(root);
    } catch (err) {
      if (!ctx.current()) return;
      flash(err.message);
      root.replaceChildren(errorCard(err.message, () => load()));
    }
  }

  function renderOccurrence(occ) {
    const kind = SOURCE_KINDS[occ.source_kind] || occ.source_kind || '';
    const file = occ.file || occ.file_rel_path || '';
    const page = occ.pdf_page;
    const link = file ? pdfUrl(file, page) : '';
    const raw = String(occ.raw_text || '');
    return `<div class="occ">
      <div class="occ-head">
        <span class="occ-source">${esc(occ.source_name || '（未关联资料）')}</span>
        ${occ.pending ? badge('出处待确认', 'ochre') : ''}
      </div>
      <div class="occ-meta">
        ${kind ? `<span>${esc(kind)}</span>` : ''}
        ${page ? `<span>PDF 第 <b class="mono">${esc(page)}</b> 页</span>` : ''}
        ${occ.print_page ? `<span>印刷页 <b class="mono">${esc(occ.print_page)}</b></span>` : ''}
        ${occ.qno ? `<span>原题号 <b class="mono">${esc(occ.qno)}</b></span>` : ''}
      </div>
      ${link ? `<div class="occ-links"><a href="${esc(link)}" target="_blank" rel="noopener">打开原文件${page ? `（第 ${esc(page)} 页）` : ''}</a></div>` : ''}
      ${raw ? `<details><summary>查看原文</summary><div class="raw-box" data-rich>${esc(raw)}</div></details>` : ''}
      ${occ.note ? `<div class="hint" style="margin-top:6px;">${esc(occ.note)}</div>` : ''}
    </div>`;
  }

  function renderSolution(sol, index) {
    const methodTags = asArray(sol.method_tags || sol.tags || sol.methods).map((tag) => (
      typeof tag === 'string' ? tag : (tag?.name || tag?.id || '')
    )).filter(Boolean);
    return `<div class="solution">
      <div class="solution-head">
        <span class="solution-label">${esc(sol.label || `解法${index + 1}`)}</span>
        ${sol.is_primary ? badge('选用解法', 'pass') : ''}
        ${methodTags.map((name) => badge(`方法：${name}`, 'violet')).join('')}
      </div>
      ${sol.approach ? `<div class="hint" style="margin-bottom:4px;">思路：<span data-rich>${esc(sol.approach)}</span></div>` : ''}
      <div class="steps" data-rich>${esc(sol.steps || '（步骤待补）')}</div>
      ${sol.conditions ? `<div class="cond">适用条件：<span data-rich>${esc(sol.conditions)}</span></div>` : ''}
      ${sol.teacher_tip ? `<div class="cond">讲评提示：<span data-rich>${esc(sol.teacher_tip)}</span></div>` : ''}
    </div>`;
  }

  function renderReviewRow(item, label) {
    const result = reviewStatus[item] || lastByItem[item]?.result || 'pending';
    const last = lastByItem[item];
    return `<div class="review-row">
      <div class="review-row-top">
        <span class="review-name">${esc(label)}</span>
        ${badge(reviewLabel(result), reviewTone(result))}
      </div>
      <div class="review-controls">
        <select data-review-select="${item}" aria-label="${esc(label)}审校结论">
          ${Object.entries(REVIEW_RESULTS).map(([value, text]) => `<option value="${value}" ${value === result ? 'selected' : ''}>${text}</option>`).join('')}
        </select>
        <button type="button" class="btn btn-sm" data-review="${item}">提交</button>
      </div>
      <input type="text" class="review-note" data-review-note="${item}" placeholder="${last?.note ? esc(last.note) : '备注（可选）'}" aria-label="${esc(label)}审校备注">
      <div class="review-last">${last ? `最近：${esc(last.reviewer || '教师')}，${esc(fmtDateTime(last.created_at))}` : '尚无审校记录'}</div>
    </div>`;
  }

  function renderRelation(rel, direction) {
    const q = rel.question || {};
    const relLabel = REL_TYPES[rel.rel_type] || rel.rel_type || '';
    const target = q.id || rel.to_id || rel.from_id || '';
    return `<div class="rel">
      <div class="rel-head">
        ${badge(relLabel, 'info')}
        <span class="arrow">${direction === 'in' ? '由' : ''}</span>
        ${target ? `<a href="#/question/${esc(target)}" class="mono" style="font-size:12px;">${esc(target)}</a>` : ''}
        <span style="font-size:12.5px;">${esc(q.short_name || q.stem_preview || '')}</span>
        ${direction === 'in' && !['same_method', 'error_contrast'].includes(rel.rel_type) ? '<span class="faint" style="font-size:11px;">指向本题</span>' : ''}
        ${['same_method', 'error_contrast'].includes(rel.rel_type) ? '<span class="faint" style="font-size:11px;">双向</span>' : ''}
        ${rel.confirmed ? badge('已确认', 'pass') : badge('待确认', 'pending')}
        ${!rel.confirmed ? `<button type="button" class="btn-link" data-confirm-rel="${esc(rel.id)}">确认关系</button>` : ''}
        <button type="button" class="btn-link danger" data-del-rel="${esc(rel.id)}">删除</button>
      </div>
      ${rel.note ? `<div class="rel-note">${esc(rel.note)}</div>` : '<div class="rel-note faint">未填写变化说明</div>'}
    </div>`;
  }

  function paint() {
    const stem = String(current.stem || '');
    const options = asArray(current.options);
    const answerKeys = asKeys(current.answer_keys);
    const keySet = new Set(answerKeys.map((key) => String(key).toUpperCase()));
    const subquestions = asArray(current.subquestions);
    const solutions = asArray(current.solutions);
    const teaching = asObject(current.teaching);
    const tags = asArray(current.tags).map((tag) => (
      typeof tag === 'string' ? { id: tag, name: tag, dimension: '' } : tag
    ));
    const statusLabel = question.status === 'inactive' ? '已停用' : '启用';
    const versionNo = current.version_no ?? 1;
    const answerText = String(current.answer || '');
    const teachRows = [
      ['why', '为什么想到'],
      ['key_step', '关键突破'],
      ['probe', '追问'],
      ['pitfalls', '易错点'],
      ['prereq', '前置知识'],
    ].filter(([key]) => teaching[key]);

    // 对称关系在后端存两行（正向 + 反向），界面上合并为一条。
    const symmetric = new Set(['same_method', 'error_contrast']);
    const seenSymmetric = new Set();
    const relationRows = [];
    relations.out.forEach((rel) => {
      const other = rel.question?.id || '';
      if (symmetric.has(rel.rel_type)) seenSymmetric.add(`${rel.rel_type}:${other}`);
      relationRows.push({ rel, direction: 'out' });
    });
    relations.in.forEach((rel) => {
      const other = rel.question?.id || '';
      if (symmetric.has(rel.rel_type) && seenSymmetric.has(`${rel.rel_type}:${other}`)) return;
      relationRows.push({ rel, direction: 'in' });
    });

    root.innerHTML = `
      <div class="detail-top">
        <div>
          <div class="breadcrumb"><a href="#/search">题库检索</a> <span class="faint">/</span> <span class="mono">${esc(qid)}</span></div>
          <h1 class="detail-title">${esc(question.short_name || '未命名题目')}<span class="qid">${esc(qid)}</span></h1>
          <div class="detail-badges">
            ${badge(statusLabel, question.status === 'inactive' ? 'na' : 'neutral')}
            ${badge(QTYPES[current.qtype] || current.qtype || '题型待定', 'plain')}
            ${badge(`版本 v${esc(versionNo)}`, 'info')}
            ${question.rec_level ? badge(`推荐 ${esc(question.rec_level)}`, 'violet') : ''}
            ${badge(eligibility.usable ? '可选用' : '暂不可选用', eligibility.usable ? 'pass' : 'pending')}
            ${question.merged_into ? `<a href="#/question/${esc(question.merged_into)}" class="badge badge-ochre">已合并至 ${esc(question.merged_into)}</a>` : ''}
          </div>
          ${question.status === 'inactive' && question.status_reason ? `<div class="hint" style="margin-top:8px;">停用原因：${esc(question.status_reason)}</div>` : ''}
        </div>
        <div class="detail-actions">
            <a class="btn" href="#/ai?qid=${encodeURIComponent(question.id)}">AI 补全与复核</a>
          <button type="button" class="btn" data-act="new-version">新建版本</button>
          <button type="button" class="btn" data-act="corrections">纠错与处理记录</button>
          <button type="button" class="btn" data-act="diff">版本对比</button>
          ${question.status === 'inactive'
            ? '<button type="button" class="btn" data-act="activate">恢复启用</button>'
            : '<button type="button" class="btn btn-danger" data-act="deactivate">停用</button>'}
          <button type="button" class="btn btn-danger" data-act="merge">合并</button>
        </div>
      </div>

      <div class="detail-grid">
        <div class="evidence-col">
          <section class="card">
            <div class="card-head"><h2>原始证据</h2><span class="hint">${occurrences.length} 处出处</span></div>
            ${occurrences.length
              ? occurrences.map(renderOccurrence).join('')
              : `<div class="card-body">${emptyState('暂无出处', '整理题目时应补一个出处；来源不明时标为「待确认」，不要编造。')}</div>`}
          </section>
        </div>

        <div class="content-col">
          <section class="card q-headline">
            <div class="badges">
              ${badge(QTYPES[current.qtype] || '题型待定', 'plain')}
              ${badge(`第 ${esc(versionNo)} 版`, 'info')}
              ${current.review_state === 'draft' ? badge('草稿版本', 'pending') : ''}
              ${eligibility.usable ? badge('可用于正式题单', 'pass') : ''}
            </div>
            <div class="q-stem-full" data-rich>${esc(stem || '（题干为空）')}</div>
            ${options.length ? `<div class="opt-list">
              ${options.map((opt, index) => {
                const key = optionLetter(opt.key, index);
                const isAnswer = keySet.has(key) || keySet.has(String(opt.key || '').toUpperCase());
                return `<div class="opt ${isAnswer ? 'answer' : ''}">
                  <span class="k">${esc(key)}</span>
                  <span data-rich>${esc(opt.text || '')}</span>
                </div>`;
              }).join('')}
            </div>` : ''}
            ${subquestions.length ? `<ol class="subq-list">
              ${subquestions.map((sub, index) => {
                const depends = asKeys(sub.depends_on);
                return `<li class="subq">
                  <div><span class="subq-key">${esc(sub.key || index + 1)}</span><span data-rich>${esc(sub.text || '')}</span></div>
                  ${depends.length ? `<div class="subq-answer">依赖：${depends.map(esc).join('、')}</div>` : ''}
                  ${sub.answer ? `<div class="subq-answer">答：<b data-rich>${esc(sub.answer)}</b></div>` : ''}
                </li>`;
              }).join('')}
            </ol>` : ''}
            ${teaching.question_tail ? `<div class="block-text" data-rich>${esc(teaching.question_tail)}</div>` : ''}
          </section>

          <section class="card">
            <div class="block answer-block">
              <div class="block-label">答案</div>
              <div class="block-text" data-rich>${esc(answerText || (answerKeys.length ? answerKeys.join('、') : '答案待补'))}</div>
              ${answerKeys.length ? `<div class="hint" style="margin-top:6px;">正确选项：<span class="mono">${esc(answerKeys.join('、'))}</span></div>` : ''}
            </div>
            <div class="block">
              <div class="block-label">解析</div>
              ${current.analysis
                ? `<div class="block-text" data-rich>${esc(current.analysis)}</div>`
                : `<div class="hint">${solutions.length ? `整题解析未单独填写；本题有 ${solutions.length} 种解法，见下方。` : '整题解析待补。'}</div>`}
            </div>
            ${teachRows.length ? `<div class="block">
              <div class="block-label">教学说明</div>
              <dl class="teach-grid">
                ${teachRows.map(([key, label]) => `<dt>${label}</dt><dd data-rich>${esc(teaching[key])}</dd>`).join('')}
              </dl>
            </div>` : ''}
          </section>

          <section class="card">
            <div class="card-head"><h2>解法</h2><span class="hint">${solutions.length} 种</span></div>
            <div class="card-body">
              ${solutions.length
                ? solutions.map(renderSolution).join('')
                : '<div class="hint">暂无独立解法。可在新建版本时补充，或直接在解析中写完整过程。</div>'}
            </div>
          </section>
        </div>

        <div class="review-col">
          <section class="card review-panel">
            <div class="card-head"><h2>审校</h2><span class="hint">逐项记录</span></div>
            <div class="card-body">
              <div class="eligibility ${eligibility.usable ? 'ok' : ''}">
                <div class="eli-title"><span class="eli-dot"></span>${eligibility.usable ? '可选用' : '暂不可选用'}</div>
                ${Array.isArray(eligibility.missing) && eligibility.missing.length
                  ? `<ul class="eli-missing">${eligibility.missing.map((text) => `<li>${esc(text)}</li>`).join('')}</ul>`
                  : `<div class="hint" style="margin-top:6px;">${eligibility.usable ? '题面、答案与解析的审校均已完成。' : '缺项暂未列出，请逐项核对。'}</div>`}
              </div>
              ${current.id
                ? REVIEW_ITEMS.map(([item, label]) => renderReviewRow(item, label)).join('')
                : '<div class="card-body hint">当前版本尚未建立，无法登记审校。</div>'}
            </div>
          </section>
        </div>
      </div>

      <div class="detail-bottom">
        <section class="card">
          <div class="card-head">
            <h2>标签</h2>
            <button type="button" class="btn btn-sm" data-act="add-tag">添加标签</button>
          </div>
          <div class="card-body">
            ${tags.length ? tags.map((tag) => `<div class="tag-row">
              <div>
                <span class="tag-name">${esc(tag.name || tag.id)}</span>
                <span class="tag-dim">${esc(TAG_DIMENSIONS[tag.dimension] || tag.dimension || '')}</span>
                ${tag.role ? badge(tag.role === 'primary' ? '主要' : '辅助', 'plain') : ''}
              </div>
              <button type="button" class="btn-link danger" data-remove-tag="${esc(tag.id)}" data-role="${esc(tag.role || 'primary')}">移除</button>
            </div>`).join('') : '<div class="hint">还没有标签。知识点是检索与组题检查的基础，建议至少选一个主要知识点。</div>'}
          </div>
        </section>

        <section class="card">
          <div class="card-head"><h2>题目关系</h2><span class="hint">${relationRows.length} 条</span></div>
          <div class="card-body">
            ${relationRows.length
              ? `<div class="rel-list">
                  ${relationRows.map(({ rel, direction }) => renderRelation(rel, direction)).join('')}
                </div>`
              : '<div class="hint" style="margin-bottom:10px;">还没有关联题目。变式、同法异题等关系需要写清「改了什么、保留什么」。</div>'}
            <form class="rel-form" id="rel-form">
              <div class="inline-fields two">
                <label class="field"><span>目标题目 ID<span class="req">*</span></span>
                  <input type="text" name="to_id" placeholder="如 Q000002" autocomplete="off">
                </label>
                <label class="field"><span>关系类型</span>
                  <select name="rel_type">${Object.entries(REL_TYPES).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select>
                </label>
              </div>
              <label class="field"><span>变化说明<span class="req">*</span></span>
                <textarea name="note" rows="2" placeholder="改了什么、保留什么、训练目标是什么"></textarea>
              </label>
              <div class="row-actions">
                <button type="submit" class="btn btn-sm">建立关系</button>
                <span class="hint">对称关系会自动生成反向记录。</span>
              </div>
            </form>
          </div>
        </section>

        <section class="card full">
          <div class="card-head"><h2>使用历史</h2><span class="hint">${usage.length} 次</span></div>
          ${usage.length ? `<div class="table-wrap"><table class="table">
            <thead><tr><th>讲义</th><th>题单</th><th>班级、小组或学生</th><th>日期</th><th>用途</th></tr></thead>
            <tbody>${usage.map((row) => `<tr>
              <td class="mono">${esc(row.release_id || '—')}</td>
              <td>${esc(row.collection_title || '—')}</td>
              <td>${esc(row.audience || '—')}</td>
              <td class="num">${esc(fmtDate(row.used_on))}</td>
              <td>${esc(USAGE_PURPOSES[row.purpose] || row.purpose || '—')}</td>
            </tr>`).join('')}</tbody>
          </table></div>` : '<div class="card-body hint">还没有使用记录。登记讲义发给谁、何时使用后，可在这里查看。</div>'}
        </section>
      </div>
    `;

    upgradeMath(root);
    bindRelForm();
  }

  // ------------------------------------------------------------ 新建版本

  function openVersionModal() {
    const optionsText = asArray(current.options)
      .map((opt, index) => `${optionLetter(opt.key, index)}: ${opt.text || ''}`)
      .join('\n');
    const keysText = asKeys(current.answer_keys).join(', ');
    const isChoice = current.qtype === 'single' || current.qtype === 'multi';

    const modal = openModal({
      title: '新建版本',
      width: 'modal-wide',
      body: `<div class="modal-error" hidden></div>
        <p class="hint">以当前版本 v${esc(current.version_no ?? 1)} 为底，只保存你改动的字段。提交后旧版本与旧审校记录都会保留。</p>
        <div class="inline-fields two">
          <label class="field"><span>题型</span>
            <select data-f="qtype">${Object.entries(QTYPES).map(([value, label]) => `<option value="${value}" ${value === current.qtype ? 'selected' : ''}>${label}</option>`).join('')}</select>
          </label>
          <label class="field"><span>修订说明<span class="req">*</span></span>
            <input type="text" data-f="change_note" placeholder="如：修正答案中的端点，补上定义域条件">
          </label>
        </div>
        <label class="field"><span>题干</span><textarea data-f="stem" rows="6">${esc(current.stem || '')}</textarea></label>
        <label class="field" data-opt-wrap><span>选项（每行一个，格式 A: 文本）</span>
          <textarea data-f="options" rows="4" ${isChoice ? '' : 'disabled'}>${esc(optionsText)}</textarea>
        </label>
        <div class="inline-fields two" data-choice-only>
          <label class="field"><span>正确选项（逗号分隔）</span><input type="text" data-f="answer_keys" value="${esc(keysText)}"></label>
          <label class="field"><span>简明答案</span><input type="text" data-f="answer" value="${esc(current.answer || '')}"></label>
        </div>
        <div data-text-answer ${isChoice ? 'hidden' : ''}>
          <label class="field"><span>简明答案</span><textarea data-f="answer-text" rows="2">${esc(current.answer || '')}</textarea></label>
        </div>
        <label class="field"><span>解析</span><textarea data-f="analysis" rows="5">${esc(current.analysis || '')}</textarea></label>`,
      footer: `<button type="button" class="btn" data-close>取消</button>
               <button type="button" class="btn btn-primary" data-save>保存新版本</button>`,
    });

    const errEl = modal.body.querySelector('.modal-error');
    const qtypeSel = modal.body.querySelector('[data-f="qtype"]');
    const optWrap = modal.body.querySelector('[data-opt-wrap]');
    const choiceOnly = modal.body.querySelector('[data-choice-only]');
    const textAnswer = modal.body.querySelector('[data-text-answer]');

    const syncType = () => {
      const choice = qtypeSel.value === 'single' || qtypeSel.value === 'multi';
      modal.body.querySelector('[data-f="options"]').disabled = !choice;
      optWrap.hidden = !choice;
      choiceOnly.hidden = !choice;
      textAnswer.hidden = choice || qtypeSel.value === '';
    };
    qtypeSel.addEventListener('change', syncType);
    syncType();

    modal.foot.querySelector('[data-save]').addEventListener('click', async (event) => {
      const button = event.currentTarget;
      await runSubmit(button, errEl, async () => {
        const qtype = qtypeSel.value;
        const choice = qtype === 'single' || qtype === 'multi';
        const changeNote = modal.body.querySelector('[data-f="change_note"]').value.trim();
        if (!changeNote) throw new Error('请填写修订说明，便于以后回查改了什么。');

        const parsedOptions = choice
          ? modal.body.querySelector('[data-f="options"]').value
            .split('\n').map((line) => line.trim()).filter(Boolean)
            .map((line, index) => {
              const m = line.match(/^([A-Za-z])[:：]\s*(.*)$/);
              return m ? { key: m[1].toUpperCase(), text: m[2] } : { key: String.fromCharCode(65 + index), text: line };
            })
          : null;
        if (choice && parsedOptions.length < 2) throw new Error('选择题至少需要两个选项。');
        const keys = choice
          ? modal.body.querySelector('[data-f="answer_keys"]').value.split(/[,，、\s]+/).filter(Boolean).map((k) => k.toUpperCase())
          : null;
        const answer = choice
          ? modal.body.querySelector('[data-f="answer"]').value.trim()
          : modal.body.querySelector('[data-f="answer-text"]').value.trim();
        const stem = modal.body.querySelector('[data-f="stem"]').value.trim();
        if (!stem) throw new Error('题干不能为空。');
        const analysis = modal.body.querySelector('[data-f="analysis"]').value.trim();

        const next = { qtype, stem, options: parsedOptions, answer_keys: keys, answer, analysis };
        const base = {
          qtype: current.qtype,
          stem: current.stem || '',
          options: asArray(current.options).length ? asArray(current.options) : null,
          answer_keys: asKeys(current.answer_keys).length ? asKeys(current.answer_keys) : null,
          answer: current.answer || '',
          analysis: current.analysis || '',
        };
        const payload = { change_note: changeNote };
        Object.keys(next).forEach((key) => {
          if (JSON.stringify(next[key] ?? null) !== JSON.stringify(base[key] ?? null)) {
            payload[key] = next[key];
          }
        });
        if (Object.keys(payload).length === 1) {
          throw new Error('内容没有变化，无需新建版本。');
        }
        await api(`/api/questions/${encodeURIComponent(qid)}/versions`, { method: 'POST', body: payload });
        modal.close();
        flash(`已保存为第 ${(current.version_no ?? 1) + 1} 版。`, 'ok');
        const y = window.scrollY;
        await load({ silent: true });
        window.scrollTo(0, y);
        refreshMeta();
      });
    });
  }

  // ------------------------------------------------------------ 版本对比

  function normalizeDiff(data) {
    if (!data) return [];
    const list = data.fields || data.changes || data.diff || data.items;
    if (Array.isArray(list)) {
      return list.map((row) => ({
        field: row.field || row.name || row.key,
        from: row.from ?? row.old ?? row.a,
        to: row.to ?? row.new ?? row.b,
        changed: row.changed,
      }));
    }
    if (Array.isArray(data)) {
      return data.map((row) => ({
        field: row.field || row.name || row.key,
        from: row.from ?? row.old ?? row.a,
        to: row.to ?? row.new ?? row.b,
        changed: row.changed,
      }));
    }
    if (data.from && data.to && typeof data.from === 'object' && typeof data.to === 'object') {
      const keys = new Set([...Object.keys(data.from), ...Object.keys(data.to)]);
      return [...keys].map((key) => ({ field: key, from: data.from[key], to: data.to[key] }));
    }
    if (typeof data === 'object') {
      return Object.entries(data)
        .filter(([key]) => !['from', 'to', 'from_no', 'to_no', 'question_id'].includes(key))
        .map(([key, value]) => {
          if (value && typeof value === 'object' && ('from' in value || 'to' in value)) {
            return { field: key, from: value.from, to: value.to, changed: value.changed };
          }
          if (Array.isArray(value) && value.length === 2) {
            return { field: key, from: value[0], to: value[1] };
          }
          return null;
        })
        .filter(Boolean);
    }
    return [];
  }

  function openDiffModal() {
    if (!versions.length) {
      flash('还没有版本记录，无法对比。');
      return;
    }
    const sorted = [...versions].sort((a, b) => Number(a.version_no) - Number(b.version_no));
    if (sorted.length < 2) {
      flash('当前只有一个版本，暂无可对比内容。');
      return;
    }
    const latest = sorted[sorted.length - 1];
    const prev = sorted[sorted.length - 2];

    const modal = openModal({
      title: '版本对比',
      width: 'modal-wide',
      body: `<div class="diff-picker">
          <label class="field" style="width:170px;"><span>旧版本</span>
            <select data-from>${sorted.map((v) => `<option value="${esc(v.version_no)}" ${v.version_no === prev.version_no ? 'selected' : ''}>v${esc(v.version_no)}，${esc(fmtDate(v.created_at))}</option>`).join('')}</select>
          </label>
          <span class="faint" style="padding-bottom:7px;">→</span>
          <label class="field" style="width:170px;"><span>新版本</span>
            <select data-to>${sorted.map((v) => `<option value="${esc(v.version_no)}" ${v.version_no === latest.version_no ? 'selected' : ''}>v${esc(v.version_no)}，${esc(fmtDate(v.created_at))}</option>`).join('')}</select>
          </label>
        </div>
        <div data-diff-result>${loading('正在比较…')}</div>`,
    });

    const fromSel = modal.body.querySelector('[data-from]');
    const toSel = modal.body.querySelector('[data-to]');
    const resultEl = modal.body.querySelector('[data-diff-result]');

    const renderDiff = (rows) => {
      if (!rows.length) {
        resultEl.innerHTML = '<div class="diff-empty">两个版本在这些字段上没有差异。</div>';
        return;
      }
      const view = rows.map((row) => {
        const changed = row.changed !== undefined
          ? Boolean(row.changed)
          : JSON.stringify(row.from ?? null) !== JSON.stringify(row.to ?? null);
        return `<tr class="${changed ? 'changed' : ''}">
          <td class="field-name">${esc(FIELD_LABELS[row.field] || row.field || '')}</td>
          <td class="val">${esc(serialize(row.from))}</td>
          <td class="val">${esc(serialize(row.to))}</td>
        </tr>`;
      }).join('');
      resultEl.innerHTML = `<div class="table-wrap"><table class="table diff-table">
        <thead><tr><th>字段</th><th>v${esc(fromSel.value)}</th><th>v${esc(toSel.value)}</th></tr></thead>
        <tbody>${view}</tbody>
      </table></div>`;
    };

    const run = async () => {
      resultEl.innerHTML = loading('正在比较…');
      try {
        const data = await api(`/api/questions/${encodeURIComponent(qid)}/diff`, {
          query: { from: fromSel.value, to: toSel.value },
        });
        renderDiff(normalizeDiff(data));
      } catch (err) {
        flash(err.message);
        resultEl.innerHTML = `<div class="modal-error">${esc(err.message)}</div>`;
      }
    };

    fromSel.addEventListener('change', run);
    toSel.addEventListener('change', run);
    run();
  }

  // ------------------------------------------------------------ 关系表单

  function bindRelForm() {
    const form = $('#rel-form', root);
    if (!form) return;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const toId = String(data.get('to_id') || '').trim().toUpperCase();
      const relType = String(data.get('rel_type') || '');
      const note = String(data.get('note') || '').trim();
      if (!toId) {
        flash('请填写目标题目 ID。');
        form.querySelector('[name="to_id"]').focus();
        return;
      }
      if (toId === qid) {
        flash('题目不能与自己建立关系。');
        return;
      }
      if (!note) {
        flash('请写清变化说明：改了什么、保留什么、训练目标是什么。');
        form.querySelector('[name="note"]').focus();
        return;
      }
      const btn = form.querySelector('button[type="submit"]');
      btn.disabled = true;
      btn.textContent = '建立中…';
      try {
        await api('/api/relations', {
          method: 'POST',
          body: { from_id: qid, to_id: toId, rel_type: relType, note },
        });
        flash('关系已建立。', 'ok');
        const y = window.scrollY;
        await load({ silent: true });
        window.scrollTo(0, y);
      } catch (err) {
        flash(err.message);
        btn.disabled = false;
        btn.textContent = '建立关系';
      }
    });
  }

  // ------------------------------------------------------------ 事件

  root.addEventListener('click', async (event) => {
    const reviewBtn = event.target.closest('[data-review]');
    if (reviewBtn) {
      const item = reviewBtn.dataset.review;
      const row = reviewBtn.closest('.review-row');
      const result = row.querySelector('select').value;
      const note = row.querySelector('[data-review-note]').value.trim();
      if (!current.id) {
        flash('当前版本尚未建立，无法登记审校。');
        return;
      }
      reviewBtn.disabled = true;
      reviewBtn.textContent = '提交中…';
      try {
        // 「解析」项按具体解法记录；其余项目记在当前版本上。
        const solutionList = asArray(current.solutions);
        const targets = (item === 'solution' && solutionList.length)
          ? solutionList.map((sol) => ({ target_type: 'solution', target_id: sol.id }))
          : [{ target_type: 'question_version', target_id: current.id }];
        for (const target of targets) {
          await api('/api/reviews', {
            method: 'POST',
            body: {
              target_type: target.target_type,
              target_id: target.target_id,
              item,
              result,
              note: note || null,
              reviewer: '教师',
            },
          });
        }
        flash(item === 'solution' && solutionList.length
          ? `解析审校已记录（${solutionList.length} 种解法）。`
          : '审校结论已记录。', 'ok');
        const y = window.scrollY;
        await load({ silent: true });
        window.scrollTo(0, y);
      } catch (err) {
        flash(err.message);
        reviewBtn.disabled = false;
        reviewBtn.textContent = '提交';
      }
      return;
    }

    const tagBtn = event.target.closest('[data-remove-tag]');
    if (tagBtn) {
      const tagId = tagBtn.dataset.removeTag;
      const role = tagBtn.dataset.role || 'primary';
      tagBtn.disabled = true;
      try {
        await api(`/api/questions/${encodeURIComponent(qid)}/tags/${encodeURIComponent(tagId)}`, {
          method: 'DELETE',
          query: { role },
        });
        flash('标签已移除。', 'ok');
        const y = window.scrollY;
        await load({ silent: true });
        window.scrollTo(0, y);
      } catch (err) {
        flash(err.message);
        tagBtn.disabled = false;
      }
      return;
    }

    const confirmRel = event.target.closest('[data-confirm-rel]');
    if (confirmRel) {
      confirmRel.disabled = true;
      try {
        await api(`/api/relations/${encodeURIComponent(confirmRel.dataset.confirmRel)}`, {
          method: 'PATCH',
          body: { confirmed: 1 },
        });
        flash('关系已确认。', 'ok');
        const y = window.scrollY;
        await load({ silent: true });
        window.scrollTo(0, y);
      } catch (err) {
        flash(err.message);
        confirmRel.disabled = false;
      }
      return;
    }

    const delRel = event.target.closest('[data-del-rel]');
    if (delRel) {
      const ok = await confirmDialog({
        title: '删除关系',
        message: '删除后不再显示这条关联。题目本身不会被删除。',
        confirmText: '删除',
        danger: true,
      });
      if (!ok) return;
      try {
        await api(`/api/relations/${encodeURIComponent(delRel.dataset.delRel)}`, { method: 'DELETE' });
        flash('关系已删除。', 'ok');
        const y = window.scrollY;
        await load({ silent: true });
        window.scrollTo(0, y);
      } catch (err) {
        flash(err.message);
      }
      return;
    }

    const act = event.target.closest('[data-act]')?.dataset.act;
    if (!act) return;

    if (act === 'new-version') {
      openVersionModal();
      return;
    }
    if (act === 'corrections') {
      const modal = openCorrections({questionId: qid, versionId: current.id, onEdit: openVersionModal});
      ctx.onCleanup?.(modal.close);
      return;
    }
    if (act === 'diff') {
      openDiffModal();
      return;
    }

    if (act === 'deactivate') {
      const reason = await textDialog({
        title: '停用题目',
        label: '停用原因',
        multiline: true,
        placeholder: '如：题干条件有误，等待修订；本题不再使用。',
        confirmText: '停用',
        hint: '停用不会删除题目与历史版本，可随时恢复。',
      });
      if (reason === null) return;
      try {
        await api(`/api/questions/${encodeURIComponent(qid)}/deactivate`, {
          method: 'POST',
          body: { reason },
        });
        flash('题目已停用。', 'ok');
        await load();
        refreshMeta();
      } catch (err) {
        flash(err.message);
      }
      return;
    }

    if (act === 'activate') {
      const ok = await confirmDialog({
        title: '恢复启用',
        message: '恢复后本题会重新出现在检索结果中（仍受审校状态限制）。',
        confirmText: '恢复启用',
      });
      if (!ok) return;
      try {
        await api(`/api/questions/${encodeURIComponent(qid)}/activate`, { method: 'POST' });
        flash('题目已恢复启用。', 'ok');
        await load();
        refreshMeta();
      } catch (err) {
        flash(err.message);
      }
      return;
    }

    if (act === 'merge') {
      openMergeModal();
      return;
    }

    if (act === 'add-tag') {
      await addTags();
    }
  });

  async function addTags() {
    const { data, error } = await apiOptional('/api/tags');
    if (error) {
      flash(error.message);
      return;
    }
    const tags = asArray(data?.items);
    const existing = new Set(asArray(current.tags).map((tag) => (typeof tag === 'string' ? tag : tag.id)));
    const picked = await pickTags({
      tags,
      selected: [...existing],
      multiple: true,
      title: '为题目添加标签',
      confirmText: '添加',
    });
    if (picked === null) return;
    const fresh = picked.filter((tag) => !existing.has(tag.id));
    if (!fresh.length) {
      flash('没有新增标签。', 'ok');
      return;
    }
    try {
      for (const tag of fresh) {
        await api(`/api/questions/${encodeURIComponent(qid)}/tags`, {
          method: 'POST',
          body: { tag_id: tag.id, role: 'primary' },
        });
      }
      flash(`已添加 ${fresh.length} 个标签。`, 'ok');
      const y = window.scrollY;
      await load({ silent: true });
      window.scrollTo(0, y);
    } catch (err) {
      flash(err.message);
    }
  }

  function openMergeModal() {
    const modal = openModal({
      title: '合并重复题',
      body: `<div class="modal-error" hidden></div>
        <p class="hint">把本题并入另一道题。旧 ID 会保留跳转，出处、版本与合并记录都不会丢失。</p>
        <label class="field"><span>并入题目 ID<span class="req">*</span></span>
          <input type="text" data-target placeholder="如 Q000002" autocomplete="off">
        </label>
        <label class="field"><span>合并说明<span class="req">*</span></span>
          <textarea data-note rows="3" placeholder="如：与 Q000002 为同一道题，仅排版不同。"></textarea>
        </label>`,
      footer: `<button type="button" class="btn" data-close>取消</button>
               <button type="button" class="btn btn-primary" data-save>确认合并</button>`,
    });
    const errEl = modal.body.querySelector('.modal-error');
    modal.foot.querySelector('[data-save]').addEventListener('click', async (event) => {
      const target = modal.body.querySelector('[data-target]').value.trim().toUpperCase();
      const note = modal.body.querySelector('[data-note]').value.trim();
      await runSubmit(event.currentTarget, errEl, async () => {
        if (!target) throw new Error('请填写要并入的题目 ID。');
        if (target === qid) throw new Error('不能并入自己。');
        if (!note) throw new Error('请写清合并依据。');
        await api(`/api/questions/${encodeURIComponent(qid)}/merge`, {
          method: 'POST',
          body: { into: target, note },
        });
        modal.close();
        flashAfterNavigation(`已合并至 ${target}。`, 'ok');
        location.hash = `#/question/${encodeURIComponent(target)}`;
        refreshMeta();
      });
    });
  }

  await load();
  if (ctx.current() && current.id && ctx.query.edit === '1') openVersionModal();
}
