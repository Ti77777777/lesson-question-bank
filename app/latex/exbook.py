"""题库数据 → ExBook LaTeX 源。

本模块只做渲染：输入「快照字典 + 选项」，输出 .tex 文本，不读写数据库、
不调用编译器。快照由 app/latex/build.py 生成，结构见 build.make_snapshot。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app import db

TEMPLATE_DIR = db.ROOT / "templates" / "exbook"
CLASS_FILE = TEMPLATE_DIR / "ExBook.cls"
# Optional legacy assets may be archived when present; the renderer does not request them.
COVER_FILE = "img/cover.jpg"
WATER_FILE = "img/water.png"

# 模板提供的主题色名（ExBook.cls 里 \blue \green … 这些宏）
THEMES = {
    "blue", "green", "purple", "orange",
    "infj", "enfp", "infp", "esfp", "intj", "entp", "isfj", "enfj",
}

# 版式 → 封面上的版式标签宏（ExBook.cls 里 \TypeOne … \TypeSix）
TYPE_MACRO = {
    "compact": "TypeOne", "standard": "TypeTwo", "padl": "TypeThree",
    "loose": "TypeFour", "single": "TypeFive", "padp": "TypeSix",
}

CN_NUM = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十")

DEFAULT_OPTIONS = {
    "title": "习题课讲义",
    "subtitle": "习题课讲义",
    "pretitle": "专题讲义",
    "motto": "先想定义，再动笔",
    "creator": "题库示例",
    "theme": "blue",
    "format": "standard",
    "template": "exbook",
    "versions": ["student", "answer", "teacher"],
    "toc": False,
    "cover": True,
    "options_layout": "auto",
    "teacher_notes": True,
}


# ---------------------------------------------------------------- 基础工具
def template_hash() -> str:
    """ExBook.cls 的 sha256 前 12 位，用于在快照里锁住模板版本。"""
    if not CLASS_FILE.is_file():
        return ""
    return hashlib.sha256(CLASS_FILE.read_bytes()).hexdigest()[:12]


_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(text) -> str:
    """转义纯文本里的 LaTeX 特殊字符。题干、解析不做此处理。"""
    return "".join(_ESCAPES.get(ch, ch) for ch in str(text or ""))


def smart_quotes(text) -> str:
    """把成对的 ASCII 双引号换成中文引号，避免 ctex 错映射。"""
    out: list[str] = []
    opening = True
    for ch in str(text or ""):
        if ch == '"':
            out.append("“" if opening else "”")
            opening = not opening
        else:
            out.append(ch)
    return "".join(out)


def plain(text) -> str:
    """元信息（标题、页眉、座右铭等）：转义 + 中文引号。"""
    return latex_escape(smart_quotes(text))


def _num(value) -> str:
    """把 12.0 写成 12，把 12.5 写成 12.5，供 mm 尺寸用。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    return ("%g" % number)


def _cn(index: int) -> str:
    if 1 <= index <= 10:
        return CN_NUM[index]
    return str(index)


def normalise_options(options: dict | None, collection: dict | None = None) -> dict:
    """请求选项 → 完整选项。题单字段作为兜底，最后落到默认值。"""
    opts = dict(DEFAULT_OPTIONS)
    collection = collection or {}
    if collection.get("title"):
        opts["title"] = collection["title"]
    if collection.get("topic"):
        opts["subtitle"] = collection["topic"]
        opts["pretitle"] = collection["topic"]
    if collection.get("level"):
        opts["pretitle"] = collection["level"] if not collection.get("topic") else \
            "%s · %s" % (collection["level"], collection["topic"])
    if collection.get("template"):
        opts["template"] = collection["template"]

    saved = collection.get("layout_options") or {}
    if isinstance(saved, dict):
        opts.update(saved.get("options") or {})
    for key, value in (options or {}).items():
        if value is None:
            continue
        opts[key] = value

    theme = str(opts.get("theme") or "blue").strip()
    opts["theme"] = theme if theme in THEMES else "blue"
    fmt = str(opts.get("format") or "standard").strip()
    opts["format"] = fmt if fmt in TYPE_MACRO else "standard"
    versions = opts.get("versions") or ["student", "answer", "teacher"]
    if isinstance(versions, str):
        versions = [versions]
    opts["versions"] = [v for v in ("student", "answer", "teacher")
                        if v in set(versions)] or ["student", "answer", "teacher"]
    opts["title"] = str(opts.get("title") or DEFAULT_OPTIONS["title"])
    opts["subtitle"] = str(opts.get("subtitle") or "")
    opts["pretitle"] = str(opts.get("pretitle") or "")
    opts["motto"] = str(opts.get("motto") or "")
    opts["creator"] = str(opts.get("creator") or DEFAULT_OPTIONS["creator"])
    return opts


