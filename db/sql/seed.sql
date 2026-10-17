-- =============================================================================
-- Synthetic demo data for the AI Tutor schema. Run as TUTOR_OWNER after schema.sql.
-- Everything here is invented. Lesson text is placeholder wording for the
-- prototype and must be reviewed by a human educator before it is trusted.
-- =============================================================================

-- ===== Age bands =============================================================

INSERT INTO age_bands (band_code, label, min_age, max_age, config) VALUES
 ('B8_10', 'Ages 8 to 10', 8, 10,
  '{"max_tutor_words":60,"voice_first":false,
    "safety_topics":["privacy","ask_a_grown_up","ai_can_be_wrong","check_answers","honest_credit"]}');

INSERT INTO age_bands (band_code, label, min_age, max_age, config) VALUES
 ('B6_7', 'Ages 6 to 7', 6, 7,
  '{"max_tutor_words":25,"voice_first":true,
    "safety_topics":["strangers_online","tell_a_grown_up"]}');

-- ===== Cohort and synthetic learners (6, so the cohort-of-5 rule is visible) ==

INSERT INTO cohorts (label, region_code) VALUES ('Demo class A', 'TN');

INSERT INTO learners (cohort_id, band_code)
SELECT c.cohort_id, 'B8_10'
  FROM cohorts c, (SELECT level FROM dual CONNECT BY level <= 6)
 WHERE c.label = 'Demo class A';

-- ===== Problems and answer keys ==============================================

INSERT INTO problems (band_code, topic_code, prompt_text, is_transfer) VALUES
 ('B8_10', 'FRACTIONS_ADD', 'What is 1/2 + 1/3?', 0);
INSERT INTO problems (band_code, topic_code, prompt_text, is_transfer) VALUES
 ('B8_10', 'FRACTIONS_ADD', 'What is 1/5 + 1/10?', 1);

INSERT INTO problem_answers (problem_id, answer_text, accepted_forms)
SELECT problem_id, '5/6', '["5/6"]' FROM problems WHERE prompt_text = 'What is 1/2 + 1/3?';
INSERT INTO problem_answers (problem_id, answer_text, accepted_forms)
SELECT problem_id, '3/10', '["3/10"]' FROM problems WHERE prompt_text = 'What is 1/5 + 1/10?';

-- ===== Lesson chunks: the full ladder for FRACTIONS_ADD ======================
-- Rung 4 is a worked example of a DIFFERENT problem (1/4 + 1/6), never the answer.

INSERT INTO lesson_chunks (band_code, topic_code, kind, ladder_rung, content, source_ref, reviewed_by, approved) VALUES
 ('B8_10','FRACTIONS_ADD','HINT',0,
  'Ask the learner to say the problem back in their own words before doing anything.',
  'SYNTHETIC-DEMO','demo-author',1);
INSERT INTO lesson_chunks (band_code, topic_code, kind, ladder_rung, content, source_ref, reviewed_by, approved) VALUES
 ('B8_10','FRACTIONS_ADD','HINT',1,
  'Ask: what have you tried so far, and what do the two bottom numbers tell you?',
  'SYNTHETIC-DEMO','demo-author',1);
INSERT INTO lesson_chunks (band_code, topic_code, kind, ladder_rung, content, source_ref, reviewed_by, approved) VALUES
 ('B8_10','FRACTIONS_ADD','CONCEPT',2,
  'You can only add fractions when the pieces are the same size, which means the bottom numbers (denominators) must match.',
  'SYNTHETIC-DEMO','demo-author',1);
INSERT INTO lesson_chunks (band_code, topic_code, kind, ladder_rung, content, source_ref, reviewed_by, approved) VALUES
 ('B8_10','FRACTIONS_ADD','HINT',3,
  'Point at the step: find a number that both bottom numbers divide into, then rewrite each fraction with that bottom number.',
  'SYNTHETIC-DEMO','demo-author',1);
INSERT INTO lesson_chunks (band_code, topic_code, kind, ladder_rung, content, source_ref, reviewed_by, approved) VALUES
 ('B8_10','FRACTIONS_ADD','WORKED_EXAMPLE',4,
  'Similar problem: 1/4 + 1/6. Both 4 and 6 divide into 12. 1/4 = 3/12 and 1/6 = 2/12. Add the tops: 3 + 2 = 5, so the answer is 5/12. Now try yours the same way.',
  'SYNTHETIC-DEMO','demo-author',1);

