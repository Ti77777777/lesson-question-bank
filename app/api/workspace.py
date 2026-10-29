"""Saved searches and previewed, reversible source corrections."""
import hashlib
import json
import sqlite3
from app import db, provenance, search_catalog
from app.server import ApiError, get, post, patch, delete

QUERY_KEYS = {'keyword','source_id','source_category','source_year','source_region','source_grade',
              'qtype','tags','tag_mode','think_min','think_max','think_unrated','calc_max','scope_max',
              'review','sort','correction'}
SOURCE_FIELDS = ('name','school','year','region','exam_type','grade')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def preset_body(req):
    name=req.body('name');query=req.body('query')
    if not isinstance(name,str) or not 1<=len(name.strip())<=80:
        raise ApiError(400,'name','方案名称应为1至80字')
    if not isinstance(query,dict) or set(query)-QUERY_KEYS or any(not isinstance(v,str) or len(v)>2000 for v in query.values()):
        raise ApiError(400,'query','检索方案包含无效条件')
    return name.strip(),{k:v for k,v in query.items() if v}


@get('/api/search/presets')
def presets(req):
    rows=db.all_('SELECT * FROM search_preset ORDER BY updated_at DESC,id')
    for row in rows:row['query']=json.loads(row.pop('query_json'))
    return {'items':rows}


@post('/api/search/presets')
def create_preset(req):
    name,query=preset_body(req)
    try:
        with db.transaction() as conn:
            pid=db.next_id(conn,'SP')
            conn.execute('INSERT INTO search_preset VALUES(?,?,?,?,?)',(pid,name,db.dumps(query),db.now(),db.now()))
            db.audit('save_search','search_preset',pid,{'name':name})
    except sqlite3.IntegrityError:raise ApiError(409,'duplicate_name','已有同名检索方案，请换一个名称') from None
    return {'id':pid,'name':name,'query':query}


@patch('/api/search/presets/{pid}')
def update_preset(req):
    name,query=preset_body(req)
    try:
        with db.transaction() as conn:
            if not conn.execute('SELECT 1 FROM search_preset WHERE id=?',(req.params['pid'],)).fetchone():raise ApiError(404,'not_found','方案不存在')
            conn.execute('UPDATE search_preset SET name=?,query_json=?,updated_at=? WHERE id=?',(name,db.dumps(query),db.now(),req.params['pid']))
            db.audit('update_search','search_preset',req.params['pid'],{'name':name})
    except sqlite3.IntegrityError:raise ApiError(409,'duplicate_name','已有同名检索方案') from None
    return {'ok':True}


@delete('/api/search/presets/{pid}')
def delete_preset(req):
    with db.transaction() as conn:
        conn.execute('DELETE FROM search_preset WHERE id=?',(req.params['pid'],))
        db.audit('delete_search','search_preset',req.params['pid'])
    return {'ok':True}


def source_patch(value):
    if not isinstance(value,dict) or not value or set(value)-set(SOURCE_FIELDS):raise ApiError(400,'fields','来源修改字段无效')
    out={}
    for key,v in value.items():
        if v is not None and not isinstance(v,str):raise ApiError(400,'fields','来源信息应填写文本')
        v=(v or '').strip()
        if len(v)>300:raise ApiError(400,'fields','来源信息过长')
        if key=='name' and not v:raise ApiError(400,'name','卷名不能为空')
        if key=='year' and v and (not v.isascii() or not v.isdigit() or not 1900<=int(v)<=2100):raise ApiError(400,'year','年份应为1900至2100')
        if key=='exam_type' and v and v not in search_catalog.EXAM_CATEGORIES:raise ApiError(400,'exam_type','考试类别无效')
        if key=='grade' and v and v not in search_catalog.GRADES:raise ApiError(400,'grade','年级无效')
        out[key]=v or None
    return out


def source_snapshot(sid):
    row=db.one('SELECT * FROM source WHERE id=?',(sid,))
    if not row:raise ApiError(404,'source','来源不存在')
    # Do not compete with document identity extraction in a running import.
    if db.scalar("SELECT count(*) FROM ai_job j JOIN source_file f ON f.id=j.source_file_id WHERE f.source_id=? AND j.state IN ('running','queued')",(sid,)):
        raise ApiError(409,'running','相关试卷正在处理，请先暂停处理任务')
    return {'id':sid,**{k:row.get(k) for k in SOURCE_FIELDS}}


def preview_sources(req):
    items=req.body('items')
    if not isinstance(items,list) or not 1<=len(items)<=100:raise ApiError(400,'items','请选择1至100份资料')
    before=[];after=[];seen=set();counts=[]
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or item['id'] in seen:raise ApiError(400,'items','来源编号无效或重复')
        seen.add(item['id']);changes=source_patch(item.get('changes'))
        old=source_snapshot(item['id']);new={**old,**changes}
        if old==new:continue
        before.append(old);after.append(new)
        counts.append({'id':old['id'],'questions':db.scalar('SELECT count(DISTINCT question_id) FROM occurrence WHERE source_id=?',(old['id'],))})
    if not before:raise ApiError(400,'no_change','没有需要保存的修改')
    return {'before':before,'after':after,'affected':counts,'preview_hash':digest([before,after])}


@post('/api/sources/batch-preview')
def source_preview(req):
    return preview_sources(req)


def apply_sources(conn,before,after):
    for old,new in zip(before,after):
        changes={k:new[k] for k in SOURCE_FIELDS if new[k]!=old[k]}
        conn.execute('UPDATE source SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',(*changes.values(),new['id']))
        if 'name' in changes:
            provenance.rename_generated_titles(conn,new['id'],old['name'],new['name'])
            provenance.sync_source_jobs(conn,new['id'],new['name'])


@post('/api/sources/batch-apply')
def source_apply(req):
    with db.transaction() as conn:
        preview=preview_sources(req)
        if req.body('preview_hash')!=preview['preview_hash']:raise ApiError(409,'changed','来源信息已变化，请重新预览')
        apply_sources(conn,preview['before'],preview['after'])
        cid=db.next_id(conn,'SC')
        conn.execute('INSERT INTO source_change VALUES(?,?,?,?,?)',(cid,db.dumps(preview['before']),db.dumps(preview['after']),'applied',db.now()))
        db.audit('batch_source_update','source_change',cid,{'source_ids':[s['id'] for s in preview['after']]})
    return {'id':cid,'changed':len(preview['after'])}


@get('/api/source-changes')
def source_changes(req):
    rows=db.all_('SELECT * FROM source_change ORDER BY created_at DESC,id DESC LIMIT 30')
    for row in rows:
        row['before']=json.loads(row.pop('before_json'));row['after']=json.loads(row.pop('after_json'))
    return {'items':rows}


@post('/api/source-changes/{cid}/undo')
def undo_sources(req):
    with db.transaction() as conn:
        row=db.one('SELECT * FROM source_change WHERE id=?',(req.params['cid'],))
        if not row or row['state']!='applied':raise ApiError(409,'state','这次修改不存在或已撤销')
        before=json.loads(row['before_json']);after=json.loads(row['after_json'])
        if [source_snapshot(s['id']) for s in after]!=after:raise ApiError(409,'changed','来源已被再次修改，不能直接撤销，请手动核对')
        apply_sources(conn,after,before)
        conn.execute("UPDATE source_change SET state='undone' WHERE id=?",(row['id'],))
        db.audit('undo_source_update','source_change',row['id'])
    return {'ok':True}