def _header(text: str, limit: int = 20) -> str:
    """页眉过长会 Overfull，截断到 limit 个字符。"""
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------- 素材
def asset_dest(asset: dict) -> str | None:
    """素材在构建目录里的相对路径，如 assets/AST000001.png。"""
    rel = (asset or {}).get("rel_path") or ""
    if not rel:
        return None
    suffix = Path(rel).suffix or ".png"
    return "assets/%s%s" % (asset.get("id"), suffix)


def asset_targets(snapshot: dict) -> list[tuple[dict, str, str]]:
    """[(asset, 构建目录内相对路径, 项目内相对路径)]，按素材 ID 去重。"""
    seen: set[str] = set()
    out: list[tuple[dict, str, str]] = []
    from app.snapshots import entries
    for entry in entries(snapshot):
        for asset in entry.get("assets") or []:
            aid = asset.get("id")
            if not aid or aid in seen:
                continue
            dest = asset_dest(asset)
            if not dest:
                continue
            seen.add(aid)
            out.append((asset, dest, asset.get("rel_path")))
    return out


# ---------------------------------------------------------------- 题目片段
_BLANK_RUN = re.compile(r"_{3,}")
_BLANK_CMD = re.compile(r"\\blank(?![A-Za-z])")


def _stem_latex(version: dict) -> str:
    """题干原样输出；填空题的填空标记统一成 \\blankline。"""
    stem = str(version.get("stem") or "").strip()
    qtype = version.get("qtype")
    has_marker = bool(_BLANK_RUN.search(stem) or _BLANK_CMD.search(stem)
                      or "\\blankline" in stem)
    if qtype == "blank" or has_marker:
        stem = _BLANK_RUN.sub(r"\\blankline{}", stem)
        stem = _BLANK_CMD.sub(r"\\blankline{}", stem)
        if qtype == "blank" and "\\blankline" not in stem and "\\blankbox" not in stem:
            stem = stem + " \\blankline"
    # \blankbox 自带一对括号，录题人常再套一层，会排成「（ （ ））」
    stem = re.sub(r"[（(]\s*\\blankbox\s*[）)]", r"\\blankbox", stem)
    return stem


def _options_latex(options, layout="auto") -> str | None:
    """四个选项走 \\fourchoices，三个走 \\threechoices，其余逐行排。"""
    if not options:
        return None
    texts = []
    for opt in options:
        text = (opt.get("text") if isinstance(opt, dict) else opt) or ""
        texts.append(str(text).strip())
    texts = [t for t in texts if t != ""]
    if layout == "vertical":
        return "\\par " + "\\par ".join("%s.~%s" % (chr(65+i), text) for i, text in enumerate(texts))
    if len(texts) == 4:
        return "\\qbankfourchoices{%s}{%s}{%s}{%s}" % tuple(texts)
    if len(texts) == 3:
        return "\\threechoices{%s}{%s}{%s}" % tuple(texts)
    if not texts:
        return None
    labels = "ABCDEFGH"
    return "\\par " + "\\par ".join(
        "%s.~%s" % (labels[i] if i < len(labels) else i + 1, t)
        for i, t in enumerate(texts))


def _selected_subquestions(version: dict, item: dict) -> list[dict]:
    subs = [s for s in (version.get("subquestions") or []) if isinstance(s, dict)]
    keys = item.get("subq_keys") or []
    if isinstance(keys, str):
        keys = [keys]
    if keys:
        wanted = {str(k) for k in keys}
        subs = [s for s in subs if str(s.get("key")) in wanted]
    return subs


