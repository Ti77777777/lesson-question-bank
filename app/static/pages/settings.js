import { mountBackupPanel } from '../lib/backup-panel.js';
/* 系统设置：把题库信息、备份位置与模型配置集中到一页，只读展示。 */

import { api } from '../lib/api.js';
import { esc, loading, errorCard, $, num } from '../lib/dom.js';

const PROTOCOLS = {
  chat: 'OpenAI 兼容对话',
  responses: 'OpenAI Responses',
  anthropic: 'Anthropic Messages',
  gemini: 'Gemini',
};

function row(label, value, mono = false) {
  const text = value === null || value === undefined || value === '' ? '—' : String(value);
  return `<tr><th>${esc(label)}</th><td>${mono ? `<span class="mono">${esc(text)}</span>` : esc(text)}</td></tr>`;
}

function countRow(label, value) {
  return `<tr><th>${esc(label)}</th><td class="num">${esc(num(value, '0'))}</td></tr>`;
}

function table(rows) {
  return `<div class="table-wrap"><table class="table"><tbody>${rows.join('')}</tbody></table></div>`;
}

function renderLibrary(el, meta) {
  const counts = meta.counts || {};
  el.innerHTML = `${table([
    row('库文件', meta.db_path, true),
    row('项目根目录', meta.root, true),
    row('结构版本', meta.schema_version),
    countRow('题目', counts.questions),
    countRow('题单', counts.collections),
    countRow('资料', counts.sources),
    countRow('发布物', counts.releases),
    countRow('待整理', counts.inbox_pending),
  ])}`;
}

function renderAi(el, data) {
  const cfg = data.config || {};
  const preset = (data.presets || []).find((item) => item.id === cfg.preset);
  const provider = preset ? preset.label : (cfg.preset || '未设置');
  const price = (value) => (value === null || value === undefined ? '未填写' : `${esc(value)} 元 / 百万 tokens`);
  el.innerHTML = `${table([
    row('服务商', provider),
    row('模型', cfg.model || '未填写'),
    row('接口协议', PROTOCOLS[cfg.protocol] || cfg.protocol),
    row('接口地址', cfg.base_url || '未填写', true),
    row('凭据', data.configured ? '已配置' : '未配置'),
    `<tr><th>输入单价</th><td>${price(cfg.input_price)}</td></tr>`,
    `<tr><th>输出单价</th><td>${price(cfg.output_price)}</td></tr>`,
  ])}
  <a class="btn" href="#/ai?settings=1">配置模型接口</a>`;
}

export default async function mount(root, ctx) {
  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">系统信息</h1>

      </div>
      <div class="page-head-actions">
        <button type="button" class="btn" id="settings-refresh">刷新</button>
      </div>
    </div>

    <section class="card">
      <div class="card-head"><h2>当前题库</h2></div>
      <div class="card-body" id="settings-library">${loading('正在读取题库信息…')}</div>
    </section>

    <section class="card">
      <div class="card-head"><h2>备份</h2></div>
      <details class="compact-fold" id="system-backup"><summary>备份与恢复</summary><div class="card-body" id="backup-panel"></div></details>
    </section>

    <section class="card">
      <div class="card-head"><h2>模型与费用</h2></div>
      <div class="card-body" id="settings-ai">${loading('正在读取模型配置…')}</div>
    </section>
  `;

  ctx.onCleanup(mountBackupPanel($('#backup-panel',root)));
  if(ctx.query.section==='backup')$('#system-backup',root).open=true;
  const libraryEl = $('#settings-library', root);
  const aiEl = $('#settings-ai', root);

  async function loadLibrary() {
    libraryEl.innerHTML = loading('正在读取题库信息…');
    try {
      const meta = await api('/api/meta');
      if (!ctx.current()) return;
      renderLibrary(libraryEl, meta);
    } catch (err) {
      if (!ctx.current()) return;
      libraryEl.replaceChildren(errorCard(err.message, loadLibrary));
    }
  }

  async function loadAi() {
    aiEl.innerHTML = loading('正在读取模型配置…');
    try {
      const data = await api('/api/ai/settings');
      if (!ctx.current()) return;
      renderAi(aiEl, data);
    } catch (err) {
      if (!ctx.current()) return;
      aiEl.replaceChildren(errorCard(err.message, loadAi));
    }
  }

  $('#settings-refresh', root).addEventListener('click', () => {
    loadLibrary();
    loadAi();
  });

  await Promise.all([loadLibrary(), loadAi()]);
}
