"""将 ExBook 分卷正文导入题库。默认只解析；--apply 才写入，并先备份。

不修改来源目录，不把导入等同于数学审校；同一批资料重复执行不会重复建题。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PARSER_VERSION = 1


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def uncomment(text: str) -> str:
    """注释换成等长空白，保留与源文件一致的行号和字符位置。"""
    return re.sub(r'(?<!\\)%[^\n]*', lambda m: ' '*len(m[0]), text)


def group(text: str, pos: int, opening='{', closing='}') -> tuple[str, int]:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos >= len(text) or text[pos] != opening:
        raise ValueError('应有 %s，附近为 %r' % (opening, text[pos:pos+60]))
    start, depth = pos+1, 1
    pos += 1
    while pos < len(text):
        if text[pos] == '\\' and pos+1 < len(text):
            pos += 2
            continue
        if text[pos] == opening:
            depth += 1
        elif text[pos] == closing:
            depth -= 1
            if not depth:
                return text[start:pos], pos+1
        pos += 1
    raise ValueError('括号未配对：'+text[start:start+80])


def env_spans(text: str, name: str) -> list[tuple[int, int, int, int]]:
    token = re.compile(r'\\(begin|end)\s*\{'+re.escape(name)+r'\}')
    stack, out = [], []
    for m in token.finditer(text):
        if m[1] == 'begin':
            stack.append((m.start(), m.end()))
        else:
            if not stack:
                raise ValueError('多余的环境结束：'+name)
            begin, content = stack.pop()
            if not stack:
                out.append((begin, content, m.start(), m.end()))
    if stack:
        raise ValueError('环境未关闭：'+name)
    return out


def split_answer(text: str) -> tuple[str, str]:
    text = text.strip()
    if text.startswith('['):
        _, end = group(text, 0, '[', ']')
        text = text[end:].lstrip()
    m = re.match(r'\\textbf\s*\{\s*答案\s*[:：]?\s*\}\s*[:：]?\s*', text)
    if not m:
        raise ValueError('解析没有可识别的答案标题')
    rest = text[m.end():]
    # 修订后的源文件有些答案与证明只隔一个换行，不能把整段证明吞进答案。
    for stop in re.finditer(r'\n', rest):
        part = rest[:stop.start()]
        stripped = re.sub(r'\\[{}$]', '', part)
        if (stripped.count('{') == stripped.count('}') and stripped.count('$') % 2 == 0
                and part.count(r'\[') == part.count(r'\]')
                and part.count(r'\begin{') == part.count(r'\end{')):
            return part.strip(), rest[stop.end():].strip()
    return rest.strip(), ''


def split_options(text: str) -> tuple[str, list[dict]]:
    m = re.search(r'\\(three|four|five|six)choices\b', text)
    if not m:
        marks = list(re.finditer(r'^\s*([A-F])[.．、]\s*', text, re.M))
        if len(marks) >= 3 and [x[1] for x in marks] == [chr(65+i) for i in range(len(marks))]:
            options = [{'key': mark[1], 'text': text[mark.end():marks[i+1].start()
                        if i+1 < len(marks) else len(text)].strip()} for i,mark in enumerate(marks)]
            return text[:marks[0].start()].strip(), options
        return text, []
    n = {'three': 3, 'four': 4, 'five': 5, 'six': 6}[m[1]]
    options, end = [], m.end()
    for i in range(n):
        value, end = group(text, end)
        options.append({'key': chr(65+i), 'text': value.strip()})
    return (text[:m.start()]+text[end:]).strip(), options


def split_subquestions(text: str) -> tuple[str, list[dict], str, bool]:
    spans = env_spans(text, 'subqitems')
    if not spans:
        return text.strip(), [], '', False
    if len(spans) != 1:
        # 非标准多组结构保持原文；不尝试危险的拆题。
        return text.strip(), [], '', True
    begin, content, stop, end = spans[0]
    inner = text[content:stop]
    marks, depth = [], 0
    for m in re.finditer(r'\\(begin|end)\{subqitems\}|\\subqitem\b', inner):
        if m[0].startswith(r'\begin'):
            depth += 1
        elif m[0].startswith(r'\end'):
            depth -= 1
        elif depth == 0:
            marks.append(m)
    if not marks or inner[:marks[0].start()].strip():
        return text.strip(), [], '', True
    subs = []
    for i, mark in enumerate(marks):
        stop_item = marks[i+1].start() if i+1 < len(marks) else len(inner)
        subs.append({'key': str(i+1), 'text': inner[mark.end():stop_item].strip(),
                     'depends_on': [], 'answer': ''})
    return text[:begin].strip(), subs, text[end:].strip(), False


def image_calls(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r'\\(imgin|includegraphics)\b', text):
        end = m.end()
        while end < len(text) and text[end].isspace():
            end += 1
        opt = ''
        if end < len(text) and text[end] == '[':
            opt, end = group(text, end, '[', ']')
        if m[1] == 'imgin':
            _, end = group(text, end)
            opt = 'scale='+(opt.strip() or '0.38')
        path, end = group(text, end)
        out.append({'start': m.start(), 'end': end, 'options': opt,
                    'path': path.strip().replace('\\', '/')})
    return out


def image_paths(item: dict) -> dict[str, set[str]]:
    roles = {}
    for role, texts in [('student', [item['stem'], item['tail']] +
                         [o['text'] for o in item['options']] +
                         [s['text'] for s in item['subquestions']]),
                        ('teacher', [item['answer'], item['analysis']])]:
        for text in texts:
            for call in image_calls(text):
                roles.setdefault(call['path'], set()).add(role)
    return roles


def parse_paper(root: Path, metadata: dict) -> dict:
    pid = metadata['id']
    path = root/'tex'/(pid+'.tex')
    original = path.read_text(encoding='utf-8')
    text = uncomment(original)
    heading = re.search(r'\\section\{([^}]+)\}', text)
    title = heading[1] if heading else pid
    subsections = list(re.finditer(r'\\subsection\{([^}]+)\}', text))
    groups = list(re.finditer(r'\\begin\{qitems\}\s*\[([^]]*)\]', text))
    choice_sections = [s[1] for s in subsections if '选' in s[1] and '选考' not in s[1]]
    counters = {}
    inventory_path = root/'raw'/pid/'清单.md'
    inventory = inventory_path.read_text(encoding='utf-8') if inventory_path.is_file() else ''
    group_descriptions = {}
    for line in inventory.splitlines():
        if line.startswith('- ') and '：' in line:
            key, desc = line[2:].split('：', 1)
            group_descriptions[key] = desc
    items = []
    for begin, content, stop, end in env_spans(text, 'bbox'):
        preceding = [g for g in groups if g.start() < begin]
        if not preceding:
            raise ValueError(pid+'：题目不属于 qitems')
        g = preceding[-1]
        start = re.search(r'startnum\s*=\s*(\d+)', g[1])
        if g.start() not in counters:
            counters[g.start()] = int(start[1]) if start else 1
        qno = counters[g.start()]
        counters[g.start()] += 1
        section = next((s[1] for s in reversed(subsections) if s.start() < begin), '')
        body = text[content:stop]
        qm = re.search(r'\\qitem\b', body)
        if qm is None:
            raise ValueError(pid+'：bbox 没有 qitem')
        analyses = env_spans(body, 'analysis')
        if len(analyses) != 1:
            raise ValueError(pid+'：每题应恰有一段解析')
        a, ai, az, ae = analyses[0]
        question = body[qm.end():a].strip()
        answer, analysis = split_answer(body[ai:az])
        if body[ae:].strip():
            analysis += '\n\n'+body[ae:].strip()
        question, options = split_options(question)
        if '填空' in section:
            qtype = 'blank'
        elif '解答' in section or '选考' in section:
            qtype = 'solve'
        elif ('多选' in section or '多项' in group_descriptions.get(section, '')
              or (len(choice_sections) > 1 and section == choice_sections[1])):
            qtype = 'multi'
        elif options:
            qtype = 'single'
        else:
            raise ValueError('%s Q%d：无法确定题型，分节 %s' % (pid, qno, section))
        keys = re.findall(r'[A-F]', re.sub(r'\\[A-Za-z]+', '', answer)) if options else []
        keys = list(dict.fromkeys(keys))
        valid = {o['key'] for o in options}
        if options and (not keys or not set(keys) <= valid or (qtype == 'single' and len(keys) != 1)):
            raise ValueError('%s Q%d：选择题答案无法匹配选项：%s' % (pid, qno, answer))
        stem, subs, tail, inline_subs = split_subquestions(question)
        if not stem or not answer:
            raise ValueError('%s Q%d：题干或答案为空' % (pid, qno))
        item = {'paper': pid, 'number': qno, 'section': section, 'qtype': qtype,
                'stem': stem, 'options': options, 'subquestions': subs, 'tail': tail,
                'inline_subquestions': inline_subs, 'answer': answer, 'answer_keys': keys,
                'analysis': analysis, 'source_line': original.count('\n', 0, begin)+1,
                'raw_block': original[begin:end]}
        item['images'] = {p: sorted(roles) for p, roles in image_paths(item).items()}
        for rel in item['images']:
            target = (root/rel).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError('%s Q%d：图片不存在或超出来源目录：%s' % (pid, qno, rel))
        items.append(item)
    if len(items) != len(re.findall(r'\\qitem\b', text)):
        raise ValueError(pid+'：题目计数与 qitem 数量不一致')
    numbers = [item['number'] for item in items]
    if len(set(numbers)) != len(numbers) or numbers != list(range(1, len(items)+1)):
        raise ValueError(pid+'：题号重复或不连续：'+str(numbers))
    return {'id': pid, 'title': title, 'metadata': metadata, 'tex_sha256': sha(path), 'items': items}


def make_plan(root: Path) -> dict:
    root = root.resolve()
    metadata = json.loads((root/'_build_report.json').read_text(encoding='utf-8'))
    papers = [parse_paper(root, row) for row in metadata]
    pictures = sorted({rel for p in papers for q in p['items'] for rel in q['images']})
    image_hashes = {rel: sha(root/rel) for rel in pictures}
    identity = {'parser_version': PARSER_VERSION, 'root': str(root),
                'papers': {p['id']: p['tex_sha256'] for p in papers}, 'images': image_hashes}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    items = [q for p in papers for q in p['items']]
    return {'format': 'gaokao-exbook', 'parser_version': PARSER_VERSION,
            'source_root': str(root), 'fingerprint': fingerprint, 'papers': papers,
            'image_hashes': image_hashes, 'summary': {
                'papers': len(papers), 'questions': len(items), 'qtypes': dict(Counter(q['qtype'] for q in items)),
                'unique_images': len(pictures), 'questions_with_images': sum(bool(q['images']) for q in items),
                'questions_with_subquestions': sum(bool(q['subquestions']) for q in items),
                'empty_analysis': [q['paper']+':'+str(q['number']) for q in items if not q['analysis']],
                'inline_subquestions': [q['paper']+':'+str(q['number']) for q in items if q['inline_subquestions']]}}


def rewrite_images(text: str, mapping: dict) -> str:
    for call in reversed(image_calls(text)):
        asset_id, dest = mapping[call['path']]
        opts = call['options'] or 'scale=1'
        replacement = r'\qbankimage['+opts+']{'+dest+'}'
        text = text[:call['start']]+replacement+text[call['end']:]
    return text


def copy_immutable(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        if sha(dest) != sha(source):
            raise ValueError('拒绝覆盖内容不同的文件：'+str(dest))
        return
    shutil.copy2(source, dest)


def apply_plan(plan: dict) -> dict:
    from app import db
    from app.api.content import _create_question
    db.init_db()
    for batch in db.all_("SELECT * FROM import_batch WHERE status='applied' ORDER BY created_at DESC"):
        mapping = batch.get('mapping') or {}
        if mapping.get('format') != plan['format'] or mapping.get('source_root') != plan['source_root']:
            continue
        if mapping.get('fingerprint') == plan['fingerprint']:
            result_file = db.abs_path(mapping['result_file'])
            saved = json.loads(result_file.read_text(encoding='utf-8'))
            for qid in saved['question_ids']:
                if not db.one('SELECT id FROM question WHERE id=?', (qid,)):
                    raise ValueError('既有批次缺少题目 '+qid+'，需要核对后恢复')
            return {**saved, 'skipped_existing_batch': True}
        raise ValueError('同一来源已有成功批次，但正文或图片已改变；请先比较差异，不能重复灌入整批题目')
    stamp = db.now().replace(':', '').replace('+', '_')
    backup = db.backup(ROOT/'backup'/('before-gaokao-'+stamp))
    source_root = Path(plan['source_root'])
    result = {'summary': plan['summary'], 'fingerprint': plan['fingerprint'],
              'source_root': str(source_root), 'backup': db.rel_to_root(backup),
              'question_ids': [], 'papers': [], 'asset_ids': [], 'preview_collection_id': None}
    ts = db.now()
    with db.transaction() as conn:
        claim_key = 'import-gaokao-root:'+hashlib.sha256(plan['source_root'].casefold().encode()).hexdigest()
        if conn.execute('SELECT value FROM meta WHERE key=?', (claim_key,)).fetchone():
            raise ValueError('该来源已由另一导入任务登记，请重新运行以读取已有批次')
        conn.execute('INSERT INTO meta(key,value) VALUES(?,?)', (claim_key, plan['fingerprint']))
        batch_id = db.next_id(conn, 'IMP')
        batch_dir = db.store_dir('imports')/batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir/'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
        for name in ('README.md', '待裁决清单.md', '转换规范.md'):
            copy_immutable(source_root/name, batch_dir/'provenance'/name)
        result.update({'batch_id': batch_id, 'result_file': db.rel_to_root(batch_dir/'result.json')})
        image_map = {}
        for rel, digest in plan['image_hashes'].items():
            aid = db.next_id(conn, 'AST')
            dest = db.store_dir('assets')/(aid+Path(rel).suffix.lower())
            copy_immutable(source_root/rel, dest)
            if sha(dest) != digest:
                raise ValueError('解析后图片内容发生变化，请重新解析：'+rel)
            image_map[rel] = (aid, 'assets/'+dest.name)
            roles = sorted({role for p in plan['papers'] for q in p['items'] for role in q['images'].get(rel, [])})
            conn.execute('INSERT INTO asset(id,kind,filename,rel_path,sha256,note,created_at) VALUES(?,?,?,?,?,?,?)',
                         (aid, 'original_image', Path(rel).name, db.rel_to_root(dest), digest,
                          db.dumps({'import_batch': batch_id, 'source': rel, 'roles': roles, 'placement': 'inline'}), ts))
            result['asset_ids'].append(aid)
        for paper in plan['papers']:
            pid = paper['id']
            sid = db.next_id(conn, 'SRC')
            conn.execute('INSERT INTO source(id,name,kind,edition,region,pending_note,note,created_at) VALUES(?,?,?,?,?,?,?,?)',
                         (sid, paper['title']+'（本地整理版）', 'exam', pid[:4], pid.split('-', 1)[1],
                          '由本地整理版导入；题库内知识分类、难度和数学审校待确认。',
                          '来源目录：'+str(source_root)+'；当前整理版可能含勘误，原始资料与整理稿分别保存。', ts))
            file_ids = {}
            candidates = [('tex', source_root/'tex'/(pid+'.tex'), pid+'.tex'),
                          ('original_pdf', source_root.parent/'高考真题'/paper['metadata']['pdf'], '原始资料.pdf'),
                          ('tidy_pdf', source_root/'pdf'/(paper['metadata']['no']+'-'+pid+'.pdf'), '整理版.pdf'),
                          ('report', source_root/'报告'/(pid+'.md'), '整理报告.md')]
            for role, original_file, filename in candidates:
                if not original_file.is_file():
                    continue
                dest = db.store_dir('sources')/sid/filename
                copy_immutable(original_file, dest)
                if role == 'tex' and sha(dest) != paper['tex_sha256']:
                    raise ValueError('解析后题目正文发生变化，请重新解析：'+pid)
                sfid = db.next_id(conn, 'SF')
                conn.execute('INSERT INTO source_file(id,source_id,filename,rel_path,sha256,bytes,uploaded_at,note) '
                             'VALUES(?,?,?,?,?,?,?,?)', (sfid, sid, filename, db.rel_to_root(dest), sha(dest),
                             dest.stat().st_size, ts, role+'；原路径：'+str(original_file)))
                file_ids[role] = sfid
            cid = db.next_id(conn, 'COL')
            conn.execute('INSERT INTO collection(id,title,topic,purpose,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                         (cid, paper['title']+'（导入题单）', '高考真题', 'handout',
                          '按当前正文完整导入；未经本轮独立数学审校；原题号见来源。批次 '+batch_id, ts, ts))
            sections, qids, by_number = {}, [], {}
            for order, q in enumerate(paper['items']):
                if q['section'] not in sections:
                    sec = db.next_id(conn, 'SEC')
                    conn.execute('INSERT INTO collection_section(id,collection_id,title,order_no) VALUES(?,?,?,?)',
                                 (sec, cid, q['section'], len(sections)))
                    sections[q['section']] = sec
                converted = dict(q)
                for field in ('stem', 'tail', 'answer', 'analysis'):
                    converted[field] = rewrite_images(q[field], image_map)
                converted['options'] = [{**o, 'text': rewrite_images(o['text'], image_map)} for o in q['options']]
                converted['subquestions'] = [{**s, 'text': rewrite_images(s['text'], image_map)} for s in q['subquestions']]
                teaching = {'asset_placement': 'inline', 'question_tail': converted['tail'],
                            'subquestion_dependency_review': 'pending' if q['subquestions'] else None,
                            'source_label': paper['title']+' 第'+str(q['number'])+'题',
                            'import': {'batch_id': batch_id, 'paper': pid, 'qno': q['number'],
                                       'line': q['source_line'], 'parser_version': PARSER_VERSION}}
                qid = _create_question({
                    'qtype': q['qtype'], 'short_name': pid+' 第'+str(q['number'])+'题',
                    'stem': converted['stem'], 'options': converted['options'] or None,
                    'subquestions': converted['subquestions'] or None, 'answer': converted['answer'],
                    'answer_keys': q['answer_keys'] or None, 'analysis': converted['analysis'],
                    'material_refs': [image_map[rel][0] for rel in q['images']], 'teaching': teaching,
                    'created_by': '真题 LaTeX 导入', 'change_note': '从当前整理版无改题导入；保留原始资料。',
                    'note': '批次 '+batch_id+'；来源 '+pid+' 第'+str(q['number'])+'题；分类、难度及审校待确认。',
                    'occurrence': {'source_id': sid, 'source_file_id': file_ids['tex'],
                                   'qno': str(q['number']), 'raw_text': q['raw_block'],
                                   'note': '整理稿第 '+str(q['source_line'])+' 行；PDF 页码未自动推定。'}}, conn)
                vid = conn.execute('SELECT current_version_id FROM question WHERE id=?', (qid,)).fetchone()[0]
                conn.execute("UPDATE question_version SET review_state='submitted' WHERE id=?", (vid,))
                iid = db.next_id(conn, 'ITM')
                conn.execute('INSERT INTO collection_item(id,collection_id,section_id,question_id,question_version_id,'
                             'order_no,role,created_at) VALUES(?,?,?,?,?,?,?,?)',
                             (iid, cid, sections[q['section']], qid, vid, order, 'practice', ts))
                qids.append(qid)
                by_number[str(q['number'])] = {'question_id': qid, 'version_id': vid, 'qtype': q['qtype']}
            result['question_ids'].extend(qids)
            result['papers'].append({'paper': pid, 'source_id': sid, 'collection_id': cid,
                                     'count': len(qids), 'questions': by_number, 'source_files': file_ids})
        # 用一套卷中的代表题检验公式、表格、图形、多选及多小问的完整链路。
        picked = next(p for p in result['papers'] if p['paper'] == '2025-新课标I')
        cid = db.next_id(conn, 'COL')
        conn.execute('INSERT INTO collection(id,title,topic,purpose,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                     (cid, '高考真题导入效果预览（8题）', '导入预览 · 待题库复核', 'handout',
                      '仅作导入与版式核对，不代表正式审校。批次 '+batch_id, ts, ts))
        preview_section = db.next_id(conn, 'SEC')
        conn.execute('INSERT INTO collection_section(id,collection_id,title,order_no) VALUES(?,?,?,0)',
                     (preview_section, cid, '真题导入样例'))
        for order, number in enumerate((1, 3, 5, 6, 9, 12, 16, 17)):
            selected = picked['questions'][str(number)]
            conn.execute('INSERT INTO collection_item(id,collection_id,section_id,question_id,question_version_id,order_no,'
                         'role,space_mm,page_break_before,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (db.next_id(conn, 'ITM'), cid, preview_section, selected['question_id'], selected['version_id'], order,
                          'practice', 40 if selected['qtype']=='solve' else 12, int(number in (6, 9, 17)), ts))
        result['preview_collection_id'] = cid
        mapping = {key: plan[key] for key in ('format','source_root','fingerprint','parser_version')}
        mapping['result_file'] = result['result_file']
        conn.execute('INSERT INTO import_batch(id,filename,rel_path,mapping,stats,errors,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
                     (batch_id, source_root.name, db.rel_to_root(batch_dir/'plan.json'), db.dumps(mapping),
                      db.dumps({'total': len(result['question_ids']), 'ok': len(result['question_ids']), 'failed': 0, 'skipped': 0}),
                      '[]', 'applied', ts))
        db.audit('import.gaokao_tex', 'import_batch', batch_id, {'summary': plan['summary'], 'backup': result['backup']})
        (batch_dir/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path, default=ROOT/'build/gaokao-import-plan.json')
    args = parser.parse_args()
    plan = make_plan(args.source)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    result = apply_plan(plan) if args.apply else {'dry_run': True, 'summary': plan['summary'], 'fingerprint': plan['fingerprint']}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
