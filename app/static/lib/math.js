/* KaTeX 可选增强：CDN 可用时把 [data-rich] 里的 $...$ 渲染成公式，
   不可用时保持原始 LaTeX 源码，不阻塞页面、不报错。 */

import { esc } from './dom.js';
import {normaliseMathBody} from './math-notation.js';

const KATEX_VERSION = '0.19.1';
// 同一份文件在多个 CDN 上逐字节一致，加载失败时按顺序回退。
// 2026-10-30 实测：cdn.jsdelivr.net 在本机被连接重置（ECONNRESET），
// 只写这一个源会让公式全部退回 LaTeX 源码。
const KATEX_SOURCES = [
  `https://cdn.jsdelivr.net/npm/katex@${KATEX_VERSION}/dist/`,
  `https://fastly.jsdelivr.net/npm/katex@${KATEX_VERSION}/dist/`,
  `https://registry.npmmirror.com/katex/${KATEX_VERSION}/files/dist/`,
  `https://unpkg.com/katex@${KATEX_VERSION}/dist/`,
];
// SHA-384 of the official release; all mirrors above serve identical bytes.
const KATEX_INTEGRITY = {
  js: 'sha384-a1WQdjRj162i159OYq041X6I4K8NenkrI09LlPmiHg0YOb7CR+wJOraXwK9abpnE',
  css: 'sha384-WwGtaC6ywCHxA5cstpj/3+xIZvTez8dq2IcBQaXRkenHZ7FVVq1ZROtURwfbtTlG',
};

const SOURCE_TIMEOUT = 5000;
let loader = null;

function loadScript(base) {
  return new Promise((resolve) => {
    let settled = false;
    let timer;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(Boolean(ok));
    };
    const script = document.createElement('script');
    script.src = `${base}katex.min.js`;
    script.integrity = KATEX_INTEGRITY.js;
    script.crossOrigin = 'anonymous';
    script.async = true;
    script.onload = () => finish(Boolean(window.katex));
    script.onerror = () => finish(false);
    timer = setTimeout(() => finish(Boolean(window.katex)), SOURCE_TIMEOUT);
    document.head.appendChild(script);
  });
}

function loadStylesheet(base) {
  return new Promise((resolve) => {
    let settled = false;
    let timer;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(Boolean(ok));
    };
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = `${base}katex.min.css`;
    link.integrity = KATEX_INTEGRITY.css;
    link.crossOrigin = 'anonymous';
    link.onload = () => finish(true);
    link.onerror = () => finish(false);
    timer = setTimeout(() => finish(true), SOURCE_TIMEOUT);
    document.head.appendChild(link);
  });
}

async function loadStylesheetWithFallback(from) {
  const order = KATEX_SOURCES.slice(KATEX_SOURCES.indexOf(from)).concat(KATEX_SOURCES.slice(0, KATEX_SOURCES.indexOf(from)));
  for (const base of order) {
    if (await loadStylesheet(base)) return true;
  }
  return false;
}

async function boot() {
  // 先按首个源放一份样式（同步创建，便于页面尽早应用）；失败再逐源回退。
  const css = loadStylesheet(KATEX_SOURCES[0])
    .then((ok) => (ok ? true : loadStylesheetWithFallback(KATEX_SOURCES[1] || KATEX_SOURCES[0])));
  for (const base of KATEX_SOURCES) {
    if (await loadScript(base)) {
      css.catch(() => {});
      return true;
    }
  }
  css.catch(() => {});
  // 全部源都失败时保留原始源码，并在控制台留一条可排查的线索。
  console.warn('[题库] 公式组件 KaTeX 未能从任何 CDN 加载，页面会显示 LaTeX 源码；请检查网络或代理。');
  return false;
}

export function loadKatex() {
  if (!loader) loader = boot();
  return loader;
}

function renderTex(tex, display) {
  const source = String(tex ?? '');
  if (!source.trim()) return '';
  try {
    return window.katex.renderToString(normaliseMathBody(source.trim()), {
      displayMode: display,
      throwOnError: false,
      strict: 'ignore',
      trust: false,
      maxExpand: 1000,
      maxSize: 50,
    });
  } catch {
    const wrapped = display ? `$$${source}$$` : `$${source}$`;
    return `<code class="tex-src">${esc(wrapped)}</code>`;
  }
}

const MATH_RE = /\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g;

