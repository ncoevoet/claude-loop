#!/usr/bin/env bash
# Deterministic test suite — no network / API key. Safe for CI.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
rc=0

echo "== JSON manifests valid =="
for f in "$ROOT/.claude-plugin/plugin.json" "$ROOT/.claude-plugin/marketplace.json" "$ROOT/hooks/hooks.json"; do
  if python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$f" 2>/dev/null; then
    echo "  ok: ${f#"$ROOT"/}"
  else
    echo "  INVALID JSON: $f"; rc=1
  fi
done

echo
echo "== shell syntax (bash -n) =="
for f in "$ROOT"/skills/goal-loop/scripts/*.sh; do
  if bash -n "$f"; then echo "  ok: ${f##*/}"; else echo "  SYNTAX ERROR: $f"; rc=1; fi
done

echo
echo "== shellcheck (if available) =="
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x --source-path="$ROOT/skills/goal-loop/scripts" "$ROOT"/skills/goal-loop/scripts/*.sh && echo "  shellcheck clean" || rc=1
else
  echo "  shellcheck not installed — skipped (CI runs it)"
fi

echo
echo "== python unittests =="
( cd "$HERE" && python3 -m unittest discover -s . -p "test_*.py" -v ) || rc=1

echo
if [ "$rc" -eq 0 ]; then echo "ALL TESTS PASSED"; else echo "TESTS FAILED"; fi
exit "$rc"
