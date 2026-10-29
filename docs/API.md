# 接口契约 v1

> 本文件是所有后端模块与前端页面共同遵守的唯一约定。字段名以 `data/schema.sql` 为准，本文件不另立名称。

## 0 通用约定

- 服务地址 `http://127.0.0.1:8760`，仅监听本机。
- 请求与响应一律 UTF-8 JSON；上传文件走 base64 内联，不使用 multipart。
- 时间一律 ISO 8601 带时区，如 `2026-10-27T00:30:00+08:00`。日期字段用 `2026-10-27`。
- 列表接口统一返回 `{"items": [...], "total": 123}`；`total` 为符合条件的总数（不受 `limit` 限制）。
- 出错返回 `{"error": {"code": "...", "message": "..."}}`，HTTP 状态码同时生效。`message` 用中文，可直接展示给教师。
- 静态文件：`/files/<相对项目根的路径>`，例如 `/files/releases/RLS000001/student.pdf`。
- 路径参数形如 `{qid}`，处理器内通过 `req.params["qid"]` 取得。
- 所有写操作需调用 `db.audit(...)` 留下审计记录。

### 0.1 创建类接口的返回包装

创建成功一律返回「对象名: 对象」的形式，便于前端直接取 `resp.collection.id`，不必猜：

| 接口 | 返回 |
|---|---|
| `POST /api/questions`、`/api/inbox/{iid}/promote`、`PATCH /api/questions/{qid}` | `{"question": {...}, "current_version": {...}, …}`（同 §4 详情） |
| `POST /api/collections`、`PATCH /api/collections/{cid}` | `{"collection": {...}}` |
| `POST /api/collections/{cid}/sections`、`PATCH /api/sections/{sid}` | `{"section": {...}}` |
| `POST /api/collections/{cid}/items`、`PATCH /api/items/{iid}` | `{"item": {...}}` |
| `POST /api/audiences`、`PATCH /api/audiences/{aid}` | `{"audience": {...}}` |
| `POST /api/assignments`、`PATCH /api/assignments/{aid}` | `{"assignment": {...}, "warnings": [...]}` |
| `POST /api/usages`、`PATCH /api/usages/{uid}` | `{"usage": {...}, "notice": "…"}` |
| `POST /api/feedback`、`PATCH /api/feedback/{fid}` | `{"feedback": {...}}` |
| `POST /api/releases/{rid}/rebuild` | 同 §10 构建返回 |
| `POST /api/tags`、`POST /api/relations`、`POST /api/reviews`、`POST /api/occurrences` | 直接返回该行对象（不套壳） |

### 0.2 文件落在哪里

- 正式库 `data/qbank.db`：`sources/<SRC编号>/…`、`releases/<RLS编号>/…`。
- 演示库、测试库、恢复件：落到同名子目录，如 `releases/demo/RLS000001/`、`sources/flow/SRC000001/`。
  各库的编号都从 `000001` 起，不加这一层就会互相覆盖，归档随之失真。
- 构建中间文件在 `build/<COL编号>-<时间戳>/`，只保留最近 10 次，可随时整个删除。

## 1 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/meta` | 结构版本、各类数量、根目录、数据库路径 |
| POST | `/api/backup` | 一致性备份，返回备份文件相对路径 |
| GET | `/api/audit?target_type=&target_id=&limit=` | 变更记录 |

`GET /api/meta` 返回：

```json
{
  "schema_version": "1",
  "root": "/path/to/question-bank",
  "db_path": "data/qbank.db",
  "counts": {"questions": 0, "versions": 0, "sources": 0, "collections": 0,
             "releases": 0, "inbox_pending": 0, "tags": 0},
  "today": "2026-10-27"
}
```

## 2 收题箱

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/inbox?status=&limit=` | 收题条目列表 |
| POST | `/api/inbox` | 登记一条原始资料 |
| GET | `/api/inbox/{iid}` | 单条 |
| PATCH | `/api/inbox/{iid}` | 改状态、补备注、记失败原因 |
| POST | `/api/inbox/{iid}/promote` | 整理为正式题目（建题目 + 出处），并把收题条目置为 organized |

创建请求：

```json
{"raw_text": "题干原文…", "file_name": "2024期末.pdf", "file_rel_path": "sources/…",
 "source_hint": "2024 学年期末", "note": "截图不清", "operator": "教师"}