function segmentToHtml(raw) {
  raw = raw.replace(/\\begin\{center\}\s*/g, '').replace(/\s*\\end\{center\}/g, '')
    .replace(/^[ \t]+/gm, '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n')
    .replace(/\\blankbox\b/g, '（　）')
    .replace(/\\blankline(?:\[[^\]]*\])?/g, '________');
  let out = '';
  let last = 0;
  let match;
  MATH_RE.lastIndex = 0;
  while ((match = MATH_RE.exec(raw)) !== null) {
    out += esc(raw.slice(last, match.index)).replace(/\n/g, '<br>');
    const display = match[1] !== undefined || match[3] !== undefined;
    const tex = match[1] ?? match[2] ?? match[3] ?? match[4] ?? '';
    out += renderTex(tex, display);
    last = MATH_RE.lastIndex;
  }
  out += esc(raw.slice(last)).replace(/\n/g, '<br>');
  return out;
}

// 导入图片保持在原题干/选项/解析的位置，不把解析图额外塞进学生题面。
export function richToHtml(raw) {
  const source = String(raw ?? '');
  const blockRe = /\\begin\{tabular\}\{([|lcr\s]+)\}([\s\S]*?)\\end\{tabular\}|\\qbankimage(?:\[[^\]]*\])?\{(assets\/[A-Za-z0-9_./-]+)\}/g;
  let result = '';
  let last = 0;
  let match;
  while ((match = blockRe.exec(source)) !== null) {
    result += segmentToHtml(source.slice(last, match.index));
    if (match[1] !== undefined) {
      result += simpleTableToHtml(match[1], match[2]) ?? segmentToHtml(match[0]);
    } else {
      const path = match[3];
      const safe = /^assets\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.(?:png|jpe?g|webp)$/i.test(path);
      result += safe
        ? `<img src="/files/${path.split('/').map(encodeURIComponent).join('/')}" alt="题目配图" loading="lazy" style="display:block;max-width:100%;max-height:460px;object-fit:contain;margin:10px auto;">`
        : esc(match[0]);
    }
    last = blockRe.lastIndex;
  }
  return result + segmentToHtml(source.slice(last));
}

// 仅转换普通网格表；跨行、跨列或嵌套表继续展示原文，避免猜错题面。
function simpleTableToHtml(spec, body) {
  if (/\\(?:multi(?:row|column)|begin|end)\b/.test(body)) return null;
  const columns = [...spec].filter(c => /[lcr]/.test(c));
  const rows = [];
  let cells = [], value = '', depth = 0;
  body = body.replace(/\\hline\b/g, '').trim();
  for (let i = 0; i < body.length; i++) {
    const c = body[i];
    if (c === '\\' && body[i + 1] !== '\\') {
      value += c;
      if (i + 1 < body.length) value += body[++i];
    } else if (c === '\\' && body[i + 1] === '\\' && depth === 0) {
      cells.push(value.trim()); rows.push(cells); cells = []; value = ''; i++;
    } else if (c === '&' && depth === 0) {
      cells.push(value.trim()); value = '';
    } else {
      if (c === '{') depth++;
      if (c === '}') depth--;
      if (depth < 0) return null;
      value += c;
    }
  }
  if (value.trim() || cells.length) { cells.push(value.trim()); rows.push(cells); }
  if (depth !== 0 || !rows.length || rows.some(row => row.length !== columns.length)) return null;
  const align = { l: 'left', c: 'center', r: 'right' };
  return '<div class="tex-table-wrap"><table class="tex-table"><tbody>' + rows.map(row =>
    '<tr>' + row.map((cell, i) => `<td style="text-align:${align[columns[i]]}">${richToHtml(cell)}</td>`).join('') + '</tr>'
  ).join('') + '</tbody></table></div>';
}

/** 渲染 root 内所有尚未处理的 [data-rich]。可对局部容器重复调用。 */
export function upgradeMath(root = document) {
  if (!root || !root.querySelectorAll) return;
  const nodes = Array.from(root.querySelectorAll('[data-rich]'));
  if (!nodes.length) return;

  loadKatex().then((ok) => {
    nodes.forEach((node) => {
      if (node.dataset.mathReady === '1' && (node.dataset.katexReady === '1' || !ok)) return;
      const raw = node.dataset.mathRaw ?? node.textContent ?? '';
      node.dataset.mathRaw = raw;
      node.dataset.mathReady = '1';
      node.dataset.katexReady = ok ? '1' : '0';
      try {
        node.innerHTML = richToHtml(raw);
      } catch {
        node.textContent = raw;
      }
    });
  });
}

export function katexState() {
  return Boolean(window.katex);
}
