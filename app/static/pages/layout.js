import { api } from '../lib/api.js';
import { esc } from '../lib/dom.js';
import { flash } from '../lib/ui.js';
import { richToHtml, loadKatex } from '../lib/math.js';
import { paginateBlocks } from '../lib/layout-preview.js';

const VERSIONS = { student: '学生版', answer: '答案版', teacher: '教师版' };
const COLORS = { blue: '#426987', green: '#39705b', purple: '#765c91', orange: '#ae7441' };
const FORMATS = { standard: '标准', compact: '紧凑', loose: '宽松', single: '单面' };
const selectOptions = (values, selected) => Object.entries(values).map(([v, t]) => `<option value="${esc(v)}" ${v === selected ? 'selected' : ''}>${esc(t)}</option>`).join('');

export default async function mount(root, ctx) {
  const cid = ctx.params.id;
  const data = await api(`/api/collections/${encodeURIComponent(cid)}/layout`);
  if (!ctx.current()) return;
  const snap = data.snapshot;
  let options = { ...snap.options }, overrides = structuredClone(data.layout.items || {});
  let selected = snap.items[0]?.id || '', version = 'student', mode = 'quick', dirty = false, pdf = null, disposed = false, painting = 0;
  root.classList.add('layout-page');
  ctx.onCleanup(() => { disposed = true; root.classList.remove('layout-page'); });
  const payload = () => ({ options: Object.fromEntries(['title', 'subtitle', 'pretitle', 'creator', 'motto', 'theme', 'format', 'cover', 'toc', 'show_sources', 'teacher_notes', 'options_layout'].map(k => [k, options[k] ?? (['cover', 'teacher_notes'].includes(k) ? true : ['toc', 'show_sources'].includes(k) ? false : '')])), items: overrides });
  const $ = s => root.querySelector(s);
  const effective = item => ({ ...item, ...(overrides[item.id] || {}) });
  root.innerHTML = `<div class="page-head"><div><p class="layout-eyebrow">讲义制作</p><h1 class="page-title">排版与预览</h1><p class="page-sub">${esc(snap.collection.title)} · ${snap.items.length}道题</p></div><div class="detail-actions"><a class="btn" href="#/collection/${encodeURIComponent(cid)}">返回题单</a><button class="btn" data-save>保存排版</button><button class="btn btn-primary" data-publish ${snap.items.length ? '' : 'disabled'}>导出三版讲义</button></div></div>
    <div class="layout-workspace"><aside class="layout-controls">
      <section class="card"><h2>整份讲义</h2>
        ${[['title', '讲义标题'], ['subtitle', '副标题'], ['pretitle', '封面前置语'], ['creator', '编写署名']].map(([k,t]) => `<label class="field"><span>${t}</span><input data-option="${k}" value="${esc(options[k] || '')}" maxlength="300"></label>`).join('')}
        <div class="layout-fields"><label class="field"><span>版面密度</span><select data-option="format">${selectOptions(FORMATS, options.format)}</select></label><label class="field"><span>主题色</span><select data-option="theme">${selectOptions({ blue: '青蓝', green: '松绿', purple: '浅紫', orange: '暖橙' }, options.theme)}</select></label></div>
        <label class="field"><span>全卷选项排列</span><select data-option="options_layout">${selectOptions({ auto: '自动：放得下时横排', vertical: '全部竖排' }, options.options_layout || 'auto')}</select></label>
        <div class="layout-checks">${[['cover','显示封面'],['show_sources','显示题目来源'],['teacher_notes','教师版讲评提示']].map(([k,t]) => `<label><input type="checkbox" data-option="${k}" ${options[k] ? 'checked' : ''}>${t}</label>`).join('')}</div>
      </section>
      <section class="card"><h2>逐题微调</h2><p class="hint">点击题号或纸面中的题目选择。</p><div class="layout-item-list">${snap.items.map((i,n) => `<button class="btn btn-sm" data-pick="${esc(i.id)}">${n+1}</button>`).join('')}</div><div data-item-settings></div></section>
      <section class="layout-note"><strong>预览与正式导出共用设置</strong><p>快速预览估算分页；公式、图形及长题的最终位置，以“实际PDF校样”为准。校样不会新增发布记录。</p><span data-save-state>已加载保存设置</span></section>
    </aside><section class="layout-stage"><div class="layout-toolbar"><div class="layout-modes"><button class="btn btn-sm active" data-mode="quick">快速预览</button><button class="btn btn-sm" data-mode="pdf">实际PDF校样</button></div><select data-version aria-label="预览版本">${selectOptions(VERSIONS, version)}</select><label class="layout-zoom">缩放<select data-zoom aria-label="预览缩放"><option value="0.65">65%</option><option value="0.8" selected>80%</option><option value="1">100%</option></select></label><button class="btn btn-sm" data-compile ${snap.items.length ? '' : 'disabled'}>生成PDF校样</button></div>
      <div class="layout-status" data-status role="status">快速预览 · 分页为估算</div><div class="layout-canvas" data-canvas><div data-pages></div><div data-pdf hidden></div></div><div data-export role="status"></div></section></div>`;
  // Friendly labels without binding model values to arbitrary HTML.
  $('[data-option="theme"]').innerHTML = selectOptions({ blue: '青蓝', green: '松绿', purple: '浅紫', orange: '暖橙', ...(!COLORS[options.theme] ? { [options.theme]: options.theme } : {}) }, options.theme);

  function markDirty() {
    dirty = true; pdf = null;
    $('[data-save-state]').textContent = '有未保存的排版调整';
    $('[data-pdf]').innerHTML = '<div class="layout-empty">设置已变化，请重新生成PDF校样。</div>';
    $('[data-status]').textContent = mode === 'pdf' ? 'PDF校样已过期，请重新生成' : '快速预览 · 分页为估算';
  }
  function renderItemSettings() {
    root.querySelectorAll('[data-pick]').forEach(b => b.classList.toggle('selected', b.dataset.pick === selected));
    const original = snap.items.find(i => i.id === selected);
    if (!original) { $('[data-item-settings]').innerHTML = '<p class="hint">题单为空，请先返回题单添加题目。</p>'; return; }
    const item = effective(original), num = snap.items.indexOf(original) + 1;
    $('[data-item-settings]').innerHTML = `<h3>第${num}题</h3><label class="field"><span>学生版作答留白（毫米）</span><input type="number" data-item="space_mm" min="0" max="200" step="5" value="${Number(item.space_mm || 0)}"></label><label class="field"><span>本题选项排列</span><select data-item="options_layout">${selectOptions({ inherit: '跟随整卷设置', auto: '自动横排或竖排', vertical: '竖排' }, overrides[selected]?.options_layout || 'inherit')}</select></label><div class="layout-checks"><label><input type="checkbox" data-item="page_break_before" ${item.page_break_before ? 'checked' : ''}>本题从新页开始</label><label><input type="checkbox" data-item="keep_together" ${item.keep_together ? 'checked' : ''}>尽量保持本题完整</label></div><p class="hint">超出一页的长题仍需分开；请检查PDF校样。</p><button class="btn btn-sm" data-reset>恢复本题原排版</button>`;
    $('[data-item-settings]').querySelectorAll('[data-item]').forEach(input => { const update = () => {
      let val = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
      if (input.dataset.item === 'space_mm' && (!Number.isFinite(val) || val < 0 || val > 200)) { flash('留白须为0至200毫米'); return; }
      const entry = overrides[selected] ||= {};
      if (val === 'inherit') delete entry[input.dataset.item]; else entry[input.dataset.item] = val;
      markDirty(); paint();
    }; input.onchange = update; if (input.type === 'number') input.oninput = update; });
    $('[data-reset]').onclick = () => { delete overrides[selected]; markDirty(); renderItemSettings(); paint(); };
  }
  function itemHtml(original, number) {
    const i = effective(original), entry = snap.question_versions[i.question_version_id], q = entry.version;
    const subkeys = i.subq_keys || [], subs = (q.subquestions || []).filter(s => !subkeys.length || subkeys.includes(String(s.key)));
    const optionLayout = i.options_layout || options.options_layout || 'auto';
    const source = options.show_sources && entry.source_label ? `<div class="paper-source">${esc(entry.source_label)}</div>` : '';
    const choices = (q.options || []).map(o => `<div><b>${esc(o.key)}.</b> ${richToHtml(o.text)}</div>`).join('');
    let html = `<div class="paper-stem"><b class="paper-number">${number}.</b>${i.required === 0 ? '<b>（选做）</b>' : ''}${source}${richToHtml(q.stem)}${i.points != null ? `<span class="paper-points">（${Number(i.points)}分）</span>` : ''}</div>`;
    for (const a of entry.assets || []) {
      if (q.teaching?.asset_placement === 'inline' || !/^assets\/[A-Za-z0-9_./-]+\.(png|jpe?g|webp)$/i.test(a.rel_path || '') || a.rel_path.includes('..')) continue;
      html += `<img class="paper-figure" src="/files/${a.rel_path.split('/').map(encodeURIComponent).join('/')}" alt="题目配图">`;
    }
    if (choices) html += `<div class="paper-choices ${optionLayout === 'vertical' ? 'vertical' : 'auto'}">${choices}</div>`;
    if (subs.length) html += `<div class="paper-subs">${subs.map(s => `<div>（${esc(s.key)}）${richToHtml(s.text)}</div>`).join('')}</div>`;
    if (version === 'student') {
      const hint = q.teaching || {};
      if (i.hint_level !== 'none' && (hint.key_step || hint.probe)) html += `<p class="paper-hint">提示：${richToHtml(i.hint_level === 'full' ? hint.probe : hint.key_step)}</p>`;
      if (i.space_mm) html += `<div class="paper-space" style="height:${Number(i.space_mm)}mm"><span>作答区 · ${Number(i.space_mm)} mm</span></div>`;
    } else {
      const selectedSolutions = (entry.solutions || []).filter(s => !i.solution_id || s.id === i.solution_id);
      const answer = q.answer || (q.answer_keys || []).join('、') || subs.map(s => s.answer ? `（${s.key}）${s.answer}` : '').filter(Boolean).join('；') || '（略）';
      const solution = selectedSolutions.length ? selectedSolutions.map(s => `<div><strong>${esc(s.label || '解法')}</strong>${richToHtml(s.steps)}${s.conditions ? `<p>适用条件：${richToHtml(s.conditions)}</p>` : ''}</div>`).join('') : richToHtml(q.analysis || '');
      html += `<div class="paper-solution">${version === 'teacher' ? `<h4>解析</h4>${solution}` : ''}<h4>答案</h4>${richToHtml(answer)}`;
      if (version === 'teacher' && options.teacher_notes) html += Object.entries({ why: '为什么想到', key_step: '关键一步', pitfalls: '易错点', probe: '追问' }).filter(([k]) => q.teaching?.[k]).map(([k,t]) => `<p><b>${t}：</b>${richToHtml(q.teaching[k])}</p>`).join('');
      html += '</div>';
    }
    return `<article class="paper-question ${selected === i.id ? 'selected' : ''}" data-paper-item="${esc(i.id)}" tabindex="0" aria-label="选择第${number}题">${html}</article>`;
  }
  function paint() {
    if (disposed) return;
    const run = ++painting, stage = $('[data-pages]');
    stage.style.setProperty('--paper-theme', COLORS[options.theme] || COLORS.blue);
    stage.style.setProperty('--paper-font', options.format === 'compact' ? '14px' : options.format === 'loose' ? '16px' : '15px');
    stage.style.setProperty('--paper-leading', options.format === 'compact' ? '1.65' : options.format === 'loose' ? '2' : '1.85');
    const blocks = []; let number = 0;
    const sections = [...snap.sections], known = new Set(sections.map(s => s.id));
    if (snap.items.some(i => !known.has(i.section_id))) sections.push({ id: null, title: '未分节' });
    for (const section of sections) {
      const items = snap.items.filter(i => section.id ? i.section_id === section.id : !known.has(i.section_id));
      if (!items.length && section.kind === 'question') continue;
      blocks.push({ heading: true, html: `<header class="paper-section"><h2>${esc(section.title)}</h2>${section.intro ? richToHtml(section.intro) : ''}</header>` });
      for (const item of items) blocks.push({ html: itemHtml(item, ++number), breakBefore: Boolean(effective(item).page_break_before) });
    }
    const cover = options.cover ? `<div class="layout-paper paper-cover"><div>${esc(options.pretitle || '')}</div><h1>${esc(options.title)}</h1><h2>${esc(options.subtitle || '')} · ${VERSIONS[version]}</h2><footer>${esc(options.creator || '')}</footer></div>` : '';
    stage.innerHTML = '<div class="paper-measure">' + blocks.map(b => `<div>${b.html}</div>`).join('') + '</div>';
    const measurement = stage.querySelector('.paper-measure');
    measurement.querySelectorAll('.paper-choices.auto').forEach(c => {
      const widths = [...c.children].map(el => { const range = document.createRange(); range.selectNodeContents(el); return range.getBoundingClientRect().width / Number($('[data-zoom]').value); });
      if (c.children.length === 4 && widths.every(w => w < (measurement.clientWidth - 60) / 4)) c.classList.add('horizontal');
    });
    [...measurement.children].forEach((el,i) => { blocks[i].height = Math.ceil(el.getBoundingClientRect().height / Number($('[data-zoom]').value)) + 8; blocks[i].html = el.innerHTML; });
    const pages = paginateBlocks(blocks, 920);
    if (run !== painting) return;
    const label = `<span>${esc(options.title)}</span><span>${VERSIONS[version]}</span>`;
    stage.innerHTML = cover + pages.map((page,n) => `<div class="layout-paper"><div class="paper-header">${label}</div><div class="paper-body">${page.map(b => b.html).join('')}</div><div class="paper-footer">第 ${n+1} 页 · 快速预览</div></div>`).join('');
    if (!blocks.length) stage.innerHTML = '<div class="layout-empty">题单中还没有题目，请返回题单添加。</div>';
    if (mode === 'quick') $('[data-status]').textContent = `快速预览 · 约${pages.length + (options.cover ? 1 : 0)}页 · 分页以PDF校样为准`;
    stage.querySelectorAll('[data-paper-item]').forEach(el => {
      const pick = () => { selected = el.dataset.paperItem; renderItemSettings(); stage.querySelectorAll('[data-paper-item]').forEach(x => x.classList.toggle('selected', x === el)); };
      el.onclick = pick; el.onkeydown = e => { if (e.key === 'Enter') pick(); };
    });
    // Late image dimensions must trigger another measurement.
    stage.querySelectorAll('img').forEach(img => { if (!img.complete) img.onload = () => { if (run === painting) paint(); }; });
  }
  async function save() {
    await api(`/api/collections/${encodeURIComponent(cid)}/layout`, { method: 'PUT', body: payload() });
    dirty = false; if (disposed) return; $('[data-save-state]').textContent = '排版已保存，正式导出将使用这些设置';
  }
  function showMode(next) {
    mode = next; $('[data-pages]').hidden = mode !== 'quick'; $('[data-pdf]').hidden = mode !== 'pdf';
    root.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('active', b.dataset.mode === next));
    if (mode === 'pdf' && !pdf) $('[data-pdf]').innerHTML = '<div class="layout-empty">点击“生成PDF校样”查看与正式导出相同的排版。生成校样不会登记发布物。</div>';
    if (mode === 'quick') paint();
  }
  root.querySelectorAll('[data-option]').forEach(input => { const update = () => { options[input.dataset.option] = input.type === 'checkbox' ? input.checked : input.value; markDirty(); paint(); }; input.onchange = update; if (input.type === 'text') input.oninput = update; });
  root.querySelectorAll('[data-pick]').forEach(b => b.onclick = () => { selected = b.dataset.pick; renderItemSettings(); paint(); });
  root.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => showMode(b.dataset.mode));
  $('[data-version]').onchange = () => { version = $('[data-version]').value; pdf = null; paint(); if (mode === 'pdf') showMode('pdf'); };
  $('[data-zoom]').onchange = () => { $('[data-pages]').style.zoom = $('[data-zoom]').value; paint(); };
  $('[data-save]').onclick = async () => { try { await save(); flash('排版已保存', 'ok'); } catch(e) { flash(e.message); } };
  $('[data-compile]').onclick = async () => {
    const button = $('[data-compile]'), requestedVersion = version, signature = JSON.stringify(payload());
    button.disabled = true; button.textContent = '正在生成…'; $('[data-status]').textContent = '正在使用正式排版引擎生成校样…';
    try {
      const result = await api(`/api/collections/${encodeURIComponent(cid)}/layout/pdf`, { method: 'POST', body: { version, layout: payload() } });
      if (disposed) return;
      if (requestedVersion !== version || signature !== JSON.stringify(payload())) { $('[data-status]').textContent = '设置已变化，请重新生成校样'; return; }
      pdf = result; showMode('pdf');
      $('[data-pdf]').innerHTML = `<div class="layout-pdf-link"><a class="btn" href="${esc(pdf.url)}" target="_blank" rel="noopener">新窗口打开 / 下载PDF</a><span>${VERSIONS[version]} · ${pdf.pages || '?'}页 · 未登记发布</span></div><iframe title="讲义实际PDF校样" src="${esc(pdf.url)}#toolbar=1&view=FitH"></iframe>`;
      const warnings = Number(pdf.checks?.overfull || 0) + Number(pdf.checks?.missing_char || 0);
      $('[data-status]').textContent = warnings ? `PDF校样已生成 · 有${warnings}项溢出或缺字提示，请检查` : `PDF校样已生成 · ${pdf.pages || '?'}页 · 与正式导出共用排版引擎`;
    } catch(e) { if (!disposed) { $('[data-status]').textContent = e.message; flash(e.message); } }
    finally { if (!disposed) { button.disabled = false; button.textContent = '重新生成PDF校样'; } }
  };
  $('[data-publish]').onclick = async () => {
    const b = $('[data-publish]'); b.disabled = true; b.textContent = '正在导出…';
    try {
      await save();
      const result = await api(`/api/collections/${encodeURIComponent(cid)}/build`, { method: 'POST', body: { ...payload().options, versions: ['student', 'answer', 'teacher'] } });
      if (disposed) return;
      $('[data-export]').innerHTML = `<div class="card"><h3>${result.release?.status === 'failed' ? '导出未完成，请到归档查看诊断' : '三版讲义已导出'}</h3><div class="detail-actions">${Object.entries(result.files || {}).filter(([,f]) => f.url).map(([v,f]) => `<a class="btn" target="_blank" rel="noopener" href="${esc(f.url)}">${VERSIONS[v]} · ${f.pages || '?'}页</a>`).join('')}<a class="btn" href="#/releases">查看归档</a></div></div>`;
      document.dispatchEvent(new Event('meta:refresh'));
    } catch(e) { flash(e.message); }
    finally { if (!disposed) { b.disabled = false; b.textContent = '导出三版讲义'; } }
  };
  renderItemSettings(); $('[data-pages]').style.zoom = '0.8'; paint();
  loadKatex().then(() => { if (!disposed) paint(); });
}
