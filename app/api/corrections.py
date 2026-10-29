"""题目纠错登记与处理，不覆盖题目内容或既有版本。"""
from app import db
from app.server import ApiError, get, post, patch

CATEGORIES = ('stem', 'answer', 'solution', 'figure', 'tags', 'source', 'difficulty', 'other')
STATUSES = ('pending', 'resolved', 'dismissed')


def _body(req):
    if not isinstance(req.json, dict):
        raise ApiError(400, 'bad_field', '请提交纠错信息对象')
    return req.json


def _text(data, key, required=False, limit=5000):
    value = data.get(key)
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ApiError(400, 'bad_field', f'{key} 必须是文本')
    value = value.strip()
    if required and not value:
        raise ApiError(400, 'bad_field', '请填写问题说明或处理说明')
    if len(value) > limit:
        raise ApiError(400, 'bad_field', f'文本不能超过 {limit} 字')
    return value


def _version(conn, qid, vid):
    row = conn.execute('SELECT id FROM question_version WHERE id=? AND question_id=?', (vid, qid)).fetchone()
    if row is None:
        raise ApiError(400, 'bad_version', '所选版本不属于这道题目')


def _event(conn, cid, status, note, fixed, ts):
    conn.execute('INSERT INTO correction_event(id,correction_id,status,note,fixed_version_id,created_at) VALUES(?,?,?,?,?,?)',
                 (db.next_id(conn, 'CE'), cid, status, note, fixed, ts))


@get('/api/questions/{qid}/corrections')
def listing(req):
    qid = req.params['qid']
    question = db.one('SELECT id,short_name,current_version_id FROM question WHERE id=?', (qid,))
    if question is None:
        raise ApiError(404, 'not_found', '题目不存在')
    versions = db.all_('SELECT id,version_no FROM question_version WHERE question_id=? ORDER BY version_no DESC', (qid,))
    items = db.all_('SELECT c.*,v.version_no,f.version_no AS fixed_version_no FROM question_correction c '
                    'JOIN question_version v ON v.id=c.version_id '
                    'LEFT JOIN question_version f ON f.id=c.fixed_version_id '
                    'WHERE c.question_id=? ORDER BY c.created_at DESC,c.id DESC', (qid,))
    events = db.all_('SELECT e.*,v.version_no AS fixed_version_no FROM correction_event e '
                     'JOIN question_correction c ON c.id=e.correction_id '
                     'LEFT JOIN question_version v ON v.id=e.fixed_version_id '
                     'WHERE c.question_id=? ORDER BY e.id', (qid,))
    by_id = {item['id']: item for item in items}
    for item in items:
        item['events'] = []
    for event in events:
        by_id[event['correction_id']]['events'].append(event)
    return {'question': question, 'versions': versions, 'items': items,
            'pending_count': sum(item['status'] == 'pending' for item in items)}


@post('/api/questions/{qid}/corrections')
def create(req):
    data = _body(req)
    qid = req.params['qid']
    category = _text(data, 'category')
    if category not in CATEGORIES:
        raise ApiError(400, 'bad_field', '请选择有效的问题类型')
    description = _text(data, 'description', required=True)
    suggestion = _text(data, 'suggestion')
    vid = _text(data, 'version_id', required=True)
    with db.transaction() as conn:
        if conn.execute('SELECT 1 FROM question WHERE id=?', (qid,)).fetchone() is None:
            raise ApiError(404, 'not_found', '题目不存在')
        _version(conn, qid, vid)
        # 双击、断线重提同一条待处理反馈时返回原记录。
        existing = conn.execute('SELECT id FROM question_correction WHERE question_id=? AND version_id=? '
                                'AND category=? AND description=? AND COALESCE(suggestion,\'\')=? AND status=\'pending\'',
                                (qid, vid, category, description, suggestion)).fetchone()
        if existing:
            return {'id': existing['id'], 'duplicate': True}
        cid, ts = db.next_id(conn, 'COR'), db.now()
        conn.execute('INSERT INTO question_correction(id,question_id,version_id,category,description,suggestion,created_at,updated_at) '
                     'VALUES(?,?,?,?,?,?,?,?)', (cid, qid, vid, category, description, suggestion or None, ts, ts))
        _event(conn, cid, 'pending', '登记纠错', None, ts)
        db.audit('correction.create', 'question_correction', cid, {'question_id': qid, 'version_id': vid})
    return {'id': cid, 'duplicate': False}


@patch('/api/corrections/{cid}')
def handle(req):
    data = _body(req)
    status = _text(data, 'status')
    if status not in STATUSES:
        raise ApiError(400, 'bad_field', '纠错状态无效')
    note = _text(data, 'note', required=True)
    fixed = _text(data, 'fixed_version_id') or None
    if fixed and status != 'resolved':
        raise ApiError(400, 'bad_field', '仅已修正记录可关联修订版本')
    with db.transaction() as conn:
        row = conn.execute('SELECT * FROM question_correction WHERE id=?', (req.params['cid'],)).fetchone()
        if row is None:
            raise ApiError(404, 'not_found', '纠错记录不存在')
        if _text(data, 'expected_status', required=True) != row['status']:
            raise ApiError(409, 'correction_changed', '处理状态已变化，请刷新记录后重试')
        if row['status'] == status:
            raise ApiError(409, 'same_status', '记录已处于该状态')
        if fixed:
            _version(conn, row['question_id'], fixed)
        ts = db.now()
        conn.execute('UPDATE question_correction SET status=?,resolution_note=?,fixed_version_id=?,updated_at=? WHERE id=?',
                     (status, note, fixed, ts, row['id']))
        _event(conn, row['id'], status, note, fixed, ts)
        db.audit('correction.handle', 'question_correction', row['id'],
                 {'from': row['status'], 'to': status, 'note': note, 'fixed_version_id': fixed})
    return {'id': row['id'], 'status': status}
