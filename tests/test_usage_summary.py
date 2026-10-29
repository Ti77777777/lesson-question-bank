"""N04 按教学对象查已用：/api/usage-summary 的口径。

关键口径（与检索「仅看未用过」一致）：
- 只认实际使用记录，计划使用与导出不算；
- 只登记了部分题目时只统计那部分；
- 同一题多次使用取最近一次；
- 没有记录就是没有记录，不推断成「没见过」。

测试数据直接写库构造发布快照，避免为了造历史去跑一次 TeX 编译。

运行：
    python -m unittest tests.test_usage_summary -v
"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "build" / "_test_usage_summary.db"

for _p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    if _p.exists():
        _p.unlink()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB_PATH)
os.environ["QBANK_AI_WORKER"] = "0"

sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.server import Handler, create_app  # noqa: E402


def iso(days_ago: int) -> str:
    return (datetime.now(db.TZ).date() - timedelta(days=days_ago)).isoformat()


class UsageSummaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.seed()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # ---------------------------------------------------------- 造数据
    @classmethod
    def seed(cls):
        with db.transaction() as conn:
            for qid in ("Q000001", "Q000002", "Q000003"):
                # 先建题再指向版本：current_version_id 是外键，顺序反了会失败
                conn.execute(
                    "INSERT INTO question(id, short_name, status, created_at, updated_at) "
                    "VALUES(?,?,'active','x','x')", (qid, qid))
                conn.execute(
                    "INSERT INTO question_version(id, question_id, version_no, qtype, stem, "
                    "created_at) VALUES(?,?,1,'solve','题干','x')",
                    ("QV" + qid[1:], qid))
                conn.execute("UPDATE question SET current_version_id = ? WHERE id = ?",
                             ("QV" + qid[1:], qid))
            for aid, name in (("AUD000001", "高一(1)班"), ("AUD000002", "高一(2)班")):
                conn.execute(
                    "INSERT INTO audience(id, kind, name, created_at) VALUES(?,?,?,'x')",
                    (aid, "class", name))

            conn.execute(
                "INSERT INTO collection(id, title, status, created_at, updated_at) "
                "VALUES('COL000001','函数专题','draft','x','x')")
            items = [{"id": "ITM000001", "question_id": "Q000001", "question_version_id": "QV000001"},
                     {"id": "ITM000002", "question_id": "Q000002", "question_version_id": "QV000002"}]
            snapshot = {
                "collection": {"id": "COL000001", "title": "函数专题"},
                "items": items,
                "question_versions": {
                    "QV000001": {"version": {"id": "QV000001"}, "question": {"short_name": "Q000001", "stem": "题干"}},
                    "QV000002": {"version": {"id": "QV000002"}, "question": {"short_name": "Q000002", "stem": "题干"}},
                },
            }
            conn.execute(
                "INSERT INTO release(id, collection_id, snapshot, template, status, created_at) "
                "VALUES('RLS000001','COL000001',?,'exbook','built','x')", (json.dumps(snapshot),))

            # 一班：40 天前用过第 1 题（含两题），昨天只用第 2 题
            conn.execute(
                "INSERT INTO usage_log(id, release_id, audience_id, used_on, purpose, items, created_at) "
                "VALUES('USG000001','RLS000001','AUD000001',?,'class',?,'x')",
                (iso(40), json.dumps(["ITM000001", "ITM000002"])))
            conn.execute(
                "INSERT INTO usage_log(id, release_id, audience_id, used_on, purpose, items, created_at) "
                "VALUES('USG000002','RLS000001','AUD000001',?,'class',?,'x')",
                (iso(1), json.dumps(["ITM000002"])))
            # 二班：昨天只用第 1 题
            conn.execute(
                "INSERT INTO usage_log(id, release_id, audience_id, used_on, purpose, items, created_at) "
                "VALUES('USG000003','RLS000001','AUD000002',?,'class',?,'x')",
                (iso(1), json.dumps(["ITM000001"])))

    # ---------------------------------------------------------- 工具
    def get(self, path, **query):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    # ---------------------------------------------------------- 用例
    def test_01_only_actual_usage_counts_and_latest_wins(self):
        status, data = self.get("/api/usage-summary", audience_id="AUD000001", days=60)
        self.assertEqual(status, 200)
        items = data["items"]
        self.assertEqual(set(items), {"Q000001", "Q000002"})
        # 第 2 题最近一次是昨天，覆盖 40 天前那次
        self.assertEqual(items["Q000002"]["used_on"], iso(1))
        self.assertEqual(items["Q000001"]["used_on"], iso(40))

    def test_02_window_excludes_old_records(self):
        status, data = self.get("/api/usage-summary", audience_id="AUD000001", days=7)
        self.assertEqual(status, 200)
        self.assertEqual(set(data["items"]), {"Q000002"})

    def test_03_partial_selection_only_counts_selected_items(self):
        # 40 天前那次登记了两题，但 7 天窗口内只有第 2 题被登记
        status, data = self.get("/api/usage-summary", audience_id="AUD000001", days=7,
                                question_ids="Q000001,Q000002")
        self.assertEqual(status, 200)
        self.assertNotIn("Q000001", data["items"])

    def test_04_audiences_do_not_leak(self):
        status, other = self.get("/api/usage-summary", audience_id="AUD000002", days=7)
        self.assertEqual(set(other["items"]), {"Q000001"})
        status, first = self.get("/api/usage-summary", audience_id="AUD000001", days=7)
        self.assertNotEqual(set(first["items"]), set(other["items"]))

    def test_05_question_filter_limits_scope(self):
        status, data = self.get("/api/usage-summary", audience_id="AUD000001", days=60,
                                question_ids="Q000002")
        self.assertEqual(status, 200)
        self.assertEqual(set(data["items"]), {"Q000002"})
        self.assertEqual(data["checked"], 1)

    def test_06_unknown_or_missing_audience_rejected(self):
        status, err = self.get("/api/usage-summary", audience_id="AUD999999", days=30)
        self.assertEqual(status, 404)
        status, err = self.get("/api/usage-summary", days=30)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
