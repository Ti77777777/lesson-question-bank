"""检索来源目录：根据已保存的来源信息派生筛选项，不修改原始资料。"""
from __future__ import annotations

import re
from app import db

CATEGORIES = {
    'gaokao': '高考真题', 'mock': '模拟考', 'competition': '学科竞赛',
    'monthly': '月考试卷', 'midterm': '期中考', 'final': '期末考',
    'opening': '开学考', 'stage': '阶段测试',
    'exam': '其他试卷', 'textbook': '教材', 'workbook': '教辅',
    'lecture': '讲义', 'self': '自编题', 'other': '其他来源',
}
EXAM_CATEGORIES = tuple(k for k in CATEGORIES if k not in ('textbook','workbook','lecture','self','other'))
GRADES = {'senior1':'高一', 'senior2':'高二', 'senior3':'高三'}


def exam_category(source):
    explicit=source.get('exam_type')
    if explicit in EXAM_CATEGORIES:return explicit
    name=source.get('name') or ''
    evidence=name+' '+(source.get('note') or '')
    if '高考真题' in evidence or re.search(r'高考.*真题',name):return 'gaokao'
    for pattern,category in [
        (r'竞赛|联赛|奥林匹克|奥赛','competition'),
        (r'开学|入学|分班','opening'),
        (r'月考','monthly'), (r'期中','midterm'), (r'期末','final'),
        (r'模拟|[一二三四五]模|高考适应性|(?:高三|高中毕业班|毕业年级).*?(?:摸底|联考|质量检测|适应性)','mock'),
        (r'阶段|单元测试','stage')]:
        if re.search(pattern,name):return category
    return 'exam'


def grade(source,category):
    if source.get('grade') in GRADES:return source['grade']
    name=source.get('name') or ''
    values={key for key,label in GRADES.items() if label in name}
    if len(values)==1:return values.pop()
    if len(values)>1:return ''  # A multi-grade document cannot safely be assigned one grade.
    if re.search(r'普通高中毕业班|高中毕业班|高中毕业年级',name) or category=='gaokao':return 'senior3'
    return ''


def metadata(source):
    name = source.get('name') or ''
    kind = source.get('kind') or 'other'
    category = kind if kind in CATEGORIES else 'other'
    if kind == 'exam':category=exam_category(source)
    # Cohort labels such as 2027届 identify students, not an examination year.
    year = re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d|\s*[届级])', str(source.get('year') or '') + ' ' + str(source.get('edition') or '') + ' ' + name)
    return {
        'id': source['id'], 'name': name, 'category': category,
        'year': year.group() if year else '',
        # 历史字段含“新高考I”等卷别，界面因此明确称作“地区 / 卷别”。
        'region': (source.get('region') or '').strip(),
        'grade': grade(source,category),
        'school': source.get('school') or '',
    }


def sources():
    return [metadata(row) for row in db.all_('SELECT * FROM source ORDER BY name, id')]


def matches(source, category='', year='', region='', source_id='', grade=''):
    return (not source_id or source['id'] == source_id) and all(
        not wanted or (source[field] or '_unknown') == wanted
        for field, wanted in [('category', category), ('year', year), ('region', region), ('grade',grade)]
    )


def catalog():
    rows = db.all_("SELECT DISTINCT o.source_id, q.id AS question_id FROM occurrence o "
                   "JOIN question q ON q.id=o.question_id "
                   "JOIN question_version v ON v.id=q.current_version_id "
                   "WHERE q.merged_into IS NULL AND q.status='active'")
    question_ids = {}
    for row in rows:
        question_ids.setdefault(row['source_id'], set()).add(row['question_id'])
    items = [dict(s, count=len(question_ids[s['id']])) for s in sources() if s['id'] in question_ids]
    counts = {};grade_counts={}
    for source in items:
        counts.setdefault(source['category'], set()).update(question_ids[source['id']])
        grade_counts.setdefault(source['grade'] or '_unknown',set()).update(question_ids[source['id']])
    return {
        'sources': items,
        'categories': [{'id': key, 'name': label, 'count': len(counts.get(key,set()))}
                       for key, label in CATEGORIES.items() if key in EXAM_CATEGORIES or key in counts],
        'grades': [{'id':key,'name':label,'count':len(grade_counts.get(key,set()))}
                   for key,label in [*GRADES.items(),('_unknown','未标注')]],
    }
