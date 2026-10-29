"""检索组合：真实来源分类、同一出处约束、空难度与当前解法标签。"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['QBANK_DB'] = str(ROOT / 'build/_test_search.db')
os.environ['QBANK_AI_WORKER'] = '0'
for suffix in ('', '-wal', '-shm'):
    path = Path(os.environ['QBANK_DB'] + suffix)
    if path.exists():
        path.unlink()
from app import db
if db.DB_PATH.resolve()!=Path(os.environ['QBANK_DB']).resolve():
    raise RuntimeError('Search tests require their isolated database; run this module in a fresh Python process.')
from app.server import Handler, create_app
from app import search_catalog


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_address[1])
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.gaokao = cls.call('/api/sources', {'name': '2024年北京卷', 'kind': 'exam', 'region': '北京', 'note': '来源目录：高考真题-tex'})['id']
        cls.monthly = cls.call('/api/sources', {'name': '某中学2026～2027学年高一10月月考', 'kind': 'exam'})['id']
        cls.other = cls.call('/api/sources', {'name': '自编补充练习', 'kind': 'self'})['id']
        cls.a = cls.question(cls.gaokao, 'single', None)
        cls.b = cls.question(cls.monthly, 'solve', 3)
        cls.c = cls.question(cls.other, 'blank', 1)
        cls.d = cls.question(cls.monthly, 'multi', 4)
        # 同一题再有月考出处：不能让高考的年份与月考的类别交叉满足筛选。
        with db.transaction() as conn:
            conn.execute('INSERT INTO occurrence(id, source_id, question_id, qno, created_at) VALUES(?,?,?,?,?)',
                         (db.next_id(conn, 'O'), cls.monthly, cls.a, '2', db.now()))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        db.close()

    @classmethod
    def call(cls, path, data=None, method=None):
        request = urllib.request.Request(cls.base + path, data=json.dumps(data).encode() if data is not None else None,
                                         method=method, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    @classmethod
    def question(cls, source, qtype, think):
        q = cls.call('/api/questions', {'qtype': qtype, 'stem': '测试题', 'answer': '2',
                                       'occurrence': {'source_id': source, 'qno': '1'}})['question']['id']
        if think is not None:
            cls.call('/api/questions/' + q, {'difficulty_think': think}, 'PATCH')
        return q

    def ids(self, **query):
        data = self.call('/api/questions?' + urllib.parse.urlencode(query))
        self.assertEqual(data['total'], len(data['items']))
        return {x['id'] for x in data['items']}

    def test_catalog_uses_saved_evidence_and_distinct_questions(self):
        catalog = self.call('/api/search/catalog')
        cats = {x['id']: x['count'] for x in catalog['categories']}
        self.assertEqual({key:n for key,n in cats.items() if n}, {'gaokao': 1, 'monthly': 3, 'self': 1})
        self.assertTrue(set(search_catalog.EXAM_CATEGORIES)<=set(cats))
        self.assertEqual(cats['competition'],0)
        sources = {x['id']: x for x in catalog['sources']}
        self.assertEqual(sources[self.monthly]['year'], '2026')
        self.assertEqual(sources[self.monthly]['region'], '')
        self.assertEqual(sources[self.other]['year'], '')
        self.assertEqual(sources[self.monthly]['grade'],'senior1')
        self.assertEqual(sources[self.gaokao]['grade'],'senior3')

    def test_combined_source_filters_must_match_one_occurrence(self):
        self.assertEqual(self.ids(source_category='monthly', source_year='2024'), set())
        self.assertEqual(self.ids(source_category='monthly', source_year='2026', qtype='single,solve'), {self.a, self.b})
        self.assertEqual(self.ids(source_id=self.monthly, source_region='北京'), set())
        self.assertEqual(self.ids(source_category='monthly', source_region='_unknown'), {self.a, self.b, self.d})
        self.assertEqual(self.ids(source_year='_unknown'), {self.c})

    def test_difficulty_unrated_range_and_validation(self):
        self.assertEqual(self.ids(think_unrated=1), {self.a})
        self.assertEqual(self.ids(think_min=3, think_max=4), {self.b, self.d})
        for query in ({'think_min': 4, 'think_max': 2}, {'think_max': 6}, {'think_unrated': 1, 'think_min': 2}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.ids(**query)
            self.assertEqual(error.exception.code, 400)

    def test_search_keeps_complete_formula_for_rendered_preview(self):
        stem = '这是较长的试题背景。' * 12 + r'求 $\frac{x^2+1}{x-1}$ 的最小值。'
        qid = self.call('/api/questions', {'qtype': 'solve', 'stem': stem, 'answer': '测试'})['question']['id']
        data = self.call('/api/questions?' + urllib.parse.urlencode({'keyword': '这是较长的试题背景'}))
        item = next(x for x in data['items'] if x['id'] == qid)
        self.assertEqual(item['stem'], stem)
        self.assertLess(len(item['stem_preview']), len(stem))

    def test_tags_match_knowledge_and_current_solution_together(self):
        knowledge = self.call('/api/tags', {'dimension': 'knowledge', 'name': '集合'})['id']
        method = self.call('/api/tags', {'dimension': 'method', 'name': '数形结合'})['id']
        stale = self.call('/api/tags', {'dimension': 'method', 'name': '旧方法'})['id']
        self.call('/api/questions/' + self.b + '/tags', {'tag_id': knowledge})
        vid = db.one('SELECT current_version_id FROM question WHERE id=?', (self.b,))['current_version_id']
        self.call('/api/versions/' + vid + '/solutions', {'steps': '详细步骤', 'method_tags': [method]})
        self.assertEqual(self.ids(tags=knowledge + ',' + method, tag_mode='all'), {self.b})
        self.assertEqual(self.ids(tags=method, tag_mode='any'), {self.b})
        self.assertNotIn(self.b, self.ids(tags=method, tag_mode='exclude'))
        self.assertEqual(self.ids(tags=method, dimension='method'), {self.b})
        # 另一题升级版本后，只在旧版本的解法中追加一个标签。
        old_vid = db.one('SELECT current_version_id FROM question WHERE id=?', (self.c,))['current_version_id']
        sol = self.call('/api/versions/' + old_vid + '/solutions', {'steps': '旧解法'})['id']
        self.call('/api/questions/' + self.c + '/versions', {'qtype': 'blank', 'stem': '更新测试题', 'answer': '3'})
        with db.transaction() as conn:
            conn.execute('INSERT INTO solution_tag(solution_id,tag_id,created_at) VALUES(?,?,?)', (sol, stale, db.now()))
        self.assertEqual(self.ids(tags=stale), set())

    def test_y_exam_type_and_grade_inference(self):
        for title,category,grade in [
            ('广西南宁市2027届普通高中毕业班摸底测试数学试卷','mock','senior3'),
            ('某校高三一模数学试卷','mock','senior3'),
            ('某校高二数学学科竞赛','competition','senior2'),
            ('某校高一上学期期中考试','midterm','senior1'),
            ('某校高二下学期期末考试','final','senior2'),
            ('某校高三开学摸底考试','opening','senior3'),
            ('某校2027届测试','exam',''),
            ('高一高二联合数学竞赛','competition','')]:
            with self.subTest(title=title):
                meta=search_catalog.metadata({'id':'test','name':title,'kind':'exam'})
                self.assertEqual((meta['category'],meta['grade']),(category,grade))

    def test_z_explicit_classification_and_combined_grade_filter(self):
        self.assertEqual(self.ids(source_category='monthly',source_grade='senior3'),set())
        self.assertEqual(self.ids(source_category='monthly',source_grade='senior1'),{self.a,self.b,self.d})
        sid=self.call('/api/sources',{'name':'某地2027届普通高中毕业班摸底测试数学试卷','kind':'exam','exam_type':'mock','grade':'senior3'})['id']
        qid=self.question(sid,'solve',2)
        self.assertEqual(self.ids(source_category='mock',source_grade='senior3',source_year='_unknown'),{qid})
        self.assertEqual(self.ids(source_category='mock',source_grade='senior3',source_year='2027'),set())
        self.call('/api/sources/'+sid,{'year':'2026'},'PATCH')
        self.assertEqual(self.ids(source_category='mock',source_grade='senior3',source_year='2026'),{qid})
        self.call('/api/sources/'+sid,{'name':'更正后的试卷名称'},'PATCH')
        self.assertEqual(self.ids(source_category='mock',source_grade='senior3'),{qid})
        self.call('/api/sources/'+sid,{'exam_type':'competition','grade':'senior2'},'PATCH')
        self.assertEqual(self.ids(source_category='competition',source_grade='senior2'),{qid})
        for path,data,method in [('/api/questions?source_grade=bad',None,None),
                                  ('/api/sources/'+sid,{'exam_type':'bad'},'PATCH'),
                                  ('/api/sources/'+sid,{'grade':'bad'},'PATCH'),
                                  ('/api/sources',{'name':'错误分类','kind':'exam','grade':'bad'},None)]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.call(path,data,method)
            self.assertEqual(error.exception.code,400)
        catalog=self.call('/api/search/catalog')
        self.assertEqual(next(s for s in catalog['sources'] if s['id']==sid)['grade'],'senior2')
        self.assertEqual({g['id'] for g in catalog['grades']},{'senior1','senior2','senior3','_unknown'})


if __name__ == '__main__':
    unittest.main()
