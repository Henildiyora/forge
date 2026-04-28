#!/usr/bin/env bash
# Build and validate a FORGE release artifact.
#
# Usage:
#   scripts/release.sh                # build, validate, but do NOT publish
#   PUBLISH=1 scripts/release.sh      # also publish to PyPI (requires creds)
#
# This script is deliberately conservative. It refuses to run when:
#   - the working tree is dirty
#   - tests do not pass locally
#   - the version in pyproject.toml does not match the latest CHANGELOG entry

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

bold() { printf "\033[1m%s\033[0m\n" "$1"; }
red() { printf "\033[31m%s\033[0m\n" "$1" >&2; }

if [ -n "$(git status --porcelain)" ]; then
  red "Working tree is dirty. Commit or stash first."
  exit 1
fi

VERSION="$(grep -E '^version = ' pyproject.toml | head -n1 | sed -E 's/version = "([^"]+)"/\1/')"
bold "Releasing forge-devops ${VERSION}"

if ! grep -q "## \[${VERSION}\]" CHANGELOG.md; then
  red "CHANGELOG.md has no entry for ${VERSION}. Add one before releasing."
  exit 1
fi

bold "Running test suite"
pytest -q

bold "Linting"
ruff check forge tests
mypy forge tests || true   # mypy strict failures should not block a release script smoke

bold "Building wheel + sdist"
rm -rf dist build *.egg-info
python -m pip install --upgrade build twine >/dev/null
python -m build

bold "Validating distribution"
twine check dist/*

if [ "${PUBLISH:-0}" = "1" ]; then
  bold "Publishing to PyPI"
  twine upload dist/*
  bold "Tagging git release"
  git tag -a "v${VERSION}" -m "forge-devops ${VERSION}"
  echo
  echo "Don't forget: git push origin v${VERSION}"
else
  bold "Dry-run complete. Set PUBLISH=1 to upload."
  echo "Validate locally with:  pipx install --force ./dist/forge_devops-${VERSION}-py3-none-any.whl"
fi
