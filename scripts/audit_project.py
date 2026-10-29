"""只读检查题库内容与结构；不会初始化或修改数据库。"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inspect_database(path: Path, plan: dict | None = None) -> dict:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {t: conn.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]
                  for t in tables}
        questions = [dict(r) for r in conn.execute(
            "SELECT q.id, q.short_name, q.difficulty_calc, q.difficulty_think, "
            "q.difficulty_scope, q.rec_level, v.id version_id, v.qtype, v.stem, "
            "v.answer, v.analysis, v.material_refs, v.teaching "
            "FROM question q LEFT JOIN question_version v ON v.id=q.current_version_id "
            "WHERE q.status='active' AND q.merged_into IS NULL ORDER BY q.id")]
        primary = {r[0] for r in conn.execute(
            "SELECT qt.question_id FROM question_tag qt JOIN tag t ON t.id=qt.tag_id "
            "WHERE qt.role='primary' AND t.dimension='knowledge'")}
        sourced = {r[0] for r in conn.execute(
            "SELECT DISTINCT question_id FROM occurrence WHERE source_id IS NOT NULL AND pending=0")}
        reviewed = {}
        for r in conn.execute("SELECT * FROM review ORDER BY created_at, id"):
            reviewed[(r['target_type'], r['target_id'], r['item'])] = r['result']
        gaps = {k: [] for k in ('no_primary_knowledge', 'unconfirmed_source',
                                'missing_answer', 'missing_solution', 'unreviewed_core',
                                'unrated_difficulty', 'missing_material_file')}
        inspected = []
        for q in questions:
            qid, vid = q['id'], q['version_id']
            solutions = [dict(r) for r in conn.execute(
                'SELECT id, steps FROM solution WHERE question_version_id=?', (vid,))]
            answer = str(q['answer'] or '').strip()
            if not answer:
                row = conn.execute('SELECT answer_keys, subquestions FROM question_version WHERE id=?', (vid,)).fetchone()
                if row:
                    keys = json.loads(row['answer_keys'] or '[]')
                    subs = json.loads(row['subquestions'] or '[]')
                    answer = 'structured' if keys or (subs and all(str(s.get('answer') or '').strip() for s in subs)) else ''
            has_solution = bool(solutions) or bool(str(q['analysis'] or '').strip())
            core_pass = all(reviewed.get(('question_version', vid, item)) == 'pass'
                            for item in ('stem', 'answer'))
            all_solutions_pass = (all(reviewed.get(('solution', sol['id'], 'solution')) == 'pass'
                                      and str(sol['steps'] or '').strip() for sol in solutions)
                                  if solutions else bool(str(q['analysis'] or '').strip())
                                  and reviewed.get(('question_version', vid, 'solution')) == 'pass')
            checks = {'no_primary_knowledge': qid not in primary,
                      'unconfirmed_source': qid not in sourced,
                      'missing_answer': not answer,
                      'missing_solution': not has_solution,
                      'unreviewed_core': not (core_pass and all_solutions_pass),
                      'unrated_difficulty': any(q[k] is None for k in
                                                ('difficulty_calc','difficulty_think','difficulty_scope'))}
            refs = json.loads(q['material_refs'] or '[]')
            missing_assets = []
            for aid in refs:
                asset = conn.execute('SELECT rel_path FROM asset WHERE id=?', (aid,)).fetchone()
                if asset is None or not asset['rel_path'] or not (ROOT / asset['rel_path']).is_file():
                    missing_assets.append(aid)
            checks['missing_material_file'] = bool(missing_assets)
            for key, missing in checks.items():
                if missing:
                    gaps[key].append(qid)
            inspected.append({'id': qid, 'name': q['short_name'], 'version_id': vid,
                              'qtype': q['qtype'], 'stem': q['stem'], 'answer': q['answer'],
                              'solution_count': len(solutions), 'gaps': [k for k,v in checks.items() if v]})
        coverage = [dict(r) for r in conn.execute(
            "SELECT t.id,t.name,t.parent_id,t.dimension,COUNT(DISTINCT q.id) questions "
            "FROM tag t LEFT JOIN question_tag qt ON qt.tag_id=t.id "
            "LEFT JOIN question q ON q.id=qt.question_id AND q.status='active' AND q.merged_into IS NULL "
            "WHERE t.status='active' GROUP BY t.id ORDER BY t.dimension,t.id")]
        names = {}
        for row in conn.execute(
                "SELECT t.name,q.id FROM question_tag qt JOIN tag t ON t.id=qt.tag_id "
                "JOIN question q ON q.id=qt.question_id WHERE t.dimension='knowledge' "
                "AND q.status='active' AND q.merged_into IS NULL"):
            names.setdefault(row[0], set()).add(row[1])
        module_coverage, covered = [], set()
        for module in (plan or {}).get('modules', []):
            ids = set().union(*(names.get(name, set()) for name in module['knowledge_names']))
            covered.update(ids)
            module_coverage.append({'code': module['code'], 'name': module['name'],
                                    'question_count': len(ids), 'question_ids': sorted(ids)})
        pilot = (plan or {}).get('pilot') or {}
        pilot_ids = set().union(*(names.get(name, set()) for name in pilot.get('knowledge_names', [])))
        return {'database': str(path), 'counts': counts,
                'integrity': conn.execute('PRAGMA quick_check').fetchone()[0],
                'foreign_key_issues': [list(r) for r in conn.execute('PRAGMA foreign_key_check')],
                'active_current_questions': len(questions),
                'qtypes': dict(Counter(q['qtype'] for q in questions)),
                'tags_by_dimension': dict(Counter(r['dimension'] for r in coverage)),
                'gap_counts': {k:len(v) for k,v in gaps.items()}, 'gap_questions': gaps,
                'knowledge_coverage': [r for r in coverage if r['dimension']=='knowledge'],
                'planned_module_coverage': module_coverage,
                'unmapped_question_ids': sorted({q['id'] for q in questions} - covered),
                'pilot': {'name': pilot.get('name'), 'target': pilot.get('target_total'),
                          'current_candidates': len(pilot_ids),
                          'note': '候选数不代表已审校真实题数，正式库与演示库必须分别看待'},
                'questions': inspected,
                'notes': ['unreviewed_core 按题面、答案、全部已存解法审校统计，'
                          '是内容盘点口径，不等于某一题单所选解法的发布资格；图形等仍须独立检查。',
                          'missing_material_file 包含没有文件路径的素材记录，需人工判断源码素材的处理方式。',
                          '知识点覆盖按显式题目标签计数，不自动向祖先累计；解法方法标签不在此表重复计数。']}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', action='append', help='数据库路径，可重复指定；默认正式库与演示库')
    parser.add_argument('--output', type=Path, help='可选的 JSON 报告路径')
    parser.add_argument('--plan', type=Path, default=ROOT/'data/catalog_plan.json', help='内容建设目录')
    args = parser.parse_args()
    paths = [Path(p) for p in args.db] if args.db else [ROOT/'data/qbank.db', ROOT/'data/demo.db']
    plan = json.loads(args.plan.read_text(encoding='utf-8')) if args.plan.is_file() else {}
    result = {'checked_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
              'read_only': True, 'plan_version': plan.get('version'),
              'databases': [inspect_database(p, plan) for p in paths]}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=None))


if __name__ == '__main__':
    main()
