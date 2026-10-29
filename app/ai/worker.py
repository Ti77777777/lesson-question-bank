"""Persistent single-worker queue. A request boundary is the pause/recovery boundary."""
import hashlib
import json
import os
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from app import db
from app.ai import adopt, contracts, documents, prompts, provider, layout, identity

_started=False
_start_lock=threading.Lock()

def unpack(row, field, default=None):
    value=row.get(field)
    return json.loads(value) if value else ({} if default is None else default)

def init():
    db.db().executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
    db._ensure_column('ai_question','request_generation','INTEGER NOT NULL DEFAULT 0')
    db.db().execute("INSERT INTO meta(key,value) VALUES('ai_schema_version','1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    db.db().commit()

def active(job_id):
    j=db.one('SELECT state FROM ai_job WHERE id=?',(job_id,))
    if not j or j['state']!='running':raise InterruptedError()

def progress(job_id, stage, **values):
    with db.transaction() as conn:
        conn.execute('UPDATE ai_job SET stage=?,progress_json=?,updated_at=? WHERE id=?',
                     (stage,db.dumps(values),db.now(),job_id))

def _overlap(a,b):
    return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])

def _answer_label(page,bbox):
    words=[]
    for w in page.get('words',[]):
        b=w['bbox'];x=(b[0]+b[2])/2;y=(b[1]+b[3])/2
        if bbox[0]<=x<=bbox[2] and bbox[1]<=y<=bbox[3]:words.append(w['text'])
    return any(layout.answer_label(w) for w in words) or layout.answer_label(''.join(words))

def merge_continuation(source,item):
    """Preserve statement fragments as well as the reference on later pages."""
    if item.get('stem'):source['stem']+='\n'+item['stem']
    options={o['key']:o for o in source.get('options',[])}
    options.update({o['key']:o for o in item.get('options',[])})
    source['options']=sorted(options.values(),key=lambda o:o['key'])
    subquestions={s['key']:dict(s) for s in source.get('subquestions',[])}
    for sub in item.get('subquestions',[]):
        old=subquestions.get(sub['key'])
        if old:
            old['text']+='\n'+sub['text']
            old['depends_on']=list(dict.fromkeys(old.get('depends_on',[])+sub.get('depends_on',[])))
        else:subquestions[sub['key']]=dict(sub)
    source['subquestions']=list(subquestions.values())
    for k in ('answer','analysis'):
        addition=item.get('reference',{}).get(k,'')
        if addition:source['reference'][k]=(source['reference'].get(k,'')+'\n'+addition).strip()
    source.setdefault('uncertainties',[]).extend(item.get('uncertainties',[]))

def _region(job,qid,page,r,role,key,reference_regions):
    bbox=contracts.box(r['bbox'])
    clean=role=='statement' and not any(_overlap(bbox,x['bbox']) for x in reference_regions) and not _answer_label(page,bbox)
    if role=='figure' and (any(_overlap(bbox,x['bbox']) for x in reference_regions) or _answer_label(page,bbox)):
        raise provider.ProviderError('reference_overlap','题图区域与原卷解答重叠，需要人工调整题面')
    target=db.store_dir('assets')/'ai'/job['id']/qid/(key+'.png')
    target.parent.mkdir(parents=True,exist_ok=True)
    documents.crop(page,bbox,target)
    relative=target.relative_to(db.ROOT).as_posix()
    with db.transaction() as conn:
        conn.execute('INSERT OR REPLACE INTO ai_evidence_region(id,ai_question_id,region_key,page_no,bbox_json,role,answer_free,rel_path) VALUES(?,?,?,?,?,?,?,?)',
          (db.next_id(conn,'AIE'),qid,key,page['page'],db.dumps(bbox),role,int(clean),relative))
    return relative,clean

