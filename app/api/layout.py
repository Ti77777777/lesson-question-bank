"""Draft handout preview, without creating a release or changing question content."""
import re
import threading
from app import db, layout
from app.server import ApiError, get, put, post
from app.latex import build, exbook

_compiler = threading.BoundedSemaphore(1)


def snapshot(cid, draft=None):
    snap = build.make_snapshot(cid)
    if draft is not None:
        checked = layout.validate(draft, {i['id'] for i in snap['items']})
        # Recreate from the original items so clearing an override really clears it.
        saved = snap['collection'].get('layout_options') or {}
        base = db.all_('SELECT * FROM collection_item WHERE collection_id=? ORDER BY order_no,id', (cid,))
        snap['items'] = base
        snap['options'] = exbook.normalise_options(checked['options'], {**snap['collection'], 'layout_options': None})
        layout.apply(snap, checked)
    else:
        saved = snap['collection'].get('layout_options') or {'options': {}, 'items': {}}
    saved = {'options': saved.get('options') or {},
             'items': {k: v for k, v in (saved.get('items') or {}).items() if k in {i['id'] for i in snap['items']}}}
    return snap, saved


@get('/api/collections/{cid}/layout')
def view(req):
    snap, saved = snapshot(req.params['cid'])
    return {'snapshot': snap, 'layout': saved}


@put('/api/collections/{cid}/layout')
def save(req):
    cid = req.params['cid']
    with db.transaction() as conn:
        if not conn.execute('SELECT 1 FROM collection WHERE id=?', (cid,)).fetchone():
            raise ApiError(404, 'not_found', '题单不存在')
        ids = {r['id'] for r in conn.execute('SELECT id FROM collection_item WHERE collection_id=?', (cid,))}
        clean = layout.validate(req.json, ids)
        conn.execute('UPDATE collection SET layout_options=?,updated_at=? WHERE id=?', (db.dumps(clean), db.now(), cid))
        db.audit('collection.layout', 'collection', cid, {'items': len(clean['items'])})
    return {'layout': clean}


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