```

`source_hint` 缺失时必须写 `"待确认"`。`status` 取值：`pending`（待提取）、`extracted`（已提取待整理）、`organized`（已整理）、`rejected`（不采用）。

`promote` 请求体即「创建题目」的载荷（见 §4），额外允许 `"source_id"`、`"occurrence"` 覆盖出处信息。返回新建题目详情。

## 3 资料与出处

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/sources?keyword=&kind=` | 资料列表（附 `file_count`、`occurrence_count`） |
| POST | `/api/sources` | 新建资料 |
| GET | `/api/sources/{sid}` | 资料详情，含 `files` 与 `occurrences` |
| PATCH | `/api/sources/{sid}` | 修改 |
| POST | `/api/sources/{sid}/files` | 上传原文件，body `{"filename": "...", "content_b64": "..."}` |
| GET | `/api/sources/{sid}/files` | 文件列表 |
| POST | `/api/occurrences` | 给题目补一个出处 |
| PATCH | `/api/occurrences/{oid}` | 修改出处 |
| DELETE | `/api/occurrences/{oid}` | 删除出处（题目至少保留一个出处或标记待确认） |

文件落地到 `sources/<SRC编号>/原文件名`，同时记录 `sha256`。同一资料重复上传同一校验值只提示不重复落盘。

