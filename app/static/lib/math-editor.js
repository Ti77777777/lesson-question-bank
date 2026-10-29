/**
 * N03 统一数学编辑器（首期）。
 *
 * 三个入口（新建题目、新建版本、AI 修订）共用这一套：输入 + 公式按钮 + 实时预览。
 * 原则：
 * - 源文本永远是 LaTeX，预览只负责显示，不回写、不转图片；
 * - 插入片段只改选区，不重排其他字符；
 * - 预览失败保留原文并提示，不阻止保存。
 */
import { richToHtml, upgradeMath } from './math.js';
import { esc } from './dom.js';

/** ${} 表示插入后光标位置；没有 ${} 则光标落在末尾。 */
export const SNIPPETS = [
  { group: '结构', label: '分式', insert: '\\frac{${}}{}' },
  { group: '结构', label: '根式', insert: '\\sqrt{${}}' },
  { group: '结构', label: '括号', insert: '\\left(${}\\right)' },
  { group: '结构', label: '绝对值', insert: '\\left|{}\\right|' },
  { group: '结构', label: '分段', insert: '\\begin{cases}\n  ${} & x>0 \\\\\n  0 & x=0\n\\end{cases}' },
  { group: '结构', label: '矩阵', insert: '\\begin{pmatrix}\n  {} & {} \\\\\n  {} & {}\n\\end{pmatrix}' },
  { group: '上下标', label: '上标', insert: '^{${}}' },
  { group: '上下标', label: '下标', insert: '_{${}}' },
  { group: '上下标', label: '同时上下标', insert: '_{${}}^{}' },
  { group: '符号', label: '≤', insert: '\\leqslant ' },
  { group: '符号', label: '≥', insert: '\\geqslant ' },
  { group: '符号', label: '≠', insert: '\\neq ' },
  { group: '符号', label: '∈', insert: '\\in ' },
  { group: '符号', label: '∞', insert: '\\infty ' },
  { group: '符号', label: '∪', insert: '\\cup ' },
  { group: '符号', label: '∩', insert: '\\cap ' },
  { group: '符号', label: '∅', insert: '\\varnothing ' },
  { group: '集合', label: 'ℝ', insert: '\\mathbb{R}' },
  { group: '集合', label: '区间', insert: '\\left[{}, {}\\right]' },
  { group: '集合', label: '集合', insert: '\\left\\{{} \\mid {}\\right\\}' },
];

const MARK = '${}';

/**
 * 把片段插到选区。纯函数：给同样的输入必得同样的输出，便于单测。
 * @returns {{value: string, start: number, end: number}}
 */
export function applySnippet({ value = '', start = 0, end = start }, snippet) {
  const text = String(value);
  const from = Math.max(0, Math.min(Number(start) || 0, text.length));
  const to = Math.max(from, Math.min(Number(end) || from, text.length));
  const raw = String(snippet || '');
  const markAt = raw.indexOf(MARK);
  const inserted = raw.split(MARK).join('');
  const next = text.slice(0, from) + inserted + text.slice(to);
  let caret = from + inserted.length;
  if (markAt >= 0) {
    // 片段里第一个 ${} 之前有多少个占位符被清掉，光标就落在哪里
    caret = from + raw.slice(0, markAt).split(MARK).join('').length;
  }
  return { value: next, start: caret, end: caret };
}

/** 需要时自动补一对 $，避免公式落在数学模式之外。 */
export function wrapIfOutsideMath({ value = '', start = 0, end = start }, snippet) {
  const text = String(value);
  const before = text.slice(0, start);
  const dollars = (before.match(/(?<!\\)\$/g) || []).length;
  if (dollars % 2 === 0) {
    const inner = applySnippet({ value: text, start, end }, snippet);
    const head = text.slice(0, start);
    const tail = text.slice(end);
    const body = inner.value.slice(head.length, inner.value.length - tail.length);
    return {
      value: `${head}$${body}$${tail}`,
      start: inner.start + 1,
      end: inner.end + 1,
    };
  }
  return applySnippet({ value: text, start, end }, snippet);
}

function insertInto(el, snippet) {
  const state = { value: el.value, start: el.selectionStart, end: el.selectionEnd };
  const out = wrapIfOutsideMath(state, snippet);
  el.value = out.value;
  el.focus();
  el.setSelectionRange(out.start, out.end);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

function toolbarHtml() {
  const groups = new Map();
  SNIPPETS.forEach((item, index) => {
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push({ ...item, index });
  });
  return `<div class="math-toolbar">${[...groups.entries()].map(([group, items]) => `
    <span class="math-toolbar-group"><span class="math-toolbar-label">${esc(group)}</span>
      ${items.map((item) => `<button type="button" class="btn-link" data-tex-insert="${item.index}" title="${esc(item.insert)}">${esc(item.label)}</button>`).join('')}
    </span>`).join('')}</div>`;
}

/**
 * 给指定字段加上公式工具栏与预览。
 * @param {HTMLElement} root 容器
 * @param {string[]} selectors 要增强的 textarea 选择器
 */
export function enhanceMathFields(root, selectors) {
  if (!root) return;
  selectors.forEach((selector) => {
    const area = root.querySelector(selector);
    if (!area || area.dataset.mathEnhanced === '1') return;
    area.dataset.mathEnhanced = '1';

    const wrap = document.createElement('div');
    wrap.className = 'math-field';
    area.parentNode.insertBefore(wrap, area);
    wrap.appendChild(area);

    const bar = document.createElement('div');
    bar.innerHTML = toolbarHtml();
    wrap.insertBefore(bar.firstElementChild, area);

    const preview = document.createElement('div');
    preview.className = 'math-preview';
    preview.hidden = true;
    wrap.appendChild(preview);

    let timer = null;
    const render = () => {
      const raw = area.value.trim();
      if (!raw) { preview.hidden = true; return; }
      preview.hidden = false;
      preview.innerHTML = `<div class="math-preview-body" data-rich>${richToHtml(raw)}</div>`;
      upgradeMath(preview);
      const failed = preview.querySelector('.katex-error');
      preview.classList.toggle('has-error', Boolean(failed));
      if (failed) {
        const tip = preview.querySelector('.math-preview-tip') || document.createElement('p');
        tip.className = 'hint math-preview-tip';
        tip.textContent = '有公式没能渲染，原文仍会照原样保存。';
        preview.appendChild(tip);
      }
    };
    area.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(render, 260);
    });
    render();
  });

  if (root.dataset.mathToolbarBound !== '1') {
    root.dataset.mathToolbarBound = '1';
    root.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-tex-insert]');
      if (!btn) return;
      const area = btn.closest('.math-field')?.querySelector('textarea');
      if (!area) return;
      const snippet = SNIPPETS[Number(btn.dataset.texInsert)];
      if (snippet) insertInto(area, snippet.insert);
    });
  }
}
