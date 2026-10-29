"""N05 从讲义登记使用：幂等、失败讲义拒绝、同日多次成立的边界。

运行：
    python -m unittest tests.test_register_usage -v
"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "build" / "_test_register_usage.db"

for _p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    if _p.exists():
        _p.unlink()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB_PATH)
os.environ["QBANK_AI_WORKER"] = "0"

sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.server import Handler, create_app  # noqa: E402


class RegisterUsageTest(unittest.TestCase):
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

    @classmethod
    def seed(cls):
        with db.transaction() as conn:
            conn.execute("INSERT INTO collection(id, title, status, created_at, updated_at) "
                         "VALUES('COL000001','快捷登记','draft','x','x')")
            for qid in ("Q000001", "Q000002"):
                conn.execute("INSERT INTO question(id, short_name, status, created_at, updated_at) "
                             "VALUES(?,?,'active','x','x')", (qid, qid))
                conn.execute("INSERT INTO question_version(id, question_id, version_no, qtype, stem, "
                             "created_at) VALUES(?,?,1,'solve','题干','x')",
                             ("QV" + qid[1:], qid))
                conn.execute("UPDATE question SET current_version_id=? WHERE id=?",
                             ("QV" + qid[1:], qid))
            conn.execute("INSERT INTO audience(id, kind, name, created_at) "
                         "VALUES('AUD000001','class','高一(1)班','x')")
            snapshot = {
                "collection": {"id": "COL000001", "title": "快捷登记"},
                "items": [{"id": "ITM000001", "question_id": "Q000001", "question_version_id": "QV000001"},
                          {"id": "ITM000002", "question_id": "Q000002", "question_version_id": "QV000002"}],
                "question_versions": {
                    "QV000001": {"version": {"id": "QV000001"}, "question": {"short_name": "Q1", "stem": "题干"}},
                    "QV000002": {"version": {"id": "QV000002"}, "question": {"short_name": "Q2", "stem": "题干"}},
                },
            }
            for rid, status in (("RLS000001", "built"), ("RLS000002", "failed")):
                conn.execute(
                    "INSERT INTO release(id, collection_id, snapshot, template, status, created_at) "
                    "VALUES(?, 'COL000001', ?, 'exbook', ?, 'x')",
                    (rid, json.dumps(snapshot), status))

    def call(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def body(self, op_id, release_id="RLS000001", items=("ITM000001",)):
        return {"operation_id": op_id, "release_id": release_id,
                "audience_id": "AUD000001", "used_on": "2026-10-29",
                "purpose": "class", "items": list(items)}

    def test_01_same_operation_id_writes_once(self):
        op = str(uuid.uuid4())
        status, first = self.call("POST", "/api/usages", self.body(op))
        self.assertEqual(status, 200, first)
        status, again = self.call("POST", "/api/usages", self.body(op))
        self.assertEqual(status, 200)
        self.assertTrue(again.get("replayed"))
        self.assertEqual(again["usage"]["id"], first["usage"]["id"])
        count = db.scalar("SELECT COUNT(*) FROM usage_log WHERE release_id='RLS000001'")
        self.assertEqual(count, 1)

    def test_02_a_second_real_use_same_day_is_allowed(self):
        op = str(uuid.uuid4())
        status, _ = self.call("POST", "/api/usages", self.body(op, items=("ITM000001",)))
        self.assertEqual(status, 200)
        status, out = self.call("POST", "/api/usages", self.body(str(uuid.uuid4()), items=("ITM000002",)))
        self.assertEqual(status, 200)
        self.assertFalse(out.get("replayed"))
        self.assertGreaterEqual(db.scalar("SELECT COUNT(*) FROM usage_log WHERE release_id='RLS000001'"), 2)

    def test_03_failed_release_cannot_register(self):
        status, err = self.call("POST", "/api/usages", self.body(str(uuid.uuid4()), release_id="RLS000002"))
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "failed_release")

    def test_04_item_must_belong_to_snapshot(self):
        status, err = self.call("POST", "/api/usages",
                                self.body(str(uuid.uuid4()), items=("ITM999999",)))
        self.assertEqual(status, 400)
        self.assertEqual(err["error"]["code"], "bad_items")


if __name__ == "__main__":
    unittest.main(verbosity=2)
