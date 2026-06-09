#!/usr/bin/env bash
# loop-lib.sh — shared helpers for the goal-loop oracle (verify.sh) and the
# Stop hook (goal-loop-gate.sh). Source it; do not execute.
#
# Pure-ish: only `loop_work_sha` touches git. No global side effects on source.

# Project root = nearest ancestor of cwd holding a project marker (so a monorepo
# subproject like apps/ng is the root, not the whole repo), else git toplevel,
# else cwd. Loops operate on this subtree of the working tree.
loop_project_root() {
  local d="$PWD"
  while [ "$d" != "/" ] && [ -n "$d" ]; do
    if [ -f "$d/.claude/loop.json" ] || [ -f "$d/package.json" ] \
      || [ -f "$d/pom.xml" ] || [ -f "$d/Cargo.toml" ] || [ -f "$d/go.mod" ] \
      || [ -f "$d/build.gradle" ] || [ -f "$d/build.gradle.kts" ] \
      || [ -d "$d/.git" ]; then
      printf '%s' "$d"; return 0
    fi
    d="$(dirname "$d")"
  done
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

loop_state_dir() { printf '%s/.claude/loop' "$(loop_project_root)"; }
loop_config_file() { printf '%s/.claude/loop.json' "$(loop_project_root)"; }
loop_gate_verdict_file() { printf '%s/.claude/review-all/gate-verdict.json' "$(loop_project_root)"; }

# work_sha — a stable fingerprint of the CURRENT working state, so a verdict can
# be matched to the exact code it judged. A loop usually has uncommitted changes
# and makes no commits between iterations, so HEAD alone is not enough: include
# the tracked diff AND the contents of untracked (not-ignored) files.
loop_work_sha() {
  local root gitroot rel
  root="$(loop_project_root)"
  gitroot="$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$root")"
  rel="${root#"$gitroot"}"; rel="${rel#/}"; [ -z "$rel" ] && rel="."
  {
    git -C "$gitroot" rev-parse HEAD 2>/dev/null || echo no-head
    git -C "$gitroot" diff HEAD -- "$rel" 2>/dev/null
    # untracked, non-ignored files under the subproject: hash names + contents.
    # EXCLUDE harness run-state (.claude/loop/, .claude/review-all/) — those are
    # written by the verify/gate run itself; including them would change the sha
    # every run and defeat the cache + freshness checks.
    git -C "$gitroot" ls-files --others --exclude-standard -- "$rel" 2>/dev/null \
      | while IFS= read -r f; do
          case "$f" in
            *.claude/loop/*|*.claude/review-all/*) continue ;;
          esac
          sha256sum "$gitroot/$f" 2>/dev/null
        done
  } | sha256sum | cut -d' ' -f1
}

# loop_json_get FILE DOT.PATH DEFAULT — read a nested key from a JSON file.
# Returns DEFAULT if the file is missing/unreadable or the key is absent/null.
# Lists are returned as newline-joined scalars; objects/other as compact JSON.
loop_json_get() {
  local file=$1 path=$2 default=${3:-}
  [ -f "$file" ] || { printf '%s' "$default"; return 0; }
  python3 - "$file" "$path" "$default" <<'PY' 2>/dev/null || printf '%s' "$default"
import json, sys
file, path, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(file) as fh:
        cur = json.load(fh)
except Exception:
    print(default, end=""); sys.exit(0)
for part in [p for p in path.split(".") if p]:
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        print(default, end=""); sys.exit(0)
if cur is None:
    print(default, end="")
elif isinstance(cur, list):
    print("\n".join(str(x) for x in cur), end="")
elif isinstance(cur, (dict,)):
    print(json.dumps(cur), end="")
elif isinstance(cur, bool):
    print("true" if cur else "false", end="")
else:
    print(cur, end="")
PY
}

# loop_state_get KEY DEFAULT — convenience reader for STATE.json top-level keys.
loop_state_get() {
  loop_json_get "$(loop_state_dir)/STATE.json" "$1" "$2"
}
