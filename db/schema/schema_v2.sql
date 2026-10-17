-- =============================================================================
-- AI Tutor schema v2: teacher-as-admin, roster, join codes, syllabus upload.
-- Apply AFTER schema.sql and seed.sql. Status: DRAFT, untested on a live 26ai
-- instance; paste any ORA- errors back to get them fixed.
--
-- What this adds:
--   * Names live ONLY in roster_identity, reachable only by the teacher portal.
--     The child-facing tutor service and the model see pseudonymous IDs only.
--   * Children join with a short-lived join code, not an email account.
--   * A teacher uploads syllabus chunks; they stay PENDING until she approves
--     them, so the teacher is the human reviewer the schema already requires.
--   * Teacher views are filtered to her own class and fail closed if no
--     teacher identity has been set for the session.
--   * Teachers see progress (mastery, rung reached, attempts), never turns.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- PART 0: run as SYSTEM inside FREEPDB1
-- ---------------------------------------------------------------------------
-- CREATE ROLE teacher_portal_role;   -- the teacher-facing backend service account
-- CREATE CONTEXT teacher_ctx USING tutor_owner.teacher_ctx_pkg;
-- (Create one login for the portal service and grant it teacher_portal_role.)

-- ---------------------------------------------------------------------------
-- PART 1: run as TUTOR_OWNER
-- ---------------------------------------------------------------------------

CREATE TABLE teachers (
  teacher_id    RAW(16) DEFAULT SYS_GUID() PRIMARY KEY,
  display_name  VARCHAR2(80) NOT NULL,
  created_at    TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
);

-- A cohort is a class; it now has one teacher-admin.
ALTER TABLE cohorts ADD (teacher_id RAW(16) REFERENCES teachers(teacher_id));

-- The only table that links a real first name to a learner. Keep it minimal:
-- first name and last initial, no birthdate, no address, no email.
CREATE TABLE roster_identity (
  learner_id            RAW(16) PRIMARY KEY REFERENCES learners(learner_id),
  cohort_id             NUMBER NOT NULL REFERENCES cohorts(cohort_id),
  first_name            VARCHAR2(40) NOT NULL,
  last_initial          VARCHAR2(1),
  participation_status  VARCHAR2(14) DEFAULT 'ACTIVE' NOT NULL,
  added_at              TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT chk_participation CHECK
    (participation_status IN ('ACTIVE','OPTED_OUT','PENDING_NOTICE'))
);

-- Join codes map to a learner ID only, with no names, so the tutor service can
-- redeem one without ever touching roster_identity.
CREATE TABLE join_codes (
  join_code    VARCHAR2(12) PRIMARY KEY,
  learner_id   RAW(16) NOT NULL REFERENCES learners(learner_id),
  expires_at   TIMESTAMP NOT NULL,
  redeemed_at  TIMESTAMP
);

CREATE TABLE syllabus_uploads (
  upload_id    NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  cohort_id    NUMBER NOT NULL REFERENCES cohorts(cohort_id),
  filename     VARCHAR2(200) NOT NULL,
  uploaded_at  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
);

-- Teacher-uploaded content joins the same retrieval corpus, scoped to her class.
ALTER TABLE lesson_chunks ADD (
  cohort_id  NUMBER REFERENCES cohorts(cohort_id),
  upload_id  NUMBER REFERENCES syllabus_uploads(upload_id),
  origin     VARCHAR2(20) DEFAULT 'CURATED' NOT NULL
);
ALTER TABLE lesson_chunks ADD CONSTRAINT chk_origin
  CHECK (origin IN ('CURATED','TEACHER_UPLOAD'));
ALTER TABLE lesson_chunks ADD CONSTRAINT chk_upload_has_cohort
  CHECK (origin <> 'TEACHER_UPLOAD' OR cohort_id IS NOT NULL);
ALTER TABLE lesson_chunks DROP CONSTRAINT chk_chunk_kind;
ALTER TABLE lesson_chunks ADD CONSTRAINT chk_chunk_kind CHECK (kind IN
  ('CONCEPT','HINT','WORKED_EXAMPLE','SAFETY_BITE','HABIT','PLANTED_ERROR','SYLLABUS'));

-- ===== Session identity for teacher views ====================================
-- The portal backend authenticates the teacher (use real SSO and MFA in
-- production), then calls set_teacher so every query in that session is
-- filtered to her class. This is the simple version of row-level security; the
-- hardened options are Oracle's Virtual Private Database and Deep Data Security.

