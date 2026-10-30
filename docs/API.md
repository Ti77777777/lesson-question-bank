# 接口契约 v1

> 本文件记录主要接口契约和增量接口。字段名以 `data/schema.sql` 为准，本文件不另立名称。

## 0 通用约定

- 默认服务地址 `http://127.0.0.1:8761`，演示模式使用 8760；仅监听本机。
- 普通请求与响应使用 UTF-8 JSON。来源文件等接口使用 base64；AI 文件上传和完整备份导入使用原始二进制，下载返回相应文件类型。
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
| POST | `/api/collections/{cid}/items/batch` | 批量加入（N01）：`{"operation_id","base_revision","section_id","items":[{"question_id","question_version_id"?,"allow_duplicate"?}]}` |
| PATCH | `/api/items/{iid}` | 改条目设置 |
| DELETE | `/api/items/{iid}` | 移除条目 |
| POST | `/api/collections/{cid}/reorder` | `{"items": [{"id","order_no","section_id"}]}` |
| GET | `/api/collections/{cid}/check` | 组题检查 |

条目字段：`role`、`solution_id`、`hint_level`、`time_solve_min`、`time_explain_min`、`space_mm`、`page_break_before`、`required`、`points`、`subq_keys`、`note`。

### 批量加入与题单修订（N01）

`POST /api/collections/{cid}/items/batch` 的语义：

- **逐项预校验、合法项原子写入**：不合格的条目不写入，在 `results[]` 里给出 `code`（`not_found` / `merged` / `version_mismatch` / `no_version` / `duplicate` / `duplicate_in_batch` / `missing_field` / `bad_field`）与说明；其余条目正常加入，不会因为一条出错整批丢弃。
- **并发基准**：请求带 `base_revision`（取自 `GET /api/collections/{cid}` 的 `revision`）。与服务端当前值不一致时返回 409 `revision_conflict`，**整批不执行**，`detail.revision` 给出当前值。
- **幂等**：`operation_id` 由页面生成。同一题单内，同编号同输入返回第一次的结果并带 `"replayed": true`；同编号但输入不同返回 409 `operation_conflict`。摘要是按原始请求计算的，重试时不会因为「题已加入」而误判成另一个操作。
- **重复题**：默认把集合里已有的题判为 `duplicate`；确实要放两次时对那一条传 `allow_duplicate: true`。

返回：

```json
{
  "results": [{"index": 0, "question_id": "Q000001", "question_version_id": "QV000001",
               "ok": true, "item_id": "ITM000007", "order_no": 3}],
  "added": 1,
  "section_id": "SEC000001",
  "revision": "cr1:9f2c…",
  "replayed": false
}
```

### 可撤销的题单写入（N02 首期）

`POST /api/collections/{cid}/commands`，请求 `{"operation_id","base_revision","action","item_id","payload"}`：

| action | payload | 逆动作 |
|---|---|---|
| `remove` | — | `restore`（含完整条目快照） |
| `restore` | 条目快照：`question_id`、`question_version_id`、`section_id`、`order_no` 及条目设置 | `remove` |
| `reorder` | `{"items":[{"id","order_no","section_id"}]}` | `reorder`（还原每个条目原来的位置） |
| `settings` | 条目设置字段 | `settings`（原有值） |

返回 `{"action","label","inverse","revision","replayed"}`。撤销只是把 `inverse` 原样再发一次，因此同样受操作编号幂等与修订校验保护；`restore` 时编号已被占用则返回 409 `already_exists`，不会悄悄换号。

`revision` 是题单、分节、条目与排版的聚合摘要（`app/commands.py`）。它只用于题单结构写入的并发校验；题目自身版本变化另行核对，不混进这个摘要。

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
| GET | `/api/usage-summary` | 按对象批量查已用：`audience_id`、`days`、`question_ids`（逗号分隔，上限 200）；返回 `items: {题目: {used_on, usage_id, collection_title}}` |
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


## 编辑体验扩展（v0.3）

- GET /api/collections/{cid}/layout：增加 revision 和 draft_scope。
- PUT /api/collections/{cid}/layout：可传 base_revision，冲突返回409；成功返回新 revision。旧客户端不传时仍兼容。
- GET /api/meta：增加 draft_scope，供本机草稿按数据库隔离。
- GET /api/layout-templates：返回当前题库的自定义样式 items。
- POST /api/layout-templates：接收 name、options、space_mm，返回 template；最多50份，不包含标题、副标题或逐题数据。
- DELETE /api/layout-templates/{tid}：删除自定义样式；不修改已套用题单。
- POST /api/ai/questions/{qid}/edit：可传 base_revision；旧题面修订冲突返回409。候选修订仍使用 content_hash 核对。

