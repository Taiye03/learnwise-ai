# tutor-harness

Harness for a Claude-powered children's tutor, persisting to Oracle Database 26ai.
Full design rationale and references: the "Tutor Agent Harness — Build Workflow" doc.

## Layer map

```
tutor/
├── runtime/                 owns the process and every side effect
│   ├── config.py            settings + systemd-credential secrets, loaded once
│   ├── llm.py               the only Claude API client; structured_call() = JSON via forced tool
│   ├── db.py                the only Oracle client; in-DB embeddings via VECTOR_EMBEDDING
│   ├── privacy.py           HMAC pseudonyms, PII scrub before persistence
│   ├── safety.py            gate 1: deterministic crisis screen; gate 2: review (fails closed)
│   ├── tools.py             2-tool allowlist, constant defs (cache-stable), executor
│   ├── loop.py              TurnEngine: sequences both layers, enforces every call bound
│   └── process.py           entrypoint: HTTP service or --repl demo
└── context/                 decides what the model sees and which model sees it
    ├── cues.py              child's signals from text + latency, EMA-smoothed
    ├── planner.py           one cheap call plans move/retrieval/tools/risk (fast path: zero)
    ├── router.py            safety > budget > need > cache-stickiness tier choice
    ├── assembler.py         cache-ordered, token-budgeted prompt
    ├── memory.py            recall before, keep after, mastery estimate
    └── prompts/             tutor_system.md, planner.md, reviewer.md (comments stripped at load)
```

Model calls per turn: crisis 0 · acknowledgement 2 · normal 3 · +1 tool round max · +2 on review rejection, then fallback.

## Setup (short form)

1. **Oracle 26ai.** As DBA: `sql/00_users.sql`. As `TUTOR_APP`: `sql/01_schema.sql`, then place
   `all_MiniLM_L12_v2.onnx` in the DB's `ONNX_DIR` and run `sql/02_load_embedding_model.sql`.
2. **Host.** `sudo ./deploy/setup_host.sh` (creates the `tutor` user, venv, config, unit).
3. **Secrets.** Put real values in `/etc/tutor-harness/creds/{anthropic_api_key,oracle_password,api_bearer_token}`.
   The pepper is generated for you; back it up offline.
4. **Enroll a learner** (the roster service's job; by hand for a demo):
   ```bash
   python3 -c "from tutor.runtime.privacy import pseudonymize; print(pseudonymize('demo-kid-1', open('/etc/tutor-harness/creds/pseudonym_pepper','rb').read().strip()))"
   ```
   then `INSERT INTO learner_state (pseudonym, class_id, age_band, state) VALUES ('<that>', 'class-demo', '8-10', JSON('{}'));`
5. **Run.** `systemctl enable --now tutor-harness`, or for a demo:
   `sudo -u tutor env CREDENTIALS_DIRECTORY=... .venv/bin/python -m tutor.runtime.process --repl demo-kid-1`
6. **Call it.**
   ```bash
   curl -s localhost:8088/v1/turn -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"learner_id":"demo-kid-1","session_id":"s1","message":"is 1/2 + 1/3 = 2/5?","seconds_since_last_reply":40}'
   ```

## Tests

```bash
python3 -m unittest -v      # stdlib only: fakes stand in for Claude and Oracle
```