CREATE OR REPLACE PACKAGE teacher_ctx_pkg AS
  PROCEDURE set_teacher (p_teacher_id RAW);
END teacher_ctx_pkg;
/
CREATE OR REPLACE PACKAGE BODY teacher_ctx_pkg AS
  PROCEDURE set_teacher (p_teacher_id RAW) IS
  BEGIN
    DBMS_SESSION.SET_CONTEXT('TEACHER_CTX', 'TEACHER_ID', RAWTOHEX(p_teacher_id));
  END set_teacher;
END teacher_ctx_pkg;
/

-- If the context is unset, SYS_CONTEXT returns NULL and these views return no rows.
CREATE OR REPLACE VIEW v_my_roster AS
SELECT r.learner_id, r.first_name, r.last_initial, r.participation_status,
       c.cohort_id, c.label AS class_label
  FROM roster_identity r
  JOIN cohorts c ON c.cohort_id = r.cohort_id
 WHERE c.teacher_id = HEXTORAW(SYS_CONTEXT('TEACHER_CTX', 'TEACHER_ID'));

CREATE OR REPLACE VIEW v_my_student_progress AS
SELECT r.learner_id, r.first_name, r.last_initial, p.topic_code,
       COUNT(*)                    AS problems_started,
       MAX(ls.rung_unlocked)       AS highest_rung,
       SUM(ls.attempts_total)      AS attempts,
       MAX(ls.updated_at)          AS last_active
  FROM roster_identity r
  JOIN cohorts c       ON c.cohort_id = r.cohort_id
  JOIN ladder_state ls ON ls.learner_id = r.learner_id
  JOIN problems p      ON p.problem_id = ls.problem_id
 WHERE c.teacher_id = HEXTORAW(SYS_CONTEXT('TEACHER_CTX', 'TEACHER_ID'))
 GROUP BY r.learner_id, r.first_name, r.last_initial, p.topic_code;

-- ===== Teacher-portal procedures =============================================

-- Add a student: creates the pseudonymous learner, the roster row, and a join code.
CREATE OR REPLACE PROCEDURE enroll_student (
  p_teacher_id   RAW,
  p_cohort_id    NUMBER,
  p_band_code    VARCHAR2,
  p_first_name   VARCHAR2,
  p_last_initial VARCHAR2,
  p_join_code    OUT VARCHAR2
) AS
  v_ok      NUMBER;
  v_learner RAW(16);
BEGIN
  SELECT COUNT(*) INTO v_ok FROM cohorts
   WHERE cohort_id = p_cohort_id AND teacher_id = p_teacher_id;
  IF v_ok = 0 THEN
    RAISE_APPLICATION_ERROR(-20001, 'Not your class');
  END IF;

  INSERT INTO learners (cohort_id, band_code)
  VALUES (p_cohort_id, p_band_code)
  RETURNING learner_id INTO v_learner;

  INSERT INTO roster_identity (learner_id, cohort_id, first_name, last_initial)
  VALUES (v_learner, p_cohort_id, p_first_name, SUBSTR(p_last_initial, 1, 1));

  -- DBMS_RANDOM is fine for a demo; use DBMS_CRYPTO.RANDOMBYTES for real codes.
  p_join_code := UPPER(DBMS_RANDOM.STRING('X', 8));
  INSERT INTO join_codes (join_code, learner_id, expires_at)
  VALUES (p_join_code, v_learner, SYSTIMESTAMP + INTERVAL '7' DAY);
END enroll_student;
/

CREATE OR REPLACE PROCEDURE create_upload (
  p_teacher_id RAW,
  p_cohort_id  NUMBER,
  p_filename   VARCHAR2,
  p_upload_id  OUT NUMBER
) AS
  v_ok NUMBER;
BEGIN
  SELECT COUNT(*) INTO v_ok FROM cohorts
   WHERE cohort_id = p_cohort_id AND teacher_id = p_teacher_id;
  IF v_ok = 0 THEN
    RAISE_APPLICATION_ERROR(-20001, 'Not your class');
  END IF;
  INSERT INTO syllabus_uploads (cohort_id, filename)
  VALUES (p_cohort_id, p_filename)
  RETURNING upload_id INTO p_upload_id;
END create_upload;
/

-- New syllabus chunks always start unapproved. Embed them afterwards with the
-- UPDATE ... VECTOR_EMBEDDING statement from seed.sql.
CREATE OR REPLACE PROCEDURE add_syllabus_chunk (
  p_teacher_id RAW,
  p_upload_id  NUMBER,
  p_band_code  VARCHAR2,
  p_topic_code VARCHAR2,
  p_content    CLOB,
  p_chunk_id   OUT NUMBER
) AS
  v_cohort NUMBER;
