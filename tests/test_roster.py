"""Teacher ownership, class rosters and migration compatibility; no paid calls."""
import json,os,sys,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['QBANK_DB']=str(ROOT/'build/_test_roster.db');os.environ['QBANK_AI_WORKER']='0'
for suffix in ('','-wal','-shm'):Path(os.environ['QBANK_DB']+suffix).unlink(missing_ok=True)
from app import db
from app.server import Handler,create_app

class RosterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app();cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.base='http://127.0.0.1:'+str(cls.server.server_address[1])
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();db.close()
    def call(self,path,body=None,method=None,status=200):
        request=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request) as response:code,data=response.status,json.load(response)
        except urllib.error.HTTPError as e:code,data=e.code,json.load(e)
        self.assertEqual(code,status,data);return data
    def teacher(self,name):return self.call('/api/teachers',{'name':name})['teacher']['id']
    def audience(self,kind,name,teachers=None):
        body={'kind':kind,'name':name}
        if teachers is not None:body['teacher_ids']=teachers
        return self.call('/api/audiences',body)['audience']['id']
    def members(self,cid,students,revision=None,status=200):
        revision=revision or self.call('/api/audiences/'+cid+'/members')['revision']
        return self.call('/api/audiences/'+cid+'/members',{'base_revision':revision,'student_ids':students},'PUT',status)
    def filtered(self,tid):return {a['id'] for a in self.call('/api/audiences?teacher_id='+tid)['items']}
    def test_shared_classes_and_multi_class_students(self):
        t1,t2=self.teacher('甲老师'),self.teacher('乙老师')
        c1=self.audience('class','共享班',[t1,t2]);c2=self.audience('group','学习组',[t2]);s=self.audience('student','学生甲')
        self.members(c1,[s]);self.members(c2,[s])
        self.assertTrue({c1,s}<=self.filtered(t1));self.assertNotIn(c2,self.filtered(t1))
        self.assertTrue({c1,c2,s}<=self.filtered(t2))
        item=next(a for a in self.call('/api/audiences')['items'] if a['id']==s)
        self.assertEqual({c['id'] for c in item['classes']},{c1,c2})
        self.members(c1,[]);self.assertNotIn(s,self.filtered(t1));self.assertIn(s,self.filtered(t2))
        self.assertIsNotNone(db.one('SELECT id FROM audience WHERE id=?',(s,)))
    def test_direct_student_and_unassigned_legacy_data(self):
        tid=self.teacher('一对一老师');s=self.audience('student','一对一学生',[tid]);old=self.audience('class','旧班级')
        self.assertIn(s,self.filtered(tid));self.assertIn(old,self.filtered('_unassigned'))
        db.init_db();self.assertEqual(db.one('SELECT name FROM audience WHERE id=?',(old,))['name'],'旧班级')
        self.assertFalse(db.scalar('SELECT count(*) FROM audience_teacher WHERE audience_id=?',(old,)))
        self.call('/api/teachers/'+tid,{'name':'更名老师'},'PATCH')
        item=next(a for a in self.call('/api/audiences?teacher_id='+tid)['items'] if a['id']==s)
        self.assertEqual(item['teachers'][0]['name'],'更名老师')
    def test_stale_membership_edits_rejected_and_invalid_lists_rollback(self):
        c=self.audience('class','冲突班');s1=self.audience('student','学生1');s2=self.audience('student','学生2')
        rev=self.call('/api/audiences/'+c+'/members')['revision']
        self.members(c,[s1]);self.members(c,[s2],rev,409)
        self.members(c,[s1,s1],status=400);self.members(c,[c],status=400)
        self.members(c,['AUD_MISSING'],status=404)
        self.assertEqual([s['id'] for s in self.call('/api/audiences/'+c+'/members')['items']],[s1])
        self.call('/api/audiences/'+s1,{'kind':'class'},'PATCH',409)
        self.call('/api/audiences/'+c,{'kind':'student'},'PATCH',409)
    def test_teacher_assignment_conflict_and_creation_atomicity(self):
        tid=self.teacher('归属老师');c=self.audience('class','归属班')
        rev=self.call('/api/audiences/'+c+'/teachers')['revision']
        path='/api/audiences/'+c+'/teachers'
        self.call(path,{'base_revision':rev,'teacher_ids':[tid]},'PUT')
        self.call(path,{'base_revision':rev,'teacher_ids':[]},'PUT',409)
        count=db.scalar('SELECT count(*) FROM audience')
        self.call('/api/audiences',{'kind':'class','name':'不得残留','teacher_ids':['TCH_MISSING']},status=404)
        self.assertEqual(db.scalar('SELECT count(*) FROM audience'),count)
        self.assertIn(c,self.filtered(tid))
    def test_new_student_is_atomically_enrolled_and_kinds_are_validated(self):
        c=self.audience('group','名单组');s=self.call('/api/audiences/'+c+'/members',{'name':'新增学生'})['student']['id']
        self.assertEqual(self.call('/api/audiences/'+c+'/members')['total'],1)
        self.call('/api/audiences/'+s+'/members',{'name':'无效嵌套'},status=400)
        self.call('/api/teachers',{'name':{}},status=400)
        self.call('/api/audiences/'+s,{'kind':None},'PATCH',400)
        self.call('/api/audiences/'+s,{'name':{}},'PATCH',400)
        self.call('/api/audiences?teacher_id=TCH_MISSING',status=404)
    def test_backups_are_database_scoped_and_unique(self):
        first,second=db.backup(),db.backup()
        try:
            self.assertEqual(first.parent,db.store_dir('backup'))
            self.assertNotEqual(first,second);self.assertTrue(first.name.startswith(db.DB_PATH.stem+'-'))
        finally:
            first.unlink();second.unlink()

if __name__=='__main__':unittest.main()
