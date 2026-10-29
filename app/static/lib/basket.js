/* 选题篮：localStorage 中只存题目 ID 数组，顺序即选入顺序。 */

const KEY = 'qbank.basket';

export function readBasket() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    if (!Array.isArray(list)) return [];
    return list.filter((id) => typeof id === 'string' && id);
  } catch {
    return [];
  }
}

function persist(ids) {
  const unique = [...new Set(ids)];
  try {
    localStorage.setItem(KEY, JSON.stringify(unique));
  } catch {
    /* 存储不可用时保持界面可用 */
  }
  updateBasketBadge(unique);
  document.dispatchEvent(new CustomEvent('basket:change', { detail: unique }));
  return unique;
}

export function writeBasket(ids) {
  return persist(Array.isArray(ids) ? ids : []);
}

export function addToBasket(id) {
  if (!id) return readBasket();
  return persist([...readBasket(), id]);
}

export function removeFromBasket(id) {
  return persist(readBasket().filter((item) => item !== id));
}

export function toggleBasket(id) {
  const list = readBasket();
  return list.includes(id) ? removeFromBasket(id) : addToBasket(id);
}

export function inBasket(id) {
  return readBasket().includes(id);
}

export function basketCount() {
  return readBasket().length;
}

export function updateBasketBadge(ids = readBasket()) {
  const node = document.getElementById('basket-count');
  if (node) node.textContent = String(ids.length);
  const btn = document.getElementById('basket-btn');
  if (btn) btn.classList.toggle('has-items', ids.length > 0);
}
