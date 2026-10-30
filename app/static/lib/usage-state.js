// A failed request may already have reached the server. Retrying unchanged data
// must use the original operation ID, including after editing and undoing edits.
export function usageOperations(makeId = () => crypto.randomUUID
  ? crypto.randomUUID() : 'usage-' + Date.now() + '-' + Math.random().toString(16).slice(2)) {
  const ids = new Map();
  return {
    forPayload(payload) {
      const key = JSON.stringify(payload);
      if (!ids.has(key)) ids.set(key, makeId());
      return ids.get(key);
    },
    clear() { ids.clear(); },
  };
}

// Invalidate both in-flight reads and already rendered results when selection
// changes. Callers also check their page lifecycle before rendering.
export function latestSelection() {
  let generation = 0;
  return {
    start() { const ticket = ++generation; return () => ticket === generation; },
    invalidate() { generation++; },
  };
}

export function snapshotUsageItems(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.items)) throw new Error('这份讲义缺少可读取的历史题目，请重新导出讲义。');
  return snapshot.items.map(item => {
    const entry = snapshot.question_versions?.[item.question_version_id]
      || snapshot.questions?.[item.question_id] || {};
    if (item.question_version_id && entry.version?.id && entry.version.id !== item.question_version_id) {
      throw new Error('这份讲义的历史题目版本不匹配，请核对后重新导出。');
    }
    return {...item, label: entry.question?.short_name || item.question_id};
  });
}
