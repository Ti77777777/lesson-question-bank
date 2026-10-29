"""N01 连续选题：批量加入题目的服务端行为。

对应《新功能设计与实施规划》N01 的四条验收：
A 一次向指定分节加入多题；B 同一操作编号重试不重复加入；
C 部分无效项逐项报告、基准修订过期整批不执行；D 换一个库不沿用另一个库的题单。

运行：
    python -m unittest tests.test_collection_batch -v
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
DB_PATH = ROOT / "build" / "_test_collection_batch.db"

for _p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    if _p.exists():
        _p.unlink()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB_PATH)
os.environ["QBANK_AI_WORKER"] = "0"

sys.path.insert(0, str(ROOT))

from app.server import Handler, create_app  # noqa: E402

S: dict = {}


class BatchAddTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # ---------------------------------------------------------- 工具
    def call(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8") or "{}"
            return exc.code, json.loads(raw)

    def ok(self, method, path, body=None):
        status, payload = self.call(method, path, body)
        self.assertLess(status, 400, "%s %s → %s %s" % (method, path, status, payload))
        return payload

    def new_question(self, name):
        body = self.ok("POST", "/api/questions", {
            "short_name": name, "qtype": "solve",
            "stem": "%s：已知 $x>0$，求 $x+\\frac{1}{x}$ 的最小值。" % name,
            "answer": "$2$", "change_note": "批量加入用例"})
        return body["question"]["id"]

    def revision(self, cid):
        return self.ok("GET", "/api/collections/%s" % cid)["revision"]

    # ---------------------------------------------------------- 用例
    def test_01_batch_add_and_replay(self):
        cid = self.ok("POST", "/api/collections", {"title": "批量加入 A"})["collection"]["id"]
        sid = self.ok("POST", "/api/collections/%s/sections" % cid,
                      {"title": "一、例题", "order_no": 0})["section"]["id"]
        q1, q2, q3 = (self.new_question("批加入题 %d" % i) for i in (1, 2, 3))
        base = self.revision(cid)
        op = str(uuid.uuid4())

        payload = {"operation_id": op, "base_revision": base, "section_id": sid,
                   "items": [{"question_id": q1}, {"question_id": q2}, {"question_id": q3}]}
        first = self.ok("POST", "/api/collections/%s/items/batch" % cid, payload)
        self.assertEqual(first["added"], 3)
        self.assertFalse(first["replayed"])
        self.assertTrue(all(r["ok"] for r in first["results"]))
        self.assertEqual(len({r["item_id"] for r in first["results"]}), 3)

        detail = self.ok("GET", "/api/collections/%s" % cid)
        self.assertEqual(len(detail["items"]), 3)
        self.assertTrue(all(item["section_id"] == sid for item in detail["items"]))
        self.assertEqual([item["order_no"] for item in detail["items"]], [0, 1, 2])
        self.assertNotEqual(detail["revision"], base)

        # B：同一操作编号重发，返回原结果且不重复写入
        again = self.ok("POST", "/api/collections/%s/items/batch" % cid, payload)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["added"], 3)
        self.assertEqual(len(self.ok("GET", "/api/collections/%s" % cid)["items"]), 3)

        # 同一编号换了输入必须拒绝
        changed = dict(payload, items=[{"question_id": q1}])
        status, err = self.call("POST", "/api/collections/%s/items/batch" % cid, changed)
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "operation_conflict")

    def test_02_partial_invalid_reports_each_item(self):
        cid = self.ok("POST", "/api/collections", {"title": "批量加入 B"})["collection"]["id"]
        q1 = self.new_question("部分无效题 1")
        q2 = self.new_question("部分无效题 2")
        base = self.revision(cid)
        body = {"operation_id": str(uuid.uuid4()), "base_revision": base,
                "items": [{"question_id": q1},
                          {"question_id": "Q999999"},
                          {"question_id": q2}]}
        out = self.ok("POST", "/api/collections/%s/items/batch" % cid, body)
        self.assertEqual(out["added"], 2)
        codes = [r.get("code") for r in out["results"]]
        self.assertIn("not_found", codes)
        self.assertEqual(sum(1 for r in out["results"] if r["ok"]), 2)
        self.assertEqual(len(self.ok("GET", "/api/collections/%s" % cid)["items"]), 2)

    def test_03_stale_revision_writes_nothing(self):
        cid = self.ok("POST", "/api/collections", {"title": "批量加入 C"})["collection"]["id"]
        q1 = self.new_question("并发题 1")
        q2 = self.new_question("并发题 2")
        base = self.revision(cid)
        # 另一个页面先写入一次，使基准过期
        self.ok("POST", "/api/collections/%s/items" % cid, {"question_id": q1})
        status, err = self.call("POST", "/api/collections/%s/items/batch" % cid, {
            "operation_id": str(uuid.uuid4()), "base_revision": base,
            "items": [{"question_id": q2}]})
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "revision_conflict")
        self.assertIn("revision", err["error"].get("detail") or {})
        # 整批不执行：题单里只有先前那一题
        self.assertEqual(len(self.ok("GET", "/api/collections/%s" % cid)["items"]), 1)

    def test_04_duplicate_rules(self):
        cid = self.ok("POST", "/api/collections", {"title": "批量加入 D"})["collection"]["id"]
        q1 = self.new_question("重复题 1")
        base = self.revision(cid)
        self.ok("POST", "/api/collections/%s/items" % cid, {"question_id": q1})
        base = self.revision(cid)

        out = self.ok("POST", "/api/collections/%s/items/batch" % cid, {
            "operation_id": str(uuid.uuid4()), "base_revision": base,
            "items": [{"question_id": q1}]})
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["results"][0]["code"], "duplicate")

        # 明确要求再次加入才允许重复
        base = self.revision(cid)
        out = self.ok("POST", "/api/collections/%s/items/batch" % cid, {
            "operation_id": str(uuid.uuid4()), "base_revision": base,
            "items": [{"question_id": q1, "allow_duplicate": True}]})
        self.assertEqual(out["added"], 1)
        self.assertEqual(len(self.ok("GET", "/api/collections/%s" % cid)["items"]), 2)

        # 同一批里重复选同一题：默认拒第二个
        q2 = self.new_question("重复题 2")
        base = self.revision(cid)
        out = self.ok("POST", "/api/collections/%s/items/batch" % cid, {
            "operation_id": str(uuid.uuid4()), "base_revision": base,
            "items": [{"question_id": q2}, {"question_id": q2}]})
        self.assertEqual(out["added"], 1)
        self.assertEqual(out["results"][1]["code"], "duplicate_in_batch")

    def test_05_section_must_belong_to_collection(self):
        c1 = self.ok("POST", "/api/collections", {"title": "分节归属 1"})["collection"]["id"]
        c2 = self.ok("POST", "/api/collections", {"title": "分节归属 2"})["collection"]["id"]
        sid2 = self.ok("POST", "/api/collections/%s/sections" % c2, {"title": "别家的分节"})["section"]["id"]
        q1 = self.new_question("跨题单分节题")
        status, err = self.call("POST", "/api/collections/%s/items/batch" % c1, {
            "operation_id": str(uuid.uuid4()), "base_revision": self.revision(c1),
            "section_id": sid2, "items": [{"question_id": q1}]})
        self.assertGreaterEqual(status, 400)
        self.assertEqual(len(self.ok("GET", "/api/collections/%s" % c1)["items"]), 0)

    def test_06_revision_is_deterministic(self):
        cid = self.ok("POST", "/api/collections", {"title": "修订稳定性"})["collection"]["id"]
        first = self.revision(cid)
        self.assertTrue(first.startswith("cr1:"))
        self.assertEqual(first, self.revision(cid), "没有写入时修订不应变化")
        q1 = self.new_question("修订变化题")
        self.ok("POST", "/api/collections/%s/items" % cid, {"question_id": q1})
        self.assertNotEqual(first, self.revision(cid))

    def test_07_batch_rejects_bad_shape(self):
        cid = self.ok("POST", "/api/collections", {"title": "参数校验"})["collection"]["id"]
        for body in ({"operation_id": str(uuid.uuid4()), "items": []},
                     {"items": [{"question_id": "Q000001"}]}):
            status, _err = self.call("POST", "/api/collections/%s/items/batch" % cid, body)
            self.assertGreaterEqual(status, 400, body)

        # 单条不合格不拖垮整批：请求成功，结果里逐项说明
        out = self.ok("POST", "/api/collections/%s/items/batch" % cid,
                      {"operation_id": str(uuid.uuid4()), "items": [{}]})
        self.assertEqual(out["added"], 0)
        self.assertEqual(out["results"][0]["code"], "missing_field")


if __name__ == "__main__":
    unittest.main(verbosity=2)