def _answer_text(version: dict) -> str:
    """答案永远先出：优先 answer，选择题再补答案键。"""
    ans = str(version.get("answer") or "").strip()
    keys = version.get("answer_keys") or []
    if isinstance(keys, str):
        keys = [keys]
    keys = [str(k) for k in keys if str(k).strip()]
    if keys and version.get("qtype") in ("single", "multi"):
        label = "、".join(keys)
        if not ans:
            ans = label
        elif label not in ans:
            ans = "%s（选 %s）" % (ans, label)
    return ans


def _selected_solutions(entry: dict, item: dict) -> list[dict]:
    sols = [s for s in (entry.get("solutions") or []) if isinstance(s, dict)]
    sols.sort(key=lambda s: (s.get("order_no") or 0, s.get("id") or ""))
    chosen = item.get("solution_id")
    if chosen:
        picked = [s for s in sols if s.get("id") == chosen]
        if picked:
            return picked
    return sols


def _label_text(text: str) -> str:
    """xeCJK 会吞掉两个汉字之间的空格，「解法一 判别式法」会连成一个词。

    这里把标签里的半角空格换成控制空格，强制留出一个词距。
    """
    return str(text or "").strip().replace(" ", "\\ ")


def _teaching_notes(version: dict) -> str | None:
    """教师版末尾的讲评提示：为什么想到、关键一步、易错点、追问。

    只写有内容的项；这些内容不进学生版。
    """
    teaching = version.get("teaching") or {}
    if not isinstance(teaching, dict):
        return None
    parts = []
    for key, label in (("why", "为什么想到"), ("key_step", "关键一步"),
                       ("pitfalls", "易错点"), ("probe", "追问")):
        text = str(teaching.get(key) or "").strip()
        if text:
            parts.append("\\textbf{%s：}%s" % (label, text))
    if not parts:
        return None
    # 四项各占一行：讲评文字常常较长，挤成一段读起来费劲
    return "\\smallskip{\\small\\raggedright\n" + "\\par\n".join(parts) + "\\par}"


def _analysis_block(entry: dict, item: dict, subs: list[dict],
                    opts: dict | None = None, mode: str = "solution") -> str:
    """解析框：解析（解法、适用条件、讲评）在前，答案在后。

    mode="answer" 只出答案；mode="solution" 出解析与答案两段。
    """
    version = entry.get("version") or {}
    answer = _answer_text(version)
    blocks: list[str] = []

    if mode == "solution":
        analysis_parts: list[str] = []
        solutions = _selected_solutions(entry, item)
        if solutions:
            for index, sol in enumerate(solutions, 1):
                label = str(sol.get("label") or "").strip() or ("解法" + _cn(index))
                steps = str(sol.get("steps") or "").strip()
                conditions = str(sol.get("conditions") or "").strip()
                body = "\\textbf{%s}\\par %s" % (_label_text(label), steps)
                if conditions:
                    body += "（适用条件：%s）" % conditions
                analysis_parts.append(body)
        else:
            analysis = str(version.get("analysis") or "").strip()
            if analysis:
                analysis_parts.append(analysis)
        if (opts or {}).get("teacher_notes", True):
            notes = _teaching_notes(version)
            if notes:
                analysis_parts.append(notes)
        if analysis_parts:
            blocks.append("\\analysispart{解析}\n" + "\n\n".join(analysis_parts))

    answer_parts: list[str] = []
    if answer:
        answer_parts.append(answer)
    if subs:
        pairs = []
        for sub in subs:
            sub_answer = str(sub.get("answer") or "").strip()
            if sub_answer:
                pairs.append("（%s）%s" % (sub.get("key"), sub_answer))
        # 题面已给出整题答案时不再重复一份「小问答案」
        if pairs and not answer:
            answer_parts.append("；".join(pairs))
    blocks.append("\\analysispart{答案}\n" + ("\n\n".join(answer_parts) or "（略）"))

    body = "\n\n".join(blocks)
    return ("\\begin{analysis}[]\n"
            "\\fontsize{10.5pt}{14.2pt}\\selectfont\n"
            + body + "\n\\end{analysis}")


