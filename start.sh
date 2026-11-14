#!/usr/bin/env bash
# ===========================================================================
#  College Assistant - one command start script (macOS / Linux)
#
#  Safe to run as many times as you like:
#    * creates .venv only if it is missing
#    * installs requirements only when requirements.txt has changed
#    * picks the next free port if the usual one is busy
#
#  Usage:  ./start.sh          (uses port 8501, or the next free one)
#          ./start.sh 8600     (start on a specific port)
# ===========================================================================
set -uo pipefail
cd "$(dirname "$0")" || exit 1

VENV_DIR=".venv"
PY="$VENV_DIR/bin/python"
STAMP="$VENV_DIR/requirements-installed.txt"
REQS="requirements.txt"
MIN_PORT=8501

say()  { printf '%s\n' "$*"; }
fail() { printf '  [X] %s\n' "$*"; printf '\n'; exit 1; }

printf '\n  ==============================================\n'
printf '    College Assistant\n'
printf '  ==============================================\n\n'

# ------------------------------------------------------------------ python ---
BASE_PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys' >/dev/null 2>&1; then
        BASE_PY="$candidate"
        break
    fi
done
[ -n "$BASE_PY" ] || fail "Python was not found. Install Python 3.10 or newer."

"$BASE_PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 \
    || fail "Python 3.10 or newer is required. You have: $($BASE_PY --version 2>&1)"
printf '  [1/5] Python %s found.\n' "$("$BASE_PY" -c 'import sys; print(sys.version.split()[0])')"

# --------------------------------------------------------------- app files ---
[ -f app.py ] || fail "app.py is missing. Keep start.sh in the same folder as app.py."
[ -f "$REQS" ] || fail "requirements.txt is missing. Keep start.sh in the same folder as app.py."

# ------------------------------------------------------------------- .venv ---
if [ -x "$PY" ] && ! "$PY" -c 'import sys' >/dev/null 2>&1; then
    say "      The existing .venv is broken, rebuilding it..."
    rm -rf "$VENV_DIR"
fi
if [ ! -x "$PY" ]; then
    printf '  [2/5] Creating virtual environment in %s (first run only)...\n' "$VENV_DIR"
    "$BASE_PY" -m venv "$VENV_DIR" || fail "Could not create the virtual environment."
else
    say "  [2/5] Reusing the existing .venv."
fi

# ------------------------------------------------------------- requirements ---
NEED_INSTALL=1
if [ -f "$STAMP" ] && cmp -s "$REQS" "$STAMP"; then
    NEED_INSTALL=0
fi
if [ "$NEED_INSTALL" = "1" ]; then
    say "  [3/5] Installing packages (only when requirements.txt changes)..."
    "$PY" -m pip install --disable-pip-version-check --quiet --upgrade pip \
        || say "      (could not upgrade pip, continuing)"
    "$PY" -m pip install --disable-pip-version-check --quiet -r "$REQS" \
        || fail "Package installation failed. Try running this again."
    cp "$REQS" "$STAMP"
    say "      Packages installed."
else
    say "  [3/5] All packages already installed - skipping."
fi

# --------------------------------------------------------------------- key ---
if [ ! -f .env ] && [ -f .env.example ]; then
    cp .env.example .env
    say "      Created .env from .env.example - you can paste your Groq key in it."
fi
HASKEY=0
if [ -n "${GROQ_API_KEY:-}" ]; then
    HASKEY=1
elif [ -f .env ] && grep -Eq '^[[:space:]]*GROQ_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]#]' .env; then
    HASKEY=1
fi

# -------------------------------------------------------------------- port ---
PORT="${1:-$MIN_PORT}"
[ "$PORT" -ge 1 ] 2>/dev/null || PORT="$MIN_PORT"
port_busy() {
    "$PY" -c "import socket,sys;s=socket.socket();r=s.connect_ex(('127.0.0.1',$1));s.close();sys.exit(0 if r==0 else 1)" >/dev/null 2>&1
}
tries=0
while port_busy "$PORT"; do
    tries=$((tries + 1))
    if [ "$tries" -ge 30 ]; then
        fail "No free port found."
    fi
    PORT=$((PORT + 1))
done

# ------------------------------------------------------------------- start ---
printf '  [4/5] Starting the server on port %s ...\n\n' "$PORT"
if [ "$HASKEY" = "1" ]; then
    say "      GROQ_API_KEY found - the AI agent is enabled."
else
    say "      No GROQ_API_KEY yet. The Add Data and View Data tabs still work."
    say "      To enable the AI agent, put your key in the .env file and start again."
fi
printf '\n  ------------------------------------------------------------\n'
printf '    Open this URL in your browser:\n\n'
printf '        http://localhost:%s\n\n' "$PORT"
printf '  ------------------------------------------------------------\n'
printf '    Press Ctrl+C in this terminal to stop the server.\n\n'
printf '  [5/5] Server running.\n\n'

"$PY" -m streamlit run app.py --server.port "$PORT" --browser.gatherUsageStats false
status=$?

printf '\n  The server has stopped (exit code %s).\n' "$status"
printf '  You can close this terminal, or run ./start.sh again whenever you like.\n\n'
exit $status
