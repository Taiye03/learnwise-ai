-- sql/01_schema.sql — run as TUTOR_APP. Oracle Database 26ai (native JSON + VECTOR types).
-- Every table is keyed by `pseudonym` = HMAC(pepper, roster_id). No names anywhere.

-- Learner state: one row per enrolled learner, created by the roster service at
-- enrollment (closed roster). The harness only UPDATEs `state`.
CREATE TABLE learner_state (
  pseudonym   VARCHAR2(64)  PRIMARY KEY,
  class_id    VARCHAR2(64)  NOT NULL,
  age_band    VARCHAR2(8)   NOT NULL CHECK (age_band IN ('6-7', '8-10', '11-13')),
  state       JSON          NOT NULL,          -- mastery, affect EMAs, router state, current topic
  updated_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP
);

-- Conversation turns, already scrubbed by runtime/privacy.py before insert.
CREATE TABLE turns (
  id             NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id     VARCHAR2(64)  NOT NULL,
  pseudonym      VARCHAR2(64)  NOT NULL,
  role           VARCHAR2(8)   NOT NULL CHECK (role IN ('child', 'tutor')),
  text_scrubbed  CLOB,                          -- NULLed at de-identification (see below)
  model          VARCHAR2(64),
  tokens         NUMBER DEFAULT 0,
  created_at     TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP
);
CREATE INDEX turns_session_ix ON turns (session_id, id);
CREATE INDEX turns_pseudonym_ix ON turns (pseudonym);

-- Long-term learner memory. 384 dims = all-MiniLM-L12-v2 output size.
CREATE TABLE learner_memory (
  id          NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pseudonym   VARCHAR2(64)   NOT NULL,
  kind        VARCHAR2(16)   NOT NULL,
  note        VARCHAR2(1000) NOT NULL,
  embedding   VECTOR(384, FLOAT32),
  created_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP
);
CREATE INDEX learner_memory_p_ix ON learner_memory (pseudonym);
-- Per-learner exact search is fast at hackathon scale (we filter by pseudonym first).
-- Past ~100k rows per tenant, add an HNSW index:
--   CREATE VECTOR INDEX learner_memory_vix ON learner_memory (embedding)
--     ORGANIZATION INMEMORY NEIGHBOR GRAPH DISTANCE COSINE WITH TARGET ACCURACY 95;

-- Teacher-uploaded syllabus, chunked and embedded at upload time.
CREATE TABLE curriculum_chunks (
  id         NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  class_id   VARCHAR2(64)   NOT NULL,
  title      VARCHAR2(200)  NOT NULL,
  chunk      VARCHAR2(4000) NOT NULL,
  embedding  VECTOR(384, FLOAT32)
);
CREATE INDEX curriculum_class_ix ON curriculum_chunks (class_id);

CREATE TABLE usage_daily (
  pseudonym  VARCHAR2(64) NOT NULL,
  day        DATE         NOT NULL,
  tokens     NUMBER       DEFAULT 0 NOT NULL,
  PRIMARY KEY (pseudonym, day)
);

-- Human escalation queue. Teachers review; parents see pattern notes only
-- after teacher review unless escalated (product rule).
CREATE TABLE adult_flags (
  id          NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pseudonym   VARCHAR2(64)  NOT NULL,
  session_id  VARCHAR2(64),
  severity    VARCHAR2(8)   NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
  reason      VARCHAR2(500),
  status      VARCHAR2(12)  DEFAULT 'open' NOT NULL CHECK (status IN ('open', 'reviewed', 'escalated', 'closed')),
  created_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP
);

-- Append-only audit trail. Events and metadata, never child text.
CREATE TABLE audit_log (
  id          NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event       VARCHAR2(40)  NOT NULL,
  pseudonym   VARCHAR2(64),
  detail      JSON,
  created_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP
);
-- For a tamper-evident log, recreate audit_log as a blockchain table:
--   CREATE BLOCKCHAIN TABLE audit_log (...) NO DROP UNTIL 365 DAYS IDLE
--     NO DELETE UNTIL 365 DAYS AFTER INSERT HASHING USING "SHA2_512" VERSION "v1";


-- ---------------------------------------------------------------------------
-- De-identification: the step that makes "keep the data" defensible.
--
-- Called by a scheduled job when a learner's retention window ends, or when a
-- parent asks for deletion. After it runs:
--   * raw text is gone (scrubbing is not trusted to have caught everything),
--   * memory notes are gone (they describe one child),
--   * the pseudonym is replaced by a random token with no stored mapping, so
--     even the pepper can no longer link the rows back to the roster id,
--   * what remains is derived: mastery/affect trajectories, timings, moves,
--     models, token counts — the research-useful part.
-- The roster service deletes the matching TUTOR_VAULT row in the same run.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE deidentify_learner (p_pseudonym IN VARCHAR2) AS
  v_token VARCHAR2(64) := 'anon-' || LOWER(RAWTOHEX(SYS_GUID()));
BEGIN
  UPDATE turns          SET text_scrubbed = NULL, pseudonym = v_token, session_id = 'anon'
   WHERE pseudonym = p_pseudonym;
  DELETE FROM learner_memory WHERE pseudonym = p_pseudonym;
  UPDATE adult_flags    SET reason = NULL, session_id = NULL, pseudonym = v_token
   WHERE pseudonym = p_pseudonym;
  UPDATE usage_daily    SET pseudonym = v_token WHERE pseudonym = p_pseudonym;
  UPDATE audit_log      SET pseudonym = v_token WHERE pseudonym = p_pseudonym;
  -- learner_state.class_id + age_band stay as coarse cohort attributes.
  UPDATE learner_state  SET pseudonym = v_token WHERE pseudonym = p_pseudonym;
  INSERT INTO audit_log (event, pseudonym, detail)
    VALUES ('deidentified', v_token, JSON('{"by":"deidentify_learner"}'));
  COMMIT;
END;
/

-- Example enrollment row (the roster service does this for real):
-- INSERT INTO learner_state (pseudonym, class_id, age_band, state)
--   VALUES ('<hmac from runtime/privacy.py>', 'class-demo', '8-10', JSON('{}'));
