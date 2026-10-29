/**
 * N03 统一数学编辑器：片段插入与选项解析的纯函数。
 * 这些函数不碰 DOM，因此可以在这里直接断言；页面接线由人工检查。
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { applySnippet, wrapIfOutsideMath, SNIPPETS } from '../app/static/lib/math-editor.js';
import { parseOptionLines, formatOptionLines } from '../app/static/lib/option-text.js';

test('插入片段把光标放在第一个占位符处', () => {
  const out = applySnippet({ value: 'ab', start: 1, end: 1 }, '\\frac{${}}{}');
  assert.equal(out.value, 'a\\frac{}{}b');
  // 光标落在分子花括号内：a + \frac{ 的长度
  assert.equal(out.start, 1 + '\\frac{'.length);
  assert.equal(out.start, out.end);
});

test('有选区时替换选区而不是追加', () => {
  const out = applySnippet({ value: 'abTARGETcd', start: 2, end: 8 }, '\\sqrt{${}}');
  assert.equal(out.value, 'ab\\sqrt{}cd');
  assert.equal(out.start, 2 + '\\sqrt{'.length);
});

test('片段里的多个空花括号逐个保留', () => {
  const out = applySnippet({ value: '', start: 0, end: 0 }, '\\begin{pmatrix}\n  {} & {} \\\\\n  {} & {}\n\\end{pmatrix}');
  assert.equal((out.value.match(/\{\}/g) || []).length, 4);
});

test('数学模式之外自动补一对 $，模式之内不补', () => {
  const outside = wrapIfOutsideMath({ value: '速度 ', start: 3, end: 3 }, '\\frac{${}}{}');
  assert.ok(outside.value.startsWith('速度 $\\frac{}{}$'));
  const inside = wrapIfOutsideMath({ value: '$x+$', start: 3, end: 3 }, '^{${}}');
  assert.equal(inside.value, '$x+^{}$');
});

test('片段表本身没有空项，且都带占位符或可直接插入', () => {
  assert.ok(SNIPPETS.length >= 15);
  for (const item of SNIPPETS) {
    assert.ok(item.label && item.insert, '样例缺字段');
    assert.ok(item.group);
  }
});

test('跨行选项不会被拆成两个选项', () => {
  const parsed = parseOptionLines('A: 第一行\n\\\\begin{aligned}x&=1\\\\end{aligned}\nB: 第二行');
  assert.equal(parsed.length, 2);
  assert.equal(parsed[0].key, 'A');
  assert.ok(parsed[0].text.includes('aligned'), '续行应并入上一个选项');
  assert.equal(parsed[1].key, 'B');
});

test('全角冒号与无字母行都能解析', () => {
  const parsed = parseOptionLines('A：甲\n乙\nB：丙');
  assert.equal(parsed.length, 2);
  assert.equal(parsed[0].text, '甲\n乙');
  assert.equal(parsed[1].text, '丙');
});

test('格式化与解析互为逆运算', () => {
  const options = [{ key: 'A', text: '$1$' }, { key: 'B', text: '$\\frac{1}{2}$' }];
  assert.deepEqual(parseOptionLines(formatOptionLines(options)), options);
});
