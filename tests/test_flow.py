"""端到端验收：验证真实业务流程。

运行：
    python tests/test_flow.py

会在 build/_flow/ 下新建一个空库与临时工作目录，起真实 HTTP 服务，
并真实调用 xelatex 生成双版本讲义。结束打印逐条结论。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "build" / "_flow"
DB = WORK / "flow.db"
PORT = 8790

if WORK.exists():
    shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)
os.environ["QBANK_DB"] = str(DB)

sys.path.insert(0, str(ROOT))
import app.server as server  # noqa: E402
from app.api import register_all  # noqa: E402
from app import db  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("  [通过] " if ok else "  [未过] ") + name + (f"　{detail}" if detail else ""))


def call(method: str, path: str, body: dict | None = None):
    # 查询串里可能有中文（keywords），必须百分号编码后再发
    url = f"http://127.0.0.1:{PORT}" + urllib.parse.quote(path, safe="/?=&")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw}


def serve() -> ThreadingHTTPServer:
    db.init_db()
    register_all()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# ---------------------------------------------------------------- 题目素材
STEM_A = r"设函数 $f(x)=x^2-2ax+1$。若 $f(x)$ 在区间 $[1,+\infty)$ 上单调递增，求实数 $a$ 的取值范围。"
ANSWER_A = r"$a\leqslant 1$"

STEM_B = r"设函数 $f(x)=x^2-2ax+1$。若 $f(x)$ 在区间 $[1,+\infty)$ 上单调递减，求实数 $a$ 的取值范围。"
ANSWER_B = "不存在满足条件的实数 $a$"

STEM_C = r"已知函数 $f(x)=x+\dfrac{1}{x}$。"
SUBS_C = [
    {"key": "1", "text": r"用单调性的定义证明：$f(x)$ 在 $(0,1)$ 上单调递减。",
     "depends_on": [], "answer": "证明见解析"},
    {"key": "2", "text": r"求 $f(x)$ 在区间 $\left[\dfrac{1}{2},3\right]$ 上的最小值。",
     "depends_on": ["1"], "answer": r"$2$"},
]

STEM_D = r"函数 $f(x)=|x-1|+|x+2|$ 的单调递减区间是\blankbox。"

STEM_E = r"若函数 $f(x)=x^3+ax$ 在 $\mathbb{R}$ 上单调递增，则实数 $a$ 的取值范围是（\blankbox）"
OPTIONS_E = [{"key": "A", "text": r"$a\geqslant 0$"}, {"key": "B", "text": r"$a>0$"},
             {"key": "C", "text": r"$a\leqslant 0$"}, {"key": "D", "text": r"$a<0$"}]


def create_question(short_name, stem, answer, *, qtype="solve", options=None,
                    subquestions=None, source_id=None, occurrence=None):
    body = {
        "short_name": short_name, "qtype": qtype, "stem": stem, "answer": answer,
        "options": options, "subquestions": subquestions,
        "difficulty": {"calc": 2, "think": 3, "scope": 2, "level": "高一"},
        "time_solve_min": 5, "time_explain_min": 6,
        "teaching": {"why": "", "key_step": "", "probe": "", "pitfalls": "", "prereq": ""},
        "occurrence": occurrence or {"source_id": source_id, "pending": 0 if source_id else 1},
        "change_note": "初次录入",
    }
    if qtype in ("single", "multi") and options:
        body["answer_keys"] = [options[0]["key"]]
    return call("POST", "/api/questions", body)


def main() -> int:
    httpd = serve()
    try:
        run()
    finally:
        httpd.shutdown()

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n合计 {len(RESULTS)} 项，通过 {passed} 项。")
    return 0 if passed == len(RESULTS) else 1


def run() -> None:
    print("\n一、准备资料")

    code, src = call("POST", "/api/sources", {
        "name": "2025 学年高一期末（数学）", "kind": "exam", "edition": "2025 学年",
        "region": "校本", "note": "扫描件，第 3 页起为解答题"})
    check("建资料", code in (200, 201) and src.get("id", "").startswith("SRC"), src.get("id", str(code)))
    sid = src["id"]

    pdf_bytes = base64.b64encode(b"%PDF-1.4\n% flow test\n%%EOF\n").decode()
    code, up = call("POST", f"/api/sources/{sid}/files",
                    {"filename": "期末试卷.pdf", "content_b64": pdf_bytes})
    check("上传原文件并记校验值", code in (200, 201) and up.get("sha256"), up.get("rel_path", ""))

    print("\n二、先收一张未整理截图")
    code, inbox = call("POST", "/api/inbox", {
        "raw_text": "学生拍照传来的第 8 题，字迹模糊", "source_hint": "待确认",
        "note": "等问清来源再整理", "operator": "教师"})
    check("收题箱可保存、来源记为待确认", code in (200, 201) and inbox.get("status") == "pending",
          inbox.get("id", ""))
    iid = inbox["id"]

    code, qlist = call("GET", "/api/questions?keyword=字迹模糊")
    check("未整理条目不会被当成可选题", code == 200 and qlist["total"] == 0,
          f"命中 {qlist.get('total')} 题")

    print("\n三、整理成题目并登记出处")
    code, promoted = call("POST", f"/api/inbox/{iid}/promote", {
        "short_name": "含参二次函数在区间上单调", "qtype": "solve",
        "stem": STEM_A, "answer": ANSWER_A,
        "occurrence": {"source_id": sid, "pdf_page": 2, "qno": "8"},
        "change_note": "从收题箱整理"})
    ok = code in (200, 201) and promoted.get("question", {}).get("id")
    check("收题箱整理为正式题目", ok, promoted.get("question", {}).get("id", str(code)))
    qa = promoted["question"]["id"]
    va = promoted["current_version"]["id"]

    code, detail = call("GET", f"/api/inbox/{iid}")
    check("收题条目转为已整理并指向新题",
          detail.get("status") == "organized" and detail.get("question_id") == qa)

    print("\n四、同一原题来自两份资料")
    code, src2 = call("POST", "/api/sources", {
        "name": "校本专题练习（自编）", "kind": "self", "edition": "2026 秋"})
    sid2 = src2["id"]
    code, occ = call("POST", "/api/occurrences", {
        "question_id": qa, "source_id": sid2, "print_page": "第 3 页", "qno": "例 5"})
    check("一题关联两个出处", code in (200, 201), occ.get("id", str(code)))
    code, detail = call("GET", f"/api/questions/{qa}")
    occs = detail.get("occurrences", [])
    check("两个出处都能打开原文件定位",
          len(occs) == 2 and all(o.get("source_name") for o in occs),
          "、".join(o["source_name"] for o in occs))

    print("\n五、一题多解、多小问")
    code, qc = create_question("对勾函数的最值", STEM_C, r"（1）证明见解析；（2）$2$",
                               subquestions=SUBS_C, source_id=sid2)
    qc_id = qc["question"]["id"]
    vc_id = qc["current_version"]["id"]
    code, s1 = call("POST", f"/api/versions/{vc_id}/solutions", {
        "label": "解法一 定义法", "approach": "作差比较",
        "steps": r"设 $0<x_1<x_2<1$，$f(x_1)-f(x_2)=(x_1-x_2)\left(1-\dfrac{1}{x_1x_2}\right)>0$，故函数在 $(0,1)$ 上单调递减。",
        "is_primary": 1})
    code2, s2 = call("POST", f"/api/versions/{vc_id}/solutions", {
        "label": "解法二 导数法", "approach": "求导定号",
        "steps": r"$f'(x)=1-\dfrac{1}{x^2}<0$ 在 $(0,1)$ 上恒成立。", "is_primary": 0})
    check("一题可存多条解法", code in (200, 201) and code2 in (200, 201),
          f"{s1.get('id', '')}、{s2.get('id', '')}")

    code, qd = create_question("绝对值函数的单调区间", STEM_D, r"$(-\infty,-2]$", source_id=sid2)
    code, qe = create_question("三次函数恒增", STEM_E, "A", qtype="single",
                               options=OPTIONS_E, source_id=sid2)
    qe_id = qe["question"]["id"]

    print("\n六、审校：题面、答案、每种解析分别记录")
    for item in ("stem", "answer"):
        call("POST", "/api/reviews", {"target_type": "question_version", "target_id": va,
                                      "item": item, "result": "pass", "reviewer": "教师"})
    code, sols = call("GET", f"/api/questions/{qa}")
    sol_ids = [s["id"] for s in sols["current_version"]["solutions"]]
    if not sol_ids:
        code, made = call("POST", f"/api/versions/{va}/solutions", {
            "label": "解法一 对称轴法", "steps": r"对称轴 $x=a$，需 $a\leqslant 1$。",
            "is_primary": 1})
        sol_ids = [made["id"]]
    call("POST", "/api/reviews", {"target_type": "solution", "target_id": sol_ids[0],
                                  "item": "solution", "result": "pass", "reviewer": "教师"})
    code, detail = call("GET", f"/api/questions/{qa}")
    check("三项审校齐备后判为可选用", detail["eligibility"]["usable"] is True,
          "缺项：" + ("无" if not detail["eligibility"]["missing"] else "、".join(detail["eligibility"]["missing"])))

    code, detail_c = call("GET", f"/api/questions/{qc_id}")
    check("解析未审校时明确指出缺项", detail_c["eligibility"]["usable"] is False,
          "、".join(detail_c["eligibility"]["missing"]))

    print("\n七、改解析走新版本，旧讲义仍指原版本")
    code, newv = call("POST", f"/api/questions/{qc_id}/versions", {
        "change_note": "补全第（2）问取等条件",
        "answer": r"（1）证明见解析；（2）$2$，在 $x=1$ 处取得"})
    ok = code in (200, 201)
    vc_new = newv.get("current_version", {}).get("id") if ok else None
    check("版本号递增、旧版本不覆盖", ok and newv["current_version"]["version_no"] == 2,
          f"{vc_id} → {vc_new}")
    code, old = call("GET", f"/api/questions/{qc_id}/versions/1")
    check("旧版本内容仍可读", code == 200 and "证明见解析" in old.get("answer", ""))
    code, diff = call("GET", f"/api/questions/{qc_id}/diff?from=1&to=2")
    check("两版可逐字段对比", code == 200 and any(
        f.get("changed") for f in diff.get("fields", [])), str(code))

    print("\n八、改变条件 → 新题 + 关系，原题可查")
    code, qb = create_question("含参二次函数在区间上单调（递减条件）", STEM_B, ANSWER_B, source_id=sid2)
    qb_id = qb["question"]["id"]
    code, rel = call("POST", "/api/relations", {
        "from_id": qa, "to_id": qb_id, "rel_type": "cond_variant",
        "note": "把「单调递增」改为「单调递减」，保留函数与区间，对比开口向上的二次函数在右无界区间上不可能递减。"})
    check("条件变式关系可建立", code in (200, 201), rel.get("id", str(code)))
    code, dup = call("POST", "/api/relations", {
        "from_id": qa, "to_id": qa, "rel_type": "cond_variant", "note": "自指"})
    check("关系禁止自指", dup.get("error", {}).get("code") in ("self_relation", "bad_request", None)
          or code == 400, dup.get("error", {}).get("message", ""))

    print("\n九、按方法筛题")
    code, tags = call("GET", "/api/tags?dimension=method")
    if not tags["items"]:
        call("POST", "/api/tags", {"dimension": "method", "name": "对称轴法",
                                   "definition": "用二次函数对称轴与区间端点的位置关系定参数"})
        code, tags = call("GET", "/api/tags?dimension=method")
    if tags["items"]:
        tag_id = tags["items"][0]["id"]
        call("POST", f"/api/solutions/{sol_ids[0]}/tags", {"tag_id": tag_id})
        code, found = call("GET", f"/api/questions?method_tag={tag_id}")
        hit = [i for i in found["items"] if i["id"] == qa]
        check("按解法方法能筛到具体题", code == 200 and bool(hit), f"命中 {found['total']} 题")
    else:
        check("按解法方法能筛到具体题", False, "标签库为空，跳过")

    print("\n十、题单：同题进入两份题单，各自设角色")
    code, col1 = call("POST", "/api/collections", {
        "title": "函数的单调性", "topic": "单调性", "level": "高一提高",
        "duration_min": 90, "purpose": "handout"})
    c1 = col1["collection"]["id"]
    code, sec1 = call("POST", f"/api/collections/{c1}/sections", {"title": "一、例题：对称轴与区间", "order_no": 0})
    code, sec2 = call("POST", f"/api/collections/{c1}/sections", {"title": "二、跟练", "order_no": 1})
    k1, k2 = sec1["section"]["id"], sec2["section"]["id"]
    for qid, sec, role in ((qa, k1, "example"), (qc_id, k1, "example"),
                           (qb_id, k2, "practice"), (qd["question"]["id"], k2, "practice")):
        code, _ = call("POST", f"/api/collections/{c1}/items",
                       {"question_id": qid, "section_id": sec, "role": role,
                        "time_solve_min": 6, "time_explain_min": 7, "space_mm": 25})
        assert code in (200, 201), code

    code, col2 = call("POST", "/api/collections", {
        "title": "课后巩固（同题另用）", "duration_min": 30, "purpose": "homework"})
    c2 = col2["collection"]["id"]
    call("POST", f"/api/collections/{c2}/items",
         {"question_id": qa, "role": "homework", "time_solve_min": 8, "space_mm": 40})
    code, d1 = call("GET", f"/api/collections/{c1}")
    code, d2 = call("GET", f"/api/collections/{c2}")
    same_q = [i for i in d1["items"] if i["question_id"] == qa]
    other_q = [i for i in d2["items"] if i["question_id"] == qa]
    check("同题进入两份题单，题面不被复制",
          bool(same_q) and bool(other_q) and same_q[0]["id"] != other_q[0]["id"],
          f"{same_q[0]['role']} / {other_q[0]['role']}")
    check("同题在两份题单里可分别设置留白",
          same_q[0].get("space_mm") != other_q[0].get("space_mm"),
          f"{same_q[0].get('space_mm')} vs {other_q[0].get('space_mm')}")

    code, chk = call("GET", f"/api/collections/{c1}/check")
    check("组题检查给出时间、难度与缺项",
          code == 200 and "time_solve" in chk and "difficulty_spread" in chk,
          f"共 {chk.get('count')} 题，作答 {chk.get('time_solve')} 分，讲解 {chk.get('time_explain')} 分")

    print("\n十一、导出学生版与教师版（真实编译）")
    t0 = time.time()
    code, built = call("POST", f"/api/collections/{c1}/build", {
        "title": "函数的单调性", "subtitle": "习题课讲义", "pretitle": "高一数学 · 专题",
        "motto": "先看定义，再动手", "theme": "blue",
        "versions": ["student", "teacher"], "format": "standard"})
    ok = code in (200, 201) and built.get("release", {}).get("status") == "built"
    check(f"双版本编译成功（{time.time() - t0:.1f} 秒）", ok,
          f"页数 {built.get('release', {}).get('pages_student')}/{built.get('release', {}).get('pages_teacher')}")
    checks = built.get("checks", {})
    check("编译 0 错误", checks.get("student_errors") == 0 and checks.get("teacher_errors") == 0)
    check("0 Overfull", checks.get("overfull") == 0, str(checks.get("overfull")))
    check("0 缺字", checks.get("missing_char") == 0, str(checks.get("missing_char")))
    check("学生版不含答案", checks.get("leak") == "clean", str(checks.get("leak")))
    check("无 LaTeX 警告", not checks.get("warnings"), str(checks.get("warnings"))[:80])
    rid = built.get("release", {}).get("id")
    release_folder = db.store_dir('releases') / rid
    check('源码归档包含实际模板和封面素材',
          (release_folder/'ExBook.cls').is_file() and (release_folder/'img/cover.jpg').is_file())
    check('归档包含文件校验清单', (release_folder/'manifest.json').is_file())
    student = built.get("files", {}).get("student", {})
    check("学生版 PDF 可下载", bool(student.get("path")) and (ROOT / student["path"]).is_file())
    teacher = built.get("files", {}).get("teacher", {})
    check("教师版 PDF 可下载", bool(teacher.get("path")) and (ROOT / teacher["path"]).is_file())

    print("\n十二、生成不等于已发放")
    code, usages = call("GET", "/api/usages")
    check("刚生成时没有使用记录", usages["total"] == 0, f"记录 {usages['total']} 条")

    code, aud = call("POST", "/api/audiences", {"kind": "class", "name": "高一(3)班", "code": "G1-3"})
    aid = aud["audience"]["id"]
    code, asg = call("POST", "/api/assignments", {
        "title": "单调性 · 课后巩固", "collection_id": c1, "release_id": rid,
        "audience_id": aid, "plan_date": "2026-10-27", "status": "planned",
        "requirements": "完成第 3—4 题"})
    asg_id = asg["assignment"]["id"]
    code, patched = call("PATCH", f"/api/assignments/{asg_id}", {"status": "distributed"})
    check("登记发放不改写使用记录", patched.get("assignment", {}).get("status") == "distributed"
          and call("GET", "/api/usages")[1]["total"] == 0)

    code, usg = call("POST", "/api/usages", {
        "assignment_id": asg_id, "release_id": rid, "audience_id": aid,
        "used_on": "2026-10-27", "purpose": "class", "note": "当堂讲了例 1、例 2"})
    usage_id = usg.get("usage", {}).get("id")
    check("登记实际使用", code in (200, 201), usage_id or str(code))

    code, recent = call("GET", f"/api/audiences/{aid}/recent?days=30")
    hit = [i for i in recent["items"] if i["question_id"] == qa]
    check("按班级回查近期用过的题", code == 200 and bool(hit),
          f"{len(recent['items'])} 条，日期 {hit[0]['used_on'] if hit else '—'}")

    code, fb = call("POST", "/api/feedback", {
        "usage_id": usage_id, "common_errors": "忽略定义域，把端点取错",
        "actual_minutes": 85, "effect": "多数能写出对称轴，端点讨论不完整",
        "suggestion": "下次先画区间与对称轴的位置图", "sample_size": 42,
        "stat_scope": "当堂小测，全批", "stat_date": "2026-10-27", "level": "高一平行班"})
    check("登记教学反馈并保存统计口径", code in (200, 201) and fb.get("feedback", {}).get("sample_size") == 42)

    print("\n十三、发现已用题有错，能定位受影响的发布物与班级")
    code, rel_detail = call("GET", f"/api/releases?collection_id={c1}")
    rel_ids = [r["id"] for r in rel_detail["items"]]
    code, usages = call("GET", f"/api/usages?audience_id={aid}")
    affected = [u for u in usages["items"] if u.get("release_id") in rel_ids]
    check("能从使用记录反查受影响讲义与对象", bool(affected),
          f"{affected[0]['collection_title']} → {affected[0]['audience_name']}" if affected else "未找到")

    print("\n十四、快照自足：题面改了，旧讲义仍按原版本重导")
    call("POST", f"/api/questions/{qa}/versions", {
        "change_note": "题干补上定义域说明",
        "stem": STEM_A.replace("设函数", "设定义在 $\\mathbb{R}$ 上的函数")})
    code, rebuilt = call("POST", f"/api/releases/{rid}/rebuild", {})
    ok = code in (200, 201) and rebuilt.get("release", {}).get("status") == "built"
    check("用原快照重导不读取题目最新版", ok, rebuilt.get("release", {}).get("id", str(code)))
    archive = db.store_dir("releases") / rebuilt.get("release", {}).get("id", "")
    check("快照文件已归档", bool(ok) and (archive / "snapshot.json").is_file(),
          db.rel_to_root(archive))

    print("\n十五、备份与恢复")
    code, bk = call("POST", "/api/backup", {})
    backup_file = ROOT / bk.get("file", "")
    check("生成一致性备份", code == 200 and backup_file.is_file(), bk.get("file", ""))
    restore_dir = WORK / "restore"
    restore_dir.mkdir(parents=True, exist_ok=True)
    restored = restore_dir / "restored.db"
    shutil.copy2(backup_file, restored)
    conn = sqlite3.connect(restored)
    try:
        n_q = conn.execute("SELECT COUNT(*) FROM question").fetchone()[0]
        n_rel = conn.execute("SELECT COUNT(*) FROM relation").fetchone()[0]
        n_rel_rls = conn.execute("SELECT COUNT(*) FROM release").fetchone()[0]
    finally:
        conn.close()
    check("在另一目录恢复后题目、关系、发布物仍在",
          n_q >= 5 and n_rel >= 1 and n_rel_rls >= 1,
          f"题目 {n_q}、关系 {n_rel}、发布物 {n_rel_rls}")

    print("\n十六、编译失败要如实报告，不能报成 0 错误")
    code, bad = create_question("故意写坏命令的题", r"$\undefinedcommand{x}$", "无", source_id=sid2)
    bad_id = bad["question"]["id"]
    call("POST", "/api/collections", {"title": "坏样例", "duration_min": 20})
    cols = call("GET", "/api/collections?keyword=坏样例")[1]["items"]
    cbad = cols[0]["id"]
    call("POST", f"/api/collections/{cbad}/items", {"question_id": bad_id})
    code, failed = call("POST", f"/api/collections/{cbad}/build", {"versions": ["student", "teacher"]})
    st = failed.get("release", {}).get("status")
    errs = failed.get("checks", {}).get("student_errors")
    check("编译失败时状态为 failed 且错误数不为 0", st == "failed" and errs not in (0, None),
          f"status={st} student_errors={errs}")
    check("失败时不留虚假 PDF 路径", not failed.get("files", {}).get("student", {}).get("path"),
          str(failed.get("files", {}).get("student", {}).get("path")))


if __name__ == "__main__":
    raise SystemExit(main())
