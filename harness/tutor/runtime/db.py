"""
runtime/db.py — the only module that talks to Oracle Database 26ai.

`Store` is the interface; `OracleStore` is the real implementation; tests use
an in-memory fake with the same methods. Every method is one short
transaction, so a crash mid-turn never leaves half-written state.

Embeddings are computed INSIDE the database with VECTOR_EMBEDDING() and an
ONNX model loaded by sql/02_load_embedding_model.sql. Children's text never
leaves Oracle to be embedded by a third-party API.

Refs:
  python-oracledb   https://python-oracledb.readthedocs.io/
  AI Vector Search  https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/
"""
from __future__ import annotations

import json
import re
from typing import Protocol

# Identifier we interpolate into SQL (model names can't be bind variables).
# Validated once at construction — never built from user input.
_IDENT = re.compile(r"[A-Z][A-Z0-9_]{0,127}")


class Store(Protocol):
    """Everything the harness persists or retrieves. Pseudonyms only — never roster ids or names."""
    def load_state(self, pseudonym: str) -> dict | None: ...
    def save_state(self, pseudonym: str, state: dict) -> None: ...
    def recent_turns(self, session_id: str, limit: int) -> list[tuple[str, str]]: ...
    def append_turn(self, session_id: str, pseudonym: str, role: str, text: str,
                    model: str | None, tokens: int) -> None: ...
    def search_memory(self, pseudonym: str, query: str, k: int) -> list[str]: ...
    def add_memory(self, pseudonym: str, kind: str, note: str) -> None: ...
    def search_curriculum(self, class_id: str, query: str, k: int) -> list[str]: ...
    def tokens_today(self, pseudonym: str) -> int: ...
    def add_usage(self, pseudonym: str, tokens: int) -> None: ...
    def flag_for_adult(self, pseudonym: str, session_id: str, severity: str, reason: str) -> None: ...
    def audit(self, event: str, pseudonym: str | None, detail: dict) -> None: ...


class OracleStore:
    """python-oracledb in thin mode: pure Python, no Instant Client install needed."""

    def __init__(self, user: str, password: str, dsn: str, embedding_model: str):
        import oracledb
        if not _IDENT.fullmatch(embedding_model):
            raise ValueError("embedding model name must be an uppercase SQL identifier")
        self._emb = embedding_model
        oracledb.defaults.fetch_lobs = False   # return CLOBs as str; our texts are small
        # Small pool: one process, a handful of concurrent turns. Grow with load tests, not guesses.
        self._pool = oracledb.create_pool(user=user, password=password, dsn=dsn, min=1, max=4, increment=1)

    def close(self) -> None:
        self._pool.close()

    # -- learner state (JSON column) ----------------------------------------
    def load_state(self, pseudonym):
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("SELECT class_id, age_band, state FROM learner_state WHERE pseudonym = :p", p=pseudonym)
            row = cur.fetchone()
        if row is None:
            return None   # closed roster: unknown learners are rejected upstream
        state = row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")
        state.update(class_id=row[0], age_band=row[1])
        return state

    def save_state(self, pseudonym, state):
        # class_id / age_band are roster-owned columns; the harness never rewrites them.
        body = {k: v for k, v in state.items() if k not in ("class_id", "age_band")}
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("UPDATE learner_state SET state = :s, updated_at = SYSTIMESTAMP WHERE pseudonym = :p",
                        s=json.dumps(body), p=pseudonym)
            conn.commit()

    # -- conversation turns ---------------------------------------------------
    def recent_turns(self, session_id, limit):
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("""SELECT role, text_scrubbed FROM turns WHERE session_id = :s
                           ORDER BY id DESC FETCH FIRST :n ROWS ONLY""", s=session_id, n=limit)
            rows = cur.fetchall()
        return [(r[0], r[1]) for r in reversed(rows)]   # oldest first

    def append_turn(self, session_id, pseudonym, role, text, model, tokens):
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO turns (session_id, pseudonym, role, text_scrubbed, model, tokens)
                           VALUES (:s, :p, :r, :t, :m, :k)""",
                        s=session_id, p=pseudonym, r=role, t=text, m=model, k=tokens)
            conn.commit()

    # -- vector memory: embed in-database, rank by cosine distance ------------
    def search_memory(self, pseudonym, query, k):
        sql = f"""SELECT note FROM learner_memory WHERE pseudonym = :p
                  ORDER BY VECTOR_DISTANCE(embedding, VECTOR_EMBEDDING({self._emb} USING :q AS data), COSINE)
                  FETCH FIRST :k ROWS ONLY"""
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute(sql, p=pseudonym, q=query, k=k)
            return [r[0] for r in cur.fetchall()]

    def add_memory(self, pseudonym, kind, note):
        sql = f"""INSERT INTO learner_memory (pseudonym, kind, note, embedding)
                  VALUES (:p, :kind, :note, VECTOR_EMBEDDING({self._emb} USING :note AS data))"""
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute(sql, p=pseudonym, kind=kind, note=note)
            conn.commit()

    def search_curriculum(self, class_id, query, k):
        sql = f"""SELECT title || ': ' || chunk FROM curriculum_chunks WHERE class_id = :c
                  ORDER BY VECTOR_DISTANCE(embedding, VECTOR_EMBEDDING({self._emb} USING :q AS data), COSINE)
                  FETCH FIRST :k ROWS ONLY"""
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute(sql, c=class_id, q=query, k=k)
            return [r[0] for r in cur.fetchall()]

    # -- budget ---------------------------------------------------------------
    def tokens_today(self, pseudonym):
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("SELECT tokens FROM usage_daily WHERE pseudonym = :p AND day = TRUNC(SYSDATE)", p=pseudonym)
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def add_usage(self, pseudonym, tokens):
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("""MERGE INTO usage_daily u
                           USING (SELECT :p AS pseudonym, TRUNC(SYSDATE) AS day) s
                           ON (u.pseudonym = s.pseudonym AND u.day = s.day)
                           WHEN MATCHED THEN UPDATE SET u.tokens = u.tokens + :t
                           WHEN NOT MATCHED THEN INSERT (pseudonym, day, tokens) VALUES (s.pseudonym, s.day, :t)""",
                        p=pseudonym, t=tokens)
            conn.commit()

    # -- human escalation & audit --------------------------------------------
    def flag_for_adult(self, pseudonym, session_id, severity, reason):
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO adult_flags (pseudonym, session_id, severity, reason)
                           VALUES (:p, :s, :sev, :r)""", p=pseudonym, s=session_id, sev=severity, r=reason[:500])
            conn.commit()

    def audit(self, event, pseudonym, detail):
        with self._pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO audit_log (event, pseudonym, detail) VALUES (:e, :p, :d)",
                        e=event, p=pseudonym, d=json.dumps(detail))
            conn.commit()
