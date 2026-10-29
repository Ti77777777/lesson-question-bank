"""Teacher associations and class rosters; selection is not authentication."""
import hashlib
from app import db
from app.server import ApiError, get, post, patch, put


def text(value, label, limit=150, required=True):
    if value is None and not required:return None
    if not isinstance(value,str) or len(value.strip())>limit or (required and not value.strip()):
        raise ApiError(400,'field',label+'格式不正确或过长')
    return value.strip() or None


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
    for row in db.all_('SELECT a.audience_id,t.id,t.name FROM audience_teacher a JOIN teacher t ON t.id=a.teacher_id ORDER BY t.name,t.id'):
        direct.setdefault(row['audience_id'],[]).append({'id':row['id'],'name':row['name']})
    for row in db.all_('SELECT r.container_id,r.student_id,a.name FROM roster_member r JOIN audience a ON a.id=r.container_id ORDER BY a.name,a.id'):
        parents.setdefault(row['student_id'],[]).append({'id':row['container_id'],'name':row['name']})
        counts[row['container_id']]=counts.get(row['container_id'],0)+1
    out=[]
    for row in rows:
        aid=row['id'];teachers={t['id']:t for t in direct.get(aid,[])}
        for parent in parents.get(aid,[]):
            teachers.update({t['id']:t for t in direct.get(parent['id'],[])})
        out.append({**row,'direct_teachers':direct.get(aid,[]),'teachers':list(teachers.values()),
                    'classes':parents.get(aid,[]),'member_count':counts.get(aid,0)})
    return out


def filter_rows(rows,teacher_id):
    if not teacher_id:return rows
    if teacher_id=='_unassigned':return [r for r in rows if not r['teachers']]
    if not db.one('SELECT id FROM teacher WHERE id=?',(teacher_id,)):raise ApiError(404,'teacher','教师不存在')
    return [r for r in rows if any(t['id']==teacher_id for t in r['teachers'])]


@get('/api/teachers')
def teacher_list(req):
    rows=db.all_('SELECT * FROM teacher ORDER BY name,id')
    return {'items':rows,'total':len(rows)}


@post('/api/teachers')
def teacher_create(req):
    if set(req.json)-{'name','note'}:raise ApiError(400,'fields','教师字段无效')
    name=text(req.body('name'),'教师名称',80);note=text(req.body('note'),'备注',500,False)
    with db.transaction() as conn:
        tid=db.next_id(conn,'TCH')
        conn.execute('INSERT INTO teacher VALUES(?,?,?,?)',(tid,name,note,db.now()))
        db.audit('teacher.create','teacher',tid,{'name':name})
    return {'teacher':db.one('SELECT * FROM teacher WHERE id=?',(tid,))}


@patch('/api/teachers/{tid}')
def teacher_update(req):
    if not req.json or set(req.json)-{'name','note'}:raise ApiError(400,'fields','教师字段无效')
    tid=req.params['tid']
    with db.transaction() as conn:
        row=db.one('SELECT * FROM teacher WHERE id=?',(tid,))
        if not row:raise ApiError(404,'teacher','教师不存在')
        name=text(req.body('name',row['name']),'教师名称',80)
        note=text(req.body('note',row['note']),'备注',500,False)
        conn.execute('UPDATE teacher SET name=?,note=? WHERE id=?',(name,note,tid))
        db.audit('teacher.update','teacher',tid,{'name':name})
    return {'teacher':db.one('SELECT * FROM teacher WHERE id=?',(tid,))}


def teacher_data(aid):
    row=audience(aid)
    return {'audience':decorate([row])[0],'revision':revision('teachers',aid)}


@get('/api/audiences/{aid}/teachers')
def audience_teachers(req):
    with db.transaction():return teacher_data(req.params['aid'])


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
    return {'audience':row,'items':rows,'total':len(rows),'revision':revision('members',aid)}


@get('/api/audiences/{aid}/members')
def members(req):
    with db.transaction():return member_data(req.params['aid'])


@put('/api/audiences/{aid}/members')
def members_replace(req):
    aid=req.params['aid'];values=ids(req.body('student_ids'))
    with db.transaction() as conn:
        audience(aid,True);check_revision(req,'members',aid)
        if any(audience(sid)['kind']!='student' for sid in values):raise ApiError(400,'kind','名单中只能加入学生')
        conn.execute('DELETE FROM roster_member WHERE container_id=?',(aid,))
        conn.executemany('INSERT INTO roster_member VALUES(?,?,?)',[(aid,sid,db.now()) for sid in values])
        db.audit('roster.replace','audience',aid,{'student_ids':values})
        result=member_data(aid)
    return result


@post('/api/audiences/{aid}/members')
def member_create(req):
    aid=req.params['aid'];name=text(req.body('name'),'学生姓名或代号')
    code=text(req.body('code'),'学生代号',80,False)
    with db.transaction() as conn:
        audience(aid,True)
        sid=db.next_id(conn,'AUD')
        conn.execute("INSERT INTO audience(id,kind,name,code,created_at) VALUES(?,'student',?,?,?)",(sid,name,code,db.now()))
        conn.execute('INSERT INTO roster_member VALUES(?,?,?)',(aid,sid,db.now()))
        db.audit('roster.student_create','audience',aid,{'student_id':sid})
    return {'student':audience(sid)}
