#!/usr/bin/env bash
# Record a 30-second asciinema cast that demonstrates the FORGE happy path.
#
# Usage:
#   scripts/record-demo.sh [output-cast-path]
#
# Prereqs:
#   - asciinema (brew install asciinema  /  apt install asciinema)
#   - forge installed and on PATH (run install.sh first)
#
# Output: docs/demo.cast (or the path you pass as $1).

set -euo pipefail

OUT="${1:-docs/demo.cast}"
DEMO_DIR="$(mktemp -d -t forge-demo-XXXXXX)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v asciinema >/dev/null 2>&1; then
  echo "asciinema is not installed. brew install asciinema" >&2
  exit 1
fi
if ! command -v forge >/dev/null 2>&1; then
  echo "forge is not on your PATH. Run ./install.sh first." >&2
  exit 1
fi

cp -R "${REPO_ROOT}/tests/fixtures/sample_projects/python_fastapi/." "${DEMO_DIR}/"
rm -rf "${DEMO_DIR}/.forge"

cat <<EOF >"${DEMO_DIR}/.demo-script.sh"
#!/usr/bin/env bash
set -e
cd "${DEMO_DIR}"
clear
echo "# A 30-second tour of FORGE"
sleep 1
echo
echo "\$ forge doctor --quick"
forge doctor --quick
sleep 1
echo
echo "\$ forge index ."
forge index .
sleep 1
echo
echo "\$ forge build . --goal 'simple API to test locally with docker' --auto-approve"
forge build . --goal "simple API to test locally with docker" --auto-approve || true
sleep 1
echo
echo "\$ forge audit . --tail 5"
forge audit . --tail 5 || true
sleep 1
echo
echo "Done. Generated artifacts: ${DEMO_DIR}/.forge/generated"
EOF
chmod +x "${DEMO_DIR}/.demo-script.sh"

mkdir -p "$(dirname "${OUT}")"
asciinema rec --overwrite --idle-time-limit 1.5 \
  --title "FORGE — 30-second demo" \
  --command "${DEMO_DIR}/.demo-script.sh" \
  "${OUT}"

echo
echo "Recorded: ${OUT}"
echo "Preview:  asciinema play ${OUT}"
echo "Upload:   asciinema upload ${OUT}"
