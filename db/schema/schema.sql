-- =============================================================================
-- AI Tutor schema for Oracle AI Database 26ai (Free container, FREEPDB1)
-- Status: DRAFT, written without access to a live 26ai instance. Run it, and
-- paste any ORA- errors back so they can be fixed.
--
-- Design rules baked into the schema:
--   1. Only synthetic learners exist (CHECK constraint). No real children.
--   2. No names, schools, or free-text identity: learners are pseudonymous GUIDs.
--   3. Answer keys live in a table the tutor role cannot read.
--   4. The hint ladder is enforced here, not in the prompt.
--   5. Teachers see only aggregate views with a minimum cohort of 5.
--   6. Safety disclosures are insert-only for the tutor; one role reads them.
--   7. Only redacted text is stored, and it expires (purge_after).
--
-- Run order: PART 0 as SYSTEM, then PART 1 as TUTOR_OWNER, then seed.sql.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- PART 0: run as SYSTEM inside the pluggable database
-- ---------------------------------------------------------------------------
-- ALTER SESSION SET CONTAINER = FREEPDB1;
-- CREATE USER tutor_owner IDENTIFIED BY "&owner_password" QUOTA UNLIMITED ON users;
-- GRANT DB_DEVELOPER_ROLE TO tutor_owner;
--
-- CREATE ROLE tutor_app_role;     -- the runtime service account
-- CREATE ROLE leak_check_role;    -- the only role that can read answer keys
-- CREATE ROLE teacher_role;       -- aggregate views only
-- CREATE ROLE safeguard_role;     -- the designated human for Channel B
--
-- Then create one login per role (tutor_app, leak_check, teacher_demo,
-- safeguard_demo) and grant each its role. Give each a CREATE SESSION grant.

-- ---------------------------------------------------------------------------
-- PART 1: run as TUTOR_OWNER
-- ---------------------------------------------------------------------------

-- ===== Reference data ========================================================

CREATE TABLE age_bands (
  band_code  VARCHAR2(10)  PRIMARY KEY,
  label      VARCHAR2(40)  NOT NULL,
  min_age    NUMBER(2)     NOT NULL,
  max_age    NUMBER(2)     NOT NULL,
  config     JSON          NOT NULL,   -- tone, turn length, safety topics per band
  CONSTRAINT chk_band_ages CHECK (min_age <= max_age)
);

CREATE TABLE cohorts (
  cohort_id    NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  label        VARCHAR2(60) NOT NULL,   -- e.g. 'Demo class A', never a real school
  region_code  VARCHAR2(10)             -- coarse region only, for local/national roll-ups
);

CREATE TABLE learners (
  learner_id    RAW(16)      DEFAULT SYS_GUID() PRIMARY KEY,
  cohort_id     NUMBER       NOT NULL REFERENCES cohorts(cohort_id),
  band_code     VARCHAR2(10) NOT NULL REFERENCES age_bands(band_code),
  is_synthetic  NUMBER(1)    DEFAULT 1 NOT NULL,
  created_at    TIMESTAMP    DEFAULT SYSTIMESTAMP NOT NULL,
  -- Prototype guard: drop this only after compliance review for real children.
  CONSTRAINT chk_synthetic_only CHECK (is_synthetic = 1)
);

-- ===== Lesson content (the RAG corpus) =======================================

