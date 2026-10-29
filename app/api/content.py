"""内容域接口：收题箱、资料与出处、题目与版本、解法、标签、关系、检索。

对应 docs/API.md §2–§8。字段名以 data/schema.sql 为准。

几条贯穿全文件的规则：
1. 题目内容不可覆盖，改内容一律新建版本；管理字段才走 PATCH /api/questions/{qid}。
2. 版本新建时复制解法（新 ID），旧版本 review_state 置 submitted，审校记录自然重置。
3. 写操作一律在 db.transaction() 内完成，并在同一事务内调用 db.audit 留痕。
"""
from __future__ import annotations

import base64
import binascii
import difflib
import hashlib
import re
from datetime import datetime, timedelta

from app import db,provenance,search_catalog
from app.server import ApiError, delete, get, patch, post

# ------------------------------------------------------------------ 常量
QTYPES = ("single", "multi", "blank", "solve")

# 有向关系：写入前需做环检测
DIRECTED_REL = (
    "cond_variant", "concl_variant", "param", "generalize",
    "special", "inverse", "correction",
)
# 对称关系：写入时生成反向记录，删除时一并清理
SYMMETRIC_REL = ("same_method", "error_contrast")
REL_TYPES = DIRECTED_REL + SYMMETRIC_REL

TAG_DIMENSIONS = ("knowledge", "textbook", "prereq", "method", "idea", "purpose", "error")
REVIEW_ITEMS = ("stem", "answer", "solution", "figure",
                "classification", "boundary", "method_scope")
REVIEW_RESULTS = ("pending", "pass", "fail", "dispute", "na")
# 审校对象类型 → 实际表名
REVIEW_TARGETS = {"question_version": "question_version",
                  "solution": "solution", "asset": "asset"}
SOURCE_KINDS = ("exam", "textbook", "workbook", "lecture", "self", "other")
INBOX_STATUS = ("pending", "extracted", "organized", "rejected")

# 版本内容字段：只能通过新建版本修改
VERSION_FIELDS = ("qtype", "stem", "options", "answer", "answer_keys", "subquestions",
                  "analysis", "material_refs", "teaching", "total_points")

# 题目管理字段：允许 PATCH 直接改
MANAGE_FIELDS = ("status", "status_reason", "short_name", "difficulty_calc",
                 "difficulty_think", "difficulty_scope", "difficulty_level",
                 "difficulty_note", "rec_level", "rec_reason", "time_solve_min",
                 "time_explain_min", "note")

# 审校项的中文描述
ITEM_LABEL = {
    "stem": "题面", "answer": "答案", "solution": "解析", "figure": "图形",
    "classification": "分类", "boundary": "边界", "method_scope": "方法范围",
}


# ------------------------------------------------------------------ 小工具
def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _split_csv(value) -> list[str]:
    """把逗号分隔的参数拆成去重列表。

    说明：server.Request.query 对同名参数只保留第一个值，req.path 又不含查询串，
    因此 qtype / tags / method_tag 的「可重复」只能以逗号分隔表达。
    """
    if not value:
        return []
    out: list[str] = []
    for part in str(value).split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _preview(text, limit: int = 80) -> str:
    from app.text_preview import preview
    return preview(text,limit)


def _ph(count: int) -> str:
    return ",".join("?" * count)


def _as_int(value, field: str, low=None, high=None):
    """把可选整数字段转成 int，越界或非数字报 400。"""
    if value is None or value == "":
        return None
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "bad_field", f"字段 {field} 必须是整数")
    if low is not None and num < low:
        raise ApiError(400, "bad_field", f"字段 {field} 不能小于 {low}")
    if high is not None and num > high:
        raise ApiError(400, "bad_field", f"字段 {field} 不能大于 {high}")
    return num


def _as_float(value, field: str):
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ApiError(400, "bad_field", f"字段 {field} 必须是数字")
    if num < 0:
        raise ApiError(400, "bad_field", f"字段 {field} 不能为负")
    return num


# ------------------------------------------------------------------ 取对象
def _question_or_404(qid: str) -> dict:
    row = db.one("SELECT * FROM question WHERE id = ?", (qid,))
    if row is None:
        raise ApiError(404, "not_found", f"题目 {qid} 不存在")
    return row


def _version_or_404(version_id: str) -> dict:
    row = db.one("SELECT * FROM question_version WHERE id = ?", (version_id,))
    if row is None:
        raise ApiError(404, "not_found", f"题目版本 {version_id} 不存在")
    return row


def _source_or_404(sid: str) -> dict:
    row = db.one("SELECT * FROM source WHERE id = ?", (sid,))
    if row is None:
        raise ApiError(404, "not_found", f"资料 {sid} 不存在")
    return row


def _tag_or_404(tid: str) -> dict:
    row = db.one("SELECT * FROM tag WHERE id = ?", (tid,))
    if row is None:
        raise ApiError(404, "not_found", f"标签 {tid} 不存在")
    return row


def _solution_or_404(sid: str) -> dict:
    row = db.one("SELECT * FROM solution WHERE id = ?", (sid,))
    if row is None:
        raise ApiError(404, "not_found", f"解法 {sid} 不存在")
    return row


def _occurrence_or_404(oid: str) -> dict:
    row = db.one("SELECT * FROM occurrence WHERE id = ?", (oid,))
    if row is None:
        raise ApiError(404, "not_found", f"出处 {oid} 不存在")
    return row


def _inbox_or_404(iid: str) -> dict:
    row = db.one("SELECT * FROM inbox_item WHERE id = ?", (iid,))
    if row is None:
        raise ApiError(404, "not_found", f"收题条目 {iid} 不存在")
    return row


def _relation_or_404(rid: str) -> dict:
    row = db.one("SELECT * FROM relation WHERE id = ?", (rid,))
    if row is None:
        raise ApiError(404, "not_found", f"题目关系 {rid} 不存在")
    return row


def _current_version_of(question: dict) -> dict:
    vid = question.get("current_version_id")
    if vid:
        row = db.one("SELECT * FROM question_version WHERE id = ?", (vid,))
        if row is not None:
            return row
    rows = db.all_("SELECT * FROM question_version WHERE question_id = ? "
                   "ORDER BY version_no DESC LIMIT 1", (question["id"],))
    if not rows:
        raise ApiError(409, "no_version", f"题目 {question['id']} 还没有任何版本")
    return rows[0]


def _assert_editable_version(question: dict, version_id: str) -> None:
    """解法只允许挂在题目当前版本下修改，历史版本一律锁定。"""
    if question.get("current_version_id") != version_id:
        raise ApiError(409, "version_locked", "该解法属于历史版本，请先新建版本再改")


# ------------------------------------------------------------------ 审校状态
def _review_status_bulk(version_ids: list[str]) -> dict[str, dict]:
    """批量计算版本七项审校状态，返回 {version_id: {item: result}}。

    每项取该项目最新一条 review；无记录视为 pending。
    solution 项综合该版本下全部解法；figure 项在版本无 material_refs 时自动 na。
    """
    version_ids = [v for v in dict.fromkeys(version_ids) if v]
    if not version_ids:
        return {}

    ph = _ph(len(version_ids))
    versions = {r["id"]: r for r in db.all_(
        f"SELECT id, material_refs FROM question_version WHERE id IN ({ph})", version_ids)}
    solutions = db.all_(
        f"SELECT id, question_version_id FROM solution WHERE question_version_id IN ({ph})",
        version_ids)
    sol_ids = [s["id"] for s in solutions]
    refs: list[str] = []
    for ver in versions.values():
        for ref in (ver.get("material_refs") or []):
            if ref not in refs:
                refs.append(ref)

    parts = [f"(target_type = 'question_version' AND target_id IN ({ph}))"]
    params: list = list(version_ids)
    if sol_ids:
        parts.append(f"(target_type = 'solution' AND target_id IN ({_ph(len(sol_ids))}))")
        params += sol_ids
    if refs:
        parts.append(f"(target_type = 'asset' AND target_id IN ({_ph(len(refs))}))")
        params += refs

    rows = db.all_(
        "SELECT target_type, target_id, item, result FROM review WHERE "
        + " OR ".join(parts) + " ORDER BY created_at, id", params)
    latest: dict[tuple, str] = {}
    for row in rows:
        latest[(row["target_type"], row["target_id"], row["item"])] = row["result"]

    sols_by_version: dict[str, list[str]] = {}
    for sol in solutions:
        sols_by_version.setdefault(sol["question_version_id"], []).append(sol["id"])

    out: dict[str, dict] = {}
    for vid in version_ids:
        status: dict[str, str] = {}
        for item in ("stem", "answer", "classification", "boundary", "method_scope"):
            status[item] = latest.get(("question_version", vid, item), "pending")

        ids = sols_by_version.get(vid, [])
        if ids:
            results = [latest.get(("solution", sid, "solution"), "pending") for sid in ids]
            if any(r == "fail" for r in results):
                status["solution"] = "fail"
            elif all(r == "pass" for r in results):
                status["solution"] = "pass"
            else:
                status["solution"] = "pending"
        else:
            # 无解法时，analysis 视作方法解析，以版本级 solution 项为准
            status["solution"] = latest.get(("question_version", vid, "solution"), "pending")

        ver_refs = versions.get(vid, {}).get("material_refs") or []
        if not ver_refs:
            status["figure"] = "na"
        else:
            direct = latest.get(("question_version", vid, "figure"))
            if direct:
                status["figure"] = direct
            else:
                res = [latest.get(("asset", rid, "figure"), "pending") for rid in ver_refs]
                if any(r == "fail" for r in res):
                    status["figure"] = "fail"
                elif all(r == "pass" for r in res):
                    status["figure"] = "pass"
                else:
                    status["figure"] = "pending"
        out[vid] = status
    return out


def _review_status(version_id: str, question_id: str) -> dict:
    """单个版本的七项审校状态。question_id 仅用于报错信息定位。"""
    status = _review_status_bulk([version_id]).get(version_id)
    if status is None:
        raise ApiError(404, "not_found",
                       f"题目 {question_id} 的版本 {version_id} 不存在")
    return status


def _eligibility(status: dict) -> dict:
    """按七项状态算出是否可选用，并用中文列出缺项。"""
    missing: list[str] = []
    for item in ("stem", "answer", "solution"):
        result = status.get(item, "pending")
        if result == "pass":
            continue
        label = ITEM_LABEL[item]
        if result in ("pending", "na"):
            missing.append(f"{label}尚未审校")
        else:
            missing.append(f"{label}未通过审校")
    if status.get("figure") == "fail":
        missing.append("图形未通过审校")

    usable = (status.get("stem") == "pass"
              and status.get("answer") == "pass"
              and status.get("solution") == "pass"
              and status.get("figure") != "fail")
    return {"usable": usable, "missing": [] if usable else missing}


