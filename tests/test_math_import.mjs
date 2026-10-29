import assert from 'node:assert/strict';
import { richToHtml } from '../app/static/lib/math.js';

const table = '\\begin{center}\n\\begin{tabular}{|c|c|}\\hline\n等级 & 名称 \\\\ 2 & 轻风 \\\\ \\hline\\end{tabular}\n\\end{center}';
const html = richToHtml(table);
assert.equal((html.match(/<tr>/g) || []).length, 2);
assert.equal((html.match(/<td /g) || []).length, 4);
assert(!html.includes('tabular'));
assert(html.includes('轻风'));
assert(richToHtml('\\qbankimage[scale=.4]{assets/AST000001.png}').includes('src="/files/assets/AST000001.png"'));
assert(!richToHtml('\\qbankimage{assets/../private.png}').includes('src='));
assert(!richToHtml('<script>alert(1)</script>').includes('<script>'));
const complex = '\\begin{tabular}{cc}\\multicolumn{2}{c}{复杂表}\\end{tabular}';
assert(!richToHtml(complex).includes('<table'));
assert(!richToHtml('\\begin{tabular}{cc}只有一格\\end{tabular}').includes('<table'));
assert(richToHtml('填空\\blankbox').includes('（　）'));
console.log('10 imported-rich-text checks passed');
