"""教学域接口：题单（§9）、导出与发布（§10）、使用与反馈（§11）。

依赖说明（重要）：
    POST /api/collections/{cid}/build  → app.latex.build.build_release(collection_id, options)
    POST /api/releases/{rid}/rebuild   → app.latex.build.rebuild_release(release_id)

上述排版模块由另一位同事并行开发，本文件不把它 import 到模块顶层，
而是在函数体内 import。模块尚未落地时，这两个接口返回 503（code=latex_missing）
并给出中文提示，其余接口完全不受影响。

约定：
    - 编号、时间、审计一律走 app.db：next_id / now / today / audit。
    - 多表写入用 db.transaction()，内部不再嵌套事务。
    - 题单条目显式保存 question_version_id，保证讲义引用的是明确版本。
"""
from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timedelta

from app import db
from app.server import ApiError, delete, get, patch, post

# ---------------------------------------------------------------- 常量

REVIEW_ITEMS = ("stem", "answer", "solution", "figure",
                "classification", "boundary", "method_scope")

ROLES = ("diagnostic", "example", "variant", "practice",
         "homework", "summary", "challenge")
ROLE_LABEL = {
    "diagnostic": "诊断", "example": "例题", "variant": "变式", "practice": "练习",
    "homework": "作业", "summary": "小结", "challenge": "挑战",
}
HINT_LEVELS = ("none", "keyword", "method", "full")
SECTION_KINDS = ("question", "concept", "summary", "note")
PURPOSES = ("handout", "quiz", "homework", "diagnostic")
COL_STATUS = ("draft", "ready", "archived")
AUDIENCE_KINDS = ("class", "group", "student")
ASSIGN_STATUS = ("planned", "distributed", "closed", "cancelled")
USAGE_PURPOSES = ("class", "homework", "quiz", "review", "other")
RELEASE_STATUS = ("built", "failed", "archived")

DIM_LABEL = {
    "knowledge": "知识点", "textbook": "教材位置", "prereq": "前置知识",
    "method": "解题方法", "idea": "数学思想", "purpose": "教学用途", "error": "错误类型",
}

DUP_THRESHOLD = 0.85

# build 接口的 options 默认值；title 由题单标题填充
BUILD_DEFAULTS = {
    "title": "",
    "subtitle": "",
    "pretitle": "",
    "motto": "",
    "theme": "blue",
    "versions": ["student", "teacher"],
    "template": "exbook",
    "format": "standard",
    "toc": False,
}

# ---------------------------------------------------------------- 通用小工具


def _enum(value, allowed, field: str):
    """校验枚举取值，报错用中文并列出可选值。"""
    if value is None:
        return None
    if value not in allowed:
        raise ApiError(400, "bad_field",
                       f"{field} 取值不合法：{value}。只能是：{'、'.join(allowed)}")
    return value


def _number(value, field: str):
    """数值字段，允许 null。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ApiError(400, "bad_field", f"{field} 必须是数字，收到：{value}")


def _flag(value, field: str):
    """0/1 开关字段，允许 null。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "bad_field", f"{field} 只能是 0 或 1")
    if num not in (0, 1):
        raise ApiError(400, "bad_field", f"{field} 只能是 0 或 1")
    return num


def _text(value) -> str:
    return "" if value is None else str(value)


def _loads(value, default=None):
    """容错解析 JSON 列（库里存的是文本）。"""
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _preview(text, limit: int = 80) -> str:
    from app.text_preview import preview
    return preview(text,limit)


def _num_out(value):
    """输出用数字：整数不带小数点。"""
    if value is None:
        return 0
    num = float(value)
    if abs(num - round(num)) < 1e-9:
        return int(round(num))
    return round(num, 2)


def _fmt_min(value) -> str:
    return str(_num_out(value))


def _cutoff(days):
    """days<=0 视为不限时间；返回 ISO 日期字符串或 None。"""
    if not days or days <= 0:
        return None
    return (datetime.now(db.TZ).date() - timedelta(days=days)).isoformat()


def _check_date(value, field: str):
    """校验 YYYY-MM-DD。"""
    if value is None or value == "":
        return None
    try:
        datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError:
        raise ApiError(400, "bad_field", f"{field} 必须是 2026-10-27 这样的日期")
    return str(value)


_LATEX_CMD = re.compile(r"\\[a-zA-Z]+\*?")
_LATEX_PUNCT = re.compile(r"[\\${}^_&%~#。，、；：？！,.;:?!()（）\[\]【】<>《》\"'“”‘’]")


def _norm_stem(text) -> str:
    """题面规范化：去 LaTeX 命令、去符号空白，用于重复检测。"""
    plain = _LATEX_CMD.sub(" ", _text(text))
    plain = _LATEX_PUNCT.sub("", plain)
    return re.sub(r"\s+", "", plain)


def _dup_pairs(items: list[dict]) -> list[tuple[int, int, float]]:
    """两两比较规范化题面，返回 (i, j, ratio)，i<j。同一道题直接按 1.0 计。"""
    pairs: list[tuple[int, int, float]] = []
    total = len(items)
    for i in range(total):
        for j in range(i + 1, total):
            if items[i]["question_id"] == items[j]["question_id"]:
                pairs.append((i, j, 1.0))
                continue
            a = items[i]["_stem_norm"]
            b = items[j]["_stem_norm"]
            if not a or not b:
                continue
            ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
            if ratio >= DUP_THRESHOLD:
                pairs.append((i, j, ratio))
    return pairs


# ---------------------------------------------------------------- 审校可用性


def _default_review_status() -> dict:
    return {key: "pending" for key in REVIEW_ITEMS}


def _review_status_map(version_ids: list[str]) -> dict[str, dict]:
    """按「对象 + 项目」取最新一条审校结论，无记录视为 pending。"""
    status = {vid: _default_review_status() for vid in version_ids}
    if not version_ids:
        return status
    holder = ",".join("?" * len(version_ids))
    rows = db.all_(
        "SELECT target_id, item, result FROM review "
        f"WHERE target_type = 'question_version' AND target_id IN ({holder}) "
        "ORDER BY created_at, id",
        version_ids,
    )
    for row in rows:
        if row["item"] in REVIEW_ITEMS and row["target_id"] in status:
            status[row["target_id"]][row["item"]] = row["result"]
    return status


def _solution_map(version_ids: list[str]) -> dict[str, dict]:
    """每个版本下解法的数量及是否有已通过审校的解法。"""
    info = {vid: {"count": 0, "any_pass": False, "by_id": {}} for vid in version_ids}
    if not version_ids:
        return info
    holder = ",".join("?" * len(version_ids))
    sols = db.all_(
        f"SELECT id, question_version_id FROM solution WHERE question_version_id IN ({holder})",
        version_ids,
    )
    if not sols:
        return info
    for sol in sols:
        info[sol["question_version_id"]]["count"] += 1
    ids = [sol["id"] for sol in sols]
    holder2 = ",".join("?" * len(ids))
    latest: dict[str, str] = {}
    rows = db.all_(
        "SELECT target_id, result FROM review "
        "WHERE target_type = 'solution' AND item = 'solution' "
        f"AND target_id IN ({holder2}) ORDER BY created_at, id",
        ids,
    )
    for row in rows:
        latest[row["target_id"]] = row["result"]
    for sol in sols:
        info[sol["question_version_id"]]["by_id"][sol['id']] = latest.get(sol['id'], 'pending')
        if latest.get(sol["id"]) == "pass":
            info[sol["question_version_id"]]["any_pass"] = True
    return info


def _judge_usable(version: dict, status: dict,
                  solution: dict) -> tuple[bool, list[str], list[str]]:
    """可用性：题干、答案通过；解析（解法或整题解析）通过；图形不得为 fail。

    返回 (是否可用, 缺失项完整说明, 缺失项简称)。
    """
    missing: list[str] = []
    short: list[str] = []
    if status.get("stem") != "pass":
        missing.append("题干未通过审校")
        short.append("题干")
    if status.get("answer") != "pass":
        missing.append("答案未通过审校")
        short.append("答案")
    if status.get("figure") == "fail":
        missing.append("图形未通过审校")
        short.append("图形")
    if solution["count"]:
        states = solution.get('by_id') or {}
        chosen = version.get('solution_id')
        selected_pass = states.get(chosen) == 'pass' if chosen else bool(states) and all(s == 'pass' for s in states.values())
        if not selected_pass:
            missing.append("解析未通过审校")
            short.append("解析")
    else:
        analysis = _text(version.get("analysis")).strip()
        if not analysis:
            missing.append("缺少解析")
            short.append("解析缺失")
        elif status.get("solution") != "pass":
            missing.append("解析未通过审校")
            short.append("解析")
    return (not missing), missing, short


def _summarize_state(usable: bool, status: dict) -> str:
    if usable:
        return "pass"
    if any(status.get(item) == "fail" for item in ("stem", "answer", "solution")):
        return "fail"
    return "pending"


# ---------------------------------------------------------------- 题单基础


def _collection(cid: str) -> dict:
    row = db.one("SELECT * FROM collection WHERE id = ?", (cid,))
    if row is None:
        raise ApiError(404, "not_found", f"题单 {cid} 不存在")
    return row


def _item_row(iid: str) -> dict:
    row = db.one("SELECT * FROM collection_item WHERE id = ?", (iid,))
    if row is None:
        raise ApiError(404, "not_found", f"题单条目 {iid} 不存在")
    return row


def _section_row(sid: str) -> dict:
    row = db.one("SELECT * FROM collection_section WHERE id = ?", (sid,))
    if row is None:
        raise ApiError(404, "not_found", f"分节 {sid} 不存在")
    return row


def _section_in_collection(sid: str, cid: str) -> dict:
    section = _section_row(sid)
    if section["collection_id"] != cid:
        raise ApiError(400, "section_mismatch", f"分节 {sid} 不属于题单 {cid}")
    return section


def _touch_collection(conn, cid: str) -> None:
    conn.execute("UPDATE collection SET updated_at = ? WHERE id = ?", (db.now(), cid))


def _public(item: dict) -> dict:
    """去掉内部下划线字段（下划线字段仅用于组题检查）。"""
    return {key: value for key, value in item.items() if not key.startswith("_")}


# ---------------------------------------------------------------- 条目装配


def _load_items(cid: str) -> list[dict]:
    """装配题单条目：条目设置 + 题目预览 + 审校状态 + 逐条警告。"""
    rows = db.all_(
        "SELECT i.*, q.short_name, q.status AS question_status, q.current_version_id, "
        "q.merged_into, q.difficulty_calc, q.difficulty_think, q.difficulty_scope, "
        "q.time_solve_min AS q_time_solve, q.time_explain_min AS q_time_explain, "
        "v.version_no, v.qtype, v.stem, v.analysis, v.subquestions "
        "FROM collection_item i "
        "JOIN question q ON q.id = i.question_id "
        "JOIN question_version v ON v.id = i.question_version_id "
        "WHERE i.collection_id = ? ORDER BY i.order_no, i.id",
        (cid,),
    )
    if not rows:
        return []

    version_ids = sorted({row["question_version_id"] for row in rows})
    status_map = _review_status_map(version_ids)
    solution_map = _solution_map(version_ids)
    from app import provenance
    source_map=provenance.for_questions([row['question_id'] for row in rows])

    items: list[dict] = []
    for index, row in enumerate(rows):
        status = status_map.get(row["question_version_id"], _default_review_status())
        solution = solution_map.get(row["question_version_id"], {"count": 0, "any_pass": False})
        usable, missing, short = _judge_usable(row, status, solution)
        item = dict(row)
        # 条目响应只给预览，完整题干、解析与重复的题目字段不随条目下发
        for noisy in ("stem", "analysis", "subquestions", "short_name", "question_status",
                      "merged_into", "difficulty_calc", "difficulty_think",
                      "difficulty_scope", "qtype", "version_no"):
            item.pop(noisy, None)
        subq_keys = [str(sub.get("key")) for sub in _subquestions(row) if sub.get("key") is not None]
        item["subquestion_keys"] = subq_keys
        item["question"] = {
            "id": row["question_id"],
            "short_name": row["short_name"],
            "qtype": row["qtype"],
            "stem_preview": _preview(row["stem"]),
            "source_label": provenance.summary(source_map.get(row['question_id'],[]),row['question_version_id']),
            "version_no": row["version_no"],
            "difficulty": {
                "calc": row["difficulty_calc"],
                "think": row["difficulty_think"],
                "scope": row["difficulty_scope"],
            },
            "status": row["question_status"],
            "current_version_id": row["current_version_id"],
        }
        item["review_state"] = _summarize_state(usable, status)
        item["review_status"] = status
        item["usable"] = usable
        item["review_missing"] = missing
        item["_review_short"] = short
        item["solution_count"] = solution["count"]
        item["warnings"] = []
        item["_stem_norm"] = _norm_stem(row["stem"])
        item["_index"] = index
        item["_q_status"] = row["question_status"]
        item["_q_merged"] = row["merged_into"]
        item["_q_time_solve"] = row["q_time_solve"]
        item["_q_time_explain"] = row["q_time_explain"]
        item["_q_think"] = row["difficulty_think"]
        items.append(item)

    # 逐条警告：未过审、疑似重复、历史版本、停用、已合并
    for i, j, ratio in _dup_pairs(items):
        a = items[i]
        b = items[j]
        if a["question_id"] == b["question_id"]:
            a["warnings"].append(f"与第 {j + 1} 题是同一道题的重复引用")
            b["warnings"].append(f"与第 {i + 1} 题是同一道题的重复引用")
        else:
            a["warnings"].append(f"与第 {j + 1} 题疑似重复（题面相似度 {ratio:.2f}）")
            b["warnings"].append(f"与第 {i + 1} 题疑似重复（题面相似度 {ratio:.2f}）")

    for item in items:
        head: list[str] = []
        if not item["usable"]:
            head.append("尚未通过审校：" + "、".join(item["_review_short"]))
        if item["question_version_id"] != item["current_version_id"]:
            head.append(f"本条使用历史版本 {item['question_version_id']}")
        if item["_q_status"] != "active":
            head.append("题目已停用")
        if item["_q_merged"]:
            head.append(f"题目已合并至 {item['_q_merged']}")
        item["warnings"] = head + item["warnings"]
    return items


