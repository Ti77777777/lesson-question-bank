"""快照 → 写文件 → 调用 TeX → 校验 → 落盘 → 登记发布物。

对外只有两个入口：
    build_release(collection_id, options)   按题单当前数据构建
    rebuild_release(release_id, options)    按已有快照重导（不读题目最新版）
两者都返回 docs/API.md §10 约定的响应体。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from app import db
from app.latex import exbook
from app.snapshots import entries

BUILD_ROOT = db.ROOT / "build"
RELEASE_ROOT = db.ROOT / "releases"
COMPILE_TIMEOUT = 180
TAIL_CHARS = 2000

VERSION_ORDER = ("student", "answer", "teacher")

VERSION_LABEL = {"student": "学生", "answer": "解答", "teacher": "解析"}


class CompileError(RuntimeError):
    """某一版编译失败，携带编译器输出尾部。"""

    def __init__(self, stage: str, output: str):
        super().__init__("%s 失败" % stage)
        self.stage = stage
        self.output = output or ""


# ---------------------------------------------------------------- 快照
def make_snapshot(collection_id: str, options: dict | None = None) -> dict:
    """把题单、分节、条目、题目版本、解法、素材、选项一次性取全。

    快照必须自足：重建时只读这份 JSON，不再回查题目最新版本。
    """
    collection = db.one("SELECT * FROM collection WHERE id = ?", (collection_id,))
    if collection is None:
        from app.server import ApiError
        raise ApiError(404, "not_found", "没有这个题单：%s" % collection_id)

    sections = db.all_(
        "SELECT * FROM collection_section WHERE collection_id = ? "
        "ORDER BY order_no, id", (collection_id,))
    items = db.all_(
        "SELECT * FROM collection_item WHERE collection_id = ? "
        "ORDER BY order_no, id", (collection_id,))

    questions: dict[str, dict] = {}
    question_versions: dict[str, dict] = {}
    answers: dict[str, str] = {}
    from app import provenance
    source_map=provenance.for_questions([item['question_id'] for item in items])
    for item in items:
        qid = item.get("question_id")
        vid = item.get("question_version_id")
        if vid in question_versions:
            continue
        question = db.one("SELECT * FROM question WHERE id = ?", (qid,)) or {"id": qid}
        version = db.one(
            "SELECT * FROM question_version WHERE id = ?",
            (item.get("question_version_id"),)) or {}
        solutions = db.all_(
            "SELECT * FROM solution WHERE question_version_id = ? "
            "ORDER BY order_no, id", (version.get("id"),))
        assets = []
        for ref in version.get("material_refs") or []:
            asset = db.one("SELECT * FROM asset WHERE id = ?", (ref,))
            if asset:
                assets.append(asset)
        entry = {
            "question": question,
            "version": version,
            "solutions": solutions,
            "assets": assets,
            "source_label": provenance.summary(source_map.get(qid,[]),vid),
        }
        question_versions[vid] = entry
        questions.setdefault(qid, entry)  # 保留旧消费者使用的按题目编号索引
        answers.setdefault(qid, str(version.get("answer") or ""))

    resolved = exbook.normalise_options(options, collection)
    return {
        "collection": collection,
        "sections": sections,
        "items": items,
        "questions": questions,
        "snapshot_schema": 2,
        "question_versions": question_versions,
        "answers": answers,
        "template_hash": exbook.template_hash(),
        "options": resolved,
        "built_at": db.now(),
    }


# ---------------------------------------------------------------- 编译
def _tools() -> dict:
    import glob
    candidates = [shutil.which('xelatex')]
    if os.name == 'nt':
        candidates = sorted(glob.glob('D:/texlive/*/bin/windows/xelatex.exe'),reverse=True) + candidates
    for candidate in candidates:
        if not candidate:continue
        xelatex=Path(candidate).resolve()
        converter=xelatex.with_name('xdvipdfmx.exe' if os.name=='nt' else 'xdvipdfmx')
        if converter.is_file():return {'xelatex':str(xelatex),'xdvipdfmx':str(converter)}
    return {'xelatex':None,'xdvipdfmx':None}


def _run(cmd: list[str], cwd: Path, env: dict) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=COMPILE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        partial = ""
        for part in (exc.stdout, exc.stderr):
            if part:
                partial += part if isinstance(part, str) else part.decode("utf-8", "replace")
        return 124, partial + "\n编译超时：超过 %d 秒未完成" % COMPILE_TIMEOUT
    except OSError as exc:
        return 127, "无法启动 %s：%s" % (cmd[0], exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


_PAGE_PATTERNS = (
    re.compile(r"(\d+)\s+pages?\b"),                 # xdvipdfmx: student.pdf: 6 pages
    re.compile(r"Output written on .*?\((\d+) pages?"),  # xelatex: Output written on … (6 pages
)


def _parse_pages(text: str) -> int | None:
    """页数取自编译器自己的输出；xelatex 的 Output written 作为兜底。"""
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return int(match.group(1))
    marks = re.findall(r"\[(\d+)\]", text or "")
    if marks:
        return max(int(m) for m in marks)
    return None


def _compile_version(work: Path, env: dict, tools: dict, version: str) -> dict:
    """跑 xelatex -no-pdf 若干遍，再 xdvipdfmx；任一步非 0 立即返回失败。

    遍数不固定为两遍：页脚有「第 N 页／共 M 页」，M 由 LastPage 标签得到，
    要等第二遍排版结束后才知道总页数；若第二遍又改变了分页，需要第三遍收敛。
    """
    tex = version + ".tex"
    output: list[str] = []

    if not tools.get("xelatex"):
        return {"ok": False, "stage": "未找到 xelatex",
                "output": "未找到 xelatex，请确认 MiKTeX 已安装", "pages": None}
    if not tools.get("xdvipdfmx"):
        return {"ok": False, "stage": "未找到 xdvipdfmx",
                "output": "未找到 xdvipdfmx，请确认 MiKTeX 已安装", "pages": None}

    log = work / (version + ".log")
    for index in (1, 2, 3):
        code, out = _run([tools["xelatex"], "-no-pdf", "-no-shell-escape", "-interaction=nonstopmode",
                          "-halt-on-error", "-file-line-error", tex], work, env)
        output.append(out)
        if code != 0:
            return {"ok": False, "stage": "xelatex 第 %d 遍" % index,
                    "output": "\n".join(output), "pages": None}
        if index >= 2:
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if "Rerun to get" not in text and "Label(s) may have changed" not in text:
                break

    code, out = _run([tools["xdvipdfmx"], "-q", version + ".xdv"], work, env)
    output.append(out)
    if code != 0:
        return {"ok": False, "stage": "xdvipdfmx", "output": "\n".join(output),
                "pages": None}

    pdf = work / (version + ".pdf")
    if not pdf.is_file() or pdf.stat().st_size == 0:
        return {"ok": False, "stage": "xdvipdfmx",
                "output": "\n".join(output) + "\n未生成 PDF：%s" % pdf.name,
                "pages": None}

    return {"ok": True, "stage": "", "output": "\n".join(output),
            "pages": _parse_pages(output[-1]) or _parse_pages("\n".join(output))}


# ---------------------------------------------------------------- 校验
_WARNING_START = re.compile(r"^LaTeX Warning:", re.M)


def _log_path(work: Path, version: str) -> Path:
    return work / (version + ".log")


def _read_log(work: Path, version: str) -> str:
    path = _log_path(work, version)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _log_warnings(text: str) -> list[str]:
    """取 LaTeX Warning 原文（含续行），按出现顺序。"""
    warnings: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("LaTeX Warning:"):
            continue
        chunk = [line.strip()]
        for follow in lines[index + 1:]:
            if not follow.strip():
                break
            chunk.append(follow.strip())
        warnings.append(" ".join(chunk))
    return warnings


_ERROR_PATTERNS = (
    re.compile(r"^!", re.M),                        # 默认模式：以 ! 开头
    re.compile(r"^[^\s:]+\.tex:\d+:", re.M),        # -file-line-error 模式：body.tex:31: …
)


def _log_errors(text: str) -> int:
    """数编译错误。

    build.py 用 -file-line-error 调 xelatex，此模式下错误行不再以 ! 开头，
    只认 ^! 会把所有错误数成 0（只剩「成功 0、失败 1」两种取值）。
    """
    return sum(len(pattern.findall(text)) for pattern in _ERROR_PATTERNS)


def _log_checks(work: Path, version: str) -> dict:
    text = _read_log(work, version)
    return {
        "errors": _log_errors(text),
        "overfull": len(re.findall(r"Overfull \\hbox", text)),
        "underfull": len(re.findall(r"Underfull \\hbox", text)),
        "missing_char": len(re.findall(r"Missing character", text)),
        "warnings": _log_warnings(text),
    }


_CJK = re.compile(r"[\u3400-\u9fff]")


def _fragment(text: str) -> str:
    """答案正文的前 16 个非空白字符；去掉 LaTeX 命令与花括号后再比。"""
    cleaned = re.sub(r"\\[A-Za-z]+", "", str(text or ""))
    cleaned = re.sub(r"[{}$\\]", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned[:16]


def _is_fingerprint(fragment: str) -> bool:
    """太短或纯 ASCII 的片段会在题干、选项里天然出现，不足以判定泄露。"""
    if len(fragment) < 6:
        return False
    if not _CJK.search(fragment) and len(fragment) < 12:
        return False
    return True


def _leak_check(snapshot: dict, sources: str) -> tuple[str, list[str]]:
    """学生版源码全文核对：答案/解法首段若出现即判泄露。"""
    hits: list[str] = []
    for entry in entries(snapshot):
        qid = (entry.get("question") or {}).get("id") or "未知题目"
        version = entry.get("version") or {}
        candidates: list[tuple[str, str]] = []
        if version.get("answer"):
            candidates.append(("答案", str(version["answer"])))
        for index, sol in enumerate(entry.get("solutions") or [], 1):
            if sol.get("steps"):
                candidates.append(("解法%d" % index, str(sol["steps"])))
        for sub in version.get("subquestions") or []:
            if sub.get("answer"):
                candidates.append(("小问答案", str(sub["answer"])))
        for kind, text in candidates:
            frag = _fragment(text)
            if not _is_fingerprint(frag):
                continue
            if frag in sources:
                hits.append("%s 的%s出现在学生版" % (qid, kind))
    return ("leaked" if hits else "clean"), hits


# ---------------------------------------------------------------- 目录与文件
def _stamp() -> str:
    return datetime.now(db.TZ).strftime("%Y%m%d-%H%M%S")


def _build_dir(collection_id: str) -> Path:
    base = BUILD_ROOT / ("%s-%s" % (collection_id, _stamp()))
    target = base
    index = 2
    while target.exists():
        target = base.with_name("%s-%d" % (base.name, index))
        index += 1
    target.mkdir(parents=True, exist_ok=True)
    (target / "tmp").mkdir(exist_ok=True)
    return target


def _prune_builds(keep: int = 10) -> None:
    """构建目录是可清理的中间产物，只留最近若干次，便于查编译失败的现场。"""
    try:
        dirs = sorted((p for p in BUILD_ROOT.iterdir() if p.is_dir() and not p.name.startswith("_")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in dirs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def _copy_template(work: Path, archive: Path | None = None) -> list[str]:
    notes: list[str] = []
    template_dir = archive if archive and (archive / 'ExBook.cls').is_file() else exbook.TEMPLATE_DIR
    if archive and template_dir != archive:
        notes.append('[重导] 历史文件未归档模板，本次使用当前模板，版式可能与原文件不同')
    class_file = template_dir / 'ExBook.cls'
    if not class_file.is_file():
        notes.append("[构建] 未找到模板类文件 templates/exbook/ExBook.cls")
        return notes
    shutil.copy2(class_file, work / "ExBook.cls")
    for name in (exbook.COVER_FILE, exbook.WATER_FILE):
        source = template_dir / name
        if source.is_file():
            (work / "img").mkdir(exist_ok=True)
            shutil.copy2(source, work / name)
        # Custom cover/watermark images are optional; the class has a neutral fallback.
    return notes


def _copy_assets(snapshot: dict, work: Path, archive: Path | None = None) -> tuple[set[str], list[str]]:
    """被引用的素材复制进构建目录 assets/，返回可用素材 ID 与提示。"""
    available: set[str] = set()
    notes: list[str] = []
    for asset, dest, rel in exbook.asset_targets(snapshot):
        archived_file = archive / dest if archive else None
        if archived_file is not None and archived_file.is_file():
            source = archived_file
        elif rel and (db.ROOT / rel).is_file():
            # 重导时归档缺图，退回当前项目的同名文件；版式看起来一样，
            # 内容可能已经被换过，必须留痕，不能静默替换。
            source = db.ROOT / rel
            notes.append("[构建] 归档缺素材，改用当前文件：%s" % rel)
        else:
            notes.append("[构建] 素材文件缺失：%s" % (rel or asset.get("id")))
            continue
        target = work / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        available.add(asset.get("id"))
    return available, notes


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 主流程
def _build(snapshot: dict, options: dict, action: str, archive: Path | None = None) -> dict:
    collection = snapshot.get("collection") or {}
    collection_id = collection.get("id") or ""
    work = _build_dir(collection_id)
    env = os.environ.copy()
    env.pop('DEEPSEEK_API_KEY', None)
    env['openin_any'] = 'p'
    env['openout_any'] = 'p'
    env["TEMP"] = str(work / "tmp")
    env["TMP"] = str(work / "tmp")

    notes = _copy_template(work, archive)
    available, asset_notes = _copy_assets(snapshot, work, archive)
    notes += asset_notes

    body_student = exbook.render_body(snapshot, options, "student", available)
    body_answer = exbook.render_body(snapshot, options, "answer", available)
    body_teacher = exbook.render_body(snapshot, options, "teacher", available)
    _write(work / "config.tex", exbook.render_config(options))
    _write(work / "body.tex", body_student)
    _write(work / "student.tex",
           exbook.render_document(snapshot, options, "student", available=available))
    _write(work / "answer.tex",
           exbook.render_document(snapshot, options, "answer",
                                  body=body_answer, available=available))
    _write(work / "teacher.tex",
           exbook.render_document(snapshot, options, "teacher",
                                  body=body_teacher, available=available))

    tools = _tools()
    if not tools.get("xelatex"):
        notes.append("未找到 xelatex，请确认 MiKTeX 已安装")

    results: dict[str, dict] = {}
    failure: str | None = None
    versions = options.get("versions") or list(VERSION_ORDER)
    for version in VERSION_ORDER:
        if version not in versions:
            results[version] = {"ok": False, "stage": "未请求", "output": "", "pages": None}
            continue
        if failure:
            results[version] = {"ok": False, "stage": "前序失败已停止", "output": "",
                                "pages": None}
            continue
        result = _compile_version(work, env, tools, version)
        results[version] = result
        if not result["ok"]:
            failure = "%s：%s" % (version, result["stage"])

    student_result = results.get("student") or {}
    answer_result = results.get("answer") or {}
    teacher_result = results.get("teacher") or {}
    requested = {"student", "answer", "teacher"} & set(versions)
    built = [v for v in requested if results.get(v, {}).get("ok")]
    status = "built" if len(built) == len(requested) else "failed"

    scans = {v: _log_checks(work, v) for v in VERSION_ORDER}

    def _errors(version: str):
        result = results.get(version) or {}
        if result.get("stage") in ("未请求", "前序失败已停止"):
            # 没编译过就不能报 0，否则等于谎称「干净」
            return None
        return max(scans[version]["errors"], 0 if result.get("ok") else 1)

    for version in VERSION_ORDER:
        stage = (results.get(version) or {}).get("stage")
        if stage == "前序失败已停止":
            notes.append("%s版未编译：前序版本编译失败，已停止" % VERSION_LABEL.get(version, version))
        # 「未请求」是用户自己没勾选，不作为警告写进 checks

    checks: dict = {
        "student_errors": _errors("student"),
        "answer_errors": _errors("answer"),
        "teacher_errors": _errors("teacher"),
        "overfull": sum(scans[v]["overfull"] for v in VERSION_ORDER),
        "underfull": sum(scans[v]["underfull"] for v in VERSION_ORDER),
        "missing_char": sum(scans[v]["missing_char"] for v in VERSION_ORDER),
        "warnings": [w for v in VERSION_ORDER for w in scans[v]["warnings"]][:20],
        "pages_student": student_result.get("pages"),
        "pages_answer": answer_result.get("pages"),
        "pages_teacher": teacher_result.get("pages"),
        "template_hash": snapshot.get("template_hash"),
        "build_dir": db.rel_to_root(work),
        "checked_at": db.now(),
    }

    # 学生版防泄露：核对学生版源码（student.tex 与其引用的 body.tex）
    student_source = (work / "student.tex").read_text(encoding="utf-8") + \
                     body_student
    leak, hits = _leak_check(snapshot, student_source)
    checks["leak"] = leak
    if hits:
        checks["warnings"] = (["学生版疑似泄露：" + hit for hit in hits] +
                              checks["warnings"])[:20]

    if status != "built":
        tail = ""
        for version in VERSION_ORDER:
            output = results.get(version, {}).get("output") or ""
            if output:
                tail = output
        checks["warnings"] = ([("编译失败（%s）" % failure)] +
                              [tail[-TAIL_CHARS:]] +
                              checks["warnings"])[:20]

    checks["warnings"] = checks["warnings"] + notes
    checks["warnings"] = checks["warnings"][:20]

    # ---- 落盘到 releases/<RLS>/
    with db.transaction() as conn:
        release_id = db.next_id(conn, "RLS")

    release_dir = db.store_dir("releases") / release_id
    if release_dir.exists():
        # 编号只有库回滚（用备份覆盖 data/qbank.db）才会重用，此时的旧目录
        # 可能已经发给学生。不能直接删，挪到 _superseded/ 留证再建新目录。
        superseded = release_dir.parent / "_superseded"
        superseded.mkdir(parents=True, exist_ok=True)
        stamp = _stamp()
        target = superseded / ("%s-%s" % (release_id, stamp))
        index = 2
        while target.exists():
            target = superseded / ("%s-%s-%d" % (release_id, stamp, index))
            index += 1
        shutil.move(str(release_dir), str(target))
        notes.append("[构建] 发布物编号 %s 被重用，原目录已移至 %s"
                     % (release_id, db.rel_to_root(target)))
    release_dir.mkdir(parents=True, exist_ok=True)
    for name in ("student.tex", "answer.tex", "teacher.tex", "body.tex", "config.tex"):
        shutil.copy2(work / name, release_dir / name)
    if (work / 'ExBook.cls').is_file():
        shutil.copy2(work / 'ExBook.cls', release_dir / 'ExBook.cls')
    for folder in ('img', 'assets'):
        if (work / folder).is_dir():
            shutil.copytree(work / folder, release_dir / folder)
    _write(release_dir / "snapshot.json",
           json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=False))
    for version in VERSION_ORDER:
        pdf = work / (version + ".pdf")
        if pdf.is_file():
            shutil.copy2(pdf, release_dir / (version + ".pdf"))
    manifest = {p.relative_to(release_dir).as_posix(): _sha256(p)
                for p in release_dir.rglob('*') if p.is_file()}
    _write(release_dir / 'manifest.json', json.dumps(
        {'created_at': db.now(), 'files': manifest}, ensure_ascii=False, indent=2))

    student_pdf = release_dir / "student.pdf"
    answer_pdf = release_dir / "answer.pdf"
    teacher_pdf = release_dir / "teacher.pdf"
    student_path = db.rel_to_root(student_pdf) if student_pdf.is_file() else None
    answer_path = db.rel_to_root(answer_pdf) if answer_pdf.is_file() else None
    teacher_path = db.rel_to_root(teacher_pdf) if teacher_pdf.is_file() else None
    student_sha = _sha256(student_pdf)
    teacher_sha = _sha256(teacher_pdf)

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO release(id, collection_id, snapshot, template, build_dir, "
            "student_path, answer_path, teacher_path, student_sha256, teacher_sha256, checks, "
            "status, pages_student, pages_answer, pages_teacher, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (release_id, collection_id,
             json.dumps(snapshot, ensure_ascii=False),
             exbook.normalise_options(options)["template"],
             db.rel_to_root(work), student_path, answer_path, teacher_path,
             student_sha, teacher_sha, json.dumps(checks, ensure_ascii=False),
             status, checks["pages_student"], checks["pages_answer"],
             checks["pages_teacher"], db.now()))
        db.audit(action, "release", release_id, {
            "status": status,
            "pages": [checks["pages_student"], checks["pages_answer"],
                      checks["pages_teacher"]],
            "leak": checks["leak"],
            "build_dir": db.rel_to_root(work),
        })

    release = {
        "id": release_id,
        "collection_id": collection_id,
        "status": status,
        "template": exbook.normalise_options(options)["template"],
        "build_dir": db.rel_to_root(work),
        "student_path": student_path,
        "answer_path": answer_path,
        "teacher_path": teacher_path,
        "student_sha256": student_sha,
        "teacher_sha256": teacher_sha,
        "pages_student": checks["pages_student"],
        "pages_answer": checks["pages_answer"],
        "pages_teacher": checks["pages_teacher"],
        "created_at": db.now(),
    }
    _prune_builds()

    def _file(path: str | None, pages: int | None) -> dict:
        if not path:
            return {"path": None, "url": None, "pages": pages}
        return {"path": path, "url": "/files/" + path, "pages": pages}

    return {
        "release": release,
        "files": {
            "student": _file(student_path, checks["pages_student"]),
            "answer": _file(answer_path, checks["pages_answer"]),
            "teacher": _file(teacher_path, checks["pages_teacher"]),
        },
        "tex": {
            "student": db.rel_to_root(release_dir / "student.tex")
            if (release_dir / "student.tex").is_file() else None,
            "answer": db.rel_to_root(release_dir / "answer.tex")
            if (release_dir / "answer.tex").is_file() else None,
            "teacher": db.rel_to_root(release_dir / "teacher.tex")
            if (release_dir / "teacher.tex").is_file() else None,
        },
        "checks": checks,
    }


def build_release(collection_id: str, options: dict | None = None) -> dict:
    """按题单当前数据生成讲义、编译并登记发布物（docs/API.md §10）。"""
    snapshot = make_snapshot(collection_id, options)
    resolved = snapshot.get("options") or exbook.normalise_options(options)
    return _build(snapshot, resolved, "release.build")


def rebuild_release(release_id: str, options: dict | None = None) -> dict:
    """用已存快照重导，不读题目最新版本；用于核对历史发布物。"""
    row = db.one("SELECT * FROM release WHERE id = ?", (release_id,))
    if row is None:
        from app.server import ApiError
        raise ApiError(404, "not_found", "没有这个发布物：%s" % release_id)
    snapshot = row.get("snapshot")
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    base = snapshot.get("options") or {}
    merged = dict(base)
    merged.update({k: v for k, v in (options or {}).items() if v not in (None, "")})
    resolved = exbook.normalise_options(merged, snapshot.get("collection"))
    snapshot["options"] = resolved
    archived_path = row.get('student_path') or row.get('teacher_path')
    archive = db.abs_path(archived_path).parent if archived_path else db.store_dir('releases') / release_id
    return _build(snapshot, resolved, "release.rebuild", archive)
