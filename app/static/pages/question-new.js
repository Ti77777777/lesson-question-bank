/* 新建题目：按题型切换选项、答案与小问区域；可多解、多出处、标签与三维难度。 */

import { api, apiOptional } from '../lib/api.js';
import { esc, $, $$, optionLetter } from '../lib/dom.js';
import {
  QTYPES, REC_LEVELS,
} from '../lib/constants.js';
import { flash, flashAfterNavigation, pickTags } from '../lib/ui.js';
import { refreshMeta } from '../lib/bus.js';
import { upgradeMath } from '../lib/math.js';

function intOrNull(value) {
  if (value === '' || value === null || value === undefined) return null;
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? n : null;
}

function floatOrNull(value) {
  if (value === '' || value === null || value === undefined) return null;
  const n = Number.parseFloat(value);
  return Number.isFinite(n) ? n : null;
}

function splitKeys(text) {
  return String(text || '').split(/[,，、\s]+/).map((s) => s.trim()).filter(Boolean);
}

export default async function mount(root, ctx) {
  const inboxId = ctx.query.inbox || '';
  let inbox = null;

  root.innerHTML = '<div class="loading">正在准备表单…</div>';

  if (inboxId) {
    try {
      inbox = await api(`/api/inbox/${encodeURIComponent(inboxId)}`);
    } catch (err) {
      flash(`读取收题条目失败：${err.message}`);
    }
    if (inbox?.question_id) {
      flashAfterNavigation(`这条收题记录已整理为 ${inbox.question_id}。`, 'ok');
      location.hash = `#/question/${encodeURIComponent(inbox.question_id)}`;
      return;
    }
  }

  const [tagRes, sourceRes] = await Promise.all([
    apiOptional('/api/tags'),
    apiOptional('/api/sources'),
  ]);
  if (!ctx.current()) return;

  const allTags = Array.isArray(tagRes.data?.items) ? tagRes.data.items : [];
  const methodTags = allTags.filter((tag) => tag.dimension === 'method');
  const sources = Array.isArray(sourceRes.data?.items) ? sourceRes.data.items : [];
  const tagError = tagRes.error?.message || '';
  const sourceError = sourceRes.error?.message || '';

  let qtype = 'solve';
  let answerKeys = new Set();
  let tags = [];
  let options = [
    { key: 'A', text: '' },
    { key: 'B', text: '' },
    { key: 'C', text: '' },
    { key: 'D', text: '' },
  ];
  let subquestions = [];
  let solutions = [{ label: '', approach: '', steps: '', conditions: '', is_primary: true, method_tags: [] }];
  const prefillText = inbox?.raw_text ? String(inbox.raw_text) : '';
  const prefillHint = inbox?.source_hint || '待确认';
  let occurrences = [{
    source_id: '',
    pdf_page: '',
    print_page: '',
    qno: '',
    raw_text: prefillText,
    pending: inbox ? 1 : 0,
    note: inbox ? `来源线索：${prefillHint}` : '',
  }];

  root.innerHTML = `
    <div class="newq-wrap">
      <div class="page-head">
        <div>
          <h1 class="page-title">新建题目</h1>
          <p class="page-sub">整理稿与原始资料分开保存。题干、答案、解析先按数学内容如实录入，排版交给题单与模板。</p>
        </div>
        <div class="page-head-actions">
          <a class="btn" href="${inboxId ? '#/inbox' : '#/search'}">取消</a>
        </div>
      </div>

      ${inbox ? `<div class="prefill-note" style="margin-bottom:16px;">
        来自收题箱 <b class="mono">${esc(inbox.id)}</b>。原文已填入题干与出处原文，出处标记为「待确认」（来源线索：${esc(prefillHint)}）。保存后该收题条目会置为已整理。
      </div>` : ''}

      ${tagError ? `<div class="prefill-note" style="margin-bottom:16px;border-color:#ecd5c7;background:#fbf5f1;color:#7b3c1f;">标签接口暂不可用：${esc(tagError)}。仍可保存题目，标签稍后在详情页补。</div>` : ''}

      <form class="newq-form" id="newq-form" novalidate>
        <section class="card form-section">
          <div class="card-head"><h3>基本信息</h3><span class="hint">题目 ID 与版本由系统分配</span></div>
          <div class="card-body">
            <div class="inline-fields two">
              <label class="field"><span>简短名称</span>
                <input type="text" name="short_name" placeholder="如 含参二次方程根的分布">
                <span class="field-hint">用于列表与题单，不必与题干完全相同。</span>
              </label>
              <div class="field">
                <span>题型</span>
                <div class="qtype-switch" id="qtype-switch">
                  ${Object.entries(QTYPES).map(([value, label]) => `<button type="button" data-qtype="${value}" aria-pressed="${value === qtype}">${label}</button>`).join('')}
                </div>
              </div>
            </div>
            <label class="field"><span>题干<span class="req">*</span></span>
              <textarea name="stem" rows="7" placeholder="已知 $a\\in\\mathbb{R}$，方程 $x^2-2ax+1=0$。">${esc(prefillText)}</textarea>
              <span class="field-hint">公式用 $...$ 写 LaTeX；换行会保留。</span>
            </label>
          </div>
        </section>

        <section class="card form-section" id="sec-options">
          <div class="card-head">
            <h3>选项</h3>
            <button type="button" class="btn btn-sm" data-add-option>添加选项</button>
          </div>
          <div class="card-body">
            <div class="option-editor" id="option-list"></div>
            <div>
              <span class="field-label">正确选项</span>
              <div class="chips" id="answer-key-list"></div>
              <span class="field-hint">单选点一个；多选可点多个。选项标识稳定不变，显示时映射为 A/B/C/D。</span>
            </div>
          </div>
        </section>

        <section class="card form-section" id="sec-answer">
          <div class="card-head"><h3>答案</h3><span class="hint">答案与解析分开保存</span></div>
          <div class="card-body">
            <div id="answer-choice" class="hidden">
              <p class="hint">选择题的答案取上方勾选的正确选项，可在下方补充简答。</p>
            </div>
            <label class="field" id="answer-text-wrap"><span>简明答案</span>
              <textarea name="answer" rows="2" placeholder="如 $a<-1$ 或 $a>1$；多小问可按小问分行。"></textarea>
            </label>
            <label class="field"><span>整题解析</span>
              <textarea name="analysis" rows="5" placeholder="未拆分独立解法时，把完整过程写在这里。"></textarea>
            </label>
          </div>
        </section>

        <section class="card form-section" id="sec-subq">
          <div class="card-head">
            <h3>小问</h3>
            <button type="button" class="btn btn-sm" data-add-subq>添加小问</button>
          </div>
          <div class="card-body">
            <div class="sub-editor" id="subq-list"></div>
            <span class="field-hint">拆用小问进入题单时会检查依赖，请如实填写小问依赖。</span>
          </div>
        </section>

        <section class="card form-section">
          <div class="card-head"><h3>教学说明</h3><span class="hint">可按教师版配置显示</span></div>
          <div class="card-body">
            <div class="inline-fields two">
              <label class="field"><span>为什么想到</span><textarea name="teach_why" rows="2" placeholder="观察到的结构 → 可尝试的方法"></textarea></label>
              <label class="field"><span>关键突破</span><textarea name="teach_key_step" rows="2" placeholder="本题最关键的一步"></textarea></label>
              <label class="field"><span>追问</span><textarea name="teach_probe" rows="2" placeholder="可以继续问学生什么"></textarea></label>
              <label class="field"><span>易错点</span><textarea name="teach_pitfalls" rows="2" placeholder="如忽略定义域、漏掉等号"></textarea></label>
            </div>
            <label class="field"><span>前置知识</span><input type="text" name="teach_prereq" placeholder="如 判别式、韦达定理、定义域"></label>
          </div>
        </section>

        <section class="card form-section">
          <div class="card-head">
            <h3>解法</h3>
            <button type="button" class="btn btn-sm" data-add-solution>添加解法</button>
          </div>
          <div class="card-body">
            <div class="sol-editor" id="solution-list"></div>
            <span class="field-hint">方法标签标在具体解法上，检索时按方法找题会命中对应解法。</span>
          </div>
        </section>

        <section class="card form-section">
          <div class="card-head"><h3>分类、难度与推荐</h3><span class="hint">三维难度各自独立</span></div>
          <div class="card-body">
            <div>
              <span class="field-label">标签</span>
              <div class="chips" id="tag-chips"></div>
              <div class="row-actions" style="margin-top:8px;">
                <button type="button" class="btn btn-sm" id="add-tags">选择标签</button>
                <span class="hint">至少选一个主要知识点；点击「主要/辅助」可切换。</span>
              </div>
            </div>
            <div class="inline-fields four">
              <label class="field"><span>计算难度</span><select name="difficulty_calc">${levelOptions()}</select></label>
              <label class="field"><span>思维难度</span><select name="difficulty_think">${levelOptions()}</select></label>
              <label class="field"><span>综合程度</span><select name="difficulty_scope">${levelOptions()}</select></label>
              <label class="field"><span>适用层次</span><input type="text" name="difficulty_level" placeholder="如 高一提高"></label>
            </div>
            <label class="field"><span>难度理由</span><input type="text" name="difficulty_note" placeholder="简短说明判断依据"></label>
            <div class="inline-fields four">
              <label class="field"><span>推荐等级</span>
                <select name="rec_level"><option value="">未评</option>${REC_LEVELS.map((level) => `<option value="${level}">${level}</option>`).join('')}</select>
              </label>
              <label class="field"><span>独立作答（分钟）</span><input type="number" name="time_solve_min" min="0" step="0.5" placeholder="5"></label>
              <label class="field"><span>预计讲解（分钟）</span><input type="number" name="time_explain_min" min="0" step="0.5" placeholder="8"></label>
              <label class="field"><span>推荐理由</span><input type="text" name="rec_reason" placeholder="如 边界比较典型"></label>
            </div>
          </div>
        </section>

        <section class="card form-section">
          <div class="card-head">
            <h3>出处</h3>
            <button type="button" class="btn btn-sm" data-add-occ>再添一处出处</button>
          </div>
          <div class="card-body">
            ${sourceError ? `<div class="prefill-note" style="border-color:#ecd5c7;background:#fbf5f1;color:#7b3c1f;">资料接口暂不可用：${esc(sourceError)}。可先把出处标为待确认。</div>` : ''}
            <div class="occ-editor" id="occ-list"></div>
            <span class="field-hint">一题可关联多个出处；来源不明时保留「待确认」，不要编造资料名称。</span>
          </div>
        </section>

        <section class="card form-section">
          <div class="card-head"><h3>保存</h3></div>
          <div class="card-body">
            <label class="field"><span>修订说明</span>
              <input type="text" name="change_note" placeholder="初次录入；如依据原卷整理">
            </label>
          </div>
        </section>

        <div class="form-actions">
          <label class="check"><input type="checkbox" id="ai-after-save">保存后自动交给 AI 补全、分类与复核（本题预算上限 2 元，结果待确认）</label>
          <span class="left">${inbox ? '保存后收题条目会置为「已整理」' : '保存后进入题目详情，可继续审校与补标签'}</span>
          <div class="row-actions">
            <a class="btn" href="${inboxId ? '#/inbox' : '#/search'}">取消</a>
            <button type="submit" class="btn btn-primary btn-lg" id="save-question">${inbox ? '保存并整理' : '保存题目'}</button>
          </div>
        </div>
      </form>
    </div>
  `;

  const form = $('#newq-form', root);
  const optionList = $('#option-list', root);
  const keyList = $('#answer-key-list', root);
  const subqList = $('#subq-list', root);
  const solutionList = $('#solution-list', root);
  const occList = $('#occ-list', root);
  const tagChips = $('#tag-chips', root);

  function levelOptions() {
    return ['<option value="">未评</option>', ...[1, 2, 3, 4, 5].map((n) => `<option value="${n}">${n}</option>`)].join('');
  }

  // ------------------------------------------------------------ 选项与小问

  function renderOptions() {
    optionList.innerHTML = options.map((opt, index) => {
      const key = optionLetter(opt.key, index);
      return `<div class="option-edit-row">
        <span class="key">${esc(key)}</span>
        <input type="text" data-opt-index="${index}" data-opt-field="text" value="${esc(opt.text || '')}" placeholder="选项文本，可用 $...$">
        <button type="button" class="del" data-del-option="${index}" aria-label="删除选项 ${esc(key)}">×</button>
      </div>`;
    }).join('');
    renderAnswerKeys();
  }

  function renderAnswerKeys() {
    if (!options.length) {
      keyList.innerHTML = '<span class="hint">先添加选项。</span>';
      return;
    }
    keyList.innerHTML = options.map((opt, index) => {
      const key = optionLetter(opt.key, index);
      return `<button type="button" class="chip ${answerKeys.has(key) ? 'on' : ''}" data-answer-key="${key}" aria-pressed="${answerKeys.has(key)}">${key}</button>`;
    }).join('');
  }

  function renderSubquestions() {
    if (!subquestions.length) {
      subqList.innerHTML = '<div class="hint">暂无小问。整题使用时可留空。</div>';
      return;
    }
    subqList.innerHTML = subquestions.map((sub, index) => `<div class="sub-edit">
      <div class="sub-edit-head">
        <span class="review-name">小问 <span class="mono">${index + 1}</span></span>
        <button type="button" class="btn-link danger" data-del-subq="${index}">删除</button>
      </div>
      <label class="field"><span>小问文字</span>
        <textarea rows="2" data-sub-index="${index}" data-sub-field="text" placeholder="如：求方程有两个不相等正实根时 $a$ 的范围。">${esc(sub.text || '')}</textarea>
      </label>
      <div class="inline-fields two">
        <label class="field"><span>小问答案</span><input type="text" data-sub-index="${index}" data-sub-field="answer" value="${esc(sub.answer || '')}" placeholder="可留空，稍后补"></label>
        <label class="field"><span>依赖小问（逗号分隔）</span><input type="text" data-sub-index="${index}" data-sub-field="depends_on" value="${esc(sub.depends_on || '')}" placeholder="如 1,2"></label>
      </div>
    </div>`).join('');
  }

  // ------------------------------------------------------------ 解法

  function renderSolutions() {
    solutionList.innerHTML = solutions.map((sol, index) => `<div class="sol-edit">
      <div class="sub-edit-head">
        <span class="review-name">解法 <span class="mono">${index + 1}</span></span>
        <div class="row-actions">
          <label class="check"><input type="radio" name="primary_solution" data-sol-primary="${index}" ${sol.is_primary ? 'checked' : ''}> 选用解法</label>
          <button type="button" class="btn-link danger" data-del-sol="${index}">删除</button>
        </div>
      </div>
      <div class="inline-fields two">
        <label class="field"><span>解法名称</span><input type="text" data-sol-index="${index}" data-sol-field="label" value="${esc(sol.label || '')}" placeholder="如 判别式法 / 解法一"></label>
        <label class="field"><span>适用条件</span><input type="text" data-sol-index="${index}" data-sol-field="conditions" value="${esc(sol.conditions || '')}" placeholder="如 二次项系数不为零"></label>
      </div>
      <label class="field"><span>思路</span><input type="text" data-sol-index="${index}" data-sol-field="approach" value="${esc(sol.approach || '')}" placeholder="一句话说明为什么这样想"></label>
      <label class="field"><span>详细步骤<span class="req">*</span></span>
        <textarea rows="5" data-sol-index="${index}" data-sol-field="steps" placeholder="完整过程，公式用 $...$">${esc(sol.steps || '')}</textarea>
      </label>
      <div>
        <span class="field-label">方法标签</span>
        ${methodTags.length
          ? `<div class="chips">${methodTags.map((tag) => `<button type="button" class="chip violet ${sol.method_tags.includes(tag.id) ? 'on' : ''}" data-sol-tag="${tag.id}" data-sol-tag-index="${index}">${esc(tag.name)}</button>`).join('')}</div>`
          : '<span class="hint">方法标签库为空或暂不可用，可稍后在详情页补。</span>'}
      </div>
    </div>`).join('');
  }

  // ------------------------------------------------------------ 出处

  function renderOccurrences() {
    occList.innerHTML = occurrences.map((occ, index) => `<div class="occ-edit">
      <div class="sub-edit-head">
        <span class="review-name">出处 <span class="mono">${index + 1}</span></span>
        ${occurrences.length > 1 ? `<button type="button" class="btn-link danger" data-del-occ="${index}">删除</button>` : ''}
      </div>
      <div class="inline-fields two">
        <label class="field"><span>资料</span>
          <select data-occ-index="${index}" data-occ-field="source_id">
            <option value="">（不关联资料，标为待确认）</option>
            ${sources.map((src) => `<option value="${esc(src.id)}" ${src.id === occ.source_id ? 'selected' : ''}>${esc(src.name)}（${esc(src.id)}）</option>`).join('')}
          </select>
        </label>
        <label class="field"><span>原题号</span><input type="text" data-occ-index="${index}" data-occ-field="qno" value="${esc(occ.qno || '')}" placeholder="如 8"></label>
      </div>
      <div class="inline-fields three">
        <label class="field"><span>PDF 页序</span><input type="number" min="1" data-occ-index="${index}" data-occ-field="pdf_page" value="${esc(occ.pdf_page || '')}" placeholder="3"></label>
        <label class="field"><span>印刷页码</span><input type="text" data-occ-index="${index}" data-occ-field="print_page" value="${esc(occ.print_page || '')}" placeholder="12"></label>
        <label class="check" style="align-self:end;margin-bottom:6px;"><input type="checkbox" data-occ-index="${index}" data-occ-field="pending" ${occ.pending ? 'checked' : ''}> 出处待确认</label>
      </div>
      <label class="field"><span>原文 / OCR 结果</span>
        <textarea rows="3" data-occ-index="${index}" data-occ-field="raw_text" placeholder="照原样保留，便于核对识别错误">${esc(occ.raw_text || '')}</textarea>
      </label>
      <label class="field"><span>出处备注</span>
        <input type="text" data-occ-index="${index}" data-occ-field="note" value="${esc(occ.note || '')}" placeholder="如 来源线索、题号存疑">
      </label>
    </div>`).join('');
  }

  // ------------------------------------------------------------ 标签

  function renderTags() {
    if (!tags.length) {
      tagChips.innerHTML = '<span class="hint">未选择标签。知识点标签会影响检索与组题检查。</span>';
      return;
    }
    tagChips.innerHTML = tags.map((tag) => `<span class="chip on">${esc(tag.name)}
      <button type="button" class="x" data-tag-role="${esc(tag.id)}" title="切换主要/辅助">${tag.role === 'primary' ? '主要' : '辅助'}</button>
      <button type="button" class="x" data-tag-remove="${esc(tag.id)}" aria-label="移除 ${esc(tag.name)}">×</button>
    </span>`).join('');
  }

  // ------------------------------------------------------------ 题型切换

  function syncQtype() {
    const choice = qtype === 'single' || qtype === 'multi';
    const hasSub = qtype === 'blank' || qtype === 'solve';
    $('#sec-options', root).classList.toggle('hidden', !choice);
    $('#sec-subq', root).classList.toggle('hidden', !hasSub);
    $('#answer-choice', root).classList.toggle('hidden', !choice);
    $('#answer-text-wrap', root).classList.toggle('hidden', choice);
    $$('#qtype-switch button', root).forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.qtype === qtype));
    });
  }

  // ------------------------------------------------------------ 事件

  $('#qtype-switch', root).addEventListener('click', (event) => {
    const btn = event.target.closest('[data-qtype]');
    if (!btn) return;
    qtype = btn.dataset.qtype;
    syncQtype();
  });

  form.addEventListener('input', (event) => {
    const target = event.target;
    if (target.matches('[data-opt-index]')) {
      const index = Number(target.dataset.optIndex);
      if (options[index]) options[index].text = target.value;
    } else if (target.matches('[data-sub-index]')) {
      const index = Number(target.dataset.subIndex);
      if (subquestions[index]) subquestions[index][target.dataset.subField] = target.value;
    } else if (target.matches('[data-sol-index]')) {
      const index = Number(target.dataset.solIndex);
      if (solutions[index]) solutions[index][target.dataset.solField] = target.value;
    } else if (target.matches('[data-occ-index]')) {
      const index = Number(target.dataset.occIndex);
      if (occurrences[index]) occurrences[index][target.dataset.occField] = target.value;
    }
  });

  form.addEventListener('change', (event) => {
    const target = event.target;
    if (target.matches('[data-occ-field="pending"]')) {
      const index = Number(target.dataset.occIndex);
      if (occurrences[index]) occurrences[index].pending = target.checked ? 1 : 0;
    }
    if (target.matches('[data-occ-field="source_id"]')) {
      const index = Number(target.dataset.occIndex);
      if (occurrences[index]) occurrences[index].source_id = target.value;
    }
    if (target.matches('[data-sol-primary]')) {
      const index = Number(target.dataset.solPrimary);
      solutions = solutions.map((sol, i) => ({ ...sol, is_primary: i === index }));
    }
  });

  root.addEventListener('click', (event) => {
    if (event.target.closest('[data-add-option]')) {
      options.push({ key: '', text: '' });
      options = options.map((opt, index) => ({ ...opt, key: String.fromCharCode(65 + index) }));
      renderOptions();
      return;
    }
    const delOpt = event.target.closest('[data-del-option]');
    if (delOpt) {
      const index = Number(delOpt.dataset.delOption);
      options.splice(index, 1);
      options = options.map((opt, idx) => ({ ...opt, key: String.fromCharCode(65 + idx) }));
      answerKeys.clear();
      renderOptions();
      return;
    }
    const keyBtn = event.target.closest('[data-answer-key]');
    if (keyBtn) {
      const key = keyBtn.dataset.answerKey;
      if (answerKeys.has(key)) answerKeys.delete(key);
      else answerKeys.add(key);
      keyBtn.classList.toggle('on', answerKeys.has(key));
      keyBtn.setAttribute('aria-pressed', String(answerKeys.has(key)));
      return;
    }
    if (event.target.closest('[data-add-subq]')) {
      subquestions.push({ text: '', answer: '', depends_on: '' });
      renderSubquestions();
      return;
    }
    const delSub = event.target.closest('[data-del-subq]');
    if (delSub) {
      subquestions.splice(Number(delSub.dataset.delSubq), 1);
      renderSubquestions();
      return;
    }
    if (event.target.closest('[data-add-solution]')) {
      solutions.push({ label: '', approach: '', steps: '', conditions: '', is_primary: solutions.length === 0, method_tags: [] });
      renderSolutions();
      return;
    }
    const delSol = event.target.closest('[data-del-sol]');
    if (delSol) {
      solutions.splice(Number(delSol.dataset.delSol), 1);
      if (solutions.length && !solutions.some((sol) => sol.is_primary)) solutions[0].is_primary = true;
      renderSolutions();
      return;
    }
    const solTag = event.target.closest('[data-sol-tag]');
    if (solTag) {
      const index = Number(solTag.dataset.solTagIndex);
      const tagId = solTag.dataset.solTag;
      const sol = solutions[index];
      if (!sol) return;
      const set = new Set(sol.method_tags);
      if (set.has(tagId)) set.delete(tagId);
      else set.add(tagId);
      sol.method_tags = [...set];
      solTag.classList.toggle('on', set.has(tagId));
      return;
    }
    if (event.target.closest('[data-add-occ]')) {
      occurrences.push({
        source_id: '',
        pdf_page: '',
        print_page: '',
        qno: '',
        raw_text: '',
        pending: occurrences.every((occ) => !occ.source_id) ? 1 : 0,
        note: '',
      });
      renderOccurrences();
      return;
    }
    const delOcc = event.target.closest('[data-del-occ]');
    if (delOcc) {
      occurrences.splice(Number(delOcc.dataset.delOcc), 1);
      renderOccurrences();
      return;
    }
    const roleBtn = event.target.closest('[data-tag-role]');
    if (roleBtn) {
      const tag = tags.find((item) => item.id === roleBtn.dataset.tagRole);
      if (tag) {
        tag.role = tag.role === 'primary' ? 'secondary' : 'primary';
        renderTags();
      }
      return;
    }
    const removeTag = event.target.closest('[data-tag-remove]');
    if (removeTag) {
      tags = tags.filter((tag) => tag.id !== removeTag.dataset.tagRemove);
      renderTags();
    }
  });

  $('#add-tags', root).addEventListener('click', async () => {
    if (!allTags.length) {
      flash(tagError ? `标签接口暂不可用：${tagError}` : '标签库为空，无法选择标签。');
      return;
    }
    const picked = await pickTags({
      tags: allTags,
      selected: tags.map((tag) => tag.id),
      multiple: true,
      title: '为题目选择标签',
      confirmText: '添加',
    });
    if (picked === null) return;
    const existing = new Set(tags.map((tag) => tag.id));
    picked.forEach((tag) => {
      if (!existing.has(tag.id)) {
        tags.push({
          id: tag.id,
          name: tag.name,
          dimension: tag.dimension,
          role: tag.dimension === 'knowledge' ? 'primary' : 'secondary',
        });
      }
    });
    renderTags();
  });

  // ------------------------------------------------------------ 提交

  function collectOccurrence(occ) {
    const sourceId = occ.source_id || null;
    return {
      source_id: sourceId,
      pdf_page: intOrNull(occ.pdf_page),
      print_page: String(occ.print_page || '').trim() || null,
      qno: String(occ.qno || '').trim() || null,
      raw_text: String(occ.raw_text || '').trim() || null,
      pending: occ.pending ? 1 : (sourceId ? 0 : 1),
      note: String(occ.note || '').trim() || null,
    };
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = $('#save-question', root);
    const data = new FormData(form);
    const val = (name) => String(data.get(name) || '').trim();
    const stem = val('stem');
    if (!stem) {
      flash('题干不能为空。');
      form.querySelector('[name="stem"]').focus();
      return;
    }

    const choice = qtype === 'single' || qtype === 'multi';
    let optionPayload = null;
    if (choice) {
      const filled = options
        .map((opt, index) => ({
          key: optionLetter(opt.key, index),
          text: String(opt.text || '').trim(),
          index,
        }))
        .filter((opt) => opt.text);
      if (filled.length < 2) {
        flash('选择题至少需要两个有文字的选项。');
        return;
      }
      const lastFilled = Math.max(...filled.map((opt) => opt.index));
      if (options.some((opt, index) => index < lastFilled && !String(opt.text || '').trim())) {
        flash('中间有空选项；请补上文字，或先删除多余的空选项。');
        return;
      }
      optionPayload = filled.map(({ key, text }) => ({ key, text }));
      const available = new Set(optionPayload.map((opt) => opt.key));
      const staleKeys = [...answerKeys].filter((key) => !available.has(key));
      if (staleKeys.length) {
        flash(`正确选项 ${staleKeys.join('、')} 没有对应选项文字，请补全或取消勾选。`);
        return;
      }
      // 答案可以稍后补：这里不强制勾选，只在详情页显示「答案待补」。
    }

    const subPayload = (qtype === 'blank' || qtype === 'solve')
      ? subquestions
        .filter((sub) => String(sub.text || '').trim())
        .map((sub, index) => ({
          key: String(index + 1),
          text: String(sub.text || '').trim(),
          depends_on: splitKeys(sub.depends_on).filter((key) => Number(key) < index + 1),
          answer: String(sub.answer || '').trim() || null,
        }))
      : null;

    const solPayload = solutions
      .filter((sol) => String(sol.steps || '').trim())
      .map((sol, index) => ({
        label: String(sol.label || '').trim() || `解法${index + 1}`,
        approach: String(sol.approach || '').trim() || null,
        steps: String(sol.steps || '').trim(),
        conditions: String(sol.conditions || '').trim() || null,
        is_primary: sol.is_primary ? 1 : 0,
        method_tags: sol.method_tags,
      }));
    if (solPayload.length && !solPayload.some((sol) => sol.is_primary)) solPayload[0].is_primary = 1;

    const primaryOccurrence = occurrences.length ? collectOccurrence(occurrences[0]) : null;
    const extraOccurrences = occurrences.slice(1).map(collectOccurrence)
      .filter((occ) => occ.source_id || occ.raw_text || occ.qno || occ.pdf_page);

    const payload = {
      short_name: val('short_name') || null,
      qtype,
      stem,
      options: optionPayload,
      answer: val('answer') || (choice && answerKeys.size ? [...answerKeys].join('、') : null),
      answer_keys: choice ? [...answerKeys] : null,
      subquestions: subPayload && subPayload.length ? subPayload : null,
      analysis: val('analysis') || null,
      teaching: {
        why: val('teach_why'),
        key_step: val('teach_key_step'),
        probe: val('teach_probe'),
        pitfalls: val('teach_pitfalls'),
        prereq: val('teach_prereq'),
      },
      difficulty: {
        calc: intOrNull(val('difficulty_calc')),
        think: intOrNull(val('difficulty_think')),
        scope: intOrNull(val('difficulty_scope')),
        level: val('difficulty_level') || null,
        note: val('difficulty_note') || null,
      },
      rec_level: val('rec_level') || null,
      rec_reason: val('rec_reason') || null,
      time_solve_min: floatOrNull(val('time_solve_min')),
      time_explain_min: floatOrNull(val('time_explain_min')),
      tags: tags.map((tag) => ({ tag_id: tag.id, role: tag.role })),
      solutions: solPayload,
      change_note: val('change_note') || '初次录入',
    };
    // 没有出处时不要发 occurrence:null，后端会按「待确认」自动补一条；
    // 显式传 null 会被当成空对象并报「既没有资料也没有待确认」。
    if (primaryOccurrence && (primaryOccurrence.source_id || primaryOccurrence.raw_text || primaryOccurrence.pending)) {
      payload.occurrence = primaryOccurrence;
    }

    button.disabled = true;
    button.textContent = '保存中…';
    try {
      const path = inboxId
        ? `/api/inbox/${encodeURIComponent(inboxId)}/promote`
        : '/api/questions';
      const result = await api(path, { method: 'POST', body: payload });
      const newId = result?.question?.id || result?.id || result?.question_id;
      if (!newId) {
        throw new Error('保存成功，但后端没有返回题目编号，无法跳转。');
      }

      let extraWarning = '';
      for (const occ of extraOccurrences) {
        try {
          await api('/api/occurrences', {
            method: 'POST',
            body: { question_id: newId, ...occ },
          });
        } catch (err) {
          extraWarning = `题目已保存，但补充出处失败：${err.message}`;
          break;
        }
      }

      let aiJob = '';
      if (root.querySelector('#ai-after-save')?.checked) {
        try {
          const task = await api('/api/ai/jobs', { method: 'POST', body: { question_ids: [newId], budget_cny: 2 } });
          aiJob = task.job_ids[0];
        } catch (err) { extraWarning = `题目已保存，AI 任务未启动：${err.message}`; }
      }
      flashAfterNavigation(extraWarning || `已保存 ${newId}。`, extraWarning ? 'error' : 'ok');
      refreshMeta();
      location.hash = aiJob ? `#/ai?job=${encodeURIComponent(aiJob)}` : `#/question/${encodeURIComponent(newId)}`;
    } catch (err) {
      flash(err.message);
      button.disabled = false;
      button.textContent = inboxId ? '保存并整理' : '保存题目';
    }
  });

  renderOptions();
  renderSubquestions();
  renderSolutions();
  renderOccurrences();
  renderTags();
  syncQtype();
  if (tagError) flash(tagError);
  else if (sourceError) flash(sourceError);
  upgradeMath(root);
}
