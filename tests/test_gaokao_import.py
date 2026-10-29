"""真题 LaTeX 导入的结构、图片分区和安全回归。"""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.import_gaokao_tex import group, split_answer, split_options, split_subquestions, rewrite_images, copy_immutable, parse_paper
from app.latex import exbook


class GaokaoImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT/'build'/('_import-unit-'+uuid.uuid4().hex[:10])
        assert cls.work.resolve().is_relative_to((ROOT/'build').resolve())
        cls.work.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        for p in sorted(cls.work.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            p.rmdir() if p.is_dir() else p.unlink()
        cls.work.rmdir()

    def test_nested_choice_arguments_preserve_set_braces(self):
        body = r'题干 \fourchoices{$\{x\mid x>0\}$}{$\frac{1}{2}$}{C}{D}'
        stem, options = split_options(body)
        self.assertEqual(stem, '题干')
        self.assertEqual(options[0]['text'], r'$\{x\mid x>0\}$')
        self.assertEqual(len(options), 4)

    def test_picture_choices_with_letter_prefixes(self):
        stem, options = split_options('图象为\nA. 图甲\nB. 图乙\nC. 图丙\nD. 图丁')
        self.assertEqual(stem, '图象为')
        self.assertEqual([o['text'] for o in options], ['图甲','图乙','图丙','图丁'])

    def test_answer_not_swallowing_explanation_without_empty_line(self):
        answer, solution = split_answer('\\textbf{答案}：BC\n由条件可知 B、C 正确，A、D 错误。')
        self.assertEqual(answer, 'BC')
        self.assertIn('A、D', solution)

    def test_multiline_math_answer_is_not_cut_mid_formula(self):
        text = '\\textbf{答案}：$\\begin{cases}x>0\\\\\ny<1\\end{cases}$\n完整证明。'
        answer, solution = split_answer(text)
        self.assertIn(r'\end{cases}$', answer)
        self.assertEqual(solution, '完整证明。')

    def test_subquestions_and_following_given_table(self):
        stem, subs, tail, inline = split_subquestions(
            r'公共题干\begin{subqitems}\subqitem 求值；\subqitem 证明。\end{subqitems}附：已知公式。')
        self.assertEqual(stem, '公共题干')
        self.assertEqual(len(subs), 2)
        self.assertEqual(tail, '附：已知公式。')
        self.assertFalse(inline)

    def test_teacher_figures_not_appended_to_student_stem(self):
        entry = {'question': {'id': 'Q1'}, 'version': {
            'id':'QV1','qtype':'solve','stem':r'题干 \qbankimage[scale=0.3]{assets/AST1.png}',
            'answer':'结果','analysis':r'证明 \qbankimage[scale=0.4]{assets/AST2.png}',
            'teaching':{'asset_placement':'inline'}}, 'assets':[
                {'id':'AST1','rel_path':'assets/AST1.png'}, {'id':'AST2','rel_path':'assets/AST2.png'}]}
        snapshot = {'questions':{'Q1':entry}, 'items':[{'id':'ITM1','question_id':'Q1','question_version_id':'QV1'}]}
        student = exbook.render_body(snapshot, {}, 'student')
        teacher = exbook.render_body(snapshot, {}, 'teacher')
        self.assertEqual(student.count('AST1.png'), 1)
        self.assertNotIn('AST2.png', student)
        self.assertIn('AST1.png', teacher)
        self.assertIn('AST2.png', teacher)

    def test_imported_subquestion_partial_use_requires_review(self):
        from app.api.teaching import _validate_subq_keys
        from app.server import ApiError
        version = {'subquestions':[{'key':'1'},{'key':'2'}],
                   'teaching':{'subquestion_dependency_review':'pending'}}
        with self.assertRaises(ApiError):
            _validate_subq_keys(version, ['2'])
        self.assertEqual(_validate_subq_keys(version, ['1','2']), ['1','2'])

    def test_image_rewrite_keeps_source_options(self):
        text = r'如图 \imgin[0.4]{}{img/p/q01.jpg}'
        self.assertEqual(rewrite_images(text, {'img/p/q01.jpg':('AST1','assets/AST1.jpg')}),
                         r'如图 \qbankimage[scale=0.4]{assets/AST1.jpg}')

    def test_copy_does_not_overwrite_different_file(self):
        src, dest = self.work/'a.txt', self.work/'b.txt'
        src.write_text('a'); dest.write_text('b')
        with self.assertRaises(ValueError):
            copy_immutable(src, dest)
        self.assertEqual(dest.read_text(), 'b')

    def test_parse_two_choice_sections_with_single_letter_multi_answer(self):
        (self.work/'tex').mkdir(exist_ok=True)
        text = (r'\section{测试卷}\subsection{一、选择题}'
                r'\begin{qitems}[startnum=1]\begin{bbox}\qitem 题甲\fourchoices{A}{B}{C}{D}'
                '\\begin{analysis}\\textbf{答案}：A\n证明甲。\\end{analysis}\\end{bbox}\\end{qitems}'
                r'\subsection{二、选择题}\begin{qitems}[startnum=2]\begin{bbox}\qitem 题乙\fourchoices{A}{B}{C}{D}'
                '\\begin{analysis}\\textbf{答案}：B\n证明乙。\\end{analysis}\\end{bbox}\\end{qitems}')
        (self.work/'tex/test.tex').write_text(text, encoding='utf-8')
        paper = parse_paper(self.work, {'id':'test'})
        self.assertEqual([q['qtype'] for q in paper['items']], ['single','multi'])
        self.assertEqual(paper['items'][1]['answer_keys'], ['B'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