def _item_by_id(cid: str, iid: str) -> dict:
    for item in _load_items(cid):
        if item["id"] == iid:
            return item
    raise ApiError(404, "not_found", f"题单条目 {iid} 不属于题单 {cid}")


def _effective_time(item: dict) -> tuple[float, float]:
    """时间取条目设置，缺省回落到题目自身的预计用时。"""
    solve = item["time_solve_min"]
    if solve is None:
        solve = item["_q_time_solve"]
    explain = item["time_explain_min"]
    if explain is None:
        explain = item["_q_time_explain"]
    return float(solve or 0), float(explain or 0)


# ---------------------------------------------------------------- 组题检查


def _coverage_requirements(raw) -> list[tuple[str | None, str]]:
    """解析 collection.coverage，兼容多种写法。

    支持：
        {"knowledge": ["TAG000003", "单调性"], "method": [...]}
        ["TAG000003", "单调性"]
        [{"dimension": "knowledge", "tag_id": "TAG000003"}]
    """
    data = _loads(raw, None)
    if not data:
        return []
    out: list[tuple[str | None, str]] = []
    if isinstance(data, dict):
        for dimension, values in data.items():
            if isinstance(values, str):
                values = [values]
            for value in values or []:
                if isinstance(value, dict):
                    key = value.get("tag_id") or value.get("id") or value.get("name")
                    if key:
                        out.append((value.get("dimension") or dimension, str(key)))
                elif value:
                    out.append((dimension, str(value)))
    elif isinstance(data, list):
        for value in data:
            if isinstance(value, dict):
                key = value.get("tag_id") or value.get("id") or value.get("name")
                if key:
                    out.append((value.get("dimension"), str(key)))
            elif value:
                out.append((None, str(value)))
    return out


def _build_check(collection: dict, items: list[dict]) -> dict:
    budget = collection.get("duration_min")
    total_solve = 0.0
    total_explain = 0.0
    spread = {str(level): 0 for level in range(1, 6)}
    for item in items:
        solve, explain = _effective_time(item)
        total_solve += solve
        total_explain += explain
        think = item["_q_think"]
        if think in (1, 2, 3, 4, 5):
            spread[str(think)] += 1

    duplicates = []
    for i, j, ratio in _dup_pairs(items):
        if items[i]["question_id"] == items[j]["question_id"]:
            reason = "同一道题重复出现"
        else:
            reason = f"题面相似度 {ratio:.2f}"
        duplicates.append({"a": items[i]["id"], "b": items[j]["id"], "reason": reason})

    unreviewed = [item["id"] for item in items if item["review_state"] != "pass"]

    coverage = {"knowledge": [], "method": []}
    question_ids = sorted({item["question_id"] for item in items})
    if question_ids:
        holder = ",".join("?" * len(question_ids))
        rows = db.all_(
            "SELECT t.id, t.name, t.dimension, COUNT(DISTINCT qt.question_id) AS cnt "
            "FROM question_tag qt JOIN tag t ON t.id = qt.tag_id "
            f"WHERE qt.question_id IN ({holder}) AND t.dimension = 'knowledge' "
            "GROUP BY t.id ORDER BY cnt DESC, t.name",
            question_ids,
        )
        for row in rows:
            coverage[row["dimension"]].append(
                {"id": row["id"], "name": row["name"], "count": row["cnt"]})

    # 方法属于具体解法；题单没有选用的解法不能充当已覆盖的方法。
    method_rows = db.all_(
        "SELECT t.id,t.name,COUNT(DISTINCT ci.question_id) AS cnt "
        "FROM collection_item ci JOIN solution s ON s.question_version_id=ci.question_version_id "
        "JOIN solution_tag st ON st.solution_id=s.id JOIN tag t ON t.id=st.tag_id "
        "WHERE ci.collection_id=? AND t.dimension='method' "
        "AND (ci.solution_id IS NULL OR ci.solution_id=s.id) GROUP BY t.id ORDER BY cnt DESC,t.name",
        (collection['id'],))
    coverage['method'] = [{'id': row['id'], 'name': row['name'], 'count': row['cnt']}
                          for row in method_rows]

    missing: list[str] = []
    if not items:
        missing.append("题单还没有任何题目，请先加入条目")

    if not budget:
        missing.append("题单未设课时（duration_min），无法核对时间预算")
    else:
        loaded = total_solve + total_explain
        if loaded > budget * 0.9:
            missing.append(
                f"课时预算不足：作答 {_fmt_min(total_solve)} 分钟 + 讲解 "
                f"{_fmt_min(total_explain)} 分钟 = {_fmt_min(loaded)} 分钟，已超过预算 "
                f"{_fmt_min(budget)} 分钟的 90%（{_fmt_min(budget * 0.9)} 分钟）。"
                "课时预算含过渡与预留时间，并非只算做题时间，建议精简题目或延长课时。"
            )

    if unreviewed:
        numbers = "、".join(str(item["_index"] + 1) for item in items
                            if item["review_state"] != "pass")
        missing.append(f"有 {len(unreviewed)} 条尚未通过审校：第 {numbers} 条")

    # 覆盖要求：题目里已命中的标签（按 id）
    hit_ids = set()
    if question_ids:
        holder = ",".join("?" * len(question_ids))
        rows = db.all_(
            "SELECT DISTINCT qt.tag_id FROM question_tag qt JOIN tag t ON t.id=qt.tag_id "
            f"WHERE qt.question_id IN ({holder}) AND t.dimension<>'method'",
            question_ids,
        )
        hit_ids = {row["tag_id"] for row in rows}
    hit_ids.update(row['id'] for row in method_rows)

    for dimension, key in _coverage_requirements(collection.get("coverage")):
        tag = None
        if re.fullmatch(r"TAG\d+", key):
            tag = db.one("SELECT id, name, dimension FROM tag WHERE id = ?", (key,))
        else:
            if dimension:
                tag = db.one("SELECT id, name, dimension FROM tag WHERE name = ? AND dimension = ?",
                             (key, dimension))
            else:
                tag = db.one("SELECT id, name, dimension FROM tag WHERE name = ?", (key,))
        if tag is None:
            label = DIM_LABEL.get(dimension, "标签") if dimension else "标签"
            missing.append(f"覆盖要求无法核对：{label}「{key}」在标签库里找不到")
            continue
        if tag["id"] not in hit_ids:
            label = DIM_LABEL.get(tag["dimension"], "标签")
            missing.append(
                f"覆盖要求未满足：{label}「{tag['name']}」没有任何题命中，"
                "请补入对应题目（不放宽覆盖要求）"
            )

    return {
        "count": len(items),
        "time_solve": _num_out(total_solve),
        "time_explain": _num_out(total_explain),
        "budget": _num_out(budget) if budget else None,
        "difficulty_spread": spread,
        "duplicates": duplicates,
        "unreviewed": unreviewed,
        "coverage": coverage,
        "missing": missing,
    }


# ---------------------------------------------------------------- §9 题单列表与增改


@get("/api/collections")
def collection_list(req):
    keyword = req.arg("keyword")
    status = req.arg("status")
    purpose = req.arg("purpose")
    sql = "SELECT * FROM collection WHERE 1 = 1"
    params: list = []
    if keyword:
        sql += " AND (title LIKE ? OR topic LIKE ?)"
        like = f"%{keyword}%"
        params += [like, like]
    if status:
        sql += " AND status = ?"
        params.append(status)
    if purpose:
        sql += " AND purpose = ?"
        params.append(purpose)
    sql += " ORDER BY updated_at DESC, created_at DESC"
    collections = db.all_(sql, params)

    totals: dict[str, dict] = {}
    if collections:
        rows = db.all_(
            "SELECT i.collection_id, i.time_solve_min, i.time_explain_min, "
            "q.time_solve_min AS q_time_solve, q.time_explain_min AS q_time_explain "
            "FROM collection_item i JOIN question q ON q.id = i.question_id"
        )
        for row in rows:
            bucket = totals.setdefault(row["collection_id"],
                                       {"count": 0, "solve": 0.0, "explain": 0.0})
            bucket["count"] += 1
            solve = row["time_solve_min"] if row["time_solve_min"] is not None else row["q_time_solve"]
            explain = (row["time_explain_min"] if row["time_explain_min"] is not None
                       else row["q_time_explain"])
            bucket["solve"] += float(solve or 0)
            bucket["explain"] += float(explain or 0)

    ids = [c["id"] for c in collections]
    release_counts: dict[str, int] = {}
    section_counts: dict[str, int] = {}
    if ids:
        holder = ",".join("?" * len(ids))
        for row in db.all_(
                f"SELECT collection_id, COUNT(*) AS cnt FROM release WHERE collection_id IN ({holder}) "
                "GROUP BY collection_id", ids):
            release_counts[row["collection_id"]] = row["cnt"]
        for row in db.all_(
                f"SELECT collection_id, COUNT(*) AS cnt FROM collection_section "
                f"WHERE collection_id IN ({holder}) GROUP BY collection_id", ids):
            section_counts[row["collection_id"]] = row["cnt"]

    items = []
    for collection in collections:
        bucket = totals.get(collection["id"], {"count": 0, "solve": 0.0, "explain": 0.0})
        collection = dict(collection)
        collection["item_count"] = bucket["count"]
        collection["time_solve"] = _num_out(bucket["solve"])
        collection["time_explain"] = _num_out(bucket["explain"])
        collection["section_count"] = section_counts.get(collection["id"], 0)
        collection["release_count"] = release_counts.get(collection["id"], 0)
        items.append(collection)
    return {"items": items, "total": len(items)}


CREATE_FIELDS = ("title", "topic", "level", "prereqs", "objectives", "duration_min",
                 "purpose", "coverage", "template", "status", "note")


def _collection_payload(body: dict, partial: bool) -> dict:
    data: dict = {}
    if "title" in body or not partial:
        title = _text(body.get("title")).strip()
        if not title:
            raise ApiError(400, "missing_field", "缺少必填字段：title（题单标题）")
        data["title"] = title
    for field in ("topic", "level", "prereqs", "objectives", "note", "template"):
        if field in body:
            data[field] = None if body[field] is None else _text(body[field])
    if "duration_min" in body:
        minutes = _number(body["duration_min"], "duration_min")
        if minutes is not None and minutes <= 0:
            raise ApiError(400, "bad_field", "duration_min 必须大于 0")
        data["duration_min"] = None if minutes is None else int(minutes)
    if "purpose" in body:
        data["purpose"] = _enum(body["purpose"], PURPOSES, "purpose")
    if "status" in body:
        data["status"] = _enum(body["status"], COL_STATUS, "status")
    if "coverage" in body:
        data["coverage"] = db.dumps(body["coverage"])
    return data


