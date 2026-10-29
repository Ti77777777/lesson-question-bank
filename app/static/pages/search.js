/* 题库检索：常用条件即时筛选，完整条件保存在链接中。 */
import { api, apiOptional } from '../lib/api.js';
import { esc, badge, $, $$, emptyState, errorCard, loading, num } from '../lib/dom.js';
import { QTYPES, SOURCE_GRADES, reviewLabel, reviewTone } from '../lib/constants.js';
import { flash } from '../lib/ui.js';
import { mountTagSidebar } from '../lib/tag-sidebar.js';
import { openBasket } from '../lib/bus.js';
import { addToBasket, readBasket, removeFromBasket } from '../lib/basket.js';
import { upgradeMath } from '../lib/math.js';
import { openCorrections } from '../lib/corrections.js';
import { mountSavedSearches } from '../lib/saved-searches.js';
import { openQuestionDrawer } from '../lib/question-drawer.js';

const SORTS = [['recent', '最近录入'], ['difficulty', '思维难度优先'], ['used', '最近使用']];
const LEVELS = [['', '不限'], ['basic', '基础', '1–2'], ['regular', '常规', '3'], ['advanced', '进阶', '4–5'], ['unrated', '未标注']];
const csv = value => new Set((value || '').split(',').filter(Boolean));
const validScore = value => /^[1-5]$/.test(value || '') ? value : '';