def _aggregate_review(status: dict) -> str:
    """列表用的单一审校结论：pass / pending / fail。"""
    blocking = [status.get("stem"), status.get("answer"), status.get("solution")]
    if all(x == "pass" for x in blocking) and status.get("figure") != "fail":
        return "pass"
    if "fail" in blocking or status.get("figure") == "fail":
        return "fail"
    return "pending"


# ------------------------------------------------------------------ 关联读取
def _question_tags(qid: str) -> list[dict]:
    return db.all_(
        "SELECT t.id, t.name, t.dimension, t.status, qt.role, qt.source "
        "FROM question_tag qt JOIN tag t ON t.id = qt.tag_id "
        "WHERE qt.question_id = ? ORDER BY t.dimension, t.id", (qid,))


def _solutions_of(version_id: str) -> list[dict]:
    rows = db.all_(
        "SELECT * FROM solution WHERE question_version_id = ? "
        "ORDER BY is_primary DESC, order_no, id", (version_id,))
    for row in rows:
        row["tags"] = db.all_(
            "SELECT t.id, t.name, t.dimension FROM solution_tag st "
            "JOIN tag t ON t.id = st.tag_id WHERE st.solution_id = ? ORDER BY t.id",
            (row["id"],))
    return rows


def _version_brief(row: dict, solution_count: int | None = None) -> dict:
    if solution_count is None:
        solution_count = db.scalar(
            "SELECT COUNT(*) FROM solution WHERE question_version_id = ?", (row["id"],))
    return {
        "id": row["id"], "version_no": row["version_no"], "qtype": row["qtype"],
        "change_note": row["change_note"], "created_at": row["created_at"],
        "review_state": row["review_state"], "solution_count": solution_count,
    }


def _occurrences_of(qid: str) -> list[dict]:
    return db.all_(
        "SELECT o.id, o.question_id, o.question_version_id, o.source_id, "
        "s.name AS source_name, s.kind AS source_kind, f.rel_path AS file, "
        "o.source_file_id, o.pdf_page, o.print_page, o.qno, o.raw_text, "
        "o.ocr_tool, o.ocr_at, o.ocr_suspect, o.pending, o.note, o.created_at "
        "FROM occurrence o "
        "LEFT JOIN source s ON s.id = o.source_id "
        "LEFT JOIN source_file f ON f.id = o.source_file_id "
        "WHERE o.question_id = ? ORDER BY o.created_at, o.id", (qid,))


def _relation_side(qid: str, direction: str) -> list[dict]:
    if direction == "out":
        sql = ("SELECT r.id, r.rel_type, r.note, r.confirmed, r.created_at, "
               "q.id AS qid, q.short_name AS short_name, v.stem AS stem "
               "FROM relation r JOIN question q ON q.id = r.to_id "
               "LEFT JOIN question_version v ON v.id = q.current_version_id "
               "WHERE r.from_id = ? ORDER BY r.created_at, r.id")
    else:
        sql = ("SELECT r.id, r.rel_type, r.note, r.confirmed, r.created_at, "
               "q.id AS qid, q.short_name AS short_name, v.stem AS stem "
               "FROM relation r JOIN question q ON q.id = r.from_id "
               "LEFT JOIN question_version v ON v.id = q.current_version_id "
               "WHERE r.to_id = ? ORDER BY r.created_at, r.id")
    rows = db.all_(sql, (qid,))
    return [{
        "id": row["id"], "rel_type": row["rel_type"], "note": row["note"],
        "confirmed": row["confirmed"], "created_at": row["created_at"],
        "question": {"id": row["qid"], "short_name": row["short_name"],
                     "stem_preview": _preview(row["stem"])},
    } for row in rows]


def _reviews_of(qid: str, versions: list[dict]) -> list[dict]:
    version_ids = [v["id"] for v in versions]
    sol_ids = [s["id"] for s in db.all_(
        f"SELECT id FROM solution WHERE question_version_id IN ({_ph(len(version_ids))})",
        version_ids)] if version_ids else []
    refs: list[str] = []
    for ver in versions:
        for ref in (ver.get("material_refs") or []):
            if ref not in refs:
                refs.append(ref)

    parts = []
    params: list = []
    if version_ids:
        parts.append(f"(target_type = 'question_version' AND target_id IN ({_ph(len(version_ids))}))")
        params += version_ids
    if sol_ids:
        parts.append(f"(target_type = 'solution' AND target_id IN ({_ph(len(sol_ids))}))")
        params += sol_ids
    if refs:
        parts.append(f"(target_type = 'asset' AND target_id IN ({_ph(len(refs))}))")
        params += refs
    if not parts:
        return []
    return db.all_(
        "SELECT * FROM review WHERE " + " OR ".join(parts)
        + " ORDER BY created_at DESC, id DESC", params)


def _usage_of(qid: str) -> list[dict]:
    from app.history import question_history
    audiences = {a['id']: a['name'] for a in db.all_('SELECT id,name FROM audience')}
    result, seen = [], set()
    for row in question_history():
        if row['question_id'] != qid or row['usage_id'] in seen:
            continue
        seen.add(row['usage_id'])
        result.append({**row, 'audience': audiences.get(row['audience_id'], '')})
    return result


def _detail(qid: str) -> dict:
    """§4 的题目详情，字段与 docs/API.md 一一对应。"""
    question = _question_or_404(qid)
    versions = db.all_(
        "SELECT * FROM question_version WHERE question_id = ? ORDER BY version_no",
        (qid,))
    if not versions:
        raise ApiError(409, "no_version", f"题目 {qid} 还没有任何版本")

    current = None
    if question.get("current_version_id"):
        current = next((v for v in versions if v["id"] == question["current_version_id"]), None)
    current = current or versions[-1]

    status = _review_status(current["id"], qid)
    current_out = dict(current)
    current_out["solutions"] = _solutions_of(current["id"])
    current_out["tags"] = _question_tags(qid)
    current_out["version_count"] = len(versions)

    counts = {r["question_version_id"]: r["n"] for r in db.all_(
        "SELECT question_version_id, COUNT(*) AS n FROM solution "
        "WHERE question_version_id IN (" + _ph(len(versions)) + ") "
        "GROUP BY question_version_id",
        [v["id"] for v in versions])}

    return {
        "question": question,
        "current_version": current_out,
        "versions": [_version_brief(v, counts.get(v["id"], 0)) for v in versions],
        "occurrences": _occurrences_of(qid),
        "relations": {"out": _relation_side(qid, "out"),
                      "in": _relation_side(qid, "in")},
        "reviews": _reviews_of(qid, versions),
        "review_status": status,
        "eligibility": _eligibility(status),
        "usage": _usage_of(qid),
    }


# ------------------------------------------------------------------ 写入构件
def _insert_solutions(conn, version_id: str, items, ts: str) -> list[str]:
    """在版本下批量写解法；同一版本内 is_primary 只允许一个。"""
    created: list[str] = []
    primary_done = False
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            raise ApiError(400, "bad_solution", "solutions 数组元素必须是对象")
        label = _text(item.get("label")) or f"解法{idx + 1}"
        steps = item.get("steps")
        if not _text(steps):
            raise ApiError(400, "missing_field", f"解法「{label}」缺少 steps")
        is_primary = 1 if item.get("is_primary") else 0
        if is_primary and primary_done:
            is_primary = 0
        if is_primary:
            primary_done = True
        order_no = item.get("order_no")
        if order_no is None:
            order_no = idx
        sid = db.next_id(conn, "SOL")
        conn.execute(
            "INSERT INTO solution(id, question_version_id, label, approach, steps, "
            "conditions, is_primary, order_no, teacher_tip, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sid, version_id, label, item.get("approach"), steps, item.get("conditions"),
             is_primary, _as_int(order_no, "order_no"), item.get("teacher_tip"), ts, ts))
        for tid in (item.get("method_tags") or []):
            _link_solution_tag(conn, sid, tid, ts)
        created.append(sid)

    if created and not primary_done:
        conn.execute("UPDATE solution SET is_primary = 1 WHERE id = ?", (created[0],))
    return created


def _link_solution_tag(conn, solution_id: str, tag_id: str, ts: str) -> None:
    tag = conn.execute("SELECT id, merged_into, status FROM tag WHERE id = ?",
                       (tag_id,)).fetchone()
    if tag is None:
        raise ApiError(404, "not_found", f"标签 {tag_id} 不存在")
    if tag["status"] != "active":
        raise ApiError(409, "tag_inactive", f"标签 {tag_id} 已停用，不能挂到解法上")
    conn.execute(
        "INSERT OR IGNORE INTO solution_tag(solution_id, tag_id, created_at) VALUES(?,?,?)",
        (solution_id, tag_id, ts))


def _insert_occurrence(conn, qid: str, version_id: str, occ: dict, ts: str) -> str:
    """写一条出处。source 为空时必须 pending=1（出处待确认）。"""
    occ = occ or {}
    source_id = occ.get("source_id")
    pending = 1 if _text(occ.get("pending")).lower() in ("1", "true", "yes", "y") else 0
    if not source_id and not pending:
        raise ApiError(400, "bad_occurrence",
                       "出处既没有 source_id 也没有标记为待确认（pending=1）")
    if source_id and conn.execute("SELECT 1 FROM source WHERE id = ?",
                                  (source_id,)).fetchone() is None:
        raise ApiError(404, "not_found", f"资料 {source_id} 不存在")

    file_id = occ.get("source_file_id")
    if file_id:
        row = conn.execute("SELECT source_id FROM source_file WHERE id = ?",
                           (file_id,)).fetchone()
        if row is None:
            raise ApiError(404, "not_found", f"资料文件 {file_id} 不存在")
        if source_id and row["source_id"] != source_id:
            raise ApiError(400, "bad_occurrence", f"文件 {file_id} 不属于资料 {source_id}")

    oid = db.next_id(conn, "OCC")
    conn.execute(
        "INSERT INTO occurrence(id, question_id, question_version_id, source_id, "
        "source_file_id, pdf_page, print_page, qno, screenshot_asset_id, raw_text, "
        "ocr_tool, ocr_at, ocr_suspect, pending, note, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, qid, version_id, source_id, file_id,
         _as_int(occ.get("pdf_page"), "pdf_page"),
         occ.get("print_page"), occ.get("qno"), occ.get("screenshot_asset_id"),
         occ.get("raw_text"), occ.get("ocr_tool"), occ.get("ocr_at"),
         occ.get("ocr_suspect"), pending, occ.get("note"), ts))
    return oid


