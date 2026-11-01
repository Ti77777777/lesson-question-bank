import {esc} from './dom.js';
import {comparisonContent} from './swap-question.js';

export function updateEntry(row, {mode, keys=[], solution}) {
  if (!['all','selected'].includes(mode)) throw new Error('请确认新版的小问范围。');
  if (mode==='selected'&&!keys.length) throw new Error('请至少选择一个新版小问。');
  if (!solution) throw new Error('请确认新版的解法。');
  return {item_id:row.item_id,from_version_id:row.used.version_id,
    question_version_id:row.new.version_id,content_digest:row.new.content_digest,
    subquestions:{mode,keys:mode==='selected'?keys:[]},solution_id:solution==='all'?null:solution};
}

export function updateMarkup(row,index) {
  const subs=row.new.content.subquestions||[],sols=row.new.solutions||[];
  const oldSubs=(row.used.subquestions||[]).filter(s=>!row.used.subq_keys||row.used.subq_keys.includes(String(s.key)));
  const detail={question:{id:row.question_id,short_name:row.short_name},current_version:{...row.new.content,solutions:sols}};
  return `<section class="version-update-row" data-version-row="${esc(row.item_id)}">
    <label class="check"><input type="checkbox" data-update="${esc(row.item_id)}" ${index===0?'checked':''}><strong>第 ${esc(row.order_no+1)} 题　${esc(row.short_name||row.question_id)}：v${esc(row.used.version_no)} → v${esc(row.new.version_no)}</strong></label>
    ${row.new.change_note?`<p>${esc(row.new.change_note)}</p>`:''}
    ${oldSubs.length?`<details class="compact-fold"><summary>原先选用的小问</summary>${oldSubs.map(s=>`<div data-rich>${esc('（'+s.key+'）'+s.text)}</div>`).join('')}</details>`:''}
    <details class="compact-fold"><summary>查看新版题面与解析</summary>${comparisonContent(detail)}</details>
    <div class="inline-fields two">
      <label class="field"><span>新版小问范围</span><select data-sub-mode>${subs.length?'<option value="">请选择范围</option><option value="all">全部小问</option><option value="selected">指定小问</option>':'<option value="all">整题（无小问）</option>'}</select></label>
      <label class="field"><span>新版解法</span><select data-solution>${sols.length||row.used.solution_id?'<option value="">请选择解法</option>':''}<option value="all">${sols.length?'全部解法':'使用整题解析'}</option>${sols.map(sol=>`<option value="${esc(sol.id)}">${esc(sol.label||sol.id)}</option>`).join('')}</select></label>
    </div>
    <div data-subselection hidden>${subs.map(s=>`<label class="check version-sub"><input type="checkbox" data-sub-key="${esc(s.key)}"><span data-rich>${esc('（'+s.key+'）'+s.text)}</span></label>`).join('')}</div>
  </section>`;
}