BEGIN
  SELECT u.cohort_id INTO v_cohort
    FROM syllabus_uploads u
    JOIN cohorts c ON c.cohort_id = u.cohort_id
   WHERE u.upload_id = p_upload_id AND c.teacher_id = p_teacher_id;

  INSERT INTO lesson_chunks
    (band_code, topic_code, kind, content, source_ref, approved,
     cohort_id, upload_id, origin)
  VALUES
    (p_band_code, p_topic_code, 'SYLLABUS', p_content, 'TEACHER-UPLOAD', 0,
     v_cohort, p_upload_id, 'TEACHER_UPLOAD')
  RETURNING chunk_id INTO p_chunk_id;
EXCEPTION WHEN NO_DATA_FOUND THEN
  RAISE_APPLICATION_ERROR(-20001, 'Upload not found or not your class');
END add_syllabus_chunk;
/

-- The teacher approves a chunk; she becomes its recorded reviewer.
CREATE OR REPLACE PROCEDURE approve_chunk (
  p_teacher_id RAW,
  p_chunk_id   NUMBER
) AS
BEGIN
  UPDATE lesson_chunks c
     SET c.approved    = 1,
         c.reviewed_by = (SELECT display_name FROM teachers WHERE teacher_id = p_teacher_id)
   WHERE c.chunk_id = p_chunk_id
     AND c.origin   = 'TEACHER_UPLOAD'
     AND c.cohort_id IN (SELECT cohort_id FROM cohorts WHERE teacher_id = p_teacher_id);
  IF SQL%ROWCOUNT = 0 THEN
    RAISE_APPLICATION_ERROR(-20002, 'Chunk not found or not your class');
  END IF;
END approve_chunk;
/

-- ===== Tutor-service procedures (no names involved) ==========================

-- The tutor service redeems a join code and gets a learner ID back. Single use,
-- expiring, and refused for children whose participation is not ACTIVE.
CREATE OR REPLACE PROCEDURE redeem_join_code (
  p_code       VARCHAR2,
  p_learner_id OUT RAW
) AS
BEGIN
  SELECT j.learner_id INTO p_learner_id
    FROM join_codes j
    JOIN roster_identity r ON r.learner_id = j.learner_id
   WHERE j.join_code   = UPPER(p_code)
     AND j.expires_at > SYSTIMESTAMP
     AND j.redeemed_at IS NULL
     AND r.participation_status = 'ACTIVE';

  UPDATE join_codes SET redeemed_at = SYSTIMESTAMP WHERE join_code = UPPER(p_code);
EXCEPTION WHEN NO_DATA_FOUND THEN
  p_learner_id := NULL;
END redeem_join_code;
/

-- Replaces the v1 function: same ladder rules, now also scoped to the learner's
-- own class, so one teacher's uploads never appear for another teacher's students.
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
      JOIN learners lr ON lr.learner_id = p_learner_id
      LEFT JOIN ladder_state l
        ON l.learner_id = p_learner_id AND l.problem_id = p_problem_id
     WHERE c.approved = 1
       AND c.contains_planted_error = 0
       AND c.kind IN ('CONCEPT','HINT','WORKED_EXAMPLE')
       AND (c.cohort_id IS NULL OR c.cohort_id = lr.cohort_id)
       AND c.ladder_rung <= NVL(l.rung_unlocked, 0)
     ORDER BY VECTOR_DISTANCE(c.embedding, p_query_vec, COSINE)
     FETCH FIRST p_k ROWS ONLY;
  RETURN rc;
END get_hint_chunks;
/

-- "What is my class doing this week?" Approved syllabus chunks for the
-- learner's own class only.
CREATE OR REPLACE FUNCTION get_class_context (
  p_learner_id RAW,
  p_query_vec  VECTOR,
  p_k          PLS_INTEGER DEFAULT 2
) RETURN SYS_REFCURSOR AS
  rc SYS_REFCURSOR;
BEGIN
  OPEN rc FOR
    SELECT c.chunk_id, c.topic_code, c.content
      FROM lesson_chunks c
      JOIN learners lr ON lr.learner_id = p_learner_id
     WHERE c.kind = 'SYLLABUS'
       AND c.approved = 1
       AND c.cohort_id = lr.cohort_id
     ORDER BY VECTOR_DISTANCE(c.embedding, p_query_vec, COSINE)
     FETCH FIRST p_k ROWS ONLY;
  RETURN rc;
