-- sql/00_users.sql — run ONCE as a DBA (e.g. SYSTEM) in the PDB (FREEPDB1 on Oracle 26ai Free).
--
-- Two schemas, two trust levels:
--   TUTOR_APP    what the harness connects as. Sees pseudonyms only.
--   TUTOR_VAULT  roster identity (names, guardian contact, consent). The harness
--                has NO grant on it. Only the roster/admin service connects here.
-- If the harness host is compromised, the attacker gets learning data keyed by
-- HMAC pseudonyms, not a list of children.

CREATE USER tutor_app IDENTIFIED BY "change-me-app" QUOTA 2G ON users;
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE PROCEDURE TO tutor_app;
GRANT CREATE MINING MODEL TO tutor_app;          -- needed to load the ONNX embedding model
GRANT EXECUTE ON DBMS_VECTOR TO tutor_app;

CREATE USER tutor_vault IDENTIFIED BY "change-me-vault" QUOTA 100M ON users;
GRANT CREATE SESSION, CREATE TABLE TO tutor_vault;

-- Directory holding the ONNX file for sql/02_load_embedding_model.sql.
-- Path is inside the DB server/container, not on the harness host.
CREATE OR REPLACE DIRECTORY onnx_dir AS '/opt/oracle/onnx_models';
GRANT READ ON DIRECTORY onnx_dir TO tutor_app;