## 4 题目与版本

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/questions` | 新建题目（含版本 1），可同时带出处、解法、标签 |
| GET | `/api/questions/{qid}` | 题目详情（见下） |
| PATCH | `/api/questions/{qid}` | 改管理字段：`status`、`status_reason`、`short_name`、三维难度、推荐等级、预计用时、`note` |
| POST | `/api/questions/{qid}/versions` | 新增版本：以当前版本为底，按传入字段覆盖 |
| GET | `/api/questions/{qid}/versions/{no}` | 指定版本号的内容 |
| GET | `/api/questions/{qid}/diff?from=1&to=2` | 逐字段差异 |
| POST | `/api/questions/{qid}/merge` | 合并误建重复题，body `{"into": "Q000002", "note": "..."}` |
| POST | `/api/questions/{qid}/deactivate` | 停用，body `{"reason": "..."}` |
| POST | `/api/questions/{qid}/activate` | 恢复启用 |
| POST | `/api/reviews` | 登记审校：`{"target_type","target_id","item","result","note","reviewer"}` |
| GET | `/api/reviews?target_type=&target_id=` | 审校记录，按时间倒序 |

审校按「对象 + 项目」分别记录，当前结论取该项目最新一条记录，历史记录永不覆盖。`target_type` 取 `question_version`、`solution`、`asset`；`item` 取 `stem`、`answer`、`solution`、`figure`、`classification`、`boundary`、`method_scope`。

新建题目请求：

```json
{
  "short_name": "含参二次方程根的分布",
  "qtype": "solve",
  "stem": "已知 $a\\in\\mathbb{R}$，方程 $x^2-2ax+1=0$。",
  "options": null,
  "answer": "$a<-1$ 或 $a>1$",
  "answer_keys": null,
  "subquestions": null,
  "analysis": null,
  "teaching": {"why": "", "key_step": "", "probe": "", "pitfalls": "", "prereq": ""},
  "difficulty": {"calc": 2, "think": 3, "scope": 2, "level": "高一提高", "note": ""},
  "rec_level": "A",
  "rec_reason": "边界比较典型",
  "time_solve_min": 5,
  "time_explain_min": 8,
  "tags": [{"tag_id": "TAG000003", "role": "primary"}],
  "occurrence": {"source_id": "SRC000001", "pdf_page": 3, "print_page": "12",
                 "qno": "8", "raw_text": "…", "pending": 0},
  "solutions": [{"label": "解法一", "approach": "…", "steps": "…", "conditions": "…",
                 "is_primary": 1, "method_tags": ["TAG000010"]}],
  "change_note": "初次录入"
}
```

`GET /api/questions/{qid}` 返回：

```json
{
  "question": {"id": "Q000001", "short_name": "…", "status": "active", "current_version_id": "QV000001",
               "difficulty_calc": 2, "difficulty_think": 3, "difficulty_scope": 2, "rec_level": "A",
               "time_solve_min": 5, "time_explain_min": 8, "…": "…"},
  "current_version": {"id": "QV000001", "version_no": 1, "qtype": "solve", "stem": "…",
                      "options": null, "answer": "…", "answer_keys": null, "subquestions": null,
                      "analysis": null, "teaching": {}, "solutions": [], "tags": [], "version_count": 2},
  "versions": [{"id": "QV000001", "version_no": 1, "qtype": "solve", "change_note": "初次录入",
                "created_at": "…", "review_state": "draft", "solution_count": 1}],
  "occurrences": [{"id": "OCC000001", "source_id": "SRC000001", "source_name": "…",
                   "source_kind": "exam", "file": "sources/SRC000001/a.pdf", "pdf_page": 3,
                   "print_page": "12", "qno": "8", "pending": 0, "raw_text": "…"}],
  "relations": {"out": [{"id": "REL000001", "rel_type": "cond_variant", "note": "…", "confirmed": 1,
                          "question": {"id": "Q000002", "short_name": "…", "stem_preview": "…"}}],
                "in": []},
  "reviews": [{"id": "REV000001", "item": "stem", "result": "pass", "reviewer": "教师",
               "note": "", "created_at": "…", "target_type": "question_version", "target_id": "QV000001"}],
  "review_status": {"stem": "pass", "answer": "pending", "solution": "fail",
                    "figure": "na", "classification": "pending", "boundary": "na", "method_scope": "na"},
  "eligibility": {"usable": false, "missing": ["答案未通过审校", "解析未通过审校"]},
  "usage": [{"release_id": "RLS000001", "collection_title": "…", "audience": "高一(3)班",
             "used_on": "2026-10-20", "purpose": "class"}]
}
```

`review_status` 由最近一条 `pending/pass/fail/dispute/na` 记录决定；无记录视为 `pending`。`figure` 项在题面无素材时可标 `na`，不阻塞可用性。

`eligibility.usable`：`stem`、`answer` 均为 `pass`，且当前版本下至少一条解法 `solution` 为 `pass`（无解法而 `analysis` 有内容时，`analysis` 视作方法解析，以其 `solution` 项通过为准）；`figure` 若为 `fail` 则不可用。

## 5 解法

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/versions/{vid}/solutions` | 在当前版本下新增解法 |
| PATCH | `/api/solutions/{sid}` | 修改解法（只允许改草稿版本下的解法；已发布版本另开新版本） |
| DELETE | `/api/solutions/{sid}` | 删除解法 |
| POST | `/api/solutions/{sid}/primary` | 设为选用解法 |
| POST | `/api/solutions/{sid}/tags` | body `{"tag_id": "TAG000010"}` 方法标签 |

## 6 标签

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tags?dimension=&status=` | 标签列表（附 `question_count`） |
| POST | `/api/tags` | 新建，`{"dimension","name","definition","parent_id"}` |
| PATCH | `/api/tags/{tid}` | 改名、改定义、启停 |
| POST | `/api/tags/{tid}/merge` | 合并同义标签，`{"into": "TAG000002"}`，旧标签写 `merged_into` |
| POST | `/api/questions/{qid}/tags` | 给题目打标签，`{"tag_id","role"}` |
| DELETE | `/api/questions/{qid}/tags/{tid}?role=primary` | 移除 |

维度固定七类：`knowledge` 知识点、`textbook` 教材位置、`prereq` 前置知识、`method` 解题方法、`idea` 数学思想、`purpose` 教学用途、`error` 错误类型。

## 7 题目关系

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/relations` | `{"from_id","to_id","rel_type","note"}` |
| PATCH | `/api/relations/{rid}` | 确认关系、补说明 |
| DELETE | `/api/relations/{rid}` | 删除 |

