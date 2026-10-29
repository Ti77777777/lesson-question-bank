"""SQLite 存取层：连接、编号、事务、审计。

本地使用，采用「每线程一个连接」+ WAL；教师归属不代表访问权限。
"""
from __future__ import annotations

import json
import os
import uuid
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
# 允许用 QBANK_DB 指向另一份库，便于演示、测试与恢复后核对
DB_PATH = Path(os.environ.get("QBANK_DB") or (DATA_DIR / "qbank.db"))
SCHEMA_PATH = DATA_DIR / "schema.sql"

TZ = timezone(timedelta(hours=8))

_local = threading.local()


def now() -> str:
    """带时区的当前时间，形如 2026-10-27T00:30:00+08:00。"""
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


def close() -> None:
    """释放当前请求线程的连接，避免线程结束后等垃圾回收才关闭数据库。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        del _local.conn


@contextmanager
def transaction():
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


SCHEMA_VERSION = "1"


def _ensure_column(table: str, column: str, decl: str) -> None:
    """给已有库补列。CREATE TABLE IF NOT EXISTS 不会改旧表结构。"""
    cols = {row[1] for row in db().execute("PRAGMA table_info(%s)" % table)}
    if column not in cols:
        db().execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))


def init_db() -> None:
    conn = db()
    with SCHEMA_PATH.open("r", encoding="utf-8") as fh:
        conn.executescript(fh.read())
    _ensure_column("collection", "layout_options", "TEXT")
    _ensure_column("release", "answer_path", "TEXT")
    _ensure_column("release", "pages_answer", "INTEGER")
    _ensure_column("source", "exam_type", "TEXT")
    _ensure_column("source", "grade", "TEXT")
    _ensure_column("source", "school", "TEXT")
    _ensure_column("source", "year", "TEXT")
    # N05：快捷登记使用带操作编号，重试不重复写记录
    _ensure_column("usage_log", "op_key", "TEXT")
    db().execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_usage_op ON usage_log(op_key) "
                 "WHERE op_key IS NOT NULL")
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('created_at', ?) ON CONFLICT(key) DO NOTHING",
        (now(),),
    )
    conn.commit()


# ------------------------------------------------------------------ 编号
PREFIX_WIDTH = {
    "Q": 6, "QV": 6, "SRC": 6, "SF": 6, "OCC": 6, "SOL": 6, "TAG": 6,
    "REL": 6, "AST": 6, "REV": 6, "COL": 6, "SEC": 6, "ITM": 6, "RLS": 6,
    "AUD": 6, "ASG": 6, "USG": 6, "FB": 6, "INB": 6, "IMP": 6, "LOG": 8,
}


def next_id(conn: sqlite3.Connection, prefix: str) -> str:
    """分配稳定编号，如 Q000001。不编码章节、难度或年份。"""
    width = PREFIX_WIDTH.get(prefix, 6)
    row = conn.execute("SELECT next_val FROM id_seq WHERE prefix = ?", (prefix,)).fetchone()
    if row is None:
        n = 1
        conn.execute("INSERT INTO id_seq(prefix, next_val) VALUES(?, ?)", (prefix, n + 1))
    else:
        n = row["next_val"]
        conn.execute("UPDATE id_seq SET next_val = ? WHERE prefix = ?", (n + 1, prefix))
    return f"{prefix}{n:0{width}d}"


# ------------------------------------------------------------------ 行转换
JSON_COLUMNS = {
    "options", "answer_keys", "subquestions", "material_refs", "teaching",
    "layout_options", "subq_keys", "items", "snapshot", "checks", "mapping", "stats", "errors",
}


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out: dict = {}
    for key in row.keys():
        value = row[key]
        if key in JSON_COLUMNS and isinstance(value, str):
            try:
                out[key] = json.loads(value)
                continue
            except (ValueError, TypeError):
                out[key] = value
                continue
        out[key] = value
    return out


def rows_to_list(rows) -> list[dict]:
    return [row_to_dict(r) for r in rows]


def dumps(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def one(sql: str, params=()) -> dict | None:
    return row_to_dict(db().execute(sql, params).fetchone())


def all_(sql: str, params=()) -> list[dict]:
    return rows_to_list(db().execute(sql, params).fetchall())


def scalar(sql: str, params=()):
    row = db().execute(sql, params).fetchone()
    return None if row is None else row[0]


# ------------------------------------------------------------------ 审计
def audit(action: str, target_type: str | None = None,
          target_id: str | None = None, detail=None, actor: str = "教师") -> None:
    conn = db()
    conn.execute(
        "INSERT INTO audit_log(id, at, actor, action, target_type, target_id, detail) "
        "VALUES(?,?,?,?,?,?,?)",
        (next_id(conn, "LOG"), now(), actor, action, target_type, target_id,
         detail if isinstance(detail, str) or detail is None else dumps(detail)),
    )


# ------------------------------------------------------------------ 备份
def backup(target_dir: Path | None = None) -> Path:
    """使用 sqlite3 在线备份 API，保证一致性。"""
    target_dir = target_dir or store_dir('backup')
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S-%f")
    target = target_dir / f"{DB_PATH.stem}-{stamp}-{uuid.uuid4().hex[:8]}.db"
    dest = sqlite3.connect(target)
    try:
        db().backup(dest)
    finally:
        dest.close()
    return target


def rel_to_root(path: Path | str) -> str:
    """转为相对项目根的 POSIX 路径，避免绑定本机绝对路径。"""
    p = Path(path).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def abs_path(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else (ROOT / rel)


def store_dir(base: str) -> Path:
    """文件落盘目录。

    正式库 qbank.db 直接用 base/（如 releases/RLS000001），与大纲的目录约定一致；
    演示库、测试库、恢复件则落到同名子目录（如 releases/demo/RLS000001），
    否则不同库的发布物编号相同，会互相覆盖，归档就不可信了。
    """
    root = ROOT / base
    stem = DB_PATH.stem
    return root if stem == "qbank" else root / stem
