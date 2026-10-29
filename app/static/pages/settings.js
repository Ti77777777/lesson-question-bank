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
  ])}
  <p class="hint">这里看到的是当前打开的题库文件和各表记录数量。要新增或修改内容，请到收题箱、题库检索或题单页操作。</p>`;
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
  <p class="hint">密钥只写本机加密存储，不回显，本页也不显示密钥内容。单价按百万 tokens 计，用于估算费用；未填写单价时不能开始 AI 批量处理。</p>
  <p class="hint">浏览本页不会发起任何模型请求。要改服务商、模型或密钥，请到 <a href="#/ai">AI 工作台</a> 的接口设置。</p>`;
}

export default async function mount(root, ctx) {
  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">系统设置</h1>
        <p class="page-sub">题库、备份与模型的信息集中在这里查看。本页不修改任何内容。</p>
      </div>
      <div class="page-head-actions">
        <button type="button" class="btn" id="settings-refresh">刷新</button>
      </div>
    </div>

    <section class="card">
      <div class="card-head"><h2>当前题库</h2><span class="hint">库文件与记录数量</span></div>
      <div class="card-body" id="settings-library">${loading('正在读取题库信息…')}</div>
    </section>

    <section class="card">
      <div class="card-head"><h2>备份</h2><span class="hint">放在哪、从哪里触发</span></div>
      <div class="card-body">
        <p>备份在项目 <span class="mono">backup</span> 目录，按时间命名：题库快照是
        <span class="mono">qbank-20261027-003000.db</span> 这样的文件，完整备份是
        <span class="mono">backup/full/QB-….zip</span>。</p>
        <p class="hint">这里说明备份的位置和文件形态，不提供备份按钮。创建备份、校验与恢复都在
        「导出与归档」页的备份与恢复区：<a class="btn-link" href="#/releases">打开导出与归档</a>。</p>
      </div>
    </section>

    <section class="card">
      <div class="card-head"><h2>模型与费用</h2><span class="hint">当前接口配置，只读</span></div>
      <div class="card-body" id="settings-ai">${loading('正在读取模型配置…')}</div>
    </section>
  `;

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
