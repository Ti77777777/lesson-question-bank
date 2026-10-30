import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.math_notation import normalise_math_notation

class MathNotationTest(unittest.TestCase):
    def test_shared_cases_are_correct_and_idempotent(self):
        for row in json.loads((ROOT/'examples/notation-cases.json').read_text(encoding='utf-8')):
            with self.subTest(source=row['source']):
                actual=normalise_math_notation(row['source'])
                self.assertEqual(actual,row['expected'])
                self.assertEqual(normalise_math_notation(actual),actual)

    def test_ai_normalisation_repairs_derivative_without_touching_prose(self):
        from app.ai.provider import normalise
        self.assertEqual(normalise({'answer':r'$f\prime(x)=−2$','note':'正文 -2'}),
                         {'answer':r'$f^{\prime}(x)=-2$','note':'正文 -2'})

    def test_export_repairs_all_question_fields_without_changing_snapshot(self):
        import copy
        from app.latex import exbook
        snapshot={'collection':{},'sections':[], 'options':{},'items':[{'id':'I1','question_id':'Q1','question_version_id':'V1','order_no':1}],
            'question_versions':{'V1':{'question':{'id':'Q1'},'version':{'id':'V1','qtype':'solve','stem':r'$f\prime(x)$','answer':r'$g\prime(x)$','subquestions':[{'key':'1','text':r'$h\prime(x)$','answer':r'$p\prime(x)$'}]},'solutions':[{'label':'解法','steps':r'$y\prime=−1$','is_primary':1}]}}}
        before=copy.deepcopy(snapshot)
        output=exbook.render_body(snapshot,None,'teacher')
        for base in ('f','g','h','y'):self.assertIn(base+r'^{\prime}',output)
        self.assertNotIn('−',output);self.assertEqual(snapshot,before)
        snapshot['question_versions']['V1']['version']['answer']=''
        self.assertIn(r'p^{\prime}',exbook.render_body(snapshot,None,'teacher'))

if __name__=='__main__':unittest.main()
