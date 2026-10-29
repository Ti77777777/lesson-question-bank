"""发布快照读取：按条目版本定位内容，兼容原先按题目编号存储的快照。"""
from __future__ import annotations


def entries(snapshot: dict) -> list[dict]:
    source = snapshot.get('question_versions') or snapshot.get('questions') or {}
    return list(source.values())


def entry_for_item(snapshot: dict, item: dict) -> dict:
    vid = item.get('question_version_id')
    by_version = snapshot.get('question_versions') or {}
    entry = by_version.get(vid)
    if entry is None:
        entry = (snapshot.get('questions') or {}).get(item.get('question_id'))
    if not entry:
        raise ValueError('快照缺少条目 %s 的题目内容' % item.get('id'))
    stored_vid = (entry.get('version') or {}).get('id')
    if vid and stored_vid and vid != stored_vid:
        raise ValueError('历史快照缺少条目 %s 引用的版本 %s，不能用其他版本替代' %
                         (item.get('id'), vid))
    return entry


def selected_items(snapshot: dict, selection: list[str] | None) -> list[dict]:
    """None 表示整份；显式空数组表示没有条目，不能推断成整份。"""
    items = snapshot.get('items') or []
    if selection is None:
        return items
    wanted = set(selection)
    return [item for item in items if item.get('id') in wanted]