表单草稿仅存浏览器，不增加模型请求，详见[编辑与草稿](编辑与草稿.md)。


## 直接排版扩展（v0.4）

排版保存及PDF校样的layout对象可包含order数组，每项为{id, section_id}，须完整覆盖当前题单全部条目且不重复。section_id可为本题单分节编号或null；保存时与排版设置在同一事务中更新。order不作为永久覆盖写入layout_options，而是更新collection_item。base_revision同时检查题单、条目及分节变更，冲突返回409。


## 18 教师归属与班级名单（0.8.0）

教师选择不是认证信息，不改变接口访问权限。接口保留全部教师与名单数据，前端按当前教师筛选。

| 方法与路径 | 请求 / 返回 |
|---|---|
| `GET /api/teachers` | `{items,total}`，教师按名称、编号排序 |
| `POST /api/teachers` | `{name,code?,note?,operation_id?,allow_same_name?}` → `{teacher}`；名称 1–80 字，工号最多 80 字，备注最多 500 字 |
| `PATCH /api/teachers/{tid}` | `{name?,code?,note?,base_revision?}` → `{teacher}` |
| `GET /api/audiences?teacher_id=...` | 可用具体教师编号或 `_unassigned`；空值返回全部。保留 `kind` 等既有条件 |
| `POST /api/audiences` | 既有字段可附 `teacher_ids:[]`，一次事务创建并关联；未指定时不分配 |
| `GET /api/audiences/{aid}/teachers` | `{audience,revision}`，含直接与继承教师 |
| `PUT /api/audiences/{aid}/teachers` | `{teacher_ids:[],base_revision}` 替换直接关联 → 同 GET 包装 |
| `GET /api/audiences/{aid}/members` | 班级/小组 → `{audience,items,total,revision}` |
| `PUT /api/audiences/{aid}/members` | `{student_ids:[],base_revision}` 替换名单 → 同 GET 包装；空列表移出所有成员，不删除学生 |
| `POST /api/audiences/{aid}/members` | `{name,code?,note?,operation_id?,allow_same_name?}` → `{student}`，一次事务创建学生并加入本班 |

列表中的 audience 增加 `direct_teachers`、`teachers`、`classes`、`member_count`。`teachers` 是直接教师与所在班级/小组教师的并集。名单只接受学生；有名单关联的记录不能直接改变类型。编号列表最多 1000 项且不能重复。名单/归属 GET 返回的 `revision` 必须原样随 PUT 提交；过期或缺失返回 409，不写入。不存在的教师/对象返回 404，无效类型或名称返回 400。

## 19 已有功能的增量接口补记

### 换题与更新版本

- `POST /api/items/{iid}/swap`：提交 `question_id`、可选 `question_version_id`、`operation_id` 和 `base_revision`。原位换题，保留条目位置与角色；专属设置清空项见返回的 `dropped`。
- `GET /api/collections/{cid}/version-updates`：查询题单的新版候选；`POST /api/collections/{cid}/version-updates/apply` 提交 `updates:[{item_id,question_version_id?}]`（1–100 条）及操作编号、修订号，逐条结果见 `results`，成功数量见 `updated`。前端必须展示每条结果，不能只按 HTTP 200 判断全部成功。
- `POST /api/collections/{cid}/commands`：题单操作入口。换题、换版及相关题单修改由操作编号防重复、修订号防覆盖；具体操作参数沿用各接口实现与测试，冲突时重新读取题单。

### 逐题反馈

`GET /api/usages/{uid}/item-feedback` 返回本次实际使用的条目范围与观察记录；`POST` 提交 `{item_id,error_note,error_tags?,sample_scope?,suggestion?}`。`error_note` 必填。同次使用同一条目重复提交更新现有观察；超出使用范围返回 409 `not_in_use_scope`。该接口与整次使用反馈独立，目前逐题反馈网页入口尚未接入。

### 完整备份

