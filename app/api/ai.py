"""AI workbench. Credentials and uploaded documents never enter public static routes."""
import hashlib
import io
import json
import math
import re
import urllib.parse
import zipfile
import uuid
from pathlib import Path
from app import db
from app.server import ApiError,get,post
from app.ai import adopt,contracts,credentials,documents,provider,worker,identity,settings

def require(table, identity):
    row=db.one('SELECT * FROM '+table+' WHERE id=?',(identity,))
    if not row:raise ApiError(404,'not_found','记录不存在')
    return row

def error(exc):
    raise ApiError(400,getattr(exc,'code','invalid_input'),str(exc)) from None

@get('/api/ai/status')
def status(req):
    cfg = settings.current()
    return {'configured':credentials.configured(cfg),'model':cfg['model'],'provider':settings.PRESETS[cfg['preset']]['label'],
      'max_upload_mb':50,'pdf_max_pages':50,'credential_storage':('Windows DPAPI（当前用户加密）' if credentials.os.name == 'nt' else '环境变量（不写入配置）'),
      'processing':'后台队列；PDF本地转页图；解答与参考答案分开调用；AI自复核后待教师确认'}

@post('/api/ai/credentials')
def save_credentials(req):
    try:credentials.save_key(req.body('key',required=True))
    except ValueError as exc:error(exc)
    return {'configured':True}

@post('/api/ai/probe')
def probe(req):
    try:return provider.probe(req.json.get('mode', 'models'))
    except provider.ProviderError as exc:error(exc)

@get('/api/ai/settings')
def connection_settings(req):
    cfg = settings.current()
    return {'config': cfg, 'configured': credentials.configured(cfg),
            'presets': [{'id': key, 'label': value['label'], 'config': settings.preset(key)} for key, value in settings.PRESETS.items()]}

@post('/api/ai/settings')
def save_connection_settings(req):
    try:
        cfg = settings.validate(req.json.get('config'), require_model=False)
        key = req.json.get('key')
        if key: credentials.save_key(key, cfg)
        settings.save(cfg)
    except ValueError as exc: error(exc)
    return {'config': cfg, 'configured': credentials.configured(cfg)}

@post('/api/ai/files')
def upload(req):
    raw=req._raw
    if not raw or len(raw)>50*1024*1024:raise ApiError(413,'upload_size','文件须在50MB以内')
    filename=Path(urllib.parse.unquote(req.headers.get('X-Filename','upload.pdf')).replace('\\','/')).name
    filename=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',filename)[:160]
    suffix=Path(filename).suffix.lower()
    valid=(suffix=='.pdf' and raw.startswith(b'%PDF-')) or (suffix=='.png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or (suffix in ('.jpg','.jpeg') and raw.startswith(b'\xff\xd8\xff')) or (suffix=='.webp' and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP')
    if not valid:raise ApiError(400,'file_type','仅支持PDF、PNG、JPEG、WebP，且文件内容须与扩展名一致')
    sha=hashlib.sha256(raw).hexdigest()
    existing=db.one('SELECT * FROM source_file WHERE sha256=?',(sha,))
    if existing:return {'file':existing,'duplicate':True}
    with db.transaction() as conn:
        sid=db.next_id(conn,'SRC');fid=db.next_id(conn,'SF')
        directory=db.store_dir('sources')/sid;directory.mkdir(parents=True,exist_ok=True)
        target=directory/(fid+suffix);target.write_bytes(raw)
        conn.execute('INSERT INTO source(id,name,kind,pending_note,note,created_at) VALUES(?,?,?,?,?,?)',
          (sid,'待识别试卷来源','exam','来源与授权范围待教师确认','AI上传原件，未经自动改写',db.now()))
        conn.execute('INSERT INTO source_file(id,source_id,filename,rel_path,sha256,bytes,uploaded_at) VALUES(?,?,?,?,?,?,?)',
          (fid,sid,filename,target.relative_to(db.ROOT).as_posix(),sha,len(raw),db.now()))
        db.audit('ai_upload','source_file',fid,{'sha256':sha,'bytes':len(raw)})
    return {'file':require('source_file',fid),'duplicate':False}