CREATE TABLE lesson_chunks (
  chunk_id               NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  band_code              VARCHAR2(10) NOT NULL REFERENCES age_bands(band_code),
  topic_code             VARCHAR2(40) NOT NULL,
  kind                   VARCHAR2(20) NOT NULL,
  ladder_rung            NUMBER(1),
  content                CLOB         NOT NULL,
  teaching_note          CLOB,                    -- why a planted error is wrong, etc.
  contains_planted_error NUMBER(1)    DEFAULT 0 NOT NULL,
  source_ref             VARCHAR2(200),           -- vetted source, or 'SYNTHETIC-DEMO'
  reviewed_by            VARCHAR2(60),            -- a human reviewer, never null when approved
  approved               NUMBER(1)    DEFAULT 0 NOT NULL,
  embedding              VECTOR(384, FLOAT32),    -- all-MiniLM-L12-v2 produces 384 dims
  CONSTRAINT chk_chunk_kind CHECK (kind IN
    ('CONCEPT','HINT','WORKED_EXAMPLE','SAFETY_BITE','HABIT','PLANTED_ERROR')),
  CONSTRAINT chk_rung_range CHECK (ladder_rung IS NULL OR ladder_rung BETWEEN 0 AND 4),
  CONSTRAINT chk_rung_required CHECK
    (kind NOT IN ('CONCEPT','HINT','WORKED_EXAMPLE') OR ladder_rung IS NOT NULL),
  CONSTRAINT chk_worked_is_rung4 CHECK (kind <> 'WORKED_EXAMPLE' OR ladder_rung = 4),
  CONSTRAINT chk_planted_flag CHECK
    ((kind = 'PLANTED_ERROR' AND contains_planted_error = 1)
     OR (kind <> 'PLANTED_ERROR' AND contains_planted_error = 0)),
  CONSTRAINT chk_approved_reviewed CHECK (approved = 0 OR reviewed_by IS NOT NULL)
);

CREATE INDEX ix_chunks_lookup ON lesson_chunks (band_code, topic_code, ladder_rung);

-- Optional ANN index. HNSW needs the vector memory pool (VECTOR_MEMORY_SIZE) and a
-- restart on the Free container; with ~50 chunks, exact search is already instant.
-- CREATE VECTOR INDEX ix_chunks_vec ON lesson_chunks (embedding)
--   ORGANIZATION INMEMORY NEIGHBOR GRAPH DISTANCE COSINE WITH TARGET ACCURACY 95;

CREATE TABLE problems (
  problem_id   NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  band_code    VARCHAR2(10) NOT NULL REFERENCES age_bands(band_code),
  topic_code   VARCHAR2(40) NOT NULL,
  prompt_text  VARCHAR2(500) NOT NULL,
  is_transfer  NUMBER(1) DEFAULT 0 NOT NULL   -- 1 = end-of-session transfer problem
);

-- Answer keys are physically separate so the tutor role can never read them.
CREATE TABLE problem_answers (
  problem_id    NUMBER PRIMARY KEY REFERENCES problems(problem_id),
  answer_text   VARCHAR2(200) NOT NULL,
  accepted_forms JSON                          -- e.g. ["5/6","0.833"]
);

CREATE TABLE lesson_completions (
  learner_id    RAW(16) NOT NULL REFERENCES learners(learner_id),
  chunk_id      NUMBER  NOT NULL REFERENCES lesson_chunks(chunk_id),
  completed_at  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_completions PRIMARY KEY (learner_id, chunk_id)
);

-- ===== Sessions and turns ====================================================

CREATE TABLE sessions (
  session_id           NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  learner_id           RAW(16) NOT NULL REFERENCES learners(learner_id),
  started_at           TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  ended_at             TIMESTAMP,
  ai_disclosure_shown  NUMBER(1) DEFAULT 0 NOT NULL,
  sim_persona          VARCHAR2(20),            -- set for simulation runs
  CONSTRAINT chk_disclosure_flag CHECK (ai_disclosure_shown IN (0,1)),
  -- A session cannot close unless the child was told they are talking to an AI.
  CONSTRAINT chk_disclosure_before_end CHECK (ended_at IS NULL OR ai_disclosure_shown = 1),
  CONSTRAINT chk_sim_persona CHECK (sim_persona IS NULL OR sim_persona IN
    ('GUESSER','EXTRACTOR','STUCK','DISCLOSER','JAILBREAKER'))
);