`rel_type`：`cond_variant` 条件变式、`concl_variant` 结论变式、`param` 参数化、`generalize` 推广、`special` 特殊情形、`inverse` 逆向问题、`same_method` 同法异题、`error_contrast` 易错对比、`correction` 纠错改编。

校验：禁止自指；`correction`、`cond_variant`、`concl_variant`、`param`、`generalize`、`special`、`inverse` 为有向关系，写入前检测是否成环；`same_method`、`error_contrast` 为对称关系，写入时同时生成反向记录并在删除时一并清理。

## 8 检索

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/questions?...` | 检索题目 |
| GET | `/api/questions/{qid}/similar` | 查重候选（同题型 + 规范文本相似度） |
| GET | `/api/search/similar?text=` | 录入时按文本找候选 |

检索参数：

| 参数 | 说明 |
|---|---|
| `keyword` | 匹配题面、答案、简名、出处原文 |
| `qtype` | 可重复，逗号分隔 |
| `tags` | 标签 ID，逗号分隔 |
| `tag_mode` | `all` 必须包含（默认）、`any` 任一包含、`exclude` 排除 |
| `dimension` | 与 `tags` 配合，限定标签维度 |
| `method_tag` | 解法方法标签，命中具体解法而非题目 |
| `think_min` / `think_max` | 思维难度区间 |
| `calc_max` / `scope_max` | 计算难度、综合程度上限 |
| `review` | `pass` 仅可用、`pending` 含未审校、`any` |
| `status` | `active`（默认）、`inactive`、`all` |
| `exclude_audience` | 排除该使用对象近期用过的题，`exclude_days` 默认 30 |
| `sort` | `recent`（默认）、`difficulty`、`used` |
| `limit` / `offset` | 分页，默认 30 / 0 |

返回条目：

```json
{"id": "Q000001", "short_name": "…", "qtype": "solve", "version_no": 1,
 "stem_preview": "已知 $a\\in\\mathbb{R}$…", "tags": [{"id": "TAG000003", "name": "单调性", "dimension": "knowledge"}],
 "methods": [{"id": "TAG000010", "name": "判别式法"}],
 "difficulty": {"calc": 2, "think": 3, "scope": 2}, "rec_level": "A",
 "review_state": "pass", "status": "active", "used_on": "2026-10-20"}
```

## 9 题单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/collections` | 题单列表（附条目数、总用时、发布次数） |
| POST | `/api/collections` | 新建 |
| GET | `/api/collections/{cid}` | 详情：题单 + 分节 + 条目 + 组题检查 |
| PATCH | `/api/collections/{cid}` | 改题单级信息 |
| POST | `/api/collections/{cid}/clone` | 复制一份 |
| POST | `/api/collections/{cid}/sections` | 新增分节 |
| PATCH | `/api/sections/{sid}` | 改分节 |
| DELETE | `/api/sections/{sid}` | 删分节（其条目移入未分节） |
| POST | `/api/collections/{cid}/items` | 加题：`{"question_id","version_id"?,"section_id","role",...}` |
| PATCH | `/api/items/{iid}` | 改条目设置 |
| DELETE | `/api/items/{iid}` | 移除条目 |
| POST | `/api/collections/{cid}/reorder` | `{"items": [{"id","order_no","section_id"}]}` |
| GET | `/api/collections/{cid}/check` | 组题检查 |

条目字段：`role`、`solution_id`、`hint_level`、`time_solve_min`、`time_explain_min`、`space_mm`、`page_break_before`、`required`、`points`、`subq_keys`、`note`。

`GET /api/collections/{cid}` 返回：

```json
{
  "collection": {"id": "COL000001", "title": "…", "purpose": "handout", "duration_min": 90, "…": "…"},
  "sections": [{"id": "SEC000001", "title": "一、单调性的定义", "order_no": 0, "kind": "question", "intro": "…"}],
  "items": [{"id": "ITM000001", "section_id": "SEC000001", "order_no": 0, "role": "example",
             "question_id": "Q000001", "question_version_id": "QV000001",
             "question": {"id": "Q000001", "short_name": "…", "qtype": "solve", "stem_preview": "…",
                          "version_no": 1, "difficulty": {"calc":2,"think":3,"scope":2}},
             "review_state": "pass", "solution_count": 2, "warnings": ["本题与第 3 题疑似重复"]}],
  "check": {"count": 6, "time_solve": 30, "time_explain": 45, "budget": 90,
            "difficulty_spread": {"1":0,"2":2,"3":3,"4":1,"5":0},
            "duplicates": [{"a": "ITM000001", "b": "ITM000004", "reason": "题面相似度 0.93"}],
            "unreviewed": ["ITM000002"],
            "coverage": {"knowledge": [{"id":"TAG000003","name":"单调性","count":3}],
                         "method": []},
            "missing": []}
}
```

