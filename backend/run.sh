#!/usr/bin/env bash
# Start the Veritas API on 127.0.0.1:8000 (Nginx forwards your HTTPS domain to it).
# Run it inside tmux on the VM:   tmux new -s veritas   then   ./backend/run.sh
# The first run creates backend/.venv; every run installs any new requirements (fast when unchanged).
set -euo pipefail
cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

if [ ! -f .env ]; then
  echo "backend/.env is missing. Create it with:  cp .env.example .env  and set GEMINI_API_KEY and CORS_ORIGINS." >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 .venv
  else
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
      || { echo "Python 3.10+ is required (3.12 recommended). Install it or install uv: https://docs.astral.sh/uv/" >&2; exit 1; }
    python3 -m venv .venv
  fi
fi

echo "Installing / checking dependencies..."
if command -v uv >/dev/null 2>&1; then
  uv pip install --quiet --python .venv/bin/python -r requirements.txt
else
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

echo "Starting Veritas API on http://$HOST:$PORT (health: /api/health)"
# One worker on purpose: runs that wait for plan approval live in process memory.
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" --workers 1 \
  --proxy-headers --forwarded-allow-ips "127.0.0.1" --timeout-keep-alive 75