def _insert_question_tags(conn, qid: str, tags, ts: str) -> None:
    for item in tags or []:
        if isinstance(item, str):
            item = {"tag_id": item}
        if not isinstance(item, dict):
            raise ApiError(400, "bad_tag", "tags 数组元素必须是对象或标签 ID")
        tid = _text(item.get("tag_id"))
        if not tid:
            raise ApiError(400, "missing_field", "打标签需要 tag_id")
        role = _text(item.get("role")) or "primary"
        if role not in ("primary", "secondary"):
            raise ApiError(400, "bad_field", "role 只能是 primary 或 secondary")
        source = _text(item.get("source")) or "manual"
        if source not in ("manual", "ai"):
            raise ApiError(400, "bad_field", "source 只能是 manual 或 ai")
        tag = conn.execute("SELECT id, merged_into, status FROM tag WHERE id = ?",
                           (tid,)).fetchone()
        if tag is None:
            raise ApiError(404, "not_found", f"标签 {tid} 不存在")
        if tag["status"] != "active":
            raise ApiError(409, "tag_inactive", f"标签 {tid} 已停用，不能打在新题上")
        conn.execute(
            "INSERT OR REPLACE INTO question_tag(question_id, tag_id, role, source, created_at) "
            "VALUES(?,?,?,?,?)", (qid, tid, role, source, ts))


def _create_question(data: dict, conn) -> str:
    """新建题目：question + version 1 + solutions + tags + occurrence，一次事务完成。"""
    qtype = _text(data.get("qtype"))
    if qtype not in QTYPES:
        raise ApiError(400, "bad_qtype", "题型必须是 single、multi、blank、solve 之一")
    stem = data.get("stem")
    if not _text(stem):
        raise ApiError(400, "missing_field", "缺少必填字段：stem")

    options = data.get("options")
    if options is not None and not isinstance(options, list):
        raise ApiError(400, "bad_field", "options 必须是数组")
    answer_keys = data.get("answer_keys")
    if answer_keys is not None and not isinstance(answer_keys, list):
        raise ApiError(400, "bad_field", "answer_keys 必须是数组")
    subquestions = data.get("subquestions")
    if subquestions is not None and not isinstance(subquestions, list):
        raise ApiError(400, "bad_field", "subquestions 必须是数组")
    material_refs = data.get("material_refs")
    if material_refs is not None and not isinstance(material_refs, list):
        raise ApiError(400, "bad_field", "material_refs 必须是数组")

    diff = data.get("difficulty") or {}
    if not isinstance(diff, dict):
        raise ApiError(400, "bad_field", "difficulty 必须是对象")
    rec_level = _text(data.get("rec_level")) or None
    if rec_level and rec_level not in ("S", "A", "B", "C"):
        raise ApiError(400, "bad_field", "rec_level 只能是 S、A、B、C")

    ts = db.now()
    qid = db.next_id(conn, "Q")
    vid = db.next_id(conn, "QV")

    conn.execute(
        "INSERT INTO question(id, short_name, status, status_reason, current_version_id, "
        "merged_into, difficulty_calc, difficulty_think, difficulty_scope, "
        "difficulty_level, difficulty_note, rec_level, rec_reason, time_solve_min, "
        "time_explain_min, note, created_at, updated_at, created_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (qid, _text(data.get("short_name")) or None, "active", None, None, None,
         _as_int(diff.get("calc"), "difficulty.calc", 1, 5),
         _as_int(diff.get("think"), "difficulty.think", 1, 5),
         _as_int(diff.get("scope"), "difficulty.scope", 1, 5),
         diff.get("level") or None, diff.get("note") or None, rec_level,
         data.get("rec_reason"), _as_float(data.get("time_solve_min"), "time_solve_min"),
         _as_float(data.get("time_explain_min"), "time_explain_min"),
         data.get("note"), ts, ts, data.get("created_by") or "教师"))

    conn.execute(
        "INSERT INTO question_version(id, question_id, version_no, qtype, stem, options, "
        "answer, answer_keys, subquestions, analysis, material_refs, teaching, "
        "total_points, change_note, review_state, created_at, created_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (vid, qid, 1, qtype, stem, db.dumps(options), data.get("answer"),
         db.dumps(answer_keys), db.dumps(subquestions), data.get("analysis"),
         db.dumps(material_refs), db.dumps(data.get("teaching")),
         _as_float(data.get("total_points"), "total_points"),
         data.get("change_note") or "初次录入", "draft", ts,
         data.get("created_by") or "教师"))

    conn.execute("UPDATE question SET current_version_id = ? WHERE id = ?", (vid, qid))

    _insert_solutions(conn, vid, data.get("solutions"), ts)
    _insert_question_tags(conn, qid, data.get("tags"), ts)

    if "occurrence" in data:
        occ = data.get("occurrence") or {}
    else:
        occ = {"pending": 1}
    _insert_occurrence(conn, qid, vid, occ, ts)

    db.audit("create_question", "question", qid,
             {"version_id": vid, "qtype": qtype, "short_name": data.get("short_name")})
    return qid


# ================================================================== §2 收题箱
@get("/api/inbox")
def inbox_list(req):
    status = req.arg("status")
    limit = req.arg("limit", 50, int)
    sql = "SELECT * FROM inbox_item WHERE 1 = 1"
    params: list = []
    if status:
        if status not in INBOX_STATUS:
            raise ApiError(400, "bad_query", f"status 取值不合法：{status}")
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    items = db.all_(sql, params)
    return {"items": items, "total": len(items)}


@post("/api/inbox")
def inbox_create(req):
    raw_text = req.body("raw_text")
    file_name = req.body("file_name")
    file_rel_path = req.body("file_rel_path")
    if not _text(raw_text) and not _text(file_name) and not _text(file_rel_path):
        raise ApiError(400, "missing_field", "收题至少要有一条原始文字或一个文件")
    status = _text(req.body("status")) or "pending"
    if status not in INBOX_STATUS:
        raise ApiError(400, "bad_field", "status 取值不合法")
    ts = db.now()
    with db.transaction() as conn:
        iid = db.next_id(conn, "INB")
        conn.execute(
            "INSERT INTO inbox_item(id, raw_text, file_name, file_rel_path, source_hint, "
            "note, status, question_id, fail_reason, operator, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, raw_text, file_name, file_rel_path,
             _text(req.body("source_hint")) or "待确认", req.body("note"), status, None,
             None, req.body("operator") or "教师", ts, ts))
        db.audit("create_inbox_item", "inbox_item", iid,
                 {"file_name": file_name, "source_hint": _text(req.body("source_hint")) or "待确认"})
    return db.one("SELECT * FROM inbox_item WHERE id = ?", (iid,))


@get("/api/inbox/{iid}")
def inbox_detail(req):
    return _inbox_or_404(req.params["iid"])


@patch("/api/inbox/{iid}")
def inbox_update(req):
    item = _inbox_or_404(req.params["iid"])
    data = req.json
    sets: list[str] = []
    params: list = []
    if "status" in data:
        status = _text(data.get("status"))
        if status not in INBOX_STATUS:
            raise ApiError(400, "bad_field", "status 取值不合法")
        sets.append("status = ?")
        params.append(status)
    for field in ("note", "fail_reason", "operator", "raw_text", "file_name",
                  "file_rel_path", "source_hint", "question_id"):
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data.get(field))
    if not sets:
        raise ApiError(400, "no_change", "没有可修改的字段")
    sets.append("updated_at = ?")
    params.append(db.now())
    params.append(item["id"])
    with db.transaction() as conn:
        conn.execute(f"UPDATE inbox_item SET {', '.join(sets)} WHERE id = ?", params)
        db.audit("update_inbox_item", "inbox_item", item["id"],
                 {k: v for k, v in data.items()
                  if k in ("status", "note", "fail_reason")})
    return _inbox_or_404(item["id"])


@post("/api/inbox/{iid}/promote")
def inbox_promote(req):
    item = _inbox_or_404(req.params["iid"])
    if item["status"] == "organized" and item["question_id"]:
        raise ApiError(409, "already_organized",
                       f"该收题条目已整理为题目 {item['question_id']}")

    payload = dict(req.json)
    occ = payload.get("occurrence")
    occ = dict(occ) if isinstance(occ, dict) else {}
    if payload.get("source_id") and not occ.get("source_id"):
        occ["source_id"] = payload["source_id"]
    if item.get("raw_text") and not _text(occ.get("raw_text")):
        occ["raw_text"] = item["raw_text"]
    if "pending" not in occ:
        occ["pending"] = 0 if occ.get("source_id") else 1
    payload["occurrence"] = occ
    payload.setdefault("created_by", item.get("operator") or "教师")

    with db.transaction() as conn:
        qid = _create_question(payload, conn)
        conn.execute(
            "UPDATE inbox_item SET question_id = ?, status = 'organized', updated_at = ? "
            "WHERE id = ?", (qid, db.now(), item["id"]))
        db.audit("promote_inbox", "inbox_item", item["id"], {"question_id": qid})
    return _detail(qid)


# ================================================================== §3 资料与出处
@get("/api/sources")
def source_list(req):
    keyword = _text(req.arg("keyword"))
    kind = req.arg("kind")
    sql = ("SELECT s.*, "
           "(SELECT COUNT(*) FROM source_file f WHERE f.source_id = s.id) AS file_count, "
           "(SELECT COUNT(*) FROM occurrence o WHERE o.source_id = s.id) AS occurrence_count "
           "FROM source s WHERE 1 = 1")
    params: list = []
    if keyword:
        sql += " AND (s.name LIKE ? OR s.publisher LIKE ? OR s.note LIKE ?)"
        like = f"%{keyword}%"
        params += [like, like, like]
    if kind:
        if kind not in SOURCE_KINDS:
            raise ApiError(400, "bad_query", f"kind 取值不合法：{kind}")
        sql += " AND s.kind = ?"
        params.append(kind)
    sql += " ORDER BY s.created_at DESC, s.id DESC"
    items = db.all_(sql, params)
    for item in items:
        item['classification'] = search_catalog.metadata(item)
    return {"items": items, "total": len(items)}


@post("/api/sources")
def source_create(req):
    name = req.body("name", required=True)
    from app.api.workspace import source_patch, SOURCE_FIELDS
    metadata = source_patch({k:v for k,v in req.json.items() if k in SOURCE_FIELDS})
    name = metadata['name']
    kind = _text(req.body("kind", "other"))
    if kind not in SOURCE_KINDS:
        raise ApiError(400, "bad_field", "kind 取值不合法")
    exam_type=_text(req.body('exam_type'))
    grade=_text(req.body('grade'))
    if exam_type and exam_type not in search_catalog.EXAM_CATEGORIES:
        raise ApiError(400,'bad_field','考试类别无效')
    if grade and grade not in search_catalog.GRADES:
        raise ApiError(400,'bad_field','年级无效')
    ts = db.now()
    with db.transaction() as conn:
        sid = db.next_id(conn, "SRC")
        conn.execute(
            "INSERT INTO source(id, name, kind, edition, region, publisher, usage_scope, "
            "license_basis, pending_note, note, created_at, exam_type, grade, school, year) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, name, kind, req.body("edition"), req.body("region"),
             req.body("publisher"), req.body("usage_scope"), req.body("license_basis"),
             req.body("pending_note"), req.body("note"), ts,exam_type or None,grade or None,metadata.get('school'),metadata.get('year')))
        db.audit("create_source", "source", sid, {"name": name, "kind": kind})
    return _source_or_404(sid)


