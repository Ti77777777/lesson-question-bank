"""N06 新版提醒与显式更新。

要点：只提示不自动改；换版后旧解法与小问范围不能照搬，必须清空并报告；
整批原子、带修订校验；已是最新或更旧的版本要拒绝。

运行：
    python -m unittest tests.test_version_updates -v
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
DB_PATH = ROOT / "build" / "_test_version_updates.db"

for _p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    if _p.exists():
        _p.unlink()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB_PATH)
os.environ["QBANK_AI_WORKER"] = "0"

sys.path.insert(0, str(ROOT))

from app.server import Handler, create_app  # noqa: E402


class VersionUpdateTest(unittest.TestCase):
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

    def setup_collection(self, title):
        cid = self.ok("POST", "/api/collections", {"title": title})["collection"]["id"]
        sid = self.ok("POST", "/api/collections/%s/sections" % cid,
                      {"title": "一、例题"})["section"]["id"]
        q = self.ok("POST", "/api/questions", {
            "short_name": title + " 题", "qtype": "solve",
            "stem": "求 $x+\\frac{1}{x}$ 的最小值。", "answer": "$2$",
            "subquestions": [{"key": "1", "text": "第一问", "depends_on": []},
                             {"key": "2", "text": "第二问", "depends_on": []}],
            "change_note": "初版"})
        qid = q["question"]["id"]
        v1 = q["current_version"]["id"]
        item = self.ok("POST", "/api/collections/%s/items" % cid,
                       {"question_id": qid, "section_id": sid})["item"]["id"]
        sol = self.ok("POST", "/api/versions/%s/solutions" % v1,
                      {"label": "解法一", "steps": "$x+\\frac1x\\geqslant 2$", "is_primary": 1})
        self.ok("PATCH", "/api/items/%s" % item, {"solution_id": sol["id"], "subq_keys": ["1", "2"]})
        return cid, qid, v1, item, sol["id"]

    def new_version(self, qid, note, subquestions=None):
        body = {"change_note": note, "answer": "$2$（$x=1$ 时取等）"}
        if subquestions is not None:
            body["subquestions"] = subquestions
        return self.ok("POST", "/api/questions/%s/versions" % qid, body)["current_version"]["id"]

    # ------------------------------------------------------------------
    def test_01_list_only_reports_stale_items(self):
        cid, qid, v1, item, _sol = self.setup_collection("新版提醒 A")
        before = self.ok("GET", "/api/collections/%s/version-updates" % cid)
        self.assertEqual(before["total"], 0)

        v2 = self.new_version(qid, "修正答案端点")
        after = self.ok("GET", "/api/collections/%s/version-updates" % cid)
        self.assertEqual(after["total"], 1)
        row = after["items"][0]
        self.assertEqual(row["used"]["version_id"], v1)
        self.assertEqual(row["new"]["version_id"], v2)
        self.assertEqual(row["new"]["change_note"], "修正答案端点")
        self.assertIn("指定解法", row["needs_review"])
        self.assertIn("小问范围", row["needs_review"])
        # 只提示，不改数据
        item_now = next(i for i in self.ok("GET", "/api/collections/%s" % cid)["items"]
                        if i["id"] == item)
        self.assertEqual(item_now["question_version_id"], v1)

    def test_02_apply_switches_version_and_reports_dropped_settings(self):
        cid, qid, v1, item, sol = self.setup_collection("新版提醒 B")
        # 新版本只有第 1 问，旧解法也不属于新版本
        v2 = self.new_version(qid, "删去第二问", subquestions=[
            {"key": "1", "text": "第一问", "depends_on": []}])
        out = self.ok("POST", "/api/collections/%s/version-updates/apply" % cid, {
            "operation_id": str(uuid.uuid4()),
            "base_revision": self.ok("GET", "/api/collections/%s" % cid)["revision"],
            "updates": [{"item_id": item}]})
        self.assertEqual(out["updated"], 1)
        dropped = out["results"][0]["dropped"]
        self.assertIn("指定解法", dropped)
        self.assertIn("小问范围", dropped)

        detail = self.ok("GET", "/api/collections/%s" % cid)
        row = next(i for i in detail["items"] if i["id"] == item)
        self.assertEqual(row["question_version_id"], v2)
        self.assertIsNone(row.get("solution_id"))
        self.assertEqual(row.get("subq_keys") or [], ["1"])
        # 换版后提醒消失
        self.assertEqual(self.ok("GET", "/api/collections/%s/version-updates" % cid)["total"], 0)

    def test_03_rejects_older_or_same_version(self):
        cid, qid, v1, item, _sol = self.setup_collection("新版提醒 C")
        v2 = self.new_version(qid, "第二版")
        self.ok("POST", "/api/collections/%s/version-updates/apply" % cid, {
            "operation_id": str(uuid.uuid4()),
            "base_revision": self.ok("GET", "/api/collections/%s" % cid)["revision"],
            "updates": [{"item_id": item}]})
        status, err = self.call("POST", "/api/collections/%s/version-updates/apply" % cid, {
            "operation_id": str(uuid.uuid4()),
            "base_revision": self.ok("GET", "/api/collections/%s" % cid)["revision"],
            "updates": [{"item_id": item, "question_version_id": v1}]})
        self.assertEqual(status, 200)          # 单条失败不拖垮整批
        self.assertEqual(err["results"][0]["code"], "not_newer")
        self.assertEqual(err["updated"], 0)

    def test_04_stale_revision_blocks_the_whole_batch(self):
        cid, qid, _v1, item, _sol = self.setup_collection("新版提醒 D")
        self.new_version(qid, "第二版")
        stale = self.ok("GET", "/api/collections/%s" % cid)["revision"]
        # 另一个页面先改了一次
        self.ok("POST", "/api/collections/%s/sections" % cid, {"title": "二、跟练"})
        status, err = self.call("POST", "/api/collections/%s/version-updates/apply" % cid, {
            "operation_id": str(uuid.uuid4()), "base_revision": stale,
            "updates": [{"item_id": item}]})
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "revision_conflict")

    def test_05_replay_is_idempotent(self):
        cid, qid, _v1, item, _sol = self.setup_collection("新版提醒 E")
        self.new_version(qid, "第二版")
        body = {"operation_id": str(uuid.uuid4()),
                "base_revision": self.ok("GET", "/api/collections/%s" % cid)["revision"],
                "updates": [{"item_id": item}]}
        first = self.ok("POST", "/api/collections/%s/version-updates/apply" % cid, body)
        again = self.ok("POST", "/api/collections/%s/version-updates/apply" % cid, body)
        self.assertTrue(again.get("replayed"))
        self.assertEqual(again["updated"], first["updated"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
