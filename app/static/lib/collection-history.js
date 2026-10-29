/**
 * N02 首期：题单撤销／重做。
 *
 * 服务端每个动作都会返回逆动作；页面把逆动作压栈，撤销就是再发一次同样的请求。
 * 栈只活在当前页面会话里（刷新即清空），刷新后的操作记录仅用于排查，不是跨会话撤销承诺。
 */
import { api } from './api.js';

const LIMIT = 20;
const newOpId = () => (crypto.randomUUID ? crypto.randomUUID()
  : 'op-' + Date.now() + '-' + Math.random().toString(16).slice(2));

export function createHistory(limit = LIMIT) {
  const undoStack = [];
  const redoStack = [];
  return {
    push(label, inverses) {
      if (!inverses || !inverses.length) return;
      undoStack.push({ label, inverses });
      while (undoStack.length > limit) undoStack.shift();
      redoStack.length = 0;
    },
    canUndo: () => undoStack.length > 0,
    canRedo: () => redoStack.length > 0,
    peekUndo: () => undoStack.at(-1) || null,
    peekRedo: () => redoStack.at(-1) || null,
    takeUndo() {
      const entry = undoStack.pop();
      return entry || null;
    },
    takeRedo() {
      const entry = redoStack.pop();
      return entry || null;
    },
    pushRedo(label, inverses) {
      redoStack.push({ label, inverses });
    },
    pushUndo(label, inverses) {
      undoStack.push({ label, inverses });
      while (undoStack.length > limit) undoStack.shift();
    },
    clear() {
      undoStack.length = 0;
      redoStack.length = 0;
    },
    size: () => undoStack.length,
  };
}

export async function sendCommand(cid, revision, action, itemId = '', payload = {}) {
  return api(`/api/collections/${encodeURIComponent(cid)}/commands`, {
    method: 'POST',
    body: {
      operation_id: newOpId(),
      base_revision: revision || '',
      action,
      item_id: itemId,
      payload,
    },
  });
}

/**
 * 依次执行一组逆动作，返回它们的逆动作（按执行顺序）。
 * 用于撤销：逆动作的逆动作，正好把这次撤销再反过来，可压回重做栈。
 */
export async function applyAll(cid, revision, inverses) {
  let current = revision;
  const produced = [];
  for (const inverse of inverses) {
    const out = await sendCommand(cid, current, inverse.action, inverse.item_id, inverse.payload);
    current = out.revision || current;
    produced.push(out.inverse);
  }
  return { revision: current, inverses: produced };
}