def _cut_safely(text: str, limit: int) -> str:
    """按句读截断提示语，但不切断 $…$ 公式。

    教学说明是自由文本，中文逗号常出现在区间 $[1，2]$ 里；按标点硬切会把
    公式拦腰截断，编译时报 Missing $。这里先跳到第一个「不在公式里」的句读，
    没有句读再按长度截断，且截断点必须落在公式之外。
    """
    text = text.strip()
    if not text or text.count("$") % 2:      # 公式本身不闭合，宁可不提示
        return ""
    depth = 0
    cut = 0
    for i, ch in enumerate(text):
        if ch == "$":
            depth ^= 1
        elif depth == 0 and ch in "，。；;：:！？!?":
            cut = i + 1
            break
    if not cut:
        if len(text) <= limit:
            return text
        depth = 0
        for i, ch in enumerate(text[:limit]):
            if ch == "$":
                depth ^= 1
            if depth == 0:
                cut = i + 1
    return text[:cut].strip()


def _hint_latex(item: dict, version: dict) -> str | None:
    """学生版提示：keyword 取 key_step 前半句，full 取 probe。"""
    level = item.get("hint_level") or "none"
    teaching = version.get("teaching") or {}
    if not isinstance(teaching, dict):
        return None
    text = ""
    if level == "keyword":
        text = _cut_safely(str(teaching.get("key_step") or ""), 24)
    elif level == "method":
        text = _cut_safely(str(teaching.get("key_step") or ""), 60)
    elif level == "full":
        text = _cut_safely(str(teaching.get("probe") or ""), 60)
    if not text:
        return None
    if text[-1] not in "。？！?!；;":
        text += "。"
    return "\\hinttext{" + text + "}"


def _item_block(snapshot: dict, item: dict, version: str, number: int,
                available: set[str] | None) -> str:
    from app.snapshots import entry_for_item
    entry = entry_for_item(snapshot, item)
    qversion = entry.get("version") or {}
    parts: list[str] = []

    if item.get("page_break_before") and number > 1:
        parts.append("\\clearpage")

    optional = "\\textbf{（选做）}" if item.get('required') == 0 else ''
    points = item.get('points')
    score = "\\quad（%s 分）" % _num(points) if points is not None else ''
    teaching = qversion.get('teaching') or {}
    if not isinstance(teaching, dict):
        teaching = {}
    source = entry.get('source_label') or (teaching.get('source_label') if isinstance(teaching, dict) else None)
    source_label = ("{\\footnotesize\\color{gray}[%s]}\\par " % plain(source)
                    if source and (snapshot.get('options') or {}).get('show_sources') else '')
    if item.get("keep_together"):
        parts.append("\\begin{samepage}")
    parts.append("\\qitem " + optional + source_label + _stem_latex(qversion) + score)

    for asset in entry.get("assets") or []:
        if teaching.get('asset_placement') == 'inline':
            continue
        aid = asset.get("id")
        dest = asset_dest(asset)
        if not dest:
            continue
        if available is not None and aid not in available:
            continue
        parts.append("\\imgin[0.5]{%s}{%s}" % ("", dest))

    option_layout = item.get("options_layout", (snapshot.get("options") or {}).get("options_layout", "auto"))
    options = _options_latex(qversion.get("options"), option_layout)
    if options:
        parts.append(options)

    subs = _selected_subquestions(qversion, item)
    if subs:
        lines = ["\\par\\begin{subqitems}"]
        for sub in subs:
            lines.append("\\subqitem " + str(sub.get("text") or "").strip())
        lines.append("\\end{subqitems}")
        parts.append("\n".join(lines))
    if teaching.get('question_tail'):
        parts.append(str(teaching['question_tail']))

    if version == "student":
        hint = _hint_latex(item, qversion)
        if hint:
            # 短句强制两端对齐会 Underfull，提示行左对齐；并禁止在题干与提示之间分页
            parts.append("\\par\\nopagebreak[4]{\\small\\raggedright " + hint + "\\par}")
        space = item.get("space_mm")
        if space:
            # 留白走模板的 \answerspace：左侧淡色竖线标出书写区边界。
            parts.append("\\answerspace{%s}" % _num(space))
    else:
        mode = "answer" if version == "answer" else "solution"
        parts.append(_analysis_block(entry, item, subs,
                                     snapshot.get("options"), mode))

    parts.append("\\par")
    if item.get("keep_together"):
        parts.append("\\end{samepage}")
    return "\n".join(parts)


