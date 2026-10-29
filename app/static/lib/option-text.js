/**
 * 选项文本 ⇄ 选项数组。
 *
 * 历史问题：按行拆分会把一个跨行的选项（矩阵、分段函数、aligned）拆成两个选项，
 * 顺序一变，正确选项的字母就整体错位。这里改为：
 * - 以 `A:` / `A：` 开头的行才是新选项；
 * - 其余非空行并入上一个选项（保留换行），没有上一个选项时才算作无字母的选项。
 */

const LETTER = /^([A-Za-z])[:：]\s*(.*)$/;

export function parseOptionLines(text) {
  const lines = String(text ?? '').split('\n');
  const out = [];
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (!line.trim()) continue;
    const hit = line.match(LETTER);
    if (hit) {
      out.push({ key: hit[1].toUpperCase(), text: hit[2] });
    } else if (out.length) {
      out[out.length - 1].text = `${out[out.length - 1].text}\n${line.trim()}`;
    } else {
      out.push({ key: String.fromCharCode(65 + out.length), text: line.trim() });
    }
  }
  return out;
}

export function formatOptionLines(options) {
  return (Array.isArray(options) ? options : [])
    .map((opt, index) => {
      const key = (opt && opt.key ? String(opt.key) : String.fromCharCode(65 + index)).toUpperCase();
      return `${key}: ${(opt && opt.text) || ''}`;
    })
    .join('\n');
}