@post("/api/collections")
def collection_create(req):
    body = req.json or {}
    data = _collection_payload(body, partial=False)
    data.setdefault("purpose", "handout")
    data.setdefault("template", "exbook")
    data.setdefault("status", "draft")
    with db.transaction() as conn:
        cid = db.next_id(conn, "COL")
        stamp = db.now()
        columns = ["id", "title", "created_at", "updated_at"] + list(data.keys() - {"id", "title"})
        columns = list(dict.fromkeys(columns))
        values = []
        for column in columns:
            if column == "id":
                values.append(cid)
            elif column == "title":
                values.append(data["title"])
            elif column in ("created_at", "updated_at"):
                values.append(stamp)
            else:
                values.append(data.get(column))
        conn.execute(
            f"INSERT INTO collection({', '.join(columns)}) VALUES({', '.join('?' * len(columns))})",
            values,
        )
        db.audit("collection.create", "collection", cid, {"title": data["title"]})
    return {"collection": _collection(cid)}


@get("/api/collections/{cid}")
def collection_detail(req):
    return _collection_detail_payload(req.params["cid"])


def _collection_detail_payload(cid: str) -> dict:
    from app import commands

    collection = _collection(cid)
    sections = db.all_(
        "SELECT * FROM collection_section WHERE collection_id = ? ORDER BY order_no, id", (cid,))
    sections = [dict(section, item_count=_section_item_count(section["id"])) for section in sections]
    items = _load_items(cid)
    return {
        "collection": collection,
        "sections": sections,
        "items": [_public(item) for item in items],
        "check": _build_check(collection, items),
        # 写入前的并发校验基准；题目版本变化另行核对，不混进这个摘要
        "revision": commands.collection_revision(cid),
    }


def _section_item_count(sid: str) -> int:
    return db.scalar("SELECT COUNT(*) FROM collection_item WHERE section_id = ?", (sid,)) or 0


@patch("/api/collections/{cid}")
def collection_update(req):
    cid = req.params["cid"]
    _collection(cid)
    body = req.json or {}
    unknown = [key for key in body if key not in CREATE_FIELDS]
    if unknown:
        raise ApiError(400, "bad_field",
                       f"题单级字段不接受：{'、'.join(unknown)}")
    data = _collection_payload(body, partial=True)
    if not data:
        raise ApiError(400, "empty_body", "没有需要修改的字段")
    with db.transaction() as conn:
        assignments = ", ".join(f"{key} = ?" for key in data)
        conn.execute(f"UPDATE collection SET {assignments}, updated_at = ? WHERE id = ?",
                     list(data.values()) + [db.now(), cid])
        db.audit("collection.update", "collection", cid, data)
    return {"collection": _collection(cid)}


@post("/api/collections/{cid}/clone")
def collection_clone(req):
    cid = req.params["cid"]
    source = _collection(cid)
    body = req.json or {}
    title = _text(body.get("title")).strip() or f"{source['title']}（副本）"
    sections = db.all_(
        "SELECT * FROM collection_section WHERE collection_id = ? ORDER BY order_no, id", (cid,))
    items = db.all_(
        "SELECT * FROM collection_item WHERE collection_id = ? ORDER BY order_no, id", (cid,))
    with db.transaction() as conn:
        new_cid = db.next_id(conn, "COL")
        stamp = db.now()
        conn.execute(
            "INSERT INTO collection(id, title, topic, level, prereqs, objectives, duration_min, "
            "purpose, coverage, template, status, note, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_cid, title, source["topic"], source["level"], source["prereqs"],
             source["objectives"], source["duration_min"], source["purpose"],
             source["coverage"], source["template"], "draft", source["note"], stamp, stamp),
        )
        item_map = {}
        section_map: dict[str, str] = {}
        for section in sections:
            new_sid = db.next_id(conn, "SEC")
            section_map[section["id"]] = new_sid
            conn.execute(
                "INSERT INTO collection_section(id, collection_id, title, order_no, kind, intro, note) "
                "VALUES(?,?,?,?,?,?,?)",
                (new_sid, new_cid, section["title"], section["order_no"], section["kind"],
                 section["intro"], section["note"]),
            )
        for item in items:
            new_iid = db.next_id(conn, "ITM")
            item_map[item["id"]] = new_iid
            conn.execute(
                "INSERT INTO collection_item(id, collection_id, section_id, question_id, "
                "question_version_id, order_no, role, solution_id, hint_level, time_solve_min, "
                "time_explain_min, space_mm, page_break_before, required, points, subq_keys, note, "
                "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_iid, new_cid, section_map.get(item["section_id"]), item["question_id"],
                 item["question_version_id"], item["order_no"], item["role"], item["solution_id"],
                 item["hint_level"], item["time_solve_min"], item["time_explain_min"],
                 item["space_mm"], item["page_break_before"], item["required"], item["points"],
                 db.dumps(item["subq_keys"]), item["note"], stamp),
            )
        layout = source.get("layout_options") or {}
        if layout:
            cloned_layout = {"options": layout.get("options", {}), "items": {item_map[k]: v for k, v in layout.get("items", {}).items() if k in item_map}}
            conn.execute("UPDATE collection SET layout_options=? WHERE id=?", (db.dumps(cloned_layout), new_cid))
        db.audit("collection.clone", "collection", new_cid,
                 {"from": cid, "items": len(items), "sections": len(sections)})
    return _collection_detail_payload(new_cid)


# ---------------------------------------------------------------- §9 分节


@post("/api/collections/{cid}/sections")
def section_create(req):
    cid = req.params["cid"]
    _collection(cid)
    body = req.json or {}
    title = _text(body.get("title")).strip()
    if not title:
        raise ApiError(400, "missing_field", "缺少必填字段：title（分节标题）")
    kind = _enum(body.get("kind") or "question", SECTION_KINDS, "kind")
    order_no = body.get("order_no")
    if order_no is None:
        order_no = db.scalar(
            "SELECT COALESCE(MAX(order_no), -1) + 1 FROM collection_section WHERE collection_id = ?",
            (cid,)) or 0
    with db.transaction() as conn:
        sid = db.next_id(conn, "SEC")
        conn.execute(
            "INSERT INTO collection_section(id, collection_id, title, order_no, kind, intro, note) "
            "VALUES(?,?,?,?,?,?,?)",
            (sid, cid, title, int(order_no), kind,
             None if body.get("intro") is None else _text(body.get("intro")),
             None if body.get("note") is None else _text(body.get("note"))),
        )
        _touch_collection(conn, cid)
        db.audit("section.create", "collection_section", sid, {"collection_id": cid, "title": title})
    return {"section": _section_row(sid)}


SECTION_FIELDS = ("title", "order_no", "kind", "intro", "note")


@patch("/api/sections/{sid}")
def section_update(req):
    sid = req.params["sid"]
    section = _section_row(sid)
    body = req.json or {}
    unknown = [key for key in body if key not in SECTION_FIELDS]
    if unknown:
        raise ApiError(400, "bad_field", f"分节不接受这些字段：{'、'.join(unknown)}")
    data: dict = {}
    if "title" in body:
        title = _text(body["title"]).strip()
        if not title:
            raise ApiError(400, "bad_field", "分节标题不能为空")
        data["title"] = title
    if "order_no" in body:
        try:
            data["order_no"] = int(body["order_no"])
        except (TypeError, ValueError):
            raise ApiError(400, "bad_field", "order_no 必须是整数")
    if "kind" in body:
        data["kind"] = _enum(body["kind"], SECTION_KINDS, "kind")
    for field in ("intro", "note"):
        if field in body:
            data[field] = None if body[field] is None else _text(body[field])
    if not data:
        raise ApiError(400, "empty_body", "没有需要修改的字段")
    with db.transaction() as conn:
        assignments = ", ".join(f"{key} = ?" for key in data)
        conn.execute(f"UPDATE collection_section SET {assignments} WHERE id = ?",
                     list(data.values()) + [sid])
        _touch_collection(conn, section["collection_id"])
        db.audit("section.update", "collection_section", sid, data)
    return {"section": _section_row(sid)}


@delete("/api/sections/{sid}")
def section_delete(req):
    sid = req.params["sid"]
    section = _section_row(sid)
    cid = section["collection_id"]
    with db.transaction() as conn:
        moved = conn.execute(
            "UPDATE collection_item SET section_id = NULL WHERE section_id = ?", (sid,)).rowcount
        conn.execute("DELETE FROM collection_section WHERE id = ?", (sid,))
        _touch_collection(conn, cid)
        db.audit("section.delete", "collection_section", sid,
                 {"collection_id": cid, "items_moved": moved})
    return {"ok": True, "deleted": sid, "items_moved_to_unsectioned": moved}


# ---------------------------------------------------------------- §9 条目


def _subquestions(version: dict) -> list[dict]:
    data = _loads(version.get("subquestions"), [])
    return data if isinstance(data, list) else []


def _validate_subq_keys(version: dict, keys) -> list[str] | None:
    """拆用小问校验：被拆出的小问，其依赖的小问必须同时在内。"""
    if keys is None:
        return None
    if not isinstance(keys, list):
        raise ApiError(400, "bad_field", "subq_keys 必须是数组，如 [\"1\",\"2\"]")
    keys = [str(key) for key in keys]
    if not keys:
        return None
    subs = _subquestions(version)
    if not subs:
        raise ApiError(400, "no_subquestions", "该题没有小问，不能设置拆用小问（subq_keys）")
    known = {str(sub.get("key")): sub for sub in subs if sub.get("key") is not None}
    teaching = _loads(version.get('teaching'), {}) or {}
    if teaching.get('subquestion_dependency_review') == 'pending' and set(keys) != set(known):
        raise ApiError(409, 'subq_dependency_pending',
                       '这道导入题的小问依赖尚未核对，请先整题使用；核对并修订后可拆题')
    unknown = [key for key in keys if key not in known]
    if unknown:
        raise ApiError(400, "bad_subq",
                       f"题面里没有这些小问：{'、'.join(unknown)}。可选：{'、'.join(known)}")
    for key in keys:
        depends = known[key].get("depends_on") or []
        if isinstance(depends, (str, int)):
            depends = [depends]
        for dep in depends:
            if str(dep) not in keys:
                raise ApiError(
                    409, "subq_dependency",
                    f"第 ({key}) 问依赖第 ({dep}) 问的结论，需一并选取")
    return keys


ITEM_FIELDS = ("role", "solution_id", "hint_level", "time_solve_min", "time_explain_min",
               "space_mm", "page_break_before", "required", "points", "subq_keys", "note")

CONTENT_FIELDS = ("stem", "answer", "analysis", "options", "answer_keys", "subquestions",
                  "qtype", "question_id", "question_version_id")


def _item_settings(body: dict, item: dict | None, version: dict) -> dict:
    """把请求体收敛成条目设置。item 为 None 表示新建。"""
    unknown = [key for key in body if key not in ITEM_FIELDS]
    if unknown:
        content = [key for key in unknown if key in CONTENT_FIELDS]
        if content:
            raise ApiError(
                400, "readonly_field",
                f"条目只记录引用关系，不能改题目内容（{'、'.join(content)}）。"
                "请到题目里改版本内容，再换用新版本。")
        raise ApiError(400, "bad_field", f"条目不接受这些字段：{'、'.join(unknown)}")

    data: dict = {}
    if "role" in body:
        data["role"] = _enum(body["role"], ROLES, "role")
    elif item is None:
        data["role"] = "example"
    if "hint_level" in body:
        data["hint_level"] = _enum(body["hint_level"], HINT_LEVELS, "hint_level")
    elif item is None:
        data["hint_level"] = "none"
    if "solution_id" in body:
        solution_id = body["solution_id"] or None
        if solution_id:
            vid = item["question_version_id"] if item else version["id"]
            row = db.one("SELECT id, question_version_id FROM solution WHERE id = ?", (solution_id,))
            if row is None:
                raise ApiError(404, "not_found", f"解法 {solution_id} 不存在")
            if vid and row["question_version_id"] != vid:
                raise ApiError(400, "solution_mismatch", "所选解法不属于该条目引用的题目版本")
        data["solution_id"] = solution_id
    for field in ("time_solve_min", "time_explain_min", "space_mm", "points"):
        if field in body:
            data[field] = _number(body[field], field)
    if "page_break_before" in body:
        data["page_break_before"] = _flag(body["page_break_before"], "page_break_before")
    elif item is None:
        data["page_break_before"] = 0
    if "required" in body:
        data["required"] = _flag(body["required"], "required")
    elif item is None:
        data["required"] = 1
    if "subq_keys" in body:
        data["subq_keys"] = db.dumps(_validate_subq_keys(version, body["subq_keys"]))
    if "note" in body:
        data["note"] = None if body["note"] is None else _text(body["note"])
    return data


