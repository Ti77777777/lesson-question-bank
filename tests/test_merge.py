"""题目合并回归测试。

背景：合并接口的「关系去重」曾写成
    DELETE FROM relation WHERE id NOT IN (SELECT MIN(id) … WHERE from_id=? OR to_id=?)
外层没有限定范围，于是全库所有与合并目标无关的关系一起被删。
原有测试只覆盖了标签合并，题目合并这条路径没有用例，所以一直没被发现。

本测试固定三件事：
1. 与合并无关的关系必须原样保留；
2. 合并目标上的重复关系（同方向、同类型）去重成一条；
3. 合并双方之间的两条互指关系被清掉（否则会变成自指）。

运行：
    python tests/test_merge.py -v
"""
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
DB_PATH = ROOT / "build" / "_test_merge.db"

for _p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    if _p.exists():
        _p.unlink()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB_PATH)

sys.path.insert(0, str(ROOT))

from app.server import Handler, create_app  # noqa: E402
from app import db  # noqa: E402

S: dict = {}


class MergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_app()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # -------------------------------------------------- 工具
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
        self.assertLess(status, 400, f"{method} {path} → {status} {payload}")
        return payload

    def new_question(self, name):
        body = self.ok("POST", "/api/questions", {
            "short_name": name, "qtype": "solve",
            "stem": f"{name}：已知 $x>0$，求最小值。", "answer": "$2$",
            "change_note": "回归测试"})
        return body["question"]["id"]

    # -------------------------------------------------- 用例
    def test_merge_keeps_unrelated_relations(self):
        a = self.new_question("合并目标 A")
        b = self.new_question("被合并的 B")
        c = self.new_question("无关题 C")
        d = self.new_question("无关题 D")
        e = self.new_question("无关题 E")

        # A→B（关系按无向去重，不能再建一条 B→A）
        self.ok("POST", "/api/relations", {"from_id": a, "to_id": b,
                                           "rel_type": "cond_variant", "note": "test"})
        # B→C 与 A→C：合并后应并成一条 A→C
        self.ok("POST", "/api/relations", {"from_id": b, "to_id": c,
                                           "rel_type": "same_method", "note": "test"})
        self.ok("POST", "/api/relations", {"from_id": a, "to_id": c,
                                           "rel_type": "same_method", "note": "test"})
        # C→D、D→E 与合并完全无关，必须一条不少
        self.ok("POST", "/api/relations", {"from_id": c, "to_id": d,
                                           "rel_type": "param", "note": "test"})
        self.ok("POST", "/api/relations", {"from_id": d, "to_id": e,
                                           "rel_type": "inverse", "note": "test"})

        before = db.scalar("SELECT COUNT(*) FROM relation")
        self.assertGreaterEqual(before, 5)

        self.ok("POST", f"/api/questions/{b}/merge", {"into": a, "note": "合并重复题"})

        rows = db.all_("SELECT from_id, to_id, rel_type FROM relation")
        pairs = {(r["from_id"], r["to_id"], r["rel_type"]) for r in rows}

        # 1. 无关关系必须还在
        self.assertIn((c, d, "param"), pairs, "与合并无关的 C→D 被删了")
        self.assertIn((d, e, "inverse"), pairs, "与合并无关的 D→E 被删了")
        # 2. A→C 只留一条
        same = [p for p in pairs if p[0] == a and p[1] == c and p[2] == "same_method"]
        self.assertEqual(len(same), 1, "A→C 应去重成一条")
        # 3. 合并双方之间的互指被清掉，不留自指
        self.assertFalse([p for p in pairs if p[0] == a and p[1] == a], "出现了自指关系")
        self.assertFalse([p for p in pairs if b in (p[0], p[1])], "B 已合并，仍有关系指向它")

    def test_merge_moves_occurrences_tags_and_marks_inactive(self):
        a = self.new_question("合并目标 F")
        b = self.new_question("被合并的 G")
        source = self.ok("POST", "/api/sources", {"name": "合并测试卷", "kind": "exam"})
        self.ok("POST", "/api/occurrences", {"question_id": b, "source_id": source["id"],
                                             "qno": "3"})
        tag = self.ok("POST", "/api/tags", {"dimension": "method", "name": "合并测试法"})

        body = self.ok("POST", f"/api/questions/{b}/merge", {"into": a, "note": "合并"})
        self.assertGreaterEqual(body["moved_occurrences"], 1)
        self.assertEqual(body["merged_into"], a)

        merged = self.ok("GET", f"/api/questions/{b}")
        self.assertFalse(merged["eligibility"]["usable"])
        self.assertTrue(merged["question"]["merged_into"] == a)

        detail = self.ok("GET", f"/api/questions/{a}")
        self.assertTrue(any(o["source_id"] == source["id"] for o in detail["occurrences"]))

    def test_merge_rejects_self_and_chain(self):
        a = self.new_question("自指目标 H")
        status, payload = self.call("POST", f"/api/questions/{a}/merge", {"into": a})
        self.assertGreaterEqual(status, 400)
        self.assertIn("error", payload)

        b = self.new_question("链式 I")
        c = self.new_question("链式目标 J")
        self.ok("POST", f"/api/questions/{b}/merge", {"into": c, "note": "先合一次"})
        status, payload = self.call("POST", f"/api/questions/{b}/merge", {"into": c})
        self.assertGreaterEqual(status, 400, "已合并的题不能再次合并")


if __name__ == "__main__":
    unittest.main(verbosity=2)
