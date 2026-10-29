/* 极小的 DOM 与格式化工具，不依赖任何框架。 */

const ESCAPE = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ESCAPE[ch]);
}

export function attr(value) {
  return esc(value);
}

export function h(html) {
  const template = document.createElement('template');
  template.innerHTML = String(html).trim();
  return template.content.firstElementChild;
}

export function $(selector, root = document) {
  return root.querySelector(selector);
}

export function $$(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

export function badge(text, tone = 'neutral') {
  if (!text) return '';
  return `<span class="badge badge-${tone}">${esc(text)}</span>`;
}

export function mono(value, cls = '') {
  return `<span class="mono ${cls}">${esc(value)}</span>`;
}

export function fmtDate(value) {
  if (!value) return '—';
  const text = String(value);
  const m = text.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : text.slice(0, 10);
}

export function fmtDateTime(value) {
  if (!value) return '—';
  const text = String(value).replace('T', ' ');
  return text.replace(/([+-]\d{2}:\d{2}|Z)$/, '').slice(0, 16);
}

export function todayISO() {
  const now = new Date();
  const offset = now.getTimezoneOffset();
  return new Date(now.getTime() - offset * 60000).toISOString().slice(0, 10);
}

export function debounce(fn, wait = 280) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function emptyState(title, hint = '') {
  return `<div class="empty"><strong>${esc(title)}</strong>${hint ? esc(hint) : ''}</div>`;
}

export function errorCard(message, onRetry) {
  const card = h(`<div class="error-card">
    <h3>读取失败</h3>
    <p></p>
    <div class="row-actions"><button type="button" class="btn btn-sm" data-retry>重试</button></div>
  </div>`);
  card.querySelector('p').textContent = message || '未知错误';
  if (onRetry) card.querySelector('[data-retry]').addEventListener('click', onRetry);
  return card;
}

export function loading(text = '正在读取…') {
  return `<div class="loading">${esc(text)}</div>`;
}

export function num(value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '');
}

export function truncate(text, length = 80) {
  const s = String(text ?? '').replace(/\s+/g, ' ').trim();
  return s.length > length ? `${s.slice(0, length)}…` : s;
}

/** 按数组下标把稳定 key 映射为 A/B/C/D（若 key 已是字母则原样）。 */
export function optionLetter(key, index) {
  if (key && /^[A-Za-z]$/.test(key)) return key.toUpperCase();
  return String.fromCharCode(65 + index);
}

export function serialize(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) {
    if (!value.length) return '—';
    return value.map((v) => (typeof v === 'object' ? JSON.stringify(v) : String(v))).join('、');
  }
  if (typeof value === 'object') {
    const text = JSON.stringify(value);
    return text === '{}' ? '—' : text;
  }
  return String(value);
}

export function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