@post("/api/collections/{cid}/items")
def item_create(req):
    cid = req.params["cid"]
    _collection(cid)
    body = req.json or {}
    qid = _text(body.get("question_id")).strip()
    if not qid:
        raise ApiError(400, "missing_field", "缺少必填字段：question_id")
    question = db.one("SELECT * FROM question WHERE id = ?", (qid,))
    if question is None:
        raise ApiError(404, "not_found", f"题目 {qid} 不存在")
    if question["merged_into"]:
        raise ApiError(409, "merged",
                       f"题目 {qid} 已合并到 {question['merged_into']}，请改用它")

    version_id = body.get("question_version_id") or body.get("version_id")
    if version_id:
        version = db.one("SELECT * FROM question_version WHERE id = ?", (version_id,))
        if version is None:
            raise ApiError(404, "not_found", f"题目版本 {version_id} 不存在")
        if version["question_id"] != qid:
            raise ApiError(400, "version_mismatch", f"版本 {version_id} 不属于题目 {qid}")
    else:
        version_id = question["current_version_id"]
        if not version_id:
            latest = db.one(
                "SELECT id FROM question_version WHERE question_id = ? "
                "ORDER BY version_no DESC LIMIT 1", (qid,))
            version_id = latest["id"] if latest else None
        if not version_id:
            raise ApiError(409, "no_version", f"题目 {qid} 还没有任何版本，请先录入题面")
        version = db.one("SELECT * FROM question_version WHERE id = ?", (version_id,))

    section_id = body.get("section_id") or None
    if section_id:
        _section_in_collection(section_id, cid)

    order_no = body.get("order_no")
    if order_no is None:
        order_no = db.scalar(
            "SELECT COALESCE(MAX(order_no), -1) + 1 FROM collection_item WHERE collection_id = ?",
            (cid,)) or 0
    try:
        order_no = int(order_no)
    except (TypeError, ValueError):
        raise ApiError(400, "bad_field", "order_no 必须是整数")

    settings = _item_settings({key: value for key, value in body.items() if key in ITEM_FIELDS},
                              None, version)
    settings.setdefault("role", "example")
    settings.setdefault("hint_level", "none")
    settings.setdefault("required", 1)
    settings.setdefault("page_break_before", 0)

    previous = db.one(
        "SELECT id FROM collection_item WHERE collection_id = ? AND question_id = ? "
        "ORDER BY order_no, id LIMIT 1", (cid, qid))

    with db.transaction() as conn:
        iid = db.next_id(conn, "ITM")
        columns = ["id", "collection_id", "section_id", "question_id", "question_version_id",
                   "order_no", "created_at"] + list(settings.keys())
        values = [iid, cid, section_id, qid, version_id, order_no, db.now()] + list(settings.values())
        conn.execute(
            f"INSERT INTO collection_item({', '.join(columns)}) "
            f"VALUES({', '.join('?' * len(columns))})", values)
        _touch_collection(conn, cid)
        db.audit("item.create", "collection_item", iid,
                 {"collection_id": cid, "question_id": qid, "version_id": version_id})

    item = _item_by_id(cid, iid)
    warnings = list(item["warnings"])
    if previous:
        order = item["_index"] + 1
        warnings.append(
            f"同一题单里已有这道题（第 {order} 条，原条目 {previous['id']}），"
            "如需保留两个位置请确认这是有意重复")
    return {
        "item": _public(item),
        "warnings": warnings,
        "duplicate": bool(previous),
        "notice": (f"题单里已有同一道题（{previous['id']}）" if previous else ""),
    }


@post("/api/collections/{cid}/items/batch")
def item_batch_create(req):
    """N01 连续选题：向指定分节批量加入题目。

    逐项预校验，合法项在一个事务里整批写入；不合格项单独报告原因，
    不会因为一条出错就丢掉已经合法的其余条目。基准修订过期则整批不执行。
    """
    from app import commands

    cid = req.params["cid"]
    _collection(cid)
    body = req.json or {}
    entries = body.get("items")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ApiError(400, "bad_field", "items 必须是 1 至 100 条")
    section_id = _text(body.get("section_id")) or None
    if section_id:
        _section_in_collection(section_id, cid)

    existing = {row["question_id"] for row in db.all_(
        "SELECT question_id FROM collection_item WHERE collection_id = ?", (cid,))}

    prepared: list[dict] = []
    results: list[dict] = []
    seen: set[str] = set()

    for index, raw in enumerate(entries):
        entry = {"index": index, "question_id": None, "ok": False}
        try:
            if not isinstance(raw, dict):
                raise ApiError(400, "bad_field", "每条应为对象")
            qid = _text(raw.get("question_id")).strip()
            if not qid:
                raise ApiError(400, "missing_field", "缺少 question_id")
            entry["question_id"] = qid
            question = db.one("SELECT * FROM question WHERE id = ?", (qid,))
            if question is None:
                raise ApiError(404, "not_found", "题目 %s 不存在" % qid)
            if question["merged_into"]:
                raise ApiError(409, "merged", "题目已合并到 %s" % question["merged_into"])

            version_id = raw.get("question_version_id") or raw.get("version_id")
            if version_id:
                version = db.one("SELECT * FROM question_version WHERE id = ?", (version_id,))
                if version is None:
                    raise ApiError(404, "not_found", "版本 %s 不存在" % version_id)
                if version["question_id"] != qid:
                    raise ApiError(400, "version_mismatch", "版本 %s 不属于该题" % version_id)
            else:
                version_id = question["current_version_id"]
                if not version_id:
                    latest = db.one("SELECT id FROM question_version WHERE question_id = ? "
                                    "ORDER BY version_no DESC LIMIT 1", (qid,))
                    version_id = latest["id"] if latest else None
                    if not version_id:
                        raise ApiError(409, "no_version", "题目还没有任何版本")
                version = db.one("SELECT * FROM question_version WHERE id = ?", (version_id,))
            entry["question_version_id"] = version_id

            allow_duplicate = bool(raw.get("allow_duplicate"))
            if qid in existing and not allow_duplicate:
                raise ApiError(409, "duplicate", "题单里已有这道题")
            if qid in seen and not allow_duplicate:
                raise ApiError(409, "duplicate_in_batch", "本批里重复选了同一道题")

            settings = _item_settings(
                {key: value for key, value in raw.items() if key in ITEM_FIELDS},
                None, version)
            settings.setdefault("role", "example")
            settings.setdefault("hint_level", "none")
            settings.setdefault("required", 1)
            settings.setdefault("page_break_before", 0)

            seen.add(qid)
            prepared.append({"entry": entry, "settings": settings,
                             "question_id": qid, "version_id": version_id})
            entry["ok"] = True
        except ApiError as exc:
            entry.update({"code": exc.code, "message": exc.message})
        results.append(entry)

    def apply(conn, _before):
        next_no = conn.execute(
            "SELECT COALESCE(MAX(order_no), -1) + 1 AS n FROM collection_item "
            "WHERE collection_id = ?", (cid,)).fetchone()["n"]
        added = 0
        for index, item in enumerate(prepared):
            iid = db.next_id(conn, "ITM")
            columns = (["id", "collection_id", "section_id", "question_id",
                        "question_version_id", "order_no", "created_at"]
                       + list(item["settings"].keys()))
            values = ([iid, cid, section_id, item["question_id"], item["version_id"],
                       next_no + index, db.now()] + list(item["settings"].values()))
            conn.execute(
                "INSERT INTO collection_item(%s) VALUES(%s)"
                % (", ".join(columns), ", ".join("?" * len(columns))), values)
            item["entry"]["item_id"] = iid
            item["entry"]["order_no"] = next_no + index
            added += 1
            db.audit("item.create", "collection_item", iid,
                     {"collection_id": cid, "question_id": item["question_id"],
                      "version_id": item["version_id"], "batch": True})
        if added:
            _touch_collection(conn, cid)
        return {"results": results, "added": added,
                "section_id": section_id}

    outcome = commands.execute(cid, _text(body.get("operation_id")),
                               "items.batch", _batch_input(entries, section_id),
                               apply, base_revision=_text(body.get("base_revision")) or None)
    return outcome


def _batch_input(entries, section_id):
    """幂等摘要按「原始请求」计算，不按预校验后的子集。

    否则重试时合法项已入库、预校验把它们判成重复，摘要随之变化，
    同一个操作编号会被误判成「用旧编号改了内容」。
    """
    normalized = []
    for raw in entries:
        if isinstance(raw, dict):
            normalized.append({
                "question_id": str(raw.get("question_id") or ""),
                "version": raw.get("question_version_id") or raw.get("version_id") or None,
                "allow_duplicate": bool(raw.get("allow_duplicate")),
            })
        else:
            normalized.append({"invalid": True, "value": repr(raw)[:40]})
    return {"section_id": section_id, "items": normalized}


@patch("/api/items/{iid}")
def item_update(req):
    iid = req.params["iid"]
    item = _item_row(iid)
    version = db.one("SELECT * FROM question_version WHERE id = ?", (item["question_version_id"],))
    if version is None:
        raise ApiError(409, "broken_reference",
                       f"条目引用的版本 {item['question_version_id']} 已不存在")
    body = req.json or {}
    data = _item_settings(body, item, version)
    if not data:
        raise ApiError(400, "empty_body", "没有需要修改的字段")
    with db.transaction() as conn:
        assignments = ", ".join(f"{key} = ?" for key in data)
        conn.execute(f"UPDATE collection_item SET {assignments} WHERE id = ?",
                     list(data.values()) + [iid])
        from app.layout import clear_item_override
        clear_item_override(conn, item['collection_id'], iid, set(data) & {'space_mm', 'page_break_before'})
        _touch_collection(conn, item["collection_id"])
        db.audit("item.update", "collection_item", iid, data)
    return {"item": _public(_item_by_id(item["collection_id"], iid))}


@delete("/api/items/{iid}")
def item_delete(req):
    iid = req.params["iid"]
    item = _item_row(iid)
    with db.transaction() as conn:
        conn.execute("DELETE FROM collection_item WHERE id = ?", (iid,))
        from app.layout import clear_item_override
        clear_item_override(conn, item['collection_id'], iid)
        _touch_collection(conn, item["collection_id"])
        db.audit("item.delete", "collection_item", iid,
                 {"collection_id": item["collection_id"], "question_id": item["question_id"]})
    return {"ok": True, "deleted": iid}


ITEM_RESTORE_FIELDS = ("section_id", "question_id", "question_version_id", "order_no") + ITEM_FIELDS


