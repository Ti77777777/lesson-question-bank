import { api } from '../lib/api.js';
import { esc } from '../lib/dom.js';
import { flash, confirmDialog } from '../lib/ui.js';
import { draftStore, autosaver } from '../lib/drafts.js';
import { mountLayoutTemplates } from '../lib/layout-templates.js';
import { rememberCollection } from '../lib/recent-work.js';
import { richToHtml, loadKatex } from '../lib/math.js';
import { paginateBlocks } from '../lib/layout-preview.js';
import { groupedItems, movePreviewItem, bindLayoutInteractions } from '../lib/layout-interactions.js';

const VERSIONS = { student: '学生版', answer: '答案版', teacher: '教师版' };
const COLORS = { blue: '#426987', green: '#39705b', purple: '#765c91', orange: '#ae7441' };
const FORMATS = { standard: '标准', compact: '紧凑', loose: '宽松', single: '单面' };
const selectOptions = (values, selected) => Object.entries(values).map(([v, t]) => `<option value="${esc(v)}" ${v === selected ? 'selected' : ''}>${esc(t)}</option>`).join('');

export default async function mount(root, ctx) {
  const cid = ctx.params.id;
  const data = await api(`/api/collections/${encodeURIComponent(cid)}/layout`);
  if (!ctx.current()) return;
  const snap = data.snapshot;
  rememberCollection(cid, (snap.collection || {}).title, 'layout');
  snap.items = groupedItems(snap.items, snap.sections);
  let options = { ...snap.options }, overrides = structuredClone(data.layout.items || {});
  let selected = snap.items[0]?.id || '', version = 'student', mode = 'quick', dirty = false, pdf = null, disposed = false, painting = 0;
  let interactionTimer;
  root.classList.add('layout-page');
  ctx.onCleanup(() => { disposed = true;clearTimeout(interactionTimer);root.classList.remove('layout-page'); });
  const payload = () => ({ options: Object.fromEntries(['title', 'subtitle', 'pretitle', 'creator', 'motto', 'theme', 'format', 'cover', 'toc', 'show_sources', 'teacher_notes', 'options_layout'].map(k => [k, options[k] ?? (['cover', 'teacher_notes'].includes(k) ? true : ['toc', 'show_sources'].includes(k) ? false : '')])), items: overrides, order: snap.items.map(i=>({id:i.id,section_id:i.section_id||null})) });
  const store = draftStore(data.draft_scope, `layout:${cid}`), recovered = store.read();
  const $ = s => root.querySelector(s);
  const effective = item => ({ ...item, ...(overrides[item.id] || {}) });
  root.innerHTML = `<div class="page-head"><div><h1 class="page-title">排版与预览</h1><p class="page-sub">${esc(snap.collection.title)}，${snap.items.length}道题</p></div><div class="detail-actions"><span class="layout-save-state" data-save-state role="status">已保存</span><a class="btn" href="#/collection/${encodeURIComponent(cid)}">返回题单</a><button class="btn btn-sm" data-save>保存排版</button><button class="btn btn-primary" data-publish ${snap.items.length ? '' : 'disabled'}>导出三版讲义</button></div></div>
    <div data-recovery></div><div class="layout-workspace"><aside class="layout-controls">
      <section class="card"><h2>讲义设置</h2>
        <label class="field"><span>讲义标题</span><input data-option="title" value="${esc(options.title||'')}" maxlength="300"></label>
        <label class="field"><span>版面密度</span><select data-option="format">${selectOptions(FORMATS, options.format)}</select></label>
        <details class="compact-fold"><summary>封面、署名与显示</summary><div class="compact-fold-body">
        ${[['subtitle', '副标题'], ['pretitle', '封面前置语'], ['creator', '编写署名']].map(([k,t]) => `<label class="field"><span>${t}</span><input data-option="${k}" value="${esc(options[k] || '')}" maxlength="300"></label>`).join('')}
        <label class="field"><span>主题色</span><select data-option="theme">${selectOptions({ blue: '青蓝', green: '松绿', purple: '浅紫', orange: '暖橙' }, options.theme)}</select></label>
        <label class="field"><span>全卷选项排列</span><select data-option="options_layout">${selectOptions({ auto: '自动：放得下时横排', vertical: '全部竖排' }, options.options_layout || 'auto')}</select></label>
        <div class="layout-checks">${[['cover','显示封面'],['show_sources','显示题目来源'],['teacher_notes','教师版讲评提示']].map(([k,t]) => `<label><input type="checkbox" data-option="${k}" ${options[k] ? 'checked' : ''}>${t}</label>`).join('')}</div>
        </div></details>
      </section>
      <section class="card"><h2>当前题目</h2><div class="layout-item-list" data-item-list></div><div data-item-settings></div></section>
      <details class="card compact-card"><summary>常用讲义样式</summary><div data-templates class="compact-card-body"></div></details>
      <details class="compact-fold layout-help"><summary>操作帮助</summary><div class="compact-fold-body"><p>拖动题目左侧把手排序，拖动作答区底边调整留白；也可使用方向键。Esc取消拖动。</p><p>修改自动保存，PDF校样不新增发布记录。快速预览估算分页，最终位置以PDF校样为准。</p></div></details>
    </aside><section class="layout-stage"><div class="layout-toolbar"><div class="layout-modes"><button class="btn btn-sm active" data-mode="quick">快速预览</button><button class="btn btn-sm" data-mode="pdf" data-compile ${snap.items.length ? '' : 'disabled'}>PDF校样</button></div><select data-version aria-label="预览版本">${selectOptions(VERSIONS, version)}</select><label class="layout-zoom">缩放<select data-zoom aria-label="预览缩放"><option value="0.65">65%</option><option value="0.8" selected>80%</option><option value="1">100%</option></select></label></div>
      <div class="layout-status"><span data-status role="status">分页为估算</span><span data-interaction role="status"></span></div><div class="layout-page-nav" data-page-nav aria-label="预览页码"></div><div class="layout-canvas" data-canvas><div data-pages></div><div data-pdf hidden></div></div><div data-export role="status"></div></section></div>`;
  // Friendly labels without binding model values to arbitrary HTML.
  $('[data-option="theme"]').innerHTML = selectOptions({ blue: '青蓝', green: '松绿', purple: '浅紫', orange: '暖橙', ...(!COLORS[options.theme] ? { [options.theme]: options.theme } : {}) }, options.theme);
  const saver = autosaver({read:payload,store,base:data.revision,
    save:(value,base)=>api(`/api/collections/${encodeURIComponent(cid)}/layout`,{method:'PUT',body:{...value,base_revision:base}}),
    onState:(state,error)=>{if(disposed)return;dirty=state!=='saved';$('[data-save-state]').textContent=({pending:'待保存',saving:'正在保存…',saved:'已保存',unprotected:'浏览器无法保留草稿，正在尝试保存到题库',error:`保存失败，修改仍保留在本页：${error?.message||''}`})[state];}
  });
  ctx.onCleanup(()=>saver.dispose());
  const beforeUnload=e=>{if(saver.dirty){e.preventDefault();e.returnValue='';}};
  window.addEventListener('beforeunload',beforeUnload);ctx.onCleanup(()=>window.removeEventListener('beforeunload',beforeUnload));
  function syncControls(){root.querySelectorAll('[data-option]').forEach(input=>{const v=options[input.dataset.option];if(input.type==='checkbox')input.checked=Boolean(v);else input.value=v??'';});renderItemSettings();paint();}
  if(recovered){
    $('[data-recovery]').innerHTML='<div class="draft-banner"><span>发现上次未完成的排版草稿。</span><button class="btn btn-sm" data-restore>恢复草稿</button><button class="btn btn-sm" data-discard>丢弃草稿</button></div>';
    $('[data-restore]').onclick=async()=>{
      if(recovered.base!==data.revision&&!await confirmDialog({title:'核对旧草稿',message:'题单已在草稿之后发生修改。恢复会用草稿替换当前通用排版和留白，请确认要恢复。',confirmText:'恢复并核对'}))return;
      if(disposed)return;options={...options,...recovered.data.options};const ids=new Set(snap.items.map(i=>i.id));overrides=Object.fromEntries(Object.entries(recovered.data.items||{}).filter(([id])=>ids.has(id)));
      if(Array.isArray(recovered.data.order)){
        const byId=new Map(snap.items.map(i=>[i.id,i])),sections=new Set(snap.sections.map(s=>s.id)),seen=new Set(),rows=[];
        for(const e of recovered.data.order){if(!byId.has(e.id)||seen.has(e.id))continue;seen.add(e.id);const item=byId.get(e.id);rows.push({...item,section_id:e.section_id===null||sections.has(e.section_id)?e.section_id:item.section_id});}
        snap.items=groupedItems([...rows,...snap.items.filter(i=>!seen.has(i.id))],snap.sections);
      }
      $('[data-recovery]').replaceChildren();markDirty();syncControls();
    };
    $('[data-discard]').onclick=()=>{store.clear();$('[data-recovery]').replaceChildren();};
  }
  mountLayoutTemplates($('[data-templates]'),{read:payload,current:()=>!disposed,apply:t=>{options={...options,...t.options};snap.items.forEach(i=>{overrides[i.id]={...(overrides[i.id]||{}),space_mm:t.space_mm};});markDirty();syncControls();}});

  function markDirty() {
    dirty = true; pdf = null;
    saver.change();
    $('[data-recovery]').replaceChildren();
    $('[data-pdf]').innerHTML = '<div class="layout-empty">设置已变化，请重新生成PDF校样。</div>';
    $('[data-status]').textContent = mode === 'pdf' ? 'PDF校样已过期，请重新生成' : '快速预览，分页为估算';
  }
  function renderItemSettings() {
    $('[data-item-list]').innerHTML=snap.items.map((i,n)=>`<button class="btn btn-sm" data-pick="${esc(i.id)}" aria-label="定位第${n+1}题">${n+1}</button>`).join('');
    root.querySelectorAll('[data-pick]').forEach(b => b.classList.toggle('selected', b.dataset.pick === selected));
    const original = snap.items.find(i => i.id === selected);
    if (!original) { $('[data-item-settings]').innerHTML = '<p class="hint">题单为空，请先返回题单添加题目。</p>'; return; }
    const item = effective(original), num = snap.items.indexOf(original) + 1;
    $('[data-item-settings]').innerHTML = `<h3>第${num}题</h3><label class="field"><span>学生版作答留白（毫米）</span><input type="number" data-item="space_mm" min="0" max="200" step="5" value="${Number(item.space_mm || 0)}"></label><div class="layout-checks"><label><input type="checkbox" data-item="page_break_before" ${item.page_break_before ? 'checked' : ''}>本题从新页开始</label></div><details class="compact-fold"><summary>选项与完整性</summary><div class="compact-fold-body"><label class="field"><span>本题选项排列</span><select data-item="options_layout">${selectOptions({ inherit: '跟随整卷设置', auto: '自动横排或竖排', vertical: '竖排' }, overrides[selected]?.options_layout || 'inherit')}</select></label><label class="check"><input type="checkbox" data-item="keep_together" ${item.keep_together ? 'checked' : ''}>尽量保持本题完整</label><p class="hint">超出一页的长题仍会分开。</p><button class="btn btn-sm" data-reset>恢复本题原排版</button></div></details>`;
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
      html += `<div class="paper-space ${i.space_mm?'':'paper-space-empty'}" style="height:${Number(i.space_mm||0)}mm"><span data-space-label>作答区：${Number(i.space_mm||0)} mm</span><button type="button" class="paper-space-handle" data-space-resize role="slider" aria-label="调整第${number}题作答留白" aria-valuemin="0" aria-valuemax="200" aria-valuenow="${Number(i.space_mm||0)}" aria-valuetext="${Number(i.space_mm||0)}毫米" title="拖动调整留白；上下方向键每次调整5毫米">↕</button></div>`;
    } else {
      const selectedSolutions = (entry.solutions || []).filter(s => !i.solution_id || s.id === i.solution_id);
      const answer = q.answer || (q.answer_keys || []).join('、') || subs.map(s => s.answer ? `（${s.key}）${s.answer}` : '').filter(Boolean).join('；') || '（略）';
      const solution = selectedSolutions.length ? selectedSolutions.map(s => `<div><strong>${esc(s.label || '解法')}</strong>${richToHtml(s.steps)}${s.conditions ? `<p>适用条件：${richToHtml(s.conditions)}</p>` : ''}</div>`).join('') : richToHtml(q.analysis || '');
      html += `<div class="paper-solution">${version === 'teacher' ? `<h4>解析</h4>${solution}` : ''}<h4>答案</h4>${richToHtml(answer)}`;
      if (version === 'teacher' && options.teacher_notes) html += Object.entries({ why: '为什么想到', key_step: '关键一步', pitfalls: '易错点', probe: '追问' }).filter(([k]) => q.teaching?.[k]).map(([k,t]) => `<p><b>${t}：</b>${richToHtml(q.teaching[k])}</p>`).join('');
      html += '</div>';
    }
    return `<article class="paper-question ${selected === i.id ? 'selected' : ''}" data-paper-item="${esc(i.id)}" tabindex="0" aria-label="选择第${number}题"><button type="button" class="paper-drag-handle" data-paper-drag aria-label="拖动第${number}题排序" title="拖动排序；上下方向键移动题目">⠿</button>${html}</article>`;
  }
  function paint() {
    if (disposed) return;
    const run = ++painting, stage = $('[data-pages]');
    const canvas=$('[data-canvas]'),scrollTop=canvas.scrollTop,scrollLeft=canvas.scrollLeft;
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
    const cover = options.cover ? `<div class="layout-paper paper-cover"><div>${esc(options.pretitle || '')}</div><h1>${esc(options.title)}</h1><h2>${esc(options.subtitle || '')}（${VERSIONS[version]}）</h2><footer>${esc(options.creator || '')}</footer></div>` : '';
    const measurement=document.createElement('div');measurement.className='paper-measure';measurement.innerHTML=blocks.map(b=>`<div>${b.html}</div>`).join('');stage.appendChild(measurement);
    measurement.querySelectorAll('.paper-choices.auto').forEach(c => {
      const widths = [...c.children].map(el => { const range = document.createRange(); range.selectNodeContents(el); return range.getBoundingClientRect().width / Number($('[data-zoom]').value); });
      if (c.children.length === 4 && widths.every(w => w < (measurement.clientWidth - 60) / 4)) c.classList.add('horizontal');
    });
    [...measurement.children].forEach((el,i) => { blocks[i].height = Math.ceil(el.getBoundingClientRect().height / Number($('[data-zoom]').value)) + 8; blocks[i].html = el.innerHTML; });
    const pages = paginateBlocks(blocks, 920);
    if (run !== painting) return;
    const label = `<span>${esc(options.title)}</span><span>${VERSIONS[version]}</span>`;
    stage.innerHTML = cover + pages.map((page,n) => `<div class="layout-paper"><div class="paper-header">${label}</div><div class="paper-body">${page.map(b => b.html).join('')}</div><div class="paper-footer">第 ${n+1} 页（快速预览）</div></div>`).join('');
    canvas.scrollTop=scrollTop;canvas.scrollLeft=scrollLeft;
    if (!blocks.length) stage.innerHTML = '<div class="layout-empty">题单中还没有题目，请返回题单添加。</div>';
    if (mode === 'quick') $('[data-status]').textContent = `约${pages.length + (options.cover ? 1 : 0)}页，最终分页以PDF校样为准`;
    const papers=[...stage.querySelectorAll('.layout-paper')];
    $('[data-page-nav]').innerHTML=papers.map((paper,n)=>`<button class="layout-page-thumb ${paper.querySelector('.paper-question.selected')?'active':''}" data-page-index="${n}" aria-label="跳到${paper.classList.contains('paper-cover')?'封面':'第'+(n+(options.cover?0:1))+'页'}"><span aria-hidden="true">▤</span>${paper.classList.contains('paper-cover')?'封面':'第'+(n+(options.cover?0:1))+'页'}</button>`).join('');
    $('[data-page-nav]').hidden=mode!=='quick'||papers.length<2;
    stage.querySelectorAll('[data-paper-item]').forEach(el => {
      const pick = () => selectItem(el.dataset.paperItem);
      el.onclick = e=>{if(!e.target.closest('[data-paper-drag],[data-space-resize]'))pick();};
      el.onkeydown = e => { if(e.target===el&&e.key==='Enter')pick(); };
    });
    // Late image dimensions must trigger another measurement.
    stage.querySelectorAll('img').forEach(img => { if (!img.complete) img.onload = () => { if (run === painting) paint(); }; });
  }
  async function save() {
    await saver.flush();
    dirty = false; if (disposed) return; $('[data-save-state]').textContent = '已保存';
  }
  function showMode(next) {
    mode = next; $('[data-pages]').hidden = mode !== 'quick'; $('[data-pdf]').hidden = mode !== 'pdf';
    $('[data-page-nav]').hidden=mode!=='quick'||$('[data-pages]').querySelectorAll('.layout-paper').length<2;$('[data-interaction]').textContent='';
    root.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('active', b.dataset.mode === next));
    if (mode === 'pdf') {
      if (!pdf) {
        $('[data-pdf]').innerHTML = '<div class="layout-empty">点击上方“PDF校样”生成当前版本。</div>';
        $('[data-status]').textContent = '当前版本尚未生成PDF校样';
      } else {
        const warnings = Number(pdf.checks?.overfull || 0) + Number(pdf.checks?.missing_char || 0);
        $('[data-status]').textContent = warnings ? `PDF校样已生成，有${warnings}项溢出或缺字提示，请检查` : `PDF校样已生成，${pdf.pages || '?'}页，与正式导出共用排版引擎`;
      }
    }
    if (mode === 'quick') paint();
  }
  root.querySelectorAll('[data-option]').forEach(input => { const update = () => { options[input.dataset.option] = input.type === 'checkbox' ? input.checked : input.value; markDirty(); paint(); }; input.onchange = update; if (input.type === 'text') input.oninput = update; });
  function selectItem(id){selected=id;renderItemSettings();$('[data-pages]').querySelectorAll('[data-paper-item]').forEach(el=>el.classList.toggle('selected',el.dataset.paperItem===id));}
  function focusItem(id,selector){const el=[...$('[data-pages]').querySelectorAll('[data-paper-item]')].find(el=>el.dataset.paperItem===id),target=el?.querySelector(selector),canvas=$('[data-canvas]');if(target){const rect=target.getBoundingClientRect(),box=canvas.getBoundingClientRect();if(rect.top<box.top+15)canvas.scrollTop+=rect.top-box.top-15;else if(rect.bottom>box.bottom-15)canvas.scrollTop+=rect.bottom-box.bottom+15;target.focus({preventScroll:true});}}
  $('[data-item-list]').onclick=e=>{const b=e.target.closest('[data-pick]');if(b){selectItem(b.dataset.pick);focusItem(selected,'[data-paper-drag]');}};
  $('[data-page-nav]').onclick=e=>{const button=e.target.closest('[data-page-index]');if(!button)return;const paper=$('[data-pages]').querySelectorAll('.layout-paper')[Number(button.dataset.pageIndex)],canvas=$('[data-canvas]');if(paper){canvas.scrollTop+=paper.getBoundingClientRect().top-canvas.getBoundingClientRect().top-20;root.querySelectorAll('[data-page-index]').forEach(b=>b.classList.toggle('active',b===button));}};
  ctx.onCleanup(bindLayoutInteractions($('[data-canvas]'),{
    select:selectItem,
    move:(id,target,before)=>{const next=movePreviewItem(snap.items,snap.sections,id,target,before);if(next===snap.items)return;snap.items=next;selected=id;markDirty();renderItemSettings();paint();focusItem(id,'[data-paper-drag]');},
    resize:(id,mm)=>{selected=id;overrides[id]={...(overrides[id]||{}),space_mm:mm};markDirty();renderItemSettings();paint();focusItem(id,'[data-space-resize]');},
    announce:message=>{if(disposed)return;clearTimeout(interactionTimer);$('[data-interaction]').textContent=message;interactionTimer=setTimeout(()=>{if(!disposed)$('[data-interaction]').textContent='';},3500);}
  }));
  $('[data-mode="quick"]').onclick=()=>showMode('quick');
  $('[data-version]').onchange = () => { version = $('[data-version]').value; pdf = null; paint(); if (mode === 'pdf') showMode('pdf'); };
  $('[data-zoom]').onchange = () => { $('[data-pages]').style.zoom = $('[data-zoom]').value; paint(); };
  $('[data-save]').onclick = async () => { try { await save(); } catch(e) { flash(e.message); } };
  async function generatePreview(force=false) {
    if(pdf&&!force){showMode('pdf');return;}
    const button = $('[data-compile]'), requestedVersion = version, signature = JSON.stringify(payload());
    if(button.disabled)return;
    button.disabled = true; button.textContent = '正在生成…'; $('[data-status]').textContent = '正在使用正式排版引擎生成校样…';
    try {
      const result = await api(`/api/collections/${encodeURIComponent(cid)}/layout/pdf`, { method: 'POST', body: { version, layout: payload() } });
      if (disposed) return;
      if (requestedVersion !== version || signature !== JSON.stringify(payload())) { $('[data-status]').textContent = '设置已变化，请重新生成校样'; return; }
      pdf = result; showMode('pdf');
      $('[data-pdf]').innerHTML = `<div class="layout-pdf-link"><a class="btn" href="${esc(pdf.url)}" target="_blank" rel="noopener">打开 / 下载PDF</a><button class="btn btn-sm" data-pdf-refresh>重新生成</button><span>${VERSIONS[version]}，${pdf.pages || '?'}页</span></div><iframe title="讲义实际PDF校样" src="${esc(pdf.url)}#toolbar=1&view=FitH"></iframe>`;
    } catch(e) { if (!disposed) { $('[data-status]').textContent = e.message; flash(e.message); } }
    finally { if (!disposed) { button.disabled = false; button.textContent = 'PDF校样'; } }
  }
  $('[data-compile]').onclick=()=>generatePreview();
  $('[data-pdf]').onclick=e=>{if(e.target.closest('[data-pdf-refresh]'))generatePreview(true);};
  $('[data-publish]').onclick = async () => {
    const b = $('[data-publish]'); b.disabled = true; b.textContent = '正在导出…';
    try {
      await save();
      const result = await api(`/api/collections/${encodeURIComponent(cid)}/build`, { method: 'POST', body: { ...payload().options, versions: ['student', 'answer', 'teacher'] } });
      if (disposed) return;
      $('[data-export]').innerHTML = `<div class="card"><h3>${result.release?.status === 'failed' ? '导出未完成，请到归档查看诊断' : '三版讲义已导出'}</h3><div class="detail-actions">${Object.entries(result.files || {}).filter(([,f]) => f.url).map(([v,f]) => `<a class="btn" target="_blank" rel="noopener" href="${esc(f.url)}">${VERSIONS[v]}，${f.pages || '?'}页</a>`).join('')}<a class="btn" href="#/releases">查看归档</a></div></div>`;
      document.dispatchEvent(new Event('meta:refresh'));
    } catch(e) { flash(e.message); }
    finally { if (!disposed) { b.disabled = false; b.textContent = '导出三版讲义'; } }
  };
  renderItemSettings(); $('[data-pages]').style.zoom = '0.8'; paint();
  loadKatex().then(() => { if (!disposed) paint(); });
}
