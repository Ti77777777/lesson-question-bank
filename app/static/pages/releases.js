/* 导出与归档：发布物列表、双版本 PDF 链接、检查结果与快照下载。 */

import { openRegisterUsage } from '../lib/register-usage.js';
import { api, fileUrl } from '../lib/api.js';
import { esc, badge, fmtDateTime, $, emptyState, errorCard, loading, num } from '../lib/dom.js';
import { RELEASE_STATUS, RELEASE_TONE } from '../lib/constants.js';
import { flash, openModal, confirmDialog } from '../lib/ui.js';
import { refreshMeta } from '../lib/bus.js';
import { mountBackupPanel } from '../lib/backup-panel.js';

function pdfLink(pathOrUrl, label, pages) {
  if (!pathOrUrl) return `<span class="file-chip missing">${esc(label)}未生成</span>`;
  const url = /^https?:|^\//.test(pathOrUrl) ? pathOrUrl : fileUrl(pathOrUrl);
  return `<a class="file-chip" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)} PDF
    ${pages ? `<span class="pg">${esc(pages)} 页</span>` : ''}</a>`;
}

export default async function mount(root, ctx) {
  let items = [];
  let total = 0;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">导出与归档</h1>
        <p class="page-sub">查看已生成讲义，下载文件或按原快照重新导出。</p>
      </div>
      <div class="page-head-actions">
        <button type="button" class="btn" id="release-refresh">刷新</button>
        <a class="btn btn-primary" href="#/collections">去题单生成</a>
      </div>
    </div>
    <section class="card">
      <div class="card-body">
        <p class="hint"><b>预览</b>　排版页的「快速预览」与「PDF校样」只检查版式，不生成讲义，也不会出现在本页。</p>
        <p class="hint"><b>导出</b>　题单页点「导出三版讲义」才生成可发放的学生版、解答版、解析版，并作为一条记录进入本页。</p>
        <p class="hint"><b>补记一次使用</b>　讲义发出后在这里补记，属于事后记录，不代表本页完成了发放。</p>
      </div>
    </section>
    <div class="results-head" style="margin-top:16px;"><span class="total" id="release-total"></span></div>
    <section class="card">
      <div id="release-list">${loading()}</div>
    </section>
    <details class="card compact-card backup-fold"><summary>备份与恢复</summary><div class="workspace-card compact-card-body" id="backup-panel"></div></details>
  `;

  const listEl = $('#release-list', root);
  ctx.onCleanup(mountBackupPanel($('#backup-panel',root)));
  const totalEl = $('#release-total', root);

  function render() {
    if (!items.length) {
      listEl.innerHTML = `<div class="card-body">${emptyState('还没有导出记录', '从题单进入「排版与导出」，生成的三版讲义会归档到这里，并保留当时使用的题目版本。')}</div>`;
      return;
    }
    listEl.innerHTML = `<div class="table-wrap"><table class="table">
      <thead><tr>
        <th>导出编号</th><th>题单</th><th>生成时间</th><th>页数（学生/解答/解析）</th><th>状态</th><th>文件</th><th>导出时的记录</th>
      </tr></thead>
      <tbody>
        ${items.map((row) => {
          const release = row.release || row;
          const id = release.id || row.id;
          const collectionTitle = release.collection_title
            || release.collection?.title
            || row.collection_title
            || row.collection?.title
            || release.collection_id
            || row.collection_id
            || '—';
          const collectionId = release.collection_id || row.collection_id || row.collection?.id;
          const student = release.student_path || release.student_url || row.student_path || row.student_url;
          const answer = release.answer_path || release.answer_url || row.answer_path || row.answer_url;
          const teacher = release.teacher_path || release.teacher_url || row.teacher_path || row.teacher_url;
          const pagesStudent = release.pages_student ?? row.pages_student;
          const pagesAnswer = release.pages_answer ?? row.pages_answer;
          const pagesTeacher = release.pages_teacher ?? row.pages_teacher;
          const status = release.status || row.status || 'built';
          return `<tr>
            <td><span class="mono">${esc(id)}</span></td>
            <td>${collectionId ? `<a href="#/collection/${esc(collectionId)}">${esc(collectionTitle)}</a>` : esc(collectionTitle)}</td>
            <td class="num">${esc(fmtDateTime(release.created_at || row.created_at))}</td>
            <td class="num">${esc(num(pagesStudent))} / ${esc(num(pagesAnswer))} / ${esc(num(pagesTeacher))}</td>
            <td>${badge(RELEASE_STATUS[status] || status, RELEASE_TONE[status] || 'plain')}</td>
            <td><div class="release-files">
              ${pdfLink(student, '学生版', pagesStudent)}
              ${pdfLink(answer, '解答版', pagesAnswer)}
              ${pdfLink(teacher, '解析版', pagesTeacher)}
            </div></td>
            <td>
              <div class="row-actions">
                <button type="button" class="btn btn-sm" data-detail="${esc(id)}" title="看这次导出的检查结果与文件">看详情</button>
                <button type="button" class="btn btn-sm" data-register="${esc(id)}" ${status === 'failed' ? 'disabled title="编译失败，不能补记使用"' : 'title="讲义已发出时，在这里补记一次使用"'}>补记一次使用</button>
                <a class="btn-link" href="/api/releases/${encodeURIComponent(id)}/snapshot" download title="下载这次导出当时使用的题目内容">这次导出用的内容</a>
              </div>
            </td>
          </tr>`;
        }).join('')}
      </tbody>
    </table></div>`;
  }

  async function load() {
    listEl.innerHTML = loading();
    try {
      const data = await api('/api/releases');
      items = Array.isArray(data.items) ? data.items : [];
      total = Number(data.total ?? items.length);
      totalEl.innerHTML = `共 <b>${esc(total)}</b> 条导出记录`;
      render();
    } catch (err) {
      if (!ctx.current()) return;
      flash(err.message);
      listEl.replaceChildren(errorCard(err.message, load));
      totalEl.textContent = '';
    }
  }

  async function openDetail(id) {
    const modal = openModal({
      title: `讲义导出记录 ${id}`,
      width: 'modal-wide',
      body: loading('正在读取导出记录…'),
      footer: `<button type="button" class="btn" data-close>关闭</button>
               <button type="button" class="btn" data-rebuild>用原快照重导</button>`,
    });
    try {
      const data = await api(`/api/releases/${encodeURIComponent(id)}`);
      const release = data.release || data;
      const files = data.files || {
        student: {
          url: release.student_url,
          path: release.student_path,
          pages: release.pages_student,
        },
        teacher: {
          url: release.teacher_url,
          path: release.teacher_path,
          pages: release.pages_teacher,
        },
        answer: {
          url: release.answer_url,
          path: release.answer_path,
          pages: release.pages_answer,
        },
      };
      let checks = data.checks || release.checks || {};
      if (typeof checks === 'string') {
        try { checks = JSON.parse(checks); } catch { checks = {}; }
      }
      const snapshot = data.snapshot || release.snapshot || {};
      const studentPath = files.student?.url || files.student?.path || release.student_path || release.student_url;
      const answerPath = files.answer?.url || files.answer?.path || release.answer_path || release.answer_url;
      const teacherPath = files.teacher?.url || files.teacher?.path || release.teacher_path || release.teacher_url;
      const status = release.status || 'built';
      const warnings = Array.isArray(checks.warnings) ? checks.warnings.join('\n') : (checks.warnings || '');
      const snapshotTitle = snapshot.collection?.title || snapshot.title || '';
      const itemCount = Array.isArray(snapshot.items) ? snapshot.items.length : (snapshot.item_count ?? '');

      modal.body.innerHTML = `
        <div class="row" style="gap:8px;flex-wrap:wrap;">
          ${badge(RELEASE_STATUS[status] || status, RELEASE_TONE[status] || 'plain')}
          <span class="hint">模板 <span class="mono">${esc(release.template || 'exbook')}</span></span>
          <span class="hint">生成于 ${esc(fmtDateTime(release.created_at))}</span>
        </div>
        <div class="release-files">
          ${pdfLink(studentPath, '学生版', files.student?.pages || release.pages_student)}
          ${pdfLink(answerPath, '解答版', files.answer?.pages || release.pages_answer)}
          ${pdfLink(teacherPath, '解析版', files.teacher?.pages || release.pages_teacher)}
        </div>
        <div class="checks-inline">
          <span class="check-pill ${Number(checks.student_errors) > 0 ? 'bad' : 'good'}">学生版错误 ${esc(checks.student_errors ?? 0)}</span>
          <span class="check-pill ${Number(checks.answer_errors) > 0 ? 'bad' : 'good'}">解答版错误 ${esc(checks.answer_errors ?? 0)}</span>
          <span class="check-pill ${Number(checks.teacher_errors) > 0 ? 'bad' : 'good'}">教师版错误 ${esc(checks.teacher_errors ?? 0)}</span>
          <span class="check-pill ${Number(checks.overfull) > 0 ? 'bad' : 'good'}">溢出 ${esc(checks.overfull ?? 0)}</span>
          <span class="check-pill ${Number(checks.missing_char) > 0 ? 'bad' : 'good'}">缺字 ${esc(checks.missing_char ?? 0)}</span>
          <span class="check-pill ${checks.leak && checks.leak !== 'clean' ? 'bad' : 'good'}">答案泄露 ${esc(checks.leak === 'clean' || !checks.leak ? '未发现' : checks.leak)}</span>
        </div>
        ${snapshotTitle || itemCount ? `<div class="hint">快照：${esc(snapshotTitle)}${itemCount ? `，${esc(itemCount)} 道题` : ''}</div>` : ''}
        ${release.note ? `<div class="hint">备注：${esc(release.note)}</div>` : ''}
        ${warnings ? `<div class="build-warn">${esc(warnings)}</div>` : ''}
        <div class="row-actions">
          <a class="btn btn-sm" href="/api/releases/${encodeURIComponent(id)}/snapshot" download>下载快照 JSON</a>
          ${release.collection_id ? `<a class="btn btn-sm" href="#/collection/${esc(release.collection_id)}">打开题单</a>` : ''}
        </div>
      `;
      modal.foot.querySelector('[data-rebuild]').addEventListener('click', async (event) => {
        const ok = await confirmDialog({
          title: '用原快照重导',
          message: '重导使用当时的题单快照与题目版本，不读取题目最新版。适合修复编译环境问题；内容修订请回题单重新生成。',
          confirmText: '重导',
        });
        if (!ok) return;
        const btn = event.currentTarget;
        btn.disabled = true;
        btn.textContent = '重导中…';
        try {
          await api(`/api/releases/${encodeURIComponent(id)}/rebuild`, { method: 'POST' });
          modal.close();
          flash(`已用原快照重导 ${id}。`, 'ok');
          await load();
          refreshMeta();
        } catch (err) {
          flash(err.message);
          btn.disabled = false;
          btn.textContent = '用原快照重导';
        }
      });
    } catch (err) {
      modal.body.innerHTML = `<div class="modal-error">${esc(err.message)}</div>`;
      flash(err.message);
    }
  }

  listEl.addEventListener('click', (event) => {
    const reg = event.target.closest('[data-register]');
    if (reg && !reg.disabled) {
      openRegisterUsage(reg.dataset.register, { onDone: () => load() });
      return;
    }

    const btn = event.target.closest('[data-detail]');
    if (btn) openDetail(btn.dataset.detail);
  });

  $('#release-refresh', root).addEventListener('click', load);

  await load();
}