@get("/api/sources/{sid}")
def source_detail(req):
    source = _source_or_404(req.params["sid"])
    source["files"] = db.all_(
        "SELECT * FROM source_file WHERE source_id = ? ORDER BY uploaded_at, id",
        (source["id"],))
    source["occurrences"] = db.all_(
        "SELECT o.id, o.question_id, q.short_name, o.question_version_id, o.pdf_page, "
        "o.print_page, o.qno, o.pending, o.raw_text, o.created_at "
        "FROM occurrence o LEFT JOIN question q ON q.id = o.question_id "
        "WHERE o.source_id = ? ORDER BY o.created_at, o.id", (source["id"],))
    return source


@patch("/api/sources/{sid}")
def source_update(req):
    source = _source_or_404(req.params["sid"])
    fields = ("name", "kind", "edition", "region", "publisher", "usage_scope",
              "license_basis", "pending_note", "note", "exam_type", "grade", "school", "year")
    data = dict(req.json)
    from app.api.workspace import source_patch, SOURCE_FIELDS
    changed_source = {k:v for k,v in data.items() if k in SOURCE_FIELDS}
    if changed_source:
        data.update(source_patch(changed_source))
    for field,allowed,label in [('exam_type',search_catalog.EXAM_CATEGORIES,'考试类别'),('grade',search_catalog.GRADES,'年级')]:
        if field in data:
            value=_text(data[field])
            if value and value not in allowed:raise ApiError(400,'bad_field',label+'无效')
            data[field]=value or None
    if "kind" in data and _text(data["kind"]) not in SOURCE_KINDS:
        raise ApiError(400, "bad_field", "kind 取值不合法")
    sets = [f"{f} = ?" for f in fields if f in data]
    params = [data[f] for f in fields if f in data]
    if not sets:
        raise ApiError(400, "no_change", "没有可修改的字段")
    params.append(source["id"])
    with db.transaction() as conn:
        conn.execute(f"UPDATE source SET {', '.join(sets)} WHERE id = ?", params)
        if 'name' in data:
            if not _text(data['name']):raise ApiError(400,'source_name','来源名称不能为空')
            provenance.rename_generated_titles(conn,source['id'],source['name'],data['name'])
            provenance.sync_source_jobs(conn,source['id'],data['name'])
        db.audit("update_source", "source", source["id"],
                 {f: data[f] for f in fields if f in data})
    return _source_or_404(source["id"])


def _safe_filename(name: str) -> str:
    """去掉路径分隔符与 ..，只留纯文件名。"""
    raw = _text(name).replace("\\", "/")
    raw = raw.split("/")[-1]
    raw = raw.replace("..", "").strip().strip(".")
    raw = re.sub(r'[<>:"|?*\x00-\x1f]', "_", raw)
    return raw or "file"


@post("/api/sources/{sid}/files")
def source_file_upload(req):
    source = _source_or_404(req.params["sid"])
    filename = _safe_filename(req.body("filename", required=True))
    content_b64 = req.body("content_b64", required=True)
    # 允许客户端按 76 列换行的 base64
    content_b64 = re.sub(r"\s+", "", str(content_b64))
    try:
        blob = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, "bad_base64", "content_b64 不是合法的 base64 内容")

    digest = hashlib.sha256(blob).hexdigest()
    exists = db.one("SELECT id, filename FROM source_file WHERE source_id = ? AND sha256 = ?",
                    (source["id"], digest))
    if exists:
        raise ApiError(409, "duplicate_file", "该文件已登记过",
                       {"file_id": exists["id"], "filename": exists["filename"]})

    folder = db.store_dir("sources") / source["id"]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    if target.exists():
        stem, dot, ext = filename.rpartition(".")
        if not dot:
            stem, ext = filename, ""
            dot = ""
        target = folder / f"{stem}-{digest[:8]}{dot}{ext}"
    target.write_bytes(blob)
    rel_path = db.rel_to_root(target)

    ts = db.now()
    with db.transaction() as conn:
        fid = db.next_id(conn, "SF")
        conn.execute(
            "INSERT INTO source_file(id, source_id, filename, rel_path, sha256, bytes, "
            "pages, uploaded_at, note) VALUES(?,?,?,?,?,?,?,?,?)",
            (fid, source["id"], target.name, rel_path, digest, len(blob),
             _as_int(req.body("pages"), "pages"), ts, req.body("note")))
        db.audit("upload_source_file", "source_file", fid,
                 {"source_id": source["id"], "filename": target.name, "sha256": digest,
                  "bytes": len(blob)})
    row = db.one("SELECT * FROM source_file WHERE id = ?", (fid,))
    row["url"] = "/files/" + rel_path
    return row


@get("/api/sources/{sid}/files")
def source_file_list(req):
    source = _source_or_404(req.params["sid"])
    items = db.all_(
        "SELECT * FROM source_file WHERE source_id = ? ORDER BY uploaded_at, id",
        (source["id"],))
    for item in items:
        item["url"] = "/files/" + item["rel_path"]
    return {"items": items, "total": len(items)}


@post("/api/occurrences")
def occurrence_create(req):
    qid = _text(req.body("question_id", required=True))
    question = _question_or_404(qid)
    if question.get("merged_into"):
        raise ApiError(409, "merged", f"题目 {qid} 已合并到 {question['merged_into']}，请改挂到目标题")
    version_id = _text(req.body("version_id")) or question.get("current_version_id")
    if version_id and db.one("SELECT 1 AS x FROM question_version WHERE id = ?",
                             (version_id,)) is None:
        raise ApiError(404, "not_found", f"题目版本 {version_id} 不存在")
    ts = db.now()
    with db.transaction() as conn:
        oid = _insert_occurrence(conn, qid, version_id, req.json, ts)
        db.audit("create_occurrence", "occurrence", oid,
                 {"question_id": qid, "source_id": req.body("source_id")})
    return _occurrence_or_404(oid)


@patch("/api/occurrences/{oid}")
def occurrence_update(req):
    occ = _occurrence_or_404(req.params["oid"])
    data = req.json
    fields = ("question_version_id", "source_id", "source_file_id", "pdf_page",
              "print_page", "qno", "screenshot_asset_id", "raw_text", "ocr_tool",
              "ocr_at", "ocr_suspect", "pending", "note")
    if "source_id" in data and data["source_id"]:
        _source_or_404(data["source_id"])
    if "question_version_id" in data and data["question_version_id"]:
        if db.one("SELECT 1 AS x FROM question_version WHERE id = ?",
                  (data["question_version_id"],)) is None:
            raise ApiError(404, "not_found",
                           f"题目版本 {data['question_version_id']} 不存在")
    sets = [f"{f} = ?" for f in fields if f in data]
    params = [data[f] for f in fields if f in data]
    if not sets:
        raise ApiError(400, "no_change", "没有可修改的字段")

    source_id = data.get("source_id", occ.get("source_id"))
    pending = _as_int(data.get("pending", occ.get("pending") or 0), "pending") or 0
    if not source_id and not pending:
        raise ApiError(400, "bad_occurrence",
                       "出处既没有 source_id 也没有标记为待确认（pending=1）")

    params.append(occ["id"])
    with db.transaction() as conn:
        conn.execute(f"UPDATE occurrence SET {', '.join(sets)} WHERE id = ?", params)
        db.audit("update_occurrence", "occurrence", occ["id"],
                 {f: data[f] for f in fields if f in data})
    return _occurrence_or_404(occ["id"])


@delete("/api/occurrences/{oid}")
def occurrence_delete(req):
    occ = _occurrence_or_404(req.params["oid"])
    others = db.scalar("SELECT COUNT(*) FROM occurrence WHERE question_id = ? AND id <> ?",
                       (occ["question_id"], occ["id"]))
    if not others:
        raise ApiError(409, "last_occurrence",
                       "题目至少保留一个出处；如果出处不明，请先把这条出处标为待确认")
    with db.transaction() as conn:
        conn.execute("DELETE FROM occurrence WHERE id = ?", (occ["id"],))
        db.audit("delete_occurrence", "occurrence", occ["id"],
                 {"question_id": occ["question_id"], "source_id": occ["source_id"]})
    return {"ok": True, "deleted": occ["id"]}


# ================================================================== §4 题目与版本
@post("/api/questions")
def question_create(req):
    with db.transaction() as conn:
        qid = _create_question(req.json, conn)
    return _detail(qid)


@get("/api/questions/{qid}")
def question_detail(req):
    return _detail(req.params["qid"])


@patch("/api/questions/{qid}")
def question_update(req):
    question = _question_or_404(req.params["qid"])
    data = req.json
    for field in VERSION_FIELDS:
        if field in data:
            raise ApiError(400, "content_locked",
                           f"题目内容（{field}）不能直接修改，请通过 "
                           f"POST /api/questions/{question['id']}/versions 新建版本")
    if "merged_into" in data:
        raise ApiError(400, "content_locked", "合并请使用 POST /api/questions/{id}/merge")

    if "status" in data and _text(data["status"]) not in ("active", "inactive"):
        raise ApiError(400, "bad_field", "status 只能是 active 或 inactive")
    if "rec_level" in data:
        rec = _text(data["rec_level"]) or None
        if rec and rec not in ("S", "A", "B", "C"):
            raise ApiError(400, "bad_field", "rec_level 只能是 S、A、B、C")
    for field in ("difficulty_calc", "difficulty_think", "difficulty_scope"):
        if field in data:
            _as_int(data[field], field, 1, 5)
    for field in ("time_solve_min", "time_explain_min"):
        if field in data:
            _as_float(data[field], field)

    sets: list[str] = []
    params: list = []
    for field in MANAGE_FIELDS:
        if field in data:
            value = data[field]
            if field in ("difficulty_calc", "difficulty_think", "difficulty_scope"):
                value = _as_int(value, field, 1, 5)
            elif field in ("time_solve_min", "time_explain_min"):
                value = _as_float(value, field)
            sets.append(f"{field} = ?")
            params.append(value)
    if not sets:
        raise ApiError(400, "no_change", "没有可修改的管理字段")
    sets.append("updated_at = ?")
    params.append(db.now())
    params.append(question["id"])
    with db.transaction() as conn:
        conn.execute(f"UPDATE question SET {', '.join(sets)} WHERE id = ?", params)
        db.audit("update_question", "question", question["id"],
                 {f: data[f] for f in MANAGE_FIELDS if f in data})
    return _detail(question["id"])


