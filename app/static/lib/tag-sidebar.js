/* 检索页常驻标签侧栏。浏览状态与题目筛选状态分开，勾选即时回传。 */
import {esc} from './dom.js';
import {TAG_CATEGORIES, makeTagDirectory, filterTagDirectory} from './tag-taxonomy.js';
import {upgradeMath} from './math.js';

const MODES = [['all', '同时包含'], ['any', '任一包含'], ['exclude', '排除所选']];
const MODE_HINTS = {all: '题目须包含所选全部标签', any: '命中任意一个所选标签即可', exclude: '排除带有所选任一标签的题目'};

export function mountTagSidebar(container, {onChange, onModeChange, onRetry} = {}) {
  let directory = [], categories = [], chosen = new Set(), mode = 'all';
  let dimension = 'knowledge', topic = '', keyword = '', selectedOnly = false;
  let tagsRef, selectionKey = '', loading = true, error = '', destroyed = false, navMarkup = '';
  const expanded = new Map();
  container.innerHTML = `<details class="card tag-side-panel">
    <summary class="tag-side-heading"><span><strong>知识点与方法</strong><small>按类别选标签 · 勾选即筛选</small></span><span class="tag-side-count" data-side-count>0</span><span class="tag-side-disclosure" aria-hidden="true"></span></summary>
    <div class="tag-side-body">
      <label class="tag-side-search"><span class="sr-only">搜索侧栏标签</span><input type="search" data-side-search placeholder="搜索标签名称或说明" autocomplete="off"></label>
      <nav class="tag-side-categories" data-side-categories aria-label="标签类别"></nav>
      <div class="tag-side-fields">
        <label><span>主题</span><select data-side-topic aria-label="标签主题"><option value="">全部主题</option></select></label>
        <label><span>匹配方式</span><select data-side-mode aria-label="标签匹配方式">${MODES.map(([id, name]) => `<option value="${id}">${name}</option>`).join('')}</select></label>
      </div>
      <p class="tag-side-mode-hint" data-side-mode-hint></p>
      <div class="tag-side-tools"><label class="check"><input type="checkbox" data-side-definitions>显示说明</label><button type="button" data-side-only class="btn-link" aria-pressed="false">只看已选</button></div>
      <section class="tag-side-selected" data-side-selection hidden><div><span data-side-selection-title></span><button type="button" class="btn-link" data-side-clear>清空标签</button></div><div data-side-chips></div></section>
      <p class="tag-side-status" data-side-status role="status" aria-live="polite"></p>
      <div class="tag-side-list" data-side-list></div>
    </div>
  </details>`;
  const panel = container.querySelector('details');
  const list = container.querySelector('[data-side-list]');
  const media = window.matchMedia('(min-width: 1000px)');
  panel.open = media.matches;
  const onBreakpoint = event => { panel.open = event.matches; };
  media.addEventListener('change', onBreakpoint);

  function render() {
    if (destroyed) return;
    container.querySelector('[data-side-count]').textContent = chosen.size;
    container.querySelector('[data-side-count]').setAttribute('aria-label', `已选 ${chosen.size} 个标签`);
    container.querySelector('[data-side-mode]').value = mode;
    container.querySelector('[data-side-mode-hint]').textContent = MODE_HINTS[mode];
    const nav = [['', '全部', ''], ...categories];
    const markup = nav.map(([id, name]) => `<button type="button" data-side-dimension="${id}" aria-pressed="false"><span>${esc(name)}</span><small>${directory.filter(e => !id || e.dimension === id).length}</small></button>`).join('');
    if (markup !== navMarkup) { container.querySelector('[data-side-categories]').innerHTML = markup; navMarkup = markup; }
    container.querySelectorAll('[data-side-dimension]').forEach(button => {
      const active = button.dataset.sideDimension === dimension;
      button.classList.toggle('on', active); button.setAttribute('aria-pressed', String(active));
    });
    const label = categories.find(c => c[0] === dimension)?.[1];
    container.querySelector('[data-side-search]').placeholder = label ? `在${label}中搜索名称或说明` : '搜索全部标签名称或说明';
    const criteria = {dimension, keyword, selectedOnly, chosen};
    const available = filterTagDirectory(directory, criteria);
    const topics = [...new Set(available.map(e => e.topic))];
    if (topic && !topics.includes(topic)) topics.push(topic);
    const theme = container.querySelector('[data-side-topic]');
    theme.innerHTML = '<option value="">全部主题</option>' + topics.map(name => `<option value="${esc(name)}">${esc(name)} · ${available.filter(e => e.topic === name).length}</option>`).join('');
    theme.value = topic;
    const visible = filterTagDirectory(directory, {...criteria, topic});
    const byId = new Map(directory.map(e => [e.tag.id, e]));
    container.querySelector('[data-side-selection]').hidden = !chosen.size;
    container.querySelector('[data-side-selection-title]').textContent = `已选 ${chosen.size} 个`;
    container.querySelector('[data-side-chips]').innerHTML = [...chosen].map(id => `<button type="button" data-side-remove="${esc(id)}" aria-label="移除标签：${esc(byId.get(id)?.tag.name || id)}"><span data-rich>${esc(byId.get(id)?.tag.name || id)}</span><span aria-hidden="true">×</span></button>`).join('');
    container.querySelector('[data-side-only]').setAttribute('aria-pressed', String(selectedOnly));
    container.querySelector('[data-side-status]').textContent = loading ? '正在读取标签…' : error ? '标签暂不可用，仍可使用其他筛选条件' : `当前显示 ${visible.length} 个标签${selectedOnly ? ' · 只看已选' : ''}`;

    const scroll = list.scrollTop;
    list.querySelectorAll('[data-side-group]').forEach(group => expanded.set(group.dataset.sideGroup, group.open));
    const groups = new Map();
    visible.forEach(entry => {
      const key = `${entry.dimension}:${entry.topic}`;
      if (!groups.has(key)) groups.set(key, {key, dimension: entry.dimension, name: entry.topic, entries: []});
      groups.get(key).entries.push(entry);
    });
    if (loading) list.innerHTML = '<p class="tag-side-empty">标签目录加载后在此显示。</p>';
    else if (error) list.innerHTML = `<div class="tag-side-empty"><p>${esc(error)}</p><button type="button" class="btn btn-sm" data-side-retry>重新读取标签</button></div>`;
    else if (!visible.length) list.innerHTML = `<div class="tag-side-empty"><strong>${directory.length ? '当前范围没有匹配标签' : '暂无可用标签'}</strong><p>${selectedOnly ? '可切换类别，或关闭“只看已选”。' : '可切换到“全部”搜索其他类别。'}</p>${directory.length ? '<button type="button" class="btn btn-sm" data-side-reset>清除浏览筛选</button>' : ''}</div>`;
    else list.innerHTML = [...groups.values()].map((group, index) => {
      const open = topic || keyword.trim() || selectedOnly || (expanded.has(group.key) ? expanded.get(group.key) : index === 0 || group.entries.some(e => chosen.has(e.tag.id)));
      return `<details class="tag-side-group" data-side-group="${esc(group.key)}" ${open ? 'open' : ''}><summary><span>${!dimension ? `${esc(categories.find(c => c[0] === group.dimension)?.[1] || '其他')} / ` : ''}${esc(group.name)}</span><small>${group.entries.length}</small></summary><div>${group.entries.map(({tag}) => `<label class="tag-side-option ${chosen.has(tag.id) ? 'selected' : ''}"><input type="checkbox" data-side-tag="${esc(tag.id)}" aria-label="${esc(tag.name)}" ${chosen.has(tag.id) ? 'checked' : ''}><span><span class="tag-side-name" data-rich>${esc(tag.name)}</span>${tag.definition ? `<small class="tag-side-description" data-rich>${esc(tag.definition)}</small>` : ''}</span></label>`).join('')}</div></details>`;
    }).join('');
    list.scrollTop = scroll;
    upgradeMath(container);
  }

  function onClick(event) {
    const category = event.target.closest('[data-side-dimension]');
    const remove = event.target.closest('[data-side-remove]');
    if (category) { dimension = category.dataset.sideDimension; topic = ''; list.scrollTop = 0; render(); }
    else if (remove) { const next = new Set(chosen); next.delete(remove.dataset.sideRemove); onChange?.([...next]); }
    else if (event.target.closest('[data-side-clear]')) onChange?.([]);
    else if (event.target.closest('[data-side-only]')) { selectedOnly = !selectedOnly; list.scrollTop = 0; render(); }
    else if (event.target.closest('[data-side-reset]')) {
      topic = keyword = ''; selectedOnly = false; container.querySelector('[data-side-search]').value = ''; list.scrollTop = 0; render();
    } else if (event.target.closest('[data-side-retry]')) onRetry?.();
  }
  function onInput(event) {
    if (event.target.matches('[data-side-search]')) { keyword = event.target.value; list.scrollTop = 0; render(); }
  }
  function onChangeEvent(event) {
    const target = event.target;
    if (target.matches('[data-side-tag]')) {
      const next = new Set(chosen), id = target.dataset.sideTag;
      if (target.checked) next.add(id); else next.delete(id);
      onChange?.([...next]);
      container.querySelector(`[data-side-tag="${CSS.escape(id)}"]`)?.focus({preventScroll: true});
    } else if (target.matches('[data-side-topic]')) { topic = target.value; list.scrollTop = 0; render(); }
    else if (target.matches('[data-side-mode]')) onModeChange?.(target.value);
    else if (target.matches('[data-side-definitions]')) panel.classList.toggle('show-definitions', target.checked);
  }
  container.addEventListener('click', onClick);
  container.addEventListener('input', onInput);
  container.addEventListener('change', onChangeEvent);
  render();
  return {
    update({tags = tagsRef || [], selected = [], mode: nextMode = 'all', loading: nextLoading = false, error: nextError = ''} = {}) {
      const nextKey = [...selected].sort().join(',');
      if (tags === tagsRef && nextKey === selectionKey && mode === nextMode && loading === nextLoading && error === nextError) return;
      if (tags !== tagsRef) {
        tagsRef = tags; directory = makeTagDirectory(tags);
        categories = [...TAG_CATEGORIES, ['other', '其他标签', '']].filter(([id]) => directory.some(e => e.dimension === id));
        if (dimension && categories.length && !categories.some(c => c[0] === dimension)) { dimension = categories[0][0]; topic = ''; }
      }
      chosen = new Set(selected); selectionKey = nextKey; mode = nextMode; loading = nextLoading; error = nextError; render();
    },
    destroy() {
      destroyed = true; media.removeEventListener('change', onBreakpoint);
      container.removeEventListener('click', onClick); container.removeEventListener('input', onInput); container.removeEventListener('change', onChangeEvent);
    },
  };
}