export default async function mount(root, ctx) {
  const query = ctx.query;
  const state = {
    keyword: query.keyword || '', sourceId: query.source_id || '',
    category: query.source_category || '', year: query.source_year || '', region: query.source_region || '',
    grade: query.source_grade || '',
    qtypes: new Set([...csv(query.qtype)].filter(x => x in QTYPES)), tags: csv(query.tags),
    tagMode: ['all', 'any', 'exclude'].includes(query.tag_mode) ? query.tag_mode : 'all',
    thinkMin: validScore(query.think_min), thinkMax: validScore(query.think_max),
    calcMax: validScore(query.calc_max), scopeMax: validScore(query.scope_max),
    unrated: query.think_unrated === '1', onlyUsable: query.review === 'pass',
    onlyCorrections: query.correction === 'pending',
    sort: SORTS.some(x => x[0] === query.sort) ? query.sort : 'recent',
  };
  if (state.unrated) state.thinkMin = state.thinkMax = '';
  let tagList = [], sources = [], categories = [], items = [], total = 0;
  let grades = [...Object.entries(SOURCE_GRADES).map(([id,name])=>({id,name})),{id:'_unknown',name:'未标注'}];
  let tagError = '', tagsLoading = true, revision = 0, busy = false, appliedRevision = -1, timer, categoryMarkup = '',gradeMarkup='';
  root.classList.add('search-workbench');
  ctx.onCleanup?.(() => { clearTimeout(timer); root.classList.remove('search-workbench'); });

  root.innerHTML = `
    <div class="page-head search-heading">
      <div><p class="search-eyebrow">备课 · 选题</p><h1 class="page-title">题库检索</h1>
        <p class="page-sub">从知识点到具体试卷，找到这节课需要的题。</p></div>
      <div class="row-actions"><a class="btn" href="#/sources">管理试卷信息</a><button type="button" class="btn" id="search-basket">查看选题篮</button></div>
    </div>
    <div class="search-layout">
    <aside class="search-tag-sidebar" id="search-tag-sidebar" aria-label="知识点与方法标签筛选"></aside>
    <div class="search-main">
    <form class="search-query-bar" id="search-form" role="search">
      <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>
      <input aria-label="关键词" type="search" id="f-keyword" placeholder="搜索学校、试卷名称、题面或答案…" autocomplete="off" value="${esc(state.keyword)}">
      <button type="submit" class="btn btn-primary" id="f-search">检索</button>
    </form>
    <div class="saved-searches" id="saved-searches"></div>
    <section class="card search-filters" aria-label="题目筛选">
      <div class="search-filter-top"><span>筛选条件</span><span class="hint">切换条件后自动更新</span></div>
      <div class="search-filter-row"><span class="search-filter-label">来源类别</span><div class="search-options" id="f-categories"></div></div>
      <div class="search-filter-row"><span class="search-filter-label">适用年级</span><div class="search-options" id="f-grades"></div></div>
      <div class="search-filter-row"><span class="search-filter-label">题型</span><div class="search-options" id="f-qtypes">
        <button type="button" class="search-option" data-qtype="">全部</button>
        ${Object.entries(QTYPES).map(([id, name]) => `<button type="button" class="search-option" data-qtype="${id}">${name}</button>`).join('')}
        <span class="search-inline-hint">可多选</span>
      </div></div>
      <div class="search-filter-row"><span class="search-filter-label">思维难度</span><div class="search-options" id="f-levels">
        ${LEVELS.map(([id, name, score]) => `<button type="button" class="search-option" data-level="${id}">${name}${score ? `<small>${score}</small>` : ''}</button>`).join('')}
        <span class="search-inline-hint" id="custom-level" hidden>自定义区间</span>
      </div></div>
      <div class="search-source-area">
        <div class="search-source-title">定位试卷 <span>按年份、地区或完整卷名缩小范围</span></div>
        <div class="search-source-fields">
          <label class="field"><span>年份</span><select id="f-year"><option value="">全部年份</option></select></label>
          <label class="field"><span>地区 / 卷别</span><select id="f-region"><option value="">全部地区 / 卷别</option></select></label>
          <label class="field search-source-select"><span>来源试卷</span><select id="f-source"><option value="">全部试卷与资料</option></select></label>
        </div>
        <p class="search-source-caption" id="source-caption" hidden></p>
      </div>
      <details class="search-advanced" id="f-advanced">
        <summary>更多条件 <span id="advanced-count"></span></summary>
        <div class="search-advanced-body">
          <div class="search-advanced-fields">
            <label class="field"><span>思维下限</span><select id="f-think-min">${rangeOptions()}</select></label>
            <label class="field"><span>思维上限</span><select id="f-think-max">${rangeOptions()}</select></label>
            <label class="field"><span>计算上限</span><select id="f-calc-max">${rangeOptions()}</select></label>
            <label class="field"><span>综合上限</span><select id="f-scope-max">${rangeOptions()}</select></label>
          </div>
          <p class="hint">难度为题库中的 1–5 级标注；未标注的题目不会计入指定难度区间。</p>
        </div>
      </details>
      <div class="search-active" id="active-filters" aria-label="已选条件"></div>
      <p class="search-validation" id="filter-error" role="alert" hidden></p>
    </section>
    <div class="results-head search-results-head">
      <div class="search-result-count" id="results-total" role="status" aria-live="polite"></div>
      <span class="hint" id="results-note"></span><span class="spacer"></span>
      <label class="check"><input type="checkbox" id="f-usable"> 仅看审校通过</label>
      <label class="check"><input type="checkbox" id="f-corrections"> 仅看待纠错</label>
      <select id="f-sort" aria-label="结果排序">${SORTS.map(([id, name]) => `<option value="${id}">${name}</option>`).join('')}</select>
    </div>
    <div class="q-list" id="results" aria-busy="false"></div><div class="pager" id="pager"></div>
    </div></div>`;

  const resultsEl = $('#results', root), pagerEl = $('#pager', root);
  ctx.onCleanup?.(mountSavedSearches($('#saved-searches',root),()=>{state.keyword=$('#f-keyword',root).value.trim();return buildQuery();}));
  const totalEl = $('#results-total', root), noteEl = $('#results-note', root);
  const tagSidebar = mountTagSidebar($('#search-tag-sidebar', root), {
    onChange: ids => { state.tags = new Set(ids); changed(); },
    onModeChange: mode => { state.tagMode = mode; changed(); },
    onRetry: loadTags,
  });
  ctx.onCleanup?.(() => tagSidebar.destroy());
  function rangeOptions() {
    return '<option value="">不限</option>' + [1, 2, 3, 4, 5].map(n => `<option value="${n}">${n}</option>`).join('');
  }
  function preset() {
    if (state.unrated) return 'unrated';
    if (!state.thinkMin && !state.thinkMax) return '';
    if (state.thinkMin === '1' && state.thinkMax === '2') return 'basic';
    if (state.thinkMin === '3' && state.thinkMax === '3') return 'regular';
    if (state.thinkMin === '4' && state.thinkMax === '5') return 'advanced';
    return 'custom';
  }
  function sourceMatches(source, skip = '') {
    return ['category', 'year', 'region','grade'].every(key => key === skip || !state[key] || (source[key] || '_unknown') === state[key]);
  }
  function renderSourceOptions() {
    for (const [key, selector, all] of [['year', '#f-year', '全部年份'], ['region', '#f-region', '全部地区 / 卷别']]) {
      const values = [...new Set(sources.filter(s => sourceMatches(s, key)).map(s => s[key] || '_unknown'))];
      if (state[key] && !values.includes(state[key])) values.push(state[key]);
      values.sort((a, b) => key === 'year' ? b.localeCompare(a) : a.localeCompare(b, 'zh-CN'));
      $(selector, root).innerHTML = `<option value="">${all}</option>` + values.map(v => `<option value="${esc(v)}">${v === '_unknown' ? '未标注' : esc(v)}</option>`).join('');
      $(selector, root).value = state[key];
    }
    const available = sources.filter(s => sourceMatches(s));
    const selected = sources.find(s => s.id === state.sourceId);
    if (selected && !available.includes(selected)) available.push(selected);
    $('#f-source', root).innerHTML = '<option value="">全部试卷与资料</option>' + available.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
    $('#f-source', root).value = state.sourceId;
    const caption = $('#source-caption', root);
    caption.hidden = !state.sourceId;
    caption.textContent = selected ? `当前试卷：${selected.name}` : state.sourceId ? `来源编号：${state.sourceId}` : '';
  }
  function activeConditions() {
    const list = [];
    const push = (key, name) => list.push({key, name});
    if (state.keyword) push('keyword', `关键词：${state.keyword}`);
    if (state.category) push('category', categories.find(c => c.id === state.category)?.name || state.category);
    if (state.grade) push('grade', `年级：${grades.find(g=>g.id===state.grade)?.name||state.grade}`);
    if (state.year) push('year', `年份：${state.year === '_unknown' ? '未标注' : state.year}`);
    if (state.region) push('region', `地区 / 卷别：${state.region === '_unknown' ? '未标注' : state.region}`);
    if (state.sourceId) push('sourceId', sources.find(s => s.id === state.sourceId)?.name || state.sourceId);
    for (const id of state.qtypes) push(`type:${id}`, QTYPES[id]);
    if (state.unrated) push('think', '思维难度：未标注');
    else if (state.thinkMin || state.thinkMax) push('think', `思维：${state.thinkMin || '1'}–${state.thinkMax || '5'}`);
    if (state.calcMax) push('calcMax', `计算 ≤ ${state.calcMax}`);
    if (state.scopeMax) push('scopeMax', `综合 ≤ ${state.scopeMax}`);
    for (const id of state.tags) push(`tag:${id}`, `${state.tagMode === 'exclude' ? '排除：' : ''}${tagList.find(t => t.id === id)?.name || id}`);
    if (state.onlyUsable) push('onlyUsable', '审校通过');
    if (state.onlyCorrections) push('onlyCorrections', '待纠错');
    return list;
  }
  function renderControls() {
    const markup = [{id: '', name: '全部'}, ...categories].map(c => `<button type="button" class="search-option" data-category="${esc(c.id)}">${esc(c.name)}</button>`).join('');
    if (markup !== categoryMarkup) { $('#f-categories', root).innerHTML = markup; categoryMarkup = markup; }
    const gradeButtons=[{id:'',name:'全部'},...grades].map(g=>`<button type="button" class="search-option" data-grade="${esc(g.id)}">${esc(g.name)}</button>`).join('');
    if(gradeButtons!==gradeMarkup){$('#f-grades',root).innerHTML=gradeButtons;gradeMarkup=gradeButtons;}
    for (const [selector, attr, selected] of [
      ['#f-categories button', 'category', id => state.category === id],
      ['#f-grades button','grade',id=>state.grade===id],
      ['#f-qtypes button', 'qtype', id => id ? state.qtypes.has(id) : !state.qtypes.size],
      ['#f-levels button', 'level', id => preset() === id],
    ]) $$(selector, root).forEach(btn => { const on = selected(btn.dataset[attr]); btn.classList.toggle('on', on); btn.setAttribute('aria-pressed', String(on)); });
    $('#custom-level', root).hidden = preset() !== 'custom';
    tagSidebar.update({tags: tagList, selected: state.tags, mode: state.tagMode, loading: tagsLoading, error: tagError});
    for (const [key, id] of [['thinkMin', 'think-min'], ['thinkMax', 'think-max'], ['calcMax', 'calc-max'], ['scopeMax', 'scope-max'], ['sort', 'sort']]) $(`#f-${id}`, root).value = state[key];
    $('#f-usable', root).checked = state.onlyUsable;
    $('#f-corrections', root).checked = state.onlyCorrections;
    const extra = [state.calcMax, state.scopeMax].filter(Boolean).length;
    $('#advanced-count', root).textContent = extra ? `· ${extra} 项` : '';
    renderSourceOptions();
    const filters = activeConditions();
    $('#active-filters', root).innerHTML = filters.length ? `<span class="search-active-label">已选 ${filters.length} 项</span><div class="search-active-chips">${filters.map(f => `<button type="button" class="search-active-chip" data-remove="${esc(f.key)}" title="移除：${esc(f.name)}">${esc(f.name)}<span aria-hidden="true">×</span></button>`).join('')}</div><button type="button" class="btn-link" id="f-reset">清空条件</button>` : '<span class="hint">当前检索全部题目，可叠加多个条件。</span>';
  }
  function buildQuery(offset = 0) {
    return {
      keyword: state.keyword || undefined, source_id: state.sourceId || undefined,
      source_category: state.category || undefined, source_year: state.year || undefined, source_region: state.region || undefined,
      source_grade: state.grade || undefined,
      qtype: [...state.qtypes].join(',') || undefined, tags: [...state.tags].join(',') || undefined,
      tag_mode: state.tags.size ? state.tagMode : undefined,
      think_min: state.thinkMin || undefined, think_max: state.thinkMax || undefined,
      think_unrated: state.unrated ? '1' : undefined, calc_max: state.calcMax || undefined, scope_max: state.scopeMax || undefined,
      review: state.onlyUsable ? 'pass' : undefined, sort: state.sort === 'recent' ? undefined : state.sort,
      correction: state.onlyCorrections ? 'pending' : undefined,
      status: 'active', limit: 30, offset,
    };
  }
  function syncUrl() {
    const params = new URLSearchParams();
    Object.entries(buildQuery()).forEach(([k, v]) => { if (v !== undefined && !['status', 'limit', 'offset'].includes(k)) params.set(k, v); });
    history.replaceState(null, '', `#/search${params.size ? '?' + params.toString() : ''}`);
  }
  function changed() {
    clearTimeout(timer);
    state.keyword = $('#f-keyword', root).value.trim();
    revision++;
    renderControls(); syncUrl(); search();
  }
  function valid() {
    const bad = state.thinkMin && state.thinkMax && +state.thinkMin > +state.thinkMax;
    const el = $('#filter-error', root); el.hidden = !bad;
    el.textContent = bad ? '思维难度下限不能大于上限，请调整区间。' : '';
    if (bad) {
      totalEl.textContent = '请调整筛选条件'; noteEl.textContent = ''; pagerEl.innerHTML = '';
      resultsEl.innerHTML = emptyState('难度区间需要调整', '请将思维下限设为小于或等于上限。');
    }
    return !bad;
  }
  async function search() {
    if (!valid() || busy) return;
    busy = true;
    const current = revision, request = buildQuery();
    resultsEl.setAttribute('aria-busy', 'true');
    resultsEl.innerHTML = loading('正在检索…'); pagerEl.innerHTML = '';
    totalEl.textContent = '检索中…'; noteEl.textContent = '';
    try {
      const data = await api('/api/questions', {query: request});
      if (!ctx.current() || current !== revision) return;
      items = data.items || []; total = Number(data.total ?? items.length); appliedRevision = current;
      renderResults();
    } catch (err) {
      if (!ctx.current() || current !== revision) return;
      totalEl.textContent = '检索未完成';
      resultsEl.replaceChildren(errorCard(err.message, search)); items = [];
    } finally {
      busy = false;
      if (ctx.current()) { resultsEl.setAttribute('aria-busy', 'false'); if (current !== revision) search(); }
    }
  }
  async function loadMore() {
    if (busy || !valid() || appliedRevision !== revision) return;
    busy = true;
    const current = revision, btn = $('#load-more', root);
    if (btn) { btn.disabled = true; btn.textContent = '读取中…'; }
    try {
      const data = await api('/api/questions', {query: buildQuery(items.length)});
      if (!ctx.current() || current !== revision) return;
      items = items.concat(data.items || []); total = Number(data.total ?? total); renderResults();
    } catch (err) {
      if (ctx.current() && current === revision) { flash(err.message); if (btn) { btn.disabled = false; btn.textContent = '加载更多'; } }
    } finally {
      busy = false;
      if (ctx.current() && current !== revision) search();
    }
  }
  function renderResults() {
    totalEl.innerHTML = `共 <b>${esc(total)}</b> 道`;
    noteEl.textContent = '';
    if (!items.length) {
      resultsEl.innerHTML = emptyState(
        '没有符合条件的题目',
        '可以放宽难度区间、换一种标签匹配方式，或先到收题箱整理新题。',
      );
      pagerEl.innerHTML = '';
      return;
    }

    const basket = new Set(readBasket());
    resultsEl.innerHTML = items.map((item) => {
      const dif = item.difficulty || {};
      const tags = Array.isArray(item.tags) ? item.tags : [];
      const methods = Array.isArray(item.methods) ? item.methods : [];
      const shownTags = tags.slice(0, 5);
      const shownMethods = methods.slice(0, 3);
      const inBasketNow = basket.has(item.id);
      const title = item.short_name || '（未命名）';
      const stem = item.stem || item.stem_preview || '';
      return `<article class="q-card" data-open="${esc(item.id)}">
        <div class="q-card-main">
          <div class="q-meta-top">
            <a class="qid" href="#/question/${esc(item.id)}" data-preview="${esc(item.id)}">${esc(item.id)}</a>
            ${badge(QTYPES[item.qtype] || item.qtype || '未分题型', 'plain')}
            ${badge(reviewLabel(item.review_state), reviewTone(item.review_state))}
            ${item.status === 'inactive' ? badge('停用', 'na') : ''}
            ${item.correction_pending ? badge(`待纠错 ${item.correction_pending}`, 'pending') : ''}
            <span class="name">${esc(title)}</span>
          </div>
          <div class="q-stem" data-rich>${esc(stem || '（题面预览为空）')}</div>
          ${item.source_label && item.source_label !== item.short_name ? `<div class="source-caption">来源：${esc(item.source_label)}</div>` : ''}
          <div class="q-tags">
            ${shownTags.map((tag) => badge(tag.name, 'neutral')).join('')}
            ${tags.length > shownTags.length ? badge(`+${tags.length - shownTags.length}`, 'plain') : ''}
            ${shownMethods.map((tag) => badge(`方法 · ${tag.name}`, 'violet')).join('')}
            ${methods.length > shownMethods.length ? badge(`+${methods.length - shownMethods.length} 法`, 'plain') : ''}
          </div>
        </div>
        <div class="q-side">
          <div class="diff-line">
            ${diffCell('计算', dif.calc)}
            ${diffCell('思维', dif.think)}
            ${diffCell('综合', dif.scope)}
          </div>
          <div class="used">最近使用：<b>${item.used_on ? esc(item.used_on.slice(0, 10)) : '无记录'}</b></div>
          <button type="button" class="btn btn-sm" data-preview="${esc(item.id)}">查看题目</button>
          <button type="button" class="btn btn-sm ${inBasketNow ? '' : 'btn-primary'}" data-add="${esc(item.id)}">
            ${inBasketNow ? '移出选题篮' : '加入选题篮'}
          </button>
          <button type="button" class="btn btn-sm" data-correct="${esc(item.id)}" aria-label="纠错 ${esc(item.id)}">纠错${item.correction_count ? ` · ${item.correction_count}` : ''}</button>
        </div>
      </article>`;
    }).join('');

    upgradeMath(resultsEl);

    const loaded = items.length;
    noteEl.textContent = loaded < total ? `已显示 ${loaded} / ${total}` : '';

    if (loaded < total) {
      pagerEl.innerHTML = '<button type="button" class="btn" id="load-more">加载更多</button>';
      $('#load-more', root).addEventListener('click', () => loadMore());
    } else {
      pagerEl.innerHTML = '';
    }
  }

  function diffCell(label, value) {
    return `<div class="diff-cell"><span class="k">${label}</span><span class="v">${esc(num(value))}</span></div>`;
  }

  function syncBasketButtons() {
    const basket = new Set(readBasket());
    $$('[data-add]', resultsEl).forEach((btn) => {
      const id = btn.dataset.add;
      const active = basket.has(id);
      btn.textContent = active ? '移出选题篮' : '加入选题篮';
      btn.classList.toggle('btn-primary', !active);
    });
    const headBtn = $('#search-basket', root);
    if (headBtn) headBtn.textContent = `查看选题篮（${basket.size}）`;
  }

  resultsEl.addEventListener('click', (event) => {
    const preview=event.target.closest('[data-preview]');
    if(preview&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&!event.altKey){event.preventDefault();showPreview(preview.dataset.preview);return;}
    const correctionBtn = event.target.closest('[data-correct]');
    if (correctionBtn) {
      event.preventDefault();
      const item = items.find(item => item.id === correctionBtn.dataset.correct);
      if (item) {
        const modal = openCorrections({questionId: item.id, versionId: item.version_id, onChanged: ({pendingCount, totalCount}) => {
          if (!ctx.current()) return;
          const existing = items.find(row => row.id === item.id);
          if (!existing) return;
          existing.correction_count = totalCount;
          existing.correction_pending = pendingCount;
          if (state.onlyCorrections && !pendingCount) { items = items.filter(row => row.id !== item.id); total--; }
          renderResults();
        }});
        ctx.onCleanup?.(modal.close);
      }
      return;
    }
    const addBtn = event.target.closest('[data-add]');
    if (addBtn) {
      event.preventDefault();
      const id = addBtn.dataset.add;
      if (readBasket().includes(id)) removeFromBasket(id);
      else addToBasket(id);
      syncBasketButtons();
      return;
    }
    if (event.target.closest('a, button, input, select')) return;
    const card = event.target.closest('[data-open]');
    if (card && !window.getSelection()?.toString()) showPreview(card.dataset.open);
  });

  function showPreview(id){
    const modal=openQuestionDrawer({id,ids:items.map(i=>i.id),onCorrection:(qid,{pendingCount,totalCount})=>{
      if(!ctx.current())return;const row=items.find(i=>i.id===qid);if(!row)return;
      row.correction_pending=pendingCount;row.correction_count=totalCount;
      if(state.onlyCorrections&&!pendingCount){items=items.filter(i=>i.id!==qid);total--;}
      renderResults();
    }});ctx.onCleanup?.(modal.close);
  }

  $('#search-basket', root).addEventListener('click', openBasket);
  document.addEventListener('basket:change', syncBasketButtons);
  if (ctx.onCleanup) ctx.onCleanup(() => document.removeEventListener('basket:change', syncBasketButtons));


  $('#f-categories', root).addEventListener('click', event => {
    const btn = event.target.closest('[data-category]'); if (!btn) return;
    state.category = btn.dataset.category;
    // 上级来源类别改变时清除试卷定位，避免“高考 + 月考试卷”这样的隐性冲突。
    state.sourceId = state.year = state.region = ''; changed();
  });
  $('#f-grades',root).addEventListener('click',event=>{
    const btn=event.target.closest('[data-grade]');if(!btn)return;
    state.grade=btn.dataset.grade;state.sourceId='';changed();
  });
  $('#f-qtypes', root).addEventListener('click', event => {
    const btn = event.target.closest('[data-qtype]'); if (!btn) return;
    const id = btn.dataset.qtype;
    if (!id) state.qtypes.clear(); else if (state.qtypes.has(id)) state.qtypes.delete(id); else state.qtypes.add(id);
    changed();
  });
  $('#f-levels', root).addEventListener('click', event => {
    const btn = event.target.closest('[data-level]'); if (!btn) return;
    const level = btn.dataset.level;
    [state.thinkMin, state.thinkMax] = ({basic: ['1', '2'], regular: ['3', '3'], advanced: ['4', '5']})[level] || ['', ''];
    state.unrated = level === 'unrated'; changed();
  });
  for (const [id, key] of [['year', 'year'], ['region', 'region'], ['source', 'sourceId']]) {
    $(`#f-${id}`, root).addEventListener('change', event => {
      state[key] = event.target.value;
      if (key !== 'sourceId') state.sourceId = '';
      changed();
    });
  }
  for (const [id, key] of [['think-min', 'thinkMin'], ['think-max', 'thinkMax'], ['calc-max', 'calcMax'], ['scope-max', 'scopeMax'], ['sort', 'sort']]) {
    $(`#f-${id}`, root).addEventListener('change', event => {
      state[key] = event.target.value;
      if (key === 'thinkMin' || key === 'thinkMax') state.unrated = false;
      changed();
    });
  }
  $('#f-usable', root).addEventListener('change', event => { state.onlyUsable = event.target.checked; changed(); });
  $('#f-corrections', root).addEventListener('change', event => { state.onlyCorrections = event.target.checked; changed(); });
  $('#active-filters', root).addEventListener('click', event => {
    const btn = event.target.closest('[data-remove]');
    if (btn) {
      const key = btn.dataset.remove;
      if (key.startsWith('type:')) state.qtypes.delete(key.slice(5));
      else if (key.startsWith('tag:')) state.tags.delete(key.slice(4));
      else if (key === 'think') { state.thinkMin = state.thinkMax = ''; state.unrated = false; }
      else state[key] = ['onlyUsable', 'onlyCorrections'].includes(key) ? false : '';
      if (key === 'keyword') $('#f-keyword', root).value = '';
      changed();
    }
    if (event.target.closest('#f-reset')) {
      Object.assign(state, {keyword: '', sourceId: '', category: '', year: '', region: '',grade:'', qtypes: new Set(), tags: new Set(), tagMode: 'all', thinkMin: '', thinkMax: '', calcMax: '', scopeMax: '', unrated: false, onlyUsable: false, onlyCorrections: false});
      $('#f-keyword', root).value = ''; changed();
    }
  });
  function submitKeyword() { state.keyword = $('#f-keyword', root).value.trim(); changed(); }
  $('#search-form', root).addEventListener('submit', event => { event.preventDefault(); submitKeyword(); });
  $('#f-keyword', root).addEventListener('input', event => {
    clearTimeout(timer);
    if (event.isComposing) return;
    timer = setTimeout(submitKeyword, 400);
  });
  $('#f-keyword', root).addEventListener('compositionend', () => { clearTimeout(timer); timer = setTimeout(submitKeyword, 400); });
  async function loadTags() {
    tagsLoading = true; tagError = ''; renderControls();
    const {data, error} = await apiOptional('/api/tags');
    if (!ctx.current()) return;
    tagList = data?.items || []; tagError = error?.message || ''; tagsLoading = false; renderControls();
  }
  async function loadCatalog() {
    const {data, error} = await apiOptional('/api/search/catalog');
    if (!ctx.current()) return;
    if (error) {
      const fallback = await apiOptional('/api/sources');
      if (!ctx.current()) return;
      sources = (fallback.data?.items || []).map(s => ({...s, category: '', year: ''}));
      flash('来源分类暂不可用，仍可按完整试卷名称检索。');
    } else { sources = data.sources || []; categories = data.categories || [];grades=data.grades||grades; }
    renderControls();
  }
  renderControls(); syncBasketButtons();
  if (state.calcMax || state.scopeMax || preset() === 'custom') $('#f-advanced', root).open = true;
  // 正文检索与筛选目录并行，目录迟到时不会重置用户刚选的条件。
  await Promise.all([loadTags(), loadCatalog(), search()]);
}
