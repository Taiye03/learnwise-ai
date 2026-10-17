"""
Runtime layer: owns the process and everything with side effects.

  config.py    settings + secrets, loaded once
  llm.py       the only Claude API client
  db.py        the only Oracle 26ai client
  privacy.py   pseudonyms + PII scrubbing before persistence
  safety.py    input floor screen + post-draft review (fail closed)
  tools.py     two-tool allowlist + executor
  loop.py      the turn engine that sequences both layers
  process.py   the service entrypoint (HTTP + REPL), run under systemd
"""
