/**
 * N01 连续选题：把选题篮里的题目加入已有题单。
 *
 * 交互约定（见《新功能设计与实施规划》N01）：
 * - 目标题单与分节在这里选，检索页的筛选条件不受影响；
 * - 加入是「逐项预校验、合法项原子写入」，失败项逐条说明原因；
 * - 每次提交带操作编号与题单修订，双击或网络重试不会重复加入。
 */
import { api } from './api.js';
import { flash } from './ui.js';
import { openModal } from './ui.js';
import { esc } from './dom.js';

const newOpId = () => (crypto.randomUUID ? crypto.randomUUID()
  : 'op-' + Date.now() + '-' + Math.random().toString(16).slice(2));

async function loadCollections() {
  const data = await api('/api/collections?limit=100');
  return (data.items || []).filter((c) => c.status !== 'archived');
}

export function openAddToCollection(ids, { onDone } = {}) {
  const questions = [...ids];
  if (!questions.length) {
    flash('选题篮是空的，先从检索结果里加入题目');
    return;
  }

  openModal({
    title: `把 ${questions.length} 道题加入题单`,
    width: 'modal-wide',
    body: `<form class="form" id="add-collection-form">
      <p class="muted" id="add-collection-hint">正在读取题单…</p>
      <div class="field-row">
        <label class="field"><span>目标题单</span>
          <select name="collection_id" id="add-collection-target" disabled></select>
        </label>
        <label class="field"><span>加入分节</span>
          <select name="section_id" id="add-collection-section" disabled>
            <option value="">不分节（追加到末尾）</option>
          </select>
        </label>
      </div>
      <div class="notice notice-info" id="add-collection-result" hidden></div>
    </form>`,
    footer: `<button type="button" class="btn" data-close>取消</button>
             <button type="button" class="btn btn-primary" id="add-collection-submit" disabled>加入题单</button>`,
    onMount({ body, foot, close }) {
      const form = body.querySelector('#add-collection-form');
      const target = body.querySelector('#add-collection-target');
      const section = body.querySelector('#add-collection-section');
      const hint = body.querySelector('#add-collection-hint');
      const result = body.querySelector('#add-collection-result');
      const submit = foot.querySelector('#add-collection-submit');
      let revision = '';

      (async () => {
        try {
          const list = await loadCollections();
          if (!list.length) {
            hint.textContent = '还没有题单。可以先用选题篮新建一份，再回来加入。';
            return;
          }
          target.innerHTML = list.map((c) =>
            `<option value="${esc(c.id)}">${esc(c.title || c.id)}（${c.item_count ?? '—'} 题）</option>`).join('');
          target.disabled = false;
          submit.disabled = false;
          await refreshSections(target.value);
        } catch (err) {
          hint.textContent = '读取题单失败：' + (err.message || err);
        }
      })();

      async function refreshSections(cid) {
        section.innerHTML = '<option value="">不分节（追加到末尾）</option>';
        if (!cid) return;
        try {
          const detail = await api(`/api/collections/${encodeURIComponent(cid)}`);
          revision = detail.revision || '';
          const sections = detail.sections || [];
          section.innerHTML = '<option value="">不分节（追加到末尾）</option>'
            + sections.map((s) => `<option value="${esc(s.id)}">${esc(s.title || s.id)}</option>`).join('');
          section.disabled = false;
          const existing = new Set((detail.items || []).map((i) => i.question_id));
          const overlap = questions.filter((q) => existing.has(q)).length;
          hint.textContent = `${sections.length} 个分节`
            + (overlap ? `；其中 ${overlap} 道已在这份题单里，将按重复处理` : '');
        } catch (err) {
          hint.textContent = '读取分节失败：' + (err.message || err);
        }
      }

      target.addEventListener('change', () => refreshSections(target.value));

      submit.addEventListener('click', async () => {
        submit.disabled = true;
        submit.textContent = '加入中…';
        result.hidden = true;
        try {
          const payload = {
            operation_id: newOpId(),
            base_revision: revision,
            section_id: section.value || null,
            items: questions.map((qid) => ({ question_id: qid })),
          };
          const out = await api(`/api/collections/${encodeURIComponent(target.value)}/items/batch`,
            { method: 'POST', body: payload });
          const failed = (out.results || []).filter((r) => !r.ok);
          result.hidden = false;
          result.className = 'notice ' + (failed.length ? 'notice-warn' : 'notice-ok');
          result.innerHTML = `<b>已加入 ${out.added} 道</b>`
            + (failed.length
              ? `<ul class="plain-list">${failed.map((r) => `<li>${esc(r.question_id || '（未指名）')}：${esc(r.message || r.code || '未加入')}</li>`).join('')}</ul>`
              : '')
            + `<p><a href="#/collection/${esc(target.value)}">打开题单继续调整</a></p>`;
          if (out.revision) revision = out.revision;   // 下一次点击要用新的基准
          if (out.added && onDone) onDone(out);
          submit.textContent = '再加入一次';
        } catch (err) {
          result.hidden = false;
          result.className = 'notice notice-warn';
          result.textContent = err.code === 'revision_conflict'
            ? '题单在操作期间被改过，请关闭后重新打开本窗口再试。'
            : (err.message || String(err));
          submit.textContent = '重试';
        } finally {
          submit.disabled = false;
        }
      });
    },
  });
}