@post("/api/questions/{qid}/versions")
def question_new_version(req):
    question = _question_or_404(req.params["qid"])
    if question.get("merged_into"):
        raise ApiError(409, "merged",
                       f"题目 {question['id']} 已合并到 {question['merged_into']}，不能再建版本")
    base = _current_version_of(question)
    data = req.json

    qtype = _text(data.get("qtype")) if "qtype" in data else base["qtype"]
    if qtype not in QTYPES:
        raise ApiError(400, "bad_qtype", "题型必须是 single、multi、blank、solve 之一")
    stem = data.get("stem") if "stem" in data else base["stem"]
    if not _text(stem):
        raise ApiError(400, "missing_field", "缺少必填字段：stem")
    if "options" in data and data["options"] is not None and not isinstance(data["options"], list):
        raise ApiError(400, "bad_field", "options 必须是数组")
    if "material_refs" in data and data["material_refs"] is not None \
            and not isinstance(data["material_refs"], list):
        raise ApiError(400, "bad_field", "material_refs 必须是数组")

    ts = db.now()
    with db.transaction() as conn:
        next_no = (conn.execute(
            "SELECT MAX(version_no) AS n FROM question_version WHERE question_id = ?",
            (question["id"],)).fetchone()["n"] or 0) + 1
        vid = db.next_id(conn, "QV")
        # 以当前版本为底，按 body 覆盖
        merged_fields: dict = {}
        for field in VERSION_FIELDS:
            merged_fields[field] = data[field] if field in data else base[field]
        conn.execute(
            "INSERT INTO question_version(id, question_id, version_no, qtype, stem, options, "
            "answer, answer_keys, subquestions, analysis, material_refs, teaching, "
            "total_points, change_note, review_state, created_at, created_by) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, question["id"], next_no, qtype, stem,
             db.dumps(merged_fields.get("options")), merged_fields.get("answer"),
             db.dumps(merged_fields.get("answer_keys")),
             db.dumps(merged_fields.get("subquestions")), merged_fields.get("analysis"),
             db.dumps(merged_fields.get("material_refs")),
             db.dumps(merged_fields.get("teaching")),
             _as_float(merged_fields.get("total_points"), "total_points"),
             data.get("change_note") or f"第 {next_no} 版",
             "draft", ts, data.get("created_by") or "教师"))

        # 旧版本封存，审校记录留在旧版本上
        conn.execute("UPDATE question_version SET review_state = 'submitted' WHERE id = ?",
                     (base["id"],))

        if "solutions" in data:
            _insert_solutions(conn, vid, data.get("solutions"), ts)
        else:
            old = conn.execute(
                "SELECT * FROM solution WHERE question_version_id = ? "
                "ORDER BY order_no, id", (base["id"],)).fetchall()
            for row in old:
                sid = db.next_id(conn, "SOL")
                conn.execute(
                    "INSERT INTO solution(id, question_version_id, label, approach, steps, "
                    "conditions, is_primary, order_no, teacher_tip, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, vid, row["label"], row["approach"], row["steps"],
                     row["conditions"], row["is_primary"], row["order_no"],
                     row["teacher_tip"], ts, ts))
                for tag_row in conn.execute(
                        "SELECT tag_id FROM solution_tag WHERE solution_id = ?",
                        (row["id"],)).fetchall():
                    conn.execute(
                        "INSERT OR IGNORE INTO solution_tag(solution_id, tag_id, created_at) "
                        "VALUES(?,?,?)", (sid, tag_row["tag_id"], ts))

        conn.execute("UPDATE question SET current_version_id = ?, updated_at = ? WHERE id = ?",
                     (vid, ts, question["id"]))
        db.audit("new_version", "question", question["id"],
                 {"version_id": vid, "version_no": next_no,
                  "overridden": [f for f in VERSION_FIELDS if f in data],
                  "change_note": data.get("change_note")})
    return _detail(question["id"])


@get("/api/questions/{qid}/versions/{no}")
def question_version_detail(req):
    question = _question_or_404(req.params["qid"])
    try:
        version_no = int(req.params["no"])
    except (TypeError, ValueError):
        raise ApiError(400, "bad_query", "版本号必须是整数")
    row = db.one("SELECT * FROM question_version WHERE question_id = ? AND version_no = ?",
                 (question["id"], version_no))
    if row is None:
        raise ApiError(404, "not_found",
                       f"题目 {question['id']} 没有第 {version_no} 版")
    status = _review_status(row["id"], question["id"])
    out = dict(row)
    out["solutions"] = _solutions_of(row["id"])
    out["tags"] = _question_tags(question["id"])
    out["review_status"] = status
    out["eligibility"] = _eligibility(status)
    out["is_current"] = (question.get("current_version_id") == row["id"])
    return out


@get("/api/questions/{qid}/diff")
def question_diff(req):
    question = _question_or_404(req.params["qid"])
    try:
        left = int(req.arg("from", None, int) or 1)
        right = int(req.arg("to", None, int) or 0)
    except (TypeError, ValueError):
        raise ApiError(400, "bad_query", "from / to 必须是版本号整数")
    rows = {r["version_no"]: r for r in db.all_(
        "SELECT * FROM question_version WHERE question_id = ?", (question["id"],))}
    if right == 0:
        if question.get("current_version_id"):
            right = next((r["version_no"] for r in rows.values()
                          if r["id"] == question["current_version_id"]), 0)
        right = right or max(rows)
    if left not in rows:
        raise ApiError(404, "not_found", f"题目 {question['id']} 没有第 {left} 版")
    if right not in rows:
        raise ApiError(404, "not_found", f"题目 {question['id']} 没有第 {right} 版")

    out_fields = []
    changed = []
    for field in VERSION_FIELDS:
        before, after = rows[left][field], rows[right][field]
        differs = before != after
        if differs:
            changed.append(field)
        out_fields.append({"field": field, "from": before, "to": after, "changed": differs})
    return {"from": {"version_no": left, "id": rows[left]["id"]},
            "to": {"version_no": right, "id": rows[right]["id"]},
            "changed": changed, "fields": out_fields}


@post("/api/questions/{qid}/merge")
def question_merge(req):
    question = _question_or_404(req.params["qid"])
    into = _text(req.body("into", required=True))
    if into == question["id"]:
        raise ApiError(400, "bad_merge", "不能把题目合并到自身")
    target = _question_or_404(into)
    if question.get("merged_into"):
        raise ApiError(409, "already_merged",
                       f"题目 {question['id']} 已合并到 {question['merged_into']}")
    if target.get("merged_into"):
        raise ApiError(409, "target_merged",
                       f"目标题目 {into} 已合并到 {target['merged_into']}，请直接合并到最终题目")
    note = req.body("note")
    ts = db.now()
    with db.transaction() as conn:
        moved = conn.execute("UPDATE occurrence SET question_id = ? WHERE question_id = ?",
                             (into, question["id"])).rowcount
        tags = conn.execute(
            "SELECT tag_id, role, source, created_at FROM question_tag WHERE question_id = ?",
            (question["id"],)).fetchall()
        for row in tags:
            conn.execute(
                "INSERT OR IGNORE INTO question_tag(question_id, tag_id, role, source, created_at) "
                "VALUES(?,?,?,?,?)",
                (into, row["tag_id"], row["role"], row["source"], row["created_at"]))
        conn.execute("DELETE FROM question_tag WHERE question_id = ?", (question["id"],))

        # 关系两侧改挂；先删掉会变成自指的两条，再去重
        conn.execute("DELETE FROM relation WHERE (from_id = ? AND to_id = ?) "
                     "OR (from_id = ? AND to_id = ?)",
                     (question["id"], into, into, question["id"]))
        moved_rel = conn.execute("SELECT COUNT(*) AS n FROM relation WHERE from_id = ? OR to_id = ?",
                                 (question["id"], question["id"])).fetchone()["n"]
        conn.execute("UPDATE relation SET from_id = ? WHERE from_id = ?",
                     (into, question["id"]))
        conn.execute("UPDATE relation SET to_id = ? WHERE to_id = ?",
                     (into, question["id"]))
        conn.execute(
            "DELETE FROM relation WHERE (from_id = ? OR to_id = ?) AND id NOT IN ("
            "  SELECT MIN(id) FROM relation WHERE from_id = ? OR to_id = ? "
            "  GROUP BY from_id, to_id, rel_type)",
            (into, into, into, into))

        conn.execute("UPDATE question SET merged_into = ?, status = 'inactive', "
                     "status_reason = ?, updated_at = ? WHERE id = ?",
                     (into, f"已合并到 {into}", ts, question["id"]))
        db.audit("merge_question", "question", question["id"],
                 {"into": into, "note": note, "moved_occurrences": moved,
                  "moved_tags": len(tags), "moved_relations": moved_rel})
    return {"ok": True, "merged_into": into, "moved_occurrences": moved,
            "moved_tags": len(tags), "question": _detail(into)}


@post("/api/questions/{qid}/deactivate")
def question_deactivate(req):
    question = _question_or_404(req.params["qid"])
    reason = _text(req.body("reason"))
    if not reason:
        raise ApiError(400, "missing_field", "停用必须填写原因")
    with db.transaction() as conn:
        conn.execute("UPDATE question SET status = 'inactive', status_reason = ?, "
                     "updated_at = ? WHERE id = ?", (reason, db.now(), question["id"]))
        db.audit("deactivate_question", "question", question["id"], {"reason": reason})
    return _detail(question["id"])


@post("/api/questions/{qid}/activate")
def question_activate(req):
    question = _question_or_404(req.params["qid"])
    if question.get("merged_into"):
        raise ApiError(409, "merged",
                       f"题目 {question['id']} 已合并到 {question['merged_into']}，不能恢复")
    with db.transaction() as conn:
        conn.execute("UPDATE question SET status = 'active', status_reason = NULL, "
                     "updated_at = ? WHERE id = ?", (db.now(), question["id"]))
        db.audit("activate_question", "question", question["id"], None)
    return _detail(question["id"])


def _check_target(target_type: str, target_id: str) -> None:
    table = REVIEW_TARGETS.get(target_type)
    if table is None:
        raise ApiError(400, "bad_field",
                       "target_type 只能是 question_version、solution、asset")
    if db.one(f"SELECT 1 AS x FROM {table} WHERE id = ?", (target_id,)) is None:
        raise ApiError(404, "not_found", f"{target_type} {target_id} 不存在")


@post("/api/reviews")
def review_create(req):
    target_type = _text(req.body("target_type", required=True))
    target_id = _text(req.body("target_id", required=True))
    item = _text(req.body("item", required=True))
    result = _text(req.body("result", required=True))
    if item not in REVIEW_ITEMS:
        raise ApiError(400, "bad_field", f"item 取值不合法：{item}")
    if result not in REVIEW_RESULTS:
        raise ApiError(400, "bad_field", f"result 取值不合法：{result}")
    _check_target(target_type, target_id)
    ts = db.now()
    with db.transaction() as conn:
        rid = db.next_id(conn, "REV")
        conn.execute(
            "INSERT INTO review(id, target_type, target_id, item, result, reviewer, note, "
            "created_at) VALUES(?,?,?,?,?,?,?,?)",
            (rid, target_type, target_id, item, result,
             req.body("reviewer") or "教师", req.body("note"), ts))
        db.audit("create_review", target_type, target_id,
                 {"review_id": rid, "item": item, "result": result})
    return db.one("SELECT * FROM review WHERE id = ?", (rid,))


