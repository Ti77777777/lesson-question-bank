"""候选采纳与自动入库。

教师手动采纳（/api/ai/candidates/{cid}/accept、/api/ai/jobs/{jid}/accept）与
AI 自复核后的自动入库共用同一段事务逻辑：先验证据指纹与基准版本，再建题或建版本，
最后写 ai_decision。自动入库额外写 review（reviewer 标明 AI 自复核）并设置
teaching.ai_math_review，避免与人工审校记录混淆。
"""
from __future__ import annotations

import hashlib
import json

from app import db,provenance
from app.ai import contracts, provider
from app.server import ApiError

PARTS = ('question', 'solution', 'tags', 'rating')


def unpack(row, field):
    value = row.get(field)
    return json.loads(value) if value else {}


def require(table, identity):
    row = db.one('SELECT * FROM ' + table + ' WHERE id=?', (identity,))
    if not row:
        raise ApiError(404, 'not_found', '记录不存在')
    return row


def accept_parts(value):
    parts = value if value is not None else list(PARTS)
    if not isinstance(parts, list) or not parts or any(p not in PARTS for p in parts):
        raise ApiError(400, 'parts', '采纳范围无效')
    return parts


def asset(conn, rel):
    existing = conn.execute('SELECT id FROM asset WHERE rel_path=?', (rel,)).fetchone()
    if existing:
        return existing[0]
    p = (db.ROOT / rel).resolve()
    if not p.is_relative_to((db.ROOT / 'assets').resolve()) or not p.is_file():
        raise ApiError(400, 'asset', '题图不存在')
    aid = db.next_id(conn, 'AST')
    conn.execute('INSERT INTO asset(id,kind,filename,rel_path,sha256,note,created_at) VALUES(?,?,?,?,?,?,?)',
                 (aid, 'screenshot', p.name, rel, hashlib.sha256(p.read_bytes()).hexdigest(),
                  'AI定位原卷图形，待人工核查', db.now()))
    return aid


def write_auto_review(conn, version_id, candidate_id, note, model):
    """AI 自复核通过后的入库记录；reviewer 明确标注来源，不冒充教师。"""
    reviewer = 'AI 自复核（%s / %s）' % (model, provider.PROMPT_VERSION)
    items = [('question_version', version_id, 'stem'), ('question_version', version_id, 'answer')]
    for row in conn.execute('SELECT id FROM solution WHERE question_version_id=?', (version_id,)):
        items.append(('solution', row[0], 'solution'))
    refs = json.loads(conn.execute('SELECT material_refs FROM question_version WHERE id=?',
                                   (version_id,)).fetchone()[0] or '[]')
    items += [('asset', ref, 'figure') for ref in refs]
    for target_type, target_id, item in items:
        conn.execute('INSERT INTO review(id,target_type,target_id,item,result,reviewer,note,created_at) VALUES(?,?,?,?,?,?,?,?)',
                     (db.next_id(conn, 'REV'), target_type, target_id, item, 'pass', reviewer, note, db.now()))


