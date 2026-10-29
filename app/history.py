"""实际使用历史只从发布快照还原，不读取可继续编辑的题单。"""
from __future__ import annotations

from app import db
from app.snapshots import entry_for_item, selected_items


def usage_rows(audience_id: str | None = None, cutoff: str | None = None) -> list[dict]:
    sql = ('SELECT u.*, r.snapshot, r.collection_id FROM usage_log u '
           'JOIN release r ON r.id=u.release_id WHERE 1=1')
    params = []
    if audience_id:
        sql += ' AND u.audience_id=?'
        params.append(audience_id)
    if cutoff:
        sql += ' AND u.used_on>=?'
        params.append(cutoff)
    return db.all_(sql+' ORDER BY u.used_on DESC,u.created_at DESC,u.id DESC', params)


def question_history(audience_id: str | None = None, cutoff: str | None = None) -> list[dict]:
    out = []
    for usage in usage_rows(audience_id, cutoff):
        snapshot = usage.get('snapshot') or {}
        for item in selected_items(snapshot, usage.get('items')):
            try:
                entry = entry_for_item(snapshot, item)
            except ValueError:
                # 旧快照若缺版本，保留该版本与题号追溯，但不补造原题面。
                entry = {}
            out.append({'usage_id': usage['id'], 'release_id': usage['release_id'],
                        'audience_id': usage['audience_id'], 'used_on': usage['used_on'],
                        'purpose': usage['purpose'], 'item_id': item.get('id'),
                        'question_id': item.get('question_id'),
                        'question_version_id': item.get('question_version_id'),
                        'collection_title': (snapshot.get('collection') or {}).get('title') or '',
                        'short_name': (entry.get('question') or {}).get('short_name') or '',
                        'stem': (entry.get('version') or {}).get('stem') or ''})
    return out
