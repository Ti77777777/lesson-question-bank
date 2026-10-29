"""Draft handout preview, without creating a release or changing question content."""
import re
import threading
import hashlib
import json
import uuid
from app import db, layout
from app.server import ApiError, get, put, post, delete
from app.latex import build, exbook

_compiler = threading.BoundedSemaphore(1)


def revision(saved, items):
    return hashlib.sha256(db.dumps([saved, items]).encode()).hexdigest()


def current_revision(cid, conn=None):
    conn = conn or db.db()
    row = conn.execute('SELECT * FROM collection WHERE id=?', (cid,)).fetchone()
    if not row: raise ApiError(404, 'not_found', '题单不存在')
    # Include original item settings, so a different editor cannot be overwritten silently.
    items = [dict(r) for r in conn.execute('SELECT * FROM collection_item WHERE collection_id=? ORDER BY order_no,id', (cid,))]
    sections = [dict(r) for r in conn.execute('SELECT * FROM collection_section WHERE collection_id=? ORDER BY order_no,id', (cid,))]
    return revision(dict(row), [items, sections])


def checked_order(value, item_ids, section_ids):
    if not isinstance(value, list) or len(value) != len(item_ids):
        raise ApiError(409, 'layout_order', '题目列表已变化，请刷新后重新排序')
    seen, result = set(), []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {'id', 'section_id'}:
            raise ApiError(400, 'layout_order', '排序条目须包含题目条目编号与分节')
        iid, sid = entry['id'], entry['section_id']
        if not isinstance(iid, str) or iid not in item_ids or iid in seen:
            raise ApiError(409, 'layout_order', '排序不能包含重复或其他题单的条目')
        if sid is not None and (not isinstance(sid, str) or sid not in section_ids):
            raise ApiError(409, 'layout_order', '目标分节已变化，请刷新排版页')
        seen.add(iid); result.append({'id': iid, 'section_id': sid})
    return result


def apply_order(snap, order):
    if order is None: return
    rows = {i['id']: i for i in snap['items']}
    snap['items'] = [{**rows[e['id']], 'section_id': e['section_id'], 'order_no': n}
                     for n, e in enumerate(order)]


def snapshot(cid, draft=None):
    snap = build.make_snapshot(cid)
    if draft is not None:
        draft = dict(draft)
        order = draft.pop('order', None)
        ids = {i['id'] for i in snap['items']}
        if order is not None: order = checked_order(order, ids, {s['id'] for s in snap['sections']})
        checked = layout.validate(draft, ids)
        # Recreate from the original items so clearing an override really clears it.
        saved = snap['collection'].get('layout_options') or {}
        base = db.all_('SELECT * FROM collection_item WHERE collection_id=? ORDER BY order_no,id', (cid,))
        snap['items'] = base
        snap['options'] = exbook.normalise_options(checked['options'], {**snap['collection'], 'layout_options': None})
        layout.apply(snap, checked)
        apply_order(snap, order)
    else:
        saved = snap['collection'].get('layout_options') or {'options': {}, 'items': {}}
    saved = {'options': saved.get('options') or {},
             'items': {k: v for k, v in (saved.get('items') or {}).items() if k in {i['id'] for i in snap['items']}}}
    return snap, saved


@get('/api/collections/{cid}/layout')
def view(req):
    with db.transaction():
        snap, saved = snapshot(req.params['cid'])
        stamp = current_revision(req.params['cid'])
    return {'snapshot': snap, 'layout': saved, 'revision': stamp,
            'draft_scope': db.rel_to_root(db.DB_PATH) + ':' + str(db.scalar("SELECT value FROM meta WHERE key='created_at'"))}


