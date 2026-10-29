/* 页面与外壳之间的轻量事件。 */

export function refreshMeta() {
  document.dispatchEvent(new Event('meta:refresh'));
}

export function openBasket() {
  document.dispatchEvent(new Event('basket:open'));
}

export function basketChanged() {
  document.dispatchEvent(new Event('basket:change'));
}
