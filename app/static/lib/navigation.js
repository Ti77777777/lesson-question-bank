import { esc } from './dom.js';

export const IMPORT_LINKS = [['/ai', '导入试卷'], ['/question/new', '手工录题'], ['/inbox', '收题箱']];
export function importTabs(path) {
  return `<nav class="page-tabs" aria-label="导入方式">${IMPORT_LINKS.map(([url, name]) => `<a class="btn ${path === url ? 'active' : ''}" href="#${url}" ${path === url ? 'aria-current="page"' : ''}>${name}</a>`).join('')}</nav>`;
}

// Menus keep native details/summary keyboard behavior; Escape returns focus.
export function bindMenus(root = document) {
  const menus = () => [...root.querySelectorAll('details.compact-actions, details.nav-more')];
  const click = event => menus().forEach(menu => {
    if (!menu.contains(event.target) || event.target.closest('a,button')) menu.open = false;
  });
  const key = event => {
    if (event.key !== 'Escape') return;
    const menu = event.target.closest('details.compact-actions[open],details.nav-more[open]');
    if (menu) { menu.open = false; menu.querySelector('summary').focus(); event.preventDefault(); event.stopPropagation(); }
  };
  root.addEventListener('click', click); root.addEventListener('keydown', key);
  return () => { root.removeEventListener('click', click); root.removeEventListener('keydown', key); };
}

export function bindPanels(root, { initial = '', onChange = () => {} } = {}) {
  const buttons = [...root.querySelectorAll('[data-panel-link]')];
  const panels = [...root.querySelectorAll('[data-panel]')];
  function select(id) {
    if (!panels.some(p => p.dataset.panel === id)) id = panels[0]?.dataset.panel;
    panels.forEach(p => p.hidden = p.dataset.panel !== id);
    buttons.forEach(b => { const active = b.dataset.panelLink === id; b.classList.toggle('active', active); b.setAttribute('aria-pressed', String(active)); });
    onChange(id);
  }
  buttons.forEach(b => b.addEventListener('click', () => select(b.dataset.panelLink)));
  select(initial);
  return select;
}

export function panelLinks(items) {
  return `<nav class="page-tabs" aria-label="使用记录栏目">${items.map(([id,name]) => `<button class="btn" type="button" data-panel-link="${esc(id)}">${esc(name)}</button>`).join('')}</nav>`;
}