# ---------------------------------------------------------------- 正文
def _blocks(snapshot: dict) -> list[tuple[dict, list[dict]]]:
    """按分节分组条目；条目缺省分节时并入末尾「未分节」。"""
    sections = sorted(
        snapshot.get("sections") or [],
        key=lambda s: (s.get("order_no") or 0, s.get("id") or ""))
    items = sorted(
        snapshot.get("items") or [],
        key=lambda i: (i.get("order_no") or 0, i.get("id") or ""))
    grouped: dict[str, list[dict]] = {s.get("id"): [] for s in sections}
    orphans: list[dict] = []
    for item in items:
        sid = item.get("section_id")
        if sid in grouped:
            grouped[sid].append(item)
        else:
            orphans.append(item)
    blocks = [(section, grouped.get(section.get("id")) or []) for section in sections]
    if orphans:
        blocks.append(({"id": None, "title": "未分节", "kind": "question",
                        "intro": None}, orphans))
    return blocks


def render_body(snapshot: dict, options: dict | None, version: str,
                available: set[str] | None = None) -> str:
    """生成正文。version 取 student / answer / teacher；题号全篇连续 1–N。"""
    out: list[str] = []
    counter = 0
    for section, items in _blocks(snapshot):
        section_break = bool(items and items[0].get('page_break_before') and counter > 0)
        if section_break:
            out.append("\\clearpage")
        out.append("\\section{%s}" % plain(section.get("title") or "未命名分节"))
        intro = str(section.get("intro") or "").strip()
        if intro:
            out.append(intro)
            out.append("\\par")
        if (section.get("kind") or "question") != "question" or not items:
            continue
        key = "startnum=%d" % (counter + 1)
        if version in ("answer", "teacher"):
            key += ",showanalysis"
        out.append("\\begin{qitems}[%s]" % key)
        for index, item in enumerate(items):
            counter += 1
            if index == 0 and section_break:
                item = dict(item, page_break_before=False)
            out.append(_item_block(snapshot, item, version, counter, available))
        out.append("\\end{qitems}")
    body = "\n".join(out).rstrip() + "\n"
    # Circled labels emitted by OCR/AI are absent from the template's Latin
    # font on some Windows machines. Draw the labels with TeX instead.
    for number in range(1,21):
        body = body.replace(chr(0x2460+number-1),r'\textcircled{'+str(number)+'}')
    return body


# ---------------------------------------------------------------- 配置与主文件
def render_config(options: dict) -> str:
    """封面与页眉配置。motto 用中文引号，不走模板里的 ASCII 引号。

    三版共用同一份 config.tex：主文件先定义 \\qbAnswerVersion 或
    \\qbTeacherVersion，这里据此切换副标题后缀与页眉右端。
    """
    opts = normalise_options(options)
    type_macro = TYPE_MACRO.get(opts["format"], "TypeTwo")
    subtitle = opts["subtitle"]

    def desc(label: str) -> str:
        return (subtitle + "（%s）" % label) if subtitle else label

    lines = [
        "% 由 app/latex/build.py 生成，请勿手改",
        "\\makeatletter",
        "\\renewcommand\\motto[1]{\\def\\ExBook@value@motto{\\kaishu “#1”}}",
        "\\makeatother",
        "\\PreTitle{%s}" % plain(opts["pretitle"]),
        "\\Title{%s}" % plain(opts["title"]),
        "\\motto{%s}" % plain(opts["motto"]),
        "\\Creator{%s}" % plain(opts["creator"]),
        "\\UpdateTime{\\today}",
        "\\Lhead{%s}" % plain(_header(opts["title"])),
        "\\Chead{%s}" % plain(_header(subtitle or opts["pretitle"])),
        "\\%s{A4 讲义版}" % type_macro,
        "\\ifdefined\\qbAnswerVersion",
        "  \\TitleDescription{%s}" % plain(desc("解答版")),
        "  \\Rhead{解答版}",
        "  \\setSolutionDisplay{\\showSolution}",
        "\\else",
        "  \\ifdefined\\qbTeacherVersion",
        "    \\TitleDescription{%s}" % plain(desc("解析版")),
        "    \\Rhead{解析版}",
        "    \\setSolutionDisplay{\\showSolution}",
        "  \\else",
        "    \\TitleDescription{%s}" % plain(desc("学生版")),
        "    \\Rhead{学生版}",
        "    \\setSolutionDisplay{\\hideSolution}",
        "  \\fi",
        "\\fi",
        "\\setThemeColor{\\%s}" % opts["theme"],
    ]
    return "\n".join(lines) + "\n"


