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

    def test_homonym_teachers_are_distinct_and_codes_cannot_be_reused(self):
        first=self.call('/api/teachers',{'name':'同名教师','code':'T-01','note':'南校区'})['teacher']
        conflict=self.call('/api/teachers',{'name':'同名教师','code':'T-02','note':'北校区'},status=409)
        self.assertEqual(conflict['error']['code'],'same_name')
        self.assertEqual(conflict['error']['detail']['candidates'][0]['id'],first['id'])
        second=self.call('/api/teachers',{'name':'同名教师','code':'T-02','note':'北校区','allow_same_name':True})['teacher']
        self.assertNotEqual(first['id'],second['id']);self.assertNotEqual(first['display_name'],second['display_name'])
        collision=self.call('/api/teachers',{'name':'另一教师','code':'ｔ-０１','allow_same_name':True},status=409)
        self.assertEqual(collision['error']['code'],'duplicate_code')
        self.call('/api/teachers',{'name':'控制字符编号','code':'BAD\u007f'},status=400)
        self.call('/api/teachers',{'name':'标准化扩展编号','code':'\ufb03'*80},status=400)

    def test_homonym_students_remain_separate_when_renamed_or_transferred(self):
        first_class=self.audience('class','身份甲班');second_class=self.audience('class','身份乙班')
        first=self.call('/api/audiences/'+first_class+'/members',{'name':'同名学生','code':'2027-01'})['student']
        conflict=self.call('/api/audiences/'+second_class+'/members',{'name':'同名学生','code':'2027-02'},status=409)
        self.assertEqual(conflict['error']['detail']['candidates'][0]['classes'][0]['id'],first_class)
        second=self.call('/api/audiences/'+second_class+'/members',{'name':'同名学生','code':'2027-02','allow_same_name':True})['student']
        self.assertNotEqual(first['id'],second['id'])
        assignment=self.call('/api/assignments',{'title':'保留归属的历史安排','audience_id':first['id']})['assignment']
        collection=self.call('/api/collections',{'title':'身份历史测试讲义'})['collection']
        from app.latex.build import make_snapshot
        snapshot=make_snapshot(collection['id'])
        with db.transaction() as conn:
            rid=db.next_id(conn,'RLS')
            conn.execute('INSERT INTO release(id,collection_id,snapshot,created_at) VALUES(?,?,?,?)',
                         (rid,collection['id'],db.dumps(snapshot),db.now()))
        usage=self.call('/api/usages',{'release_id':rid,'assignment_id':assignment['id'],'audience_id':first['id'],'used_on':'2026-10-30','purpose':'class','items':[]})['usage']
        updated=self.call('/api/audiences/'+first['id'],{'name':'改名后的学生','code':'2027-03','note':'仍是原档案','base_revision':first['profile_revision']},'PATCH')['audience']
        self.assertEqual(updated['id'],first['id'])
        self.members(second_class,[first['id'],second['id']]);self.members(first_class,[])
        rows=self.call('/api/assignments')['items'];self.assertEqual(next(r for r in rows if r['id']==assignment['id'])['audience_id'],first['id'])
        self.assertEqual(self.call('/api/audiences/'+second_class+'/members')['total'],2)
        saved_usage=db.one('SELECT audience_id,assignment_id FROM usage_log WHERE id=?',(usage['id'],))
        self.assertEqual(saved_usage,{'audience_id':first['id'],'assignment_id':assignment['id']})
        self.assertEqual(db.one('SELECT name FROM audience WHERE id=?',(second['id'],))['name'],'同名学生')

    def test_creation_retries_are_atomic_and_scoped(self):
        from concurrent.futures import ThreadPoolExecutor
        gate=threading.Barrier(6)
        payload={'name':'并发教师','operation_id':'identity-concurrent-teacher'}
        def submit(_):gate.wait();return self.call('/api/teachers',payload)['teacher']['id']
        with ThreadPoolExecutor(max_workers=6) as pool:ids=list(pool.map(submit,range(6)))
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(db.scalar("SELECT count(*) FROM teacher WHERE name='并发教师'"),1)
        self.call('/api/teachers',{**payload,'name':'不同内容'},status=409)
        self.call('/api/audiences',{'kind':'class','name':'不同动作','operation_id':payload['operation_id']},status=409)

    def test_member_retry_does_not_duplicate_or_reverse_a_later_removal(self):
        parent=self.audience('class','防重名单');other=self.audience('class','防重另一班')
        payload={'name':'只创建一次的学生','operation_id':'identity-member-retry'}
        student=self.call('/api/audiences/'+parent+'/members',payload)['student']['id']
        replay=self.call('/api/audiences/'+parent+'/members',payload)
        self.assertTrue(replay['replayed']);self.assertEqual(replay['student']['id'],student)
        self.assertEqual(self.call('/api/audiences/'+parent+'/members')['total'],1)
        self.call('/api/audiences/'+other+'/members',payload,status=409)
        self.members(parent,[])
        self.call('/api/audiences/'+parent+'/members',payload)
        self.assertEqual(self.call('/api/audiences/'+parent+'/members')['total'],0)

    def test_code_uniqueness_is_per_kind_and_legacy_duplicates_survive(self):
        a=self.call('/api/audiences',{'kind':'student','name':'编号学生甲','code':'S-09'})['audience']
        self.call('/api/audiences',{'kind':'student','name':'编号学生乙','code':'s-09'},status=409)
        self.call('/api/audiences',{'kind':'class','name':'编号班级','code':'S-09'})
        b=self.audience('student','旧编号学生')
        with db.transaction() as conn:conn.execute('UPDATE audience SET code=? WHERE id IN (?,?)',('LEGACY',a['id'],b))
        db.init_db()
        self.assertEqual(db.scalar("SELECT count(*) FROM audience WHERE code='LEGACY'"),2)
        self.call('/api/audiences/'+b,{'note':'编辑备注不改旧编号','code':'LEGACY'},'PATCH')
        self.call('/api/audiences',{'kind':'student','name':'新记录不能再撞号','code':'legacy'},status=409)

    def test_profile_edits_reject_stale_revision(self):
        teacher=self.call('/api/teachers',{'name':'资料冲突老师'})['teacher']
        self.call('/api/teachers/'+teacher['id'],{'note':'先保存','base_revision':teacher['profile_revision']},'PATCH')
        self.call('/api/teachers/'+teacher['id'],{'name':'过期保存','base_revision':teacher['profile_revision']},'PATCH',409)
        row=self.call('/api/audiences',{'kind':'student','name':'资料冲突学生'})['audience']
        self.call('/api/audiences/'+row['id'],{'note':'先保存','base_revision':row['profile_revision']},'PATCH')
        self.call('/api/audiences/'+row['id'],{'code':'STALE','base_revision':row['profile_revision']},'PATCH',409)
        self.assertIsNone(db.one('SELECT code FROM audience WHERE id=?',(row['id'],))['code'])

if __name__=='__main__':unittest.main()