@get("/api/reviews")
def review_list(req):
    target_type = req.arg("target_type")
    target_id = req.arg("target_id")
    item = req.arg("item")
    limit = req.arg("limit", 100, int)
    sql = "SELECT * FROM review WHERE 1 = 1"
    params: list = []
    if target_type:
        sql += " AND target_type = ?"
        params.append(target_type)
    if target_id:
        sql += " AND target_id = ?"
        params.append(target_id)
    if item:
        sql += " AND item = ?"
        params.append(item)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    items = db.all_(sql, params)
    return {"items": items, "total": len(items)}


# ================================================================== §5 解法
@post("/api/versions/{vid}/solutions")
def solution_create(req):
    version = _version_or_404(req.params["vid"])
    question = _question_or_404(version["question_id"])
    _assert_editable_version(question, version["id"])

    data = req.json
    label = _text(data.get("label")) or "解法"
    steps = data.get("steps")
    if not _text(steps):
        raise ApiError(400, "missing_field", "解法缺少 steps")
    ts = db.now()
    with db.transaction() as conn:
        order_no = data.get("order_no")
        if order_no is None:
            order_no = (conn.execute(
                "SELECT COALESCE(MAX(order_no), -1) + 1 AS n FROM solution "
                "WHERE question_version_id = ?", (version["id"],)).fetchone()["n"])
        is_primary = 1 if data.get("is_primary") else 0
        sid = db.next_id(conn, "SOL")
        if is_primary:
            conn.execute("UPDATE solution SET is_primary = 0 WHERE question_version_id = ?",
                         (version["id"],))
        connector = conn.execute("SELECT COUNT(*) AS n FROM solution WHERE question_version_id = ?",
                                 (version["id"],)).fetchone()["n"]
        if connector == 0:
            is_primary = 1
        conn.execute(
            "INSERT INTO solution(id, question_version_id, label, approach, steps, conditions, "
            "is_primary, order_no, teacher_tip, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sid, version["id"], label, data.get("approach"), steps,
             data.get("conditions"), is_primary, order_no, data.get("teacher_tip"), ts, ts))
        for tid in (data.get("method_tags") or []):
            _link_solution_tag(conn, sid, tid, ts)
        db.audit("create_solution", "solution", sid,
                 {"question_version_id": version["id"], "label": label})
    row = db.one("SELECT * FROM solution WHERE id = ?", (sid,))
    row["tags"] = db.all_(
        "SELECT t.id, t.name, t.dimension FROM solution_tag st JOIN tag t ON t.id = st.tag_id "
        "WHERE st.solution_id = ? ORDER BY t.id", (sid,))
    return row


@patch("/api/solutions/{sid}")
def solution_update(req):
    solution = _solution_or_404(req.params["sid"])
    version = _version_or_404(solution["question_version_id"])
    question = _question_or_404(version["question_id"])
    _assert_editable_version(question, version["id"])

    data = req.json
    fields = ("label", "approach", "steps", "conditions", "is_primary", "order_no",
              "teacher_tip")
    if "steps" in data and not _text(data["steps"]):
        raise ApiError(400, "bad_field", "解法步骤不能为空")
    sets = [f"{f} = ?" for f in fields if f in data]
    params = []
    for field in fields:
        if field in data:
            value = data[field]
            if field == "is_primary":
                value = 1 if value else 0
            if field == "order_no" and value is not None:
                value = _as_int(value, "order_no")
            params.append(value)
    if not sets:
        raise ApiError(400, "no_change", "没有可修改的字段")

    ts = db.now()
    sets.append("updated_at = ?")
    params.append(ts)
    params.append(solution["id"])
    with db.transaction() as conn:
        conn.execute(f"UPDATE solution SET {', '.join(sets)} WHERE id = ?", params)
        if data.get("is_primary"):
            conn.execute(
                "UPDATE solution SET is_primary = 0 "
                "WHERE question_version_id = ? AND id <> ?",
                (version["id"], solution["id"]))
        db.audit("update_solution", "solution", solution["id"],
                 {f: data[f] for f in fields if f in data})
    row = db.one("SELECT * FROM solution WHERE id = ?", (solution["id"],))
    row["tags"] = db.all_(
        "SELECT t.id, t.name, t.dimension FROM solution_tag st JOIN tag t ON t.id = st.tag_id "
        "WHERE st.solution_id = ? ORDER BY t.id", (solution["id"],))
    return row


@delete("/api/solutions/{sid}")
def solution_delete(req):
    solution = _solution_or_404(req.params["sid"])
    version = _version_or_404(solution["question_version_id"])
    question = _question_or_404(version["question_id"])
    _assert_editable_version(question, version["id"])
    with db.transaction() as conn:
        conn.execute("DELETE FROM solution_tag WHERE solution_id = ?", (solution["id"],))
        conn.execute("DELETE FROM solution WHERE id = ?", (solution["id"],))
        if solution["is_primary"]:
            first = conn.execute(
                "SELECT id FROM solution WHERE question_version_id = ? "
                "ORDER BY order_no, id LIMIT 1", (version["id"],)).fetchone()
            if first:
                conn.execute("UPDATE solution SET is_primary = 1 WHERE id = ?", (first["id"],))
        db.audit("delete_solution", "solution", solution["id"],
                 {"question_version_id": version["id"], "label": solution["label"]})
    return {"ok": True, "deleted": solution["id"]}


@post("/api/solutions/{sid}/primary")
def solution_primary(req):
    solution = _solution_or_404(req.params["sid"])
    version = _version_or_404(solution["question_version_id"])
    question = _question_or_404(version["question_id"])
    _assert_editable_version(question, version["id"])
    with db.transaction() as conn:
        conn.execute("UPDATE solution SET is_primary = 0 WHERE question_version_id = ?",
                     (version["id"],))
        conn.execute("UPDATE solution SET is_primary = 1, updated_at = ? WHERE id = ?",
                     (db.now(), solution["id"]))
        db.audit("set_primary_solution", "solution", solution["id"],
                 {"question_version_id": version["id"]})
    return db.one("SELECT * FROM solution WHERE id = ?", (solution["id"],))


@post("/api/solutions/{sid}/tags")
def solution_add_tag(req):
    solution = _solution_or_404(req.params["sid"])
    version = _version_or_404(solution["question_version_id"])
    question = _question_or_404(version["question_id"])
    _assert_editable_version(question, version["id"])
    tag_id = _text(req.body("tag_id", required=True))
    ts = db.now()
    with db.transaction() as conn:
        _link_solution_tag(conn, solution["id"], tag_id, ts)
        db.audit("tag_solution", "solution", solution["id"], {"tag_id": tag_id})
    row = db.one("SELECT * FROM solution WHERE id = ?", (solution["id"],))
    row["tags"] = db.all_(
        "SELECT t.id, t.name, t.dimension FROM solution_tag st JOIN tag t ON t.id = st.tag_id "
        "WHERE st.solution_id = ? ORDER BY t.id", (solution["id"],))
    return row


# ================================================================== §6 标签
@get("/api/tags")
def tag_list(req):
    dimension = req.arg("dimension")
    status = req.arg("status")
    sql = ("SELECT t.*, "
           "(SELECT COUNT(DISTINCT qt.question_id) FROM question_tag qt "
           " WHERE qt.tag_id = t.id) AS question_count "
           "FROM tag t WHERE 1 = 1")
    params: list = []
    if dimension:
        if dimension not in TAG_DIMENSIONS:
            raise ApiError(400, "bad_query", f"dimension 取值不合法：{dimension}")
        sql += " AND t.dimension = ?"
        params.append(dimension)
    if status:
        if status not in ("active", "inactive"):
            raise ApiError(400, "bad_query", "status 只能是 active 或 inactive")
        sql += " AND t.status = ?"
        params.append(status)
    sql += " ORDER BY t.dimension, t.name, t.id"
    items = db.all_(sql, params)
    return {"items": items, "total": len(items)}


@post("/api/tags")
def tag_create(req):
    dimension = _text(req.body("dimension", required=True))
    name = _text(req.body("name", required=True))
    if dimension not in TAG_DIMENSIONS:
        raise ApiError(400, "bad_field",
                       "dimension 只能是 knowledge、textbook、prereq、method、idea、purpose、error")
    parent_id = _text(req.body("parent_id")) or None
    if parent_id:
        _tag_or_404(parent_id)
    dup = db.one("SELECT id FROM tag WHERE dimension = ? AND name = ?", (dimension, name))
    if dup:
        raise ApiError(409, "duplicate_tag", f"同维度下已有同名标签：{dup['id']}",
                       {"tag_id": dup["id"]})
    ts = db.now()
    with db.transaction() as conn:
        tid = db.next_id(conn, "TAG")
        conn.execute(
            "INSERT INTO tag(id, dimension, name, definition, parent_id, status, merged_into, "
            "note, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (tid, dimension, name, req.body("definition"), parent_id, "active", None,
             req.body("note"), ts))
        db.audit("create_tag", "tag", tid, {"dimension": dimension, "name": name})
    return _tag_or_404(tid)


@patch("/api/tags/{tid}")
def tag_update(req):
    tag = _tag_or_404(req.params["tid"])
    data = req.json
    fields = ("name", "definition", "parent_id", "status", "note")
    if "status" in data and _text(data["status"]) not in ("active", "inactive"):
        raise ApiError(400, "bad_field", "status 只能是 active 或 inactive")
    if "parent_id" in data and data["parent_id"]:
        if data["parent_id"] == tag["id"]:
            raise ApiError(400, "bad_field", "标签不能以自己为父节点")
        _tag_or_404(data["parent_id"])
    if "name" in data:
        name = _text(data["name"])
        if not name:
            raise ApiError(400, "bad_field", "标签名不能为空")
        dup = db.one("SELECT id FROM tag WHERE dimension = ? AND name = ? AND id <> ?",
                     (tag["dimension"], name, tag["id"]))
        if dup:
            raise ApiError(409, "duplicate_tag", f"同维度下已有同名标签：{dup['id']}",
                           {"tag_id": dup["id"]})
    sets = [f"{f} = ?" for f in fields if f in data]
    params = [data[f] for f in fields if f in data]
    if not sets:
        raise ApiError(400, "no_change", "没有可修改的字段")
    params.append(tag["id"])
    with db.transaction() as conn:
        conn.execute(f"UPDATE tag SET {', '.join(sets)} WHERE id = ?", params)
        db.audit("update_tag", "tag", tag["id"], {f: data[f] for f in fields if f in data})
    return _tag_or_404(tag["id"])


