"""N07 换一道：原位替换，身份与位置保留，题目专属设置不照搬。

运行：
    python -m unittest tests.test_item_swap -v
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
DB_PATH = ROOT / "build" / "_test_item_swap.db"

for _p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    if _p.exists():
        _p.unlink()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB_PATH)
os.environ["QBANK_AI_WORKER"] = "0"
sys.path.insert(0, str(ROOT))

from app.server import Handler, create_app  # noqa: E402


class SwapTest(unittest.TestCase):
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

    def call(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def ok(self, method, path, body=None):
        status, payload = self.call(method, path, body)
        self.assertLess(status, 400, "%s %s → %s %s" % (method, path, status, payload))
        return payload

    def new_question(self, name):
        body = self.ok("POST", "/api/questions", {
            "short_name": name, "qtype": "solve",
            "stem": "%s：求 $x+\\frac{1}{x}$ 的最小值。" % name, "answer": "$2$",
            "subquestions": [{"key": "1", "text": "第一问", "depends_on": []}],
            "change_note": "换题用例"})
        return body["question"]["id"], body["current_version"]["id"]

    def fixture(self, title):
        cid = self.ok("POST", "/api/collections", {"title": title})["collection"]["id"]
        sid = self.ok("POST", "/api/collections/%s/sections" % cid, {"title": "一、例题"})["section"]["id"]
        q1, v1 = self.new_question(title + " 原题")
        q2, v2 = self.new_question(title + " 新题")
        item = self.ok("POST", "/api/collections/%s/items" % cid, {
            "question_id": q1, "section_id": sid, "role": "practice",
            "space_mm": 35, "hint_level": "keyword", "page_break_before": 1})["item"]["id"]
        return cid, sid, q1, v1, q2, v2, item

    def test_01_swap_keeps_identity_and_clears_item_settings(self):
        cid, sid, q1, v1, q2, v2, item = self.fixture("换题 A")
        base = self.ok("GET", "/api/collections/%s" % cid)["revision"]
        out = self.ok("POST", "/api/items/%s/swap" % item, {
            "operation_id": str(uuid.uuid4()), "base_revision": base, "question_id": q2})
        self.assertEqual(out["item_id"], item)
        self.assertEqual(out["question_id"], q2)
        for label in ("提示级别", "逐题留白", "题前换页"):
            self.assertIn(label, out["dropped"])

        row = next(i for i in self.ok("GET", "/api/collections/%s" % cid)["items"] if i["id"] == item)
        self.assertEqual(row["question_id"], q2)
        self.assertEqual(row["question_version_id"], v2)
        self.assertEqual(row["section_id"], sid)          # 原位
        self.assertEqual(row["role"], "practice")          # 角色保留
        self.assertIsNone(row.get("space_mm"))            # 题目专属设置清空
        self.assertEqual(row.get("hint_level"), "none")
        self.assertEqual(row.get("page_break_before"), 0)
        self.assertEqual(out["previous"]["question_id"], q1)

    def test_02_rejects_same_question_and_bad_version(self):
        cid, sid, q1, v1, q2, v2, item = self.fixture("换题 B")
        rev = lambda: self.ok("GET", "/api/collections/%s" % cid)["revision"]
        status, err = self.call("POST", "/api/items/%s/swap" % item, {
            "operation_id": str(uuid.uuid4()), "base_revision": rev(), "question_id": q1})
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "same_question")

        status, err = self.call("POST", "/api/items/%s/swap" % item, {
            "operation_id": str(uuid.uuid4()), "base_revision": rev(),
            "question_id": q2, "question_version_id": v1})
        self.assertEqual(status, 400)
        self.assertEqual(err["error"]["code"], "version_mismatch")

    def test_03_stale_revision_blocks_swap(self):
        cid, sid, q1, v1, q2, v2, item = self.fixture("换题 C")
        stale = self.ok("GET", "/api/collections/%s" % cid)["revision"]
        self.ok("POST", "/api/collections/%s/sections" % cid, {"title": "二、跟练"})
        status, err = self.call("POST", "/api/items/%s/swap" % item, {
            "operation_id": str(uuid.uuid4()), "base_revision": stale, "question_id": q2})
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "revision_conflict")
        row = next(i for i in self.ok("GET", "/api/collections/%s" % cid)["items"] if i["id"] == item)
        self.assertEqual(row["question_id"], q1)

    def test_05_cannot_swap_into_a_question_already_in_the_collection(self):
        cid, sid, q1, v1, q2, v2, item = self.fixture("换题 E")
        # 先正常把另一条加到题单里（用当前条目的题号之外的题）
        second = self.ok("POST", "/api/collections/%s/items" % cid, {"question_id": q2})["item"]["id"]
        self.assertTrue(second)
        rev = self.ok("GET", "/api/collections/%s" % cid)["revision"]
        status, err = self.call("POST", "/api/items/%s/swap" % item, {
            "operation_id": str(uuid.uuid4()), "base_revision": rev, "question_id": q2})
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "duplicate")
        # 明确要求放两次才放行
        rev = self.ok("GET", "/api/collections/%s" % cid)["revision"]
        out = self.ok("POST", "/api/items/%s/swap" % item, {
            "operation_id": str(uuid.uuid4()), "base_revision": rev,
            "question_id": q2, "allow_duplicate": True})
        self.assertEqual(out["question_id"], q2)

    def test_04_replay_is_idempotent(self):
        cid, sid, q1, v1, q2, v2, item = self.fixture("换题 D")
        body = {"operation_id": str(uuid.uuid4()),
                "base_revision": self.ok("GET", "/api/collections/%s" % cid)["revision"],
                "question_id": q2}
        first = self.ok("POST", "/api/items/%s/swap" % item, body)
        again = self.ok("POST", "/api/items/%s/swap" % item, body)
        self.assertTrue(again.get("replayed"))
        self.assertEqual(again["question_id"], first["question_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
