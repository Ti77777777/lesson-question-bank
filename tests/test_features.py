"""Workbench feature integration. Isolated DB, local fixtures, no paid model requests."""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.environ['QBANK_DB']=str(ROOT/'build/_test_features.db')
os.environ['QBANK_AI_WORKER']='0'
for suffix in ('','-wal','-shm'):Path(os.environ['QBANK_DB']+suffix).unlink(missing_ok=True)
from app import db, full_backup, search_catalog
from app.ai import documents, worker
from app.server import create_app, Handler, ApiError


class FeatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app();cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.url='http://127.0.0.1:'+str(cls.server.server_address[1])
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();db.close()

    def call(self,path,data=None,method=None,status=200,raw=None):
        request=urllib.request.Request(self.url+path,data=raw if raw is not None else json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json'})
        try:r=urllib.request.urlopen(request,timeout=15)
        except urllib.error.HTTPError as e:r=e
        with r:
            result=json.loads(r.read());self.assertEqual(r.status,status,result);return result

    def source(self,name='2026年高三模拟考'):
        return self.call('/api/sources',{'name':name,'kind':'exam'})

    def change(self,sid,changes):
        items=[{'id':sid,'changes':changes}]
        preview=self.call('/api/sources/batch-preview',{'items':items})
        return items,preview

    def question_job(self):
        s=self.source();raw=('%PDF-1.4 '+s['id']).encode()
        f=self.call('/api/ai/files',raw=raw)['file']
        j=self.call('/api/ai/jobs',{'file_ids':[f['id']],'auto_accept':False})['job_ids'][0]
        self.call('/api/ai/jobs/'+j+'/control',{'action':'pause'})
        source={'qno':'1','qtype':'blank','stem':'计算 $1+1$。','options':[],'subquestions':[],
                'pages':[1],'figure_paths':[],'statement_images':[],'reference':{},'uncertainties':[]}
        with db.transaction() as conn:
            qid=db.next_id(conn,'AIQ')
            conn.execute('INSERT INTO ai_question(id,job_id,question_key,original_qno,qtype,state,source_json) VALUES(?,?,?,?,?,?,?)',(qid,j,'1','1','blank','failed',db.dumps(source)))
        return j,qid,source

    def test_presets_persist_all_filters_and_support_rename_delete(self):
        query={'keyword':'导数','source_grade':'senior3','tags':'TAG001,TAG002','tag_mode':'exclude','think_min':'2','correction':'pending'}
        p=self.call('/api/search/presets',{'name':'高三导数','query':query})
        rows=self.call('/api/search/presets')['items'];self.assertEqual(next(r for r in rows if r['id']==p['id'])['query'],query)
        self.call('/api/search/presets/'+p['id'],{'name':'重命名','query':query},method='PATCH')
        self.call('/api/search/presets/'+p['id'],method='DELETE')
        self.assertNotIn(p['id'],[r['id'] for r in self.call('/api/search/presets')['items']])

    def test_presets_reject_invalid_fields_and_duplicates(self):
        self.call('/api/search/presets',{'name':'','query':{}},status=400)
        self.call('/api/search/presets',{'name':'bad','query':{'offset':'9'}},status=400)
        self.call('/api/search/presets',{'name':'bad','query':{'tags':[]}},status=400)
        self.call('/api/search/presets',{'name':'duplicate','query':{}})
        self.call('/api/search/presets',{'name':'duplicate','query':{}},status=409)
        self.call('/api/search/presets/MISSING',method='DELETE',status=404)

    def test_presets_keep_independent_multi_modes_and_unions(self):
        query={'multi':'category,grade,levels,year','source_category':'mock,monthly',
               'source_grade':'senior1,senior3','think_levels':'basic,advanced,unrated','source_year':'2025,2026'}
        p=self.call('/api/search/presets',{'name':'多类别多选','query':query})
        rows=self.call('/api/search/presets')['items']
        self.assertEqual(next(r for r in rows if r['id']==p['id'])['query'],query)

    def test_source_preview_apply_and_undo(self):
        s=self.source();qid=self.call('/api/questions',{'qtype':'blank','stem':'来源测试','answer':'2','occurrence':{'source_id':s['id'],'qno':'1'}})['question']['id']
        changes={'name':'某中学2027年高三模拟考试','school':'某中学','year':'2027','exam_type':'mock','grade':'senior3'}
        items,p=self.change(s['id'],changes)
        self.assertEqual(p['affected'][0]['questions'],1)
        self.assertEqual(self.call('/api/sources/'+s['id'])['name'],s['name'])
        result=self.call('/api/sources/batch-apply',{'items':items,'preview_hash':p['preview_hash']})
        row=self.call('/api/sources/'+s['id']);self.assertEqual(search_catalog.metadata(row)['year'],'2027')
        self.assertEqual(row['school'],'某中学')
        resultq=self.call('/api/questions?'+urllib.parse.urlencode({'keyword':'某中学','source_year':'2027','source_category':'mock','source_grade':'senior3'}))
        self.assertIn(qid,[r['id'] for r in resultq['items']])
        self.call('/api/source-changes/'+result['id']+'/undo',{})
        restored=self.call('/api/sources/'+s['id']);self.assertEqual(restored['name'],s['name']);self.assertIsNone(restored['school'])

    def test_source_stale_preview_and_undo_are_blocked(self):
        s=self.source();items,p=self.change(s['id'],{'grade':'senior2'})
        self.call('/api/sources/'+s['id'],{'name':'另一卷'},method='PATCH')
        self.call('/api/sources/batch-apply',{'items':items,'preview_hash':p['preview_hash']},status=409)
        items,p=self.change(s['id'],{'grade':'senior2'})
        c=self.call('/api/sources/batch-apply',{'items':items,'preview_hash':p['preview_hash']})
        self.call('/api/sources/'+s['id'],{'grade':'senior1'},method='PATCH')
        self.call('/api/source-changes/'+c['id']+'/undo',{},status=409)

    def test_source_invalid_batch_rolls_back_and_requires_preview(self):
        s=self.source()
        self.call('/api/sources/batch-preview',{'items':[{'id':s['id'],'changes':{'year':'tomorrow'}}]},status=400)
        self.call('/api/sources/batch-preview',{'items':[{'id':s['id'],'changes':{'grade':'senior9'}}]},status=400)
        items,p=self.change(s['id'],{'name':'新卷'})
        self.call('/api/sources/batch-apply',{'items':items},status=409)
        self.assertEqual(self.call('/api/sources/'+s['id'])['name'],s['name'])

    def test_graduating_class_adaptation_exam_is_a_mock(self):
        s=self.source('广西南宁市2026届普通高中毕业班第一次适应性测试数学试题')
        derived=search_catalog.metadata(s)
        self.assertEqual(derived['category'],'mock');self.assertEqual(derived['grade'],'senior3')

    def test_regions_save_new_revision_keep_old_evidence_and_do_not_queue(self):
        j,q,source=self.question_job();old_revision=1
        with db.transaction() as conn:
            conn.execute('INSERT INTO ai_candidate VALUES(?,?,?,?,?,?,?,?)',(db.next_id(conn,'AIC'),q,1,None,'{}','hash','input',db.now()))
        pages={'pages':[{'page':1,'path':'fixture','words':[]},{'page':2,'path':'fixture','words':[]}]}
        def crop(page,box,target):Path(target).parent.mkdir(parents=True,exist_ok=True);Path(target).write_bytes(b'crop')
        with patch.object(documents,'prepare',return_value=pages),patch.object(documents,'crop',side_effect=crop):
            result=self.call('/api/ai/questions/'+q+'/regions',{'revision':old_revision,'confirmed':True,'regions':[{'page':1,'bbox':[.1,.1,.9,.6]},{'page':2,'bbox':[.1,.1,.8,.4]}]})
        row=db.one('SELECT * FROM ai_question WHERE id=?',(q,));s=worker.unpack(row,'source_json')
        self.assertEqual(row['current_revision'],2);self.assertEqual(row['request_generation'],1);self.assertFalse(s['refined'])
        self.assertEqual(s['pages'],[1,2]);self.assertEqual(len(s['statement_images']),2)
        self.assertEqual(db.scalar('SELECT count(*) FROM ai_candidate WHERE ai_question_id=?',(q,)),1)
        self.assertEqual(db.scalar('SELECT state FROM ai_job WHERE id=?',(j,)),'paused');self.assertFalse(result['queued'])
        self.assertEqual(db.scalar('SELECT count(*) FROM ai_step_run'),0)

    def test_regions_reject_answer_label_stale_revision_and_invalid_page(self):
        j,q,_=self.question_job();path='/api/ai/questions/'+q+'/regions'
        payload={'revision':1,'confirmed':True,'regions':[{'page':1,'bbox':[0,0,1,.8]}]}
        pages={'pages':[{'page':1,'path':'fixture','words':[{'text':'【答案】','bbox':[.1,.2,.3,.3]}]}]}
        with patch.object(documents,'prepare',return_value=pages):
            self.call(path,payload,status=400)
            self.call(path,{**payload,'revision':0},status=409)
            self.call(path,{**payload,'regions':[{'page':99,'bbox':[0,0,1,1]}]},status=400)
        self.call(path,{**payload,'confirmed':False},status=400)
        self.call(path,{**payload,'regions':[{'page':1,'bbox':[-1,0,1,1]}]},status=400)

    def test_regions_reject_running_or_adopted_items(self):
        j,q,_=self.question_job();payload={'revision':1,'confirmed':True,'regions':[{'page':1,'bbox':[.1,.1,.8,.5]}]}
        with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(j,))
        self.call('/api/ai/questions/'+q+'/regions',payload,status=409)

        with db.transaction() as conn:
            conn.execute("UPDATE ai_job SET state='paused' WHERE id=?",(j,));conn.execute("UPDATE ai_question SET state='adopted' WHERE id=?",(q,))
        self.call('/api/ai/questions/'+q+'/regions',payload,status=409)

    def test_figures_stay_in_their_options_and_subquestions(self):
        source={'stem':'选择图象','options':[{'key':'A','text':'[[FIGURE:a]]'}],'subquestions':[{'key':'1','text':'根据[[FIGURE:b]]求值'}]}
        worker.embed_figure(source,'[[FIGURE:a]]','assets/a.png')
        self.assertNotIn('assets/a.png',source['stem'])
        self.assertEqual(source['options'][0]['text'],r'\qbankimage{assets/a.png}')
        self.assertTrue(worker.unresolved_figures(source))
        worker.embed_figure(source,'[[FIGURE:b]]','assets/b.png')
        self.assertFalse(worker.unresolved_figures(source))
        self.assertIn('assets/b.png',source['subquestions'][0]['text'])

    def test_choice_option_explanations_are_not_original_subquestions(self):
        from app.ai import contracts,provider
        q={'qtype':'multi','options':[{'key':'A'},{'key':'B'}],'subquestions':[]}
        answer={'status':'solved','answer':'A','answer_keys':['A'],'solutions':[{'label':'解法','steps':'A正确，B错误。'}], 'subanswers':[{'key':'A','answer':'正确'},{'key':'B','answer':'错误'}]}
        contracts.solved(answer,q)
        self.assertEqual(answer['subanswers'],[])
        self.assertIn('B错误',answer['solutions'][0]['steps'])
        answer['subanswers']=[{'key':'missing','answer':'未知'}]
        with self.assertRaises(provider.ProviderError):contracts.solved(answer,q)
        contracts.text(r'$x=1\tag{1}$','solution')

    def test_repeated_whole_question_answer_does_not_create_a_subpart(self):
        from app.ai import contracts,provider
        source={'qno':'14','qtype':'blank','subquestions':[]}
        answer={'status':'solved','answer':'$e$','answer_keys':[], 'solutions':[{'label':'解法','steps':'由单调性得 $a=e^b$。'}], 'subanswers':[{'key':'14','answer':'$e$'}]}
        contracts.solved(answer,source)
        self.assertEqual(answer['subanswers'],[])
        answer['subanswers']=[{'key':'14','answer':'other'}]
        with self.assertRaises(provider.ProviderError):contracts.solved(answer,source)

    def test_named_edge_region_is_normalized_without_relaxing_coordinate_validation(self):
        from app.ai import contracts,provider
        q={'qno':'1','qtype':'blank','stem':'$1+1=$','statement_regions':[{'bbox':[{'left':.1,'top':.2,'right':.8,'bottom':.4}]}]}
        contracts.question(q)
        self.assertEqual(q['statement_regions'][0]['bbox'],[.1,.2,.8,.4])
        q['statement_regions'][0]['bbox']={'left':.1,'top':.2,'right':2,'bottom':.4}
        with self.assertRaises(provider.ProviderError):contracts.question(q)

    def test_reference_page_reprint_is_not_merged_into_blind_statement(self):
        j,q,source=self.question_job()
        source['statement_images']=[{'path':'assets/original.png','page':1,'answer_free':True}]
        with db.transaction() as conn:
            conn.execute('UPDATE ai_question SET source_json=? WHERE id=?',(db.dumps(source),q))
            conn.execute("UPDATE ai_job SET state='running' WHERE id=?",(j,))
        result={'title':'数学参考答案 第2页','questions':[{'qno':'1','qtype':'blank','continuation':False,
            'stem':'答案页重印内容，不得进入盲解题面','options':[],'subquestions':[],
            'statement_regions':[{'bbox':[.1,.1,.8,.2]}],'reference_regions':[{'bbox':[.1,.2,.8,.9]}],
            'reference':{'answer':'2','analysis':'参考说明'},'figures':[],'uncertainties':[]}]}
        result['questions'].append({'qno':'1','qtype':'blank','continuation':False,'stem':'','statement_regions':[], 'reference_regions':[{'bbox':[.1,.2,.8,.9]}],'reference':{'analysis':'补充参考'}})
        try:
            with patch.object(documents,'prepare',return_value={'pages':[{'page':2,'path':'fixture','words':[]}],'sha256':'fixture'}),patch.object(worker.provider,'call',return_value=result),patch.object(worker.identity,'apply'):
                worker.recognize(db.one('SELECT * FROM ai_job WHERE id=?',(j,)))
            saved=worker.unpack(db.one('SELECT * FROM ai_question WHERE id=?',(q,)),'source_json')
            self.assertEqual(saved['stem'],source['stem']);self.assertEqual(saved['statement_images'],source['statement_images'])
            self.assertIn('参考说明',saved['reference']['analysis'])
        finally:
            with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='paused' WHERE id=?",(j,))

    def test_backup_restores_complete_separate_copy_without_credentials(self):
        live=db.DB_PATH.read_bytes()
        with full_backup.workspace_temp(ROOT/'build') as tmp,patch.object(db,'ROOT',Path(tmp)):
            r=Path(tmp)
            fixtures={'run.py':'# test runtime','app/server.py':'# server','data/schema.sql':'-- schema','assets/example.png':'figure','sources/raw.pdf':'%PDF','templates/template.tex':'template','releases/RLS1/handout.pdf':'handout','data/private/deepseek.dpapi':'SECRET'}
            for name,text in fixtures.items():p=r/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
            result=full_backup.create();manifest=full_backup.inspect(full_backup.archive(result['id']))
            self.assertNotIn('data/private/deepseek.dpapi',manifest['files'])
            self.assertTrue(set(fixtures)-{'data/private/deepseek.dpapi'}<=set(manifest['files']))
            restored=full_backup.restore(result['id']);copy=Path(restored['directory'])
            self.assertTrue(copy.is_relative_to(r));self.assertEqual((copy/'assets/example.png').read_text(),'figure')
            self.assertIn('QBANK_AI_WORKER=0',(copy/'恢复启动.bat').read_text(encoding='utf-8'))
            self.assertNotEqual(copy/'data/qbank.db',db.DB_PATH)
        self.assertEqual(db.DB_PATH.read_bytes(),live)

    def test_backup_rejects_tampering_and_unsafe_paths(self):
        with full_backup.workspace_temp(ROOT/'build') as tmp:
            path=Path(tmp)/'bad.zip'
            for name in ('../outside','app/../../outside','C:/outside','app/CON.py','app/name.','data/private/deepseek.dpapi'):
                with zipfile.ZipFile(path,'w') as z:z.writestr(name,'bad')
                with self.assertRaises(ApiError):full_backup.inspect(path)
            files={'run.py':b'run','data/qbank.db':b'fake-db','data/schema.sql':b'schema'}
            manifest={'format':'qbank-full-v1','created_at':'now','questions':0,'files':{n:{'bytes':len(b),'sha256':'bad'} for n,b in files.items()}}
            with zipfile.ZipFile(path,'w') as z:
                for n,b in files.items():z.writestr(n,b)
                z.writestr('manifest.json',json.dumps(manifest))
            with self.assertRaises(ApiError):full_backup.inspect(path)

    def test_backup_import_check_download_and_restore_confirmation(self):
        with full_backup.workspace_temp(ROOT/'build') as tmp:
            snapshot=db.backup(tmp)
            files={'run.py':b'# fixture','data/schema.sql':b'-- schema','data/qbank.db':snapshot.read_bytes()}
            manifest={'format':'qbank-full-v1','created_at':db.now(),'questions':db.scalar('SELECT count(*) FROM question'),
                      'files':{n:{'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in files.items()}}
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w') as z:
                for n,b in files.items():z.writestr(n,b)
                z.writestr('manifest.json',json.dumps(manifest))
            imported=self.call('/api/backups/import',raw=output.getvalue())
            bid=imported['id']
            self.assertTrue(self.call('/api/backups/'+bid+'/check',{})['verified'])
            self.assertIn(bid,[b['id'] for b in self.call('/api/backups')['items']])
            with urllib.request.urlopen(self.url+'/api/backups/'+bid+'/download') as response:self.assertEqual(response.read(),output.getvalue())
            self.call('/api/backups/'+bid+'/restore',{},status=400)

    def test_backup_rejects_active_job(self):
        j,q,_=self.question_job()
        with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='queued' WHERE id=?",(j,))
        self.call('/api/backups',{},status=409)
        with db.transaction() as conn:conn.execute("UPDATE ai_job SET state='paused' WHERE id=?",(j,))


if __name__=='__main__':unittest.main()