@post("/api/tags/{tid}/merge")
def tag_merge(req):
    tag = _tag_or_404(req.params["tid"])
    into = _text(req.body("into", required=True))
    if into == tag["id"]:
        raise ApiError(400, "bad_merge", "不能把标签合并到自身")
    target = _tag_or_404(into)
    if tag.get("merged_into"):
        raise ApiError(409, "already_merged",
                       f"标签 {tag['id']} 已合并到 {tag['merged_into']}")
    if target.get("merged_into"):
        raise ApiError(409, "target_merged",
                       f"目标标签 {into} 已合并到 {target['merged_into']}")
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO question_tag(question_id, tag_id, role, source, created_at) "
            "SELECT question_id, ?, role, source, created_at FROM question_tag WHERE tag_id = ?",
            (into, tag["id"]))
        moved_q = conn.execute("SELECT COUNT(*) AS n FROM question_tag WHERE tag_id = ?",
                               (tag["id"],)).fetchone()["n"]
        conn.execute("DELETE FROM question_tag WHERE tag_id = ?", (tag["id"],))
        conn.execute(
            "INSERT OR IGNORE INTO solution_tag(solution_id, tag_id, created_at) "
            "SELECT solution_id, ?, created_at FROM solution_tag WHERE tag_id = ?",
            (into, tag["id"]))
        conn.execute("DELETE FROM solution_tag WHERE tag_id = ?", (tag["id"],))
        conn.execute("UPDATE tag SET merged_into = ?, status = 'inactive' WHERE id = ?",
                     (into, tag["id"]))
        db.audit("merge_tag", "tag", tag["id"],
                 {"into": into, "moved_question_tags": moved_q})
    return _tag_or_404(into)


@post("/api/questions/{qid}/tags")
def question_add_tag(req):
    question = _question_or_404(req.params["qid"])
    tag_id = _text(req.body("tag_id", required=True))
    role = _text(req.body("role")) or "primary"
    if role not in ("primary", "secondary"):
        raise ApiError(400, "bad_field", "role 只能是 primary 或 secondary")
    source = _text(req.body("source")) or "manual"
    if source not in ("manual", "ai"):
        raise ApiError(400, "bad_field", "source 只能是 manual 或 ai")
    ts = db.now()
    with db.transaction() as conn:
        _insert_question_tags(conn, question["id"],
                              [{"tag_id": tag_id, "role": role, "source": source}], ts)
        db.audit("tag_question", "question", question["id"],
                 {"tag_id": tag_id, "role": role})
    return {"question_id": question["id"], "tags": _question_tags(question["id"])}


@delete("/api/questions/{qid}/tags/{tid}")
def question_remove_tag(req):
    question = _question_or_404(req.params["qid"])
    tid = req.params["tid"]
    role = req.arg("role")
    if role and role not in ("primary", "secondary"):
        raise ApiError(400, "bad_query", "role 只能是 primary 或 secondary")
    sql = "DELETE FROM question_tag WHERE question_id = ? AND tag_id = ?"
    params: list = [question["id"], tid]
    if role:
        sql += " AND role = ?"
        params.append(role)
    with db.transaction() as conn:
        removed = conn.execute(sql, params).rowcount
        if removed == 0:
            raise ApiError(404, "not_found",
                           f"题目 {question['id']} 没有标签 {tid}"
                           + (f"（role={role}）" if role else ""))
        db.audit("untag_question", "question", question["id"],
                 {"tag_id": tid, "role": role})
    return {"ok": True, "removed": removed, "tags": _question_tags(question["id"])}


# ================================================================== §7 关系
def _graph() -> dict[str, list[str]]:
    edges: dict[str, list[str]] = {}
    rows = db.all_(
        "SELECT from_id, to_id FROM relation WHERE rel_type IN (" + _ph(len(DIRECTED_REL)) + ")",
        list(DIRECTED_REL))
    for row in rows:
        edges.setdefault(row["from_id"], []).append(row["to_id"])
    return edges


def _find_path(edges: dict[str, list[str]], start: str, goal: str) -> list[str] | None:
    """DFS 找一条 start → goal 的路径，找不到返回 None。"""
    stack: list[tuple[str, list[str]]] = [(start, [start])]
    seen: set[str] = set()
    while stack:
        node, path = stack.pop()
        if node == goal:
            return path
        if node in seen:
            continue
        seen.add(node)
        for nxt in edges.get(node, []):
            if nxt not in seen:
                stack.append((nxt, path + [nxt]))
    return None