@post("/api/items/{iid}/swap")
def item_swap(req):
    """N07 换一道：原位替换成另一道题，条目编号、分节、顺序与角色保留。

    题目专属设置（指定解法、小问范围、提示级别、逐题留白、题前换页）不照搬：
    新题有自己的一套，先清空并逐项列出，由教师重新设置。
    """
    from app import commands

    iid = req.params["iid"]
    row = _item_row(iid)
    cid = row["collection_id"]
    _collection(cid)
    body = req.json or {}
    qid = _text(body.get("question_id")).strip()
    if not qid:
        raise ApiError(400, "missing_field", "缺少 question_id")

    # 先查操作记录：重试时条目已经换过了，按当前状态校验会误判成「仍是同一道题」
    swap_payload = {"item_id": iid, "question_id": qid,
                    "version_id": _text(body.get("question_version_id")).strip() or ""}
    early = commands.replay_of(cid, _text(body.get("operation_id")), "item.swap", swap_payload)
    if early is not None:
        early["item"] = _public(_item_by_id(cid, iid))
        return early

    if qid == row["question_id"]:
        raise ApiError(409, "same_question", "换的仍是同一道题")

    question = db.one("SELECT * FROM question WHERE id = ?", (qid,))
    if question is None:
        raise ApiError(404, "not_found", "题目 %s 不存在" % qid)
    if question["merged_into"]:
        raise ApiError(409, "merged", "该题已合并到 %s，请改用它" % question["merged_into"])
    version_id = _text(body.get("question_version_id")).strip() or question["current_version_id"]
    version = db.one("SELECT * FROM question_version WHERE id = ?", (version_id,))
    if version is None:
        raise ApiError(404, "not_found", "版本 %s 不存在" % version_id)
    if version["question_id"] != qid:
        raise ApiError(400, "version_mismatch", "版本不属于这道题")

    dropped = []
    if row.get("solution_id"):
        dropped.append("指定解法")
    if row.get("subq_keys"):
        dropped.append("小问范围")
    if (row.get("hint_level") or "none") != "none":
        dropped.append("提示级别")
    if row.get("space_mm"):
        dropped.append("逐题留白")
    if row.get("page_break_before"):
        dropped.append("题前换页")

    previous = {"question_id": row["question_id"],
                "question_version_id": row["question_version_id"],
                "solution_id": row.get("solution_id"),
                "subq_keys": row.get("subq_keys"),
                "hint_level": row.get("hint_level"),
                "space_mm": row.get("space_mm"),
                "page_break_before": row.get("page_break_before")}

    def apply(conn, _before):
        conn.execute(
            "UPDATE collection_item SET question_id = ?, question_version_id = ?, "
            "solution_id = NULL, subq_keys = NULL, hint_level = 'none', "
            "space_mm = NULL, page_break_before = 0 WHERE id = ?",
            (qid, version_id, iid))
        from app.layout import clear_item_override
        clear_item_override(conn, cid, iid, {"space_mm", "page_break_before"})
        _touch_collection(conn, cid)
        db.audit("item.swap", "collection_item", iid,
                 {"from": row["question_id"], "to": qid, "version_id": version_id,
                  "dropped": dropped})
        return {"item_id": iid, "question_id": qid, "question_version_id": version_id,
                "dropped": dropped, "previous": previous}

    out = commands.execute(cid, _text(body.get("operation_id")), "item.swap",
                           {"item_id": iid, "question_id": qid, "version_id": swap_payload["version_id"]},
                           apply, base_revision=_text(body.get("base_revision")) or None)
    out["item"] = _public(_item_by_id(cid, iid))
    return out


@get("/api/collections/{cid}/version-updates")
def collection_version_updates(req):
    """N06 新版提醒：列出条目引用的版本落后于题目当前版本的条目。

    只提示，不改数据。归档与历史讲义走各自快照，这里只看可编辑的题单。
    """
    from app import commands

    cid = req.params["cid"]
    _collection(cid)
    rows = db.all_(
        "SELECT i.id AS item_id, i.order_no, i.section_id, i.question_id, "
        "       i.question_version_id AS used_version_id, i.solution_id, i.subq_keys, "
        "       q.short_name, q.current_version_id, q.status AS question_status, "
        "       v.version_no AS used_version_no, v.change_note AS used_change_note, "
        "       v.created_at AS used_at, "
        "       nv.version_no AS new_version_no, nv.change_note AS new_change_note, "
        "       nv.created_at AS new_at, nv.qtype AS new_qtype "
        "FROM collection_item i "
        "JOIN question q ON q.id = i.question_id "
        "JOIN question_version v ON v.id = i.question_version_id "
        "JOIN question_version nv ON nv.id = q.current_version_id "
        "WHERE i.collection_id = ? AND q.current_version_id <> i.question_version_id "
        "ORDER BY i.order_no, i.id", (cid,))
    items = [{
        "item_id": row["item_id"], "order_no": row["order_no"],
        "section_id": row["section_id"], "question_id": row["question_id"],
        "short_name": row["short_name"], "question_status": row["question_status"],
        "used": {"version_id": row["used_version_id"], "version_no": row["used_version_no"],
                 "change_note": row["used_change_note"], "created_at": row["used_at"]},
        "new": {"version_id": row["current_version_id"], "version_no": row["new_version_no"],
                "change_note": row["new_change_note"], "created_at": row["new_at"],
                "qtype": row["new_qtype"]},
        # 换了版本后这些"题目专属设置"可能失配，必须由教师重选，不能照搬
        "needs_review": [label for label, present in
                         (("指定解法", bool(row["solution_id"])),
                          ("小问范围", bool(row["subq_keys"]))) if present],
    } for row in rows]
    return {"items": items, "total": len(items), "revision": commands.collection_revision(cid)}


@post("/api/collections/{cid}/version-updates/apply")
def collection_version_updates_apply(req):
    """N06 显式更新：把选中的条目换到题目当前版本，整批原子执行。

    不自动升级、不猜新旧解法或小问的对应关系；换版后失配的解法与小问范围直接清空并逐条报告，
    让教师在新版上重选，而不是把旧编号硬套到新版本。
    """
    from app import commands

    cid = req.params["cid"]
    _collection(cid)
    body = req.json or {}
    updates = body.get("updates")
    if not isinstance(updates, list) or not 1 <= len(updates) <= 100:
        raise ApiError(400, "bad_field", "updates 必须是 1 至 100 条")

    prepared, results = [], []
    for index, raw in enumerate(updates):
        entry = {"index": index, "item_id": None, "ok": False}
        try:
            if not isinstance(raw, dict):
                raise ApiError(400, "bad_field", "每条应为对象")
            item_id = _text(raw.get("item_id")).strip()
            entry["item_id"] = item_id
            row = _item_row(item_id)
            if row["collection_id"] != cid:
                raise ApiError(404, "not_found", "条目不属于这个题单")
            question = db.one("SELECT * FROM question WHERE id = ?", (row["question_id"],))
            target_id = _text(raw.get("question_version_id")).strip() or question["current_version_id"]
            target = db.one("SELECT * FROM question_version WHERE id = ?", (target_id,))
            if target is None:
                raise ApiError(404, "not_found", "目标版本不存在")
            if target["question_id"] != row["question_id"]:
                raise ApiError(400, "version_mismatch", "目标版本不属于这道题")
            if target_id == row["question_version_id"]:
                raise ApiError(409, "already_current", "这条已经是该版本")
            used = db.one("SELECT version_no FROM question_version WHERE id = ?",
                          (row["question_version_id"],))
            if used and target["version_no"] <= used["version_no"]:
                raise ApiError(409, "not_newer", "只能更新到更新的版本")

            # 换版后哪些设置会失效：旧解法、旧小问都不属于新版本
            solution_ok = bool(row["solution_id"]) and bool(db.one(
                "SELECT 1 AS x FROM solution WHERE id = ? AND question_version_id = ?",
                (row["solution_id"], target_id)))
            keys = row.get("subq_keys") or []
            new_keys = {str(sub.get("key")) for sub in (target.get("subquestions") or [])
                        if isinstance(sub, dict)}
            kept_keys = [key for key in keys if str(key) in new_keys] if keys else []

            prepared.append({"item_id": item_id, "version_id": target_id,
                             "solution_id": row["solution_id"] if solution_ok else None,
                             "subq_keys": kept_keys or None,
                             "dropped": [label for label, gone in
                                         (("指定解法", bool(row["solution_id"]) and not solution_ok),
                                          ("小问范围", bool(keys) and len(kept_keys) != len(keys)))
                                         if gone]})
            entry.update({"ok": True, "version_id": target_id,
                          "dropped": prepared[-1]["dropped"]})
        except ApiError as exc:
            entry.update({"code": exc.code, "message": exc.message})
        results.append(entry)

    def apply(conn, _before):
        for item in prepared:
            conn.execute(
                "UPDATE collection_item SET question_version_id = ?, solution_id = ?, "
                "subq_keys = ? WHERE id = ?",
                (item["version_id"], item["solution_id"], db.dumps(item["subq_keys"]),
                 item["item_id"]))
            db.audit("item.version_update", "collection_item", item["item_id"],
                     {"version_id": item["version_id"], "dropped": item["dropped"]})
        if prepared:
            _touch_collection(conn, cid)
        return {"results": results, "updated": len(prepared)}

    return commands.execute(cid, _text(body.get("operation_id")), "version-updates.apply",
                            {"updates": [{"item_id": _text(r.get("item_id")),
                                          "version_id": r.get("question_version_id")}
                                         for r in updates if isinstance(r, dict)]},
                            apply, base_revision=_text(body.get("base_revision")) or None)


@post("/api/collections/{cid}/commands")
def collection_command(req):
    """N02 首期：可撤销的题单写入。

    每个动作都返回它的逆动作；页面把逆动作压进撤销栈，撤销时再发一次同样的请求。
    服务端只负责「校验修订 + 执行 + 记录」，不保存撤销栈——栈属于当前页面会话。
    """
    from app import commands
    from app.layout import clear_item_override

    cid = req.params["cid"]
    _collection(cid)
    body = req.json or {}
    action = _text(body.get("action")).strip()
    item_id = _text(body.get("item_id")).strip()
    payload = body.get("payload")
    if action not in ("remove", "restore", "reorder", "settings"):
        raise ApiError(400, "bad_field",
                       "action 只能是 remove、restore、reorder、settings")
    if not isinstance(payload, dict):
        payload = {}

    def item_row(iid: str) -> dict:
        row = _item_row(iid)
        if row["collection_id"] != cid:
            raise ApiError(404, "not_found", "条目 %s 不属于题单 %s" % (iid, cid))
        return row

    def snapshot(row: dict) -> dict:
        return {key: row[key] for key in ITEM_RESTORE_FIELDS if key in row}

    def apply(conn, _before):
        inverse = None
        detail = {"action": action, "item_id": item_id}

        if action == "remove":
            row = item_row(item_id)
            inverse = {"action": "restore", "item_id": item_id, "payload": snapshot(row)}
            conn.execute("DELETE FROM collection_item WHERE id = ?", (item_id,))
            clear_item_override(conn, cid, item_id)
            detail.update({"question_id": row["question_id"]})
            label = "移除题目"

        elif action == "restore":
            existing = conn.execute("SELECT collection_id FROM collection_item WHERE id = ?",
                                    (item_id,)).fetchone()
            if existing is not None:
                raise ApiError(409, "already_exists",
                               "条目 %s 已存在，不能重复恢复" % item_id)
            question_id = _text(payload.get("question_id")).strip()
            version_id = _text(payload.get("question_version_id")).strip()
            if not question_id or not version_id:
                raise ApiError(400, "bad_field", "恢复条目需要 question_id 与 question_version_id")
            version = db.one("SELECT * FROM question_version WHERE id = ?", (version_id,))
            if version is None or version["question_id"] != question_id:
                raise ApiError(409, "version_mismatch", "要恢复的版本已失效或不属于该题")
            section_id = payload.get("section_id") or None
            if section_id:
                _section_in_collection(section_id, cid)
            order_no = int(payload.get("order_no") or 0)
            settings = {key: payload.get(key) for key in ITEM_FIELDS if key in payload}
            columns = (["id", "collection_id", "section_id", "question_id",
                        "question_version_id", "order_no", "created_at"] + list(settings.keys()))
            values = ([item_id, cid, section_id, question_id, version_id, order_no, db.now()]
                      + list(settings.values()))
            conn.execute("INSERT INTO collection_item(%s) VALUES(%s)"
                         % (", ".join(columns), ", ".join("?" * len(columns))), values)
            inverse = {"action": "remove", "item_id": item_id, "payload": {}}
            detail.update({"question_id": question_id})
            label = "恢复题目"

        elif action == "reorder":
            rows = payload.get("items")
            if not isinstance(rows, list) or not rows:
                raise ApiError(400, "bad_field", "reorder 需要 items 数组")
            previous = []
            for row in rows:
                iid = _text(row.get("id")).strip()
                current = item_row(iid)
                previous.append({"id": iid, "order_no": current["order_no"],
                                 "section_id": current["section_id"]})
            for row in rows:
                iid = _text(row.get("id")).strip()
                section_id = row.get("section_id") or None
                if section_id:
                    _section_in_collection(section_id, cid)
                conn.execute("UPDATE collection_item SET order_no = ?, section_id = ? WHERE id = ?",
                             (int(row.get("order_no") or 0), section_id, iid))
            inverse = {"action": "reorder", "item_id": "", "payload": {"items": previous}}
            label = "调整顺序"

        else:  # settings
            row = item_row(item_id)
            data = _item_settings(payload, row,
                                  db.one("SELECT * FROM question_version WHERE id = ?",
                                         (row["question_version_id"],)) or {})
            if not data:
                raise ApiError(400, "empty_body", "没有需要修改的设置")
            previous = {key: row.get(key) for key in data}
            assignments = ", ".join("%s = ?" % key for key in data)
            conn.execute("UPDATE collection_item SET %s WHERE id = ?" % assignments,
                         list(data.values()) + [item_id])
            clear_item_override(conn, cid, item_id,
                                set(data) & {"space_mm", "page_break_before"})
            inverse = {"action": "settings", "item_id": item_id, "payload": previous}
            label = "修改条目设置"

        _touch_collection(conn, cid)
        db.audit("collection.command", "collection", cid,
                 {"action": action, "item_id": item_id or None, "label": label})
        return {"inverse": inverse, "label": label, "action": action}

    return commands.execute(cid, _text(body.get("operation_id")),
                            "command." + action,
                            {"action": action, "item_id": item_id, "payload": payload},
                            apply, base_revision=_text(body.get("base_revision")) or None)