`check` 的 `budget` 取题单 `duration_min`，`time_solve + time_explain` 超过 `budget × 0.9` 时在 `missing` 里给出提示（预算含过渡与预留，不只看做题时间）。

## 10 导出与发布

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/collections/{cid}/build` | 生成 LaTeX、编译、登记发布物 |
| GET | `/api/releases?collection_id=` | 发布物列表 |
| GET | `/api/releases/{rid}` | 发布物详情（含快照与检查结果） |
| POST | `/api/releases/{rid}/rebuild` | 用原快照重导（不读取题目最新版） |
| GET | `/api/releases/{rid}/snapshot` | 下载快照 JSON |

构建请求：

```json
{"title": "函数的单调性", "subtitle": "习题课讲义", "pretitle": "高一数学 · 专题",
 "motto": "先看定义，再动手", "theme": "blue",
 "versions": ["student", "answer", "teacher"], "template": "exbook", "format": "standard"}
```

`format` 取 `standard`（A4 标准版，模板默认）或 `compact`、`loose`、`single`。

`versions` 可取三个值，可任意组合：

| 值 | 产物 | 内容 |
|---|---|---|
| `student` | 学生版 | 只有题目与作答留白 |
| `answer` | 解答版 | 题目 + 答案（每题一个答案条） |
| `teacher` | 解析版 | 题目 + 解析（解法、适用条件、讲评）+ 答案 |

解析版沿用字段名 `teacher`，语义是「含解析的教师用版本」；旧发布物不受影响。

返回：

```json
{"release": {"id": "RLS000001", "status": "built", "created_at": "…"},
 "files": {"student": {"path": "releases/RLS000001/student.pdf", "url": "/files/releases/RLS000001/student.pdf", "pages": 6},
           "answer":  {"path": "releases/RLS000001/answer.pdf",  "url": "/files/releases/RLS000001/answer.pdf",  "pages": 4},
           "teacher": {"path": "releases/RLS000001/teacher.pdf", "url": "/files/releases/RLS000001/teacher.pdf", "pages": 9}},
 "tex": {"student": "releases/RLS000001/student.tex", "answer": "releases/RLS000001/answer.tex",
         "teacher": "releases/RLS000001/teacher.tex"},
 "checks": {"student_errors": 0, "answer_errors": 0, "teacher_errors": 0, "overfull": 0,
            "missing_char": 0, "leak": "clean", "warnings": []}}
```

`checks.leak`：扫描学生版源码，出现答案内容即记 `"leaked"` 并在 `warnings` 说明；解答版与解析版本来含答案，不做泄露判定。构建失败时 `release.status = "failed"`，`checks.warnings` 带编译器原始输出尾部。未请求的版本，对应 `*_errors` 为 `null`、页数为 `null`，不报 0。

## 11 使用与反馈

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/audiences` | 使用对象列表 |
| POST | `/api/audiences` | 新建，`{"kind","name","code"}` |
| GET | `/api/assignments?audience_id=&status=` | 出题任务列表 |
| POST | `/api/assignments` | 新建任务 |
| PATCH | `/api/assignments/{aid}` | 改任务（含登记已发放） |
| GET | `/api/usages?audience_id=&days=` | 使用记录 |
| POST | `/api/usages` | 登记实际使用 |
| POST | `/api/feedback` | 登记教学反馈 |
| GET | `/api/feedback?usage_id=` | 反馈列表 |
| GET | `/api/audiences/{aid}/recent?days=30` | 该对象近期用过的题目与题单 |

