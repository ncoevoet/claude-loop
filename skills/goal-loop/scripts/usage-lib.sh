#!/usr/bin/env bash
# usage-lib.sh — self-contained Anthropic usage (5h + weekly) source for the
# goal-loop pause / auto-resume feature. Sourced by goal-loop-gate.sh (the Stop
# hook) and watch-quota.sh (the in-session watch). Source it; do NOT execute.
#
# The plugin fetches usage ITSELF (its own cache under $LOOP_USAGE_CACHE_DIR);
# a present statusline cache is only an optional fast-path. Every function is
# FAIL-SOFT: on any error it yields empty / "no data" so the caller fails open
# (never pauses, never wedges). Stats come from Claude Code's own OAuth usage
# endpoint + credentials, present for any subscription (Pro/Max) login; API-key
# users have no such data, so the feature stays dormant.
#
# Line format produced by the readers: "5h|5h_reset|7d|7d_reset" (utilizations
# are floats; resets are ISO strings or empty).

# --- config (env-overridable; shared names match statusline.sh) ---
: "${CLAUDE_STATUSLINE_CACHE_DIR:=/tmp/claude}"
: "${LOOP_USAGE_CACHE_DIR:=${CLAUDE_STATUSLINE_CACHE_DIR}}"
: "${CLAUDE_USAGE_API_URL:=https://api.anthropic.com/api/oauth/usage}"
: "${CLAUDE_CREDENTIALS_PATH:=${HOME:-/root}/.claude/.credentials.json}"

usage_statusline_cache() { printf '%s/statusline-usage-cache.json' "$CLAUDE_STATUSLINE_CACHE_DIR"; }
usage_own_cache()        { printf '%s/claude-loop-usage-cache.json' "$LOOP_USAGE_CACHE_DIR"; }

# usage_claude_version — cached (1h) `claude --version`; "unknown" on failure.
usage_claude_version() {
  local cache="$LOOP_USAGE_CACHE_DIR/claude-loop-version" ver now mtime
  now="$(date +%s 2>/dev/null || echo 0)"
  if [ -f "$cache" ]; then
    mtime="$(stat -c %Y "$cache" 2>/dev/null || stat -f %m "$cache" 2>/dev/null || echo 0)"
    if [ "$(( now - mtime ))" -lt 3600 ]; then cat "$cache" 2>/dev/null && return 0; fi
  fi
  ver="$(claude --version 2>/dev/null | awk '{print $1}')"
  [ -n "$ver" ] || ver="unknown"
  mkdir -p "$LOOP_USAGE_CACHE_DIR" 2>/dev/null || true
  printf '%s' "$ver" > "$cache" 2>/dev/null || true
  printf '%s' "$ver"
}

# usage_parse FILE — echo "5h|5h_reset|7d|7d_reset" from a cache file's `.data`
# (or a bare data object). Empty on any failure.
usage_parse() {
  local f=${1:-}
  [ -f "$f" ] || return 0
  python3 - "$f" <<'PY' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
data = d.get("data") if isinstance(d, dict) and "data" in d else d
if not isinstance(data, dict):
    sys.exit(0)
def get(win, key):
    w = data.get(win)
    return w.get(key) if isinstance(w, dict) else None
def num(x):
    try:
        return float(x)
    except Exception:
        return 0.0
fh = num(get("five_hour", "utilization"));  fr = get("five_hour", "resets_at") or ""
sd = num(get("seven_day", "utilization"));  sr = get("seven_day", "resets_at") or ""
print("%s|%s|%s|%s" % (fh, fr, sd, sr))
PY
}

# _usage_cache_state FILE — prints: missing | nodata | backoff | stale | fresh
_usage_cache_state() {
  local f=${1:-}
  [ -f "$f" ] || { printf 'missing'; return 0; }
  python3 - "$f" <<'PY' 2>/dev/null || printf 'missing'
import json, sys, time
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("missing"); sys.exit(0)
data = d.get("data") if isinstance(d, dict) and "data" in d else d
if not isinstance(data, dict) or not data:
    print("nodata"); sys.exit(0)
now = time.time()
fetched = d.get("fetched_at") or 0
ttl = d.get("adaptive_ttl") or 1800
bo = d.get("backoff_until") or 0
if now < bo:
    print("backoff"); sys.exit(0)
print("fresh" if (now - fetched) < ttl else "stale")
PY
}

# usage_fetch — curl the OAuth usage API, validate JSON, write our own cache
# envelope, echo "5h|5h_reset|7d|7d_reset". Empty on any failure. Honors a
# LOOP_TEST_USAGE override (test seam) that bypasses the network entirely.
usage_fetch() {
  if [ -n "${LOOP_TEST_USAGE:-}" ]; then
    printf '%s' "$LOOP_TEST_USAGE"
    return 0
  fi
  command -v curl >/dev/null 2>&1 || return 0
  local creds token ver cache tmp now code bo
  creds="$CLAUDE_CREDENTIALS_PATH"
  [ -f "$creds" ] || return 0
  token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("claudeAiOauth",{}).get("accessToken","") or "")' "$creds" 2>/dev/null)" || return 0
  [ -n "$token" ] || return 0
  ver="$(usage_claude_version)"
  cache="$(usage_own_cache)"; tmp="${cache}.$$.tmp"; now="$(date +%s)"
  mkdir -p "$(dirname "$cache")" 2>/dev/null || true
  code="$(curl -s --max-time 5 -o "$tmp" -w '%{http_code}' \
      -H "Accept: application/json" -H "Content-Type: application/json" \
      -H "Authorization: Bearer $token" \
      -H "anthropic-beta: oauth-2025-04-20" \
      -H "User-Agent: claude-code/$ver" \
      "$CLAUDE_USAGE_API_URL" 2>/dev/null)" || { rm -f "$tmp"; return 0; }

  if [ "$code" = "200" ] && [ -s "$tmp" ] \
      && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$tmp" 2>/dev/null; then
    python3 - "$tmp" "$cache" "$now" <<'PY' 2>/dev/null || true
