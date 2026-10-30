"""Teacher associations and class rosters; selection is not authentication."""
import hashlib
import unicodedata
from app import db
from app.server import ApiError, get, post, patch, put


def text(value, label, limit=150, required=True):
    if value is None and not required:return None
    if not isinstance(value,str) or len(value.strip())>limit or (required and not value.strip()):
        raise ApiError(400,'field',label+'格式不正确或过长')
    return value.strip() or None


def key(value):
    return unicodedata.normalize('NFKC', value or '').strip().casefold()


def code_value(value):
    value = text(value, '学号、工号或代号', 80, False)
    value = unicodedata.normalize('NFKC', value) if value else None
    if value and len(value) > 80:
        raise ApiError(400, 'code', '编号标准化后不能超过 80 字')
    if value and any(unicodedata.category(c) == 'Cc' for c in value):
        raise ApiError(400, 'code', '编号不能包含换行或控制字符')
    return value


def profile_revision(row):
    return hashlib.sha256(db.dumps({k: row.get(k) for k in ('id', 'kind', 'name', 'code', 'note')}).encode()).hexdigest()


def present(row):
    row = dict(row)
    details = [row['code']] if row.get('code') else []
    if row['id'].startswith('TCH') and row.get('note'):
        details.append(row['note'][:24])
    elif row.get('classes'):
        details.append('、'.join(p['name'] for p in row['classes'][:2]))
    row['display_name'] = row['name'] + ('（' + '；'.join(details) + '）' if details else '') + '［' + row['id'] + '］'
    row['profile_revision'] = profile_revision(row)
    return row


def check_profile(req, row):
    if 'base_revision' in req.json and req.body('base_revision') != profile_revision(row):
        raise ApiError(409, 'profile_changed', '资料已被其他页面修改，请重新打开核对')


def check_code(table, code, kind=None, exclude=None):
    if table not in ('audience', 'teacher'):
        raise ValueError('Unsupported identity table')
    if not code:
        return
    rows = db.all_('SELECT id,kind,code FROM audience WHERE kind=?', (kind,)) if table == 'audience' else db.all_('SELECT id,code FROM teacher')
    current = next((r for r in rows if r['id'] == exclude), None)
    # Legacy duplicate codes remain readable/editable; only new collisions fail.
    if current and key(current['code']) == key(code):
        return
    matches = [r for r in rows if r['id'] != exclude and key(r['code']) == key(code)]
    if matches:
        records = [db.one('SELECT * FROM ' + table + ' WHERE id=?', (r['id'],)) for r in matches]
        candidates = decorate(records) if table == 'audience' else [present(r) for r in records]
        raise ApiError(409, 'duplicate_code', '这个编号已被同类档案使用，请选用已有档案或返回更换编号', {'candidates': candidates})


def duplicates(req, table, name, kind=None):
    allowed = req.body('allow_same_name', False)
    if type(allowed) is not bool:
        raise ApiError(400, 'confirmation', '同名确认必须为布尔值')
    if allowed:
        return
    rows = db.all_('SELECT * FROM audience WHERE kind=? ORDER BY id', (kind,)) if table == 'audience' else db.all_('SELECT * FROM teacher ORDER BY id')
    matches = [r for r in rows if key(r['name']) == key(name)]
    if matches:
        candidates = decorate(matches) if table == 'audience' else [present(r) for r in matches]
        raise ApiError(409, 'same_name', '已有同名档案，请核对后选用；确认是另一人或另一班级时可继续新建',
                       {'candidates': candidates})


def creation(req, action, payload):
    operation = text(req.body('operation_id'), '操作编号', 128, False)
    digest = hashlib.sha256(db.dumps(payload).encode()).hexdigest()
    if operation:
        old = db.one('SELECT * FROM identity_request WHERE operation_id=?', (operation,))
        if old:
            if old['action'] != action or old['payload_hash'] != digest:
                raise ApiError(409, 'operation_conflict', '操作编号已用于其他创建内容，请重新核对')
            return operation, digest, old['target_id']
    return operation, digest, None


def remember(conn, request, action, target):
    operation, digest, _ = request
    if operation:
        conn.execute('INSERT INTO identity_request(operation_id,action,payload_hash,target_id,created_at) VALUES(?,?,?,?,?)',
                     (operation, action, digest, target, db.now()))


