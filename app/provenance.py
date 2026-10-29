"""Shared source labels for search, question details, collections and exports."""
from app import db


def label(name, qno=None):
    name=str(name or '').strip()
    if not name:return ''
    return name + ('，第'+str(qno)+'题' if qno else '')


def for_questions(question_ids):
    ids=list(dict.fromkeys(question_ids))
    if not ids:return {}
    rows=db.all_(
      'SELECT o.question_id,o.question_version_id,o.source_id,s.name AS source_name,'
      'o.qno,o.pdf_page,o.source_file_id,f.rel_path AS file FROM occurrence o '
      'LEFT JOIN source s ON s.id=o.source_id LEFT JOIN source_file f ON f.id=o.source_file_id '
      'WHERE o.question_id IN ('+','.join('?'*len(ids))+') ORDER BY o.created_at,o.id',ids)
    out={}
    for row in rows:
        row['label']=label(row['source_name'],row['qno'])
        out.setdefault(row['question_id'],[]).append(row)
    return out


def summary(occurrences, version_id=None):
    exact=[o for o in occurrences if not o.get('question_version_id') or o['question_version_id']==version_id]
    selected=exact or occurrences
    return '；'.join(dict.fromkeys(o['label'] for o in selected if o.get('label')))


def rename_generated_titles(conn, source_id, old_name, new_name):
    """Update generated management titles; preserve teacher-authored short names."""
    changed=0
    for row in conn.execute('SELECT DISTINCT q.id,q.short_name,o.qno FROM question q JOIN occurrence o ON o.question_id=q.id WHERE o.source_id=?',(source_id,)).fetchall():
        generated=('原卷第'+str(row['qno'])+'题',label(old_name,row['qno']))
        if row['short_name'] in generated:
            conn.execute('UPDATE question SET short_name=?,updated_at=? WHERE id=?',(label(new_name,row['qno']),db.now(),row['id']))
            changed+=1
    return changed


def sync_source_jobs(conn, source_id, name, method='manual'):
    import json
    for row in conn.execute('SELECT j.* FROM ai_job j JOIN source_file f ON f.id=j.source_file_id WHERE f.source_id=?',(source_id,)).fetchall():
        document=json.loads(row['document_json'] or '{}')
        paper=document.setdefault('paper',{})
        paper.update(title=name,method=method)
        conn.execute('UPDATE ai_job SET title=?,document_json=?,updated_at=? WHERE id=?',(name,db.dumps(document),db.now(),row['id']))
