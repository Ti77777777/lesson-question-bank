/* 通用界面部件：顶部提示条、弹窗、确认框、标签选择器、提交态。 */

import { esc, h } from './dom.js';
import { TAG_CATEGORIES, makeTagDirectory, filterTagDirectory } from './tag-taxonomy.js';
import { upgradeMath } from './math.js';

export function flash(message, kind = 'error') {
  const bar = document.getElementById('flash');
  if (!bar) return;
  bar.hidden = false;
  bar.className = `flash ${kind === 'ok' ? 'flash-ok' : ''}`.trim();
  bar.replaceChildren();

  const text = document.createElement('div');
  text.className = 'flash-text';
  text.textContent = String(message ?? '');
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'flash-close';
  close.textContent = '关闭';
  close.addEventListener('click', () => { bar.hidden = true; });
  bar.append(text, close);
}

export function clearFlash() {
  const bar = document.getElementById('flash');
  if (!bar) return;
  bar.hidden = true;
  bar.replaceChildren();
}

const PENDING_FLASH = 'qbank.pendingFlash';

/** 成功提示要在 hash 跳转后仍然可见；路由渲染完成后取用。 */
export function flashAfterNavigation(message, kind = 'ok') {
  try {
    sessionStorage.setItem(PENDING_FLASH, JSON.stringify({ message: String(message ?? ''), kind }));
  } catch {
    /* 存储不可用时退化为普通提示 */
    flash(message, kind);
  }
}

export function consumePendingFlash() {
  try {
    const raw = sessionStorage.getItem(PENDING_FLASH);
    if (!raw) return;
    sessionStorage.removeItem(PENDING_FLASH);
    const data = JSON.parse(raw);
    if (data?.message) flash(data.message, data.kind || 'ok');
  } catch {
    /* 忽略损坏的缓存 */
  }
}

export function openModal({
  title,
  body = '',
  footer = '',
  width = '',
  flush = false,
  onMount,
  onClose,
} = {}) {
  const root = document.getElementById('modal-root') || document.body;
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = `<div class="modal ${width}" role="dialog" aria-modal="true" aria-label="${esc(title || '对话框')}">
    <div class="modal-head">
      <h2>${esc(title || '')}</h2>
      <button type="button" class="modal-close" data-close aria-label="关闭">×</button>
    </div>
    <div class="modal-body ${flush ? 'flush' : ''}">${body}</div>
    ${footer ? `<div class="modal-foot">${footer}</div>` : ''}
  </div>`;

  const modal = mask.querySelector('.modal');
  const bodyEl = mask.querySelector('.modal-body');
  const footEl = mask.querySelector('.modal-foot');
  if (flush) bodyEl.classList.add('flush');

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    mask.remove();
    document.removeEventListener('keydown', onKey);
    if (!root.querySelector('.modal-mask')) document.body.classList.remove('modal-open');
    if (onClose) onClose();
  };

  function onKey(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  }

  mask.addEventListener('click', (event) => {
    if (event.target === mask || event.target.closest('[data-close]')) close();
  });
  document.addEventListener('keydown', onKey);
  document.body.classList.add('modal-open');
  root.appendChild(mask);

  if (onMount) onMount({ mask, modal, body: bodyEl, foot: footEl, close });
  return { mask, modal, body: bodyEl, foot: footEl, close };
}

export function modalError(container) {
  const node = h('<div class="modal-error" hidden></div>');
  container.prepend(node);
  return {
    show(message) {
      node.hidden = false;
      node.textContent = String(message ?? '');
    },
    hide() {
      node.hidden = true;
    },
  };
}

