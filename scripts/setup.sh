#!/usr/bin/env bash
#
# stumbleUpon setup script.
#
# Idempotent: safe to re-run. Skips steps that are already done.
#
# Usage:
#   scripts/setup.sh           # interactive (asks before opening $EDITOR)
#   scripts/setup.sh --yes     # non-interactive (no prompts, no editor)
#   scripts/setup.sh --help    # show this help
#
# What it does:
#   1. Checks Python 3.11+
#   2. Creates .venv/ and installs the package + dev extras
#   3. Installs Playwright's Chromium binary (skipped if already present)
#   4. Checks for ffmpeg (warns if missing — does NOT auto-install)
#   5. Copies .env.example to .env (skipped if .env already exists)
#   6. Runs `show-config` to validate the setup

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
PYPROJECT="$PROJECT_ROOT/pyproject.toml"
ENV_FILE="$PROJECT_ROOT/.env"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

YES=false
for arg in "$@"; do
    case "$arg" in
        -y|--yes) YES=true ;;
        -h|--help)
            sed -n '3,25p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Pretty output (no colors when piped)
# ---------------------------------------------------------------------------

if [[ -t 1 ]]; then
    BOLD=$'\033[1m'
    GREEN=$'\033[32m'
    YELLOW=$'\033[33m'
    RED=$'\033[31m'
    RESET=$'\033[0m'
else
    BOLD="" GREEN="" YELLOW="" RED="" RESET=""
fi

ok()    { printf "%s✓%s %s\n" "$GREEN" "$RESET" "$*"; }
warn()  { printf "%s!%s %s\n" "$YELLOW" "$RESET" "$*" >&2; }
fail()  { printf "%s✗%s %s\n" "$RED"   "$RESET" "$*" >&2; }
step()  { printf "\n%s→ %s%s\n" "$BOLD" "$*" "$RESET"; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

check_python_version() {
    local py="$1"
    "$py" -c "
import sys
v = sys.version_info
if v.major > $MIN_PYTHON_MAJOR or (v.major == $MIN_PYTHON_MAJOR and v.minor >= $MIN_PYTHON_MINOR):
    sys.exit(0)
sys.exit(1)
" 2>/dev/null
}

python_version_string() {
    local py="$1"
    "$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

cd "$PROJECT_ROOT"

printf "%sstumbleUpon setup%s\n" "$BOLD" "$RESET"
printf "Project root: %s\n" "$PROJECT_ROOT"

# 1. Python version -------------------------------------------------------

step "Checking Python version (need ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+)"

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if check_python_version "$candidate"; then
            PYTHON_BIN="$candidate"
            ok "Found $candidate ($(python_version_string "$candidate"))"
            break
        else
            warn "$candidate is $(python_version_string "$candidate") — too old"
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    fail "No suitable Python found. Install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ and retry."
    exit 1
fi

# 2. Virtualenv -----------------------------------------------------------

step "Setting up virtualenv at .venv/"

if [[ ! -d "$VENV" ]]; then
    "$PYTHON_BIN" -m venv "$VENV"
    ok "Created .venv/"
else
    ok ".venv/ already exists"
fi

PYTHON="$VENV/bin/python"
"$PYTHON" -m pip install --upgrade pip --quiet

# 3. Package + dev extras -------------------------------------------------

step "Installing Python dependencies"

# On Python 3.14, pip's bundled hatchling is too old to produce a working
# editable install. The README documents this; we replicate the fix here.
"$PYTHON" -m pip install --quiet "hatchling>=1.30" "build>=1.5"
"$PYTHON" -m pip install --quiet -e ".[dev]"
ok "Installed (editable) with dev extras"

# 4. Playwright Chromium --------------------------------------------------

step "Installing Playwright Chromium"
# `playwright install` is idempotent — it skips already-installed browsers.
"$PYTHON" -m playwright install chromium >/dev/null
ok "Chromium ready"

# 5. ffmpeg (system dep — check, don't install) ---------------------------

step "Checking for ffmpeg"

if command -v ffmpeg >/dev/null 2>&1; then
    ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}') found"
else
    warn "ffmpeg not found — required by the composer (ffmpeg) component."
    case "$(uname -s)" in
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                warn "Install with: brew install ffmpeg"
            else
                warn "Install Homebrew (https://brew.sh), then: brew install ffmpeg"
            fi
            ;;
        Linux)
            warn "Install with: sudo apt-get install ffmpeg   (Debian/Ubuntu)"
            warn "            sudo dnf install ffmpeg         (Fedora/RHEL)"
            ;;
        *)
            warn "Install ffmpeg via your platform's package manager."
            ;;
    esac
fi

# 6. .env -----------------------------------------------------------------

step "Setting up .env"

if [[ -f "$ENV_FILE" ]]; then
    ok ".env already exists (skipping)"
else
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    ok "Copied .env.example → .env"
    warn "Fill in the required values (ANTHROPIC_API_KEY, BUFFER_API_KEY, R2_*, STUMBLEUPON_API_KEY)."
    if [[ "$YES" == "false" ]] && [[ -t 0 ]]; then
        read -rp "Open .env in your editor now? [Y/n] " ans
        ans="${ans:-Y}"
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            editor_bin="${EDITOR:-${VISUAL:-nano}}"
            "$editor_bin" "$ENV_FILE" || warn "Could not launch editor; edit $ENV_FILE manually."
        fi
    fi
fi

# 7. Validate -------------------------------------------------------------

step "Validating configuration"
echo

if ! "$PYTHON" -m stumbleupon.main show-config; then
    fail "show-config failed — check your .env"
    exit 1
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

cat <<EOF

${BOLD}Setup complete.${RESET}

Next steps:
  1. Make sure all required values in .env are filled in
     (see .env.example comments + README §Setup).
  2. Smoke test:    $PYTHON -m stumbleupon.main run
  3. Review clips:  $PYTHON -m stumbleupon.main review
  4. Schedule on macOS:
                    $PYTHON -m stumbleupon.main install

Run tests anytime:
  $PYTHON -m pytest --cov=stumbleupon
EOF