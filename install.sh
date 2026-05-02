#!/usr/bin/env bash
# FORGE installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<your-org>/forge/main/install.sh | bash
#
# Installs the `forge` CLI globally via pipx so it works from any directory.
# No API key required. Heuristic backend works out of the box; Ollama is optional.

set -euo pipefail

REPO_URL="${FORGE_REPO_URL:-git+https://github.com/Henildiyora/forge.git}"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11

bold() { printf "\033[1m%s\033[0m\n" "$1"; }
green() { printf "\033[32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }
red() { printf "\033[31m%s\033[0m\n" "$1" >&2; }

bold "FORGE installer"
echo

if ! command -v python3 >/dev/null 2>&1; then
  red "python3 is not installed."
  echo "Install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ from https://www.python.org/downloads/ and re-run."
  exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=${PY_VERSION%.*}
PY_MINOR=${PY_VERSION#*.}
if [ "$PY_MAJOR" -lt "$MIN_PYTHON_MAJOR" ] || { [ "$PY_MAJOR" -eq "$MIN_PYTHON_MAJOR" ] && [ "$PY_MINOR" -lt "$MIN_PYTHON_MINOR" ]; }; then
  red "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ required (found ${PY_VERSION})."
  exit 1
fi
green "✓ Python ${PY_VERSION}"

if ! command -v pipx >/dev/null 2>&1; then
  yellow "pipx not found — installing for the current user."
  python3 -m pip install --user --upgrade pipx >/dev/null
  python3 -m pipx ensurepath >/dev/null
  export PATH="$HOME/.local/bin:$PATH"
fi
green "✓ pipx ready"

bold "Installing forge from ${REPO_URL}"
pipx install --force "${REPO_URL}"
green "✓ forge installed"

if ! command -v forge >/dev/null 2>&1; then
  yellow "forge is installed but not on your PATH yet."
  echo "What this does: 'pipx ensurepath' prints shell snippets so ~/.local/bin is on PATH."
  if [ -t 0 ] && command -v pipx >/dev/null 2>&1; then
    read -r -p "Run 'pipx ensurepath' now (updates user PATH guidance)? [y/N] " consent
    if [[ "${consent}" =~ ^[Yy]$ ]]; then
      pipx ensurepath || true
      export PATH="$HOME/.local/bin:$PATH"
    fi
  else
    echo "Run this once to auto-configure PATH:"
    echo "  pipx ensurepath"
  fi
  echo
  echo "Then restart your shell."
  echo "If it still fails, add this to your shell rc manually:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo
  echo "Quick verify after restart:"
  echo "  which forge"
  echo "  forge doctor --post-install"
  exit 0
fi

echo
green "All set. Try it now:"
echo "  cd <any-project>"
echo "  forge index"
echo "  forge build"
echo
echo "Optional, for natural-language Q&A:"
echo "  brew install ollama && ollama pull qwen2.5-coder:1.5b"
echo "  forge setup"
