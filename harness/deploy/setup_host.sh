#!/usr/bin/env bash
# deploy/setup_host.sh — one-time host setup for the tutor harness. Run as root from the repo root.
#
# Creates a locked system user, installs the code under /opt, builds a venv,
# lays down config + credential files with tight permissions, installs the unit.
# Idempotent: safe to re-run after pulling new code.
set -euo pipefail

APP=/opt/tutor-harness
ETC=/etc/tutor-harness

# 1. Dedicated system user: no login shell, no home directory.
id tutor &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin tutor

# 2. Code: owned by root, readable by tutor, NOT writable by tutor.
#    A compromised process can't rewrite its own code or prompts.
install -d -o root -g tutor -m 0750 "$APP"
cp -r tutor requirements.txt "$APP"/
chown -R root:tutor "$APP"
chmod -R u=rwX,g=rX,o= "$APP"

# 3. Virtualenv (Python 3.11+).
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"

# 4. Non-secret config.
install -d -o root -g root -m 0755 "$ETC"
[ -f "$ETC/env" ] || install -o root -g tutor -m 0640 .env.example "$ETC/env"

# 5. Secrets: root-only files; systemd hands them to the service via LoadCredential.
install -d -o root -g root -m 0700 "$ETC/creds"
for name in anthropic_api_key oracle_password api_bearer_token; do
  [ -f "$ETC/creds/$name" ] || { echo "REPLACE_ME" > "$ETC/creds/$name"; chmod 0600 "$ETC/creds/$name"; }
done
# The pepper is generated, never typed. Losing it orphans all pseudonyms: back it up offline.
[ -f "$ETC/creds/pseudonym_pepper" ] || { head -c 32 /dev/urandom | base64 > "$ETC/creds/pseudonym_pepper"; chmod 0600 "$ETC/creds/pseudonym_pepper"; }

# 6. Unit.
install -o root -g root -m 0644 deploy/tutor-harness.service /etc/systemd/system/
systemctl daemon-reload

echo "Done. Fill in $ETC/creds/* and $ETC/env, then: systemctl enable --now tutor-harness"