@post("/api/relations")
def relation_create(req):
    from_id = _text(req.body("from_id", required=True))
    to_id = _text(req.body("to_id", required=True))
    rel_type = _text(req.body("rel_type", required=True))
    if rel_type not in REL_TYPES:
        raise ApiError(400, "bad_field", f"rel_type 取值不合法：{rel_type}")
    if from_id == to_id:
        raise ApiError(400, "bad_relation", "题目关系不能指向自身")

    source = _question_or_404(from_id)
    target = _question_or_404(to_id)
    for item in (source, target):
        if item.get("merged_into"):
            raise ApiError(409, "merged",
                           f"题目 {item['id']} 已合并到 {item['merged_into']}，请改在目标题上建关系")

    dup = db.one(
        "SELECT id FROM relation WHERE ((from_id = ? AND to_id = ?) "
        "OR (from_id = ? AND to_id = ?)) AND rel_type = ?",
        (from_id, to_id, to_id, from_id, rel_type))
    if dup:
        raise ApiError(409, "duplicate_relation", f"题目之间已有同类关系：{dup['id']}",
                       {"relation_id": dup["id"]})

    if rel_type in DIRECTED_REL:
        path = _find_path(_graph(), to_id, from_id)
        if path:
            chain = " → ".join([from_id] + path)
            raise ApiError(409, "relation_cycle", f"该关系会形成环路：{chain}",
                           {"cycle": chain})

    note = req.body("note")
    confirmed = 1 if req.body("confirmed") else 0
    ts = db.now()
    with db.transaction() as conn:
        rid = db.next_id(conn, "REL")
        conn.execute(
            "INSERT INTO relation(id, from_id, to_id, rel_type, note, confirmed, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (rid, from_id, to_id, rel_type, note, confirmed, ts))
        reverse_id = None
        if rel_type in SYMMETRIC_REL:
            reverse_id = db.next_id(conn, "REL")
            conn.execute(
                "INSERT INTO relation(id, from_id, to_id, rel_type, note, confirmed, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (reverse_id, to_id, from_id, rel_type, note, confirmed, ts))
        db.audit("create_relation", "relation", rid,
                 {"from_id": from_id, "to_id": to_id, "rel_type": rel_type,
                  "reverse_id": reverse_id})
    row = db.one("SELECT * FROM relation WHERE id = ?", (rid,))
    row["reverse_id"] = reverse_id
    return row


@patch("/api/relations/{rid}")
def relation_update(req):
    relation = _relation_or_404(req.params["rid"])
    data = req.json
    sets: list[str] = []
    values: list = []
    if "note" in data:
        sets.append("note = ?")
        values.append(data["note"])
    if "confirmed" in data:
        sets.append("confirmed = ?")
        values.append(1 if data["confirmed"] else 0)
    if not sets:
        raise ApiError(400, "no_change", "只允许修改 note 与 confirmed")
    with db.transaction() as conn:
        conn.execute(f"UPDATE relation SET {', '.join(sets)} WHERE id = ?",
                     values + [relation["id"]])
        # 对称关系的两行说明与确认状态保持一致
        if relation["rel_type"] in SYMMETRIC_REL:
            conn.execute(
                f"UPDATE relation SET {', '.join(sets)} "
                "WHERE from_id = ? AND to_id = ? AND rel_type = ?",
                values + [relation["to_id"], relation["from_id"], relation["rel_type"]])
        db.audit("update_relation", "relation", relation["id"],
                 {f: data[f] for f in ("note", "confirmed") if f in data})
    return _relation_or_404(relation["id"])


@delete("/api/relations/{rid}")
def relation_delete(req):
    relation = _relation_or_404(req.params["rid"])
    deleted = [relation["id"]]
    with db.transaction() as conn:
        conn.execute("DELETE FROM relation WHERE id = ?", (relation["id"],))
        if relation["rel_type"] in SYMMETRIC_REL:
            reverse = conn.execute(
                "SELECT id FROM relation WHERE from_id = ? AND to_id = ? AND rel_type = ?",
                (relation["to_id"], relation["from_id"], relation["rel_type"])).fetchone()
            if reverse:
                conn.execute("DELETE FROM relation WHERE id = ?", (reverse["id"],))
                deleted.append(reverse["id"])
        db.audit("delete_relation", "relation", relation["id"],
                 {"rel_type": relation["rel_type"], "deleted": deleted})
    return {"ok": True, "deleted": deleted}


# ================================================================== §8 检索
LATEX_CMD = re.compile(r"\\[a-zA-Z]+\*?")
NOISE = re.compile(r"[$\\{}^_~\s，。、；：！？,.;:!?（）()【】\[\]「」《》\"'“”‘’·—…]+")


def _normalize_text(text) -> str:
    """题面规范化：去 $、去 LaTeX 命令与花括号、去空白与常见标点。"""
    if not text:
        return ""
    flat = str(text).replace("\\$", "$")
    flat = LATEX_CMD.sub(" ", flat)
    flat = flat.replace("$", " ")
    return NOISE.sub("", flat)


def _similar_items(text: str, qtype: str | None, exclude_id: str | None,
                   threshold: float = 0.55, top: int = 10) -> list[dict]:
    target = _normalize_text(text)
    if not target:
        return []
    sql = ("SELECT q.id, q.short_name, v.stem, v.qtype FROM question q "
           "JOIN question_version v ON v.id = q.current_version_id "
           "WHERE q.merged_into IS NULL")
    params: list = []
    if qtype:
        sql += " AND v.qtype = ?"
        params.append(qtype)
    if exclude_id:
        sql += " AND q.id <> ?"
        params.append(exclude_id)
    rows = db.all_(sql, params)

    scored: list[tuple[float, dict]] = []
    for row in rows:
        candidate = _normalize_text(row["stem"])
        if not candidate:
            continue
        ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
        if ratio >= threshold:
            scored.append((ratio, row))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return [{
        "id": row["id"], "short_name": row["short_name"],
        "stem_preview": _preview(row["stem"]), "ratio": round(ratio, 4),
    } for ratio, row in scored[:top]]


@get("/api/questions/{qid}/similar")
def question_similar(req):
    question = _question_or_404(req.params["qid"])
    version = _current_version_of(question)
    items = _similar_items(version["stem"], version["qtype"], question["id"])
    return {"items": items, "total": len(items)}


@get("/api/search/similar")
def search_similar(req):
    text = req.arg("text")
    if not _text(text):
        raise ApiError(400, "missing_field", "缺少查询参数：text")
    qtype = req.arg("qtype")
    if qtype and qtype not in QTYPES:
        raise ApiError(400, "bad_query", f"qtype 取值不合法：{qtype}")
    items = _similar_items(text, qtype, req.arg("exclude_id"))
    return {"items": items, "total": len(items)}


@get('/api/search/catalog')
def search_catalog_get(req):
    return search_catalog.catalog()


@get("/api/questions")
def question_search(req):
    keyword = _text(req.arg("keyword"))
    qtypes = [q for q in _split_csv(req.arg("qtype")) if q in QTYPES]
    source_id = _text(req.arg('source_id'))
    source_category = _text(req.arg('source_category'))
    source_year = _text(req.arg('source_year'))
    source_region = _text(req.arg('source_region'))
    source_grade = _text(req.arg('source_grade'))
    if source_category and source_category not in search_catalog.CATEGORIES:
        raise ApiError(400, 'bad_query', '来源类别无效')
    if source_grade and source_grade not in (*search_catalog.GRADES,'_unknown'):
        raise ApiError(400,'bad_query','年级无效')
    tag_ids = _split_csv(req.arg("tags"))
    tag_mode = (req.arg("tag_mode") or "all").lower()
    if tag_mode not in ("all", "any", "exclude"):
        raise ApiError(400, "bad_query", "tag_mode 只能是 all、any、exclude")
    dimension = req.arg("dimension")
    if dimension and dimension not in TAG_DIMENSIONS:
        raise ApiError(400, "bad_query", f"dimension 取值不合法：{dimension}")
    method_tags = _split_csv(req.arg("method_tag"))
    think_min = req.arg("think_min", None, int)
    think_max = req.arg("think_max", None, int)
    calc_max = req.arg("calc_max", None, int)
    scope_max = req.arg("scope_max", None, int)
    think_unrated = req.arg('think_unrated') == '1'
    for value in (think_min, think_max, calc_max, scope_max):
        if value is not None and not 1 <= value <= 5:
            raise ApiError(400, 'bad_query', '难度取值应为 1 至 5')
    if think_min is not None and think_max is not None and think_min > think_max:
        raise ApiError(400, 'bad_query', '思维难度下限不能大于上限')
    if think_unrated and (think_min is not None or think_max is not None):
        raise ApiError(400, 'bad_query', '未标注思维难度不能同时指定思维难度区间')
    review = (req.arg("review") or "any").lower()
    if review not in ("pass", "pending", "any"):
        raise ApiError(400, "bad_query", "review 只能是 pass、pending、any")
    status = (req.arg("status") or "active").lower()
    if status not in ("active", "inactive", "all"):
        raise ApiError(400, "bad_query", "status 只能是 active、inactive、all")
    audience = req.arg("exclude_audience")
    exclude_days = req.arg("exclude_days", 30, int)
    if exclude_days < 0:
        raise ApiError(400, "bad_query", "exclude_days 不能为负")
    sort = (req.arg("sort") or "recent").lower()
    if sort not in ("recent", "difficulty", "used"):
        raise ApiError(400, "bad_query", "sort 只能是 recent、difficulty、used")
    limit = req.arg("limit", 30, int)
    if limit <= 0:
        raise ApiError(400, "bad_query", "limit 必须为正整数")
    limit = min(limit, 1000)
    offset = req.arg("offset", 0, int)
    if offset < 0:
        raise ApiError(400, "bad_query", "offset 不能为负")

    sql = [
        "SELECT q.*, v.id AS version_id, v.version_no, v.qtype, v.stem, v.answer, "
        "NULL AS used_on "
        "FROM question q JOIN question_version v ON v.id = q.current_version_id "
        "WHERE q.merged_into IS NULL"
    ]
    params: list = []

    if status == "active":
        sql.append(" AND q.status = 'active'")
    elif status == "inactive":
        sql.append(" AND q.status = 'inactive'")

    if keyword:
        like = f"%{keyword}%"
        sql.append(" AND (v.stem LIKE ? OR v.answer LIKE ? OR q.short_name LIKE ? "
                   "OR EXISTS (SELECT 1 FROM occurrence o "
                   "           LEFT JOIN source s ON s.id=o.source_id "
                   "           WHERE o.question_id = q.id AND (o.raw_text LIKE ? OR s.name LIKE ? OR s.school LIKE ?)))")
        params += [like, like, like, like, like, like]
    if source_id or source_category or source_year or source_region or source_grade:
        # 全部来源条件必须由同一个出处满足，避免多出处题目交叉命中。
        source_ids = [s['id'] for s in search_catalog.sources()
                      if search_catalog.matches(s, source_category, source_year, source_region, source_id,source_grade)]
        if source_ids:
            sql.append(f' AND EXISTS (SELECT 1 FROM occurrence o WHERE o.question_id=q.id AND o.source_id IN ({_ph(len(source_ids))}))')
            params += source_ids
        else:
            sql.append(' AND 0')

    if qtypes:
        sql.append(f" AND v.qtype IN ({_ph(len(qtypes))})")
        params += qtypes

    if tag_ids:
        dim_sql = " AND t.dimension = ?" if dimension else ""
        dim_params = [dimension] if dimension else []
        # 标签选择器同时展示知识与方法；方法可能挂在当前版本的解法上。
        # 两个位置使用同一匹配语义，历史版本的解法不参与当前检索。
        groups = [[tid] for tid in tag_ids] if tag_mode == 'all' else [tag_ids]
        for group in groups:
            test = f't.id IN ({_ph(len(group))})' + dim_sql
            predicate = (
                '(EXISTS (SELECT 1 FROM question_tag qt JOIN tag t ON t.id=qt.tag_id '
                f'WHERE qt.question_id=q.id AND {test}) '
                'OR EXISTS (SELECT 1 FROM solution s JOIN solution_tag st ON st.solution_id=s.id '
                'JOIN tag t ON t.id=st.tag_id '
                f'WHERE s.question_version_id=v.id AND {test}))'
            )
            sql.append(' AND ' + ('NOT ' if tag_mode == 'exclude' else '') + predicate)
            params += (group + dim_params) * 2

    if method_tags:
        sql.append(" AND EXISTS (SELECT 1 FROM solution s "
                   "JOIN solution_tag st ON st.solution_id = s.id "
                   "WHERE s.question_version_id = v.id "
                   f"AND st.tag_id IN ({_ph(len(method_tags))}))")
        params += method_tags

    if think_unrated:
        sql.append(' AND q.difficulty_think IS NULL')
    if think_min is not None:
        sql.append(" AND q.difficulty_think >= ?")
        params.append(think_min)
    if think_max is not None:
        sql.append(" AND q.difficulty_think <= ?")
        params.append(think_max)
    if calc_max is not None:
        sql.append(" AND q.difficulty_calc <= ?")
        params.append(calc_max)
    if scope_max is not None:
        sql.append(" AND q.difficulty_scope <= ?")
        params.append(scope_max)

    if req.arg('correction') == 'pending':
        sql.append(" AND EXISTS (SELECT 1 FROM question_correction c WHERE c.question_id=q.id AND c.status='pending')")

    if sort == "difficulty":
        sql.append(" ORDER BY q.difficulty_think DESC, q.difficulty_calc DESC, q.id DESC")
    elif sort == "used":
        sql.append(" ORDER BY used_on IS NULL, used_on DESC, q.id DESC")
    else:
        sql.append(" ORDER BY q.updated_at DESC, q.id DESC")

    rows = db.all_("".join(sql), params)

    from app.history import question_history
    history = question_history()
    latest_use = {}
    for use in history:
        latest_use.setdefault(use['question_id'], use['used_on'])
    for row in rows:
        row['used_on'] = latest_use.get(row['id'])
    if audience:
        cutoff = (datetime.now(db.TZ).date() - timedelta(days=exclude_days)).isoformat()
        excluded = {use['question_id'] for use in history
                    if use['audience_id'] == audience and use['used_on'] >= cutoff}
        rows = [row for row in rows if row['id'] not in excluded]
    if sort == 'used':
        rows.sort(key=lambda row: (row['used_on'] or '', row['id']), reverse=True)

    status_map = _review_status_bulk([row["version_id"] for row in rows])
    for row in rows:
        row["_review"] = status_map.get(row["version_id"],
                                        {"stem": "pending", "answer": "pending",
                                         "solution": "pending", "figure": "na"})
        row["_state"] = _aggregate_review(row["_review"])

    if review == "pass":
        rows = [r for r in rows if r["_state"] == "pass"]
    elif review == "pending":
        rows = [r for r in rows if r["_state"] != "pass"]

    total = len(rows)
    page = rows[offset:offset + limit]
    if not page:
        return {"items": [], "total": total}

    qids = [r["id"] for r in page]
    corrections_map = {r['question_id']: r for r in db.all_(
        "SELECT question_id, COUNT(*) AS total, SUM(status='pending') AS pending "
        f"FROM question_correction WHERE question_id IN ({_ph(len(qids))}) GROUP BY question_id", qids)}
    sources_map = provenance.for_questions(qids)
    vids = [r["version_id"] for r in page]

    tag_rows = db.all_(
        "SELECT qt.question_id, t.id, t.name, t.dimension, qt.role "
        "FROM question_tag qt JOIN tag t ON t.id = qt.tag_id "
        f"WHERE qt.question_id IN ({_ph(len(qids))}) "
        "ORDER BY t.dimension, t.id", qids)
    tags_map: dict[str, list[dict]] = {}
    for row in tag_rows:
        tags_map.setdefault(row["question_id"], []).append(
            {"id": row["id"], "name": row["name"], "dimension": row["dimension"],
             "role": row["role"]})

    method_rows = db.all_(
        "SELECT s.question_version_id, s.label, t.id, t.name "
        "FROM solution s JOIN solution_tag st ON st.solution_id = s.id "
        "JOIN tag t ON t.id = st.tag_id "
        f"WHERE s.question_version_id IN ({_ph(len(vids))}) "
        "ORDER BY s.order_no, t.id", vids)
    methods_map: dict[str, list[dict]] = {}
    for row in method_rows:
        bucket = methods_map.setdefault(row["question_version_id"], [])
        entry = {"id": row["id"], "name": row["name"], "label": row["label"]}
        if entry not in bucket:
            bucket.append(entry)

    items = []
    for row in page:
        items.append({
            "id": row["id"],
            "short_name": row["short_name"],
            "qtype": row["qtype"],
            "version_no": row["version_no"],
            "version_id": row["version_id"],
            "correction_count": corrections_map.get(row['id'], {}).get('total', 0),
            "correction_pending": corrections_map.get(row['id'], {}).get('pending', 0),
            "sources": sources_map.get(row['id'],[]),
            "source_label": provenance.summary(sources_map.get(row['id'],[]),row['version_id']),
            "stem": row["stem"],  # 前端渲染公式后按行折叠，避免字符截断破坏公式。
            "stem_preview": _preview(row["stem"]),
            "tags": tags_map.get(row["id"], []),
            "methods": methods_map.get(row["version_id"], []),
            "difficulty": {"calc": row["difficulty_calc"],
                           "think": row["difficulty_think"],
                           "scope": row["difficulty_scope"]},
            "rec_level": row["rec_level"],
            "review_state": row["_state"],
            "status": row["status"],
            "used_on": row["used_on"],
        })
    return {"items": items, "total": total}