- `GET /api/backups` 列出本题库的完整备份，`POST /api/backups` 创建。
- `POST /api/backups/import` 上传原始 ZIP（二进制，最多 512 MB），`GET /api/backups/{bid}/download` 下载 ZIP。
- `POST /api/backups/{bid}/check` 校验清单与文件；`POST /api/backups/{bid}/restore` 要求 `{confirmed:true}`，创建独立恢复副本，不覆盖当前题库。
- 旧 `POST /api/backup` 仍为数据库备份；数据库备份不能替代包含附件的完整备份。

### 检索方案、来源批改与纠错

- `GET/POST /api/search/presets`、`PATCH/DELETE /api/search/presets/{pid}`：保存或更新使用 `{name,query}`，query 的值均为字符串；删除不存在方案返回 404。
- `POST /api/sources/batch-preview`：`{items:[{id,changes:{...}}]}`，1–100 份，返回修改前后与 `preview_hash`。`POST /api/sources/batch-apply` 提交相同 items 和 hash，数据变化返回 409。可修改 `name,school,year,region,exam_type,grade`。
- `GET /api/source-changes` 查最近 30 次；`POST /api/source-changes/{cid}/undo` 撤销。来源已再次变化时拒绝覆盖。
- `GET/POST /api/questions/{qid}/corrections`：登记 `{version_id,category,description,suggestion?}`；category 为 `stem,answer,solution,figure,tags,source,difficulty,other`。
- `PATCH /api/corrections/{cid}`：`{status,expected_status,note,fixed_version_id?}`；status 为 `pending,resolved,dismissed`。处理说明必填，旧状态不匹配返回 409；只有 resolved 可以关联修订版本。登记纠错本身不修改题目。


## 20 使用登记的操作编号（0.8.1）

`POST /api/usages` 可提供 `operation_id`。查重与新增在同一 SQLite 写事务内完成；相同编号和相同登记字段返回原 `usage` 及 `replayed:true`，并发请求不会创建多条。相同编号但 `assignment_id,release_id,audience_id,used_on,purpose,items,note` 任何有效字段不同，返回 409 `operation_conflict`。前端应对同一次失败请求保留编号；只有新内容或明确新的一次使用才生成新编号。

网页使用发布物历史快照的条目编号；不要用可继续编辑的题单条目替代。网页的至少一题要求不改变接口既有的 `items:null` 表示整份、`items:[]` 表示没有题目这一语义。

## 21 同名档案与创建重试（0.9.0）

教师和 audience 返回 `display_name`、`profile_revision`。`display_name` 为姓名、可用的工号/代号/班级信息及永久系统编号；客户端仍须转义后展示，关联与选择只使用 `id`。

- 新建教师、班级、小组或学生遇到同类同名档案，返回 409 `same_name`，`error.detail.candidates` 包含已有档案。确认是不同对象后可加 `allow_same_name:true` 重交，也可直接选用已有 ID。此开关只豁免同名检查，不豁免编号冲突。
- 教师 `code` 与同类 audience `code` 非空时分别查重。比较按 Unicode NFKC、首尾去空格和 casefold 进行；冲突返回 409 `duplicate_code` 与 candidates。编号最多 80 字，不接受控制字符。已有重复代号保留，原档案不改该代号的编辑仍允许。
- 三类 POST 创建入口均可带 `operation_id`（最多 128 字）。查重与创建在同一写事务内进行，相同动作、相同有效内容返回原档案及 `replayed:true`；同编号换动作、父班级或有效内容返回 409 `operation_conflict`。确认同名开关不计入内容哈希。重放返回当前档案，不重新分配教师，也不恢复之后已移出的班级关系。
- `POST /api/audiences` 支持 `kind,name,code?,note?,teacher_ids?,operation_id?,allow_same_name?`。`PATCH /api/audiences/{aid}` 和教师 PATCH 支持 `base_revision`；传入时必须匹配资料版本，否则 409 `profile_changed`。为兼容已有客户端，未传版本仍接受。网页总是传版本。
- 姓名、编号和备注编辑不更换 ID，不改写既有使用记录与练习安排。名单及教师关系的 `revision` 与资料 `profile_revision` 分别校验，不可混用。读取名单/归属使用一致性读事务，不提前获取写锁。

系统没有按姓名合并档案、硬删除人员或认证教师身份。操作编号由前端在当前页面/弹窗生命周期内保存，跨刷新不保证恢复。