def recognize(job):
    doc=documents.prepare(job['source_file_id'])
    identity.apply(job['source_file_id'],identity.extract(doc['pages'][0].get('text','')))
    config=unpack(db.one('SELECT config_json FROM ai_batch WHERE id=?',(job['batch_id'],)),'config_json')
    selected=config.get('pages') or [p['page'] for p in doc['pages']]
    for page in doc['pages']:
        if page['page'] not in selected:continue
        active(job['id']);progress(job['id'],'recognize',page=page['page'],pages=len(doc['pages']))
        result=provider.call(job['id'],None,'recognize:'+str(page['page']),prompts.SYSTEM,
          prompts.RECOGNIZE+'\n当前页码：'+str(page['page'])+'\n文字层（可能有错，不替代图像）：\n'+page.get('text','')[:16000],
          [page['path']],max_tokens=16000,thinking=False)
        if page['page']==1:
            identity.apply(job['source_file_id'],identity.extract(page.get('text',''),result.get('title','')))
        reference_page=bool(re.search(r'参考答案|答案[与及和]?解析|参考解答|数学答案',str(result.get('title') or '')))
        entries=contracts.recognition_questions(result)
        reference_page=reference_page or bool(entries and all(q.get('reference_regions') and not q.get('statement_regions') for q in entries))
        reference_page=reference_page or page['page'] in unpack(job,'document_json').get('reference_pages',[])
        # A page beginning with an unnumbered continuation is linked only to the
        # immediately preceding question; retain that inference for teacher review.
        for item in contracts.recognition_questions(result):
            number=item.get('qno')
            missing_number=not number or (isinstance(number,str) and number.strip().lower() in
                ('待定','未知','不详','未识别','无法识别','未确定','unknown','n/a','?','？'))
            if item.get('continuation') and missing_number:
                previous=[]
                for old in db.all_('SELECT * FROM ai_question WHERE job_id=? ORDER BY id',(job['id'],)):
                    src=unpack(old,'source_json')
                    # On a resumed job the same question may already include
                    # this continuation page (or later ones). Link by presence
                    # on the preceding page, not by the last recorded page.
                    if page['page']-1 in src.get('pages',[]):previous.append(old)
                if previous:
                    item['qno']=previous[-1]['original_qno'];item['qtype']=previous[-1]['qtype']
                    uncertainties=item.setdefault('uncertainties',[])
                    contracts.items(uncertainties,'uncertainties',39)
                    uncertainties.append('本页题号缺失或待定，参考解答按相邻页面衔接，待教师核对')
        if reference_page:
            for item in contracts.recognition_questions(result):
                # A reference-only entry need not repeat the original stem/options.
                # Link only a numbered item whose original statement already exists.
                if item.get('reference_regions'):
                    original=db.one('SELECT source_json FROM ai_question WHERE job_id=? AND question_key=?',(job['id'],str(item.get('qno','')).strip()))
                    if original and unpack(original,'source_json').get('statement_images'):
                        # Only reference fields on an identified answer page are used;
                        # incomplete or guessed reprints must not replace the question.
                        item.update(continuation=True,stem='',options=[],subquestions=[],figures=[],statement_regions=[])
        contracts.recognition(result)
        located=layout.locate(page,result['questions'])
        for item in result['questions']:
            active(job['id'])
            key=item['qno'].strip()
            existing=db.one('SELECT * FROM ai_question WHERE job_id=? AND question_key=?',(job['id'],key))
            if existing:
                source=unpack(existing,'source_json');qid=existing['id']
                if page['page'] in source.get('pages',[]):continue
                if reference_page and item.get('reference_regions') and source.get('statement_images'):
                    # Answer sheets often reprint the original statement and restart
                    # its number. Never append those regions/text to blind input.
                    for field in ('answer','analysis'):
                        addition=(item.get('reference') or {}).get(field,'')
                        if addition:source.setdefault('reference',{})[field]=(source.get('reference',{}).get(field,'')+'\n'+addition).strip()
                    source.setdefault('reference_areas',[]).extend({'page':page['page'],**r} for r in item['reference_regions'])
                    source['pages'].append(page['page'])
                    source.setdefault('uncertainties',[]).append('参考答案页复印题号已按既有题目衔接，题面未从答案页重录；待核对参考解答。')
                    with db.transaction() as conn:
                        conn.execute('UPDATE ai_question SET source_json=? WHERE id=?',(db.dumps(source),qid))
                    continue
                if source.get('rebuild_source'):
                    source=dict(item);source.update(pages=[],statement_images=[],figure_paths=[])
                # Only merge explicit continuation; duplicate question numbers are a human conflict.
                elif not item.get('continuation'):
                    raise provider.ProviderError('duplicate_qno','同一文件题号重复：'+key+'。请分卷上传或指定页范围。')
                else:
                    merge_continuation(source,item)
            else:
                if item.get('continuation') and not item.get('stem'):
                    raise provider.ProviderError('orphan_continuation','选定页面含前页解答，但缺少原题：'+key)
                source=dict(item);source.update(pages=[],statement_images=[],figure_paths=[])
                with db.transaction() as conn:
                    qid=db.next_id(conn,'AIQ')
                    conn.execute('INSERT INTO ai_question(id,job_id,question_key,original_qno,qtype,state,source_json) VALUES(?,?,?,?,?,?,?)',
                      (qid,job['id'],key,key,item['qtype'],'recognized',db.dumps(source)))
            if key in located:
                item.update(located[key]);source['layout_method']='pdf_glyph_anchors'
            refs=item.get('reference_regions',[])
            source.setdefault('reference_areas',[]).extend({'page':page['page'],**r} for r in refs)
            for i,r in enumerate(item.get('statement_regions',[])):
                rev=existing['current_revision'] if existing else 1
                rel,clean=_region(job,qid,page,r,'statement',f'r{rev}-p{page["page"]}-s{i}',refs)
                source['statement_images'].append({'path':rel,'answer_free':clean,'page':page['page']})
            # Figure positions are refined from the isolated question crop below.
            source['pages'].append(page['page'])
            with db.transaction() as conn:
                conn.execute('UPDATE ai_question SET source_json=? WHERE id=?',(db.dumps(source),qid))
    if not db.scalar('SELECT count(*) FROM ai_question WHERE job_id=?',(job['id'],)):
        raise provider.ProviderError('no_questions','未识别到题目，请检查页范围或源文件')
    # Do not persist text/word arrays on API rows; they remain in the document cache.
    with db.transaction() as conn:
        document=unpack(db.one('SELECT document_json FROM ai_job WHERE id=?',(job['id'],)),'document_json')
        document.update(prepared=True,pages=len(doc['pages']),sha256=doc['sha256'])
        conn.execute('UPDATE ai_job SET document_json=? WHERE id=?',
          (db.dumps(document),job['id']))

