#!/usr/bin/env bash
# goal-loop-gate.sh — Claude Code Stop hook (the enforcement centerpiece).
#
# Fast freshness-gate: while a goal-loop is active (STATE.status=running) it
# blocks the session from stopping until the oracle verdict is fresh AND pass.
# It does NOT run the oracle (a Stop hook has a short timeout) — the agent runs
# scripts/verify.sh; this hook only reads the verdict it produced.
#
# Stop-hook contract: exit 2 + stderr = block (keep working); exit 0 = allow.
# FAIL-OPEN at every step: any missing tool / unreadable state → exit 0, so the
# hook can never wedge a session. Dormant (exit 0) unless a loop is armed.
set -u
cat >/dev/null 2>&1 || true            # consume the Stop-hook stdin context

command -v python3 >/dev/null 2>&1 || exit 0
command -v git >/dev/null 2>&1 || exit 0

HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd)" || exit 0
# shellcheck source=loop-lib.sh
. "$HERE/loop-lib.sh" 2>/dev/null || exit 0

STATE_DIR="$(loop_state_dir 2>/dev/null)" || exit 0
STATE="$STATE_DIR/STATE.json"
[ -f "$STATE" ] || exit 0               # not armed → allow stop

# Kill switch: an ABORT file (user "stop loop") releases the gate immediately.
if [ -f "$STATE_DIR/ABORT" ]; then
  python3 - "$STATE" <<'PY' 2>/dev/null || true
import json, sys
try:
    s = json.load(open(sys.argv[1])); s["status"] = "aborted"
    json.dump(s, open(sys.argv[1], "w"), indent=2)
except Exception:
    pass
PY
  exit 0
fi

[ "$(loop_state_get status "" 2>/dev/null)" = "running" ] || exit 0

WORK_SHA="$(loop_work_sha 2>/dev/null)" || exit 0
[ -n "$WORK_SHA" ] || exit 0

python3 "$HERE/gate-decide.py" "$STATE" "$STATE_DIR/verdict.json" "$WORK_SHA" "$HERE/verify.sh"
exit $?