@post("/api/collections/{cid}/reorder")
def collection_reorder(req):
    cid = req.params["cid"]
    _collection(cid)
    body = req.json or {}
    rows = body.get("items")
    if not isinstance(rows, list) or not rows:
        raise ApiError(400, "bad_body",
                       "items 必须是非空数组，形如 [{\"id\",\"order_no\",\"section_id\"}]")
    plans: list[tuple[str, int, str | None]] = []
    owned = {row["id"] for row in db.all_(
        "SELECT id FROM collection_item WHERE collection_id = ?", (cid,))}
    section_ids = {row["id"] for row in db.all_(
        "SELECT id FROM collection_section WHERE collection_id = ?", (cid,))}
    for entry in rows:
        iid = _text((entry or {}).get("id")).strip()
        if not iid:
            raise ApiError(400, "bad_body", "items 里每一项都要有 id")
        if iid not in owned:
            raise ApiError(404, "not_found", f"条目 {iid} 不属于题单 {cid}")
        try:
            order_no = int(entry.get("order_no"))
        except (TypeError, ValueError):
            raise ApiError(400, "bad_body", f"条目 {iid} 的 order_no 必须是整数")
        section_id = entry.get("section_id") or None
        if section_id and section_id not in section_ids:
            raise ApiError(400, "bad_body", f"分节 {section_id} 不属于题单 {cid}")
        plans.append((iid, order_no, section_id))

    with db.transaction() as conn:
        for iid, order_no, section_id in plans:
            conn.execute(
                "UPDATE collection_item SET order_no = ?, section_id = ? WHERE id = ?",
                (order_no, section_id, iid))
        _touch_collection(conn, cid)
        db.audit("collection.reorder", "collection", cid,
                 {"moved": len(plans), "ids": [p[0] for p in plans]})
    return {"updated": len(plans),
            "items": [_public(item) for item in _load_items(cid)]}


@get("/api/collections/{cid}/check")
def collection_check(req):
    cid = req.params["cid"]
    collection = _collection(cid)
    return _build_check(collection, _load_items(cid))


# ---------------------------------------------------------------- §10 导出与发布


def _latex_module(name: str):
    """函数内 import 排版模块，缺失时给中文提示而不是 500 堆栈。"""
    try:
        module = __import__("app.latex.build", fromlist=[name])
    except Exception as exc:  # noqa: BLE001 —— 模块可能尚未创建
        raise ApiError(
            503, "latex_missing",
            "排版模块 app/latex/build.py 尚未就绪，暂时无法生成讲义。"
            f"该模块由排版域负责，缺失或导入失败：{type(exc).__name__}: {exc}")
    func = getattr(module, name, None)
    if func is None:
        raise ApiError(503, "latex_missing",
                       f"排版模块 app/latex/build.py 里没有 {name}，暂时无法生成讲义。")
    return func


@post("/api/collections/{cid}/build")
def collection_build(req):
    cid = req.params["cid"]
    collection = _collection(cid)
    body = req.json or {}

    count = db.scalar("SELECT COUNT(*) FROM collection_item WHERE collection_id = ?", (cid,)) or 0
    if not count:
        raise ApiError(409, "empty_collection", "题单还没有任何题目，无法生成讲义")

    options = dict(BUILD_DEFAULTS)
    options.update((collection.get("layout_options") or {}).get("options") or {})
    options["title"] = options.get("title") or collection["title"]
    options["template"] = collection["template"] or BUILD_DEFAULTS["template"]
    for key, value in body.items():
        options[key] = value
    if not _text(options.get("title")).strip():
        options["title"] = collection["title"]

    versions = options.get("versions")
    if not isinstance(versions, list) or not versions:
        raise ApiError(400, "bad_field", "versions 必须是非空数组，可取 student、answer、teacher")
    bad = [v for v in versions if v not in ("student", "answer", "teacher")]
    if bad:
        raise ApiError(400, "bad_field",
                       f"versions 只能取 student、answer、teacher，收到：{'、'.join(map(str, bad))}")
    if options.get("format") not in ("standard", "compact", "loose", "single"):
        raise ApiError(400, "bad_field",
                       f"format 只能取 standard、compact、loose、single，收到：{options.get('format')}")

    build_release = _latex_module("build_release")
    try:
        result = build_release(cid, options)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ApiError(500, "build_failed",
                       f"生成讲义失败（排版模块内部错误）：{type(exc).__name__}: {exc}")

    release_id = None
    if isinstance(result, dict):
        release = result.get("release")
        if isinstance(release, dict):
            release_id = release.get("id")
    db.audit("collection.build", "collection", cid,
             {"release_id": release_id, "options": {k: v for k, v in options.items()}})
    db.db().commit()
    return result


def _release_or_404(rid: str) -> dict:
    release = db.one("SELECT * FROM release WHERE id = ?", (rid,))
    if release is None:
        raise ApiError(404, "not_found", f"发布物 {rid} 不存在")
    return release


def _release_urls(release: dict) -> dict:
    out = {}
    for key in ("student", "teacher", "answer"):
        path = release.get(f"{key}_path")
        if path:
            out[f"{key}_url"] = "/files/" + str(path).lstrip("/")
    return out


@get("/api/releases")
def release_list(req):
    collection_id = req.arg("collection_id")
    status = req.arg("status")
    limit = req.arg("limit", 200, int)
    sql = ("SELECT r.*, c.title AS collection_title FROM release r "
           "LEFT JOIN collection c ON c.id = r.collection_id WHERE 1 = 1")
    params: list = []
    if collection_id:
        sql += " AND r.collection_id = ?"
        params.append(collection_id)
    if status:
        sql += " AND r.status = ?"
        params.append(status)
    total = db.scalar(f"SELECT COUNT(*) FROM ({sql})", params) or 0
    sql += " ORDER BY r.created_at DESC, r.id DESC LIMIT ?"
    params.append(limit)
    rows = db.all_(sql, params)
    items = []
    for row in rows:
        row.pop("snapshot", None)
        row.update(_release_urls(row))
        items.append(row)
    return {"items": items, "total": total}


@get("/api/releases/{rid}")
def release_detail(req):
    rid = req.params["rid"]
    release = _release_or_404(rid)
    release["collection_title"] = db.scalar(
        "SELECT title FROM collection WHERE id = ?", (release["collection_id"],))
    release.update(_release_urls(release))
    if isinstance(release.get("snapshot"), str):
        release["snapshot"] = _loads(release["snapshot"], {})
    return {"release": release}


@get("/api/releases/{rid}/snapshot")
def release_snapshot(req):
    rid = req.params["rid"]
    release = _release_or_404(rid)
    snapshot = release.get("snapshot")
    if isinstance(snapshot, str):
        snapshot = _loads(snapshot, None)
        if snapshot is None:
            raise ApiError(500, "bad_snapshot", f"发布物 {rid} 的快照不是合法 JSON")
    return snapshot if snapshot is not None else {}


@post("/api/releases/{rid}/rebuild")
def release_rebuild(req):
    rid = req.params["rid"]
    release = _release_or_404(rid)
    snapshot = _loads(release.get("snapshot"), None)
    if not snapshot:
        raise ApiError(409, "bad_snapshot", f"发布物 {rid} 没有可用快照，无法重导")
    rebuild_release = _latex_module("rebuild_release")
    try:
        result = rebuild_release(rid)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ApiError(500, "rebuild_failed",
                       f"重导失败（排版模块内部错误）：{type(exc).__name__}: {exc}")
    db.audit("release.rebuild", "release", rid, {"collection_id": release["collection_id"]})
    db.db().commit()
    return result


# ---------------------------------------------------------------- §11 使用对象


@get("/api/audiences")
def audience_list(req):
    keyword = req.arg("keyword")
    kind = req.arg("kind")
    sql = ("SELECT a.*, "
           "(SELECT COUNT(*) FROM assignment s WHERE s.audience_id = a.id) AS assignment_count, "
           "(SELECT COUNT(*) FROM usage_log u WHERE u.audience_id = a.id) AS usage_count, "
           "(SELECT MAX(u.used_on) FROM usage_log u WHERE u.audience_id = a.id) AS last_used_on "
           "FROM audience a WHERE 1 = 1")
    params: list = []
    if keyword:
        sql += " AND (a.name LIKE ? OR a.code LIKE ?)"
        like = f"%{keyword}%"
        params += [like, like]
    if kind:
        sql += " AND a.kind = ?"
        params.append(kind)
    sql += " ORDER BY a.kind, a.name"
    from app.api import roster
    items = roster.filter_rows(roster.decorate(db.all_(sql, params)), req.arg('teacher_id'))
    return {"items": items, "total": len(items)}


AUDIENCE_FIELDS = ("kind", "name", "code", "note")


@post("/api/audiences")
def audience_create(req):
    from app.api import roster
    body = req.json or {}
    kind = _enum(body.get("kind"), AUDIENCE_KINDS, "kind")
    if not kind:
        raise ApiError(400, "missing_field", "缺少必填字段：kind（class 班级、group 小组、student 个人）")
    name = roster.text(body.get('name'), '名称')
    if not name:
        raise ApiError(400, "missing_field", "缺少必填字段：name（名称）")
    with db.transaction() as conn:
        aid = db.next_id(conn, "AUD")
        conn.execute(
            "INSERT INTO audience(id, kind, name, code, note, created_at) VALUES(?,?,?,?,?,?)",
            (aid, kind, name, None if body.get("code") is None else _text(body.get("code")),
             None if body.get("note") is None else _text(body.get("note")), db.now()),
        )
        if 'teacher_ids' in body:roster.assign(conn, aid, body['teacher_ids'])
        db.audit("audience.create", "audience", aid, {"kind": kind, "name": name})
    return {"audience": db.one("SELECT * FROM audience WHERE id = ?", (aid,))}


@patch("/api/audiences/{aid}")
def audience_update(req):
    aid = req.params["aid"]
    if db.one("SELECT id FROM audience WHERE id = ?", (aid,)) is None:
        raise ApiError(404, "not_found", f"使用对象 {aid} 不存在")
    body = req.json or {}
    unknown = [key for key in body if key not in AUDIENCE_FIELDS]
    if unknown:
        raise ApiError(400, "bad_field", f"使用对象不接受这些字段：{'、'.join(unknown)}")
    data: dict = {}
    if "kind" in body:
        if body["kind"] not in AUDIENCE_KINDS:
            raise ApiError(400, "bad_field", "请选择有效的班级、小组或学生类型")
        data["kind"] = _enum(body["kind"], AUDIENCE_KINDS, "kind")
    if "name" in body:
        from app.api.roster import text
        data["name"] = text(body["name"], "名称")
    for field in ("code", "note"):
        if field in body:
            data[field] = None if body[field] is None else _text(body[field])
    if not data:
        raise ApiError(400, "empty_body", "没有需要修改的字段")
    with db.transaction() as conn:
        if 'kind' in data and data['kind'] != db.one('SELECT kind FROM audience WHERE id=?',(aid,))['kind']:
            linked = db.scalar('SELECT count(*) FROM roster_member WHERE container_id=? OR student_id=?',(aid,aid))
            if linked:raise ApiError(409,'linked_roster','已有班级名单关联，不能直接改变类型')
        assignments = ", ".join(f"{key} = ?" for key in data)
        conn.execute(f"UPDATE audience SET {assignments} WHERE id = ?",
                     list(data.values()) + [aid])
        db.audit("audience.update", "audience", aid, data)
    return {"audience": db.one("SELECT * FROM audience WHERE id = ?", (aid,))}


