"""纠错的版本归属、处理过程、并发与检索回归（隔离数据库）。"""
import json
import os
import sys
import threading
import unittest
import urllib.request
import urllib.error
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['QBANK_DB'] = str(ROOT / 'build/_test_corrections.db')
os.environ['QBANK_AI_WORKER'] = '0'
Path(os.environ['QBANK_DB']).parent.mkdir(exist_ok=True)
for suffix in ('', '-wal', '-shm'):
    Path(os.environ['QBANK_DB'] + suffix).unlink(missing_ok=True)
from app import db
from app.server import Handler, create_app


class CorrectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app()
        # 初始化可重复执行，不丢失记录。
        db.init_db()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_address[1])
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        db.close()

    def setUp(self):
        detail = self.call('/api/questions', {'qtype':'blank', 'stem':'纠错测试题 $x=1$', 'answer':'1'})
        self.qid = detail['question']['id']
        self.vid = detail['current_version']['id']
        self.path = f'/api/questions/{self.qid}/corrections'

    def call(self, path, body=None, method=None, expect=200):
        req = urllib.request.Request(self.base + path, method=method,
             data=json.dumps(body).encode() if body is not None else None,
             headers={'Content-Type':'application/json'})
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as err:
            response = err
        with response:
            data = json.load(response)
            self.assertEqual(response.code, expect, data)
            return data

    def report(self, **kwargs):
        return self.call(self.path, {'version_id':self.vid, 'category':'answer', 'description':'答案不完整', 'suggestion':'应包含端点', **kwargs})

    def handle(self, cid, status, expected='pending', expect=200, **kwargs):
        return self.call('/api/corrections/' + cid, {'status':status, 'expected_status':expected, 'note':'已经核对原卷', **kwargs}, method='PATCH', expect=expect)

    def test_report_preserves_content_and_deduplicates(self):
        before = self.call('/api/questions/' + self.qid)
        report = self.report()
        repeated = self.report()
        self.assertEqual(report['id'], repeated['id'])
        self.assertTrue(repeated['duplicate'])
        result = self.call(self.path)
        self.assertEqual(result['pending_count'], 1)
        self.assertEqual(result['items'][0]['version_id'], self.vid)
        self.assertEqual(len(result['items'][0]['events']), 1)
        self.assertEqual(before, self.call('/api/questions/' + self.qid))
        db.init_db()
        self.assertEqual(len(self.call(self.path)['items']), 1)

    def test_category_and_text_validation(self):
        for changes in ({'category':'unknown'}, {'description':'  '}, {'description':[]},
                        {'suggestion':'a'*5001}, {'version_id':''}):
            self.call(self.path, {'version_id':self.vid, 'category':'stem', 'description':'错字', **changes}, expect=400)
        self.call(self.path, [], method='POST', expect=400)
        self.assertEqual(self.call(self.path)['items'], [])

    def test_version_ownership(self):
        other = self.call('/api/questions', {'qtype':'blank', 'stem':'另一题', 'answer':'2'})
        foreign = other['current_version']['id']
        self.call(self.path, {'version_id':foreign, 'category':'stem', 'description':'错误'}, expect=400)
        cid = self.report()['id']
        self.handle(cid, 'resolved', fixed_version_id=foreign, expect=400)

    def test_handling_history_and_reopen(self):
        cid = self.report()['id']
        self.call(f'/api/questions/{self.qid}/versions', {'answer':'1和2', 'change_note':'补全答案'})
        vid2 = self.call('/api/questions/' + self.qid)['current_version']['id']
        self.handle(cid, 'resolved', fixed_version_id=vid2)
        item = self.call(self.path)['items'][0]
        self.assertEqual(item['version_id'], self.vid)
        self.assertEqual(item['fixed_version_no'], 2)
        self.handle(cid, 'pending', expected='resolved')
        self.handle(cid, 'dismissed')
        result = self.call(self.path)
        self.assertEqual(result['pending_count'], 0)
        item = result['items'][0]
        self.assertEqual([e['status'] for e in item['events']], ['pending','resolved','pending','dismissed'])
        self.assertEqual(item['events'][1]['fixed_version_id'], vid2)
        self.assertIsNone(item['fixed_version_id'])

    def test_reject_stale_or_invalid_handling(self):
        cid = self.report()['id']
        self.handle(cid, 'dismissed')
        self.handle(cid, 'resolved', expect=409)
        self.handle(cid, 'dismissed', expected='dismissed', expect=409)
        self.handle(cid, 'pending', expected='dismissed', note=' ', expect=400)
        self.handle(cid, 'pending', expected='dismissed', fixed_version_id=self.vid, expect=400)
        self.handle(cid, 'unknown', expect=400)
        self.assertEqual(len(self.call(self.path)['items'][0]['events']), 2)

    def test_pending_search_includes_old_version_and_clears(self):
        cid = self.report()['id']
        self.call(f'/api/questions/{self.qid}/versions', {'answer':'2', 'change_note':'修改'})
        result = self.call('/api/questions?correction=pending&keyword=' + urllib.parse.quote('纠错测试题'))
        item = next(i for i in result['items'] if i['id'] == self.qid)
        self.assertEqual(item['correction_pending'], 1)
        self.assertEqual(item['correction_count'], 1)
        self.assertNotEqual(item['version_id'], self.vid)
        self.handle(cid, 'resolved', note='已修改答案')
        self.assertNotIn(self.qid, [i['id'] for i in self.call('/api/questions?correction=pending')['items']])
        # 默认检索仍保留已处理记录数。
        item = next(i for i in self.call('/api/questions')['items'] if i['id'] == self.qid)
        self.assertEqual(item['correction_count'], 1)
        self.assertEqual(item['correction_pending'], 0)

    def test_missing_question_and_record(self):
        self.call('/api/questions/Q999999/corrections', expect=404)
        self.call('/api/questions/Q999999/corrections', {'category':'stem','description':'错','version_id':self.vid}, expect=404)
        self.handle('COR999999', 'resolved', expect=404)


if __name__ == '__main__':
    unittest.main()