import json, sys
src, dst, now = sys.argv[1], sys.argv[2], int(sys.argv[3])
data = json.load(open(src))
def u(win):
    w = data.get(win) or {}
    try:
        return float(w.get("utilization") or 0)
    except Exception:
        return 0.0
mx = max(u("five_hour"), u("seven_day"))
ttl = 300 if mx > 80 else 900 if mx > 50 else 1800
out = {"fetched_at": now, "adaptive_ttl": ttl, "backoff_until": 0, "data": data}
json.dump(out, open(dst, "w"))
PY
    rm -f "$tmp"
    usage_parse "$cache"
    return 0
  fi

  # Failure: 429 → 15-min backoff, anything else → 5-min; keep stale data if any.
  bo=300; [ "$code" = "429" ] && bo=900
  python3 - "$cache" "$now" "$bo" <<'PY' 2>/dev/null || true
import json, os, sys
cache, now, bo = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
fetched, data = now, None
try:
    if os.path.exists(cache):
        prev = json.load(open(cache))
        data = prev.get("data")
        fetched = prev.get("fetched_at") or now
except Exception:
    data = None
out = {"fetched_at": fetched, "adaptive_ttl": 900, "backoff_until": now + bo, "data": data}
json.dump(out, open(cache, "w"))
PY
  rm -f "$tmp"
  return 0
}

# usage_ensure_fresh — print the path to the freshest usable cache file (which
# the gate then reads). Prefers a FRESH statusline cache (free, exact parity),
# then our own cache, fetching when stale. Prints nothing when no data exists.
usage_ensure_fresh() {
  local sl own st
  sl="$(usage_statusline_cache)"
  [ "$(_usage_cache_state "$sl")" = fresh ] && { printf '%s' "$sl"; return 0; }
  own="$(usage_own_cache)"
  st="$(_usage_cache_state "$own")"
  [ "$st" = fresh ] && { printf '%s' "$own"; return 0; }
  [ "$st" = backoff ] || usage_fetch >/dev/null 2>&1 || true
  case "$(_usage_cache_state "$own")" in fresh|stale|backoff) printf '%s' "$own"; return 0 ;; esac
  case "$(_usage_cache_state "$sl")" in fresh|stale|backoff) printf '%s' "$sl"; return 0 ;; esac
  return 0
}

# usage_max_util A B — integer floor of max(A,B); 0 / 100 fallbacks are decided
# by the caller (it guards non-numeric output).
usage_max_util() {
  python3 - "${1:-0}" "${2:-0}" <<'PY' 2>/dev/null || printf '0'
import sys
def n(x):
    try:
        return float(x)
    except Exception:
        return 0.0
print(int(max(n(sys.argv[1]), n(sys.argv[2]))))
PY
}

# usage_iso_epoch ISO — epoch seconds for an ISO-8601 timestamp; empty if absent.
usage_iso_epoch() {
  local iso=${1:-} e
  { [ -z "$iso" ] || [ "$iso" = "null" ]; } && return 0
  e="$(date -d "$iso" +%s 2>/dev/null)" || e=""
  if [ -z "$e" ]; then
    e="$(python3 - "$iso" <<'PY' 2>/dev/null || true
import datetime, sys
s = sys.argv[1].replace("Z", "+00:00")
try:
    print(int(datetime.datetime.fromisoformat(s).timestamp()))
except Exception:
    pass
PY
)"
  fi
  printf '%s' "$e"
}

# usage_later_reset A B — the later (max-epoch) of two ISO timestamps; this is
# the BINDING reset (the loop is free only once every tracked window has reset).
usage_later_reset() {
  local a=${1:-} b=${2:-} ea eb
  ea="$(usage_iso_epoch "$a")"; eb="$(usage_iso_epoch "$b")"
  if [ -n "$ea" ] && [ -n "$eb" ]; then
    if [ "$ea" -ge "$eb" ]; then printf '%s' "$a"; else printf '%s' "$b"; fi
  elif [ -n "$ea" ]; then printf '%s' "$a"
  elif [ -n "$eb" ]; then printf '%s' "$b"
  fi
}

# loop_poll_interval REMAINING_SECONDS [BASE] — adaptive watch cadence, keyed
# off time-to-(binding reset): far off → poll rarely; near a reset → poll often.
# "15-min base, more/less depending on how much wait is left."
loop_poll_interval() {
  local rem=${1:-0} base=${2:-900} iv
  case "$rem" in ''|*[!0-9-]*) printf '%s' "$base"; return 0 ;; esac
  if   [ "$rem" -ge 7200 ]; then iv=1800    # >=2h away → nothing soon, poll rarely
  elif [ "$rem" -ge 1800 ]; then iv="$base" # 30m..2h  → base cadence (15m)
  else                           iv=300     # <30m     → reset imminent, poll often
  fi
  [ "$iv" -lt 120 ]  && iv=120
  [ "$iv" -gt 1800 ] && iv=1800
  # Never sleep past the reset (+30s slack) so the flip is caught promptly.
  if [ "$rem" -gt 0 ] && [ "$iv" -gt "$(( rem + 30 ))" ]; then iv=$(( rem + 30 )); fi
  printf '%s' "$iv"
}
