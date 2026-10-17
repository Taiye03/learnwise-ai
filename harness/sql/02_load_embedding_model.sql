-- sql/02_load_embedding_model.sql — run as TUTOR_APP after placing the ONNX file.
--
-- 1. Download Oracle's pre-built, "augmented" all-MiniLM-L12-v2 ONNX model
--    (tokenizer + pooling baked in, so the DB can embed raw text):
--    https://blogs.oracle.com/machinelearning/use-our-prebuilt-onnx-model-now-available-for-embedding-generation-in-oracle-database-23ai
-- 2. Unzip all_MiniLM_L12_v2.onnx into the directory behind ONNX_DIR (sql/00_users.sql).
-- 3. Run this file.
--
-- Why in-database embeddings: children's text never leaves Oracle to be embedded
-- by a third-party API, and inserts can embed in the same SQL statement.

BEGIN
  DBMS_VECTOR.LOAD_ONNX_MODEL(
    directory  => 'ONNX_DIR',
    file_name  => 'all_MiniLM_L12_v2.onnx',
    model_name => 'ALL_MINILM_L12_V2');
END;
/

-- Verify: should list ALL_MINILM_L12_V2 / ONNX / EMBEDDING
SELECT model_name, algorithm, mining_function FROM user_mining_models;

-- Smoke test: returns a 384-dimension vector
SELECT VECTOR_DIMENSION_COUNT(VECTOR_EMBEDDING(ALL_MINILM_L12_V2 USING 'adding fractions' AS data)) AS dims;

-- Example: load one syllabus chunk (the teacher upload path does this in bulk)
-- INSERT INTO curriculum_chunks (class_id, title, chunk, embedding)
--   VALUES ('class-demo', 'Week 3: Fractions', 'Students add fractions with like denominators...',
--           VECTOR_EMBEDDING(ALL_MINILM_L12_V2 USING 'Students add fractions with like denominators...' AS data));
