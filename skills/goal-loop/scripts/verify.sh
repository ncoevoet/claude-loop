#!/usr/bin/env bash
# verify.sh — the goal-loop deterministic ORACLE.
#
# Runs the project's verification gates fast->slow, short-circuiting on the
# first failure, and writes a machine verdict to .claude/loop/verdict.json:
#   { pass, failingGate, evidence, reviewedSha, partial, stages, generated_at }
# Exit 0 = pass, 1 = a gate failed, 2 = bad usage/setup.
#
# Project-agnostic: gate commands come from .claude/loop.json (oracle.*) and,
# where unset, from scripts/detect-toolchain.sh discovery. Stage 5 (reviewall)
# is NOT run here — it is the agent's `/review-all gate` skill run; verify.sh
# READS its gate-verdict.json artifact and checks it is fresh + pass.
#
# The AGENT runs this (in-session, no hook timeout) so the result is visible to
# /goal's evaluator; the Stop hook only checks the verdict it produces.
#
# Usage: verify.sh [--force] [--print-sha]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=loop-lib.sh
. "$HERE/loop-lib.sh"

ROOT="$(loop_project_root)"
STATE_DIR="$(loop_state_dir)"
CONFIG="$(loop_config_file)"
VERDICT="$STATE_DIR/verdict.json"

force=0; print_sha=0
for a in "$@"; do
  case "$a" in
    --force) force=1 ;;
    --print-sha) print_sha=1 ;;
    *) echo "verify.sh: unknown arg: $a" >&2; exit 2 ;;
  esac
done

WORK_SHA="$(loop_work_sha)"
if [ "$print_sha" -eq 1 ]; then printf '%s\n' "$WORK_SHA"; exit 0; fi

mkdir -p "$STATE_DIR" || { echo "verify.sh: cannot create $STATE_DIR" >&2; exit 2; }

# Idempotent re-run: a verdict already computed for this exact working state is
# reused (a loop re-verifying unchanged code pays nothing). --force overrides.
if [ "$force" -eq 0 ] && [ -f "$VERDICT" ]; then
  cached_sha="$(loop_json_get "$VERDICT" reviewedSha "")"
  if [ -n "$cached_sha" ] && [ "$cached_sha" = "$WORK_SHA" ]; then
    cat "$VERDICT"
    [ "$(loop_json_get "$VERDICT" pass false)" = "true" ] && exit 0 || exit 1
  fi
fi

# Discover toolchain defaults (JSON: ecosystem/framework/test/lint/typecheck/build).
TOOLCHAIN_JSON="$(bash "$HERE/detect-toolchain.sh" "$ROOT" 2>/dev/null || echo '{}')"
tc() { printf '%s' "$TOOLCHAIN_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get(sys.argv[1],'') or '')" "$1" 2>/dev/null; }

# Per-stage command: config oracle.<stage> wins, else discovery (no discovery
# default for 'architecture' — it is project-specific, config-only).
cmd_for() {
  local stage=$1 cfg
  cfg="$(loop_json_get "$CONFIG" "oracle.$stage" "")"
  if [ -n "$cfg" ]; then printf '%s' "$cfg"; return; fi
  case "$stage" in
    lint) tc lint ;; test) tc test ;; typecheck) tc typecheck ;; build) tc build ;;
    *) printf '' ;;
  esac
}

timeout_for() {
  case "$1" in
    lint) echo 120 ;; test) echo 300 ;; typecheck) echo 180 ;;
    architecture) echo 180 ;; build) echo 600 ;; *) echo 300 ;;
  esac
}

# Mandatory stages: config oracle.mandatory (a list), else default to whichever
# of lint/test were discovered — conservative + fast. Heavier gates
# (typecheck/architecture/build/reviewall) are opt-in via config.
MANDATORY="$(loop_json_get "$CONFIG" oracle.mandatory "" | tr '\n' ' ')"
if [ -z "${MANDATORY// }" ]; then
  MANDATORY=""
  [ -n "$(cmd_for lint)" ] && MANDATORY="$MANDATORY lint"
  [ -n "$(cmd_for test)" ] && MANDATORY="$MANDATORY test"
fi

ORDER=(lint test typecheck architecture build reviewall)
is_mandatory() { case " $MANDATORY " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

have_timeout=0; command -v timeout >/dev/null 2>&1 && have_timeout=1
run_cmd() {  # run_cmd <timeout> <command-string>; echoes combined output, returns rc
  if [ "$have_timeout" -eq 1 ]; then ( cd "$ROOT" && timeout "$1" bash -c "$2" ) 2>&1
  else ( cd "$ROOT" && bash -c "$2" ) 2>&1; fi
}

RA_FLOOR="$(loop_json_get "$CONFIG" reviewall.severityFloor critical)"

PASS=true; FAILING=""; EVIDENCE=""; STAGES=""
record() { STAGES="${STAGES}${1}	${2}
"; }

for stage in "${ORDER[@]}"; do
  is_mandatory "$stage" || continue

  if [ "$stage" = "reviewall" ]; then
    gv="$(loop_gate_verdict_file)"
    if [ ! -f "$gv" ]; then
      PASS=false; FAILING=reviewall; record reviewall MISSING
      EVIDENCE="No gate-verdict.json. Run: /review-all gate --severity $RA_FLOOR"; break
    fi
    if [ "$(loop_json_get "$gv" reviewedSha "")" != "$WORK_SHA" ]; then
      PASS=false; FAILING=reviewall; record reviewall STALE
      EVIDENCE="gate-verdict.json reviewed a different tree. Re-run: /review-all gate --severity $RA_FLOOR"; break
    fi
    if [ "$(loop_json_get "$gv" pass false)" != "true" ]; then
      PASS=false; FAILING=reviewall; record reviewall FAIL
      EVIDENCE="$(loop_json_get "$gv" blocking "[]")"; break
    fi
    record reviewall PASS; continue
  fi

  cmd="$(cmd_for "$stage")"
  [ -z "$cmd" ] && { record "$stage" "SKIP(no-command)"; continue; }
  out="$(run_cmd "$(timeout_for "$stage")" "$cmd")"; rc=$?
  if [ "$rc" -eq 0 ]; then
    record "$stage" PASS
  else
    PASS=false; FAILING="$stage"; record "$stage" "FAIL(rc=$rc)"
    EVIDENCE="$(printf '%s\n' "$out" | tail -n 30)"
    [ "$rc" -eq 124 ] && EVIDENCE="TIMEOUT after $(timeout_for "$stage")s.
${EVIDENCE}"
    break
  fi
done

GEN_AT="$(date -u +%FT%TZ 2>/dev/null || echo '')"
EVIDENCE="$EVIDENCE" STAGES="$STAGES" python3 - "$VERDICT" "$WORK_SHA" "$PASS" "$FAILING" "$GEN_AT" <<'PY'
import json, os, sys
verdict_path, sha, passed, failing, gen = sys.argv[1:6]
stages = {}
for line in os.environ.get("STAGES", "").splitlines():
    if "\t" in line:
        k, v = line.split("\t", 1); stages[k] = v
out = {
    "tool": "goal-loop", "mode": "verify", "generated_at": gen,
    "reviewedSha": sha, "pass": passed == "true",
    "failingGate": (failing or None), "partial": False,
    "stages": stages, "evidence": os.environ.get("EVIDENCE", ""),
}
with open(verdict_path, "w") as fh:
    json.dump(out, fh, indent=2); fh.write("\n")
print(json.dumps(out, indent=2))
PY

[ "$PASS" = "true" ] && exit 0 || exit 1