def statement(source):
    return {k:source.get(k) for k in ('qno','qtype','stem','options','subquestions','total_points')}

def embed_figure(source,token,relative):
    command='\\qbankimage{'+relative+'}'
    found=False
    for obj,field in [(source,'stem')]+[(o,'text') for o in source.get('options',[])]+[(q,'text') for q in source.get('subquestions',[])]:
        text=obj.get(field,'')
        if token in text:
            obj[field]=text.replace(token,command);found=True
    if not found:source['stem']+='\n'+command


def unresolved_figures(source):
    return any('[[FIGURE:' in str(value) for value in [source.get('stem','')]+[o.get('text','') for o in source.get('options',[])]+[q.get('text','') for q in source.get('subquestions',[])])


def current_revision(conn,row):
    current=conn.execute('SELECT current_revision,state FROM ai_question WHERE id=?',(row['id'],)).fetchone()
    if not current or current['current_revision']!=row['current_revision'] or current['state'] in ('adopted','rejected'):
        raise InterruptedError()
    return current['current_revision']

def repair_statement_regions(job,row,source):
    """Repair contaminated crops only with a PDF number box and answer boundary."""
    bad_pages={x['page'] for x in source.get('statement_images',[]) if not x.get('answer_free')}
    if not bad_pages or not job.get('source_file_id') or row.get('base_version_id'):return source
    doc=documents.prepare(job['source_file_id'])
    replacements={}
    for page in doc['pages']:
        if page['page'] not in bad_pages:continue
        hint={**source,'qno':row['original_qno'],'continuation':False}
        found=layout.locate(page,[hint]).get(row['original_qno'])
        if not found or found['method']!='pdf_boxed_number_anchors' or not found['reference_regions']:
            return source
        replacements[page['page']]=(page,found)
    if set(replacements)!=bad_pages:return source
    images=[];references=[]
    for page_no,(page,found) in replacements.items():
        refs=found['reference_regions']
        references.extend({'page':page_no,**r} for r in refs)
        for i,region in enumerate(found['statement_regions']):
            rel,clean=_region(job,row['id'],page,region,'statement',f'r{row["current_revision"]}-layout1-p{page_no}-s{i}',refs)
            if not clean:return source
            images.append({'path':rel,'answer_free':True,'page':page_no})
    repaired=dict(source)
    repaired['statement_images']=[x for x in source['statement_images'] if x['page'] not in bad_pages]+images
    repaired['reference_areas']=[x for x in source.get('reference_areas',[]) if x['page'] not in bad_pages]+references
    repaired['layout_method']='pdf_boxed_number_anchors'
    repaired['layout_repaired']=True
    note='题面已依据 PDF 题号框和答案标题重新分隔，待教师核对'
    repaired['uncertainties']=list(dict.fromkeys(source.get('uncertainties',[])+[note]))
    with db.transaction() as conn:
        current_revision(conn,row)
        conn.execute('UPDATE ai_question SET source_json=? WHERE id=?',(db.dumps(repaired),row['id']))
        db.audit('ai_repair_statement','ai_question',row['id'],{
            'pages':sorted(bad_pages),'previous_images':source['statement_images'],
            'previous_reference_areas':source.get('reference_areas',[]),'new_images':images,
            'method':'pdf_boxed_number_anchors'})
    return repaired

