#!/usr/bin/env bash
# watch-quota.sh — a bounded, idempotent usage WATCH that the AGENT runs when the
# Stop hook reports usage over the floor. It sleeps ONE bounded chunk (<= ~9 min,
# under the Bash-tool 10-min ceiling), re-checks usage, and exits 0 with a status
# line. The hook re-injects "run me" every turn until the window frees, so calling
# this repeatedly IS the wait — and each run is a tool call (= progress), so the
# Stop-hook block cap never trips. NOT a daemon; relaunches nothing; no `claude -p`.
#
# Last output line is the signal the agent acts on:
#   QUOTA FREED ...  → usage is below the floor; continue the loop.
#   WAITING ...      → still over the floor; it slept a chunk; run it again.
set -u
HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd)" || { echo "QUOTA FREED (no script dir) — continue."; exit 0; }
# shellcheck source=loop-lib.sh
. "$HERE/loop-lib.sh" 2>/dev/null || { echo "QUOTA FREED (usage lib unavailable) — continue."; exit 0; }
CONFIG="$(loop_config_file 2>/dev/null || true)"
USAGE_CACHE_DIR="$(loop_json_get "$CONFIG" budget.usageCacheDir "" 2>/dev/null || true)"
[ -n "${USAGE_CACHE_DIR// }" ] && export LOOP_USAGE_CACHE_DIR="$USAGE_CACHE_DIR"
# shellcheck source=usage-lib.sh
. "$HERE/usage-lib.sh" 2>/dev/null || { echo "QUOTA FREED (usage lib unavailable) — continue."; exit 0; }

FLOOR="$(loop_json_get "$CONFIG" budget.usagePauseFloor 96)"
case "$FLOOR" in ''|*[!0-9]*) FLOOR=96 ;; esac
BASE="$(loop_json_get "$CONFIG" budget.usagePollBase 540)"
case "$BASE" in ''|*[!0-9]*) BASE=540 ;; esac
MAXCHUNK=540   # cap a single sleep under the Bash-tool 10-min ceiling

# Read usage: own fetch first, fall back to any cache.
line="$(usage_fetch)"
if [ -z "$line" ]; then
  cf="$(usage_ensure_fresh 2>/dev/null || true)"
  [ -n "$cf" ] && line="$(usage_parse "$cf")"
fi
if [ -z "$line" ]; then
  echo "QUOTA FREED (usage data unavailable — cannot gate) — continue the loop."
  exit 0
fi
five="$(printf '%s' "$line" | cut -d'|' -f1)"
five_reset="$(printf '%s' "$line" | cut -d'|' -f2)"
seven="$(printf '%s' "$line" | cut -d'|' -f3)"
seven_reset="$(printf '%s' "$line" | cut -d'|' -f4)"
mx="$(usage_max_util "${five:-0}" "${seven:-0}")"
case "$mx" in ''|*[!0-9]*) mx=100 ;; esac

if [ "$mx" -lt "$FLOOR" ]; then
  echo "QUOTA FREED (5h=${five:-?}% 7d=${seven:-?}%, floor ${FLOOR}%) — continue the loop."
  exit 0
fi

# Still over floor → sleep one bounded chunk, then exit so the agent re-runs us.
resume_at="$(usage_later_reset "$five_reset" "$seven_reset")"
reset_epoch="$(usage_iso_epoch "$resume_at")"
now="$(date +%s 2>/dev/null || echo 0)"
rem=0
[ -n "$reset_epoch" ] && rem=$(( reset_epoch - now ))
iv="$(loop_poll_interval "$rem" "$BASE")"
[ "$iv" -gt "$MAXCHUNK" ] && iv="$MAXCHUNK"

mins=$(( rem / 60 )); [ "$mins" -lt 0 ] && mins=0
echo "WAITING: usage 5h=${five:-?}% 7d=${seven:-?}% ≥ floor ${FLOOR}%; window resets in ~${mins} min. Sleeping ${iv}s, then run me again."
[ -n "${LOOP_TEST_NOSLEEP:-}" ] || sleep "$iv"
exit 0