CREATE TABLE turns (
  turn_id         NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id      NUMBER NOT NULL REFERENCES sessions(session_id),
  seq             NUMBER NOT NULL,
  speaker         VARCHAR2(10) NOT NULL,
  intent          VARCHAR2(20),
  redacted_text   CLOB NOT NULL,                -- PII stripped BEFORE it is stored or sent
  rung_after      NUMBER(1),
  moderation      VARCHAR2(10) DEFAULT 'NA' NOT NULL,
  leak_check      VARCHAR2(10) DEFAULT 'NA' NOT NULL,
  created_at      TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  purge_after     TIMESTAMP DEFAULT (SYSTIMESTAMP + INTERVAL '30' DAY) NOT NULL,
  CONSTRAINT uq_turn_seq UNIQUE (session_id, seq),
  CONSTRAINT chk_speaker CHECK (speaker IN ('CHILD','TUTOR','SYSTEM')),
  CONSTRAINT chk_intent CHECK (intent IS NULL OR intent IN
    ('ATTEMPT','CONCEPT_Q','EXTRACTION','OFF_TOPIC','DISCLOSURE')),
  CONSTRAINT chk_moderation CHECK (moderation IN ('PASS','BLOCKED','NA')),
  CONSTRAINT chk_leak CHECK (leak_check IN ('PASS','BLOCKED','NA'))
);

CREATE TABLE guardrail_events (
  event_id   NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  turn_id    NUMBER NOT NULL REFERENCES turns(turn_id) ON DELETE CASCADE,
  kind       VARCHAR2(20) NOT NULL,
  detail     VARCHAR2(200),
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT chk_event_kind CHECK (kind IN
    ('INPUT_BLOCK','OUTPUT_BLOCK','LEAK_BLOCK','INJECTION_SUSPECT','PII_REDACTED'))
);

CREATE TABLE usage_receipts (
  receipt_id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id        NUMBER NOT NULL REFERENCES sessions(session_id),
  asked_ai          VARCHAR2(400),   -- written with the child at session end
  figured_out_self  VARCHAR2(400),
  verified          VARCHAR2(400),
  created_at        TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
);

-- ===== Hint ladder state =====================================================

CREATE TABLE ladder_state (
  learner_id              RAW(16) NOT NULL REFERENCES learners(learner_id),
  problem_id              NUMBER  NOT NULL REFERENCES problems(problem_id),
  rung_unlocked           NUMBER(1) DEFAULT 0 NOT NULL,
  attempts_since_advance  NUMBER(3) DEFAULT 0 NOT NULL,
  attempts_total          NUMBER(4) DEFAULT 0 NOT NULL,
  updated_at              TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_ladder PRIMARY KEY (learner_id, problem_id),
  CONSTRAINT chk_rung_unlocked CHECK (rung_unlocked BETWEEN 0 AND 4)
);

-- ===== Channel B: safety disclosures =========================================

CREATE TABLE disclosures (
  disclosure_id      NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id         NUMBER NOT NULL REFERENCES sessions(session_id),
  turn_id            NUMBER REFERENCES turns(turn_id) ON DELETE SET NULL,
  category           VARCHAR2(20) NOT NULL,
  excerpt_redacted   VARCHAR2(1000),   -- survives the turn's purge so a human can act
  status             VARCHAR2(10) DEFAULT 'OPEN' NOT NULL,
  child_informed     NUMBER(1) DEFAULT 0 NOT NULL,  -- the child was told what happens next
  created_at         TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT chk_disc_cat CHECK (category IN ('SAFETY','HOME','BULLYING','ONLINE_CONTACT','OTHER')),
  CONSTRAINT chk_disc_status CHECK (status IN ('OPEN','ACKED','CLOSED'))
);

-- ===== Ladder logic (enforced in the database) ===============================