@get("/api/audiences/{aid}/recent")
def audience_recent(req):
    aid = req.params["aid"]
    audience = db.one("SELECT * FROM audience WHERE id = ?", (aid,))
    if audience is None:
        raise ApiError(404, "not_found", f"使用对象 {aid} 不存在")
    days = req.arg("days", 30, int)
    cutoff = _cutoff(days)

    from app.history import question_history
    items, seen = [], set()
    for row in question_history(aid, cutoff):
        if row['question_id'] in seen:
            continue
        seen.add(row['question_id'])
        items.append({k: v for k, v in row.items() if k != 'stem'})
        items[-1]['stem_preview'] = _preview(row['stem'])

    payload = {
        "items": items,
        "total": len(items),
        "audience_id": aid,
        "audience_name": audience["name"],
        "days": days,
    }
    if not items:
        # 没有任何使用记录时只说明「无记录」，不推断学生没做过
        payload["note"] = "无记录"
    return payload


# ---------------------------------------------------------------- §11 出题任务


@get("/api/assignments")
def assignment_list(req):
    audience_id = req.arg("audience_id")
    status = req.arg("status")
    collection_id = req.arg("collection_id")
    sql = ("SELECT s.*, c.title AS collection_title, a.name AS audience_name, "
           "r.status AS release_status "
           "FROM assignment s "
           "LEFT JOIN collection c ON c.id = s.collection_id "
           "LEFT JOIN audience a ON a.id = s.audience_id "
           "LEFT JOIN release r ON r.id = s.release_id WHERE 1 = 1")
    params: list = []
    if audience_id:
        sql += " AND s.audience_id = ?"
        params.append(audience_id)
    if status:
        sql += " AND s.status = ?"
        params.append(status)
    if collection_id:
        sql += " AND s.collection_id = ?"
        params.append(collection_id)
    sql += " ORDER BY COALESCE(s.plan_date, '') DESC, s.created_at DESC"
    items = db.all_(sql, params)
    return {"items": items, "total": len(items)}


ASSIGN_FIELDS = ("title", "collection_id", "release_id", "audience_id", "plan_date",
                 "requirements", "must_do", "optional", "due_date", "answer_policy",
                 "status", "note")


def _assign_payload(body: dict, partial: bool) -> dict:
    data: dict = {}
    if "title" in body or not partial:
        title = _text(body.get("title")).strip()
        if not title:
            raise ApiError(400, "missing_field", "缺少必填字段：title（任务标题）")
        data["title"] = title

    release_id = body.get("release_id") or None
    collection_id = body.get("collection_id") or None
    if release_id:
        release = db.one("SELECT id, collection_id FROM release WHERE id = ?", (release_id,))
        if release is None:
            raise ApiError(404, "not_found", f"发布物 {release_id} 不存在")
        if collection_id and release["collection_id"] != collection_id:
            raise ApiError(400, "release_mismatch", f"发布物 {release_id} 不属于题单 {collection_id}")
        collection_id = collection_id or release["collection_id"]
    if collection_id:
        if db.one("SELECT id FROM collection WHERE id = ?", (collection_id,)) is None:
            raise ApiError(404, "not_found", f"题单 {collection_id} 不存在")
    audience_id = body.get("audience_id") or None
    if audience_id:
        if db.one("SELECT id FROM audience WHERE id = ?", (audience_id,)) is None:
            raise ApiError(404, "not_found", f"使用对象 {audience_id} 不存在")

    if "release_id" in body:
        data["release_id"] = release_id
    if "collection_id" in body or release_id:
        data["collection_id"] = collection_id
    if "audience_id" in body:
        data["audience_id"] = audience_id
    for field in ("requirements", "must_do", "optional", "answer_policy", "note"):
        if field in body:
            data[field] = None if body[field] is None else _text(body[field])
    for field in ("plan_date", "due_date"):
        if field in body:
            data[field] = _check_date(body[field], field)
    if "status" in body:
        data["status"] = _enum(body["status"], ASSIGN_STATUS, "status")
    elif not partial:
        data["status"] = "planned"
    return data


@post("/api/assignments")
def assignment_create(req):
    body = req.json or {}
    unknown = [key for key in body if key not in ASSIGN_FIELDS]
    if unknown:
        raise ApiError(400, "bad_field", f"任务不接受这些字段：{'、'.join(unknown)}")
    data = _assign_payload(body, partial=False)
    with db.transaction() as conn:
        aid = db.next_id(conn, "ASG")
        columns = ["id", "created_at"] + list(data.keys())
        values = [aid, db.now()] + list(data.values())
        conn.execute(
            f"INSERT INTO assignment({', '.join(columns)}) VALUES({', '.join('?' * len(columns))})",
            values)
        db.audit("assignment.create", "assignment", aid,
                 {"title": data["title"], "collection_id": data.get("collection_id"),
                  "audience_id": data.get("audience_id")})
    row = db.one("SELECT * FROM assignment WHERE id = ?", (aid,))
    warnings = []
    if row["status"] == "distributed" and not row["release_id"]:
        warnings.append("任务已标为已发放，但还没有关联发布物，请先生成讲义再发放")
    return {"assignment": row, "warnings": warnings}


@patch("/api/assignments/{aid}")
def assignment_update(req):
    aid = req.params["aid"]
    current = db.one("SELECT * FROM assignment WHERE id = ?", (aid,))
    if current is None:
        raise ApiError(404, "not_found", f"出题任务 {aid} 不存在")
    body = req.json or {}
    unknown = [key for key in body if key not in ASSIGN_FIELDS]
    if unknown:
        raise ApiError(400, "bad_field", f"任务不接受这些字段：{'、'.join(unknown)}")
    data = _assign_payload(body, partial=True)
    if not data:
        raise ApiError(400, "empty_body", "没有需要修改的字段")
    with db.transaction() as conn:
        assignments = ", ".join(f"{key} = ?" for key in data)
        conn.execute(f"UPDATE assignment SET {assignments} WHERE id = ?",
                     list(data.values()) + [aid])
        db.audit("assignment.update", "assignment", aid, data)
    row = db.one("SELECT * FROM assignment WHERE id = ?", (aid,))
    warnings = []
    if row["status"] == "distributed" and not row["release_id"]:
        warnings.append("任务已标为已发放，但还没有关联发布物，请先生成讲义再发放")
    if row["status"] == "distributed":
        # 发放只说明发出去了，不代表已经上过课；这里只是提醒，不自动写使用记录
        warnings.append("发放不等于已使用：实际上课后请另外登记使用记录（POST /api/usages）")
    return {"assignment": row, "warnings": warnings}


# ---------------------------------------------------------------- §11 使用记录


@get("/api/usages/{uid}/item-feedback")
def usage_item_feedback_list(req):
    """N09 逐题反馈：只列这次使用里实际登记过的条目。"""
    from app import history

    uid = req.params["uid"]
    usage = db.one("SELECT * FROM usage_log WHERE id = ?", (uid,))
    if usage is None:
        raise ApiError(404, "not_found", "没有这条使用记录：%s" % uid)
    release = db.one("SELECT snapshot FROM release WHERE id = ?", (usage["release_id"],)) or {}
    snapshot = _loads(release.get("snapshot"), {})
    used = {row.get("id"): row for row in history.selected_items(snapshot, usage.get("items"))}
    notes = db.all_("SELECT * FROM usage_item_feedback WHERE usage_id = ? ORDER BY created_at, id",
                    (uid,))
    out = []
    for row in notes:
        item = used.get(row["item_id"]) or {}
        out.append(dict(row, in_use_scope=bool(item),
                        question_no=(item.get("order_no") if item else None)))
    return {"items": out, "total": len(out),
            "scope": [{"item_id": item_id, "question_id": row.get("question_id"),
                       "question_version_id": row.get("question_version_id")}
                      for item_id, row in used.items()]}


@post("/api/usages/{uid}/item-feedback")
def usage_item_feedback_create(req):
    """登记一道题的观察。只能针对这次使用里实际登记过的条目。"""
    from app import history

    uid = req.params["uid"]
    usage = db.one("SELECT * FROM usage_log WHERE id = ?", (uid,))
    if usage is None:
        raise ApiError(404, "not_found", "没有这条使用记录：%s" % uid)
    body = req.json or {}
    item_id = _text(body.get("item_id")).strip()
    if not item_id:
        raise ApiError(400, "missing_field", "缺少 item_id")
    release = db.one("SELECT snapshot FROM release WHERE id = ?", (usage["release_id"],)) or {}
    snapshot = _loads(release.get("snapshot"), {})
    used = {row.get("id"): row for row in history.selected_items(snapshot, usage.get("items"))}
    item = used.get(item_id)
    if item is None:
        raise ApiError(409, "not_in_use_scope",
                       "这条不属于本次实际使用的题目，不能登记为本次观察")
    note = _text(body.get("error_note")).strip()
    if not note:
        raise ApiError(400, "missing_field", "缺少 error_note（具体错在哪）")
    tags = body.get("error_tags")
    tags = [str(t) for t in tags if str(t).strip()] if isinstance(tags, list) else None
    with db.transaction() as conn:
        existing = db.one("SELECT id FROM usage_item_feedback WHERE usage_id = ? AND item_id = ?",
                          (uid, item_id))
        if existing:
            conn.execute(
                "UPDATE usage_item_feedback SET error_note = ?, error_tags = ?, sample_scope = ?, "
                "suggestion = ? WHERE id = ?",
                (note, db.dumps(tags), _text(body.get("sample_scope")) or None,
                 _text(body.get("suggestion")) or None, existing["id"]))
            fid = existing["id"]
        else:
            fid = db.next_id(conn, "UIF")
            conn.execute(
                "INSERT INTO usage_item_feedback(id, usage_id, item_id, question_id, "
                "question_version_id, error_note, error_tags, sample_scope, suggestion, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (fid, uid, item_id, item.get("question_id"), item.get("question_version_id"),
                 note, db.dumps(tags), _text(body.get("sample_scope")) or None,
                 _text(body.get("suggestion")) or None, db.now()))
        db.audit("usage.item_feedback", "usage_log", uid,
                 {"item_id": item_id, "feedback_id": fid})
    return {"feedback": db.one("SELECT * FROM usage_item_feedback WHERE id = ?", (fid,))}


@get("/api/usage-summary")
def usage_summary(req):
    """N04 按教学对象批量查已用：一次问清当前这页题在该对象下的最近使用日期。

    口径与「仅未用过」筛选一致：只认实际使用记录，计划使用与导出不算；
    只登记了部分题目时只统计那部分。没有记录时返回空，界面应写「未记录使用」，
    不能推断成「学生没见过」。
    """
    from datetime import datetime, timedelta
    from app import history

    audience_id = req.arg("audience_id")
    if not audience_id:
        raise ApiError(400, "missing_field", "缺少 audience_id")
    audience = db.one("SELECT * FROM audience WHERE id = ?", (audience_id,))
    if audience is None:
        raise ApiError(404, "not_found", "没有这个教学对象：%s" % audience_id)
    days = req.arg("days", 30, int)
    if days < 0:
        raise ApiError(400, "bad_query", "days 不能为负")
    raw_ids = [x for x in (req.arg("question_ids") or "").split(",") if x.strip()]
    wanted = list(dict.fromkeys(raw_ids))[:200]

    cutoff = (datetime.now(db.TZ).date() - timedelta(days=days)).isoformat()
    summary: dict[str, dict] = {}
    for row in history.question_history(audience_id, cutoff):
        qid = row.get("question_id")
        if not qid or (wanted and qid not in wanted):
            continue
        current = summary.get(qid)
        if current is None or row["used_on"] > current["used_on"]:
            summary[qid] = {"used_on": row["used_on"], "usage_id": row["usage_id"],
                            "collection_title": row.get("collection_title") or ""}
    return {
        "audience": {"id": audience["id"], "name": audience.get("name"),
                     "kind": audience.get("kind")},
        "days": days,
        "cutoff": cutoff,
        "checked": len(wanted),
        "items": summary,
    }