def process_question(job,row):
    source=provider.normalise(unpack(row,'source_json'))
    active(job['id']);progress(job['id'],'solve',qno=row['original_qno'])
    source=repair_statement_regions(job,row,source)
    clean=[db.ROOT/x['path'] for x in source.get('statement_images',[]) if x['answer_free']]
    if source.get('statement_images') and len(clean)!=len(source['statement_images']):
        raise provider.ProviderError('blind_input_unavailable','题面截图包含答案或与解析重叠，需要人工重新指定题面区域')
    if not row['base_version_id'] and not clean:
        raise provider.ProviderError('blind_input_unavailable','未获得独立题面截图，需要人工补充题面')
    if not row['base_version_id'] and not source.get('refined'):
        active(job['id']);progress(job['id'],'refine',qno=row['original_qno'])
        extract=provider.call(job['id'],row['id'],'refine',prompts.SYSTEM,prompts.RECOGNIZE+
          '\n附件是已经分离的同一道题题面，按附件精确重录这一题。原题号为 '+row['original_qno']+
          '，整卷章节确定的题型为 '+source['qtype']+
          '。图中没有参考答案，不要补写reference。坐标相对这张题面截图。仅返回这一题。',clean,max_tokens=8000,thinking=False)
        contracts.recognition(extract)
        if len(extract['questions'])!=1 or extract['questions'][0]['qno']!=row['original_qno']:
            raise provider.ProviderError('question_identity','单题重录与原卷题号不一致，请检查题面分割')
        refined=extract['questions'][0]
        # A crop omits the single/multiple-choice section heading. Preserve that
        # metadata from the full-page pass instead of guessing it again.
        refined['qtype']=source['qtype']
        # Section headings also supply points that are absent from isolated crops.
        if source.get('total_points') is not None and source['total_points']>0:
            refined['total_points']=source['total_points']
        for k in ('stem','qtype','options','subquestions','total_points'):source[k]=refined.get(k,[] if k in ('options','subquestions') else None)
        source.setdefault('uncertainties',[]).extend(refined.get('uncertainties',[]))
        source['figure_paths']=[]
        if refined.get('figures'):
            if len(clean)!=1:raise provider.ProviderError('figure_page','跨页题图需人工定位')
            for i,r in enumerate(refined['figures']):
                rel,_=_region(job,row['id'],{'path':str(clean[0]),'page':source['pages'][0],'words':[]},r,'figure',f'r{row["current_revision"]}-refined-f{i}',[])
                token='[[FIGURE:'+r.get('key','f'+str(i+1))+']]'
                embed_figure(source,token,rel)
                source['figure_paths'].append(rel)
        if unresolved_figures(source):raise provider.ProviderError('figure_missing','题图未完成定位，需要人工补充')
        source['refined']=True
        with db.transaction() as conn:
            current_revision(conn,row)
            conn.execute('UPDATE ai_question SET source_json=?,qtype=? WHERE id=?',(db.dumps(source),source['qtype'],row['id']))
    # Existing question versions may include subquestion answers; remove all of them.
    blind=statement(source)
    blind['subquestions']=[{k:s[k] for k in ('key','text','depends_on') if k in s} for s in source.get('subquestions',[])]
    if row.get('solve_json'):
        solved=provider.normalise(unpack(row,'solve_json'))
    else:
        solved=provider.call(job['id'],row['id'],'solve',prompts.SYSTEM,prompts.SOLVE+'\n'+json.dumps(blind,ensure_ascii=False),clean or [db.ROOT/p for p in source.get('figure_paths',[])],max_tokens=14000)
        # Option keys have no meaning for a fill-in or written-response item.
        # Some model responses repeat the numeric answer here; retain the full
        # answer and raw receipt, but canonicalize this unused transport field.
        if source['qtype'] in ('blank','solve'):solved['answer_keys']=[]
        contracts.solved(solved,source)
        with db.transaction() as conn:
            current_revision(conn,row)
            conn.execute("UPDATE ai_question SET solve_json=?,state='solved',error_json=NULL WHERE id=?",(db.dumps(solved),row['id']))
    active(job['id']);progress(job['id'],'review',qno=row['original_qno'])
    config=unpack(db.one('SELECT config_json FROM ai_batch WHERE id=?',(job['batch_id'],)),'config_json')
    review=provider.call(job['id'],row['id'],'review',prompts.SYSTEM,prompts.REVIEW+'\n'+json.dumps({
       'question':blind,'independent_solution':solved,'original_reference':source.get('reference',{}),
       'recognition_uncertainties':source.get('uncertainties',[]),'audience':config.get('audience','高一学生'),
       'available_tag_catalog':db.all_("SELECT dimension,name FROM tag WHERE status='active' LIMIT 500")},ensure_ascii=False),
       clean or [db.ROOT/p for p in source.get('figure_paths',[])],max_tokens=16000 if source['qtype']=='solve' else 10000)
    contracts.reviewed(review)
    quality=review['verdict'] if solved['status']=='solved' else 'needs_input'
    if source.get('uncertainties') and quality=='no_issue_found':quality='needs_review'
    evidence_paths=source.get('figure_paths',[])+[x['path'] for x in source.get('statement_images',[])]
    evidence_hashes={p:hashlib.sha256((db.ROOT/p).read_bytes()).hexdigest() for p in evidence_paths}
    content={'question':statement(source),'solve':solved,'review':review,'source':source,'evidence_hashes':evidence_hashes,
             'quality_state':quality,'manual_review':'pending','blind_input':'question_only',
             'provider':provider.settings.for_job(job['id'])['model'],'prompt_version':provider.PROMPT_VERSION}
    with db.transaction() as conn:
        revision=current_revision(conn,row)
        if not conn.execute('SELECT 1 FROM ai_candidate WHERE ai_question_id=? AND revision=?',(row['id'],revision)).fetchone():
            cid=db.next_id(conn,'AIC')
            conn.execute('INSERT INTO ai_candidate(id,ai_question_id,revision,base_version_id,content_json,content_hash,input_hash,created_at) VALUES(?,?,?,?,?,?,?,?)',
              (cid,row['id'],revision,row['base_version_id'],db.dumps(content),provider.digest(content),provider.digest({'question':blind,'evidence':evidence_hashes}),db.now()))
        conn.execute("UPDATE ai_question SET state='ready',quality_state=?,review_json=?,error_json=NULL WHERE id=?",(quality,db.dumps(review),row['id']))

