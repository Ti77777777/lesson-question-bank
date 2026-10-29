"""N09 逐题反馈：只认这次实际用过的条目，同一题不因重复提交而重复建档。"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "build" / "_test_item_feedback.db"
for _p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    if _p.exists():
        _p.unlink()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB_PATH)
os.environ["QBANK_AI_WORKER"] = "0"
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.server import Handler, create_app  # noqa: E402


class ItemFeedbackTest(unittest.TestCase):
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
            conn.execute("INSERT INTO collection(id,title,status,created_at,updated_at) "
                         "VALUES('COL000001','逐题反馈','draft','x','x')")
            conn.execute("INSERT INTO audience(id,kind,name,created_at) "
                         "VALUES('AUD000001','class','高一(1)班','x')")
            snapshot = {"collection": {"id": "COL000001", "title": "逐题反馈"},
                        "items": [{"id": "ITM000001", "question_id": "Q000001",
                                   "question_version_id": "QV000001", "order_no": 0},
                                  {"id": "ITM000002", "question_id": "Q000002",
                                   "question_version_id": "QV000002", "order_no": 1}],
                        "question_versions": {}}
            conn.execute("INSERT INTO release(id,collection_id,snapshot,template,status,created_at) "
                         "VALUES('RLS000001','COL000001',?,'exbook','built','x')",
                         (json.dumps(snapshot),))
            # 这次只登记了第 1 题
            conn.execute("INSERT INTO usage_log(id,release_id,audience_id,used_on,purpose,items,created_at) "
                         "VALUES('USG000001','RLS000001','AUD000001','2026-10-30','class',?, 'x')",
                         (json.dumps(["ITM000001"]),))

    def call(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_01_scope_only_contains_used_items(self):
        status, data = self.call("GET", "/api/usages/USG000001/item-feedback")
        self.assertEqual(status, 200)
        self.assertEqual([row["item_id"] for row in data["scope"]], ["ITM000001"])

    def test_02_cannot_report_an_unused_item(self):
        status, err = self.call("POST", "/api/usages/USG000001/item-feedback",
                                {"item_id": "ITM000002", "error_note": "没讲过"})
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "not_in_use_scope")

    def test_03_note_required_and_saved(self):
        status, err = self.call("POST", "/api/usages/USG000001/item-feedback", {"item_id": "ITM000001"})
        self.assertEqual(status, 400)
        status, out = self.call("POST", "/api/usages/USG000001/item-feedback",
                                {"item_id": "ITM000001", "error_note": "忽略定义域",
                                 "sample_scope": "全班 42 人", "suggestion": "先画区间"})
        self.assertEqual(status, 200)
        self.assertEqual(out["feedback"]["error_note"], "忽略定义域")

    def test_04_same_item_updates_instead_of_duplicating(self):
        self.call("POST", "/api/usages/USG000001/item-feedback",
                  {"item_id": "ITM000001", "error_note": "第一次"})
        self.call("POST", "/api/usages/USG000001/item-feedback",
                  {"item_id": "ITM000001", "error_note": "补充说明"})
        status, data = self.call("GET", "/api/usages/USG000001/item-feedback")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["error_note"], "补充说明")


if __name__ == "__main__":
    unittest.main(verbosity=2)