/** 按钮提交态 + 错误就地显示。fn 抛错时把 message 原样放进 errorEl。 */
export async function runSubmit(button, errorEl, fn) {
  const original = button ? button.textContent : '';
  if (button) {
    button.disabled = true;
    button.textContent = '提交中…';
  }
  if (errorEl) {
    errorEl.hidden = true;
    errorEl.textContent = '';
  }
  try {
    await fn();
    return true;
  } catch (err) {
    const message = err?.message || '提交失败';
    if (errorEl) {
      errorEl.hidden = false;
      errorEl.textContent = message;
    }
    flash(message);
    return false;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

export function confirmDialog({
  title = '请确认',
  message = '',
  confirmText = '确定',
  cancelText = '取消',
  danger = false,
} = {}) {
  return new Promise((resolve) => {
    let result = false;
    const modal = openModal({
      title,
      width: 'modal-narrow',
      body: `<p style="font-size:13.5px;line-height:1.75;white-space:pre-wrap;">${esc(message)}</p>`,
      footer: `<button type="button" class="btn" data-cancel>${esc(cancelText)}</button>
               <button type="button" class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-ok>${esc(confirmText)}</button>`,
      onClose: () => resolve(result),
    });
    modal.foot.querySelector('[data-cancel]').addEventListener('click', modal.close);
    modal.foot.querySelector('[data-ok]').addEventListener('click', () => {
      result = true;
      modal.close();
    });
  });
}

/** 单行或多行文本输入对话框，返回字符串或 null。 */
export function textDialog({
  title = '填写',
  label = '',
  value = '',
  placeholder = '',
  hint = '',
  required = true,
  multiline = false,
  confirmText = '提交',
} = {}) {
  return new Promise((resolve) => {
    let result = null;
    const input = multiline
      ? `<textarea data-input rows="3" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`
      : `<input data-input type="text" value="${esc(value)}" placeholder="${esc(placeholder)}">`;

    const modal = openModal({
      title,
      width: 'modal-narrow',
      body: `<label class="field"><span>${esc(label)}${required ? '<span class="req">*</span>' : ''}</span>
              ${input}${hint ? `<span class="field-hint">${esc(hint)}</span>` : ''}</label>
              <div class="modal-error" hidden></div>`,
      footer: `<button type="button" class="btn" data-cancel>取消</button>
               <button type="button" class="btn btn-primary" data-ok>${esc(confirmText)}</button>`,
      onMount: ({ body }) => {
        const field = body.querySelector('[data-input]');
        field.focus();
        field.addEventListener('keydown', (event) => {
          if (!multiline && event.key === 'Enter') {
            event.preventDefault();
            modal.foot.querySelector('[data-ok]').click();
          }
        });
      },
      onClose: () => resolve(result),
    });

    const error = modal.body.querySelector('.modal-error');
    modal.foot.querySelector('[data-cancel]').addEventListener('click', modal.close);
    modal.foot.querySelector('[data-ok]').addEventListener('click', () => {
      const text = modal.body.querySelector('[data-input]').value.trim();
      if (required && !text) {
        error.hidden = false;
        error.textContent = '请填写后再提交。';
        return;
      }
      result = text;
      modal.close();
    });
  });
}

/**
 * 标签选择器。tags 为 [{id,name,dimension,definition,question_count}]。
 * 返回选中的标签数组；取消返回 null。
 */
export function pickTags({
  tags = [],
  selected = [],
  multiple = true,
  title = '选择标签',
  confirmText = '确定',
} = {}) {
  return new Promise((resolve) => {
    let result = null;
    const directory = makeTagDirectory(tags);
    const byId = new Map(directory.map(e => [e.tag.id, e]));
    const chosen = new Set(selected.filter(id => byId.has(id)));
    const categories = [...TAG_CATEGORIES, ['other', '其他标签', '尚未归入常用维度的标签']]
      .filter(([id]) => directory.some(e => e.dimension === id));
    let dimension = categories[0]?.[0] || '', topic = '', keyword = '', selectedOnly = false, topicMarkup = '';

    const modal = openModal({
      title,
      width: 'modal-tag-library',
      body: `<div class="tag-library-toolbar">
               <label class="tag-library-search"><span class="sr-only">搜索标签</span><input type="search" data-search placeholder="搜索当前分类的名称、说明或主题" autocomplete="off"></label>
               <label class="check"><input type="checkbox" data-show-definitions>显示说明</label>
             </div>
             <div class="tag-library-selection"><div class="tag-selection-head"><span data-selection-title>已选标签</span><button type="button" class="btn-link" data-clear-selected>清空已选</button></div><div data-selected-tags></div></div>
             <div class="tag-library-browser">
               <nav class="tag-library-nav" aria-label="标签分类">
                 ${[['', '全部标签'], ...categories].map(([id, name]) => `<button type="button" data-dimension="${id}" aria-pressed="false"><span>${esc(name)}</span><span class="tag-nav-meta"><span data-nav-selected></span><span class="tag-nav-count">${directory.filter(e => !id || e.dimension === id).length}</span></span></button>`).join('')}
               </nav>
               <div class="tag-library-content">
                 <div class="tag-library-context"><div><h3 data-category-title></h3><p data-category-hint></p></div><button type="button" class="tag-selected-toggle" data-selected-only aria-pressed="false">只看已选</button></div>
                 <div class="tag-library-topics" data-topics aria-label="标签主题"></div>
                 <p class="tag-library-status" data-visible-count role="status" aria-live="polite"></p>
                 <div class="tag-library-list" data-list></div>
               </div>
             </div>`,
      footer: `<span class="hint" data-count role="status" aria-live="polite" style="margin-right:auto;"></span>
               <button type="button" class="btn" data-cancel>取消</button>
               <button type="button" class="btn btn-primary" data-ok>${esc(confirmText)}</button>`,
      onClose: () => resolve(result),
    });
    modal.modal.classList.add('tag-library');

    const list = modal.body.querySelector('[data-list]');
    const countEl = modal.foot.querySelector('[data-count]');

    const render = () => {
      const criteria = {dimension, keyword, selectedOnly, chosen};
      const inCategory = filterTagDirectory(directory, criteria);
      const topics = [...new Set(inCategory.map(e => e.topic))];
      if (topic && !topics.includes(topic)) topics.push(topic);
      const markup = [{id: '', name: '全部主题', count: inCategory.length}, ...topics.map(name => ({id: name, name, count: inCategory.filter(e => e.topic === name).length}))]
        .map(t => `<button type="button" data-topic="${esc(t.id)}" class="tag-topic">${esc(t.name)}<small>${t.count}</small></button>`).join('');
      if (markup !== topicMarkup) { modal.body.querySelector('[data-topics]').innerHTML = markup; topicMarkup = markup; }
      modal.body.querySelectorAll('[data-topic]').forEach(btn => { const on = btn.dataset.topic === topic; btn.classList.toggle('on', on); btn.setAttribute('aria-pressed', String(on)); });
      const visible = filterTagDirectory(directory, {...criteria, topic});
      const currentCategory = categories.find(c => c[0] === dimension);
      modal.body.querySelector('[data-category-title]').textContent = currentCategory?.[1] || '全部标签';
      modal.body.querySelector('[data-category-hint]').textContent = currentCategory?.[2] || '可按分类与主题浏览，也可在全部标签中搜索';
      modal.body.querySelector('[data-visible-count]').textContent = `当前显示 ${visible.length} 个${selectedOnly ? '已选' : ''}标签${keyword.trim() ? ` · 关键词「${keyword.trim()}」` : ''}`;
      modal.body.querySelector('[data-search]').placeholder = dimension ? `在${currentCategory?.[1] || '当前分类'}中搜索名称、说明或主题` : '搜索全部分类的标签名称、说明或主题';
      modal.body.querySelectorAll('[data-dimension]').forEach(btn => {
        const on = btn.dataset.dimension === dimension;
        btn.classList.toggle('on', on); btn.setAttribute('aria-pressed', String(on));
        const n = directory.filter(e => chosen.has(e.tag.id) && (!btn.dataset.dimension || e.dimension === btn.dataset.dimension)).length;
        btn.querySelector('[data-nav-selected]').textContent = n ? `${n} 已选` : '';
      });
      const groups = new Map();
      visible.forEach(entry => {
        const key = `${entry.dimension}\0${entry.topic}`;
        if (!groups.has(key)) groups.set(key, {dimension: entry.dimension, topic: entry.topic, items: []});
        groups.get(key).items.push(entry.tag);
      });
      list.innerHTML = visible.length ? [...groups.values()].map(group => `<section class="tag-library-group">
        <h4>${!dimension ? `${esc(categories.find(c => c[0] === group.dimension)?.[1] || '其他标签')} / ` : ''}${esc(group.topic)}<span>${group.items.length}</span></h4>
        <div class="tag-library-grid">${group.items.map(tag => `<label class="tag-library-option ${chosen.has(tag.id) ? 'selected' : ''}">
          <input type="${multiple ? 'checkbox' : 'radio'}" name="tag-pick" value="${esc(tag.id)}" aria-label="${esc(tag.name)}" ${chosen.has(tag.id) ? 'checked' : ''}>
          <span class="tag-library-option-text"><span class="tag-library-name" data-rich>${esc(tag.name)}</span>${tag.definition ? `<span class="tag-library-description" data-rich>${esc(tag.definition)}</span>` : ''}</span>
        </label>`).join('')}</div></section>`).join('') : `<div class="tag-library-empty"><strong>${directory.length ? '当前范围没有匹配标签' : '标签库为空'}</strong><p>${selectedOnly ? '可切换到其他分类，或关闭“只看已选”。' : '可切换到“全部标签”搜索，或清除当前主题和关键词。'}</p>${directory.length ? '<button type="button" class="btn" data-reset-filter>清除当前筛选</button>' : ''}</div>`;
      const selectedEntries = directory.filter(e => chosen.has(e.tag.id));
      modal.body.querySelector('[data-selection-title]').textContent = `已选标签 · ${chosen.size}`;
      modal.body.querySelector('[data-selected-tags]').innerHTML = selectedEntries.length ? selectedEntries.map(e => `<button type="button" class="tag-selection-chip" data-remove-tag="${esc(e.tag.id)}" aria-label="移除：${esc(e.tag.name)}"><small>${esc(categories.find(c => c[0] === e.dimension)?.[1] || '其他')}</small><span data-rich>${esc(e.tag.name)}</span><span aria-hidden="true">×</span></button>`).join('') : '<span class="hint">可跨分类选择；切换主题或搜索时，已选标签会保留。</span>';
      modal.body.querySelector('[data-clear-selected]').disabled = !chosen.size;
      modal.body.querySelector('[data-selected-only]').setAttribute('aria-pressed', String(selectedOnly));
      countEl.textContent = `已选 ${chosen.size} 个`;
      upgradeMath(modal.body);
    };

    list.addEventListener('change', (event) => {
      const input = event.target.closest('input[name="tag-pick"]');
      if (!input) return;
      if (!multiple) chosen.clear();
      if (input.checked) chosen.add(input.value);
      else chosen.delete(input.value);
      render();
      list.querySelector(`input[value="${CSS.escape(input.value)}"]`)?.focus({preventScroll: true});
    });

    modal.body.addEventListener('click', event => {
      const category = event.target.closest('[data-dimension]');
      const theme = event.target.closest('[data-topic]');
      const remove = event.target.closest('[data-remove-tag]');
      if (category) { dimension = category.dataset.dimension; topic = ''; }
      else if (theme) topic = theme.dataset.topic;
      else if (remove) chosen.delete(remove.dataset.removeTag);
      else if (event.target.closest('[data-clear-selected]')) chosen.clear();
      else if (event.target.closest('[data-selected-only]')) selectedOnly = !selectedOnly;
      else if (event.target.closest('[data-reset-filter]')) { topic = keyword = ''; selectedOnly = false; modal.body.querySelector('[data-search]').value = ''; }
      else return;
      list.scrollTop = 0;
      render();
    });
    modal.body.querySelector('[data-show-definitions]').addEventListener('change', event => {
      modal.modal.classList.toggle('show-definitions', event.target.checked);
    });

    modal.body.querySelector('[data-search]').addEventListener('input', (event) => {
      keyword = event.target.value;
      render();
    });

    modal.foot.querySelector('[data-cancel]').addEventListener('click', modal.close);
    modal.foot.querySelector('[data-ok]').addEventListener('click', () => {
      result = directory.filter(e => chosen.has(e.tag.id)).map(e => e.tag);
      modal.close();
    });

    render();
    countEl.textContent = `已选 ${chosen.size} 个`;
  });
}