-- Call when the child makes a real attempt on a problem.
CREATE OR REPLACE PROCEDURE record_attempt (
  p_learner_id RAW,
  p_problem_id NUMBER
) AS
BEGIN
  MERGE INTO ladder_state t
  USING (SELECT p_learner_id AS learner_id, p_problem_id AS problem_id FROM dual) s
  ON (t.learner_id = s.learner_id AND t.problem_id = s.problem_id)
  WHEN MATCHED THEN UPDATE SET
    t.attempts_since_advance = t.attempts_since_advance + 1,
    t.attempts_total         = t.attempts_total + 1,
    t.updated_at             = SYSTIMESTAMP
  WHEN NOT MATCHED THEN INSERT
    (learner_id, problem_id, rung_unlocked, attempts_since_advance, attempts_total)
    VALUES (s.learner_id, s.problem_id, 0, 1, 1);
END record_attempt;
/

-- Climb one rung ONLY if the child has attempted something since the last climb.
-- Rapid "give me the next hint" requests with no attempt do not advance anything.
CREATE OR REPLACE PROCEDURE advance_rung (
  p_learner_id RAW,
  p_problem_id NUMBER,
  p_rung       OUT NUMBER
) AS
BEGIN
  UPDATE ladder_state
     SET rung_unlocked          = rung_unlocked + 1,
         attempts_since_advance = 0,
         updated_at             = SYSTIMESTAMP
   WHERE learner_id = p_learner_id
     AND problem_id = p_problem_id
     AND rung_unlocked < 4
     AND attempts_since_advance >= 1;

  BEGIN
    SELECT rung_unlocked INTO p_rung
      FROM ladder_state
     WHERE learner_id = p_learner_id AND problem_id = p_problem_id;
  EXCEPTION WHEN NO_DATA_FOUND THEN
    p_rung := 0;
  END;
END advance_rung;
/

-- Hint retrieval: vector search restricted to approved content at or below the
-- rung this learner has earned. The answer key is never joined. Planted-error
-- chunks are excluded; they are delivered by a separate lesson flow.
-- Exact search (no APPROX) because the corpus is tiny.
CREATE OR REPLACE FUNCTION get_hint_chunks (
  p_learner_id RAW,
  p_problem_id NUMBER,
  p_query_vec  VECTOR,
  p_k          PLS_INTEGER DEFAULT 3
) RETURN SYS_REFCURSOR AS
  rc SYS_REFCURSOR;
BEGIN
  OPEN rc FOR
    SELECT c.chunk_id, c.kind, c.ladder_rung, c.content, c.source_ref
      FROM lesson_chunks c
      JOIN problems p
        ON p.problem_id = p_problem_id
       AND p.topic_code = c.topic_code
       AND p.band_code  = c.band_code
      LEFT JOIN ladder_state l
        ON l.learner_id = p_learner_id AND l.problem_id = p_problem_id
     WHERE c.approved = 1
       AND c.contains_planted_error = 0
       AND c.kind IN ('CONCEPT','HINT','WORKED_EXAMPLE')
       AND c.ladder_rung <= NVL(l.rung_unlocked, 0)
     ORDER BY VECTOR_DISTANCE(c.embedding, p_query_vec, COSINE)
     FETCH FIRST p_k ROWS ONLY;
  RETURN rc;
END get_hint_chunks;
/

-- Retention: run on a schedule (DBMS_SCHEDULER) or by hand.
CREATE OR REPLACE PROCEDURE purge_expired_turns AS
BEGIN
  DELETE FROM turns WHERE purge_after < SYSTIMESTAMP;
  COMMIT;
END purge_expired_turns;
/

-- ===== Channel A: aggregate views (minimum cohort = 5) =======================
-- Known limit: comparing two cohorts' totals can still leak small differences.
-- Mitigate later with noise or coarser time buckets; fine for the prototype.

CREATE OR REPLACE VIEW v_cohort_topic_stalls AS
SELECT lr.cohort_id,
       p.topic_code,
       TRUNC(ls.updated_at, 'IW')            AS week_start,
       COUNT(DISTINCT ls.learner_id)         AS learners_at_rung3_plus,
       (SELECT COUNT(*) FROM learners x WHERE x.cohort_id = lr.cohort_id) AS cohort_size
  FROM ladder_state ls
  JOIN learners lr ON lr.learner_id = ls.learner_id
  JOIN problems p  ON p.problem_id  = ls.problem_id
 WHERE ls.rung_unlocked >= 3
 GROUP BY lr.cohort_id, p.topic_code, TRUNC(ls.updated_at, 'IW')
