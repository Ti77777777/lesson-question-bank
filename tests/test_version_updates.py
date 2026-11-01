"""N06 新版提醒与显式更新。

要点：绑定预览的版本和内容摘要，明确确认小问与解法，整批原子执行并保持重试幂等。

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

    def preview_body(self, cid):
        data=self.ok("GET","/api/collections/%s/version-updates" % cid)
        return {"contract":2,"operation_id":str(uuid.uuid4()),"base_revision":data["revision"],
                "updates":[{"item_id":r["item_id"],"from_version_id":r["used"]["version_id"],
                            "question_version_id":r["new"]["version_id"],"content_digest":r["new"]["content_digest"],
                            "subquestions":{"mode":"all","keys":[]},"solution_id":None} for r in data["items"]]}

    def item(self,cid,iid):
        return next(i for i in self.ok("GET","/api/collections/%s" % cid)["items"] if i["id"]==iid)

    def apply(self,cid,body):
        return self.call("POST","/api/collections/%s/version-updates/apply" % cid,body)

    def test_preview_is_read_only_and_reports_exact_target(self):
        cid,qid,v1,iid,sol=self.setup_collection("预览")
        self.assertEqual(self.ok("GET","/api/collections/%s/version-updates" % cid)["total"],0)
        v2=self.new_version(qid,"修订")
        row=self.ok("GET","/api/collections/%s/version-updates" % cid)["items"][0]
        self.assertEqual(row["used"]["version_id"],v1)
        self.assertEqual(row["new"]["content"]["id"],v2)
        self.assertEqual(row["used"]["solution_id"],sol)
        self.assertTrue(row["new"]["content_digest"])
        self.assertEqual(self.item(cid,iid)["question_version_id"],v1)

    def test_explicit_selection_is_saved_and_snapshot_keeps_only_chosen_subquestion(self):
        from app.latex.build import make_snapshot
        from app.latex.exbook import _selected_subquestions
        cid,qid,v1,iid,sol=self.setup_collection("指定范围")
        v2=self.new_version(qid,"重写小问",[{"key":"a","text":"问a","depends_on":[]},{"key":"b","text":"问b","depends_on":[]}])
        body=self.preview_body(cid);body["updates"][0]["subquestions"]={"mode":"selected","keys":["a"]}
        status,out=self.apply(cid,body);self.assertEqual(status,200,out)
        row=self.item(cid,iid)
        self.assertEqual(row["question_version_id"],v2);self.assertEqual(row["subq_keys"],["a"])
        self.assertIsNone(row["solution_id"])
        snap=make_snapshot(cid)
        entry=snap["question_versions"][v2]["version"]
        self.assertEqual([s["key"] for s in _selected_subquestions(entry,snap["items"][0])],["a"])

    def test_new_version_after_preview_rejects_without_drift(self):
        cid,qid,v1,iid,sol=self.setup_collection("目标漂移")
        self.new_version(qid,"第二版");body=self.preview_body(cid)
        self.new_version(qid,"第三版")
        status,err=self.apply(cid,body)
        self.assertEqual(status,409);self.assertEqual(err["error"]["detail"]["results"][0]["code"],"preview_changed")
        self.assertEqual(self.item(cid,iid)["question_version_id"],v1)

    def test_failure_in_batch_is_atomic(self):
        cid,qid,v1,iid,sol=self.setup_collection("整批回滚")
        self.new_version(qid,"第二版");body=self.preview_body(cid)
        body["updates"].append({**body["updates"][0],"item_id":"ITM999999"})
        status,err=self.apply(cid,body)
        self.assertEqual(status,409);self.assertEqual(self.item(cid,iid)["question_version_id"],v1)

    def test_missing_or_empty_subquestion_confirmation_never_becomes_all(self):
        cid,qid,v1,iid,sol=self.setup_collection("范围保护")
        self.new_version(qid,"新的问",[{"key":"a","text":"问a","depends_on":[]}])
        for selection in [None,{"mode":"selected","keys":[]},{"mode":"selected","keys":["missing"]}]:
            body=self.preview_body(cid);body["updates"][0]["subquestions"]=selection
            status,err=self.apply(cid,body)
            self.assertEqual(status,409,err);self.assertEqual(self.item(cid,iid)["question_version_id"],v1)
        # All questions are used only after explicit confirmation.
        status,out=self.apply(cid,self.preview_body(cid));self.assertEqual(status,200,out)
        self.assertIsNone(self.item(cid,iid)["subq_keys"])

    def test_solution_choice_and_in_place_changes_are_checked(self):
        cid,qid,v1,iid,old_sol=self.setup_collection("解法保护")
        v2=self.new_version(qid,"第二版")
        sol=self.ok("POST","/api/versions/%s/solutions" % v2,{"label":"新版解法","steps":"最初步骤"})
        body=self.preview_body(cid)
        self.ok("PATCH","/api/solutions/%s" % sol["id"],{"steps":"修订后的步骤"})
        status,err=self.apply(cid,body);self.assertEqual(status,409)
        body=self.preview_body(cid);body["updates"][0]["solution_id"]={"invalid":"object"}
        status,err=self.apply(cid,body);self.assertEqual(status,409)
        body=self.preview_body(cid);body["updates"][0]["solution_id"]=old_sol
        status,err=self.apply(cid,body);self.assertEqual(status,409)
        body=self.preview_body(cid);del body["updates"][0]["solution_id"]
        status,err=self.apply(cid,body);self.assertEqual(status,409)
        body=self.preview_body(cid);body["updates"][0]["solution_id"]=sol["id"]
        status,out=self.apply(cid,body);self.assertEqual(status,200,out)
        self.assertEqual(self.item(cid,iid)["solution_id"],sol["id"])

    def test_stale_collection_revision_and_old_contract_are_rejected(self):
        cid,qid,v1,iid,sol=self.setup_collection("旧页面保护")
        self.new_version(qid,"第二版");body=self.preview_body(cid)
        self.ok("PATCH","/api/items/%s" % iid,{"time_solve_min":19})
        status,err=self.apply(cid,body)
        self.assertEqual(status,409);self.assertEqual(err["error"]["code"],"revision_conflict")
        body=self.preview_body(cid);del body["contract"]
        status,err=self.apply(cid,body);self.assertEqual(status,409)
        self.assertEqual(self.item(cid,iid)["question_version_id"],v1)

    def test_dependency_and_duplicate_item_fail_without_partial_updates(self):
        cid,qid,v1,iid,sol=self.setup_collection("依赖确认")
        self.new_version(qid,"有依赖的新问",[{"key":"a","text":"前问","depends_on":[]},
                                              {"key":"b","text":"后问","depends_on":["a"]}])
        body=self.preview_body(cid);body["updates"][0]["subquestions"]={"mode":"selected","keys":["b"]}
        status,err=self.apply(cid,body);self.assertEqual(status,409)
        self.assertEqual(err["error"]["detail"]["results"][0]["code"],"subq_dependency")
        body=self.preview_body(cid);body["updates"].append(dict(body["updates"][0]))
        status,err=self.apply(cid,body);self.assertEqual(status,409)
        self.assertEqual(self.item(cid,iid)["question_version_id"],v1)

    def test_replay_returns_original_result_even_if_newer_version_exists(self):
        cid,qid,v1,iid,sol=self.setup_collection("幂等重试")
        v2=self.new_version(qid,"第二版");body=self.preview_body(cid)
        status,first=self.apply(cid,body);self.assertEqual(status,200,first)
        self.new_version(qid,"第三版")
        status,again=self.apply(cid,body);self.assertEqual(status,200,again)
        self.assertTrue(again["replayed"]);self.assertEqual(again["results"],first["results"])
        body["updates"][0]["subquestions"]={"mode":"selected","keys":["1"]}
        status,err=self.apply(cid,body);self.assertEqual(status,409)
        self.assertEqual(err["error"]["code"],"operation_conflict")
        self.assertEqual(self.item(cid,iid)["question_version_id"],v2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
