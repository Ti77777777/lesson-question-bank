/* Drafts stay in this browser and are isolated by database and record. No credentials. */
export function draftStore(scope, record, storage) {
  const key = `qbank.draft.v1:${scope}:${record}`;
  let owned = '';
  return {
    read() {
      try {
        const target=storage || globalThis.localStorage, d = JSON.parse(target.getItem(key));
        if (d?.v !== 1 || !d.data || typeof d.data !== 'object' || !Number.isFinite(d.time)) return null;
        if (Date.now() - d.time > 30 * 86400000) { target.removeItem(key); return null; }
        owned = d.token; return d;
      } catch { return null; }
    },
    write(data, base = '') {
      try {
        owned = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
        (storage || globalThis.localStorage).setItem(key, JSON.stringify({v: 1, data, base, time: Date.now(), token: owned}));
        return true;
      } catch { return false; }
    },
    clear() {
      try { const target=storage || globalThis.localStorage; if (JSON.parse(target.getItem(key))?.token === owned) target.removeItem(key); } catch { /* storage disabled */ }
    },
  };
}

/** Serialize saves; a slow response must never mark newer edits as saved. */
export function autosaver({read, save, store, base = '', onState = () => {}, delay = 800}) {
  let dirty = false, timer, flight = null, disposed = false, sequence = 0;
  function keep() { return store.write(read(), base); }
  async function flush() {
    clearTimeout(timer);
    if (flight) { await flight; if (dirty && !disposed) return flush(); return; }
    if (!dirty || disposed) return;
    const tick = sequence, value = structuredClone(read());
    onState('saving');
    flight = (async () => {
      try {
        const result = await save(value, base);
        base = result.revision;
        dirty = tick !== sequence;
        if (dirty) { if (!disposed) keep(); } else store.clear();
        if (!disposed) onState(dirty ? 'pending' : 'saved');
      } catch (error) {
        if (!disposed) onState('error', error);
        throw error;
      } finally { flight = null; }
    })();
    await flight;
    if (dirty && !disposed) return flush();
  }
  return {
    change() {
      sequence++; dirty = true;
      const kept = keep(); onState(kept ? 'pending' : 'unprotected');
      clearTimeout(timer); timer = setTimeout(() => flush().catch(() => {}), delay);
    },
    flush,
    dispose() { disposed = true; clearTimeout(timer); },
    get dirty() { return dirty; },
  };
}