def accept_candidate(candidate, parts, auto=False, actor=None):
    """Adopt one candidate in its own transaction. auto=True 表示 AI 自复核后直接入库。"""
    from app.api.content import _create_question, _insert_solutions, _insert_question_tags
    actor = actor or ('AI 自复核' if auto else '教师')
    with db.transaction() as conn:
        old = conn.execute('SELECT * FROM ai_decision WHERE adoption_key=?', (candidate['id'],)).fetchone()
        if old:
            return {'version_id': old['target_version_id'], 'idempotent': True}
        row = dict(conn.execute('SELECT * FROM ai_question WHERE id=?', (candidate['ai_question_id'],)).fetchone())
        if row['current_revision'] != candidate['revision'] or row['state'] != 'ready':
            raise ApiError(409, 'candidate_changed', '当前候选结果不可采纳')
        raw_content = unpack(candidate, 'content_json')
        # Old candidates may predate the current formatting normalizer. Keep
        # their reviewed content/hash intact, and normalize the adopted copy.
        c = provider.normalise(raw_content)
        format_normalized = c != raw_content
        source = c['source']; review = c['review']; solved = c['solve']
        model = str(c.get('provider') or provider.settings.for_job(row['job_id'])['model'])
        for rel, sha in c.get('evidence_hashes', {}).items():
            p = (db.ROOT / rel).resolve()
            if not p.is_relative_to((db.ROOT / 'assets').resolve()) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                raise ApiError(409, 'evidence_changed', '原始题图或题面证据已变化，请重新生成候选结果')
        if solved['status'] != 'solved':
            raise ApiError(409, 'unresolved', '尚未得到完整解答，请先补充或修订')
        try:
            contracts.solved(solved, c['question']); contracts.reviewed(review)
        except provider.ProviderError as exc:
            raise ApiError(400, getattr(exc, 'code', 'invalid_input'), str(exc)) from None
        base = conn.execute('SELECT * FROM question_version WHERE id=?', (row['base_version_id'],)).fetchone() if row['base_version_id'] else None
        if base:
            qid = base['question_id']; q = conn.execute('SELECT * FROM question WHERE id=?', (qid,)).fetchone()
            if q['current_version_id'] != base['id'] or q['merged_into']:
                raise ApiError(409, 'version_conflict', '原题已更新或合并，请重新发起AI任务')
        elif 'question' not in parts or 'solution' not in parts:
            raise ApiError(400, 'new_question_parts', '新题入库必须包含题面与解答')
        fields = dict(c['question']) if 'question' in parts or not base else db.row_to_dict(base)
        if base and 'solution' not in parts:
            b = db.row_to_dict(base)
            fields['answer'] = b.get('answer'); fields['answer_keys'] = b.get('answer_keys')
            old_answers={s['key']:s.get('answer','') for s in b.get('subquestions') or []}
            fields['subquestions']=[{**s,'answer':old_answers.get(s['key'],'')} for s in fields.get('subquestions') or []]
            keys=fields.get('answer_keys') or []
            valid=[o['key'] for o in fields.get('options') or []] if fields['qtype'] in ('single','multi') else []
            if any(key not in valid for key in keys):
                raise ApiError(400,'incompatible_parts','保留的答案与新题面不匹配，请同时采纳修订后的解答')
        else:
            fields['answer'] = solved['answer']; fields['answer_keys'] = solved.get('answer_keys', [])
            subanswers = {s['key']: s['answer'] for s in solved.get('subanswers', [])}
            fields['subquestions'] = [{**s, 'answer': subanswers.get(s['key'], '')} for s in fields.get('subquestions') or []]
            try:contracts.solved(solved,fields)
            except provider.ProviderError as exc:
                raise ApiError(400,'incompatible_parts','解答与所选题面不匹配：'+str(exc)) from None
        refs = list(source.get('material_refs', []))
        for rel in source.get('figure_paths', []):
            aid = asset(conn, rel)
            if aid not in refs:
                refs.append(aid)
        fields['material_refs'] = refs
        base_teaching = db.row_to_dict(base).get('teaching') or {} if base else {}
        fields['teaching'] = {**base_teaching, **review.get('teaching', {}), 'ai_candidate_id': candidate['id'],
                              'ai_math_review': 'ai_self_review' if auto else 'pending',
                              'subquestion_dependency_review': 'pending'}
        if source.get('figure_paths'):
            fields['teaching']['asset_placement'] = 'inline'
        fields['analysis'] = base['analysis'] if base and 'solution' not in parts else None
        fields['created_by'] = 'AI自复核自动入库' if auto else actor+'采纳AI候选'
        fields['change_note'] = ('AI 自复核未发现问题，自动入库 ' if auto else '采纳AI候选 ') + candidate['id'] + '；数学审校仍待教师确认'
        if not base:
            f = require('source_file', require('ai_job', row['job_id'])['source_file_id'])
            paper=require('source',f['source_id'])
            fields.update(short_name=provenance.label(paper['name'],row['original_qno']), solutions=solved['solutions'],
                          occurrence={'source_id': f['source_id'], 'source_file_id': f['id'],
                                      'pdf_page': (source.get('pages') or [1])[0], 'qno': row['original_qno'],
                                      'raw_text': source['stem'], 'ocr_tool': 'AI ' + model,
                                      'ocr_at': db.now(), 'pending': 1, 'note': 'AI提取，待教师审校'})
            qid = _create_question(fields, conn)
            vid = conn.execute('SELECT current_version_id FROM question WHERE id=?', (qid,)).fetchone()[0]
        else:
            vid = db.next_id(conn, 'QV')
            n = conn.execute('SELECT max(version_no)+1 FROM question_version WHERE question_id=?', (qid,)).fetchone()[0]
            conn.execute('INSERT INTO question_version(id,question_id,version_no,qtype,stem,options,answer,answer_keys,subquestions,analysis,material_refs,teaching,total_points,change_note,review_state,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (vid, qid, n, fields['qtype'], fields['stem'], db.dumps(fields.get('options')), fields.get('answer'),
                          db.dumps(fields.get('answer_keys')), db.dumps(fields.get('subquestions')), fields.get('analysis'),
                          db.dumps(refs), db.dumps(fields['teaching']), fields.get('total_points'), fields['change_note'],
                          'draft', db.now(), fields['created_by']))
            solutions = solved['solutions'] if 'solution' in parts else db.all_('SELECT * FROM solution WHERE question_version_id=?', (base['id'],))
            solution_ids=_insert_solutions(conn, vid, solutions, db.now())
            if 'solution' not in parts:
                # The old solution rows do not contain their relationship-table
                # tags. Copy those links as well when retaining the solution.
                for previous,new_id in zip(solutions,solution_ids):
                    conn.execute('INSERT INTO solution_tag(solution_id,tag_id,created_at) SELECT ?,tag_id,? FROM solution_tag WHERE solution_id=?',
                                 (new_id,db.now(),previous['id']))
            conn.execute("UPDATE question_version SET review_state='submitted' WHERE id=?", (base['id'],))
            conn.execute('UPDATE question SET current_version_id=?,updated_at=? WHERE id=?', (vid, db.now(), qid))
        if 'rating' in parts:
            d = review['difficulty']
            conn.execute('UPDATE question SET difficulty_calc=?,difficulty_think=?,difficulty_scope=?,difficulty_level=?,difficulty_note=?,rec_level=?,rec_reason=?,time_solve_min=? WHERE id=?',
                         (d['calc'], d['think'], d['scope'], d.get('level'), d.get('note', '') + ' [AI预测，待教师核对]',
                          review['rec_level'], review.get('rec_reason'), review['time_solve_min'], qid))
        if 'tags' in parts:
            tags = []
            for tag in review.get('tags', []):
                oldtag = conn.execute('SELECT id,status FROM tag WHERE dimension=? AND name=?', (tag['dimension'], tag['name'])).fetchone()
                if oldtag and oldtag['status'] != 'active':
                    raise ApiError(409, 'inactive_tag', '建议标签已停用，请先调整候选标签')
                if oldtag:
                    tid = oldtag['id']
                else:
                    tid = db.next_id(conn, 'TAG')
                    conn.execute('INSERT INTO tag(id,dimension,name,definition,note,created_at) VALUES(?,?,?,?,?,?)',
                                 (tid, tag['dimension'], tag['name'], tag.get('reason'),
                                  '教师采纳AI建议创建' if not auto else 'AI自复核入库时创建，待教师合并', db.now()))
                tags.append({'tag_id': tid, 'source': 'ai', 'role': 'primary' if tag['dimension'] == 'knowledge' else 'secondary'})
            _insert_question_tags(conn, qid, tags, db.now())
        if auto:
            write_auto_review(conn, vid, candidate['id'], 'AI 自复核未发现问题；候选 ' + candidate['id'], model)
        conn.execute('INSERT INTO ai_decision VALUES(?,?,?,?,?,?,?,?,?)',
                     (db.next_id(conn, 'AID'), candidate['id'], candidate['content_hash'], 'accept',
                      db.dumps({'parts': parts, 'format_normalized': format_normalized}), actor, vid, candidate['id'], db.now()))
        conn.execute("UPDATE ai_question SET state='adopted',adopted_version_id=?,error_json=NULL WHERE id=?", (vid, row['id']))
        db.audit('ai_accept', 'question', qid, {'candidate_id': candidate['id'], 'version_id': vid, 'parts': parts, 'auto': auto, 'format_normalized': format_normalized}, actor=actor)
    return {'question_id': qid, 'version_id': vid, 'idempotent': False}