@get("/api/usages")
def usage_list(req):
    audience_id = req.arg("audience_id")
    release_id = req.arg("release_id")
    days = req.arg("days", None, int)
    cutoff = _cutoff(days)
    sql = ("SELECT u.*, c.title AS collection_title, a.name AS audience_name, "
           "s.title AS assignment_title, "
           "(SELECT COUNT(*) FROM feedback f WHERE f.usage_id = u.id) AS feedback_count "
           "FROM usage_log u "
           "LEFT JOIN release r ON r.id = u.release_id "
           "LEFT JOIN collection c ON c.id = r.collection_id "
           "LEFT JOIN audience a ON a.id = u.audience_id "
           "LEFT JOIN assignment s ON s.id = u.assignment_id WHERE 1 = 1")
    params: list = []
    if audience_id:
        sql += " AND u.audience_id = ?"
        params.append(audience_id)
    if release_id:
        sql += " AND u.release_id = ?"
        params.append(release_id)
    if cutoff:
        sql += " AND u.used_on >= ?"
        params.append(cutoff)
    sql += " ORDER BY u.used_on DESC, u.created_at DESC, u.id DESC"
    items = db.all_(sql, params)
    return {"items": items, "total": len(items)}


def _validate_usage_selection(release: dict, selection) -> None:
    if release.get('status') == 'failed':
        raise ApiError(409, 'failed_release', '编译失败的发布物不能登记为实际使用')
    if selection is None:
        return
    if not isinstance(selection, list) or any(not isinstance(iid, str) for iid in selection):
        raise ApiError(400, 'bad_field', 'items 必须是条目 ID 字符串数组或 null')
    snapshot = _loads(release.get('snapshot'), {})
    valid = {item.get('id') for item in snapshot.get('items', [])}
    missing = [iid for iid in selection if iid not in valid]
    if missing:
        raise ApiError(400, 'bad_items', '这些条目不属于该发布物的历史快照：'+'、'.join(missing))


@post("/api/usages")
def usage_create(req):
    body = req.json or {}
    release_id = _text(body.get("release_id")).strip()
    if not release_id:
        raise ApiError(400, "missing_field",
                       "缺少必填字段：release_id（只有已生成的发布物才能登记使用）")
    release = db.one("SELECT * FROM release WHERE id = ?", (release_id,))
    if release is None:
        raise ApiError(404, "not_found", f"发布物 {release_id} 不存在，请先生成讲义")

    audience_id = _text(body.get("audience_id")).strip()
    if not audience_id:
        raise ApiError(400, "missing_field", "缺少必填字段：audience_id（给谁用）")
    audience = db.one("SELECT * FROM audience WHERE id = ?", (audience_id,))
    if audience is None:
        raise ApiError(404, "not_found", f"使用对象 {audience_id} 不存在")

    if not _text(body.get("used_on")).strip():
        raise ApiError(400, "missing_field", "缺少必填字段：used_on（实际使用日期）")
    used_on = _check_date(body.get("used_on"), "used_on")

    purpose = _enum(body.get("purpose") or "class", USAGE_PURPOSES, "purpose")

    assignment_id = body.get("assignment_id") or None
    if assignment_id:
        assignment = db.one("SELECT * FROM assignment WHERE id = ?", (assignment_id,))
        if assignment is None:
            raise ApiError(404, "not_found", f"出题任务 {assignment_id} 不存在")

    items = body.get("items")
    _validate_usage_selection(release, items)

    # N05：快捷登记会给每次提交一个操作编号，双击或超时重试不会写两条使用记录。
    op_key = _text(body.get("operation_id")).strip() or None
    if op_key:
        existing = db.one("SELECT id FROM usage_log WHERE op_key = ?", (op_key,))
        if existing is not None:
            row = db.one("SELECT * FROM usage_log WHERE id = ?", (existing["id"],))
            return {"usage": row, "notice": "这次登记已经保存过，未重复添加", "replayed": True}

    with db.transaction() as conn:
        uid = db.next_id(conn, "USG")
        conn.execute(
            "INSERT INTO usage_log(id, assignment_id, release_id, audience_id, used_on, "
            "purpose, items, note, created_at, op_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uid, assignment_id, release_id, audience_id, used_on, purpose,
             db.dumps(items), None if body.get("note") is None else _text(body.get("note")),
             db.now(), op_key),
        )
        db.audit("usage.create", "usage_log", uid,
                 {"release_id": release_id, "audience_id": audience_id, "used_on": used_on,
                  "purpose": purpose, "assignment_id": assignment_id})
    row = db.one(
        "SELECT u.*, c.title AS collection_title, a.name AS audience_name "
        "FROM usage_log u LEFT JOIN release r ON r.id = u.release_id "
        "LEFT JOIN collection c ON c.id = r.collection_id "
        "LEFT JOIN audience a ON a.id = u.audience_id WHERE u.id = ?", (uid,))
    return {"usage": row,
            "notice": "已登记实际使用；该记录不会自动改动出题任务状态，如需标记请另行 PATCH"}


USAGE_FIELDS = ("assignment_id", "release_id", "audience_id", "used_on", "purpose",
                "items", "note")


@patch("/api/usages/{uid}")
def usage_update(req):
    uid = req.params["uid"]
    current = db.one("SELECT * FROM usage_log WHERE id = ?", (uid,))
    if current is None:
        raise ApiError(404, "not_found", f"使用记录 {uid} 不存在")
    body = req.json or {}
    unknown = [key for key in body if key not in USAGE_FIELDS]
    if unknown:
        raise ApiError(400, "bad_field", f"使用记录不接受这些字段：{'、'.join(unknown)}")
    data: dict = {}
    if "release_id" in body:
        release_id = _text(body["release_id"]).strip()
        if db.one("SELECT id FROM release WHERE id = ?", (release_id,)) is None:
            raise ApiError(404, "not_found", f"发布物 {release_id} 不存在")
        data["release_id"] = release_id
    if "audience_id" in body:
        audience_id = _text(body["audience_id"]).strip()
        if db.one("SELECT id FROM audience WHERE id = ?", (audience_id,)) is None:
            raise ApiError(404, "not_found", f"使用对象 {audience_id} 不存在")
        data["audience_id"] = audience_id
    if "assignment_id" in body:
        assignment_id = body["assignment_id"] or None
        if assignment_id and db.one("SELECT id FROM assignment WHERE id = ?",
                                     (assignment_id,)) is None:
            raise ApiError(404, "not_found", f"出题任务 {assignment_id} 不存在")
        data["assignment_id"] = assignment_id
    if "used_on" in body:
        if not _text(body["used_on"]).strip():
            raise ApiError(400, "bad_field", "used_on 不能为空")
        data["used_on"] = _check_date(body["used_on"], "used_on")
    if "purpose" in body:
        data["purpose"] = _enum(body["purpose"], USAGE_PURPOSES, "purpose")
    if "items" in body:
        items = body["items"]
        if items is not None and not isinstance(items, list):
            raise ApiError(400, "bad_field", "items 必须是条目 ID 数组")
        data["items"] = db.dumps(items)
    if "note" in body:
        data["note"] = None if body["note"] is None else _text(body["note"])
    if not data:
        raise ApiError(400, "empty_body", "没有需要修改的字段")
    effective_release = db.one('SELECT * FROM release WHERE id=?',
                               (data.get('release_id', current['release_id']),))
    effective_items = body.get('items', current.get('items'))
    _validate_usage_selection(effective_release, effective_items)
    with db.transaction() as conn:
        assignments = ", ".join(f"{key} = ?" for key in data)
        conn.execute(f"UPDATE usage_log SET {assignments} WHERE id = ?",
                     list(data.values()) + [uid])
        db.audit("usage.update", "usage_log", uid, {k: v for k, v in data.items()})
    return {"usage": db.one("SELECT * FROM usage_log WHERE id = ?", (uid,))}


# ---------------------------------------------------------------- §11 教学反馈


@get("/api/feedback")
def feedback_list(req):
    usage_id = req.arg("usage_id")
    audience_id = req.arg("audience_id")
    sql = ("SELECT f.*, u.used_on, u.purpose, u.audience_id, a.name AS audience_name, "
           "c.title AS collection_title "
           "FROM feedback f LEFT JOIN usage_log u ON u.id = f.usage_id "
           "LEFT JOIN audience a ON a.id = u.audience_id "
           "LEFT JOIN release r ON r.id = u.release_id "
           "LEFT JOIN collection c ON c.id = r.collection_id WHERE 1 = 1")
    params: list = []
    if usage_id:
        sql += " AND f.usage_id = ?"
        params.append(usage_id)
    if audience_id:
        sql += " AND u.audience_id = ?"
        params.append(audience_id)
    sql += " ORDER BY f.created_at DESC, f.id DESC"
    items = db.all_(sql, params)
    return {"items": items, "total": len(items)}


FEEDBACK_FIELDS = ("usage_id", "common_errors", "actual_minutes", "effect", "suggestion",
                   "sample_size", "stat_scope", "stat_date", "level")


def _feedback_payload(body: dict, partial: bool) -> dict:
    data: dict = {}
    if "usage_id" in body:
        usage_id = body["usage_id"] or None
        if usage_id:
            if db.one("SELECT id FROM usage_log WHERE id = ?", (usage_id,)) is None:
                raise ApiError(404, "not_found", f"使用记录 {usage_id} 不存在")
        data["usage_id"] = usage_id
    elif not partial:
        data["usage_id"] = None
    if "actual_minutes" in body:
        data["actual_minutes"] = _number(body["actual_minutes"], "actual_minutes")
    if "sample_size" in body:
        size = body["sample_size"]
        if size is None or size == "":
            data["sample_size"] = None
        else:
            try:
                data["sample_size"] = int(size)
            except (TypeError, ValueError):
                raise ApiError(400, "bad_field", "sample_size 必须是整数")
    if "stat_date" in body:
        data["stat_date"] = _check_date(body["stat_date"], "stat_date")
    for field in ("common_errors", "effect", "suggestion", "stat_scope", "level"):
        if field in body:
            data[field] = None if body[field] is None else _text(body[field])
    return data


@post("/api/feedback")
def feedback_create(req):
    body = req.json or {}
    unknown = [key for key in body if key not in FEEDBACK_FIELDS]
    if unknown:
        raise ApiError(400, "bad_field", f"反馈不接受这些字段：{'、'.join(unknown)}")
    data = _feedback_payload(body, partial=False)
    with db.transaction() as conn:
        fid = db.next_id(conn, "FB")
        columns = ["id", "created_at"] + list(data.keys())
        values = [fid, db.now()] + list(data.values())
        conn.execute(
            f"INSERT INTO feedback({', '.join(columns)}) VALUES({', '.join('?' * len(columns))})",
            values)
        db.audit("feedback.create", "feedback", fid, {"usage_id": data.get("usage_id")})
    return {"feedback": db.one("SELECT * FROM feedback WHERE id = ?", (fid,))}


@patch("/api/feedback/{fid}")
def feedback_update(req):
    fid = req.params["fid"]
    if db.one("SELECT id FROM feedback WHERE id = ?", (fid,)) is None:
        raise ApiError(404, "not_found", f"反馈 {fid} 不存在")
    body = req.json or {}
    unknown = [key for key in body if key not in FEEDBACK_FIELDS]
    if unknown:
        raise ApiError(400, "bad_field", f"反馈不接受这些字段：{'、'.join(unknown)}")
    data = _feedback_payload(body, partial=True)
    if not data:
        raise ApiError(400, "empty_body", "没有需要修改的字段")
    with db.transaction() as conn:
        assignments = ", ".join(f"{key} = ?" for key in data)
        conn.execute(f"UPDATE feedback SET {assignments} WHERE id = ?",
                     list(data.values()) + [fid])
        db.audit("feedback.update", "feedback", fid, data)
    return {"feedback": db.one("SELECT * FROM feedback WHERE id = ?", (fid,))}