HAVING COUNT(DISTINCT ls.learner_id) >= 5;

CREATE OR REPLACE VIEW v_ai_literacy_progress AS
SELECT lr.cohort_id,
       c.topic_code,
       c.kind,
       COUNT(DISTINCT lc.learner_id) AS learners_completed
  FROM lesson_completions lc
  JOIN lesson_chunks c ON c.chunk_id = lc.chunk_id
  JOIN learners lr     ON lr.learner_id = lc.learner_id
 WHERE c.kind IN ('SAFETY_BITE','HABIT','PLANTED_ERROR')
 GROUP BY lr.cohort_id, c.topic_code, c.kind
HAVING COUNT(DISTINCT lc.learner_id) >= 5;

-- Simulation harness metrics (owner only; synthetic runs, not outcome data).
CREATE OR REPLACE VIEW v_sim_metrics AS
SELECT s.sim_persona,
       COUNT(DISTINCT s.session_id) AS sessions,
       SUM(CASE WHEN t.speaker = 'TUTOR' THEN 1 ELSE 0 END)       AS tutor_turns,
       SUM(CASE WHEN t.leak_check  = 'BLOCKED' THEN 1 ELSE 0 END) AS leaks_blocked,
       SUM(CASE WHEN t.moderation  = 'BLOCKED' THEN 1 ELSE 0 END) AS moderation_blocked,
       SUM(CASE WHEN t.intent = 'DISCLOSURE'   THEN 1 ELSE 0 END) AS disclosure_turns
  FROM sessions s
  JOIN turns t ON t.session_id = s.session_id
 WHERE s.sim_persona IS NOT NULL
 GROUP BY s.sim_persona;

-- ===== Grants (least privilege) ==============================================

-- Tutor runtime: read content, write its own trail, insert-only on disclosures.
GRANT SELECT ON age_bands          TO tutor_app_role;
GRANT SELECT ON lesson_chunks      TO tutor_app_role;
GRANT SELECT ON problems           TO tutor_app_role;     -- NOT problem_answers
GRANT SELECT, INSERT ON sessions         TO tutor_app_role;
GRANT UPDATE (ended_at, ai_disclosure_shown) ON sessions TO tutor_app_role;
GRANT SELECT, INSERT ON turns            TO tutor_app_role;
GRANT INSERT ON guardrail_events   TO tutor_app_role;
GRANT INSERT ON usage_receipts     TO tutor_app_role;
GRANT INSERT ON lesson_completions TO tutor_app_role;
GRANT SELECT ON ladder_state       TO tutor_app_role;
GRANT INSERT ON disclosures        TO tutor_app_role;     -- insert only; cannot read them back
GRANT EXECUTE ON record_attempt    TO tutor_app_role;
GRANT EXECUTE ON advance_rung      TO tutor_app_role;
GRANT EXECUTE ON get_hint_chunks   TO tutor_app_role;
-- ladder_state is changed only through the procedures, so no direct INSERT/UPDATE grant.

-- Answer checking: the one role that can see answer keys.
GRANT SELECT ON problem_answers    TO leak_check_role;
GRANT SELECT ON problems           TO leak_check_role;

-- Teachers: aggregate views only. No raw turns, no learner rows.
GRANT SELECT ON v_cohort_topic_stalls  TO teacher_role;
GRANT SELECT ON v_ai_literacy_progress TO teacher_role;

-- Safeguarding lead: the only reader of Channel B.
GRANT SELECT, UPDATE (status) ON disclosures TO safeguard_role;

-- TODO: add a SQL Firewall allowlist for TUTOR_APP and the teacher login after
-- capturing their normal SQL. See the DBMS_SQL_FIREWALL docs:
-- https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlfw/oracle-sql-firewall.html
