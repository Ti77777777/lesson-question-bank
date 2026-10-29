"""内容域接口自测（§2–§8）。

启动真实 HTTP 服务，用 urllib 打接口，覆盖：资料与上传、建题、版本、审校、
检索、关系、合并、收题箱、查重。

运行：
    python tests/test_content.py -v
数据库固定在 build/_test_content.db，每次运行前删除重建。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "build" / "_test_content.db"

# 环境变量必须早于 app.db 导入：db.DB_PATH 在模块导入时定型
for _p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
    if _p.exists():
        _p.unlink()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB_PATH)

sys.path.insert(0, str(ROOT))

from app.server import Handler, create_app  # noqa: E402

DETAIL_KEYS = {"question", "current_version", "versions", "occurrences", "relations",
               "reviews", "review_status", "eligibility", "usage"}
# 跨用例共享的数据（unittest 每个用例都是新实例）
S: dict = {}

REVIEW_ITEMS = {"stem", "answer", "solution", "figure",
                "classification", "boundary", "method_scope"}


class ContentApiTest(unittest.TestCase):
    """按 test_01…test_11 顺序执行，后面的用例依赖前面的数据。"""

    @classmethod
    def setUpClass(cls):
        create_app()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        # 只清理本次自测写下的那个文件，不碰 sources/ 下其他内容
        rel = S.get("file_rel")
        if rel:
            target = ROOT / rel
            if target.is_file():
                target.unlink()
            parent = target.parent
            try:
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

    # ------------------------------------------------------------ 工具
    def call(self, method: str, path: str, body=None, expect: int = 200, **params):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status, payload = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, exc.read()
        parsed = json.loads(payload.decode("utf-8"))
        self.assertEqual(status, expect,
                         f"{method} {path} 期望 {expect} 实际 {status}：{parsed}")
        return parsed

    def ok(self, method: str, path: str, body=None, **params):
        return self.call(method, path, body, **params)

    def detail(self, qid: str) -> dict:
        return self.ok("GET", f"/api/questions/{qid}")

    # ------------------------------------------------------------ 01 标签
    def test_01_tags(self):
        S["tag_k1"] = self.ok("POST", "/api/tags", {
            "dimension": "knowledge", "name": "一元二次方程", "definition": "含参讨论"})["id"]
        S["tag_k2"] = self.ok("POST", "/api/tags", {
            "dimension": "knowledge", "name": "根的判别式"})["id"]
        S["tag_m1"] = self.ok("POST", "/api/tags", {
            "dimension": "method", "name": "判别式法"})["id"]
        S["tag_k3"] = self.ok("POST", "/api/tags", {
            "dimension": "knowledge", "name": "韦达定理"})["id"]

        listing = self.ok("GET", "/api/tags", dimension="knowledge")
        self.assertEqual(listing["total"], 3)
        self.assertTrue(all(t["dimension"] == "knowledge" for t in listing["items"]))

        # 同维度同名重复
        err = self.call("POST", "/api/tags",
                        {"dimension": "knowledge", "name": "根的判别式"}, 409)
        self.assertEqual(err["error"]["code"], "duplicate_tag")

        # 合并同义标签
        merged = self.ok("POST", f"/api/tags/{S["tag_k3"]}/merge", {"into": S["tag_k2"]})
        self.assertEqual(merged["id"], S["tag_k2"])
        old = self.ok("GET", f"/api/tags?dimension=knowledge")
        self.assertTrue(any(t["id"] == S["tag_k3"] and t["merged_into"] == S["tag_k2"]
                            for t in old["items"]))

    # ------------------------------------------------------------ 02 资料与文件
    def test_02_source_and_file(self):
        S["source"] = self.ok("POST", "/api/sources", {
            "name": "2024 学年高一期末", "kind": "exam", "edition": "2024",
            "region": "本校", "publisher": "教务处", "usage_scope": "校内",
            "license_basis": "校内教研使用"})
        S["src_id"] = S["source"]["id"]
        self.assertTrue(S["src_id"].startswith("SRC"))

        blob = b"%PDF-1.4 fake exam paper for test"
        uploaded = self.ok("POST", f"/api/sources/{S["src_id"]}/files", {
            "filename": "../../evil/2024期末.pdf",
            "content_b64": base64.b64encode(blob).decode("ascii")})
        S["file_id"] = uploaded["id"]
        S["file_rel"] = uploaded["rel_path"]
        # 路径分隔符与 .. 都被清掉；同名文件已存在时加校验值后缀，故只校验前后缀
        self.assertTrue(uploaded["filename"].startswith("2024期末"))
        self.assertTrue(uploaded["filename"].endswith(".pdf"))
        self.assertNotIn("/", uploaded["filename"])
        self.assertNotIn("..", uploaded["filename"])
        # 文件落在「库名」子目录下，避免测试库与正式库的编号互相覆盖
        self.assertEqual(uploaded["rel_path"],
                         f"sources/_test_content/{S['src_id']}/{uploaded['filename']}")
        self.assertEqual(uploaded["bytes"], len(blob))
        self.assertEqual(uploaded["sha256"],
                         __import__("hashlib").sha256(blob).hexdigest())
        self.assertTrue((ROOT / uploaded["rel_path"]).is_file())

        # 同一资料重复上传同一校验值
        dup = self.call("POST", f"/api/sources/{S["src_id"]}/files", {
            "filename": "another.pdf",
            "content_b64": base64.b64encode(blob).decode("ascii")}, 409)
        self.assertEqual(dup["error"]["code"], "duplicate_file")
        self.assertEqual(dup["error"]["detail"]["file_id"], S["file_id"])

        files = self.ok("GET", f"/api/sources/{S["src_id"]}/files")
        self.assertEqual(files["total"], 1)

        detail = self.ok("GET", f"/api/sources/{S["src_id"]}")
        self.assertEqual(len(detail["files"]), 1)
        self.assertEqual(detail["occurrences"], [])

        sources = self.ok("GET", "/api/sources", keyword="期末", kind="exam")
        row = next(s for s in sources["items"] if s["id"] == S["src_id"])
        self.assertEqual(row["file_count"], 1)
        self.assertEqual(row["occurrence_count"], 0)

    # ------------------------------------------------------------ 03 建题
    def test_03_create_question(self):
        payload = {
            "short_name": "含参二次方程根的分布",
            "qtype": "solve",
            "stem": "已知 $a\\in\\mathbb{R}$，方程 $x^2-2ax+1=0$ 有两个正根，求 $a$ 的取值范围。",
            "answer": "$a>1$",
            "teaching": {"why": "两根为正需同时约束判别式与韦达定理",
                         "key_step": "列出三个不等式", "probe": "若只有一个正根呢",
                         "pitfalls": "漏掉判别式", "prereq": "韦达定理"},
            "difficulty": {"calc": 2, "think": 3, "scope": 2, "level": "高一提高",
                           "note": "边界比较典型"},
            "rec_level": "A",
            "rec_reason": "边界比较典型",
            "time_solve_min": 5,
            "time_explain_min": 8,
            "tags": [{"tag_id": S["tag_k1"], "role": "primary"},
                     {"tag_id": S["tag_k2"], "role": "secondary"}],
            "occurrence": {"source_id": S["src_id"], "source_file_id": S["file_id"],
                           "pdf_page": 3, "print_page": "12", "qno": "8",
                           "raw_text": "已知 a∈R，方程 x^2-2ax+1=0 有两个正根……", "pending": 0},
            "solutions": [{"label": "解法一", "approach": "判别式 + 韦达定理",
                           "steps": "Δ=4a^2-4≥0，x1+x2=2a>0，x1x2=1>0，得 a>1",
                           "conditions": "a 为实数", "is_primary": 1,
                           "method_tags": [S["tag_m1"]]}],
            "change_note": "初次录入",
        }
        body = self.ok("POST", "/api/questions", payload)
        S["q1"] = body["question"]["id"]
        self.assertTrue(S["q1"].startswith("Q"))
        S["v1"] = body["current_version"]["id"]

        # 详情字段一个都不能少
        self.assertEqual(set(body), DETAIL_KEYS)
        for key in ("id", "short_name", "status", "current_version_id", "difficulty_calc",
                    "difficulty_think", "difficulty_scope", "difficulty_level",
                    "difficulty_note", "rec_level", "rec_reason", "time_solve_min",
                    "time_explain_min", "note", "created_at", "updated_at", "created_by",
                    "status_reason", "merged_into"):
            self.assertIn(key, body["question"], f"question 缺字段 {key}")
        for key in ("id", "version_no", "qtype", "stem", "options", "answer", "answer_keys",
                    "subquestions", "analysis", "teaching", "solutions", "tags",
                    "version_count", "change_note", "review_state", "created_at"):
            self.assertIn(key, body["current_version"], f"current_version 缺字段 {key}")

        self.assertEqual(body["question"]["current_version_id"], S["v1"])
        self.assertEqual(body["current_version"]["version_no"], 1)
        self.assertEqual(body["current_version"]["version_count"], 1)
        self.assertEqual(body["current_version"]["qtype"], "solve")
        self.assertEqual(body["current_version"]["teaching"]["key_step"], "列出三个不等式")

        # 解法随版本、方法标签挂上
        solutions = body["current_version"]["solutions"]
        self.assertEqual(len(solutions), 1)
        self.assertEqual(solutions[0]["label"], "解法一")
        self.assertEqual(solutions[0]["is_primary"], 1)
        self.assertEqual([t["id"] for t in solutions[0]["tags"]], [S["tag_m1"]])

        # 题目标签（合并后的 k3 不影响）
        tag_ids = {t["id"] for t in body["current_version"]["tags"]}
        self.assertEqual(tag_ids, {S["tag_k1"], S["tag_k2"]})

        # 出处
        self.assertEqual(len(body["occurrences"]), 1)
        occ = body["occurrences"][0]
        self.assertEqual(occ["source_id"], S["src_id"])
        self.assertEqual(occ["source_name"], "2024 学年高一期末")
        self.assertEqual(occ["source_kind"], "exam")
        self.assertEqual(occ["file"], S["file_rel"])
        self.assertEqual(occ["pdf_page"], 3)
        self.assertEqual(occ["pending"], 0)
        self.assertIn("两个正根", occ["raw_text"])

        # 审校与可用性
        self.assertEqual(set(body["review_status"]), REVIEW_ITEMS)
        self.assertEqual(body["review_status"]["stem"], "pending")
        self.assertEqual(body["review_status"]["figure"], "na")
        self.assertFalse(body["eligibility"]["usable"])
        self.assertIn("题面尚未审校", body["eligibility"]["missing"])
        self.assertEqual(body["relations"], {"out": [], "in": []})
        self.assertEqual(body["usage"], [])

        # 管理字段
        self.assertEqual(body["question"]["difficulty_think"], 3)
        self.assertEqual(body["question"]["time_solve_min"], 5)

        # 无出处的新题必须落一条待确认出处
        bare = self.ok("POST", "/api/questions", {
            "qtype": "blank", "stem": "填空：$1+1=$____。", "answer": "2"})
        S["q_bare"] = bare["question"]["id"]
        self.assertEqual(len(bare["occurrences"]), 1)
        self.assertEqual(bare["occurrences"][0]["pending"], 1)
        self.assertIsNone(bare["occurrences"][0]["source_id"])

        # 既无 source 又无 pending 应被拒
        err = self.call("POST", "/api/questions", {
            "qtype": "blank", "stem": "无出处题", "occurrence": {"pending": 0}}, 400)
        self.assertEqual(err["error"]["code"], "bad_occurrence")

    # ------------------------------------------------------------ 04 版本
    def test_04_new_version(self):
        old = self.detail(S["q1"])
        old_solution_id = old["current_version"]["solutions"][0]["id"]
        S["old_solution_id"] = old_solution_id

        body = self.ok("POST", f"/api/questions/{S["q1"]}/versions", {
            "stem": "已知 $a\\in\\mathbb{R}$，方程 $x^2-2ax+1=0$ 有两个不相等的正根，求 $a$ 的取值范围。",
            "change_note": "补“不相等”三字"})
        S["v2"] = body["current_version"]["id"]
        self.assertNotEqual(S["v2"], S["v1"])
        self.assertEqual(body["current_version"]["version_no"], 2)
        self.assertEqual(body["current_version"]["version_count"], 2)
        self.assertIn("不相等", body["current_version"]["stem"])
        # 未传的字段沿用旧版本
        self.assertEqual(body["current_version"]["answer"], "$a>1$")
        self.assertEqual(body["current_version"]["qtype"], "solve")

        # 旧版本封存但可读
        old_brief = next(v for v in body["versions"] if v["version_no"] == 1)
        self.assertEqual(old_brief["review_state"], "submitted")
        self.assertEqual(old_brief["solution_count"], 1)
        v1_detail = self.ok("GET", f"/api/questions/{S["q1"]}/versions/1")
        self.assertEqual(v1_detail["version_no"], 1)
        self.assertNotIn("不相等", v1_detail["stem"])
        self.assertFalse(v1_detail["is_current"])

        # 解法随版本复制，ID 是新的
        new_solutions = body["current_version"]["solutions"]
        self.assertEqual(len(new_solutions), 1)
        self.assertNotEqual(new_solutions[0]["id"], old_solution_id)
        self.assertEqual(new_solutions[0]["label"], "解法一")
        self.assertEqual([t["id"] for t in new_solutions[0]["tags"]], [S["tag_m1"]])
        S["v2_solution_id"] = new_solutions[0]["id"]

        # 新版本没有审校记录，状态自然回到待审
        self.assertEqual(body["review_status"]["stem"], "pending")
        self.assertFalse(body["eligibility"]["usable"])

        # 历史版本的解法锁定
        err = self.call("PATCH", f"/api/solutions/{old_solution_id}", {"label": "改个名"}, 409)
        self.assertEqual(err["error"]["code"], "version_locked")

        # 内容不允许走 PATCH
        err = self.call("PATCH", f"/api/questions/{S["q1"]}", {"stem": "直接覆盖"}, 400)
        self.assertEqual(err["error"]["code"], "content_locked")

        # 差异
        diff = self.ok("GET", f"/api/questions/{S["q1"]}/diff", **{"from": 1, "to": 2})
        self.assertIn("stem", diff["changed"])
        self.assertNotIn("answer", diff["changed"])

    # ------------------------------------------------------------ 05 审校
    def test_05_reviews(self):
        for item, result in (("stem", "pass"), ("answer", "pass")):
            self.ok("POST", "/api/reviews", {
                "target_type": "question_version", "target_id": S["v2"],
                "item": item, "result": result, "reviewer": "教师甲"})
        self.ok("POST", "/api/reviews", {
            "target_type": "solution", "target_id": S["v2_solution_id"],
            "item": "solution", "result": "pass", "reviewer": "教师甲"})

        body = self.detail(S["q1"])
        self.assertEqual(body["review_status"]["stem"], "pass")
        self.assertEqual(body["review_status"]["answer"], "pass")
        self.assertEqual(body["review_status"]["solution"], "pass")
        self.assertEqual(body["review_status"]["figure"], "na")
        self.assertTrue(body["eligibility"]["usable"])
        self.assertEqual(body["eligibility"]["missing"], [])
        self.assertEqual(len(body["reviews"]), 3)
        self.assertEqual(body["reviews"][0]["created_at"] >= body["reviews"][-1]["created_at"],
                         True)

        # 只登记 fail 的解法则整项 fail
        failed = self.ok("POST", "/api/reviews", {
            "target_type": "solution", "target_id": S["v2_solution_id"],
            "item": "solution", "result": "fail", "note": "步骤有漏洞"})
        self.assertEqual(failed["result"], "fail")
        body = self.detail(S["q1"])
        self.assertEqual(body["review_status"]["solution"], "fail")
        self.assertFalse(body["eligibility"]["usable"])
        self.assertIn("解析未通过审校", body["eligibility"]["missing"])

        # 补一条 pass 覆盖（最新一条为准）
        self.ok("POST", "/api/reviews", {
            "target_type": "solution", "target_id": S["v2_solution_id"],
            "item": "solution", "result": "pass", "note": "已改好"})
        self.assertTrue(self.detail(S["q1"])["eligibility"]["usable"])

        # 非法项
        err = self.call("POST", "/api/reviews", {
            "target_type": "question_version", "target_id": S["v2"],
            "item": "wrong", "result": "pass"}, 400)
        self.assertEqual(err["error"]["code"], "bad_field")

        # 列表按时间倒序
        listing = self.ok("GET", "/api/reviews",
                          target_type="question_version", target_id=S["v2"])
        self.assertEqual(listing["total"], 2)

    # ------------------------------------------------------------ 06 再审即重置
    def test_06_new_version_resets_review(self):
        body = self.ok("POST", f"/api/questions/{S["q1"]}/versions", {
            "answer": "$a>1$ 且 $a\\ne 1$", "change_note": "补答案边界说明"})
        S["v3"] = body["current_version"]["id"]
        self.assertEqual(body["current_version"]["version_no"], 3)
        self.assertEqual(body["review_status"]["stem"], "pending")
        self.assertEqual(body["review_status"]["answer"], "pending")
        self.assertFalse(body["eligibility"]["usable"])

        # 第 2 版仍保持已通过的结论
        v2 = self.ok("GET", f"/api/questions/{S["q1"]}/versions/2")
        self.assertEqual(v2["review_status"]["stem"], "pass")
        self.assertTrue(v2["eligibility"]["usable"])
        self.assertFalse(v2["is_current"])

        # 给第 3 版补审校，供后续检索使用
        S["v3_solution_id"] = body["current_version"]["solutions"][0]["id"]
        for item, result in (("stem", "pass"), ("answer", "pass")):
            self.ok("POST", "/api/reviews", {
                "target_type": "question_version", "target_id": S["v3"],
                "item": item, "result": result})
        self.ok("POST", "/api/reviews", {
            "target_type": "solution", "target_id": S["v3_solution_id"],
            "item": "solution", "result": "pass"})
        self.assertTrue(self.detail(S["q1"])["eligibility"]["usable"])

    # ------------------------------------------------------------ 07 检索
    def test_07_search(self):
        # 标签 all：两个都要有
        hit = self.ok("GET", "/api/questions", tags=f"{S["tag_k1"]},{S["tag_k2"]}",
                      tag_mode="all")
        self.assertIn(S["q1"], [i["id"] for i in hit["items"]])

        # all 语义下少一个就查不到
        miss = self.ok("GET", "/api/questions", tags=f"{S["tag_k1"]},{S["tag_k3"]}",
                       tag_mode="all")
        self.assertNotIn(S["q1"], [i["id"] for i in miss["items"]])

        # any
        any_hit = self.ok("GET", "/api/questions", tags=S["tag_k2"], tag_mode="any")
        self.assertIn(S["q1"], [i["id"] for i in any_hit["items"]])

        # exclude
        excluded = self.ok("GET", "/api/questions", tags=S["tag_k1"], tag_mode="exclude")
        self.assertNotIn(S["q1"], [i["id"] for i in excluded["items"]])

        # keyword 命中题面 / 答案 / 简名 / 出处原文
        for kw in ("不相等的正根", "取值范围", "含参二次方程", "两个正根"):
            found = self.ok("GET", "/api/questions", keyword=kw)
            self.assertIn(S["q1"], [i["id"] for i in found["items"]], f"keyword={kw} 未命中")

        # method_tag：返回条目带上命中的解法 label
        by_method = self.ok("GET", "/api/questions", method_tag=S["tag_m1"])
        row = next(i for i in by_method["items"] if i["id"] == S["q1"])
        self.assertEqual(row["methods"][0]["id"], S["tag_m1"])
        self.assertEqual(row["methods"][0]["label"], "解法一")

        # review=pass 只剩可用题
        passed = self.ok("GET", "/api/questions", review="pass")
        self.assertIn(S["q1"], [i["id"] for i in passed["items"]])
        self.assertTrue(all(i["review_state"] == "pass" for i in passed["items"]))
        pending = self.ok("GET", "/api/questions", review="pending")
        self.assertNotIn(S["q1"], [i["id"] for i in pending["items"]])

        # 其余参数
        self.assertGreaterEqual(
            len(self.ok("GET", "/api/questions", qtype="solve")["items"]), 1)
        # 多题型用逗号分隔（server 的 query 只保留同名参数第一个值）
        multi = self.ok("GET", "/api/questions", qtype="solve,blank", limit=50)
        multi_ids = [i["id"] for i in multi["items"]]
        self.assertIn(S["q1"], multi_ids)
        self.assertIn(S["q_bare"], multi_ids)
        self.assertIn(S["q1"], [i["id"] for i in
                                self.ok("GET", "/api/questions", think_min=3, calc_max=3)["items"]])
        self.assertNotIn(S["q1"], [i["id"] for i in
                                   self.ok("GET", "/api/questions", think_min=4)["items"]])
        page = self.ok("GET", "/api/questions", limit=1)
        self.assertEqual(len(page["items"]), 1)
        self.assertGreaterEqual(page["total"], 2)
        self.assertEqual(self.ok("GET", "/api/questions", status="inactive")["total"], 0)

        # 条目形状
        row = next(i for i in hit["items"] if i["id"] == S["q1"])
        for key in ("id", "short_name", "qtype", "version_no", "stem_preview", "tags",
                    "methods", "difficulty", "rec_level", "review_state", "status",
                    "used_on"):
            self.assertIn(key, row)
        self.assertEqual(set(row["difficulty"]), {"calc", "think", "scope"})
        self.assertEqual(row["version_no"], 3)
        self.assertEqual(row["status"], "active")
        self.assertIsNone(row["used_on"])

    # ------------------------------------------------------------ 08 关系
    def test_08_relations(self):
        S["q2"] = self.ok("POST", "/api/questions", {
            "short_name": "含参二次方程根的分布·变式",
            "qtype": "solve",
            "stem": "已知 $a\\in\\mathbb{R}$，方程 $x^2-2ax+1=0$ 有两个负根，求 $a$ 的取值范围。",
            "answer": "$a<-1$",
            "solutions": [{"label": "解法一", "steps": "Δ≥0，x1+x2<0，x1x2>0"}]})["question"]["id"]

        rel = self.ok("POST", "/api/relations", {
            "from_id": S["q1"], "to_id": S["q2"], "rel_type": "cond_variant",
            "note": "两根由正改负", "confirmed": 1})
        S["rel_id"] = rel["id"]

        body = self.detail(S["q1"])
        self.assertEqual([r["id"] for r in body["relations"]["out"]], [S["rel_id"]])
        self.assertEqual(body["relations"]["in"], [])
        self.assertEqual(body["relations"]["out"][0]["question"]["id"], S["q2"])
        self.assertIn("负根", body["relations"]["out"][0]["question"]["stem_preview"])

        body2 = self.detail(S["q2"])
        self.assertEqual([r["id"] for r in body2["relations"]["in"]], [S["rel_id"]])
        self.assertEqual(body2["relations"]["out"], [])

        # 自指被拒
        err = self.call("POST", "/api/relations", {
            "from_id": S["q1"], "to_id": S["q1"], "rel_type": "cond_variant"}, 400)
        self.assertEqual(err["error"]["code"], "bad_relation")

        # 成环被拒，并说明环路
        err = self.call("POST", "/api/relations", {
            "from_id": S["q1"], "to_id": S["q2"], "rel_type": "cond_variant"}, 409)
        self.assertEqual(err["error"]["code"], "duplicate_relation")

        S["q3"] = self.ok("POST", "/api/questions", {
            "qtype": "solve", "stem": "已知 $a\\in\\mathbb{R}$，方程 $x^2-2ax+1=0$ 有实根。"})["question"]["id"]
        self.ok("POST", "/api/relations", {
            "from_id": S["q2"], "to_id": S["q3"], "rel_type": "param", "note": "参数化"})
        err = self.call("POST", "/api/relations", {
            "from_id": S["q3"], "to_id": S["q1"], "rel_type": "cond_variant"}, 409)
        self.assertEqual(err["error"]["code"], "relation_cycle")
        self.assertIn("环路", err["error"]["message"])
        self.assertIn(S["q1"], err["error"]["message"])

        # 对称关系：两行，删除时一并清理
        sym = self.ok("POST", "/api/relations", {
            "from_id": S["q1"], "to_id": S["q3"], "rel_type": "same_method",
            "note": "同用判别式"})
        self.assertTrue(sym["reverse_id"])
        out_ids = [r["id"] for r in self.detail(S["q1"])["relations"]["out"]]
        in_ids = [r["id"] for r in self.detail(S["q3"])["relations"]["out"]]
        self.assertIn(sym["id"], out_ids)
        self.assertIn(sym["reverse_id"], in_ids)

        deleted = self.ok("DELETE", f"/api/relations/{sym['id']}")
        self.assertEqual(set(deleted["deleted"]), {sym["id"], sym["reverse_id"]})
        out_ids = [r["id"] for r in self.detail(S["q3"])["relations"]["out"]]
        self.assertNotIn(sym["reverse_id"], out_ids)

    # ------------------------------------------------------------ 09 合并
    def test_09_merge(self):
        dup = self.ok("POST", "/api/questions", {
            "short_name": "重复录入的题",
            "qtype": "solve",
            "stem": "已知 $a\\in\\mathbb{R}$，方程 $x^2-2ax+1=0$ 有两个正根，求 $a$ 的范围。",
            "occurrence": {"source_id": S["src_id"], "qno": "8-重复", "pending": 0}})
        dup_id = dup["question"]["id"]
        dup_occ = dup["occurrences"][0]["id"]
        self.ok("POST", f"/api/questions/{dup_id}/tags", {"tag_id": S["tag_k1"]})

        before = len(self.detail(S["q1"])["occurrences"])
        result = self.ok("POST", f"/api/questions/{dup_id}/merge",
                         {"into": S["q1"], "note": "同一题重复录入"})
        self.assertEqual(result["merged_into"], S["q1"])
        self.assertEqual(result["moved_occurrences"], 1)

        after = self.detail(S["q1"])
        self.assertEqual(len(after["occurrences"]), before + 1)
        occ_ids = [o["id"] for o in after["occurrences"]]
        self.assertIn(dup_occ, occ_ids)
        self.assertEqual(after["occurrences"][-1]["qno"], "8-重复")
        # 标签迁移后不重复
        tag_ids = [t["id"] for t in after["current_version"]["tags"]]
        self.assertEqual(tag_ids.count(S["tag_k1"]), 1)

        merged = self.detail(dup_id)
        self.assertEqual(merged["question"]["merged_into"], S["q1"])
        self.assertEqual(merged["question"]["status"], "inactive")
        self.assertEqual(merged["occurrences"], [])

        # 检索默认不出现被合并题
        listing = self.ok("GET", "/api/questions", keyword="范围")
        self.assertNotIn(dup_id, [i["id"] for i in listing["items"]])

        # 自合并与已合并
        err = self.call("POST", f"/api/questions/{S["q1"]}/merge", {"into": S["q1"]}, 400)
        self.assertEqual(err["error"]["code"], "bad_merge")
        err = self.call("POST", f"/api/questions/{dup_id}/merge", {"into": S["q1"]}, 409)
        self.assertEqual(err["error"]["code"], "already_merged")

        # 停用与恢复
        off = self.ok("POST", f"/api/questions/{S["q2"]}/deactivate", {"reason": "条件抄错"})
        self.assertEqual(off["question"]["status"], "inactive")
        self.assertEqual(off["question"]["status_reason"], "条件抄错")
        inactive = self.ok("GET", "/api/questions", status="inactive")
        self.assertIn(S["q2"], [i["id"] for i in inactive["items"]])
        self.assertNotIn(S["q1"], [i["id"] for i in inactive["items"]])
        on = self.ok("POST", f"/api/questions/{S["q2"]}/activate")
        self.assertEqual(on["question"]["status"], "active")
        self.assertIsNone(on["question"]["status_reason"])

        err = self.call("POST", f"/api/questions/{S["q2"]}/deactivate", {}, 400)
        self.assertEqual(err["error"]["code"], "missing_field")

    # ------------------------------------------------------------ 10 收题箱
    def test_10_inbox(self):
        item = self.ok("POST", "/api/inbox", {
            "raw_text": "已知 a∈R，方程 x^2-2ax+1=0 有两正根，求 a 的范围。",
            "file_name": "2024期末.pdf", "source_hint": "", "operator": "教师"})
        self.assertEqual(item["source_hint"], "待确认")
        self.assertEqual(item["status"], "pending")
        iid = item["id"]

        updated = self.ok("PATCH", f"/api/inbox/{iid}", {"status": "extracted",
                                                        "note": "已 OCR"})
        self.assertEqual(updated["status"], "extracted")
        self.assertEqual(updated["note"], "已 OCR")

        promoted = self.ok("POST", f"/api/inbox/{iid}/promote", {
            "short_name": "收题箱整理", "qtype": "solve",
            "stem": "已知 $a\\in\\mathbb{R}$，方程 $x^2-2ax+1=0$ 有两个正根，求 $a$ 的范围。",
            "answer": "$a>1$", "source_id": S["src_id"]})
        qid = promoted["question"]["id"]
        self.assertEqual(promoted["occurrences"][0]["source_id"], S["src_id"])
        self.assertIn("两正根", promoted["occurrences"][0]["raw_text"])

        back = self.ok("GET", f"/api/inbox/{iid}")
        self.assertEqual(back["status"], "organized")
        self.assertEqual(back["question_id"], qid)

        err = self.call("POST", f"/api/inbox/{iid}/promote", {
            "qtype": "solve", "stem": "再整理一次"}, 409)
        self.assertEqual(err["error"]["code"], "already_organized")

        listing = self.ok("GET", "/api/inbox", status="organized")
        self.assertIn(iid, [i["id"] for i in listing["items"]])

    # ------------------------------------------------------------ 11 查重与出处守卫
    def test_11_similar_and_guards(self):
        stem = self.detail(S["q1"])["current_version"]["stem"]
        found = self.ok("GET", "/api/search/similar", text=stem)
        ids = [i["id"] for i in found["items"]]
        self.assertIn(S["q1"], ids)
        top = next(i for i in found["items"] if i["id"] == S["q1"])
        self.assertGreater(top["ratio"], 0.98)
        self.assertLessEqual(len(found["items"]), 10)
        for item in found["items"]:
            self.assertGreaterEqual(item["ratio"], 0.55)
            self.assertIn("stem_preview", item)

        by_q = self.ok("GET", f"/api/questions/{S["q1"]}/similar")
        self.assertNotIn(S["q1"], [i["id"] for i in by_q["items"]])

        # 出处不能删到一条不剩
        while len(self.detail(S["q1"])["occurrences"]) > 1:
            first = self.detail(S["q1"])["occurrences"][0]
            self.ok("DELETE", f"/api/occurrences/{first['id']}")
        left = self.detail(S["q1"])["occurrences"]
        self.assertEqual(len(left), 1)
        err = self.call("DELETE", f"/api/occurrences/{left[0]['id']}", None, 409)
        self.assertEqual(err["error"]["code"], "last_occurrence")

        # 补一条出处后可以继续删
        extra = self.ok("POST", "/api/occurrences", {
            "question_id": S["q1"], "source_id": S["src_id"], "qno": "12"})
        self.ok("DELETE", f"/api/occurrences/{left[0]['id']}")
        self.assertEqual(len(self.detail(S["q1"])["occurrences"]), 1)
        self.assertEqual(self.detail(S["q1"])["occurrences"][0]["id"], extra["id"])

        # 版本号越界 404
        err = self.call("GET", f"/api/questions/{S["q1"]}/versions/99", None, 404)
        self.assertEqual(err["error"]["code"], "not_found")
        # 题目不存在 404
        err = self.call("GET", "/api/questions/Q999999", None, 404)
        self.assertEqual(err["error"]["message"], "题目 Q999999 不存在")

    # ------------------------------------------------------------ 12 使用排除
    def test_12_exclude_audience(self):
        """§11 接口不在本域，直接写库构造使用记录，验证 §8 的排除与 used_on。"""
        import sqlite3
        from app.db import today

        saved = self.detail(S['q1'])
        snapshot = {'collection': {'id': 'COLTEST', 'title': '单调性练习'},
                    'items': [{'id': 'ITMTEST', 'question_id': S['q1'],
                               'question_version_id': S['v3']}],
                    'questions': {S['q1']: {'question': saved['question'],
                                             'version': saved['current_version']}}}
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("INSERT INTO audience(id, kind, name, created_at) "
                         "VALUES('AUDTEST', 'class', '高一(3)班', datetime('now'))")
            conn.execute("INSERT INTO collection(id, title, created_at, updated_at) "
                         "VALUES('COLTEST', '单调性练习', datetime('now'), datetime('now'))")
            conn.execute("INSERT INTO release(id, collection_id, snapshot, created_at) "
                         "VALUES('RLSTEST', 'COLTEST', ?, datetime('now'))",
                         (json.dumps(snapshot, ensure_ascii=False),))
            conn.execute("INSERT INTO collection_item(id, collection_id, question_id, "
                         "question_version_id, created_at) VALUES('ITMTEST', 'COLTEST', ?, ?, "
                         "datetime('now'))", (S["q1"], S["v3"]))
            conn.execute("INSERT INTO usage_log(id, release_id, audience_id, used_on, purpose, "
                         "created_at) VALUES('USGTEST', 'RLSTEST', 'AUDTEST', ?, "
                         "'class', datetime('now'))", (today(),))
            conn.commit()
        finally:
            conn.close()

        detail = self.detail(S["q1"])
        self.assertEqual(len(detail["usage"]), 1)
        self.assertEqual(detail["usage"][0]["release_id"], "RLSTEST")
        self.assertEqual(detail["usage"][0]["collection_title"], "单调性练习")
        self.assertEqual(detail["usage"][0]["audience"], "高一(3)班")
        self.assertEqual(detail["usage"][0]["purpose"], "class")

        kept = self.ok("GET", "/api/questions", exclude_audience="AUDTEST")
        self.assertNotIn(S["q1"], [i["id"] for i in kept["items"]])
        # 换一个对象则不受影响
        other = self.ok("GET", "/api/questions", exclude_audience="AUDOTHER")
        self.assertIn(S["q1"], [i["id"] for i in other["items"]])
        # exclude_days=0 仍能排除当天使用的题
        self.assertNotIn(S["q1"], [i["id"] for i in
                                   self.ok("GET", "/api/questions",
                                           exclude_audience="AUDTEST", exclude_days=0)["items"]])

        # used_on 出现在检索条目里
        listing = self.ok("GET", "/api/questions", keyword="不相等的正根")
        row = next(i for i in listing["items"] if i["id"] == S["q1"])
        self.assertIsNotNone(row["used_on"])
        # 按使用时间排序不报错
        used = self.ok("GET", "/api/questions", sort="used")
        self.assertIn(S["q1"], [i["id"] for i in used["items"]])
        by_diff = self.ok("GET", "/api/questions", sort="difficulty")
        self.assertIn(S["q1"], [i["id"] for i in by_diff["items"]])

    # ------------------------------------------------------------ 13 解法生命周期
    def test_13_solution_lifecycle(self):
        cur = self.detail(S["q1"])
        vid = cur["current_version"]["id"]
        self.assertTrue(cur["eligibility"]["usable"])

        added = self.ok("POST", f"/api/versions/{vid}/solutions", {
            "label": "解法二", "approach": "分离参数", "steps": "a=(x^2+1)/(2x)，求值域",
            "method_tags": [S["tag_m1"]]})
        sid = added["id"]
        self.assertEqual(added["is_primary"], 0)
        self.assertEqual([t["id"] for t in added["tags"]], [S["tag_m1"]])

        # 新增解法未审校，整项回到 pending，题目不再可用
        body = self.detail(S["q1"])
        self.assertEqual(body["review_status"]["solution"], "pending")
        self.assertFalse(body["eligibility"]["usable"])
        self.assertIn("解析尚未审校", body["eligibility"]["missing"])

        # 设为选用解法：旧的自动清零
        self.ok("POST", f"/api/solutions/{sid}/primary")
        body = self.detail(S["q1"])
        primaries = [s["id"] for s in body["current_version"]["solutions"] if s["is_primary"]]
        self.assertEqual(primaries, [sid])

        patched = self.ok("PATCH", f"/api/solutions/{sid}", {
            "label": "解法二（分离参数）", "teacher_tip": "注意 x=0 不在定义域"})
        self.assertEqual(patched["label"], "解法二（分离参数）")
        self.assertEqual(patched["teacher_tip"], "注意 x=0 不在定义域")

        # 同版本内 is_primary 只能一个
        first = next(s["id"] for s in body["current_version"]["solutions"] if s["id"] != sid)
        self.ok("PATCH", f"/api/solutions/{first}", {"is_primary": 1})
        body = self.detail(S["q1"])
        primaries = [s["id"] for s in body["current_version"]["solutions"] if s["is_primary"]]
        self.assertEqual(primaries, [first])

        # 新解法通过后恢复可用；任一解法 fail 则整项 fail
        self.ok("POST", "/api/reviews", {"target_type": "solution", "target_id": sid,
                                         "item": "solution", "result": "pass"})
        self.assertTrue(self.detail(S["q1"])["eligibility"]["usable"])
        self.ok("POST", "/api/reviews", {"target_type": "solution", "target_id": first,
                                         "item": "solution", "result": "fail"})
        body = self.detail(S["q1"])
        self.assertEqual(body["review_status"]["solution"], "fail")
        self.assertFalse(body["eligibility"]["usable"])
        self.ok("POST", "/api/reviews", {"target_type": "solution", "target_id": first,
                                         "item": "solution", "result": "pass"})
        self.assertTrue(self.detail(S["q1"])["eligibility"]["usable"])

        # 删除非主解法
        self.ok("DELETE", f"/api/solutions/{sid}")
        body = self.detail(S["q1"])
        self.assertEqual(len(body["current_version"]["solutions"]), 1)
        self.assertEqual(body["current_version"]["solutions"][0]["id"], first)

        # 新版本显式传入 solutions 时改用传入的
        body = self.ok("POST", f"/api/questions/{S['q1']}/versions", {
            "solutions": [{"label": "唯一解法", "steps": "直接配方"}],
            "change_note": "重写解法"})
        self.assertEqual(body["current_version"]["version_no"], 4)
        sols = body["current_version"]["solutions"]
        self.assertEqual(len(sols), 1)
        self.assertEqual(sols[0]["label"], "唯一解法")
        self.assertEqual(sols[0]["is_primary"], 1)
        # 新版本没有审校记录，回到待审
        self.assertEqual(body["review_status"]["solution"], "pending")
        self.assertFalse(body["eligibility"]["usable"])

    # ------------------------------------------------------------ 14 各处修改与删除
    def test_14_patch_and_delete(self):
        # 资料
        patched = self.ok("PATCH", f"/api/sources/{S['src_id']}", {"edition": "2024 修订版",
                                                                  "note": "含答案页"})
        self.assertEqual(patched["edition"], "2024 修订版")

        # 出处
        occ = self.detail(S["q1"])["occurrences"][0]
        patched = self.ok("PATCH", f"/api/occurrences/{occ['id']}",
                          {"qno": "8(1)", "print_page": "13"})
        self.assertEqual(patched["qno"], "8(1)")
        self.assertEqual(patched["print_page"], "13")

        # 出处不允许既无 source 又无 pending
        err = self.call("PATCH", f"/api/occurrences/{occ['id']}",
                        {"source_id": None, "pending": 0}, 400)
        self.assertEqual(err["error"]["code"], "bad_occurrence")

        # 标签改名与重名
        patched = self.ok("PATCH", f"/api/tags/{S['tag_k2']}", {"definition": "Δ=b^2-4ac"})
        self.assertEqual(patched["definition"], "Δ=b^2-4ac")
        err = self.call("PATCH", f"/api/tags/{S['tag_k2']}",
                        {"name": "一元二次方程"}, 409)
        self.assertEqual(err["error"]["code"], "duplicate_tag")

        # 摘标签
        before = self.detail(S["q1"])["current_version"]["tags"]
        target = next(t for t in before if t["id"] == S["tag_k2"])
        removed = self.ok("DELETE", f"/api/questions/{S['q1']}/tags/{S['tag_k2']}",
                          role=target["role"])
        self.assertEqual(removed["removed"], 1)
        self.assertNotIn(S["tag_k2"],
                         [t["id"] for t in removed["tags"]])
        err = self.call("DELETE", f"/api/questions/{S['q1']}/tags/{S['tag_k2']}",
                        None, 404)
        self.assertEqual(err["error"]["code"], "not_found")
        self.ok("POST", f"/api/questions/{S['q1']}/tags",
                {"tag_id": S["tag_k2"], "role": "secondary"})

        # 关系：改说明与确认，对称关系两行同步
        sym = self.ok("POST", "/api/relations", {
            "from_id": S["q1"], "to_id": S["q2"], "rel_type": "error_contrast",
            "note": "条件正负相反"})
        patched = self.ok("PATCH", f"/api/relations/{sym['id']}",
                          {"note": "条件正负相反，易混", "confirmed": 1})
        self.assertEqual(patched["confirmed"], 1)
        out = next(r for r in self.detail(S["q1"])["relations"]["out"]
                   if r["id"] == sym["id"])
        rev = next(r for r in self.detail(S["q2"])["relations"]["out"]
                   if r["id"] == sym["reverse_id"])
        self.assertEqual(out["note"], rev["note"])
        self.assertEqual(out["confirmed"], rev["confirmed"])
        self.ok("DELETE", f"/api/relations/{sym['id']}")

        # 非法 rel_type
        err = self.call("POST", "/api/relations", {
            "from_id": S["q1"], "to_id": S["q2"], "rel_type": "unknown"}, 400)
        self.assertEqual(err["error"]["code"], "bad_field")

        # 解法方法标签
        sol = self.detail(S["q1"])["current_version"]["solutions"][0]
        tagged = self.ok("POST", f"/api/solutions/{sol['id']}/tags",
                         {"tag_id": S["tag_k1"]})
        self.assertIn(S["tag_k1"], [t["id"] for t in tagged["tags"]])
        err = self.call("POST", f"/api/solutions/{sol['id']}/tags",
                        {"tag_id": "TAG999999"}, 404)
        self.assertEqual(err["error"]["code"], "not_found")


if __name__ == "__main__":
    unittest.main(verbosity=2)