def audience(aid, container=False):
    row=db.one('SELECT * FROM audience WHERE id=?',(aid,))
    if not row:raise ApiError(404,'not_found','班级、小组或学生不存在')
    if container and row['kind'] not in ('class','group'):raise ApiError(400,'kind','只有班级和学习小组可以设置名单')
    return row


def ids(value):
    if not isinstance(value,list) or len(value)>1000 or any(not isinstance(v,str) or not v or len(v)>80 for v in value):
        raise ApiError(400,'ids','请选择有效的编号列表')
    if len(value)!=len(set(value)):raise ApiError(400,'ids','编号不能重复')
    return value


def validate_teachers(value):
    value=ids(value)
    if any(not db.one('SELECT id FROM teacher WHERE id=?',(tid,)) for tid in value):
        raise ApiError(404,'teacher','所选教师不存在')
    return value


def assign(conn,aid,value):
    value=validate_teachers(value)
    conn.execute('DELETE FROM audience_teacher WHERE audience_id=?',(aid,))
    conn.executemany('INSERT INTO audience_teacher(audience_id,teacher_id) VALUES(?,?)',[(aid,tid) for tid in value])


def revision(kind,aid):
    if kind=='teachers':rows=db.all_('SELECT teacher_id FROM audience_teacher WHERE audience_id=? ORDER BY teacher_id',(aid,))
    else:rows=db.all_('SELECT student_id FROM roster_member WHERE container_id=? ORDER BY student_id',(aid,))
    return hashlib.sha256(db.dumps(rows).encode()).hexdigest()


def check_revision(req,kind,aid):
    if req.body('base_revision')!=revision(kind,aid):
        raise ApiError(409,'conflict','名单或归属已被其他页面修改，请重新打开核对')


def decorate(rows):
    direct={};parents={};counts={}
    for row in db.all_('SELECT a.audience_id,t.* FROM audience_teacher a JOIN teacher t ON t.id=a.teacher_id ORDER BY t.name,t.id'):
        direct.setdefault(row['audience_id'],[]).append(present(row))
    for row in db.all_('SELECT r.container_id,r.student_id,a.name,a.code FROM roster_member r JOIN audience a ON a.id=r.container_id ORDER BY a.name,a.id'):
        parents.setdefault(row['student_id'],[]).append(present({'id':row['container_id'],'name':row['name'],'code':row['code']}))
        counts[row['container_id']]=counts.get(row['container_id'],0)+1
    out=[]
    for row in rows:
        aid=row['id'];teachers={t['id']:t for t in direct.get(aid,[])}
        for parent in parents.get(aid,[]):
            teachers.update({t['id']:t for t in direct.get(parent['id'],[])})
        out.append(present({**row,'direct_teachers':direct.get(aid,[]),'teachers':list(teachers.values()),
                    'classes':parents.get(aid,[]),'member_count':counts.get(aid,0)}))
    return out


def filter_rows(rows,teacher_id):
    if not teacher_id:return rows
    if teacher_id=='_unassigned':return [r for r in rows if not r['teachers']]
    if not db.one('SELECT id FROM teacher WHERE id=?',(teacher_id,)):raise ApiError(404,'teacher','教师不存在')
    return [r for r in rows if any(t['id']==teacher_id for t in r['teachers'])]


@get('/api/teachers')
def teacher_list(req):
    rows=db.all_('SELECT * FROM teacher ORDER BY name,id')
    return {'items':[present(row) for row in rows],'total':len(rows)}


@post('/api/teachers')
def teacher_create(req):
    if set(req.json)-{'name','note','code','operation_id','allow_same_name'}:raise ApiError(400,'fields','教师字段无效')
    name=text(req.body('name'),'教师名称',80);note=text(req.body('note'),'备注',500,False)
    code=code_value(req.body('code'))
    with db.transaction() as conn:
        request=creation(req,'teacher.create',{'name':name,'note':note,'code':code})
        if request[2]:return {'teacher':present(db.one('SELECT * FROM teacher WHERE id=?',(request[2],))),'replayed':True}
        check_code('teacher',code);duplicates(req,'teacher',name)
        tid=db.next_id(conn,'TCH')
        conn.execute('INSERT INTO teacher(id,name,code,note,created_at) VALUES(?,?,?,?,?)',(tid,name,code,note,db.now()))
        remember(conn,request,'teacher.create',tid)
        db.audit('teacher.create','teacher',tid,{'name':name})
    return {'teacher':present(db.one('SELECT * FROM teacher WHERE id=?',(tid,)))}


