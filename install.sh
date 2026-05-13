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

_print_path_manual() {
  echo "Action required: add pipx apps to PATH (this shell cannot run forge until you do):"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
}

_detect_zshrc_syntax_error() {
  local zshrc="${HOME}/.zshrc"
  [ -f "${zshrc}" ] || return 1
  if ! zsh -n "${zshrc}" 2>/dev/null; then
    yellow "Refusing to auto-edit ~/.zshrc: syntax check failed (run: zsh -n ~/.zshrc)."
    return 0
  fi
  return 1
}

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
  echo "What this does: pipx installs apps under ~/.local/bin. Your shell must include that directory on PATH."
  _print_path_manual
  echo
  if [ -t 0 ] && command -v pipx >/dev/null 2>&1; then
    read -r -p "Run pipx ensurepath now (prints PATH guidance)? [y/N] " consent
    if [[ "${consent}" =~ ^[Yy]$ ]]; then
      pipx ensurepath || true
      export PATH="$HOME/.local/bin:$PATH"
    fi
  else
    echo "When you have a TTY, you can run: pipx ensurepath"
  fi

  if ! command -v forge >/dev/null 2>&1; then
    if [ -f "${HOME}/.zshrc" ]; then
      if rc_err="$(zsh -i -c "true" 2>&1)"; then
        :
      else
        if echo "${rc_err}" | grep -q "not valid in this context"; then
          yellow "Your ~/.zshrc failed to load in zsh (this often blocks pipx ensurepath):"
          echo "${rc_err}" | head -n 3
          echo "Fix that line in ~/.zshrc first, then re-run this installer or add PATH manually."
        fi
      fi
      _detect_zshrc_syntax_error || true
    fi

    if [ -t 0 ] && [ -f "${HOME}/.zshrc" ]; then
      read -r -p "Append 'export PATH=\"\$HOME/.local/bin:\$PATH\"' to ~/.zshrc? [y/N] " consent_rc
      if [[ "${consent_rc}" =~ ^[Yy]$ ]]; then
        if zsh -n "${HOME}/.zshrc" 2>/dev/null; then
          printf '\n# Added by FORGE installer\nexport PATH="$HOME/.local/bin:$PATH"\n' >>"${HOME}/.zshrc"
          green "Appended to ~/.zshrc — restart the terminal or: source ~/.zshrc"
        else
          yellow "Refusing to append: ~/.zshrc failed zsh -n (syntax error)."
        fi
      fi
    fi
  fi

  echo
  echo "Restart your shell (or run the export line above), then verify:"
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
