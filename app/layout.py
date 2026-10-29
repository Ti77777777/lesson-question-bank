"""Validated handout settings shared by preview and final export."""
import math
from app.server import ApiError

TEXT_FIELDS = {'title', 'subtitle', 'pretitle', 'creator', 'motto'}
BOOL_FIELDS = {'cover', 'show_sources', 'teacher_notes', 'toc'}
ITEM_FIELDS = {'space_mm', 'page_break_before', 'keep_together', 'options_layout'}
CHOICES = {'auto', 'horizontal', 'vertical'}


def validate(value, item_ids):
    if not isinstance(value, dict) or set(value) - {'options', 'items'}:
        raise ApiError(400, 'layout', '排版设置格式无效')
    options = value.get('options', {})
    items = value.get('items', {})
    if not isinstance(options, dict) or not isinstance(items, dict):
        raise ApiError(400, 'layout', '排版设置须包含选项对象与条目对象')
    allowed = TEXT_FIELDS | BOOL_FIELDS | {'format', 'theme', 'options_layout'}
    if set(options) - allowed:
        raise ApiError(400, 'layout', '包含不支持的版面选项')
    from app.latex.exbook import THEMES
    clean = {}
    for key, val in options.items():
        if key in TEXT_FIELDS:
            if not isinstance(val, str) or len(val) > 300:
                raise ApiError(400, 'layout', '标题与署名等文字须在300字以内')
            clean[key] = val.strip()
        elif key in BOOL_FIELDS:
            if type(val) is not bool: raise ApiError(400, 'layout', '开关须为布尔值')
            clean[key] = val
        else:
            permitted = THEMES if key == 'theme' else CHOICES if key == 'options_layout' else {'standard', 'compact', 'loose', 'single'}
            if not isinstance(val, str) or val not in permitted:
                raise ApiError(400, 'layout', '版面、主题或选项排列方式无效')
            clean[key] = val
    result = {}
    for iid, settings in items.items():
        if iid not in item_ids:
            raise ApiError(409, 'layout_item', '条目已移出当前题单，请刷新排版页')
        if not isinstance(settings, dict) or set(settings) - ITEM_FIELDS:
            raise ApiError(400, 'layout', '包含不支持的逐题排版设置')
        entry = {}
        for key, val in settings.items():
            if key == 'space_mm':
                if type(val) not in (int, float) or not math.isfinite(val) or not 0 <= val <= 200:
                    raise ApiError(400, 'layout', '留白高度须为0至200毫米')
            elif key == 'options_layout':
                if not isinstance(val, str) or val not in CHOICES: raise ApiError(400, 'layout', '选项排列方式无效')
            elif type(val) is not bool:
                raise ApiError(400, 'layout', '逐题分页开关须为布尔值')
            entry[key] = val
        result[iid] = entry
    return {'options': clean, 'items': result}


def apply(snapshot, layout):
    for item in snapshot.get('items', []):
        item.update((layout.get('items') or {}).get(item['id'], {}))
    return snapshot


def clear_item_override(conn, cid, iid, fields=None):
    """The original item editor and layout editor must agree on the latest edit."""
    import json
    row = conn.execute('SELECT layout_options FROM collection WHERE id=?', (cid,)).fetchone()
    saved = json.loads(row['layout_options']) if row and row['layout_options'] else {}
    overrides = saved.get('items') or {}
    if iid not in overrides: return
    if fields is None:
        overrides.pop(iid, None)
    else:
        for field in fields: overrides[iid].pop(field, None)
        if not overrides[iid]: overrides.pop(iid, None)
    saved['items'] = overrides
    conn.execute('UPDATE collection SET layout_options=? WHERE id=?', (json.dumps(saved, ensure_ascii=False), cid))
