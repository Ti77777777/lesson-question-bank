import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.text_preview import preview,MATH

class PreviewTest(unittest.TestCase):
    def test_formula_crossing_cutoff_is_preserved_whole(self):
        text='已知 '+r'$\frac{x+1}{x-1}=2$'+'，求实数 x 的值。'
        out=preview(text,10)
        self.assertIn(r'$\frac{x+1}{x-1}=2$',out)
        self.assertTrue(out.endswith('…'))

    def test_image_commands_become_a_readable_marker(self):
        out=preview(r'选择图象。\qbankimage[scale=.5]{assets/AST000215.png}继续',12)
        self.assertIn('【题图】',out);self.assertNotIn('qbankimage',out);self.assertNotIn('AST000',out)

    def test_display_and_parenthesized_math_are_atomic(self):
        for token in [r'\[x^2+2x+1=0\]',r'\(x^2+2x+1=0\)',r'$$x^2+2x+1=0$$']:
            with self.subTest(token=token):
                self.assertIn(token,preview('设'+token+'，求解。',6))
        out=preview('$'+('x+'*300)+'1$ 后文',6)
        self.assertIn('【公式，点开查看】',out);self.assertNotIn('$',out)

    def test_plain_text_empty_and_escaped_dollar(self):
        self.assertEqual(preview(None),'')
        self.assertEqual(preview('abcdef',3),'abc…')
        self.assertEqual(preview(r'价格\$5'),'价格\\$5')

if __name__ == '__main__':
    unittest.main()