`POST /api/usages` 必须带 `audience_id` 与 `used_on`；只有发布物已存在才允许登记。发放（assignment.status=`distributed`）与实际使用（usage_log）分开记录。

## 12 JSON 列结构

| 列 | 结构 |
|---|---|
| `question_version.options` | `[{"key": "A", "text": "…"}]`，`key` 稳定不变，显示时才映射 A/B/C/D |
| `question_version.answer_keys` | `["A"]` 或 `["A","C"]` |
| `question_version.subquestions` | `[{"key": "1", "text": "…", "depends_on": [], "answer": "…"}]` |
| `question_version.teaching` | `{"why": "为什么想到", "key_step": "关键突破", "probe": "追问", "pitfalls": "易错点", "prereq": "前置知识"}` |
| `question_version.material_refs` | `["AST000001"]` |
| `collection_item.subq_keys` | `["1","2"]`，拆用小问时必填，须检查依赖 |
| `usage_log.items` | `["ITM000001", "ITM000003"]` |
| `release.snapshot` | 见 `app/latex/build.py` 的快照结构，含题单、条目、题目版本、解法、素材、模板版本 |
| `release.checks` | `{"student_errors": 0, "answer_errors": 0, "teacher_errors": 0, "overfull": 0, "missing_char": 0, "leak": "clean", "warnings": []}` |

## 13 模块职责

| 文件 | 负责路由 |
|---|---|
| `app/api/content.py` | §2 收题箱、§3 资料与出处、§4 题目与版本、§5 解法、§6 标签、§7 关系、§8 检索 |
| `app/api/teaching.py` | §9 题单、§10 导出与发布、§11 使用与反馈 |
| `app/api/system.py` | §1 系统 |
| `app/latex/build.py` + `app/latex/exbook.py` | LaTeX 生成与编译，供 `teaching.py` 调用 |
| `app/static/*` | 六个工作页面 |

## 14 2026-10-27 检查修订

- 新快照增加 `snapshot_schema=2` 和 `question_versions`（以版本 ID 为键）。`questions` 与 `answers` 保留每题首次出现版本的兼容索引，逐条渲染以条目的 `question_version_id` 为准。旧快照可读；若缺少条目要求的版本，报错，不替换成其他版本。
- `/api/audiences/{aid}/recent`、题目详情中的 `usage`、检索中的 `used_on` 与 `exclude_audience` 均从发布快照及实际使用范围得到。草稿加题、删除或改名不会改写已使用历史。
- `usage_log.items=null` 表示整份发布物，`[]` 表示无题目条目，非空数组表示指定条目。新增和修改均按发布快照检查所属关系；编译失败的发布物不能登记为实际使用。
- 题单检查的 `coverage.method` 只计算实际所选解法的方法；未指定解法时计算会输出的全部解法。条目审校同样要求实际输出的解析通过，不能用另一条已通过的解法代替。
- 新发布物目录附 `ExBook.cls`、`img/`、实际配图 `assets/`（如有）和 `manifest.json`。重导优先使用该发布物的历史文件；旧档案缺少模板时明确提示使用当前模板。
- `collection_item.required=0` 在双版本中显示选做，非空 `points` 显示分值。角色文字标记和独立答案版仍未接入。
- `release.status=built` 表示编译成功；正式使用所需的统一审校/素材/来源资格检查尚未完成，详见项目检查报告。


## 讲义排版与多模型配置（扩展）

排版接口、请求结构与校样生命周期见 [讲义排版预览](讲义排版预览.md)。排版设置保存在 `collection.layout_options`，进入正式发布快照，不改变题目版本内容。

- `GET /api/ai/settings`：返回不含密钥的连接配置、预设与密钥配置状态。
- `POST /api/ai/settings`：`{config, key?}` 保存当前接口；密钥只写本机加密存储，不回显。
- `POST /api/ai/probe`：`{mode: "models" | "text" | "vision"}`；生成验证可能计费。
- AI批次的 `config_json.connection` 固定创建时的接口设置，后续继续任务不随全局设置改变。

协议、能力验证及费用范围见 [多模型接口接入](多模型接口接入.md)。
