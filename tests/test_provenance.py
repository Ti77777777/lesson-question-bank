"""Source identity survives import, edits, search, collection and frozen export."""
import json
import os
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.environ['QBANK_DB']=str(ROOT/'build/_test_provenance.db')
os.environ['QBANK_AI_WORKER']='0'
for p in (Path(os.environ['QBANK_DB']),Path(os.environ['QBANK_DB']+'-wal'),Path(os.environ['QBANK_DB']+'-shm')):
    if p.exists():p.unlink()
from app import db
from app.server import Handler,create_app
from app.ai import identity,export
from app.latex import build,exbook

HEAD1='示例第二中学 2026 年秋季学期 10 月月考（数学）\n标准解答版\n修订说明：第7题补充条件'
HEAD2='2026 年 10 月\n示例第一中学 2026～2027 学年度上学期高一月考（一）\n数学试题\n时间：120 分钟'

class ProvenanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app();cls.httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.base='http://127.0.0.1:'+str(cls.httpd.server_address[1]);threading.Thread(target=cls.httpd.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):cls.httpd.shutdown();cls.httpd.server_close();db.close()
    def call(self,path,data=None,method=None):
        payload=json.dumps(data).encode() if data is not None else None
        with urllib.request.urlopen(urllib.request.Request(self.base+path,data=payload,method=method,headers={'Content-Type':'application/json'})) as r:return json.load(r)
    def fixture(self,header=HEAD1):
        sid=self.call('/api/sources',{'name':'学生姓名10月月考','kind':'exam'})['id']
        with db.transaction() as conn:
            fid=db.next_id(conn,'SF')
            conn.execute('INSERT INTO source_file(id,source_id,filename,rel_path,uploaded_at) VALUES(?,?,?,?,?)',(fid,sid,'学生姓名10月月考.pdf','sources/test.pdf',db.now()))
        j=self.call('/api/ai/jobs',{'file_ids':[fid],'auto_accept':False})['job_ids'][0]
        q=self.call('/api/questions',{'qtype':'blank','stem':'计算 $1+1$。','answer':'$2$','short_name':'原卷第1题','occurrence':{'source_id':sid,'source_file_id':fid,'qno':'1','pdf_page':1}})['question']['id']
        identity.apply(fid,identity.extract(header))
        return sid,fid,j,q
    def test_01_actual_headers_and_visual_fallback(self):
        a=identity.extract(HEAD1);b=identity.extract(HEAD2)
        self.assertEqual(a['school'],'示例第二中学')
        self.assertEqual(a['title'],'示例第二中学2026年秋季学期10月月考（数学）标准解答版')
        self.assertEqual(b['school'],'示例第一中学');self.assertIn('2026～2027',b['title']);self.assertIn('2026年10月',b['title'])
        self.assertEqual(identity.extract('只有题目，没有学校信息'),{})
        self.assertEqual(identity.extract('',a['title'])['method'],'ai_page_title')
    def test_01b_regional_paper_without_school(self):
        header='广西南宁市2027届普通高中毕业班\n摸底测试数学试卷\n一、单选题'
        metadata=identity.extract(header)
        self.assertEqual(metadata['title'],'广西南宁市2027届普通高中毕业班摸底测试数学试卷')
        self.assertEqual(metadata['school'],'')
        self.assertEqual(metadata['region'],'广西南宁市')
        self.assertEqual(metadata['method'],'pdf_region_header')
        sid,fid,j,q=self.fixture(header)
        self.assertEqual(self.call('/api/sources/'+sid)['region'],'广西南宁市')
        self.assertIn('摸底测试',self.call('/api/questions/'+q)['question']['short_name'])

    def test_02_backfill_and_teacher_correction(self):
        sid,fid,j,q=self.fixture()
        title=identity.extract(HEAD1)['title']
        self.assertEqual(self.call('/api/questions/'+q)['question']['short_name'],title+' · 第1题')
        self.assertEqual(self.call('/api/ai/jobs/'+j)['source_document']['name'],title)
        self.call('/api/sources/'+sid,{'name':'南宁三十三中 · 2026年10月月考'},'PATCH')
        identity.apply(fid,identity.extract(HEAD1))
        self.assertEqual(self.call('/api/sources/'+sid)['name'],'南宁三十三中 · 2026年10月月考')
        self.assertEqual(self.call('/api/questions/'+q)['question']['short_name'],'南宁三十三中 · 2026年10月月考 · 第1题')
    def test_03_search_custom_title_and_source_filter(self):
        sid,fid,j,q=self.fixture(HEAD2)
        self.call('/api/questions/'+q,{'short_name':'集合运算例题'},'PATCH')
        data=self.call('/api/questions?'+urllib.parse.urlencode({'keyword':'示例第一中学','source_id':sid}))
        self.assertEqual([x['id'] for x in data['items']],[q]);self.assertIn('示例第一中学',data['items'][0]['source_label'])
        self.assertEqual(data['items'][0]['sources'][0]['qno'],'1')
        self.call('/api/sources/'+sid,{'name':'示例第一中学2026年10月月考（一）'},'PATCH')
        self.assertEqual(self.call('/api/questions/'+q)['question']['short_name'],'集合运算例题')
    def test_04_collection_and_export_snapshot_keep_source(self):
        sid,fid,j,q=self.fixture()
        col=self.call('/api/collections',{'title':'来源测试题单'})['collection']['id']
        self.call('/api/collections/'+col+'/items',{'question_id':q,'role':'example'})
        current=self.call('/api/collections/'+col)
        self.assertIn('示例第二中学',current['items'][0]['question']['source_label'])
        snap=build.make_snapshot(col,{'show_sources':True});rendered=exbook.render_body(snap,snap['options'],'student')
        self.assertIn('示例第二中学',rendered);self.assertIn('第1题',rendered)
        self.call('/api/sources/'+sid,{'name':'教师重新命名来源'},'PATCH')
        self.assertEqual(exbook.render_body(snap,snap['options'],'student'),rendered)

if __name__=='__main__':unittest.main()
