"""AI integration acceptance with a deterministic provider; never calls a paid API."""
import base64
import copy
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.environ['QBANK_DB']=str(ROOT/'build/_test_ai.db')
os.environ['QBANK_AI_WORKER']='0'
for p in (Path(os.environ['QBANK_DB']),Path(os.environ['QBANK_DB']+'-wal'),Path(os.environ['QBANK_DB']+'-shm')):
    if p.exists():p.unlink()
from app import db
from app.server import Handler,create_app
from app.ai import provider,worker,contracts,credentials,layout

SOLVE={'status':'solved','answer':'$4$','answer_keys':[],
  'solutions':[{'label':'解法一','approach':'代入定义','steps':'$2+2=4$','conditions':'实数范围','teacher_tip':'检查结果'}],
  'subanswers':[],'uncertainties':[]}
REVIEW={'verdict':'no_issue_found','reason':'逐步核对运算结果','reference_comparison':'原卷答案正确',
 'tags':[{'dimension':'knowledge','name':'实数运算','reason':'加法计算'}],
 'difficulty':{'calc':1,'think':1,'scope':1,'level':'高一','note':'基础运算'},
 'rec_level':'B','rec_reason':'基础巩固','time_solve_min':1,'teaching':{'key_step':'计算'}}

class AITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app();cls.httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.base='http://127.0.0.1:'+str(cls.httpd.server_address[1])
        threading.Thread(target=cls.httpd.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):cls.httpd.shutdown();cls.httpd.server_close();db.close()

    def call(self,path,data=None,expect=200,headers=None):
        if isinstance(data,dict):data=json.dumps(data).encode();headers={'Content-Type':'application/json',**(headers or {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base+path,data=data,headers=headers or {}),timeout=15) as r:status=r.status;raw=r.read()
        except urllib.error.HTTPError as e:status=e.code;raw=e.read()
        self.assertEqual(status,expect,raw[:500])
        try:return json.loads(raw)
        except ValueError:return raw

    def question_job(self):
        q=self.call('/api/questions',{'qtype':'blank','stem':'计算 $2+2$。','answer':'SECRET_REFERENCE_SENTINEL','occurrence':{'pending':1}})['question']['id']
        job=self.call('/api/ai/jobs',{'question_ids':[q],'budget_cny':2,'auto_accept':False})['job_ids'][0]
        with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(job,))
        prompts=[]
        def fake(j,qid,stage,system,prompt,images=(),**kw):
            prompts.append((stage,prompt));return copy.deepcopy(SOLVE if stage=='solve' else REVIEW)
        with patch.object(provider,'call',side_effect=fake):worker.run_job(db.one('SELECT * FROM ai_job WHERE id=?',(job,)))
        d=self.call('/api/ai/jobs/'+job)
        return q,job,d,prompts

    def test_01_pipeline_blind_review_and_version_adoption(self):
        q,j,d,prompts=self.question_job()
        self.assertEqual(d['job']['state'],'completed')
        self.assertNotIn('SECRET_REFERENCE_SENTINEL',next(p for s,p in prompts if s=='solve'))
        self.assertIn('SECRET_REFERENCE_SENTINEL',next(p for s,p in prompts if s=='review'))
        c=d['questions'][0]['candidate'];vid=db.one('SELECT current_version_id FROM question WHERE id=?',(q,))['current_version_id']
        data={'content_hash':c['content_hash'],'confirmed':True,'parts':['solution','tags','rating']}
        adopted=self.call('/api/ai/candidates/'+c['id']+'/accept',data)
        self.assertNotEqual(adopted['version_id'],vid)
        self.assertEqual(db.one('SELECT answer FROM question_version WHERE id=?',(vid,))['answer'],'SECRET_REFERENCE_SENTINEL')
        self.assertEqual(db.scalar('SELECT count(*) FROM review'),0)
        self.assertTrue(self.call('/api/ai/candidates/'+c['id']+'/accept',data)['idempotent'])
        self.assertEqual(db.scalar('SELECT count(*) FROM question_version WHERE question_id=?',(q,)),2)
        self.assertEqual(db.scalar('SELECT count(*) FROM question_tag WHERE question_id=?',(q,)),1)

    def test_import_actor_is_recorded_without_human_review_approval(self):
        from app.ai import adopt
        _,jid,detail,_=self.question_job();candidate=detail['questions'][0]['candidate']
        result=adopt.accept_candidate(db.one('SELECT * FROM ai_candidate WHERE id=?',(candidate['id'],)),list(adopt.PARTS),actor='资料整理助手')
        version=db.one('SELECT * FROM question_version WHERE id=?',(result['version_id'],))
        self.assertEqual(version['created_by'],'资料整理助手采纳AI候选')
        self.assertEqual(version['review_state'],'draft')
        self.assertEqual(version['teaching']['ai_math_review'],'pending')
        self.assertEqual(db.scalar('SELECT operator FROM ai_decision WHERE candidate_id=?',(candidate['id'],)),'资料整理助手')

    def test_02_candidate_revision_and_version_conflict(self):
        q,j,d,_=self.question_job();c=d['questions'][0]['candidate'];aq=d['questions'][0]['id']
        self.call('/api/ai/questions/'+aq+'/candidate',{'content_hash':c['content_hash'],'solve':SOLVE,'review':REVIEW})
        self.call('/api/ai/candidates/'+c['id']+'/accept',{'confirmed':True,'content_hash':c['content_hash']},409)
        self.call('/api/questions/'+q+'/versions',{'stem':'计算 $2+3$。'})
        c=self.call('/api/ai/jobs/'+j)['questions'][0]['candidate']
        self.call('/api/ai/candidates/'+c['id']+'/accept',{'confirmed':True,'content_hash':c['content_hash']},409)

    def test_source_editor_rejects_stale_revision_without_changing_source(self):
        _,jid,detail,_=self.question_job();question=detail['questions'][0]
        body={'source':copy.deepcopy(question['source']),'base_revision':question['current_revision']}
        body['source']['stem']='计算 $2+2$，写出结果。'
        self.call('/api/ai/questions/'+question['id']+'/edit',body)
        body['source']['stem']='旧页面的错误覆盖'
        self.call('/api/ai/questions/'+question['id']+'/edit',body,409)
        latest=self.call('/api/ai/jobs/'+jid)['questions'][0]
        self.assertEqual(latest['source']['stem'],'计算 $2+2$，写出结果。')
        self.assertEqual(latest['state'],'recognized')
        self.assertIsNone(latest['candidate'])

    def test_03_upload_security_and_original_preservation(self):
        raw=b'%PDF-1.4\n%test_original_bytes'
        f=self.call('/api/ai/files',raw,headers={'X-Filename':'sample.pdf'})['file']
        self.assertEqual((ROOT/f['rel_path']).read_bytes(),raw)
        self.assertTrue(self.call('/api/ai/files',raw,headers={'X-Filename':'copy.pdf'})['duplicate'])
        self.call('/api/ai/files',b'MZfake',400,{'X-Filename':'bad.pdf'})
        for path in ('/files/data/qbank.db','/files/data/private/deepseek.dpapi','/files/app/server.py','/files/assets/../../data/qbank.db'):
            self.call(path,expect=404)
        self.call('/api/ai/probe',{},403,{'Origin':'https://evil.example'})
        self.call('/api/ai/files',b'x',413,{'Content-Length':str(51*1024*1024),'X-Filename':'too-big.pdf'})

    def test_04_contracts_reject_untrusted_tex_and_invalid_answers(self):
        for source in (r'\input{data/private/x}',r'\write18{whoami}',r'\csname input\endcsname',r'^^5cinput{x}'):
            with self.assertRaises(provider.ProviderError):contracts.text(source,'stem')
        with self.assertRaises(provider.ProviderError):contracts.box([0,.2,1,1.1])
        s={**SOLVE,'answer_keys':['E']}
        with self.assertRaises(provider.ProviderError):contracts.solved(s,{'qtype':'single','options':[{'key':'A','text':'a'}]})
        bad=copy.deepcopy(REVIEW);bad['difficulty']['calc']=6
        with self.assertRaises(provider.ProviderError):contracts.reviewed(bad)

    def test_05_cost_reservation_cache_and_pause(self):
        _,j,_,_=self.question_job()
        response={'model':'test','choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}],'usage':{'prompt_tokens':100,'completion_tokens':100}}
        with patch.object(provider,'_request',return_value=response) as request:
            result=provider.call(j,None,'test','system','JSON',max_tokens=100)
            self.assertTrue(result['ok'])
            provider.call(j,None,'test','system','JSON',max_tokens=100)
            self.assertEqual(request.call_count,1)
        with db.transaction() as conn:conn.execute('UPDATE ai_batch SET max_cost_micro=1001 WHERE id=(SELECT batch_id FROM ai_job WHERE id=?)',(j,))
        with self.assertRaises(provider.BudgetPause):provider.call(j,None,'test2','system','different JSON',max_tokens=100)
        self.call('/api/ai/jobs/'+j+'/control',{'action':'pause'})
        self.assertEqual(db.one('SELECT state FROM ai_job WHERE id=?',(j,))['state'],'paused')
        self.call('/api/ai/jobs/'+j+'/control',{'action':'resume','budget_cny':3})
        self.assertEqual(db.one('SELECT state FROM ai_job WHERE id=?',(j,))['state'],'queued')

    def test_06_dpapi_key_never_in_api_or_plaintext(self):
        if os.name!='nt':self.skipTest('DPAPI is Windows-only')
        value='sk-'+('test-secret-'*3)
        self.call('/api/ai/credentials',{'key':value})
        self.assertEqual(credentials.get_key(),value)
        self.assertNotIn(value.encode(),credentials.credential_path().read_bytes())
        self.assertNotIn(value,json.dumps(self.call('/api/ai/status')))
        credentials.credential_path().unlink()

    def test_07_pdf_anchor_separation(self):
        p={'words':[{'text':'1．','bbox':[.095,.25,.12,.27]},{'text':'2．','bbox':[.095,.31,.12,.33]},
          {'text':'【答案】A','bbox':[.09,.29,.2,.3]}]}
        q=[{'qno':'1','statement_regions':[{'bbox':[.1,.4,.9,.5]}]}, {'qno':'2','statement_regions':[{'bbox':[.1,.5,.9,.6]}]}]
        b=layout.locate(p,q)['1']['statement_regions'][0]['bbox']
        self.assertLess(b[1],.25);self.assertLess(b[3],.29)

    def test_08_new_question_adoption_in_transaction(self):
        f=self.call('/api/ai/files',b'%PDF-1.4 newfixture',headers={'X-Filename':'new.pdf'})['file']
        j=self.call('/api/ai/jobs',{'file_ids':[f['id']]})['job_ids'][0]
        source={'qno':'1','qtype':'blank','stem':'计算 $2+2$。','options':[],'subquestions':[],'pages':[1],'figure_paths':[]}
        content={'source':source,'question':source,'solve':SOLVE,'review':REVIEW}
        with db.transaction() as conn:
            aq=db.next_id(conn,'AIQ');c=db.next_id(conn,'AIC')
            conn.execute("INSERT INTO ai_question(id,job_id,question_key,original_qno,qtype,state,source_json) VALUES(?,?,?,?,?,'ready',?)",(aq,j,'1','1','blank',db.dumps(source)))
            conn.execute('INSERT INTO ai_candidate VALUES(?,?,?,?,?,?,?,?)',(c,aq,1,None,db.dumps(content),provider.digest(content),'test',db.now()))
        # Intentionally ensure candidate insert is structurally checked by SQLite.
        adopted=self.call('/api/ai/candidates/'+c+'/accept',{'confirmed':True,'content_hash':provider.digest(content)})
        self.assertTrue(adopted['question_id'].startswith('Q'))
        self.assertEqual(db.scalar('SELECT count(*) FROM occurrence WHERE question_id=?',(adopted['question_id'],)),1)

    def _ready_question(self,job,qno,stem,answer,review=None):
        source={'qno':str(qno),'qtype':'blank','stem':stem,'options':[],'subquestions':[],'pages':[1],'figure_paths':[]}
        content={'source':source,'question':source,'solve':{**SOLVE,'answer':answer},'review':review or REVIEW}
        with db.transaction() as conn:
            aq=db.next_id(conn,'AIQ');c=db.next_id(conn,'AIC')
            conn.execute("INSERT INTO ai_question(id,job_id,question_key,original_qno,qtype,state,source_json) VALUES(?,?,?,?,?,'ready',?)",(aq,job,str(qno),str(qno),'blank',db.dumps(source)))
            conn.execute('INSERT INTO ai_candidate VALUES(?,?,?,?,?,?,?,?)',(c,aq,1,None,db.dumps(content),provider.digest(content),'test',db.now()))
        return aq,c

    def test_09_batch_adoption_and_reject(self):
        f=self.call('/api/ai/files',b'%PDF-1.4 batchfixture',headers={'X-Filename':'batch.pdf'})['file']
        j=self.call('/api/ai/jobs',{'file_ids':[f['id']]})['job_ids'][0]
        aq2,c2=self._ready_question(j,2,'计算 $2+2$。','$4$')
        aq1,c1=self._ready_question(j,1,'计算 $1+1$。','$2$')
        before=db.scalar('SELECT count(*) FROM question')
        result=self.call('/api/ai/jobs/'+j+'/accept',{'confirmed':True})
        self.assertEqual((result['accepted'],result['failed']),(2,0))
        self.assertEqual([x['ai_question_id'] for x in result['items']],[aq1,aq2])
        self.assertEqual(db.scalar('SELECT count(*) FROM question'),before+2)
        self.assertEqual(db.scalar("SELECT count(*) FROM ai_question WHERE job_id=? AND state='adopted'",(j,)),2)
        self.call('/api/ai/jobs/'+j+'/accept',{'confirmed':True},409)
        aq3,c3=self._ready_question(j,3,'计算 $3+3$。','$6$')
        before=db.scalar('SELECT count(*) FROM question')
        self.call('/api/ai/candidates/'+c3+'/reject',{'reason':''},400)
        self.call('/api/ai/candidates/'+c3+'/reject',{'reason':'题面截图不完整，需重新上传'})
        self.assertEqual(db.one('SELECT state FROM ai_question WHERE id=?',(aq3,))['state'],'rejected')
        self.assertTrue(self.call('/api/ai/candidates/'+c3+'/reject',{'reason':'题面截图不完整，需重新上传'})['idempotent'])
        self.assertEqual(db.scalar('SELECT count(*) FROM question'),before)
        self.assertEqual(db.scalar("SELECT count(*) FROM ai_decision WHERE action='reject'"),1)
        self.call('/api/ai/candidates/'+c3+'/accept',{'confirmed':True,'content_hash':provider.digest({'x':1})},409)

    def test_10_single_question_retry_keeps_old_candidate(self):
        q,j,d,_=self.question_job()
        aq=d['questions'][0]['id']
        before=db.one('SELECT state,current_revision FROM ai_question WHERE id=?',(aq,))
        self.assertEqual(before['state'],'ready')
        self.call('/api/ai/questions/'+aq+'/retry',{})
        after=db.one('SELECT state,current_revision FROM ai_question WHERE id=?',(aq,))
        self.assertEqual(after['state'],'recognized')
        self.assertEqual(after['current_revision'],before['current_revision']+1)
        self.assertEqual(db.scalar('SELECT count(*) FROM ai_candidate WHERE ai_question_id=?',(aq,)),1)
        self.assertEqual(db.one('SELECT state FROM ai_job WHERE id=?',(j,))['state'],'queued')
        with db.transaction() as conn:conn.execute("UPDATE ai_question SET state='adopted' WHERE id=?",(aq,))
        self.call('/api/ai/questions/'+aq+'/retry',{},409)

    def test_11_math_delimiter_and_punctuation_checks(self):
        with self.assertRaises(provider.ProviderError):contracts.text(r'\[$a+b$\]','stem')
        with self.assertRaises(provider.ProviderError):contracts.text('$A、B、D$','stem')
        self.assertEqual(contracts.text(r'$\text{A、B}$','stem'),r'$\text{A、B}$')
        contracts.text(r'由 $a+b$ 得 $c>0$，其中 $x\ne 0$。','stem')
        with self.assertRaises(provider.ProviderError):contracts.text('总价 $5 元','stem')
        contracts.text(r'总价 \$5 元','stem')

    def test_12a_boxed_numbers_and_unbracketed_answer_boundaries(self):
        page={'words':[{'text':'4','bbox':[.12,.296,.13,.309]},
            {'text':'答案','bbox':[.15,.663,.18,.674]},
            {'text':'解析','bbox':[.15,.718,.18,.729]}]}
        q={'qno':'4','statement_regions':[{'bbox':[.1,.29,.88,.68]}]}
        region=layout.locate(page,[q])['4']['statement_regions'][0]['bbox']
        self.assertLess(region[1],.296)
        self.assertLess(region[3],.663)
        self.assertTrue(worker._answer_label(page,[.1,.65,.9,.7]))
        self.assertFalse(worker._answer_label(page,region))
        contracts.text(r'$\overset{\frown}{AB}$','stem')

    def test_12b_continuation_preserves_options_and_clean_images(self):
        source={'stem':'原题','options':[{'key':'A','text':'-4'},{'key':'B','text':'缺失'}],
            'subquestions':[],'reference':{},'uncertainties':[]}
        item={'qno':'3','continuation':True,'options':[{'key':'B','text':'-3'},
            {'key':'C','text':'-2'},{'key':'D','text':'-1'}],
            'reference':{'answer':'B'}}
        worker.merge_continuation(source,item)
        self.assertEqual([o['text'] for o in source['options']],['-4','-3','-2','-1'])
        page={'words':[{'text':'B.','bbox':[.1,.049,.12,.062]},
            {'text':'D.','bbox':[.1,.092,.12,.105]},
            {'text':'答案','bbox':[.15,.149,.18,.16]},
            {'text':'4','bbox':[.12,.296,.13,.309]}]}
        q4={'qno':'4','statement_regions':[{'bbox':[.1,.29,.9,.6]}]}
        region=layout.locate(page,[item,q4])['3']['statement_regions'][0]['bbox']
        self.assertLess(region[1],.049)
        self.assertGreater(region[3],.105)
        self.assertLess(region[3],.149)
        self.assertFalse(worker._answer_label(page,region))
        self.assertNotIn('3',layout.locate(page,[{**item,'options':[]},q4]))

    def test_12c_tex_normalisation_preserves_matrix_row_breaks(self):
        for value in (r'$\begin{cases}x=2y+2\\y^2=4x\end{cases}$',
                      r'$\begin{aligned}x=1\\\frac{y}{2}=3\end{aligned}$',
                      r'$\begin{matrix}x\\n+1\end{matrix}$',
                      r'$\begin{matrix}a\\i\\j\\{x}\end{matrix}$',
                      r'$\begin{matrix}a\\\{x\}\end{matrix}$'):
            self.assertEqual(provider.normalise(value),value)
            self.assertEqual(provider.normalise(provider.normalise(value)),value)
            contracts.text(provider.normalise(value),'stem')
        self.assertEqual(provider.normalise(r'$\\vec{a}+\\frac{1}{2}$'),r'$\vec{a}+\frac{1}{2}$')
        self.assertEqual(provider.normalise(r'$\\begin{matrix}a\\\\i\\end{matrix}$'),r'$\begin{matrix}a\\i\end{matrix}$')
        value='答案为 $A、B、D$，因此成立。'
        normal=provider.normalise(value)
        self.assertEqual(normal,r'答案为 $A\text{、}B\text{、}D$，因此成立。')
        self.assertEqual(provider.normalise(normal),normal)
        contracts.text(normal,'answer')
        self.assertEqual(provider.normalise(r'价格 \$5，选项为 $A、B$'),r'价格 \$5，选项为 $A\text{、}B$')

    def test_12e_explicit_retry_refreshes_cache_but_resume_does_not(self):
        _,jid,detail,_=self.question_job()
        qid=detail['questions'][0]['id']
        response={'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}],
                  'usage':{'prompt_tokens':2,'completion_tokens':3}}
        with patch.object(provider,'_request',return_value=response) as request:
            provider.call(jid,qid,'cache-test','system','same prompt',max_tokens=100)
            provider.call(jid,qid,'cache-test','system','same prompt',max_tokens=100)
            self.assertEqual(request.call_count,1)
            self.call('/api/ai/questions/'+qid+'/retry',{})
            provider.call(jid,qid,'cache-test','system','same prompt',max_tokens=100)
            provider.call(jid,qid,'cache-test','system','same prompt',max_tokens=100)
            self.assertEqual(request.call_count,2)
        self.call('/api/ai/questions/'+qid+'/retry',{},409)  # Already queued.
        self.call('/api/ai/jobs/'+jid+'/control',{'action':'resume'},409)

    def test_12f_rating_only_adoption_preserves_solution_method_tags(self):
        question,jid,detail,_=self.question_job()
        vid=db.one('SELECT current_version_id FROM question WHERE id=?',(question,))['current_version_id']
        tag=self.call('/api/tags',{'dimension':'method','name':'部分采纳保留方法回归'})['id']
        self.call('/api/versions/'+vid+'/solutions',{'label':'原解法','steps':'原有步骤','method_tags':[tag]})
        candidate=detail['questions'][0]['candidate']
        result=self.call('/api/ai/candidates/'+candidate['id']+'/accept',{'content_hash':candidate['content_hash'],'confirmed':True,'parts':['rating']})
        methods=db.all_('SELECT st.tag_id FROM solution_tag st JOIN solution s ON s.id=st.solution_id WHERE s.question_version_id=?',(result['version_id'],))
        self.assertEqual([row['tag_id'] for row in methods],[tag])
        self.assertEqual(db.scalar('SELECT steps FROM solution WHERE question_version_id=?',(result['version_id'],)),'原有步骤')

    def test_12g_late_response_never_overwrites_teacher_revision(self):
        for target in ('solve','review'):
            with self.subTest(stage=target):
                _,jid,detail,_=self.question_job();qid=detail['questions'][0]['id']
                with db.transaction() as conn:
                    conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(jid,))
                    if target=='solve':conn.execute('UPDATE ai_question SET solve_json=NULL WHERE id=?',(qid,))
                row=db.one('SELECT * FROM ai_question WHERE id=?',(qid,))
                revised=json.loads(row['source_json']);revised['stem']='教师刚修订的题面'
                def fake(_j,_q,stage,*args,**kwargs):
                    if stage==target:
                        with db.transaction() as conn:
                            conn.execute("UPDATE ai_question SET current_revision=current_revision+1,source_json=?,state='recognized',solve_json=NULL,review_json=NULL WHERE id=?",(db.dumps(revised),qid))
                            conn.execute("UPDATE ai_job SET state='paused' WHERE id=?",(jid,))
                    return copy.deepcopy(SOLVE if stage=='solve' else REVIEW)
                with patch.object(provider,'call',side_effect=fake),self.assertRaises(InterruptedError):
                    worker.process_question(db.one('SELECT * FROM ai_job WHERE id=?',(jid,)),row)
                current=db.one('SELECT * FROM ai_question WHERE id=?',(qid,))
                self.assertEqual(current['state'],'recognized')
                self.assertEqual(json.loads(current['source_json'])['stem'],revised['stem'])
                self.assertIsNone(current['solve_json'])
                self.assertEqual(db.scalar('SELECT count(*) FROM ai_candidate WHERE ai_question_id=?',(qid,)),1)

    def test_refine_keeps_original_section_points_when_crop_has_no_score(self):
        _,jid,detail,_=self.question_job();qid=detail['questions'][0]['id']
        asset=ROOT/'build/_test_ai-score-evidence.png';asset.write_bytes(b'local test evidence')
        source={'qno':'1','qtype':'blank','stem':'计算 $2+2$。','options':[],'subquestions':[], 'total_points':5,'reference':{},'pages':[1], 'statement_images':[{'path':asset.relative_to(ROOT).as_posix(),'page':1,'answer_free':True}], 'figure_paths':[]}
        try:
            with db.transaction() as conn:
                conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(jid,))
                conn.execute("UPDATE ai_question SET original_qno='1',base_version_id=NULL,current_revision=current_revision+1,source_json=?,solve_json=NULL,review_json=NULL,state='recognized' WHERE id=?",(db.dumps(source),qid))
            def fake(_j,_q,stage,*args,**kwargs):
                if stage=='refine':return {'questions':[{'qno':'1','qtype':'blank','stem':'计算 $2+2$。','total_points':0}]}
                return copy.deepcopy(SOLVE if stage=='solve' else REVIEW)
            with patch.object(provider,'call',side_effect=fake):worker.process_question(db.one('SELECT * FROM ai_job WHERE id=?',(jid,)),db.one('SELECT * FROM ai_question WHERE id=?',(qid,)))
            current=db.one('SELECT * FROM ai_question WHERE id=?',(qid,))
            self.assertEqual(json.loads(current['source_json'])['total_points'],5)
            candidate=db.one('SELECT content_json FROM ai_candidate WHERE ai_question_id=? AND revision=?',(qid,current['current_revision']))
            self.assertEqual(json.loads(candidate['content_json'])['question']['total_points'],5)
        finally:
            asset.unlink(missing_ok=True)
            with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='paused' WHERE id=?",(jid,))

    def test_12h_edit_resets_review_and_preserves_evidence_paths(self):
        _,jid,detail,_=self.question_job();question=detail['questions'][0]
        source={**question['source'],'stem':'教师修订后的 $2+2$。',
                'figure_paths':['data/private/not-an-image'],'statement_images':[{'path':'arbitrary-file'}]}
        self.call('/api/ai/questions/'+question['id']+'/edit',{'source':source})
        row=db.one('SELECT * FROM ai_question WHERE id=?',(question['id'],))
        current=json.loads(row['source_json'])
        self.assertEqual(row['quality_state'],'pending')
        self.assertEqual(current['figure_paths'],question['source']['figure_paths'])
        self.assertEqual(current['statement_images'],question['source']['statement_images'])
        self.assertEqual(db.scalar('SELECT count(*) FROM ai_candidate WHERE ai_question_id=?',(question['id'],)),1)

    def test_12d_resuming_unnumbered_continuation_is_idempotent(self):
        f=self.call('/api/ai/files',b'%PDF-1.4 continuationfixture',headers={'X-Filename':'continuation.pdf'})['file']
        jid=self.call('/api/ai/jobs',{'file_ids':[f['id']]})['job_ids'][0]
        with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(jid,))
        job=db.one('SELECT * FROM ai_job WHERE id=?',(jid,))
        first={'qno':'1','qtype':'blank','stem':'计算 $1+1$。','reference':{},'options':[],'subquestions':[]}
        continuation={'qno':None,'qtype':'blank','continuation':True,'stem':'','reference':{'answer':'$2$'}}
        second={**first,'qno':'2','stem':'计算 $2+2$。'}
        doc={'pages':[{'page':i,'text':'','path':f'mock{i}.png'} for i in (1,2,3)],'sha256':'test'}
        def fake(_j,_q,stage,*args,**kwargs):
            questions=[first] if stage=='recognize:1' else [continuation,second] if stage=='recognize:2' else [{**continuation,'reference':{'answer':'$4$'}}]
            return {'questions':copy.deepcopy(questions)}
        with patch.object(worker.documents,'prepare',return_value=doc),patch.object(provider,'call',side_effect=fake):
            worker.recognize(job)
            worker.recognize(job)
        rows=db.all_('SELECT source_json FROM ai_question WHERE job_id=? ORDER BY id',(jid,))
        self.assertEqual(len(rows),2)
        source=json.loads(rows[0]['source_json'])
        self.assertEqual(source['pages'],[1,2])
        self.assertEqual(source['reference']['answer'],'$2$')
        second_source=json.loads(rows[1]['source_json'])
        self.assertEqual(second_source['pages'],[2,3])
        self.assertEqual(second_source['reference']['answer'],'$4$')

    def test_12i_coordinate_arrays_are_normalized_and_validated(self):
        q={'qno':'1','qtype':'blank','stem':'计算 $1+1$。',
           'statement_regions':[[.1,.1,.9,.3]],'reference_regions':[[.1,.4,.9,.5]],
           'figures':[{'key':'f1','bbox':[.2,.15,.3,.25]}]}
        contracts.recognition({'questions':[q]})
        self.assertEqual(q['statement_regions'],[{'bbox':[.1,.1,.9,.3]}])
        self.assertEqual(q['reference_regions'],[{'bbox':[.1,.4,.9,.5]}])
        self.assertEqual(q['figures'][0]['key'],'f1')
        for region in ([0,0,1,2], [0,0,1], None, 'bbox', {'bbox':[.9,.1,.1,.5]}):
            broken={**q,'statement_regions':[region]}
            with self.assertRaises(provider.ProviderError) as caught:contracts.recognition({'questions':[broken]})
            self.assertEqual(caught.exception.code,'contract_invalid')

    def test_12j_malformed_nested_items_report_contract_error(self):
        for response in ({'questions':[None]}, {'questions':{}}, []):
            with self.assertRaises(provider.ProviderError):contracts.recognition_questions(response)
        base={'qno':'1','qtype':'blank','stem':'题目'}
        for changes in ({'subquestions':[None]},{'figures':[True]}, {'uncertainties':'unclear'}, {'continuation':'false'}):
            with self.assertRaises(provider.ProviderError):contracts.recognition({'questions':[{**base,**changes}]})
        for changes in ({'solutions':[None]}, {'subanswers':[None]}, {'answer_keys':[{}]}):
            with self.assertRaises(provider.ProviderError):contracts.solved({**SOLVE,**changes},{'qtype':'blank'})
        with self.assertRaises(provider.ProviderError):contracts.reviewed({**REVIEW,'tags':[None]})

    def test_12k_array_regions_in_continuation_resume_without_loss(self):
        f=self.call('/api/ai/files',b'%PDF-1.4 regionfixture',headers={'X-Filename':'regions.pdf'})['file']
        jid=self.call('/api/ai/jobs',{'file_ids':[f['id']]})['job_ids'][0]
        with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(jid,))
        job=db.one('SELECT * FROM ai_job WHERE id=?',(jid,))
        first={'qno':'1','qtype':'blank','stem':'计算 $1+1$。','statement_regions':[[.1,.1,.9,.3]],'reference':{}}
        continuation={'qno':'待定','qtype':'blank','continuation':True,'stem':'',
                      'reference_regions':[[.1,.05,.9,.1]],'reference':{'answer':'$2$'}}
        second={**first,'qno':'2'}
        doc={'pages':[{'page':i,'text':'','path':f'mock{i}.png'} for i in (1,2)],'sha256':'test'}
        def fake(_j,_q,stage,*args,**kwargs):
            return {'questions':copy.deepcopy([first] if stage=='recognize:1' else [continuation,second])}
        def region(_job,_qid,_page,r,*args):
            self.assertIsInstance(r,dict)
            contracts.box(r['bbox'])
            return 'assets/test-region.png',True
        with patch.object(worker.documents,'prepare',return_value=doc),patch.object(provider,'call',side_effect=fake),patch.object(worker,'_region',side_effect=region):
            worker.recognize(job)
            worker.recognize(job)
        rows=db.all_('SELECT source_json FROM ai_question WHERE job_id=? ORDER BY id',(jid,))
        self.assertEqual(len(rows),2)
        source=json.loads(rows[0]['source_json'])
        self.assertEqual(source['pages'],[1,2])
        self.assertEqual(source['reference']['answer'],'$2$')
        self.assertEqual(source['reference_areas'][0]['bbox'],[.1,.05,.9,.1])
        self.assertEqual(len(source['statement_images']),1)

    def test_12l_unexpected_error_keeps_safe_code_locations(self):
        _,jid,detail,_=self.question_job()
        qid=detail['questions'][0]['id']
        with db.transaction() as conn:
            conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(jid,))
            conn.execute("UPDATE ai_question SET state='recognized' WHERE id=?",(qid,))
        with patch.object(worker,'process_question',side_effect=AttributeError('SECRET_DIAGNOSTIC_SENTINEL')):
            worker.run_job(db.one('SELECT * FROM ai_job WHERE id=?',(jid,)))
        job=db.one('SELECT * FROM ai_job WHERE id=?',(jid,))
        self.assertEqual(job['state'],'failed')
        error=json.loads(job['error_json'])
        self.assertEqual(error['code'],'processing_error')
        self.assertIn('AttributeError',error['message'])
        self.assertTrue(error['trace'])
        self.assertTrue(all(set(f)=={'file','line','function'} for f in error['trace']))
        self.assertNotIn('SECRET_DIAGNOSTIC_SENTINEL',job['error_json'])

    def test_12m_subquestion_only_stem_is_valid_without_inventing_conditions(self):
        sub=[{'key':'1','text':'计算 $1+1$。','depends_on':[]}]
        q={'qno':'19','qtype':'solve','stem':'','subquestions':copy.deepcopy(sub)}
        contracts.recognition({'questions':[q]})
        self.assertEqual(q['stem'],'解答下列问题。')
        self.assertEqual(q['subquestions'],sub)
        for changes in ({'subquestions':[]},{'subquestions':[{'key':'1','text':''}]},
                        {'qtype':'single','options':[{'key':'A','text':'1'},{'key':'B','text':'2'}]}):
            with self.assertRaises(provider.ProviderError):contracts.question({**q,'stem':'',**changes})
        continuation={**q,'stem':'','continuation':True}
        contracts.question(continuation)
        self.assertEqual(continuation['stem'],'')

    def test_12n_real_number_box_overrides_bad_visual_hint(self):
        from app.ai import document_tool
        from types import SimpleNamespace
        p=SimpleNamespace(width=100,height=100,rects=[
            {'x0':10,'x1':16,'top':45,'bottom':48,'width':6,'height':3,'fill':True}])
        words=[{'text':'6','x0':12,'x1':13,'top':46,'bottom':47},
               {'text':'6','x0':12,'x1':13,'top':70,'bottom':71}]
        boxes=document_tool.number_boxes(p,words)
        self.assertEqual(len(boxes),1)
        page={'number_boxes':boxes,'words':[
            {'text':'6','bbox':[.12,.46,.13,.47]},
            {'text':'答案','bbox':[.15,.67,.18,.68]},
            {'text':'6','bbox':[.12,.70,.13,.71]}]}
        q={'qno':'6','statement_regions':[{'bbox':[.06,.65,.94,.79]}]}
        result=layout.locate(page,[q])['6']
        self.assertEqual(result['method'],'pdf_boxed_number_anchors')
        self.assertLess(result['statement_regions'][0]['bbox'][1],.46)
        self.assertLess(result['statement_regions'][0]['bbox'][3],.67)
        unboxed=layout.locate({**page,'number_boxes':[]},[q])
        self.assertTrue('6' not in unboxed or unboxed['6']['method']!='pdf_boxed_number_anchors')

    def test_12o_repair_replaces_entire_bad_page_and_retains_guard(self):
        f=self.call('/api/ai/files',b'%PDF-1.4 boxrepairfixture',headers={'X-Filename':'boxrepair.pdf'})['file']
        jid=self.call('/api/ai/jobs',{'file_ids':[f['id']]})['job_ids'][0]
        source={'qno':'6','qtype':'blank','stem':'计算 $1+1$。','pages':[1,2],
          'statement_regions':[{'bbox':[.06,.65,.94,.79]}],
          'statement_images':[{'path':'old-bad.png','page':1,'answer_free':False},
                              {'path':'old-mislabelled.png','page':1,'answer_free':True},
                              {'path':'other-page.png','page':2,'answer_free':True}],
          'reference_areas':[{'page':1,'bbox':[.06,.94,.94,.99]}]}
        with db.transaction() as conn:
            qid=db.next_id(conn,'AIQ')
            conn.execute("INSERT INTO ai_question(id,job_id,question_key,original_qno,qtype,state,source_json) VALUES(?,?,?,?,?,'failed',?)",(qid,jid,'6','6','blank',db.dumps(source)))
        row=db.one('SELECT * FROM ai_question WHERE id=?',(qid,));job=db.one('SELECT * FROM ai_job WHERE id=?',(jid,))
        page={'page':1,'path':'page.png','words':[
            {'text':'6','bbox':[.12,.46,.13,.47]}, {'text':'答案','bbox':[.15,.67,.18,.68]}],
            'number_boxes':[{'text':'6','bbox':[.12,.46,.13,.47]}]}
        with patch.object(worker.documents,'prepare',return_value={'pages':[{**page,'number_boxes':[]}]}),patch.object(worker,'_region') as region:
            self.assertEqual(worker.repair_statement_regions(job,row,source),source)
            region.assert_not_called()
        with patch.object(worker.documents,'prepare',return_value={'pages':[page]}) as prepare,patch.object(worker,'_region',return_value=('assets/repaired.png',True)) as region:
            repaired=worker.repair_statement_regions(job,row,source)
            again=worker.repair_statement_regions(job,row,repaired)
            self.assertEqual(prepare.call_count,1)
            self.assertEqual(region.call_count,1)
            self.assertEqual(again,repaired)
        self.assertEqual([x['path'] for x in repaired['statement_images']],['other-page.png','assets/repaired.png'])
        self.assertTrue(repaired['layout_repaired'])
        self.assertEqual(json.loads(db.one('SELECT source_json FROM ai_question WHERE id=?',(qid,))['source_json']),repaired)
        self.assertEqual(db.scalar("SELECT count(*) FROM audit_log WHERE action='ai_repair_statement' AND target_id=?",(qid,)),1)

    def test_12p_old_manifest_layout_upgrade_preserves_page_images(self):
        from app.ai import documents
        import hashlib
        raw=b'%PDF-1.4 oldmanifestfixture'
        f=self.call('/api/ai/files',raw,headers={'X-Filename':'oldmanifest.pdf'})['file']
        directory=db.store_dir('build')/'_ai'/'documents'/f['id'];directory.mkdir(parents=True,exist_ok=True)
        image=directory/'page-001.png';image.write_bytes(b'IMAGE_CACHE_SENTINEL')
        manifest=directory/'manifest.json'
        manifest.write_text(json.dumps({'sha256':hashlib.sha256(raw).hexdigest(),
             'pages':[{'page':1,'path':str(image),'text':'preserved','words':[]}]}),encoding='utf-8')
        boxes=[{'text':'6','bbox':[.12,.46,.13,.47]}]
        with patch.object(documents,'tool',return_value={'layout_version':1,'pages':[{'page':1,'number_boxes':boxes}]}) as tool:
            first=documents.prepare(f['id']);second=documents.prepare(f['id'])
            self.assertEqual(tool.call_count,1)
            self.assertEqual(tool.call_args.args[0]['action'],'layout')
        self.assertEqual(first,second)
        self.assertEqual(second['pages'][0]['number_boxes'],boxes)
        self.assertEqual(second['pages'][0]['text'],'preserved')
        self.assertEqual(image.read_bytes(),b'IMAGE_CACHE_SENTINEL')

    def test_12q_legacy_candidates_normalize_on_adoption_without_rewriting_receipt(self):
        for field,value in [('answer',r'$\mathrm{A、C、D}$'),('answer','$A、B、D$ 正确。'),
                            ('steps',r'由交集计算得\[$A\cap B=\{1,2\}.\]，所以有 $4$ 个子集。')]:
            with self.subTest(field=field,value=value):
                q,j,d,_=self.question_job()
                candidate=d['questions'][0]['candidate']
                raw=worker.unpack(db.one('SELECT * FROM ai_candidate WHERE id=?',(candidate['id'],)),'content_json')
                if field=='answer':raw['solve']['answer']=value
                else:raw['solve']['solutions'][0]['steps']=value
                encoded=db.dumps(raw);content_hash=provider.digest(raw)
                with db.transaction() as conn:
                    conn.execute('UPDATE ai_candidate SET content_json=?,content_hash=? WHERE id=?',(encoded,content_hash,candidate['id']))
                payload={'content_hash':content_hash,'confirmed':True}
                result=self.call('/api/ai/candidates/'+candidate['id']+'/accept',payload)
                version=db.one('SELECT * FROM question_version WHERE id=?',(result['version_id'],))
                contracts.text(version['answer'],'answer')
                steps=db.one('SELECT steps FROM solution WHERE question_version_id=?',(result['version_id'],))['steps']
                contracts.text(steps,'steps')
                if field=='answer':self.assertIn(r'\text{、}',version['answer'])
                else:self.assertNotIn(r'\[$',steps)
                retained=db.one('SELECT * FROM ai_candidate WHERE id=?',(candidate['id'],))
                self.assertEqual(retained['content_json'],encoded)
                self.assertEqual(retained['content_hash'],content_hash)
                self.assertTrue(json.loads(db.one('SELECT detail_json FROM ai_decision WHERE candidate_id=?',(candidate['id'],))['detail_json'])['format_normalized'])
                self.assertTrue(self.call('/api/ai/candidates/'+candidate['id']+'/accept',payload)['idempotent'])

    def test_12r_normalization_does_not_bypass_invalid_tex_or_ambiguous_math(self):
        for field,value in [('answer',r'\input{private}'),('steps',r'\[$a$+$b$\]')]:
            q,j,d,_=self.question_job();candidate=d['questions'][0]['candidate']
            raw=worker.unpack(db.one('SELECT * FROM ai_candidate WHERE id=?',(candidate['id'],)),'content_json')
            if field=='answer':raw['solve']['answer']=value
            else:raw['solve']['solutions'][0]['steps']=value
            content_hash=provider.digest(raw)
            with db.transaction() as conn:
                conn.execute('UPDATE ai_candidate SET content_json=?,content_hash=? WHERE id=?',(db.dumps(raw),content_hash,candidate['id']))
            before=db.scalar('SELECT count(*) FROM question_version WHERE question_id=?',(q,))
            self.call('/api/ai/candidates/'+candidate['id']+'/accept',{'content_hash':content_hash,'confirmed':True},400)
            self.assertEqual(db.scalar('SELECT count(*) FROM question_version WHERE question_id=?',(q,)),before)
            self.assertEqual(db.scalar('SELECT count(*) FROM ai_decision WHERE candidate_id=?',(candidate['id'],)),0)

    def test_12_auto_adoption_after_self_review(self):
        def run(q_stem,answer,review):
            q=self.call('/api/questions',{'qtype':'blank','stem':q_stem,'answer':answer,'occurrence':{'pending':1}})['question']['id']
            job=self.call('/api/ai/jobs',{'question_ids':[q],'budget_cny':2})['job_ids'][0]
            with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(job,))
            def fake(_j,_q,stage,_s,_p,images=(),**kw):
                return copy.deepcopy({**SOLVE,'answer_keys':['4']} if stage=='solve' else review)
            with patch.object(provider,'call',side_effect=fake):
                worker.run_job(db.one('SELECT * FROM ai_job WHERE id=?',(job,)))
            return q,job

        q,job=run('计算 $3+3$。','SECRET_AUTO',REVIEW)
        row=db.one('SELECT * FROM ai_question WHERE job_id=?',(job,))
        self.assertEqual(row['state'],'adopted')
        vid=row['adopted_version_id']
        self.assertEqual(db.one('SELECT answer FROM question_version WHERE id=?',(vid,))['answer'],'$4$')
        teaching=db.one('SELECT teaching FROM question_version WHERE id=?',(vid,))['teaching']
        self.assertEqual(teaching.get('ai_math_review'),'ai_self_review')
        self.assertEqual(db.scalar("SELECT count(*) FROM review WHERE target_type='question_version' AND target_id=? AND item='stem' AND result='pass'",(vid,)),1)
        self.assertEqual(db.scalar("SELECT count(*) FROM review WHERE target_type='question_version' AND target_id=? AND item='answer' AND result='pass'",(vid,)),1)
        self.assertEqual(db.scalar("SELECT count(*) FROM review WHERE target_type='solution' AND target_id IN (SELECT id FROM solution WHERE question_version_id=?)",(vid,)),1)
        self.assertTrue(db.scalar("SELECT reviewer FROM review WHERE target_id=? LIMIT 1",(vid,)).startswith('AI 自复核'))
        self.assertEqual(db.one("SELECT operator FROM ai_decision WHERE candidate_id=(SELECT id FROM ai_candidate WHERE ai_question_id=? ORDER BY revision DESC LIMIT 1)",(row['id'],))['operator'],'AI 自复核')
        self.assertTrue(self.call('/api/questions/'+q+'/versions/2')['eligibility']['usable'])

        q2,job2=run('计算 $5+5$。','SECRET_KEEP',{**REVIEW,'verdict':'needs_review','reason':'需人工确认'})
        row2=db.one('SELECT * FROM ai_question WHERE job_id=?',(job2,))
        self.assertEqual(row2['state'],'ready')
        self.assertIsNone(row2['adopted_version_id'])
        self.assertEqual(db.scalar('SELECT count(*) FROM question_version WHERE question_id=?',(q2,)),1)

    def test_auto_adopt_unexpected_failure_is_visible_and_next_question_continues(self):
        from app.ai import adopt
        qids=[self.call('/api/questions',{'qtype':'blank','stem':'计算 $2+2$。','answer':'4','occurrence':{'pending':1}})['question']['id'] for _ in range(2)]
        jid=self.call('/api/ai/jobs',{'question_ids':qids,'budget_cny':2})['job_ids'][0]
        with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(jid,))
        real=adopt.accept_candidate;seen=[]
        def accept(candidate,*args,**kwargs):
            seen.append(candidate['id'])
            if len(seen)==1:raise KeyError('synthetic failure')
            return real(candidate,*args,**kwargs)
        def fake(_j,_q,stage,_s,_p,images=(),**kw):return copy.deepcopy(SOLVE if stage=='solve' else REVIEW)
        with patch.object(provider,'call',side_effect=fake),patch.object(adopt,'accept_candidate',side_effect=accept):
            worker.run_job(db.one('SELECT * FROM ai_job WHERE id=?',(jid,)))
        rows=db.all_('SELECT * FROM ai_question WHERE job_id=? ORDER BY id',(jid,))
        self.assertEqual(sorted(r['state'] for r in rows),['adopted','ready'])
        failed=next(r for r in rows if r['state']=='ready')
        self.assertEqual(json.loads(failed['error_json'])['error_type'],'KeyError')
        self.assertEqual(db.scalar("SELECT count(*) FROM audit_log WHERE action='ai_auto_adopt_failed' AND target_id=?",(failed['id'],)),1)
        candidate=db.one('SELECT * FROM ai_candidate WHERE ai_question_id=? AND revision=?',(failed['id'],failed['current_revision']))
        real(candidate,list(adopt.PARTS),auto=True)
        self.assertIsNone(db.one('SELECT error_json FROM ai_question WHERE id=?',(failed['id'],))['error_json'])

if __name__=='__main__':unittest.main()
