"""边界回归，使用独立数据库及模拟文档组件，不调用收费 AI。"""
import copy
import hashlib
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
import io
import zipfile
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.environ['QBANK_DB']=str(ROOT/'build/_test_robustness.db')
os.environ['QBANK_AI_WORKER']='0'
for suffix in ('','-wal','-shm'):Path(os.environ['QBANK_DB']+suffix).unlink(missing_ok=True)
from app import db
from app.server import Handler,create_app
from app.ai import contracts,documents,export,provider,worker

SOLVE={'status':'solved','answer':'2','answer_keys':[],
       'solutions':[{'label':'解法一','steps':'$1+1=2$'}],'subanswers':[]}
REVIEW={'verdict':'no_issue_found','reason':'核对完成','tags':[],
        'difficulty':{'calc':1,'think':1,'scope':1},'rec_level':'B','time_solve_min':1}

class RobustnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.base='http://127.0.0.1:'+str(cls.server.server_address[1])
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();db.close()

    def call(self,path,body=None,method=None,expect=200):
        if method is None:method='POST' if body is not None else 'GET'
        payload=json.dumps(body).encode() if method in ('POST','PATCH') else None
        req=urllib.request.Request(self.base+path,data=payload,method=method,headers={'Content-Type':'application/json'})
        try:response=urllib.request.urlopen(req,timeout=10)
        except urllib.error.HTTPError as exc:response=exc
        with response:
            content=json.load(response)
            self.assertEqual(response.code,expect,content)
            return content

    def file_job(self):
        raw=b'%PDF-1.4 robustness fixture'
        req=urllib.request.Request(self.base+'/api/ai/files',data=raw,headers={'X-Filename':'robustness.pdf'})
        with urllib.request.urlopen(req) as response:f=json.load(response)['file']
        jid=self.call('/api/ai/jobs',{'file_ids':[f['id']],'auto_accept':False})['job_ids'][0]
        return f,jid

    def test_json_body_requires_object(self):
        for body in ([],None,'text',7,True):
            with self.subTest(body=body):self.call('/api/ai/jobs',body,method='POST',expect=400)

    def test_invalid_identifier_lists_are_bad_requests(self):
        for data in ({'question_ids':[{}]},{'file_ids':[[]]},{'question_ids':[None]}):
            with self.subTest(data=data):self.call('/api/ai/jobs',data,expect=400)
        _,jid=self.file_job()
        self.call('/api/ai/jobs/'+jid+'/accept',{'confirmed':True,'candidate_ids':[{}]},expect=400)

    def test_corrupt_or_missing_page_cache_is_rebuilt(self):
        f,_=self.file_job()
        directory=db.store_dir('build')/'_ai/documents'/f['id'];directory.mkdir(parents=True,exist_ok=True)
        cached=directory/'manifest.json';image=directory/'page-001.png'
        manifest={'sha256':hashlib.sha256(b'%PDF-1.4 robustness fixture').hexdigest(),'layout_version':1,
                  'pages':[{'page':1,'path':str(image),'text':'fixture','words':[],'number_boxes':[]}]}
        def regenerate(args):
            self.assertEqual(args['action'],'prepare')
            image.write_bytes(b'REBUILT_IMAGE')
            return copy.deepcopy(manifest)
        for broken in ('{broken',json.dumps(manifest)):
            with self.subTest(cache=broken[:20]):
                image.unlink(missing_ok=True);cached.write_text(broken,encoding='utf-8')
                with patch.object(documents,'tool',side_effect=regenerate) as tool:
                    result=documents.prepare(f['id'])
                    self.assertEqual(result['pages'][0]['text'],'fixture')
                    self.assertEqual(documents.prepare(f['id']),result)
                    self.assertEqual(tool.call_count,1)

    def test_legacy_export_normalizes_copy_and_preserves_raw(self):
        f,jid=self.file_job()
        candidate={'question':{'qno':'1','qtype':'blank','stem':'写出元素集合。','subquestions':[]},
                   'solve':{**copy.deepcopy(SOLVE),'answer':'$A、B$',
                            'solutions':[{'label':'解法一','steps':r'\[$A+B$\]'}]},'review':copy.deepcopy(REVIEW)}
        original=copy.deepcopy(candidate)
        snap=export.snapshot(db.one('SELECT * FROM ai_job WHERE id=?',(jid,)),[candidate])
        entry=next(iter(snap['question_versions'].values()))
        contracts.text(entry['version']['answer'],'answer')
        contracts.text(entry['solutions'][0]['steps'],'steps')
        self.assertEqual(candidate,original)

    def test_document_tool_rejects_non_object_reply(self):
        with patch.object(documents.subprocess,'run',return_value=SimpleNamespace(stdout='[]',returncode=0)):
            with self.assertRaises(ValueError):documents.tool({'action':'layout','source':'unused.pdf'})

    def test_bundle_tex_normalizes_while_json_preserves_raw(self):
        q,jid,row,cid,digest=self.candidate()
        candidate=db.one('SELECT * FROM ai_candidate WHERE id=?',(cid,))
        content=worker.unpack(candidate,'content_json')
        content['solve']['answer']='$A、C$'
        content['solve']['solutions'][0]['steps']=r'\[$1+1=2$\]'
        with db.transaction() as conn:
            conn.execute('UPDATE ai_candidate SET content_json=?,content_hash=? WHERE id=?',(db.dumps(content),provider.digest(content),cid))
        with urllib.request.urlopen(self.base+'/api/ai/jobs/'+jid+'/bundle',timeout=10) as response:
            archive=zipfile.ZipFile(io.BytesIO(response.read()))
        with archive:
            original=json.loads(archive.read('results.json'))['candidates'][0]
            self.assertEqual(original,content)
            for filename in ('questions.tex','teacher.tex'):
                tex=archive.read(filename).decode('utf-8')
                self.assertIn(r'\text{、}',tex)
                self.assertNotIn(r'\[$',tex)

    def test_question_only_adoption_keeps_new_subquestion_text(self):
        q,jid,row,cid,digest=self.candidate(
            base={'qtype':'solve','stem':'解答。','answer':'2','subquestions':[{'key':'1','text':'原小问','answer':'2'}]},
            question={'qno':'1','qtype':'solve','stem':'修订题面。','options':[],
                'subquestions':[{'key':'1','text':'修订后第一问','depends_on':[]},{'key':'2','text':'新增第二问','depends_on':['1']}]},
            solved={**copy.deepcopy(SOLVE),'subanswers':[{'key':'1','answer':'2'},{'key':'2','answer':'4'}]})
        result=self.call('/api/ai/candidates/'+cid+'/accept',{'confirmed':True,'content_hash':digest,'parts':['question']})
        version=db.one('SELECT * FROM question_version WHERE id=?',(result['version_id'],))
        self.assertEqual([s['text'] for s in version['subquestions']],['修订后第一问','新增第二问'])
        self.assertEqual([s['answer'] for s in version['subquestions']],['2',''])

    def test_existing_question_macro_is_preserved_in_solution_adoption_and_export(self):
        stem=r'计算 $1+1$，填入\blankbox。'
        q,jid,row,cid,digest=self.candidate(
            base={'qtype':'blank','stem':stem,'answer':'2'},
            question={'qno':'1','qtype':'blank','stem':stem,'options':[],'subquestions':[]},
            solved=copy.deepcopy(SOLVE))
        result=self.call('/api/ai/candidates/'+cid+'/accept',{'confirmed':True,'content_hash':digest,'parts':['solution']})
        self.assertEqual(db.one('SELECT stem FROM question_version WHERE id=?',(result['version_id'],))['stem'],stem)
        content=worker.unpack(db.one('SELECT * FROM ai_candidate WHERE id=?',(cid,)),'content_json')
        snap=export.snapshot(db.one('SELECT * FROM ai_job WHERE id=?',(jid,)),[content])
        self.assertEqual(next(iter(snap['question_versions'].values()))['version']['stem'],stem)

    def test_duplicate_subanswers_are_rejected(self):
        source={'qtype':'solve','options':[],'subquestions':[{'key':'1'},{'key':'2'}]}
        solved={**copy.deepcopy(SOLVE),'subanswers':[{'key':'1','answer':'2'},{'key':'1','answer':'3'},{'key':'2','answer':'4'}]}
        with self.assertRaises(provider.ProviderError):contracts.solved(solved,source)

    def test_cyclic_subquestion_dependencies_are_rejected(self):
        question={'qno':'1','qtype':'solve','stem':'解答。','subquestions':[
            {'key':'1','text':'第一问','depends_on':['2']},{'key':'2','text':'第二问','depends_on':['1']}]}
        with self.assertRaises(provider.ProviderError):contracts.question(question)
        question['subquestions'][0]['depends_on']=[]
        contracts.question(question)

    def candidate(self,base=None,question=None,solved=None):
        q=self.call('/api/questions',base or {'qtype':'single','stem':'$1+1=$（ ）',
                    'options':[{'key':'A','text':'2'},{'key':'B','text':'3'}],'answer':'A','answer_keys':['A']})['question']['id']
        jid=self.call('/api/ai/jobs',{'question_ids':[q],'auto_accept':False})['job_ids'][0]
        row=db.one('SELECT * FROM ai_question WHERE job_id=?',(jid,))
        if question is None:
            question={k:worker.unpack(row,'source_json')[k] for k in ('qno','qtype','stem','options','subquestions')}
            question['options']=[{'key':'A','text':'3'},{'key':'C','text':'2'}]
        if solved is None:solved={**copy.deepcopy(SOLVE),'answer':'C','answer_keys':['C']}
        content={'question':question,'solve':solved,'review':copy.deepcopy(REVIEW),
                 'source':worker.unpack(row,'source_json'),'evidence_hashes':{}}
        digest=provider.digest(content)
        with db.transaction() as conn:
            cid=db.next_id(conn,'AIC')
            conn.execute('INSERT INTO ai_candidate(id,ai_question_id,revision,base_version_id,content_json,content_hash,input_hash,created_at) VALUES(?,?,1,?,?,?,?,?)',
                (cid,row['id'],row['base_version_id'],db.dumps(content),digest,'fixture',db.now()))
            conn.execute("UPDATE ai_question SET state='ready' WHERE id=?",(row['id'],))
        return q,jid,row,cid,digest

    def test_solution_only_adoption_matches_retained_options(self):
        q,jid,row,cid,digest=self.candidate()
        before=db.scalar('SELECT count(*) FROM question_version WHERE question_id=?',(q,))
        self.call('/api/ai/candidates/'+cid+'/accept',{'confirmed':True,'content_hash':digest,'parts':['solution']},expect=400)
        self.assertEqual(db.scalar('SELECT count(*) FROM question_version WHERE question_id=?',(q,)),before)

    def test_resumed_question_clears_stale_failure_before_processing(self):
        q,jid,row,cid,digest=self.candidate()
        with db.transaction() as conn:
            conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(jid,))
            conn.execute("UPDATE ai_question SET state='failed',error_json=? WHERE id=?",(db.dumps({'code':'old_error'}),row['id']))
        seen=[]
        def inspect(job,question):
            current=db.one('SELECT state,error_json FROM ai_question WHERE id=?',(question['id'],))
            seen.append((current['state'],current['error_json']))
            raise provider.ProviderError('test_stop','模拟停止')
        with patch.object(worker,'process_question',side_effect=inspect):worker.run_job(db.one('SELECT * FROM ai_job WHERE id=?',(jid,)))
        self.assertEqual(seen,[('recognized',None)])

if __name__=='__main__':unittest.main()
