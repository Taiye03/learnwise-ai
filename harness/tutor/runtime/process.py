"""
runtime/process.py — the service entrypoint. Runs as the unprivileged `tutor`
user under systemd (deploy/tutor-harness.service).

  python -m tutor.runtime.process                  # HTTP service on 127.0.0.1:8088
  python -m tutor.runtime.process --repl LEARNER   # terminal demo against real Claude + Oracle

Process rules:
  * Logs go to stdout -> journald. Log events and ids, NEVER child text.
  * Bind to loopback. TLS, rate limiting and client auth to the roster
    service live in a reverse proxy in front of this process.
  * SIGTERM is handled by uvicorn (graceful drain); we close the DB pool after.
"""
from __future__ import annotations

import argparse
import hmac
import logging
import sys
import uuid

from ..context.prompts import load_prompts
from .config import Settings, load_settings
from .db import OracleStore
from .llm import ClaudeClient
from .loop import TurnEngine, TurnRequest, UnknownLearner


def build_app(engine: TurnEngine, settings: Settings):
    """FastAPI app with one write endpoint and one health check."""
    from fastapi import FastAPI, Header, HTTPException
    from pydantic import BaseModel, Field

    app = FastAPI(title="tutor-harness", docs_url=None, redoc_url=None, openapi_url=None)  # no public schema

    class TurnIn(BaseModel):
        learner_id: str = Field(min_length=1, max_length=64)
        session_id: str = Field(min_length=1, max_length=64)
        message: str = Field(min_length=1, max_length=2000)   # caps prompt-stuffing and cost
        seconds_since_last_reply: float | None = Field(default=None, ge=0, le=86_400)

    @app.post("/v1/turn")
    def turn(body: TurnIn, authorization: str = Header(default="")):
        # Constant-time compare: the roster service is the only legitimate caller.
        if not hmac.compare_digest(authorization, f"Bearer {settings.api_bearer_token}"):
            raise HTTPException(status_code=401)
        try:
            result = engine.handle(TurnRequest(**body.model_dump()))
        except UnknownLearner:
            raise HTTPException(status_code=404, detail="learner not enrolled")
        return {"reply": result.reply, "meta": result.meta}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


def _repl(engine: TurnEngine, learner_id: str) -> None:
    """Minimal demo loop for judges and teammates. Type 'quit' to exit."""
    session = uuid.uuid4().hex[:16]
    print(f"session {session} — type 'quit' to exit")
    while (line := input("child> ").strip()) != "quit":
        if line:
            r = engine.handle(TurnRequest(learner_id, session, line))
            print(f"tutor> {r.reply}\n       [{r.meta.get('path')} | {r.meta.get('tier', '-')} | "
                  f"{r.meta.get('model_calls')} calls | {r.meta.get('route_reason', '')}]")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tutor-harness")
    parser.add_argument("--repl", metavar="LEARNER_ID", help="interactive demo instead of the HTTP service")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    settings = load_settings()
    store = OracleStore(settings.oracle_user, settings.oracle_password, settings.oracle_dsn, settings.embedding_model)
    engine = TurnEngine(settings, ClaudeClient(settings.anthropic_api_key), store, load_prompts())
    try:
        if args.repl:
            _repl(engine, args.repl)
        else:
            import uvicorn
            uvicorn.run(build_app(engine, settings), host=settings.listen_host, port=settings.listen_port,
                        access_log=False)   # access logs would record ids per request; audit_log already does
    finally:
        store.close()


if __name__ == "__main__":
    main()