END get_class_context;
/

-- ===== Grants ================================================================

-- Teacher portal: her roster and progress, plus the admin procedures. It cannot
-- read turns, disclosures, or answer keys.
GRANT SELECT  ON v_my_roster           TO teacher_portal_role;
GRANT SELECT  ON v_my_student_progress TO teacher_portal_role;
GRANT EXECUTE ON teacher_ctx_pkg       TO teacher_portal_role;
GRANT EXECUTE ON enroll_student        TO teacher_portal_role;
GRANT EXECUTE ON create_upload         TO teacher_portal_role;
GRANT EXECUTE ON add_syllabus_chunk    TO teacher_portal_role;
GRANT EXECUTE ON approve_chunk         TO teacher_portal_role;

-- Tutor service: redeem codes and read class context, still without any names.
GRANT EXECUTE ON redeem_join_code TO tutor_app_role;
GRANT EXECUTE ON get_class_context TO tutor_app_role;

-- ===== Demo data =============================================================

INSERT INTO teachers (display_name) VALUES ('Ms. Demo');

UPDATE cohorts
   SET teacher_id = (SELECT teacher_id FROM teachers WHERE display_name = 'Ms. Demo')
 WHERE label = 'Demo class A';

-- Invented first names for the six synthetic learners from seed.sql.
INSERT INTO roster_identity (learner_id, cohort_id, first_name, last_initial)
SELECT learner_id, cohort_id,
       CASE rn WHEN 1 THEN 'Ada' WHEN 2 THEN 'Ben' WHEN 3 THEN 'Cleo'
               WHEN 4 THEN 'Dev' WHEN 5 THEN 'Eli' ELSE 'Faye' END,
       CHR(64 + rn)
  FROM (SELECT learner_id, cohort_id,
               ROW_NUMBER() OVER (ORDER BY learner_id) AS rn
          FROM learners
         WHERE cohort_id = (SELECT cohort_id FROM cohorts WHERE label = 'Demo class A'));

INSERT INTO join_codes (join_code, learner_id, expires_at)
SELECT UPPER(DBMS_RANDOM.STRING('X', 8)), learner_id, SYSTIMESTAMP + INTERVAL '7' DAY
  FROM roster_identity;

-- One synthetic syllabus upload: one chunk the teacher approved, one still pending.
INSERT INTO syllabus_uploads (cohort_id, filename)
SELECT cohort_id, 'demo_syllabus_fall.txt' FROM cohorts WHERE label = 'Demo class A';

INSERT INTO lesson_chunks
  (band_code, topic_code, kind, content, source_ref, reviewed_by, approved,
   cohort_id, upload_id, origin)
SELECT 'B8_10', 'FRACTIONS_ADD', 'SYLLABUS',
       'Week of Oct 20: Unit 3, adding fractions with different bottom numbers. Quiz on Friday.',
       'TEACHER-UPLOAD', 'Ms. Demo', 1, u.cohort_id, u.upload_id, 'TEACHER_UPLOAD'
  FROM syllabus_uploads u WHERE u.filename = 'demo_syllabus_fall.txt';

INSERT INTO lesson_chunks
  (band_code, topic_code, kind, content, source_ref, approved,
   cohort_id, upload_id, origin)
SELECT 'B8_10', 'DECIMALS', 'SYLLABUS',
       'Week of Oct 27: Unit 4, decimals and place value. Homework due Wednesday.',
       'TEACHER-UPLOAD', 0, u.cohort_id, u.upload_id, 'TEACHER_UPLOAD'
  FROM syllabus_uploads u WHERE u.filename = 'demo_syllabus_fall.txt';

COMMIT;

-- ===== Checks to run =========================================================
-- 1) With no teacher set, this returns zero rows (fails closed):
--      SELECT * FROM v_my_roster;
-- 2) After setting her identity, it returns six rows:
--      EXEC teacher_ctx_pkg.set_teacher((SELECT teacher_id FROM teachers WHERE display_name='Ms. Demo'));
--    (If EXEC rejects a subquery, select the ID into a bind variable first.)
--      SELECT first_name, topic_code, highest_rung, attempts FROM v_my_student_progress;
-- 3) The pending DECIMALS chunk must not come back from get_class_context until
--    approve_chunk is called for it.
-- 4) TODO next: forget_learner(p_learner_id), a procedure that deletes a child's
--    roster row, join codes, ladder state, sessions and turns on request.