def auto_adopt(job):
    """把自复核通过（no_issue_found）的候选直接写进题库；其余留在工作台。"""
    batch = db.one('SELECT config_json FROM ai_batch WHERE id=?', (job['batch_id'],))
    config = unpack(batch or {}, 'config_json')
    if not config.get('auto_accept', True):
        return {'adopted': 0, 'disabled': True}
    rows = db.all_("SELECT * FROM ai_question WHERE job_id=? AND state='ready' AND quality_state='no_issue_found' ORDER BY CAST(original_qno AS INTEGER),id", (job['id'],))
    adopted = 0
    for row in rows:
        try:
            candidate = db.one('SELECT * FROM ai_candidate WHERE ai_question_id=? AND revision=?', (row['id'], row['current_revision']))
            if not candidate:raise ApiError(409,'missing_candidate','候选结果缺失，请核对后重试')
            accept_candidate(candidate, list(PARTS), auto=True)
            adopted += 1
        except Exception as exc:
            known=isinstance(exc,(ApiError,provider.ProviderError))
            failure={'code':getattr(exc,'code','auto_adopt_failed'), 'error_type':type(exc).__name__,
                     'message':str(exc) if known else '自动入库失败，请核对后手动采纳（'+type(exc).__name__+'）'}
            with db.transaction() as conn:
                conn.execute("UPDATE ai_question SET error_json=? WHERE id=? AND state='ready'",(db.dumps(failure),row['id']))
                db.audit('ai_auto_adopt_failed', 'ai_question', row['id'], failure)
    if adopted:
        with db.transaction() as conn:
            db.audit('ai_auto_adopt', 'ai_job', job['id'], {'adopted': adopted})
    return {'adopted': adopted}