-- ===== Study habit, safety bites, and a planted error ========================

INSERT INTO lesson_chunks (band_code, topic_code, kind, content, source_ref, reviewed_by, approved) VALUES
 ('B8_10','STUDY_HABITS','HABIT',
  'Explain it back: after a hint, say the idea in your own words. If you cannot, ask for a different hint.',
  'SYNTHETIC-DEMO','demo-author',1);
INSERT INTO lesson_chunks (band_code, topic_code, kind, content, source_ref, reviewed_by, approved) VALUES
 ('B8_10','ONLINE_PRIVACY','SAFETY_BITE',
  'Never share your full name, school, address, or photos with an AI or a stranger online. If something feels weird or secret, tell a grown-up you trust. You will not be in trouble for telling.',
  'SYNTHETIC-DEMO','demo-author',1);
INSERT INTO lesson_chunks (band_code, topic_code, kind, content, source_ref, reviewed_by, approved) VALUES
 ('B8_10','AI_CAN_BE_WRONG','SAFETY_BITE',
  'An AI can sound sure and still be wrong. Check important answers in your textbook or with your teacher.',
  'SYNTHETIC-DEMO','demo-author',1);

INSERT INTO lesson_chunks (band_code, topic_code, kind, content, teaching_note,
                           contains_planted_error, source_ref, reviewed_by, approved) VALUES
 ('B8_10','FRACTIONS_ADD','PLANTED_ERROR',
  'Tutor says: 1/4 + 1/4 = 2/8, because 1 + 1 = 2 and 4 + 4 = 8.',
  'WRONG on purpose. When the pieces are the same size the bottom number stays the same: 1/4 + 1/4 = 2/4. Praise the child for catching it.',
  1,'SYNTHETIC-DEMO','demo-author',1);

-- ===== Synthetic ladder state: all 6 learners reach rung 3 on problem 1 ======

INSERT INTO ladder_state (learner_id, problem_id, rung_unlocked, attempts_since_advance, attempts_total)
SELECT l.learner_id, p.problem_id, 3, 1, 4
  FROM learners l
 CROSS JOIN problems p
 WHERE l.cohort_id = (SELECT cohort_id FROM cohorts WHERE label = 'Demo class A')
   AND p.prompt_text = 'What is 1/2 + 1/3?';

-- Six learners at rung 3+ means v_cohort_topic_stalls returns a row.
-- Change the learner count to 4 and the same view returns nothing: that is the
-- minimum-cohort rule working, and it makes a good demo moment.

COMMIT;

-- ===== Embeddings (run after loading an ONNX model) ==========================
-- Per the ORACLE-BASE walkthrough, load all_MiniLM_L12_v2 with DBMS_VECTOR, then:
--
--   UPDATE lesson_chunks
--      SET embedding = VECTOR_EMBEDDING(MINILM_L12 USING content AS data)
--    WHERE embedding IS NULL;
--   COMMIT;
--
-- MINILM_L12 is whatever name you gave the model when loading it.
--
-- Smoke test of the ladder, as TUTOR_APP, for a learner at rung 3 on problem 1:
-- the answer (5/6) and the rung 4 worked example must NOT come back.
--
--   DECLARE
--     v  VECTOR;
--     rc SYS_REFCURSOR;
--     id NUMBER; kind VARCHAR2(20); rung NUMBER; txt CLOB; src VARCHAR2(200);
--   BEGIN
--     SELECT VECTOR_EMBEDDING(MINILM_L12 USING 'how do I add these' AS data) INTO v FROM dual;
--     rc := tutor_owner.get_hint_chunks(<learner_id>, <problem_id>, v, 5);
--     LOOP
--       FETCH rc INTO id, kind, rung, txt, src;
--       EXIT WHEN rc%NOTFOUND;
--       DBMS_OUTPUT.PUT_LINE(rung || ' ' || kind);
--     END LOOP;
--   END;
--   /
-- Expected: rungs 0 to 3 only.
