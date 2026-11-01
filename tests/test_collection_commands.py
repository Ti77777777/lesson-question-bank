"""N02 首期：题单可撤销写入。

每个动作返回逆动作，页面用它做撤销。这里验证「逆动作真的能还原」，
以及并发、幂等与拒绝重复恢复的边界。

运行：
    python -m unittest tests.test_collection_commands -v
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
DB_PATH = ROOT / "build" / "_test_collection_commands.db"

for _p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    if _p.exists():
        _p.unlink()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB_PATH)
os.environ["QBANK_AI_WORKER"] = "0"

sys.path.insert(0, str(ROOT))

from app.server import Handler, create_app  # noqa: E402


class CommandTest(unittest.TestCase):
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
            "stem": "%s：求 $x+\\frac{1}{x}$ 的最小值。" % name,
            "answer": "$2$", "change_note": "撤销用例"})
        return body["question"]["id"]

    def fixture(self, title):
        cid = self.ok("POST", "/api/collections", {"title": title})["collection"]["id"]
        sid = self.ok("POST", "/api/collections/%s/sections" % cid,
                      {"title": "一、例题"})["section"]["id"]
        ids = []
        for i in (1, 2, 3):
            qid = self.new_question("%s 题 %d" % (title, i))
            item = self.ok("POST", "/api/collections/%s/items" % cid, {
                "question_id": qid, "section_id": sid, "space_mm": 20 + i})
            ids.append(item["item"]["id"])
        return cid, sid, ids

    def revision(self, cid):
        return self.ok("GET", "/api/collections/%s" % cid)["revision"]

    def command(self, cid, action, item_id="", payload=None, base=None, op=None):
        return {"operation_id": op or str(uuid.uuid4()),
                "base_revision": self.revision(cid) if base is None else base,
                "action": action, "item_id": item_id, "payload": payload or {}}

    # ------------------------------------------------------------ 用例
    def test_01_remove_then_restore_keeps_identity_and_settings(self):
        cid, sid, ids = self.fixture("撤销 A")
        before = self.ok("GET", "/api/collections/%s" % cid)["items"]
        target = before[1]

        out = self.ok("POST", "/api/collections/%s/commands" % cid,
                      self.command(cid, "remove", target["id"]))
        self.assertEqual(out["inverse"]["action"], "restore")
        self.assertEqual(len(self.ok("GET", "/api/collections/%s" % cid)["items"]), 2)

        back = self.ok("POST", "/api/collections/%s/commands" % cid,
                       self.command(cid, "restore", target["id"], out["inverse"]["payload"]))
        self.assertEqual(back["inverse"]["action"], "remove")
        after = self.ok("GET", "/api/collections/%s" % cid)["items"]
        self.assertEqual(len(after), 3)
        restored = next(i for i in after if i["id"] == target["id"])
        for key in ("section_id", "question_id", "question_version_id", "order_no",
                    "role", "space_mm", "required"):
            self.assertEqual(restored[key], target[key], "恢复后 %s 应保持原值" % key)

    def test_02_cannot_restore_an_existing_item(self):
        cid, sid, ids = self.fixture("撤销 B")
        status, err = self.call("POST", "/api/collections/%s/commands" % cid,
                                self.command(cid, "restore", ids[0],
                                             {"question_id": "Q000001",
                                              "question_version_id": "QV000001"}))
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "already_exists")

    def test_03_reorder_undo_restores_previous_order(self):
        cid, sid, ids = self.fixture("撤销 C")
        before = [(i["id"], i["order_no"]) for i in self.ok("GET", "/api/collections/%s" % cid)["items"]]
        flipped = [{"id": i, "order_no": len(ids) - 1 - n, "section_id": sid}
                   for n, i in enumerate(ids)]
        out = self.ok("POST", "/api/collections/%s/commands" % cid,
                      self.command(cid, "reorder", payload={"items": flipped}))
        now = [i["id"] for i in self.ok("GET", "/api/collections/%s" % cid)["items"]]
        self.assertEqual(now, list(reversed(ids)))

        self.ok("POST", "/api/collections/%s/commands" % cid,
                self.command(cid, "reorder", payload=out["inverse"]["payload"]))
        after = [(i["id"], i["order_no"]) for i in self.ok("GET", "/api/collections/%s" % cid)["items"]]
        self.assertEqual(after, before)

    def test_04_settings_undo_restores_previous_values(self):
        cid, sid, ids = self.fixture("撤销 D")
        target = ids[0]
        out = self.ok("POST", "/api/collections/%s/commands" % cid,
                      self.command(cid, "settings", target,
                                   {"space_mm": 55, "note": "改成 55mm"}))
        item = next(i for i in self.ok("GET", "/api/collections/%s" % cid)["items"]
                    if i["id"] == target)
        self.assertEqual(item["space_mm"], 55)

        self.ok("POST", "/api/collections/%s/commands" % cid,
                self.command(cid, "settings", target, out["inverse"]["payload"]))
        item = next(i for i in self.ok("GET", "/api/collections/%s" % cid)["items"]
                    if i["id"] == target)
        self.assertEqual(item["space_mm"], 21)
        self.assertIsNone(item.get("note"))

    def test_05_stale_revision_blocks_undo(self):
        cid, sid, ids = self.fixture("撤销 E")
        stale = self.revision(cid)
        self.ok("POST", "/api/collections/%s/commands" % cid,
                self.command(cid, "remove", ids[0]))
        status, err = self.call("POST", "/api/collections/%s/commands" % cid,
                                self.command(cid, "remove", ids[1], base=stale))
        self.assertEqual(status, 409)
        self.assertEqual(err["error"]["code"], "revision_conflict")
        # 第二次移除没有生效
        self.assertEqual(len(self.ok("GET", "/api/collections/%s" % cid)["items"]), 2)

    def test_06_replay_returns_same_inverse_without_second_write(self):
        cid, sid, ids = self.fixture("撤销 F")
        op = str(uuid.uuid4())
        body = self.command(cid, "remove", ids[2], op=op)
        first = self.ok("POST", "/api/collections/%s/commands" % cid, body)
        again = self.ok("POST", "/api/collections/%s/commands" % cid, body)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["inverse"], first["inverse"])
        self.assertEqual(len(self.ok("GET", "/api/collections/%s" % cid)["items"]), 2)

    def test_07_unknown_action_rejected(self):
        cid, sid, ids = self.fixture("撤销 G")
        status, _err = self.call("POST", "/api/collections/%s/commands" % cid,
                                 self.command(cid, "delete_everything", ids[0]))
        self.assertEqual(status, 400)


    def set_layout(self, cid, items):
        return self.ok("PUT", "/api/collections/%s/layout" % cid,
                       {"options": {"cover": False}, "items": items})

    def get_layout(self, cid):
        return self.ok("GET", "/api/collections/%s/layout" % cid)["layout"]["items"]

    def test_remove_undo_redo_restores_layout_and_selected_subquestions(self):
        cid, sid, ids = self.fixture("完整恢复")
        q = self.ok("POST", "/api/questions", {"qtype": "solve", "stem": "合成题",
            "subquestions": [{"key": "a", "text": "问a", "depends_on": []}]})
        iid = self.ok("POST", "/api/collections/%s/items" % cid,
                      {"question_id": q["question"]["id"], "subq_keys": ["a"]})["item"]["id"]
        for override in [None, {}, {"space_mm":75,"keep_together":True,"options_layout":"vertical"}]:
            with self.subTest(override=override):
                layout = {ids[0]: {"space_mm": 35}}
                if override is not None: layout[iid] = override
                self.set_layout(cid, layout)
                out = self.ok("POST", "/api/collections/%s/commands" % cid, self.command(cid,"remove",iid))
                self.assertNotIn(iid,self.get_layout(cid))
                back = self.ok("POST", "/api/collections/%s/commands" % cid,
                               self.command(cid,"restore",iid,out["inverse"]["payload"]))
                self.assertEqual(self.get_layout(cid),layout)
                item = next(i for i in self.ok("GET","/api/collections/%s" % cid)["items"] if i["id"]==iid)
                self.assertEqual(item["subq_keys"],["a"])
                again = self.ok("POST", "/api/collections/%s/commands" % cid,
                                self.command(cid,"remove",iid,back["inverse"]["payload"]))
                self.ok("POST", "/api/collections/%s/commands" % cid,
                        self.command(cid,"restore",iid,again["inverse"]["payload"]))
                self.assertEqual(self.get_layout(cid),layout)

    def test_settings_undo_restores_override_and_rejects_tampered_inverse(self):
        cid, sid, ids = self.fixture("设置恢复")
        layout = {ids[0]: {"space_mm":75,"keep_together":True},ids[1]:{"space_mm":90}}
        self.set_layout(cid,layout)
        out=self.ok("POST","/api/collections/%s/commands" % cid,
                    self.command(cid,"settings",ids[0],{"space_mm":45}))
        self.assertEqual(self.get_layout(cid)[ids[0]],{"keep_together":True})
        fake={**out["inverse"]["payload"],"space_mm":199}
        status,err=self.call("POST","/api/collections/%s/commands" % cid,self.command(cid,"settings",ids[0],fake))
        self.assertEqual(status,409)
        self.assertEqual(err["error"]["code"],"undo_conflict")
        back=self.ok("POST","/api/collections/%s/commands" % cid,
                     self.command(cid,"settings",ids[0],out["inverse"]["payload"]))
        self.assertEqual(self.get_layout(cid),layout)
        self.ok("POST","/api/collections/%s/commands" % cid,
                self.command(cid,"settings",ids[0],back["inverse"]["payload"]))
        self.assertEqual(self.get_layout(cid)[ids[0]],{"keep_together":True})

    def test_revision_covers_all_columns_except_explicit_context_and_timestamps(self):
        from app import db, commands
        cid,sid,ids=self.fixture("修订字段矩阵")
        matrix=[("collection",cid,commands._COLLECTION_FIELDS,{"created_at","updated_at"}),
                ("collection_section",sid,commands._SECTION_FIELDS,set()),
                ("collection_item",ids[0],commands._ITEM_FIELDS,{"created_at","collection_id"})]
        for table,key,fields,excluded in matrix:
            columns={r["name"] for r in db.all_("PRAGMA table_info("+table+")")}
            self.assertEqual(columns,set(fields)|excluded,table)
        # These previously omitted writable fields must invalidate stale edits.
        cases=[("collection","prereqs","先学集合"),("collection","objectives","掌握定义"),
               ("collection","coverage","知识点范围"),("collection","template","alternate"),
               ("collection","note",'{"x": 1}'),("collection_section","note","分节备注"),
               ("collection_item","time_solve_min",19),("collection_item","time_explain_min",23)]
        for table,field,value in cases:
            key=cid if table=="collection" else sid if table=="collection_section" else ids[0]
            before=commands.collection_revision(cid)
            with db.transaction() as conn:conn.execute("UPDATE "+table+" SET "+field+"=? WHERE id=?",(value,key))
            self.assertNotEqual(commands.collection_revision(cid),before,(table,field))
        # Canonical JSON object order is immaterial; ordinary prose remains literal.
        with db.transaction() as conn:conn.execute("UPDATE collection SET layout_options=? WHERE id=?", ('{"items":{},"options":{}}',cid))
        before=commands.collection_revision(cid)
        with db.transaction() as conn:conn.execute("UPDATE collection SET layout_options=? WHERE id=?", ('{ "options": {}, "items": {} }',cid))
        self.assertEqual(commands.collection_revision(cid),before)
        with db.transaction() as conn:conn.execute("UPDATE collection SET note=? WHERE id=?",('{"x":1}',cid))
        self.assertNotEqual(commands.collection_revision(cid),before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