def _existing_source(vid):
    v=require('question_version',vid)
    paths=[]
    stem='\n'.join([v['stem']]+[x.get('text','') for x in v.get('options') or []]+[x.get('text','') for x in v.get('subquestions') or []])
    for rel in re.findall(r'\\qbankimage\{([^}]+)\}',stem):
        p=(db.ROOT/rel).resolve()
        if not p.is_relative_to((db.ROOT/'assets').resolve()) or not p.is_file():raise ApiError(400,'figure_missing','题图不存在或路径无效')
        paths.append(rel)
    return {'qno':v['question_id'],'qtype':v['qtype'],'stem':v['stem'],'options':v.get('options') or [],
      'subquestions':v.get('subquestions') or [],'total_points':v.get('total_points'),
      'reference':{'answer':v.get('answer') or '', 'analysis':v.get('analysis') or '',
        'solutions':db.all_('SELECT approach,steps,conditions FROM solution WHERE question_version_id=?',(vid,))},
      'figure_paths':paths,'statement_images':[],'pages':[],'uncertainties':[],'material_refs':v.get('material_refs') or []}

@post('/api/ai/jobs')
def create_jobs(req):
    data=req.json
    files=data.get('file_ids',[]);ids=data.get('question_ids',[])
    if not isinstance(files,list) or not isinstance(ids,list) or not 1<=len(files)+len(ids)<=100:
        raise ApiError(400,'job_input','请选择1至100份文件或题目')
    if any(not isinstance(value,str) or not value.strip() for value in files+ids):
        raise ApiError(400,'job_input','文件和题目编号必须是非空文本')
    try:
        budget=float(data.get('budget_cny',10));maximum=int(data.get('max_requests',300))
        if not math.isfinite(budget) or not 0<budget<=500 or not 1<=maximum<=3000:raise ValueError()
    except (ValueError,TypeError):raise ApiError(400,'budget','预算应为0至500元，请求上限为1至3000')
    versions=[]
    for qid in dict.fromkeys(ids):
        q=require('question',qid)
        versions.append((q['current_version_id'],_existing_source(q['current_version_id'])))
    for fid in files:require('source_file',fid)
    pages=data.get('pages',[])
    if not isinstance(pages,list) or any(type(p)!=int or not 1<=p<=50 for p in pages):raise ApiError(400,'pages','页码应为1至50的整数数组')
    try:
        connection = settings.validate(settings.current())
        settings.require_prices(connection)
    except ValueError as exc: error(exc)
    if files and not connection['vision']:
        raise ApiError(400, 'vision_unsupported', '导入PDF或图片需要支持图片的模型，请先修改接口设置')
    config={'audience':str(data.get('audience','高一学生'))[:200],'pages':pages,'auto_accept':bool(data.get('auto_accept',True)), 'connection': connection}
    jobs=[]
    with db.transaction() as conn:
        bid=db.next_id(conn,'AIB')
        conn.execute('INSERT INTO ai_batch VALUES(?,?,?,?,?,?,?)',(bid,'active',db.dumps(config),int(budget*1000000),maximum,db.now(),db.now()))
        for fid in dict.fromkeys(files):
            f=require('source_file',fid);jid=db.next_id(conn,'AIJ')
            conn.execute('INSERT INTO ai_job(id,batch_id,source_file_id,title,state,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(jid,bid,fid,f['filename'],'queued','prepare',db.now(),db.now()));jobs.append(jid)
        if versions:
            jid=db.next_id(conn,'AIJ')
            conn.execute('INSERT INTO ai_job(id,batch_id,title,state,stage,document_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(jid,bid,'已有题目补全 · '+str(len(versions))+'题','queued','solve','{"prepared":true}',db.now(),db.now()));jobs.append(jid)
            for vid,source in versions:
                conn.execute('INSERT INTO ai_question(id,job_id,question_key,original_qno,qtype,state,base_version_id,source_json) VALUES(?,?,?,?,?,?,?,?)',(db.next_id(conn,'AIQ'),jid,vid,source['qno'],source['qtype'],'recognized',vid,db.dumps(source)))
        db.audit('ai_start','ai_batch',bid,{'jobs':jobs,'budget_cny':budget})
    return {'batch_id':bid,'job_ids':jobs}

def _job(row):
    out=dict(row)
    for field in ('progress_json','document_json','error_json'):out[field.removesuffix('_json')]=worker.unpack(out,field)
    counts=db.all_('SELECT state,count(*) n FROM ai_question WHERE job_id=? GROUP BY state',(row['id'],))
    out['counts']={x['state']:x['n'] for x in counts}
    out['usage']=db.one('SELECT count(*) requests,COALESCE(sum(COALESCE(actual_cost_micro,reserved_cost_micro)),0)/1000000.0 estimated_cny,COALESCE(sum(reserved_cost_micro),0)/1000000.0 reserved_cny FROM ai_step_run WHERE job_id=?',(row['id'],))
    out['budget']=db.one('SELECT max_cost_micro/1000000.0 budget_cny,max_requests FROM ai_batch WHERE id=?',(row['batch_id'],))
    return out

@get('/api/ai/jobs')
def list_jobs(req):return {'items':[_job(x) for x in db.all_('SELECT * FROM ai_job ORDER BY created_at DESC,id DESC LIMIT 100')]}

def _question(row):
    out=dict(row)
    for f in ('source_json','solve_json','review_json','error_json'):out[f.removesuffix('_json')]=worker.unpack(out,f)
    candidate=db.one('SELECT * FROM ai_candidate WHERE ai_question_id=? AND revision=?',(row['id'],row['current_revision']))
    if candidate:
        candidate['content']=worker.unpack(candidate,'content_json');candidate.pop('content_json',None)
    out['candidate']=candidate
    out['evidence']=db.all_('SELECT * FROM ai_evidence_region WHERE ai_question_id=? ORDER BY page_no,id',(row['id'],))
    for f in ('source_json','solve_json','review_json','error_json'):out.pop(f,None)
    return out

@get('/api/ai/jobs/{jid}')
def job_detail(req):
    job=_job(require('ai_job',req.params['jid']))
    return {'job':job,'questions':[_question(x) for x in db.all_('SELECT * FROM ai_question WHERE job_id=? ORDER BY CAST(original_qno AS INTEGER),id',(job['id'],))],
      'source_file':require('source_file',job['source_file_id']) if job['source_file_id'] else None,
      'source_document':identity.for_job(job),
      'runs':db.all_('SELECT id,stage,ai_question_id,state,model,prompt_version,usage_json,actual_cost_micro,reserved_cost_micro,billing_state,error_json,created_at,completed_at FROM ai_step_run WHERE job_id=? ORDER BY id',(job['id'],))}

@post('/api/ai/jobs/{jid}/control')
def control(req):
    j=require('ai_job',req.params['jid']);action=req.body('action',required=True)
    if action not in ('pause','resume','retry','cancel'):raise ApiError(400,'action','操作无效')
    target={'pause':'paused','resume':'queued','retry':'queued','cancel':'cancelled'}[action]
    with db.transaction() as conn:
        j=dict(conn.execute('SELECT * FROM ai_job WHERE id=?',(j['id'],)).fetchone())
        if action in ('resume','retry') and j['state'] in ('running','queued'):
            raise ApiError(409,'running','任务已排队或正在运行，请等待当前步骤完成')
        if action in ('resume','retry') and conn.execute("SELECT 1 FROM ai_step_run WHERE job_id=? AND state='running'",(j['id'],)).fetchone():raise ApiError(409,'request_running','当前AI请求还未返回，请稍后继续；暂停会在请求返回后生效')
        if 'budget_cny' in req.json:
            try:b=float(req.body('budget_cny'))
            except (ValueError,TypeError):raise ApiError(400,'budget','预算无效')
            if not math.isfinite(b) or not 0<b<=500:raise ApiError(400,'budget','预算无效')
            conn.execute('UPDATE ai_batch SET max_cost_micro=?,updated_at=? WHERE id=?',(int(b*1000000),db.now(),j['batch_id']))
        if 'max_requests' in req.json:
            try:maximum=int(req.body('max_requests'))
            except (ValueError,TypeError):raise ApiError(400,'requests','调用次数上限无效')
            if not 1<=maximum<=3000:raise ApiError(400,'requests','调用次数上限应为1至3000')
            conn.execute('UPDATE ai_batch SET max_requests=? WHERE id=?',(maximum,j['batch_id']))
        conn.execute('UPDATE ai_job SET state=?,error_json=NULL,updated_at=? WHERE id=?',(target,db.now(),j['id']))
        db.audit('ai_'+action,'ai_job',j['id'],{})
    return {'state':target}

def _editable_question(conn,qid):
    """Check under the same write lock used by queue dispatch and adoption."""
    value=conn.execute('SELECT * FROM ai_question WHERE id=?',(qid,)).fetchone()
    if not value:raise ApiError(404,'not_found','记录不存在')
    row=dict(value)
    job=conn.execute('SELECT * FROM ai_job WHERE id=?',(row['job_id'],)).fetchone()
    if row['adopted_version_id'] or row['state']=='adopted':
        raise ApiError(409,'adopted','已采纳的题目请在题目详情中新建版本后处理')
    if job['state'] in ('running','queued'):
        raise ApiError(409,'running','任务已排队或正在处理，请先暂停并等待当前请求返回')
    if conn.execute("SELECT count(*) FROM ai_step_run WHERE job_id=? AND state='running'",(job['id'],)).fetchone()[0]:
        raise ApiError(409,'request_running','当前 AI 请求尚未返回，请稍后重试')
    return row,dict(job)


@get('/api/ai/jobs/{jid}/pages')
def page_catalog(req):
    job=require('ai_job',req.params['jid'])
    if not job['source_file_id']:raise ApiError(400,'no_file','这项任务没有原卷文件')
    try:doc=documents.prepare(job['source_file_id'])
    except (ValueError,OSError) as exc:error(exc)
    return {'pages':[{'page':p['page'],'width':p.get('width'),'height':p.get('height'),
        'url':f'/api/ai/jobs/{job["id"]}/pages/{p["page"]}'} for p in doc['pages']]}


@get('/api/ai/jobs/{jid}/pages/{page}')
def page_image(req):
    job=require('ai_job',req.params['jid'])
    if not job['source_file_id']:raise ApiError(404,'page','原卷页面不存在')
    try:
        number=int(req.params['page']);doc=documents.prepare(job['source_file_id'])
        page=next((p for p in doc['pages'] if p['page']==number),None)
    except (ValueError,OSError) as exc:error(exc)
    if not page:raise ApiError(404,'page','原卷页面不存在')
    return 200,'image/png',Path(page['path']).read_bytes()


@post('/api/ai/questions/{qid}/regions')
def edit_regions(req):
    if req.body('confirmed') is not True:raise ApiError(400,'confirm','请核对所选区域只包含完整题面，没有答案与解析')
    regions=req.body('regions')
    if not isinstance(regions,list) or not 1<=len(regions)<=20:raise ApiError(400,'regions','请框选1至20个题面区域')
    checked=[]
    for r in regions:
        if not isinstance(r,dict) or type(r.get('page')) is not int:raise ApiError(400,'regions','区域页码无效')
        try:box=contracts.box(r.get('bbox'))
        except (provider.ProviderError,TypeError) as exc:error(exc)
        if box[2]-box[0]<.005 or box[3]-box[1]<.005:raise ApiError(400,'regions','框选区域太小，请重新选择')
        if {'page':r['page'],'bbox':box} in checked:raise ApiError(400,'regions','请删除重复的区域')
        checked.append({'page':r['page'],'bbox':box})
    with db.transaction() as conn:
        row,job=_editable_question(conn,req.params['qid'])
        if req.body('revision')!=row['current_revision']:raise ApiError(409,'changed','题目已变化，请刷新后重新框选')
    if not job['source_file_id']:raise ApiError(400,'no_file','这项任务没有原卷文件')
    try:doc=documents.prepare(job['source_file_id'])
    except (ValueError,OSError) as exc:error(exc)
    pages={p['page']:p for p in doc['pages']}
    for r in checked:
        if r['page'] not in pages:raise ApiError(400,'page','所选页码不属于这份原卷')
        if worker._answer_label(pages[r['page']],r['bbox']):raise ApiError(400,'answer_overlap','区域内检测到答案或解析标题，请缩小题面区域')
    previous=worker.unpack(row,'source_json');source=json.loads(json.dumps(previous))
    edited=req.body('source')
    if edited is not None:
        if not isinstance(edited,dict):raise ApiError(400,'source','题面信息格式无效')
        try:contracts.question(edited)
        except provider.ProviderError as exc:error(exc)
        for key in ('stem','options','subquestions','total_points'):source[key]=edited.get(key)
    reextract=req.body('reextract',True)
    if type(reextract) is not bool:raise ApiError(400,'reextract','重新识别选项无效')
    source['refined']=not reextract
    source['manual_regions']=checked;source['manual_edited']=True
    source['pages']=list(dict.fromkeys(r['page'] for r in checked))
    source.setdefault('uncertainties',[]).append('教师重新指定题面区域；重新生成的结果需人工核对')
    images=[];targets=[];committed=False
    try:
        for i,r in enumerate(checked):
            key='manual-'+uuid.uuid4().hex
            target=db.store_dir('assets')/'ai'/job['id']/row['id']/(key+'.png');targets.append(target)
            documents.crop(pages[r['page']],r['bbox'],target)
            images.append({'page':r['page'],'path':target.relative_to(db.ROOT).as_posix(),'answer_free':True,'manual_confirmed':True,'region_key':key})
        source['statement_images']=images
        with db.transaction() as conn:
            latest,_=_editable_question(conn,row['id'])
            if latest['current_revision']!=row['current_revision']:raise ApiError(409,'changed','题目已变化，请重新打开对照窗口')
            for r,image in zip(checked,images):
                conn.execute('INSERT INTO ai_evidence_region VALUES(?,?,?,?,?,?,?,?)',(db.next_id(conn,'AIE'),row['id'],image['region_key'],r['page'],db.dumps(r['bbox']),'statement',1,image['path']))
            conn.execute("UPDATE ai_question SET source_json=?,state='recognized',solve_json=NULL,review_json=NULL,error_json=NULL,quality_state='pending',current_revision=current_revision+1,request_generation=request_generation+1 WHERE id=?",(db.dumps(source),row['id']))
            db.audit('ai_manual_regions','ai_question',row['id'],{'revision':row['current_revision']+1,'regions':checked,'previous_source':previous,'confirmed_answer_free':True})
        committed=True
    except (ValueError,OSError) as exc:error(exc)
    finally:
        if not committed:
            for target in targets:target.unlink(missing_ok=True)
    return {'ok':True,'revision':row['current_revision']+1,'images':images,'queued':False}


@post('/api/ai/questions/{qid}/edit')
def edit(req):
    source=req.body('source',required=True)
    try:contracts.question(source)
    except provider.ProviderError as exc:error(exc)
    with db.transaction() as conn:
        row,job=_editable_question(conn,req.params['qid'])
        if req.body('base_revision', row['current_revision']) != row['current_revision']:
            raise ApiError(409,'question_changed','题面已变化，草稿已保留，请重新核对')
        previous=worker.unpack(row,'source_json')
        # Editing text never grants access to arbitrary local files or changes the baseline.
        for k in ('figure_paths','statement_images','pages','material_refs'):source[k]=previous.get(k,[])
        source['refined']=True;source['manual_edited']=True
        conn.execute("UPDATE ai_question SET source_json=?,state='recognized',solve_json=NULL,review_json=NULL,error_json=NULL,quality_state='pending',current_revision=current_revision+1 WHERE id=?",(db.dumps(source),row['id']))
        db.audit('ai_edit_source','ai_question',row['id'],{'new_revision':row['current_revision']+1})
    return {'ok':True,'revision':row['current_revision']+1}

@post('/api/ai/questions/{qid}/candidate')
def revise_candidate(req):
    row=require('ai_question',req.params['qid'])
    current=db.one('SELECT * FROM ai_candidate WHERE ai_question_id=? AND revision=?',(row['id'],row['current_revision']))
    if not current or row['state']!='ready':raise ApiError(409,'not_ready','只可修订已生成且未采纳的候选结果')
    if req.body('content_hash')!=current['content_hash']:raise ApiError(409,'candidate_changed','候选结果已变化，请刷新')
    c=worker.unpack(current,'content_json');solved=req.body('solve',required=True);review=req.body('review',required=True)
    try:contracts.solved(solved,c['question']);contracts.reviewed(review)
    except provider.ProviderError as exc:error(exc)
    c['solve']=solved;c['review']=review;c['quality_state']='needs_review';c['teacher_revision']=True
    with db.transaction() as conn:
        latest=conn.execute('SELECT current_revision,state FROM ai_question WHERE id=?',(row['id'],)).fetchone()
        if latest[0]!=row['current_revision'] or latest[1]!='ready':raise ApiError(409,'candidate_changed','候选结果已变化，请刷新')
        rev=latest[0]+1
        conn.execute('INSERT INTO ai_candidate(id,ai_question_id,revision,base_version_id,content_json,content_hash,input_hash,created_at) VALUES(?,?,?,?,?,?,?,?)',(db.next_id(conn,'AIC'),row['id'],rev,row['base_version_id'],db.dumps(c),provider.digest(c),current['input_hash'],db.now()))
        conn.execute('UPDATE ai_question SET current_revision=?,solve_json=?,review_json=?,quality_state=? WHERE id=?',(rev,db.dumps(solved),db.dumps(review),'needs_review',row['id']))
        db.audit('ai_revise_candidate','ai_question',row['id'],{'revision':rev})
    return {'ok':True,'revision':rev}

def _accept_parts(value):
    return adopt.accept_parts(value)


def _accept_candidate(candidate,parts):
    return adopt.accept_candidate(candidate,parts)


@post('/api/ai/candidates/{cid}/accept')
def accept(req):
    candidate=require('ai_candidate',req.params['cid'])
    if req.body('content_hash')!=candidate['content_hash']:raise ApiError(409,'candidate_changed','候选结果已变化，请刷新核对')
    parts=_accept_parts(req.body('parts',None))
    if not req.body('confirmed',False):raise ApiError(400,'confirm','请先核对候选结果并确认采纳')
    return _accept_candidate(candidate,parts)

@post('/api/ai/jobs/{jid}/accept')
def accept_batch(req):
    """Batch adoption: one transaction per question, failures reported per item."""
    job=require('ai_job',req.params['jid'])
    if not req.body('confirmed',False):raise ApiError(400,'confirm','批量采纳前须先核对，并显式确认')
    parts=_accept_parts(req.body('parts',None))
    wanted=req.body('candidate_ids',None)
    rows=db.all_("SELECT c.* FROM ai_candidate c JOIN ai_question q ON q.id=c.ai_question_id AND q.current_revision=c.revision "
                 "WHERE q.job_id=? AND q.state='ready' ORDER BY CAST(q.original_qno AS INTEGER),q.id",(job['id'],))
    if wanted is not None:
        if not isinstance(wanted,list) or not wanted or len(wanted)>200:
            raise ApiError(400,'candidates','请提供1至200个候选编号')
        if any(not isinstance(value,str) or not value.strip() for value in wanted):
            raise ApiError(400,'candidates','候选编号必须是非空文本')
        known={r['id'] for r in rows};unknown=[str(x) for x in wanted if x not in known]
        if unknown:raise ApiError(409,'not_ready','这些候选当前不可采纳：'+'、'.join(unknown[:10]))
        keep=set(wanted);rows=[r for r in rows if r['id'] in keep]
    if not rows:raise ApiError(409,'nothing_to_accept','本任务没有待采纳的候选结果')
    items=[]
    for candidate in rows:
        try:
            result=_accept_candidate(candidate,parts)
            items.append({'candidate_id':candidate['id'],'ai_question_id':candidate['ai_question_id'],'ok':True,**result})
        except ApiError as exc:
            items.append({'candidate_id':candidate['id'],'ai_question_id':candidate['ai_question_id'],'ok':False,
                          'code':exc.code,'message':exc.message})
    accepted=sum(1 for x in items if x['ok'])
    with db.transaction() as conn:
        db.audit('ai_accept_batch','ai_job',job['id'],{'accepted':accepted,'candidate_ids':[x['candidate_id'] for x in items if x['ok']]})
    return {'job_id':job['id'],'accepted':accepted,'failed':len(items)-accepted,'items':items}

@post('/api/ai/candidates/{cid}/reject')
def reject_candidate(req):
    candidate=require('ai_candidate',req.params['cid'])
    reason=str(req.body('reason') or '').strip()
    if not 3<=len(reason)<=500:raise ApiError(400,'reason','请填写3至500字的舍弃理由')
    key='reject:'+candidate['id']
    with db.transaction() as conn:
        if conn.execute('SELECT 1 FROM ai_decision WHERE adoption_key=?',(key,)).fetchone():
            return {'rejected':True,'idempotent':True}
        row=dict(conn.execute('SELECT * FROM ai_question WHERE id=?',(candidate['ai_question_id'],)).fetchone())
        if row['current_revision']!=candidate['revision']:raise ApiError(409,'candidate_changed','候选结果已变化，请刷新后再舍弃')
        if row['state']=='adopted':raise ApiError(409,'adopted','已采纳的结果不能舍弃，请在题目详情中处理')
        conn.execute('INSERT INTO ai_decision VALUES(?,?,?,?,?,?,?,?,?)',
          (db.next_id(conn,'AID'),candidate['id'],candidate['content_hash'],'reject',db.dumps({'reason':reason}),'教师',None,key,db.now()))
        conn.execute("UPDATE ai_question SET state='rejected',error_json=NULL WHERE id=?",(row['id'],))
        db.audit('ai_reject','ai_question',row['id'],{'candidate_id':candidate['id']})
    return {'rejected':True,'idempotent':False}

@post('/api/ai/questions/{qid}/retry')
def retry_question(req):
    """Re-run one question only; a new revision keeps the old candidate intact."""
    with db.transaction() as conn:
        row,job=_editable_question(conn,req.params['qid'])
        revision=row['current_revision']+1
        conn.execute("UPDATE ai_question SET state='recognized',solve_json=NULL,review_json=NULL,error_json=NULL,quality_state='pending',current_revision=?,request_generation=request_generation+1 WHERE id=?",(revision,row['id']))
        conn.execute("UPDATE ai_job SET state='queued',error_json=NULL,updated_at=? WHERE id=?",(db.now(),job['id']))
        db.audit('ai_retry_question','ai_question',row['id'],{'revision':revision})
    return {'queued':True,'revision':revision}

@get('/api/ai/jobs/{jid}/bundle')
def bundle(req):
    j=require('ai_job',req.params['jid'])
    candidates=db.all_('SELECT c.* FROM ai_candidate c JOIN ai_question q ON q.id=c.ai_question_id AND q.current_revision=c.revision WHERE q.job_id=? ORDER BY c.ai_question_id',(j['id'],))
    original_content=[worker.unpack(c,'content_json') for c in candidates]
    from app.ai.export import normalized_content
    try:content=normalized_content(original_content)
    except provider.ProviderError as exc:error(exc)
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('results.json',json.dumps({'job':_job(j),'candidates':original_content},ensure_ascii=False,indent=2))
        lines=['% AI候选题，未经人工数学审校。题号对应原卷。']
        paths=set()
        paper=identity.for_job(j)
        from app.provenance import label
        for c in content:
            q=c['question'];lines+=['\n% '+label(paper.get('name') or j['title'],q['qno']),q['stem']]
            for o in q.get('options') or []:lines.append(o['key']+'. '+o['text'])
            for s in q.get('subquestions') or []:lines.append('('+s['key']+') '+s['text'])
            lines+=['\n% AI答案',c['solve']['answer'],'\n% AI解析']
            for s in c['solve'].get('solutions',[]):lines.append(s['steps'])
            paths.update(c['source'].get('figure_paths',[]))
        z.writestr('questions.tex','\n'.join(lines))
        from app.ai.export import tex_files
        from app.latex import exbook
        for name,value in tex_files(j,content).items():z.writestr(name,value)
        for p in exbook.TEMPLATE_DIR.rglob('*'):
            if p.is_file() and (p.name=='ExBook.cls' or 'img' in p.relative_to(exbook.TEMPLATE_DIR).parts):
                z.write(p,p.relative_to(exbook.TEMPLATE_DIR).as_posix())
        z.writestr('说明.txt','results.json保存题面、原卷参考、独立解答、复核、标签与评分。questions.tex为可嵌入讲义的题目片段（需题库qbankimage命令），不是独立文档。所有结果待教师审校。费用为按用量估算。')
        for rel in paths:
            p=(db.ROOT/rel).resolve()
            if p.is_relative_to((db.ROOT/'assets').resolve()) and p.is_file():z.write(p,rel)
    return 200,'application/zip',output.getvalue()