def render_document(snapshot: dict, options: dict | None, version: str,
                    body: str | None = None,
                    available: set[str] | None = None) -> str:
    """主文件。学生版 \\input{body}；解答版与解析版正文内联（解析须逐题插入）。"""
    opts = normalise_options(options, snapshot.get("collection"))
    class_options = [opts["format"], "windows", "notocnum"]
    if version in ("answer", "teacher"):
        class_options.insert(1, "analysis")
    lines = [
        "% 由 app/latex/build.py 生成，请勿手改",
        "\\documentclass[%s]{ExBook}" % ",".join(class_options),
        r"\newsavebox{\qbankimagebox}",
        r"\providecommand{\qbankimage}[2][]{\begingroup\sbox{\qbankimagebox}{\includegraphics[#1]{#2}}\ifdim\wd\qbankimagebox>\linewidth\resizebox{\linewidth}{!}{\usebox{\qbankimagebox}}\else\usebox{\qbankimagebox}\fi\endgroup}",
        r"\newlength{\qbankchoicewidth}\newlength{\qbankchoicetemp}",
        r"\providecommand{\qbankfourchoices}[4]{\par\begingroup\setlength{\qbankchoicewidth}{0pt}%",
        r"\settowidth{\qbankchoicetemp}{A.~#1}\ifdim\qbankchoicetemp>\qbankchoicewidth\setlength{\qbankchoicewidth}{\qbankchoicetemp}\fi%",
        r"\settowidth{\qbankchoicetemp}{B.~#2}\ifdim\qbankchoicetemp>\qbankchoicewidth\setlength{\qbankchoicewidth}{\qbankchoicetemp}\fi%",
        r"\settowidth{\qbankchoicetemp}{C.~#3}\ifdim\qbankchoicetemp>\qbankchoicewidth\setlength{\qbankchoicewidth}{\qbankchoicetemp}\fi%",
        r"\settowidth{\qbankchoicetemp}{D.~#4}\ifdim\qbankchoicetemp>\qbankchoicewidth\setlength{\qbankchoicewidth}{\qbankchoicetemp}\fi%",
        r"\ifdim\qbankchoicewidth<\dimexpr\linewidth/4-1em\relax\noindent\begin{tabular*}{\linewidth}{@{\extracolsep{\fill}}llll@{}}A.~#1&B.~#2&C.~#3&D.~#4\end{tabular*}\else\noindent A.~#1\par\noindent B.~#2\par\noindent C.~#3\par\noindent D.~#4\fi\par\endgroup}",
    ]
    if version == "answer":
        lines.append("\\def\\qbAnswerVersion{}")
    elif version == "teacher":
        lines.append("\\def\\qbTeacherVersion{}")
    lines += [
        "\\begin{document}",
        "\\input{config}",
        "\\maketitle" if opts.get("cover", True) else "",
    ]
    if version == "student":
        lines.append("\\input{body}")
    else:
        lines.append(body if body is not None else
                     render_body(snapshot, opts, version, available))
    lines.append("\\end{document}")
    return "\n".join(lines) + "\n"