@patch('/api/teachers/{tid}')
def teacher_update(req):
    if not req.json or set(req.json)-{'name','note','code','base_revision'}:raise ApiError(400,'fields','教师字段无效')
    tid=req.params['tid']
    with db.transaction() as conn:
        row=db.one('SELECT * FROM teacher WHERE id=?',(tid,))
        if not row:raise ApiError(404,'teacher','教师不存在')
        check_profile(req,row)
        name=text(req.body('name',row['name']),'教师名称',80)
        note=text(req.body('note',row['note']),'备注',500,False)
        code=code_value(req.body('code',row.get('code')));check_code('teacher',code,exclude=tid)
        conn.execute('UPDATE teacher SET name=?,code=?,note=? WHERE id=?',(name,code,note,tid))
        db.audit('teacher.update','teacher',tid,{'name':name})
    return {'teacher':present(db.one('SELECT * FROM teacher WHERE id=?',(tid,)))}


def teacher_data(aid):
    row=audience(aid)
    return {'audience':decorate([row])[0],'revision':revision('teachers',aid)}


@get('/api/audiences/{aid}/teachers')
def audience_teachers(req):
    with db.read_transaction():return teacher_data(req.params['aid'])


@put('/api/audiences/{aid}/teachers')
def teachers_replace(req):
    aid=req.params['aid']
    with db.transaction() as conn:
        audience(aid);check_revision(req,'teachers',aid)
        assign(conn,aid,req.body('teacher_ids'))
        db.audit('audience.teachers','audience',aid,{'teacher_ids':req.body('teacher_ids')})
        result=teacher_data(aid)
    return result


def member_data(aid):
    row=audience(aid,True)
    rows=db.all_('SELECT a.* FROM audience a JOIN roster_member r ON r.student_id=a.id WHERE r.container_id=? ORDER BY a.name,a.id',(aid,))
    return {'audience':present(row),'items':decorate(rows),'total':len(rows),'revision':revision('members',aid)}


@get('/api/audiences/{aid}/members')
def members(req):
    with db.read_transaction():return member_data(req.params['aid'])


@put('/api/audiences/{aid}/members')
def members_replace(req):
    aid=req.params['aid'];values=ids(req.body('student_ids'))
    with db.transaction() as conn:
        audience(aid,True);check_revision(req,'members',aid)
        if any(audience(sid)['kind']!='student' for sid in values):raise ApiError(400,'kind','名单中只能加入学生')
        conn.execute('DELETE FROM roster_member WHERE container_id=?',(aid,))
        conn.executemany('INSERT INTO roster_member(container_id,student_id,created_at) VALUES(?,?,?)',[(aid,sid,db.now()) for sid in values])
        db.audit('roster.replace','audience',aid,{'student_ids':values})
        result=member_data(aid)
    return result


@post('/api/audiences/{aid}/members')
def member_create(req):
    if set(req.json)-{'name','code','note','operation_id','allow_same_name'}:
        raise ApiError(400,'fields','学生字段无效')
    aid=req.params['aid'];name=text(req.body('name'),'学生姓名或代号')
    code=code_value(req.body('code'));note=text(req.body('note'),'学生备注',1000,False)
    with db.transaction() as conn:
        audience(aid,True)
        request=creation(req,'member.create',{'container_id':aid,'name':name,'code':code,'note':note})
        if request[2]:return {'student':decorate([audience(request[2])])[0],'replayed':True}
        check_code('audience',code,'student');duplicates(req,'audience',name,'student')
        sid=db.next_id(conn,'AUD')
        conn.execute("INSERT INTO audience(id,kind,name,code,note,created_at) VALUES(?,'student',?,?,?,?)",(sid,name,code,note,db.now()))
        conn.execute('INSERT INTO roster_member(container_id,student_id,created_at) VALUES(?,?,?)',(aid,sid,db.now()))
        remember(conn,request,'member.create',sid)
        db.audit('roster.student_create','audience',aid,{'student_id':sid})
    return {'student':decorate([audience(sid)])[0]}