def run_job(job):
    try:
        if job['source_file_id'] and not unpack(job,'document_json').get('prepared'):recognize(job)
        rows=db.all_("SELECT * FROM ai_question WHERE job_id=? AND state NOT IN ('ready','adopted','rejected') ORDER BY CAST(original_qno AS INTEGER),id",(job['id'],))
        def process(row):
            active(job['id'])
            try:
                if row['state']=='failed':
                    with db.transaction() as conn:
                        current_revision(conn,row)
                        conn.execute('UPDATE ai_question SET state=?,error_json=NULL WHERE id=?',
                                     ('solved' if row.get('solve_json') else 'recognized',row['id']))
                process_question(job,row)
            except (InterruptedError,provider.BudgetPause):raise
            except provider.ProviderError as exc:
                with db.transaction() as conn:
                    conn.execute("UPDATE ai_question SET state='failed',error_json=? WHERE id=? AND current_revision=?",(db.dumps({'code':exc.code,'message':str(exc)}),row['id'],row['current_revision']))
                if exc.code in ('key_missing','http_401','http_402','http_403','http_404','http_429') or exc.code in provider.CONNECTION_ERRORS:raise
            finally:db.close()
        # Two independent questions may run concurrently. SQLite reservations
        # serialize their budget checks, so in-flight requests count immediately.
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='ai-question') as executor:
            futures=[executor.submit(process,row) for row in rows]
            for future in as_completed(futures):
                try:future.result()
                except (provider.ProviderError,InterruptedError) as exc:
                    with db.transaction() as conn:
                        if isinstance(exc,provider.ProviderError):
                            conn.execute('UPDATE ai_job SET state=?,error_json=?,updated_at=? WHERE id=? AND state=\'running\'',('budget_paused' if isinstance(exc,provider.BudgetPause) else 'failed',db.dumps({'code':exc.code,'message':str(exc)}),db.now(),job['id']))
                    for other in futures:other.cancel()
                    raise
        active(job['id'])
        failed=db.scalar("SELECT count(*) FROM ai_question WHERE job_id=? AND state='failed'",(job['id'],))
        with db.transaction() as conn:
            state=conn.execute('SELECT state FROM ai_job WHERE id=?',(job['id'],)).fetchone()
            if not state or state[0]!='running':raise InterruptedError()
            conn.execute('UPDATE ai_job SET state=?,stage=?,updated_at=?,error_json=NULL WHERE id=?',('partial' if failed else 'completed','done',db.now(),job['id']))
        # 自复核未发现问题的题直接入库；需重点复核的题留在工作台等教师处理。
        adopt.auto_adopt(job)
    except InterruptedError:pass
    except provider.ProviderError as exc:
        with db.transaction() as conn:
            conn.execute('UPDATE ai_job SET state=?,error_json=?,updated_at=? WHERE id=? AND state=\'running\'',
              ('budget_paused' if isinstance(exc,provider.BudgetPause) else 'failed',db.dumps({'code':exc.code,'message':str(exc)}),db.now(),job['id']))
    except Exception as exc:
        # 只记录异常类型和代码位置，不保存异常文本、局部变量或请求内容。
        frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(exc.__traceback__)]
        with db.transaction() as conn:
            conn.execute("UPDATE ai_job SET state='failed',error_json=?,updated_at=? WHERE id=? AND state='running'",(db.dumps({'code':'processing_error','message':'本地文档处理失败：'+type(exc).__name__,'trace':frames}),db.now(),job['id']))

