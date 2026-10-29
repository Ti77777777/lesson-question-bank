/**
 * N08 继续上次工作：最近打开的题单。
 *
 * 只保存在当前浏览器，并按运行库身份隔离——换了数据库不会把另一套题单推给你。
 * 只存题单编号、标题和打开方式，不复制题目内容；恢复前向服务端核对对象是否还在。
 */
import { api } from './api.js';

const KEY = 'qb.recent.v1';
const KEEP = 5;
const SHOW = 3;

let identity = null;

export async function dbIdentity() {
  if (identity) return identity;
  try {
    const meta = await api('/api/meta');
    identity = meta.db_path || 'unknown';
  } catch {
    identity = 'unknown';
  }
  return identity;
}

function readAll() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}') || {};
  } catch {
    return {};
  }
}

function writeAll(all) {
  try {
    localStorage.setItem(KEY, JSON.stringify(all));
    return true;
  } catch {
    return false;   // 无痕模式或存储被禁用
  }
}

export async function rememberCollection(cid, title, mode = 'edit') {
  if (!cid) return;
  const db = await dbIdentity();
  const all = readAll();
  const list = Array.isArray(all[db]) ? all[db] : [];
  const next = [{ id: cid, title: title || cid, mode, at: new Date().toISOString() }]
    .concat(list.filter((row) => row.id !== cid))
    .slice(0, KEEP);
  all[db] = next;
  writeAll(all);
}

export async function recentCollections(limit = SHOW) {
  const db = await dbIdentity();
  const all = readAll();
  return (Array.isArray(all[db]) ? all[db] : []).slice(0, limit);
}

/** 逐条核对题单还在不在；不存在的顺手清掉，避免点进去是错误页。 */
export async function validatedRecent(limit = SHOW) {
  const rows = await recentCollections(limit);
  const alive = [];
  const dropped = [];
  for (const row of rows) {
    try {
      const detail = await api(`/api/collections/${encodeURIComponent(row.id)}`);
      alive.push({
        id: row.id,
        mode: row.mode,
        title: (detail.collection || {}).title || row.title || row.id,
        count: (detail.items || []).length,
      });
    } catch {
      dropped.push(row.id);
    }
  }
  if (dropped.length) await forgetRecent(dropped);
  return alive;
}

export async function forgetRecent(ids) {
  const db = await dbIdentity();
  const all = readAll();
  const drop = new Set(Array.isArray(ids) ? ids : [ids]);
  all[db] = (Array.isArray(all[db]) ? all[db] : []).filter((row) => !drop.has(row.id));
  writeAll(all);
}

export async function clearRecent() {
  const db = await dbIdentity();
  const all = readAll();
  delete all[db];
  writeAll(all);
}
