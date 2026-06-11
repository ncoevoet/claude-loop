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

# Resolve fresh usage stats for the pause check (own fetch with a 5s curl, well
# within the hook's 30s budget; statusline cache used as a fast-path). All
# fail-soft: an empty cache → the decision engine sees no data → no pause.
USAGE_CACHE=""
if . "$HERE/usage-lib.sh" 2>/dev/null; then
  USAGE_CACHE="$(usage_ensure_fresh 2>/dev/null || true)"
fi
CONFIG="$(loop_config_file 2>/dev/null || true)"
FLOOR="$(loop_json_get "$CONFIG" budget.usagePauseFloor 96 2>/dev/null || echo 96)"
WINDOWS="$(loop_json_get "$CONFIG" budget.usageWindows 'five_hour seven_day' 2>/dev/null | tr '\n' ' ')"
[ -n "${WINDOWS// }" ] || WINDOWS="five_hour seven_day"
MAXWAIT="$(loop_json_get "$CONFIG" budget.maxAutoWait 21600 2>/dev/null || echo 21600)"

LOOP_USAGE_CACHE="$USAGE_CACHE" LOOP_USAGE_FLOOR="$FLOOR" LOOP_USAGE_WINDOWS="$WINDOWS" \
  LOOP_USAGE_MAX_WAIT="$MAXWAIT" \
  python3 "$HERE/gate-decide.py" "$STATE" "$STATE_DIR/verdict.json" "$WORK_SHA" "$HERE/verify.sh"
rc=$?

# Short usage waits halt IN-SESSION (the engine blocks with a watch-quota.sh
# instruction — no process to spawn here). Only the long-wait fallback sets
# status=paused; ping the user (best-effort) so they know to resume later.
if [ "$(loop_state_get status "" 2>/dev/null)" = "paused" ]; then
  command -v notify-send >/dev/null 2>&1 \
    && notify-send "goal-loop" "Paused: API usage at the floor. Run /goal-loop resume once the window resets." 2>/dev/null || true
fi
exit "$rc"
