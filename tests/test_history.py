"""版本、发布快照和教学统计的回归检查，使用独立临时库。"""
from __future__ import annotations

import json
import os
import sys
import uuid
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'build').mkdir(exist_ok=True)
WORK = ROOT / 'build' / ('_history-' + uuid.uuid4().hex[:12])
assert WORK.resolve().is_relative_to((ROOT / 'build').resolve())
WORK.mkdir()
os.environ['QBANK_DB'] = str(WORK / 'history.db')
# 这本库不测 AI 队列；启动后台队列会长期占用 history.ai.lock，导致临时目录删不掉。
os.environ['QBANK_AI_WORKER'] = '0'
sys.path.insert(0, str(ROOT))

from app import db
from app.server import Handler, create_app
from app.latex.build import make_snapshot
from app.latex import exbook


class HistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app()
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.base = 'http://127.0.0.1:%d' % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        db.close()
        for path in sorted(WORK.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        WORK.rmdir()

    def call(self, method, path, body=None, status=200):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base+path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                actual, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            actual, raw = exc.code, exc.read()
        parsed = json.loads(raw)
        self.assertEqual(actual, status, (method, path, parsed))
        return parsed

    def question(self, title='初始题面'):
        return self.call('POST', '/api/questions', {
            'qtype': 'solve', 'stem': title, 'answer': '初始答案',
            'analysis': '完整解析', 'short_name': title,
            'occurrence': {'pending': 1}})

    def collection(self, q):
        col = self.call('POST', '/api/collections', {'title': '原讲义名称', 'duration_min': 90})['collection']
        item = self.call('POST', '/api/collections/'+col['id']+'/items',
                         {'question_id': q['question']['id']})['item']
        return col, item

    def release(self, cid):
        snapshot = make_snapshot(cid)
        with db.transaction() as conn:
            rid = db.next_id(conn, 'RLS')
            conn.execute('INSERT INTO release(id,collection_id,snapshot,created_at) VALUES(?,?,?,?)',
                         (rid, cid, db.dumps(snapshot), db.now()))
        return rid

    def audience(self):
        return self.call('POST', '/api/audiences', {'kind': 'class', 'name': '回归测试班', 'allow_same_name': True})['audience']['id']

    def usage(self, rid, aid, items=None, status=200):
        return self.call('POST', '/api/usages', {'release_id': rid, 'audience_id': aid,
                         'used_on': db.today(), 'purpose': 'class', 'items': items}, status)

    def test_same_question_two_versions_render_exact_content(self):
        q = self.question('第一版本题面')
        col, old = self.collection(q)
        v2 = self.call('POST', '/api/questions/'+q['question']['id']+'/versions',
                       {'stem': '第二版本题面', 'answer': '第二版本答案', 'change_note': '补充题意'})
        self.call('POST', '/api/collections/'+col['id']+'/items',
                  {'question_id': q['question']['id'], 'question_version_id': v2['current_version']['id']})
        snapshot = make_snapshot(col['id'])
        self.assertEqual(len(snapshot['question_versions']), 2)
        body = exbook.render_body(snapshot, None, 'teacher')
        for marker in ('第一版本题面', '第二版本题面', '初始答案', '第二版本答案'):
            self.assertEqual(body.count(marker), 1)

    def test_legacy_snapshot_does_not_substitute_another_version(self):
        from app.snapshots import entry_for_item
        snap = {'questions': {'Q1': {'version': {'id': 'QV1'}}}}
        with self.assertRaisesRegex(ValueError, 'QV2'):
            entry_for_item(snap, {'id': 'ITM1', 'question_id': 'Q1', 'question_version_id': 'QV2'})
        self.assertEqual(entry_for_item(snap, {'question_id': 'Q1', 'question_version_id': 'QV1'})['version']['id'], 'QV1')

    def test_partial_use_survives_collection_edits(self):
        q = self.question('历史题面')
        col, item = self.collection(q)
        rid, aid = self.release(col['id']), self.audience()
        usage = self.usage(rid, aid, [item['id']])['usage']
        self.call('DELETE', '/api/items/'+item['id'])
        added = self.question('后来加入的题目')
        self.call('POST', '/api/collections/'+col['id']+'/items', {'question_id': added['question']['id']})
        self.call('PATCH', '/api/collections/'+col['id'], {'title': '后来改过的讲义名'})
        self.call('POST', '/api/questions/'+q['question']['id']+'/versions',
                  {'stem': '后来修改的题面', 'change_note': '修订'})
        self.call('PATCH', '/api/usages/'+usage['id'], {'note': '原条目已经从草稿删除'})
        self.usage(rid, aid, [item['id']])
        recent = self.call('GET', '/api/audiences/'+aid+'/recent?days=30')['items']
        self.assertEqual([r['question_id'] for r in recent], [q['question']['id']])
        self.assertEqual(recent[0]['stem_preview'], '历史题面')
        self.assertEqual(recent[0]['collection_title'], '原讲义名称')
        detail = self.call('GET', '/api/questions/'+q['question']['id'])
        self.assertEqual(len(detail['usage']), 2)
        self.assertFalse(self.call('GET', '/api/questions/'+added['question']['id'])['usage'])
        found = self.call('GET', '/api/questions?exclude_audience='+aid+'&exclude_days=30')['items']
        ids = {r['id'] for r in found}
        self.assertNotIn(q['question']['id'], ids)
        self.assertIn(added['question']['id'], ids)

    def test_use_rejects_items_not_in_release_on_create_and_update(self):
        q = self.question()
        col, item = self.collection(q)
        rid, aid = self.release(col['id']), self.audience()
        added = self.question('发布后才新增')
        new_item = self.call('POST', '/api/collections/'+col['id']+'/items',
                             {'question_id': added['question']['id']})['item']
        self.usage(rid, aid, [new_item['id']], status=400)
        uid = self.usage(rid, aid, [item['id']])['usage']['id']
        self.call('PATCH', '/api/usages/'+uid, {'items': [new_item['id']]}, status=400)

    def test_empty_selection_does_not_mean_all_questions(self):
        q = self.question()
        col, _ = self.collection(q)
        rid, aid = self.release(col['id']), self.audience()
        self.usage(rid, aid, [])
        self.assertFalse(self.call('GET', '/api/audiences/'+aid+'/recent')['items'])
        self.usage(rid, aid, None)
        self.assertEqual(len(self.call('GET', '/api/audiences/'+aid+'/recent')['items']), 1)

    def test_selected_solution_controls_method_coverage_and_review(self):
        q = self.question()
        vid = q['current_version']['id']
        solutions, tags = [], []
        for name in ('回归方法甲', '回归方法乙'):
            tid = self.call('POST', '/api/tags', {'dimension': 'method', 'name': name})['id']
            sid = self.call('POST', '/api/versions/'+vid+'/solutions', {'label': name, 'steps': '解答步骤'})['id']
            self.call('POST', '/api/solutions/'+sid+'/tags', {'tag_id': tid})
            solutions.append(sid)
            tags.append(tid)
        for kind in ('stem', 'answer'):
            self.call('POST', '/api/reviews', {'target_type': 'question_version', 'target_id': vid,
                      'item': kind, 'result': 'pass'})
        self.call('POST', '/api/reviews', {'target_type': 'solution', 'target_id': solutions[0],
                  'item': 'solution', 'result': 'pass'})
        col, item = self.collection(q)
        self.call('PATCH', '/api/items/'+item['id'], {'solution_id': solutions[0]})
        self.call('PATCH', '/api/collections/'+col['id'], {'coverage': {'method': [tags[1]]}})
        check = self.call('GET', '/api/collections/'+col['id']+'/check')
        self.assertEqual([m['id'] for m in check['coverage']['method']], [tags[0]])
        self.assertTrue(any('回归方法乙' in s for s in check['missing']))
        detail = self.call('GET', '/api/collections/'+col['id'])
        self.assertTrue(detail['items'][0]['usable'])
        self.call('PATCH', '/api/items/'+item['id'], {'solution_id': solutions[1]})
        detail = self.call('GET', '/api/collections/'+col['id'])
        self.assertFalse(detail['items'][0]['usable'])

    def test_optional_and_points_are_visible_in_both_editions(self):
        q = self.question()
        col, item = self.collection(q)
        self.call('PATCH', '/api/items/'+item['id'], {'required': 0, 'points': 8})
        snapshot = make_snapshot(col['id'])
        for version in ('student', 'teacher'):
            body = exbook.render_body(snapshot, None, version)
            self.assertIn('选做', body)
            self.assertIn('8 分', body)

    def test_rebuild_prefers_archived_template_and_figure(self):
        from app.latex.build import _copy_template, _copy_assets
        archive, work = WORK/'archive', WORK/'render'
        archive.mkdir()
        work.mkdir()
        (archive/'ExBook.cls').write_text('frozen template', encoding='utf-8')
        for name in (exbook.COVER_FILE, exbook.WATER_FILE):
            target = archive/name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(b'frozen template image')
        (archive/'assets').mkdir()
        (archive/'assets/AST999999.png').write_bytes(b'frozen question image')
        self.assertFalse(_copy_template(work, archive))
        snapshot = {'questions': {'Q1': {'assets': [{'id': 'AST999999',
                     'rel_path': 'not-present/figure.png'}]}}}
        available, notes = _copy_assets(snapshot, work, archive)
        self.assertEqual(available, {'AST999999'})
        self.assertFalse(notes)
        self.assertEqual((work/'ExBook.cls').read_text(), 'frozen template')
        self.assertEqual((work/'assets/AST999999.png').read_bytes(), b'frozen question image')


if __name__ == '__main__':
    unittest.main(verbosity=2)
