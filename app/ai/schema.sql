CREATE TABLE IF NOT EXISTS ai_batch (
 id TEXT PRIMARY KEY, state TEXT NOT NULL, config_json TEXT NOT NULL,
 max_cost_micro INTEGER NOT NULL, max_requests INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_job (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES ai_batch(id),
 source_file_id TEXT REFERENCES source_file(id), title TEXT NOT NULL,
 state TEXT NOT NULL, stage TEXT NOT NULL, progress_json TEXT NOT NULL DEFAULT '{}',
 document_json TEXT NOT NULL DEFAULT '{}', error_json TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_question (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES ai_job(id), question_key TEXT NOT NULL,
 original_qno TEXT NOT NULL, qtype TEXT NOT NULL, state TEXT NOT NULL,
 quality_state TEXT NOT NULL DEFAULT 'pending', current_revision INTEGER NOT NULL DEFAULT 1,
 request_generation INTEGER NOT NULL DEFAULT 0,
 base_version_id TEXT REFERENCES question_version(id), source_json TEXT NOT NULL,
 solve_json TEXT, review_json TEXT, error_json TEXT, adopted_version_id TEXT REFERENCES question_version(id),
 UNIQUE(job_id, question_key)
);
CREATE TABLE IF NOT EXISTS ai_step_run (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES ai_job(id),
 ai_question_id TEXT REFERENCES ai_question(id), stage TEXT NOT NULL, attempt INTEGER NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
 lease_owner TEXT, lease_until REAL, input_hash TEXT NOT NULL, output_hash TEXT,
 model TEXT, prompt_version TEXT, usage_json TEXT, reserved_cost_micro INTEGER NOT NULL DEFAULT 0,
 actual_cost_micro INTEGER, billing_state TEXT, error_json TEXT, artifact_json TEXT,
 created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS ai_candidate (
 id TEXT PRIMARY KEY, ai_question_id TEXT NOT NULL REFERENCES ai_question(id),
 revision INTEGER NOT NULL, base_version_id TEXT, content_json TEXT NOT NULL,
 content_hash TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(ai_question_id,revision)
);
CREATE TABLE IF NOT EXISTS ai_evidence_region (
 id TEXT PRIMARY KEY, ai_question_id TEXT NOT NULL REFERENCES ai_question(id),
 region_key TEXT NOT NULL, page_no INTEGER NOT NULL, bbox_json TEXT NOT NULL,
 role TEXT NOT NULL, answer_free INTEGER NOT NULL DEFAULT 0, rel_path TEXT,
 UNIQUE(ai_question_id,region_key)
);
CREATE TABLE IF NOT EXISTS ai_decision (
 id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES ai_candidate(id),
 candidate_hash TEXT NOT NULL, action TEXT NOT NULL, detail_json TEXT NOT NULL,
 operator TEXT NOT NULL, target_version_id TEXT, adoption_key TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_job_state ON ai_job(state,created_at);
CREATE INDEX IF NOT EXISTS ai_run_job ON ai_step_run(job_id,state);
CREATE INDEX IF NOT EXISTS ai_question_job ON ai_question(job_id,state);
