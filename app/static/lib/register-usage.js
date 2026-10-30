/**
 * N05 从讲义快捷登记使用。
 *
 * 入口带着发布物进来，不再让教师重新挑一次讲义；题目默认全选，可只记实际讲过的几道。
 * 导出本身不算使用，下载也不算发放——这里必须由教师确认日期与对象后才写入。
 */
import { api } from './api.js';
import { openModal, flash } from './ui.js';
import { esc } from './dom.js';
import { usageOperations, snapshotUsageItems } from './usage-state.js';
import { identityLabel } from './identity.js';

const today = () => {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

export function openRegisterUsage(releaseId, { onDone } = {}) {
  openModal({
    title: '登记这次使用',
    width: 'modal-wide',
    body: `<form class="form" id="register-usage">
      <div class="modal-error" hidden data-error></div>
      <p class="hint" data-summary>正在读取这份讲义…</p>
      <div class="field-row">
        <label class="field"><span>给谁用<span class="req">*</span></span>
          <select name="audience_id" data-audience required><option value="">选择班级、小组或学生</option></select>
        </label>
        <label class="field"><span>实际使用日期<span class="req">*</span></span>
          <input type="date" name="used_on" data-date value="${today()}" required>
        </label>
        <label class="field"><span>用途</span>
          <select name="purpose" data-purpose>
            <option value="class">课堂讲解</option>
            <option value="homework">课后作业</option>
            <option value="quiz">当堂小测</option>
            <option value="review">复习回顾</option>
          </select>
        </label>
      </div>
      <div class="field"><span>这次实际用了哪些题</span>
        <div class="usage-items" data-items></div>
        <p class="hint">只勾选真正讲过或布置过的题目；全部取消勾选会被视为没有条目，不能提交。</p>
      </div>
      <label class="field"><span>备注</span><input type="text" data-note placeholder="如：只讲了例 1、例 2，其余留作课后"></label>
    </form>`,
    footer: `<button type="button" class="btn" data-close>取消</button>
             <button type="button" class="btn btn-primary" data-submit disabled>登记使用</button>`,
    onMount({ body, foot, close }) {
      const form = body.querySelector('#register-usage');
      const err = body.querySelector('[data-error]');
      const summary = body.querySelector('[data-summary]');
      const audienceSel = body.querySelector('[data-audience]');
      const itemsBox = body.querySelector('[data-items]');
      const submit = foot.querySelector('[data-submit]');
      let release = null;
      const operations = usageOperations();
      form.addEventListener('submit', event => {
        event.preventDefault();
        if (!submit.disabled) submit.click();
      });

      (async () => {
        try {
          const [detail, audiences] = await Promise.all([
            api(`/api/releases/${encodeURIComponent(releaseId)}`),
            api('/api/audiences?limit=100'),
          ]);
          release = detail.release || {};
          const snapshot = release.snapshot || {};
          const items = snapshotUsageItems(snapshot);
          const title = (snapshot.collection || {}).title || release.collection_title || '';
          summary.textContent = `讲义：${title}　共 ${items.length} 题　导出 ${(release.created_at || '').slice(0, 10)}`;
          itemsBox.innerHTML = items.length
            ? items.map((item, index) => `<label class="check usage-item">
                <input type="checkbox" data-item="${esc(item.id)}" checked>
                <span>${index + 1}. ${esc(item.label)}</span>
              </label>`).join('')
            : '<p class="hint">这份讲义没有条目，无法登记使用。</p>';
          audienceSel.innerHTML = '<option value="">选择班级、小组或学生</option>'
            + (audiences.items || []).map((row) =>
              `<option value="${esc(row.id)}">${esc(identityLabel(row))}</option>`).join('');

          if (release.status === 'failed') {
            err.hidden = false;
            err.textContent = '这次编译没有成功，不能登记为实际使用。';
            return;
          }
          submit.disabled = !items.length;
        } catch (e) {
          summary.textContent = '';
          err.hidden = false;
          err.textContent = '读取讲义失败：' + (e.message || e);
        }
      })();

      submit.addEventListener('click', async () => {
        err.hidden = true;
        const audienceId = audienceSel.value;
        const usedOn = body.querySelector('[data-date]').value;
        const chosen = [...itemsBox.querySelectorAll('[data-item]')]
          .filter((box) => box.checked).map((box) => box.dataset.item);
        if (!audienceId) { err.hidden = false; err.textContent = '请选择给谁用。'; return; }
        if (!usedOn) { err.hidden = false; err.textContent = '请确认实际使用日期。'; return; }
        if (!chosen.length) { err.hidden = false; err.textContent = '至少勾选一道这次真正用过的题。'; return; }

        submit.disabled = true;
        submit.textContent = '登记中…';
        try {
          const payload = {
            release_id: releaseId,
            audience_id: audienceId,
            used_on: usedOn,
            purpose: body.querySelector('[data-purpose]').value,
            items: chosen,
            note: body.querySelector('[data-note]').value.trim() || null,
          };
          const out = await api('/api/usages', { method: 'POST', body: {
            ...payload, operation_id: operations.forPayload(payload),
          }});
          close();
          flash(out.replayed ? '这次登记之前已保存过，没有重复添加。' : '已登记这次使用。', 'ok');
          if (onDone) onDone(out);
        } catch (e) {
          err.hidden = false;
          err.textContent = e.code === 'failed_release'
            ? '这次编译没有成功，不能登记为实际使用。'
            : (e.message || String(e));
          submit.disabled = false;
          submit.textContent = '重试';
        }
      });
    },
  });
}
