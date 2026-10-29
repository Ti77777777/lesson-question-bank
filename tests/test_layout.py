"""Preview/export agreement and draft isolation; no external compiler in unit tests."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
(ROOT/'build').mkdir(exist_ok=True)
os.environ['QBANK_DB'] = str(ROOT/'build/_test_layout.db')
os.environ['QBANK_AI_WORKER'] = '0'
for suffix in ('', '-wal', '-shm'): Path(os.environ['QBANK_DB']+suffix).unlink(missing_ok=True)
from app import db, layout
from app.server import create_app, Request, ApiError
from app.api import teaching, content
from app.api import layout as api_layout
from app.latex import build, exbook


def req(params=None, body=None):
    r = Request('POST', '/', {}, json.dumps(body or {}).encode(), {})
    r.params = params or {}
    return r


class LayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app()
        cls.build_root=patch.object(build,'BUILD_ROOT',ROOT/'build/_layout_test_output');cls.build_root.start()
    @classmethod
    def tearDownClass(cls):
        cls.build_root.stop();db.close()

    def setUp(self):
        result = teaching.collection_create(req(body={'title': '排版测试讲义'}))
        self.cid = result['collection']['id']
        with db.transaction() as conn:
            self.qid = content._create_question({'qtype': 'blank', 'stem': r'计算 $1+1$。', 'answer': 'PRIVATE_ANSWER', 'analysis': 'PRIVATE_ANALYSIS'}, conn)
        item = teaching.item_create(req({'cid': self.cid}, {'question_id': self.qid, 'space_mm': 15}))
        self.iid = item['item']['id']

    def test_saved_layout_drives_snapshot_and_student_export(self):
        cfg = {'options': {'cover': False, 'title': '新标题', 'options_layout': 'vertical'}, 'items': {self.iid: {'space_mm': 45, 'page_break_before': True, 'keep_together': True}}}
        api_layout.save(req({'cid': self.cid}, cfg))
        snap = build.make_snapshot(self.cid)
        self.assertEqual(snap['options']['title'], '新标题')
        self.assertEqual(snap['items'][0]['space_mm'], 45)
        self.assertEqual(db.scalar('SELECT space_mm FROM collection_item WHERE id=?',(self.iid,)),15)
        body = exbook.render_body(snap, snap['options'], 'student')
        self.assertIn(r'\answerspace{45}', body)
        self.assertIn(r'\begin{samepage}', body)
        self.assertNotIn('PRIVATE_ANSWER', body); self.assertNotIn('PRIVATE_ANALYSIS', body)
        self.assertNotIn(r'\maketitle', exbook.render_document(snap, snap['options'], 'student'))
        teacher = exbook.render_body(snap, snap['options'], 'teacher')
        self.assertIn('PRIVATE_ANSWER', teacher)
        self.assertIn('PRIVATE_ANALYSIS', teacher)

    def test_draft_reset_reverts_saved_overrides_without_saving(self):
        api_layout.save(req({'cid': self.cid}, {'items': {self.iid: {'space_mm': 85}}}))
        draft,_ = api_layout.snapshot(self.cid, {'options': {'cover': True}, 'items': {}})
        self.assertEqual(draft['items'][0]['space_mm'], 15)
        self.assertEqual(build.make_snapshot(self.cid)['items'][0]['space_mm'],85)

    def test_invalid_foreign_items_and_unbounded_dimensions_rejected(self):
        cases = [({'items': {'ITM_FOREIGN': {'space_mm': 10}}},409), ({'items':{self.iid:{'space_mm':201}}},400),
                 ({'items':{self.iid:{'space_mm':float('nan')}}},400), ({'items':{self.iid:{'page_break_before':'true'}}},400),
                 ({'options':{'cover':'false'}},400), ({'options':{'format':'untrusted'}},400), ({'options':{'unknown':True}},400)]
        for value,status in cases:
            with self.subTest(value=value),self.assertRaises(ApiError) as exc: layout.validate(value,{self.iid})
            self.assertEqual(exc.exception.status,status)

    def test_clone_remaps_layout_item_ids(self):
        api_layout.save(req({'cid':self.cid},{'options':{'cover':False},'items':{self.iid:{'space_mm':70}}}))
        clone=teaching.collection_clone(req({'cid':self.cid}))
        new_cid=clone['collection']['id']; snap=build.make_snapshot(new_cid)
        self.assertNotEqual(snap['items'][0]['id'],self.iid)
        self.assertEqual(snap['items'][0]['space_mm'],70)
        self.assertFalse(snap['options']['cover'])

    def test_pdf_preview_does_not_register_release(self):
        original=db.scalar('SELECT count(*) FROM release')
        def compile(work,env,tools,version):
            self.assertEqual(Path(env['TEMP']),work/'tmp'); self.assertEqual(env['TEMP'],env['TMP'])
            (work/(version+'.pdf')).write_bytes(b'%PDF-test')
            return {'ok':True,'pages':2,'stage':'done','output':''}
        with patch.object(build,'_tools',return_value={'xelatex':'x','xdvipdfmx':'y'}), patch.object(build,'_compile_version',side_effect=compile):
            result=api_layout.pdf(req({'cid':self.cid},{'version':'student','layout':{'options':{'cover':False}}}))
        self.assertFalse(result['release_created']); self.assertEqual(result['pages'],2)
        self.assertEqual(db.scalar('SELECT count(*) FROM release'),original)
        parts=result['url'].split('/')
        status,ctype,data=api_layout.file(req({'token':parts[-2],'version':parts[-1]}))
        self.assertEqual((status,ctype,data),(200,'application/pdf',b'%PDF-test'))

    def test_preview_file_route_is_scoped(self):
        for token,version in [('..','student'),('a'*32,'../../data/qbank.db'),('b'*32,'student')]:
            with self.assertRaises(ApiError):api_layout.file(req({'token':token,'version':version}))

    def test_original_item_editor_wins_over_saved_layout_for_shared_fields(self):
        api_layout.save(req({'cid':self.cid},{'items':{self.iid:{'space_mm':80,'keep_together':True}}}))
        teaching.item_update(req({'iid':self.iid},{'space_mm':25}))
        item=build.make_snapshot(self.cid)['items'][0]
        self.assertEqual(item['space_mm'],25);self.assertTrue(item['keep_together'])

    def test_deleted_items_do_not_leave_unusable_layout_settings(self):
        api_layout.save(req({'cid':self.cid},{'items':{self.iid:{'space_mm':80}}}))
        teaching.item_delete(req({'iid':self.iid}))
        value=api_layout.view(req({'cid':self.cid}))
        self.assertEqual(value['layout']['items'],{})
        api_layout.save(req({'cid':self.cid},value['layout']))

    def test_vertical_choices_do_not_use_horizontal_macro(self):
        options=[{'text':str(i)} for i in range(4)]
        self.assertNotIn('qbankfourchoices',exbook._options_latex(options,'vertical'))
        self.assertIn('qbankfourchoices',exbook._options_latex(options,'auto'))

    def test_section_page_break_keeps_heading_with_question(self):
        snap=build.make_snapshot(self.cid)
        first=snap['items'][0];first['section_id']='A'
        snap['sections']=[{'id':'A','title':'第一节'},{'id':'B','title':'第二节'}]
        snap['items'].append(dict(first,id='another-item',section_id='B',order_no=2,page_break_before=True))
        body=exbook.render_body(snap,snap['options'],'student')
        self.assertLess(body.index(r'\clearpage'),body.index(r'\section{第二节}'))
        self.assertEqual(body.count(r'\clearpage'),1)


if __name__=='__main__':unittest.main()