@put('/api/collections/{cid}/layout')
def save(req):
    cid = req.params['cid']
    with db.transaction() as conn:
        if not conn.execute('SELECT 1 FROM collection WHERE id=?', (cid,)).fetchone():
            raise ApiError(404, 'not_found', '题单不存在')
        ids = {r['id'] for r in conn.execute('SELECT id FROM collection_item WHERE collection_id=?', (cid,))}
        body = dict(req.json)
        expected = body.pop('base_revision', None)
        if expected is not None and expected != current_revision(cid, conn):
            raise ApiError(409, 'layout_changed', '题单已在其他页面修改。草稿已保留，请刷新并核对后再恢复。')
        order = body.pop('order', None)
        if order is not None:
            sections = {r['id'] for r in conn.execute('SELECT id FROM collection_section WHERE collection_id=?', (cid,))}
            order = checked_order(order, ids, sections)
        clean = layout.validate(body, ids)
        if order is not None:
            for n, entry in enumerate(order):
                conn.execute('UPDATE collection_item SET order_no=?,section_id=? WHERE id=? AND collection_id=?',
                             (n, entry['section_id'], entry['id'], cid))
        conn.execute('UPDATE collection SET layout_options=?,updated_at=? WHERE id=?', (db.dumps(clean), db.now(), cid))
        db.audit('collection.layout', 'collection', cid, {'items': len(clean['items'])})
        stamp = current_revision(cid, conn)
    return {'layout': clean, 'revision': stamp}


@get('/api/layout-templates')
def templates(req):
    rows = db.all_("SELECT value FROM meta WHERE key LIKE 'layout_template:%' ORDER BY key")
    return {'items': sorted([json.loads(r['value']) for r in rows], key=lambda r: r['name'])}


@post('/api/layout-templates')
def save_template(req):
    if set(req.json) - {'name', 'options', 'space_mm'}:
        raise ApiError(400, 'template', '样式不能包含题目内容或逐题设置')
    name = req.json.get('name')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise ApiError(400, 'template', '样式名称须为1至60字')
    # A reusable style never includes question IDs, content, titles or forced page breaks.
    options = req.json.get('options', {})
    permitted = (layout.TEXT_FIELDS - {'title', 'subtitle'}) | layout.BOOL_FIELDS | {'theme', 'format', 'options_layout'}
    if not isinstance(options, dict) or set(options) - permitted:
        raise ApiError(400, 'template', '样式只能包含通用排版设置')
    clean = layout.validate({'options': options, 'items': {'sample': {'space_mm': req.json.get('space_mm', 30)}}}, {'sample'})
    item = {'id': uuid.uuid4().hex, 'name': name.strip(), 'options': clean['options'],
            'space_mm': clean['items']['sample']['space_mm']}
    with db.transaction() as conn:
        existing = [json.loads(r['value']) for r in conn.execute("SELECT value FROM meta WHERE key LIKE 'layout_template:%'")]
        if any(r['name'].casefold() == item['name'].casefold() for r in existing):
            raise ApiError(409, 'template_name', '已有同名样式，请换一个名称')
        if len(existing) >= 50: raise ApiError(409, 'template_limit', '最多保存50份样式，请先删除不再使用的样式')
        conn.execute('INSERT INTO meta(key,value) VALUES(?,?)', ('layout_template:' + item['id'], db.dumps(item)))
    return {'template': item}


@delete('/api/layout-templates/{tid}')
def delete_template(req):
    if not re.fullmatch(r'[a-f0-9]{32}', req.params['tid']): raise ApiError(404, 'template', '样式不存在')
    with db.transaction() as conn:
        conn.execute('DELETE FROM meta WHERE key=?', ('layout_template:' + req.params['tid'],))
    return {'ok': True}


@post('/api/collections/{cid}/layout/pdf')
def pdf(req):
    version = req.json.get('version', 'student')
    if version not in ('student', 'answer', 'teacher'):
        raise ApiError(400, 'layout', '请选择学生版、答案版或教师版')
    snap, _ = snapshot(req.params['cid'], req.json.get('layout'))
    if not snap['items']: raise ApiError(409, 'empty_collection', '题单为空，请先加入题目')
    if not _compiler.acquire(blocking=False):
        raise ApiError(409, 'preview_busy', '已有PDF校样正在生成，请稍后重试')
    try:
        return build.build_preview(snap, version)
    finally:
        _compiler.release()


@get('/api/layout-previews/{token}/{version}')
def file(req):
    token, version = req.params['token'], req.params['version']
    if not re.fullmatch(r'[a-f0-9]{32}', token) or version not in ('student', 'answer', 'teacher'):
        raise ApiError(404, 'not_found', '校样不存在')
    root = (build.BUILD_ROOT / '_previews').resolve()
    target = (root / token / (version + '.pdf')).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ApiError(404, 'not_found', '校样已清理，请重新生成')
    return 200, 'application/pdf', target.read_bytes()
