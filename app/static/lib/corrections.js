/* 检索与题目详情共用纠错面板。 */
import { api } from './api.js';
import { esc, badge, fmtDateTime } from './dom.js';
import { openModal, runSubmit, flash } from './ui.js';

const CATEGORIES = {stem:'题干与选项', answer:'答案', solution:'解析与解答', figure:'图片与公式', tags:'知识点与方法标签', source:'试卷来源与年级', difficulty:'难度与评分', other:'其他问题'};
const STATUS = {pending:'待处理', resolved:'已修正', dismissed:'无需修改'};

export function openCorrections({questionId, versionId, onChanged, onEdit} = {}) {
  let data, closed = false, handlingModal;
  const modal = openModal({
    title: `题目纠错 · ${questionId}`, width: 'modal-wide',
    body: `<div class="correction-intro"><div><h3>发现问题，随手记录</h3><p class="hint">可先登记后核对，也可直接打开题目修改。修改内容会保留旧版本；登记纠错不会自动改动题目。</p></div><button type="button" class="btn" data-edit>打开题目修改</button></div>
      <form class="correction-form"><div class="modal-error" hidden></div>
        <div class="inline-fields two">
          <label class="field"><span>问题类型</span><select name="category">${Object.entries(CATEGORIES).map(([id,name])=>`<option value="${id}">${name}</option>`).join('')}</select></label>
          <label class="field"><span>发现问题的版本</span><select name="version_id" disabled aria-label="发现问题的版本"></select></label>
        </div>
        <label class="field"><span>问题说明 <span class="req">*</span></span><textarea name="description" rows="3" maxlength="5000" required placeholder="例如：答案遗漏端点；题干条件与原卷不一致。"></textarea></label>
        <label class="field"><span>修改建议（选填）</span><textarea name="suggestion" rows="2" maxlength="5000" placeholder="可填写正确内容、TeX 代码，或需要核对的位置。"></textarea></label>
        <div class="correction-submit"><span class="hint">提交后可在下方跟进处理。</span><button class="btn btn-primary" type="submit" disabled>提交纠错</button></div>
      </form>
      <section class="correction-history" aria-label="纠错记录"><h3>纠错记录 <span data-count class="hint"></span></h3><div data-history aria-live="polite">正在读取记录…</div></section>`,
    footer: '<button type="button" class="btn" data-close>关闭</button>',
    onClose: () => { closed = true; handlingModal?.close(); },
  });
  const form = modal.body.querySelector('form');
  const history = modal.body.querySelector('[data-history]');
  const versionSelect = form.elements.version_id;
  const submit = form.querySelector('[type="submit"]');
  const edit = modal.body.querySelector('[data-edit]');
  edit.addEventListener('click', () => {
    modal.close();
    if (onEdit) onEdit();
    else location.hash = `#/question/${encodeURIComponent(questionId)}?edit=1`;
  });

  async function refresh(notify = false) {
    try {
      data = await api(`/api/questions/${encodeURIComponent(questionId)}/corrections`);
      if (closed || !modal.mask.isConnected) return;
      const selected = versionSelect.value || versionId || data.question.current_version_id;
      versionSelect.innerHTML = data.versions.map(v=>`<option value="${esc(v.id)}">v${esc(v.version_no)}${v.id===data.question.current_version_id?' · 当前版本':''}</option>`).join('');
      versionSelect.value = selected;
      if (!versionSelect.value) versionSelect.value = data.question.current_version_id;
      versionSelect.disabled = false; submit.disabled = false;
      if (notify) onChanged?.({questionId, pendingCount:data.pending_count, totalCount:data.items.length});
      modal.body.querySelector('[data-count]').textContent = `${data.items.length} 条 · ${data.pending_count} 条待处理`;
      history.innerHTML = data.items.length ? data.items.map(item => `
        <article class="correction-record">
          <div class="correction-record-head">${badge(STATUS[item.status], item.status==='pending'?'pending':item.status==='resolved'?'pass':'na')}<strong>${esc(CATEGORIES[item.category])}</strong><span class="hint">v${esc(item.version_no)} · ${esc(fmtDateTime(item.created_at))}</span></div>
          <p class="correction-text">${esc(item.description)}</p>
          ${item.suggestion?`<div class="correction-suggestion"><span class="hint">修改建议</span><p class="correction-text">${esc(item.suggestion)}</p></div>`:''}
          ${item.resolution_note?`<p class="correction-text"><b>处理说明：</b>${esc(item.resolution_note)}${item.fixed_version_no?`（关联 v${esc(item.fixed_version_no)}）`:''}</p>`:''}
          <div class="correction-record-actions">${item.status==='pending'?`<button class="btn btn-sm" type="button" data-handle="${esc(item.id)}" data-status="resolved">标记已修正</button><button class="btn btn-sm" type="button" data-handle="${esc(item.id)}" data-status="dismissed">无需修改</button>`:`<button class="btn btn-sm" type="button" data-handle="${esc(item.id)}" data-status="pending">重新打开</button>`}
          <details><summary>处理过程（${item.events.length}）</summary><ol class="correction-events">${item.events.map(e=>`<li><span class="hint">${esc(fmtDateTime(e.created_at))} · ${esc(STATUS[e.status])}${e.fixed_version_no?` · v${esc(e.fixed_version_no)}`:''}</span><p class="correction-text">${esc(e.note)}</p></li>`).join('')}</ol></details></div>
        </article>`).join('') : '<div class="correction-empty">这道题还没有纠错记录。</div>';
    } catch (err) {
      if (closed || !modal.mask.isConnected) return;
      history.innerHTML = `<p class="hint">读取纠错记录失败：${esc(err.message)}</p><button type="button" class="btn btn-sm" data-reload>重新读取</button>`;
    }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!data || submit.disabled) return;
    await runSubmit(submit, form.querySelector('.modal-error'), async () => {
      const payload = Object.fromEntries(new FormData(form));
      if (!payload.description.trim()) throw new Error('请填写问题说明。');
      const result = await api(`/api/questions/${encodeURIComponent(questionId)}/corrections`, {method:'POST', body:payload});
      form.elements.description.value = ''; form.elements.suggestion.value = '';
      flash(result.duplicate?'已有相同的待处理记录。':'纠错已登记。', 'ok');
      await refresh(true);
    });
  });
  history.addEventListener('click', event => {
    if (event.target.closest('[data-reload]')) { refresh(true); return; }
    const button = event.target.closest('[data-handle]');
    if (!button) return;
    const item = data.items.find(item=>item.id===button.dataset.handle);
    const status = button.dataset.status;
    const handleModal = openModal({title: `${STATUS[status]} · ${CATEGORIES[item.category]}`,
      body: `<div class="modal-error" hidden></div><p class="correction-text">${esc(item.description)}</p><p class="hint">请先完成核对或修改，再记录处理结果。</p>
        <label class="field"><span>处理说明 <span class="req">*</span></span><textarea name="note" rows="3" maxlength="5000" placeholder="填写修改位置或判断依据。"></textarea></label>
        ${status==='resolved'?`<label class="field"><span>关联修订版本（选填）</span><select name="fixed_version_id"><option value="">不关联（如调整来源、标签或难度）</option>${data.versions.map(v=>`<option value="${esc(v.id)}">v${esc(v.version_no)}</option>`).join('')}</select></label>`:''}`,
      footer:'<button type="button" class="btn" data-close>取消</button><button type="button" class="btn btn-primary" data-save>保存处理结果</button>',
    });
    handlingModal = handleModal;
    handleModal.foot.querySelector('[data-save]').addEventListener('click', async event => {
      await runSubmit(event.currentTarget, handleModal.body.querySelector('.modal-error'), async () => {
        const note = handleModal.body.querySelector('[name="note"]').value.trim();
        if (!note) throw new Error('请填写处理说明。');
        await api(`/api/corrections/${encodeURIComponent(item.id)}`, {method:'PATCH', body:{status, note, expected_status:item.status, fixed_version_id:handleModal.body.querySelector('[name="fixed_version_id"]')?.value || null}});
        handleModal.close(); flash('处理结果已保存。', 'ok'); await refresh(true);
      });
    });
  });
  refresh();
  return modal;
}
