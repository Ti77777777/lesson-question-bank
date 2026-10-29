"""题单写入的共享底座：聚合修订、操作记录与幂等执行。

设计来自《新功能设计与实施规划》M0 与规则 R06：
- **聚合修订**：题单、分节、条目与排版合成一个摘要。写入前比对，过期即拒绝，
  避免两个页面各存一份旧状态后互相覆盖。
- **操作记录**：同一操作编号与同一输入返回原结果；同编号不同输入则拒绝。
  网络重试与双击因此不会写入两次。

本模块只提供能力，不定义业务；具体动作由调用方在事务内完成。
"""
from __future__ import annotations

import hashlib
import json

from app import db
from app.server import ApiError

REVISION_PREFIX = "cr1:"

# 参与聚合修订的字段。updated_at 故意不入摘要：它因任何写入而变，
# 会让「只改了标题时间戳」也判定为冲突。
_ITEM_FIELDS = (
    "id", "section_id", "order_no", "question_id", "question_version_id",
    "role", "hint_level", "required", "points", "space_mm",
    "page_break_before", "subq_keys", "solution_id", "note",
)
_SECTION_FIELDS = ("id", "title", "kind", "order_no", "intro")
_COLLECTION_FIELDS = ("id", "title", "topic", "level", "duration_min", "purpose",
                      "status", "layout_options")


def _canonical(value):
    """把行数据规范成可哈希结构：JSON 字段统一成对象，其余转字符串或数字。"""
    if isinstance(value, str) and value[:1] in "{[":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def collection_revision(cid: str, conn=None) -> str:
    """题单聚合修订。读不到题单时返回空串，由调用方决定报什么错。"""
    conn = conn or db.db()
    row = conn.execute(
        "SELECT %s FROM collection WHERE id = ?" % ", ".join(_COLLECTION_FIELDS),
        (cid,)).fetchone()
    if row is None:
        return ""
    parts = {"collection": {k: _canonical(row[k]) for k in _COLLECTION_FIELDS}}
    parts["sections"] = [
        {k: _canonical(r[k]) for k in _SECTION_FIELDS}
        for r in conn.execute(
            "SELECT %s FROM collection_section WHERE collection_id = ? "
            "ORDER BY order_no, id" % ", ".join(_SECTION_FIELDS), (cid,))
    ]
    parts["items"] = [
        {k: _canonical(r[k]) for k in _ITEM_FIELDS}
        for r in conn.execute(
            "SELECT %s FROM collection_item WHERE collection_id = ? "
            "ORDER BY order_no, id" % ", ".join(_ITEM_FIELDS), (cid,))
    ]
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return REVISION_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def input_digest(payload) -> str:
    """操作输入的摘要，用于区分「同编号重试」与「拿旧编号改内容」。"""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _existing(conn, cid: str, op_key: str):
    return conn.execute(
        "SELECT * FROM collection_command WHERE collection_id = ? AND op_key = ?",
        (cid, op_key)).fetchone()


def execute(cid: str, op_key: str, kind: str, payload, apply_fn,
            base_revision: str | None = None, conn=None):
    """在一个事务内执行一次可重试的题单写入。

    apply_fn(conn, revision_before) 负责真正的写入并返回结果字典；
    结果连同前后修订写入操作记录。调用方可以传入已开启的事务连接，
    以便把业务写入与操作记录放在同一笔里。
    """
    if not isinstance(op_key, str) or not op_key.strip():
        raise ApiError(400, "missing_field", "缺少操作编号（operation_id）")
    if conn is not None:
        return _run(conn, cid, op_key.strip(), kind, payload, apply_fn, base_revision)
    with db.transaction() as own:
        return _run(own, cid, op_key.strip(), kind, payload, apply_fn, base_revision)


def _run(conn, cid: str, op_key: str, kind: str, payload, apply_fn, base_revision):
    # 整份结果都要存：撤销动作重试时要拿回同一份 inverse，只存计数会让重试丢逆操作。
    digest = input_digest({"kind": kind, "payload": payload})

    replay = _existing(conn, cid, op_key)
    if replay is not None:
        if replay["input_digest"] != digest:
            raise ApiError(409, "operation_conflict",
                           "这个操作编号已经用在同一题单的其他内容上，请换一个新编号")
        result = json.loads(replay["result_json"] or "{}")
        result["replayed"] = True
        result["revision"] = replay["after_revision"]
        return result

    before = collection_revision(cid, conn)
    if not before:
        raise ApiError(404, "not_found", "没有这个题单：%s" % cid)
    if base_revision and base_revision != before:
        raise ApiError(409, "revision_conflict",
                       "题单在你操作期间已被修改，请刷新后再试",
                       {"revision": before})

    result = apply_fn(conn, before) or {}
    after = collection_revision(cid, conn)
    result["revision"] = after
    result["replayed"] = False
    conn.execute(
        "INSERT INTO collection_command(id, collection_id, op_key, kind, input_digest, "
        "before_revision, after_revision, result_json, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (db.next_id(conn, "OPC"), cid, op_key, kind, digest, before, after,
         db.dumps({key: value for key, value in result.items()
                   if key not in ("revision", "replayed")}), db.now()))
    return result


def recent_commands(cid: str, limit: int = 20) -> list[dict]:
    """按时间倒序列出可逆操作，供撤销栈核对与排查使用。"""
    return db.all_(
        "SELECT id, op_key, kind, before_revision, after_revision, created_at "
        "FROM collection_command WHERE collection_id = ? ORDER BY created_at DESC, id DESC "
        "LIMIT ?", (cid, int(limit)))
