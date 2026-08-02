#!/usr/bin/env bash
# Feeds seed-urls.txt through `jobwatch add`, which verifies each feed before
# writing it to companies.yaml. Failures are expected and harmless -- companies
# migrate between ATS platforms constantly.
#
# Usage:  ./bootstrap.sh [urlfile]

set -uo pipefail
cd "$(dirname "$0")"

FILE="${1:-seed-urls.txt}"
[ -f "$FILE" ] || { echo "No such file: $FILE"; exit 1; }

ok=0; fail=0
while IFS= read -r url; do
  url="${url%%#*}"                       # strip trailing comments
  url="$(echo "$url" | xargs)"           # trim whitespace
  [ -z "$url" ] && continue
  echo "──────────────────────────────────────────────"
  if python3 -m jobwatch add "$url"; then ok=$((ok+1)); else fail=$((fail+1)); fi
  sleep 1
done < "$FILE"

echo
echo "══════════════════════════════════════════════"
echo "$ok added or already known · $fail skipped"
echo "Companies now watched: $(grep -c '^- name:' companies.yaml 2>/dev/null || echo 0)"
echo
echo "Next:  python -m jobwatch check"
