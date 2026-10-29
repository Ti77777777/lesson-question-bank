-- 习题课题库系统 · 结构定义 v1
-- 依据《系统大纲》v0.2 第 3、4、8、10、11、14 节
-- 约定：
--   1. 所有主键为带前缀编号（见 id_seq），不编码章节/难度/年份。
--   2. 所有时间为一等公民字段 ISO 8601 带时区，形如 2026-10-27T00:30:00+08:00。
--   3. JSON 列一律保存 UTF-8 文本，结构见 docs/API.md「JSON 列结构」。
--   4. 不物理删除题目与版本；停用、合并均保留历史。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- 元信息
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS id_seq (
    prefix   TEXT PRIMARY KEY,
    next_val INTEGER NOT NULL
);

-- ---------------------------------------------------------------- 资料与出处
CREATE TABLE IF NOT EXISTS source (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('exam','textbook','workbook','lecture','self','other')),
    edition       TEXT,                                   -- 版次 / 年份
    region        TEXT,
    exam_type     TEXT,                                   -- 明确保存的考试类别；留空按来源标题识别
    grade         TEXT,                                   -- senior1 / senior2 / senior3；留空按标题识别
    publisher     TEXT,
    usage_scope   TEXT,                                   -- 使用范围
    license_basis TEXT,                                   -- 许可依据（与来源类型分开记录）
    pending_note  TEXT,                                   -- 待确认事项
    note          TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_file (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL REFERENCES source(id),
    filename    TEXT NOT NULL,
    rel_path    TEXT NOT NULL,                            -- 相对项目根，如 sources/SRC000001/a.pdf
    sha256      TEXT,
    bytes       INTEGER,
    pages       INTEGER,
    uploaded_at TEXT NOT NULL,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS ix_source_file_src ON source_file(source_id);
CREATE INDEX IF NOT EXISTS ix_source_file_sha ON source_file(sha256);

-- 出处：一题可关联多个出处，并标明该出处对应哪个题目版本
CREATE TABLE IF NOT EXISTS occurrence (
    id                  TEXT PRIMARY KEY,
    question_id         TEXT NOT NULL REFERENCES question(id),
    question_version_id TEXT REFERENCES question_version(id),  -- 空 = 对应当前版本
    source_id           TEXT REFERENCES source(id),
    source_file_id      TEXT REFERENCES source_file(id),
    pdf_page            INTEGER,                          -- PDF 页序
    print_page          TEXT,                             -- 印刷页码
    qno                 TEXT,                             -- 原题号
    screenshot_asset_id TEXT REFERENCES asset(id),
    raw_text            TEXT,                             -- 原文/OCR 结果
    ocr_tool            TEXT,
    ocr_at              TEXT,
    ocr_suspect         TEXT,                             -- 识别疑点位置
    pending             INTEGER NOT NULL DEFAULT 0,       -- 1 = 出处待确认
    note                TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_occ_q ON occurrence(question_id);
CREATE INDEX IF NOT EXISTS ix_occ_src ON occurrence(source_id);

-- ---------------------------------------------------------------- 收题箱
CREATE TABLE IF NOT EXISTS inbox_item (
    id             TEXT PRIMARY KEY,
    raw_text       TEXT,
    file_name      TEXT,
    file_rel_path  TEXT,
    source_hint    TEXT,                                  -- 未知则显式写“待确认”
    note            TEXT,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','extracted','organized','rejected')),
    question_id    TEXT REFERENCES question(id),
    fail_reason    TEXT,
    operator       TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inbox_status ON inbox_item(status, created_at);

-- ---------------------------------------------------------------- 题目
CREATE TABLE IF NOT EXISTS question (
    id                 TEXT PRIMARY KEY,
    short_name         TEXT,
    status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    status_reason      TEXT,
    current_version_id TEXT REFERENCES question_version(id),
    merged_into        TEXT REFERENCES question(id),      -- 误建重复题合并后保留跳转
    -- 三维难度（1–5，各自独立，不自动合成）
    difficulty_calc    INTEGER CHECK (difficulty_calc    BETWEEN 1 AND 5),
    difficulty_think   INTEGER CHECK (difficulty_think   BETWEEN 1 AND 5),
    difficulty_scope   INTEGER CHECK (difficulty_scope   BETWEEN 1 AND 5),
    difficulty_level   TEXT,                              -- 评价所适用的学生层次
    difficulty_note    TEXT,
    -- 教学价值
    rec_level          TEXT CHECK (rec_level IN ('S','A','B','C')),
    rec_reason         TEXT,
    time_solve_min     REAL,                              -- 预计独立作答
    time_explain_min   REAL,                              -- 预计讲解
    note               TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    created_by         TEXT
);
CREATE INDEX IF NOT EXISTS ix_question_status ON question(status);
CREATE INDEX IF NOT EXISTS ix_question_merged ON question(merged_into);

-- 题目版本：提交后不可覆盖
CREATE TABLE IF NOT EXISTS question_version (
    id             TEXT PRIMARY KEY,
    question_id    TEXT NOT NULL REFERENCES question(id),
    version_no     INTEGER NOT NULL,
    qtype          TEXT NOT NULL CHECK (qtype IN ('single','multi','blank','solve')),
    stem           TEXT NOT NULL,                         -- 公共题干 LaTeX
    options        TEXT,                                  -- JSON [{key,text}]
    answer         TEXT,                                  -- 简明答案 LaTeX（含多小问时按小问分行）
    answer_keys    TEXT,                                  -- JSON 选择题正确选项标识 ["A"]
    subquestions   TEXT,                                  -- JSON [{key,text,depends_on,answer}]
    analysis       TEXT,                                  -- 整题解析（未拆分为 solution 时使用）
    material_refs  TEXT,                                  -- JSON 素材 ID 数组
    teaching       TEXT,                                  -- JSON {why,key_step,probe,pitfalls,prereq}
    total_points   REAL,
    change_note    TEXT,                                  -- 本次修订说明及依据
    review_state   TEXT NOT NULL DEFAULT 'draft' CHECK (review_state IN ('draft','submitted')),
    created_at     TEXT NOT NULL,
    created_by     TEXT,
    UNIQUE (question_id, version_no)
);
CREATE INDEX IF NOT EXISTS ix_qv_question ON question_version(question_id, version_no);

-- 解法：标在具体版本上，一题可多解
CREATE TABLE IF NOT EXISTS solution (
    id                  TEXT PRIMARY KEY,
    question_version_id TEXT NOT NULL REFERENCES question_version(id),
    label               TEXT NOT NULL,                    -- 解法一 / 定义法
    approach            TEXT,                             -- 思路
    steps               TEXT NOT NULL,                    -- 详细步骤 LaTeX
    conditions          TEXT,                             -- 适用条件
    is_primary          INTEGER NOT NULL DEFAULT 0,
    order_no            INTEGER NOT NULL DEFAULT 0,
    teacher_tip         TEXT,                             -- 讲评提示
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sol_qv ON solution(question_version_id, order_no);

-- ---------------------------------------------------------------- 分类与标签
CREATE TABLE IF NOT EXISTS tag (
    id          TEXT PRIMARY KEY,
    dimension   TEXT NOT NULL CHECK (dimension IN
                ('knowledge','textbook','prereq','method','idea','purpose','error')),
    name        TEXT NOT NULL,
    definition  TEXT,
    parent_id   TEXT REFERENCES tag(id),
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    merged_into TEXT REFERENCES tag(id),
    note        TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (dimension, name)
);
CREATE INDEX IF NOT EXISTS ix_tag_dim ON tag(dimension, status);

CREATE TABLE IF NOT EXISTS question_tag (
    question_id TEXT NOT NULL REFERENCES question(id),
    tag_id      TEXT NOT NULL REFERENCES tag(id),
    role        TEXT NOT NULL DEFAULT 'primary' CHECK (role IN ('primary','secondary')),
    source      TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','ai')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (question_id, tag_id, role)
);
CREATE INDEX IF NOT EXISTS ix_qt_tag ON question_tag(tag_id);

CREATE TABLE IF NOT EXISTS solution_tag (
    solution_id TEXT NOT NULL REFERENCES solution(id),
    tag_id      TEXT NOT NULL REFERENCES tag(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (solution_id, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_st_tag ON solution_tag(tag_id);

-- ---------------------------------------------------------------- 题目关系
CREATE TABLE IF NOT EXISTS relation (
    id         TEXT PRIMARY KEY,
    from_id    TEXT NOT NULL REFERENCES question(id),
    to_id      TEXT NOT NULL REFERENCES question(id),
    rel_type   TEXT NOT NULL CHECK (rel_type IN
               ('cond_variant','concl_variant','param','generalize','special',
                'inverse','same_method','error_contrast','correction')),
    note       TEXT,                                      -- 改了什么、保留什么、训练目标
    confirmed  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    CHECK (from_id <> to_id)                              -- 禁止自指
);
CREATE INDEX IF NOT EXISTS ix_rel_from ON relation(from_id, rel_type);
CREATE INDEX IF NOT EXISTS ix_rel_to   ON relation(to_id, rel_type);

-- ---------------------------------------------------------------- 素材
CREATE TABLE IF NOT EXISTS asset (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN
                ('original_image','tidy_image','tikz','geogebra','screenshot','other')),
    filename    TEXT,
    rel_path    TEXT,                                     -- 相对项目根
    source_text TEXT,                                     -- TikZ / GeoGebra 源码
    sha256      TEXT,
    width       INTEGER,
    height      INTEGER,
    note        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_asset_sha ON asset(sha256);

-- ---------------------------------------------------------------- 审校
CREATE TABLE IF NOT EXISTS review (
    id          TEXT PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('question_version','solution','asset')),
    target_id   TEXT NOT NULL,
    item        TEXT NOT NULL CHECK (item IN
                ('stem','answer','solution','figure','classification','boundary','method_scope')),
    result      TEXT NOT NULL CHECK (result IN ('pending','pass','fail','dispute','na')),
    reviewer    TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_review_target ON review(target_type, target_id, item, created_at);

-- ---------------------------------------------------------------- 题单
CREATE TABLE IF NOT EXISTS collection (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    topic        TEXT,
    level        TEXT,                                    -- 学生层次
    prereqs      TEXT,
    objectives   TEXT,
    duration_min INTEGER,
    purpose      TEXT NOT NULL DEFAULT 'handout'
                 CHECK (purpose IN ('handout','quiz','homework','diagnostic')),
    coverage     TEXT,                                    -- 知识/方法覆盖要求
    layout_options TEXT,                                  -- JSON 题单排版选项与逐题覆盖
    template     TEXT NOT NULL DEFAULT 'exbook',
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','ready','archived')),
    note         TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_section (
    id            TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collection(id),
    title         TEXT NOT NULL,
    order_no      INTEGER NOT NULL DEFAULT 0,
    kind          TEXT NOT NULL DEFAULT 'question'
                  CHECK (kind IN ('question','concept','summary','note')),
    intro         TEXT,                                   -- 分节导语 / 概念回顾 / 方法小结
    note          TEXT
);
CREATE INDEX IF NOT EXISTS ix_sec_col ON collection_section(collection_id, order_no);

CREATE TABLE IF NOT EXISTS collection_item (
    id                  TEXT PRIMARY KEY,
    collection_id       TEXT NOT NULL REFERENCES collection(id),
    section_id          TEXT REFERENCES collection_section(id),
    question_id         TEXT NOT NULL REFERENCES question(id),
    question_version_id TEXT NOT NULL REFERENCES question_version(id),
    order_no            INTEGER NOT NULL DEFAULT 0,
    role                TEXT NOT NULL DEFAULT 'example'
                        CHECK (role IN ('diagnostic','example','variant','practice',
                                        'homework','summary','challenge')),
    solution_id         TEXT REFERENCES solution(id),     -- 教师版选用解法，空 = 全部
    hint_level          TEXT NOT NULL DEFAULT 'none'
                        CHECK (hint_level IN ('none','keyword','method','full')),
    time_solve_min      REAL,
    time_explain_min    REAL,
    space_mm            REAL,                             -- 作答留白高度
    page_break_before   INTEGER NOT NULL DEFAULT 0,
    required            INTEGER NOT NULL DEFAULT 1,
    points              REAL,
    subq_keys           TEXT,                             -- JSON 拆用的小问标识
    note                TEXT,                             -- 本次讲评备注，不改公共解析
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_item_col ON collection_item(collection_id, order_no);
CREATE INDEX IF NOT EXISTS ix_item_q ON collection_item(question_id);

-- ---------------------------------------------------------------- 发布物
CREATE TABLE IF NOT EXISTS release (
    id             TEXT PRIMARY KEY,
    collection_id  TEXT NOT NULL REFERENCES collection(id),
    snapshot       TEXT NOT NULL,                         -- JSON 完整快照
    template       TEXT NOT NULL DEFAULT 'exbook',
    build_dir      TEXT,
    student_path   TEXT,
    teacher_path   TEXT,
    answer_path    TEXT,
    student_sha256 TEXT,
    teacher_sha256 TEXT,
    checks         TEXT,                                  -- JSON 构建与泄露检查结果
    status         TEXT NOT NULL DEFAULT 'built'
                   CHECK (status IN ('built','failed','archived')),
    pages_student  INTEGER,
    pages_answer   INTEGER,
    pages_teacher  INTEGER,
    note           TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rel_col ON release(collection_id, created_at);

-- ---------------------------------------------------------------- 使用对象与记录
CREATE TABLE IF NOT EXISTS audience (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('class','group','student')),
    name       TEXT NOT NULL,
    code       TEXT,
    note       TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignment (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    collection_id TEXT REFERENCES collection(id),
    release_id    TEXT REFERENCES release(id),
    audience_id   TEXT REFERENCES audience(id),
    plan_date     TEXT,
    requirements  TEXT,
    must_do       TEXT,
    optional      TEXT,
    due_date      TEXT,
    answer_policy TEXT,                                   -- 答案发放安排
    status        TEXT NOT NULL DEFAULT 'planned'
                  CHECK (status IN ('planned','distributed','closed','cancelled')),
    note          TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_asg_aud ON assignment(audience_id, plan_date);

CREATE TABLE IF NOT EXISTS usage_log (
    id            TEXT PRIMARY KEY,
    assignment_id TEXT REFERENCES assignment(id),
    release_id    TEXT REFERENCES release(id),
    audience_id   TEXT REFERENCES audience(id),
    used_on       TEXT NOT NULL,                          -- 实际使用日期
    purpose       TEXT NOT NULL
                  CHECK (purpose IN ('class','homework','quiz','review','other')),
    items         TEXT,                                   -- JSON 实际使用的条目 ID
    note          TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usage_aud ON usage_log(audience_id, used_on);
CREATE INDEX IF NOT EXISTS ix_usage_rel ON usage_log(release_id);

CREATE TABLE IF NOT EXISTS feedback (
    id             TEXT PRIMARY KEY,
    usage_id       TEXT REFERENCES usage_log(id),
    common_errors  TEXT,
    actual_minutes REAL,
    effect         TEXT,
    suggestion     TEXT,                                  -- 下一次讲解建议
    sample_size    INTEGER,                               -- 正确率必须同时保存统计口径
    stat_scope     TEXT,
    stat_date      TEXT,
    level          TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fb_usage ON feedback(usage_id);

-- 题目纠错：绑定发现问题时的版本，保留每次处理过程。
CREATE TABLE IF NOT EXISTS question_correction (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES question(id),
    version_id TEXT NOT NULL REFERENCES question_version(id),
    category TEXT NOT NULL CHECK(category IN ('stem','answer','solution','figure','tags','source','difficulty','other')),
    description TEXT NOT NULL,
    suggestion TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','resolved','dismissed')),
    resolution_note TEXT,
    fixed_version_id TEXT REFERENCES question_version(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_correction_question ON question_correction(question_id, status);
CREATE TABLE IF NOT EXISTS correction_event (
    id TEXT PRIMARY KEY,
    correction_id TEXT NOT NULL REFERENCES question_correction(id),
    status TEXT NOT NULL CHECK(status IN ('pending','resolved','dismissed')),
    note TEXT NOT NULL,
    fixed_version_id TEXT REFERENCES question_version(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_correction_event ON correction_event(correction_id, id);

-- ---------------------------------------------------------------- 导入与审计
CREATE TABLE IF NOT EXISTS import_batch (
    id          TEXT PRIMARY KEY,
    filename    TEXT,
    rel_path    TEXT,
    mapping     TEXT,                                     -- JSON 字段映射
    stats       TEXT,                                     -- JSON {total,ok,failed,skipped}
    errors      TEXT,                                     -- JSON [{row, message}]
    status      TEXT NOT NULL DEFAULT 'preview'
                CHECK (status IN ('preview','applied','partial','failed')),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT PRIMARY KEY,
    at          TEXT NOT NULL,
    actor       TEXT,
    action      TEXT NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_target ON audit_log(target_type, target_id, at);
-- Personal workbench preferences and reversible source metadata changes.
CREATE TABLE IF NOT EXISTS search_preset (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, query_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_change (
 id TEXT PRIMARY KEY, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
 state TEXT NOT NULL, created_at TEXT NOT NULL
);