def _loop():
    # Hold an OS lock for the lifetime of the queue. Multiple UI servers may use
    # one database; only one may dispatch paid provider calls.
    lockpath=db.DB_PATH.with_suffix('.ai.lock')
    handle=lockpath.open('a+b')
    if handle.seek(0,2)==0:handle.write(b'0');handle.flush()
    handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close();return
    with db.transaction() as conn:
        conn.execute("UPDATE ai_step_run SET state='failed',billing_state='outcome_unknown',error_json=? WHERE state='running'",(db.dumps({'code':'interrupted','message':'上次请求被中断；费用结果未知'}),))
        conn.execute("UPDATE ai_job SET state='paused',error_json=? WHERE state='running'",(db.dumps({'code':'interrupted','message':'服务重启后已暂停，确认后可继续处理'}),))
    while True:
        try:
            with db.transaction() as conn:
                row=conn.execute("SELECT * FROM ai_job WHERE state='queued' ORDER BY created_at,id LIMIT 1").fetchone()
                if row:
                    conn.execute("UPDATE ai_job SET state='running',error_json=NULL,updated_at=? WHERE id=?",(db.now(),row['id']))
                    job=dict(row)
                else:job=None
            if job:run_job(job)
        except Exception:pass  # Queue survives bad files; job-level errors are recorded above.
        finally:db.close()
        time.sleep(1)

def start():
    global _started
    with _start_lock:
        if _started or os.environ.get('QBANK_AI_WORKER')=='0':return
        _started=True
        threading.Thread(target=_loop,name='qbank-ai',daemon=True).start()
