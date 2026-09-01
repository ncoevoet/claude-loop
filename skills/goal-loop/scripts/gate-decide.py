#!/usr/bin/env python3
"""gate-decide.py — the goal-loop Stop-hook decision engine.

Given the current STATE.json, the oracle verdict.json, and the current work-sha,
decide whether the session may stop. Pure + deterministic + FAIL-OPEN: any
unexpected error exits 0 (allow stop) so a harness bug can never wedge a session.

Exit 0  → allow stop (goal complete, blocked/escalated, budget, aborted, or no
          active loop). Silent, except the run-budget escalation, which emits a
          `systemMessage` JSON object on stdout (the only user-visible channel a
          Stop hook has when it allows the stop).
Exit 2  → block stop (keep working). The reason is printed to STDERR; Claude Code
          feeds it back to the model as the instruction for the next turn.

The hook NEVER runs the oracle itself (a Stop hook has a short timeout; a build +
review can take minutes). The AGENT runs scripts/verify.sh in-session; this hook
only checks the verdict it produced — fresh (sha matches the working tree) and
pass — which also makes the result unspoofable (it reads the file, not prose).

Usage: gate-decide.py STATE_JSON VERDICT_JSON WORK_SHA VERIFY_HINT [STOP_HOOK_ACTIVE]
"""
import datetime
import glob
import hashlib
import json
import os
import sys


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _write_blocker(state_dir, kind, detail):
    try:
        with open(os.path.join(state_dir, "BLOCKER.md"), "w") as fh:
            fh.write(f"# Goal-loop blocker: {kind}\n\n{detail}\n")
    except Exception:
        pass


def _block(reason):
    sys.stderr.write(reason + "\n")
    sys.exit(2)


def _tell_user(message):
    """Reach the HUMAN on an allow-stop path. A Stop hook's plain stdout/stderr is
    invisible (debug log only) when it exits 0 — the only user-visible channel is a
    structured `systemMessage`, so stdout must carry that JSON object and nothing else."""
    json.dump({"systemMessage": message[:9000]}, sys.stdout)
    sys.stdout.write("\n")


def _env_int(name, default):
    try:
        return int(float(os.environ.get(name) or default))
    except Exception:
        return default


def _transcript_files(path):
    """The session transcript plus its subagent transcripts. Claude Code stores
    Task-tool runs in SEPARATE files under `<session-id>/subagents/agent-*.jsonl`
    (documented layout), not inline — and that is where most of a marathon's cost
    lives, so a run budget that ignored them would never trip."""
    files = [path]
    base = path[:-6] if path.endswith(".jsonl") else path
    files.extend(sorted(glob.glob(os.path.join(base, "subagents", "*.jsonl"))))
    return files


def _transcript_counts(files):
    """(turns, output_tokens) over the given transcripts. A turn is one assistant
    message. Claude Code writes one line per content block, each repeating the SAME
    `message.id` AND the same `usage` object — so both counts must dedupe by id or
    they inflate (measured 2x-4.5x). The JSONL format is internal to Claude Code and
    may change between versions; every read here is best-effort by design."""
    turns = out = 0
    seen = set()
    for path in files:
        try:
            fh = open(path, errors="replace")
        except Exception:
            continue
        with fh:
            for line in fh:
                if '"assistant"' not in line:     # cheap prefilter: skip user/meta lines
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                mid = msg.get("id")
                if mid:
                    if mid in seen:
                        continue
                    seen.add(mid)
                turns += 1
                try:
                    out += int((msg.get("usage") or {}).get("output_tokens") or 0)
                except Exception:
                    pass
    return turns, out


def _over_run_budget():
    """Run ceiling for THIS session: turns + cumulative output tokens, derived from
    the transcript the Stop hook was handed. `maxIterations` bounds how many times
    the hook BLOCKS, not the work each block buys, so a stuck loop can burn millions
    of tokens between two blocks — this is that missing bound. Returns a dict when a
    ceiling is reached, else None (no transcript / under budget / 0 = disabled / any
    error → fail-open, behavior unchanged)."""
    try:
        path = (os.environ.get("LOOP_TRANSCRIPT") or "").strip()
        if not path or not os.path.isfile(path):
            return None
        max_turns = _env_int("LOOP_MAX_TURNS", 3000)
        max_out = _env_int("LOOP_MAX_OUTPUT_TOKENS", 1000000)
        if max_turns <= 0 and max_out <= 0:
            return None
        turns, out = _transcript_counts(_transcript_files(path))
        hit = []
        if 0 < max_turns <= turns:
            hit.append("turns")
        if 0 < max_out <= out:
            hit.append("outputTokens")
        if not hit:
            return None
        return {"turns": turns, "outputTokens": out, "maxTurns": max_turns,
                "maxOutputTokens": max_out, "hit": hit}
    except Exception:
        return None


def _oracle_summary(verdict, fresh):
    """One-line oracle state for the escalation message. A spent budget is NEVER a
    pass, so every branch says so out loud — the goal is unverified."""
    if not isinstance(verdict, dict):
        return "never run (no verdict.json) — goal UNVERIFIED"
    gate = verdict.get("failingGate") or "unknown"
    if not fresh:
        return ("stale verdict (pass=%s, gate `%s`) judging other code — goal "
                "UNVERIFIED on the current changes" % (verdict.get("pass"), gate))
    return "fresh FAIL at gate `%s` — goal UNVERIFIED" % gate


def _run_budget_stop(state_path, state, state_dir, info, oracle):
    """The run ceiling is spent without the oracle passing: ALLOW the stop (exit 0)
    rather than force another iteration, and escalate to the human. Deliberately
    does NOT set the oracle to pass — the objective is reported as unverified."""
    state["status"] = "budget_exhausted"
    state["runBudget"] = info
    state.pop("usageHold", None)
    _save(state_path, state)
    msg = ("goal-loop: budget exceeded (%d turns / %d output tokens; caps %d/%d) — "
           "escalating to human; oracle state: %s. The objective is NOT done: stop "
           "here and report it as unverified."
           % (info["turns"], info["outputTokens"], info["maxTurns"],
              info["maxOutputTokens"], oracle))
    _write_blocker(state_dir, "run-budget", msg)
    _tell_user(msg)
    sys.exit(0)


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _latest_reset(isos):
    """The binding reset — the loop is free only once EVERY tripped window has
    reset, so the relevant time is the latest of them."""
    best, best_s = None, None
    for s in isos:
        dt = _parse_iso(s)
        if dt is not None and (best is None or dt > best):
            best, best_s = dt, s
    return best_s


def _usage_over_floor():
    """Read the usage cache the bash hook handed us (LOOP_USAGE_CACHE) and decide
    whether any tracked window is at/above the pause floor. Returns a dict
    describing the pause, or None (no data / under floor / any error → fail-open,
    so a missing cache never changes behavior). Pure read — never fetches."""
    try:
        cache = os.environ.get("LOOP_USAGE_CACHE", "").strip()
        if not cache:
            return None
        try:
            floor = float(os.environ.get("LOOP_USAGE_FLOOR") or 96)
        except Exception:
            floor = 96.0
        wins = (os.environ.get("LOOP_USAGE_WINDOWS") or "five_hour seven_day").replace(",", " ").split()
        d = _load(cache)
        if not isinstance(d, dict):
            return None
        data = d.get("data") if "data" in d else d
        if not isinstance(data, dict):
            return None
        tripped, util, resets = [], {}, {}
        for w in wins:
            wd = data.get(w)
            if not isinstance(wd, dict):
                continue
            try:
                u = float(wd.get("utilization") or 0)
            except Exception:
                u = 0.0
            util[w] = u
            resets[w] = wd.get("resets_at")
            if u >= floor:
                tripped.append(w)
        if not tripped:
            return None
        window = tripped[0] if len(tripped) == 1 else "both"
        resume_at = _latest_reset([resets.get(w) for w in tripped])
        return {"window": window, "util": util, "resumeAt": resume_at, "floor": floor}
    except Exception:
        return None


def _write_pause(state_dir, info):
    try:
        lines = ["# Goal-loop paused: API usage ≥ %.0f%%\n" % info["floor"]]
        for w, u in info.get("util", {}).items():
            lines.append("- %s: %.1f%%" % (w, u))
        if info.get("resumeAt"):
            lines.append("\nWindow resets at: %s" % info["resumeAt"])
        lines.append(
            "\nThe wait is too long to hold this session open (e.g. the weekly window). "
            "When the window has reset, run `/goal-loop resume` (or re-run `/goal-loop`) to "
            "continue. `touch .claude/loop/ABORT` to cancel for good.")
        with open(os.path.join(state_dir, "PAUSE.md"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass


def _pause(state_path, state, state_dir, info):
    """Long-wait fallback (e.g. the multi-day weekly window): a session cannot be
    held open that long, so HALT and ALLOW stop (exit 0) for a manual resume. Does
    NOT bump iteration (a pause is neither progress nor a failed attempt)."""
    state["status"] = "paused"
    state["pausedReason"] = "usage"
    state["pausedWindow"] = info["window"]
    state["pausedUtil"] = {k: round(v, 1) for k, v in info.get("util", {}).items()}
    state["resumeAt"] = info.get("resumeAt")
    state["pausedAt"] = _now_iso()
    _save(state_path, state)
    _write_pause(state_dir, info)
    sys.exit(0)


def _usage_hold(state_path, state, state_dir, info, verify_hint, sig=None):
    """Usage is over the floor on a keep-working path. If the window resets soon
    (≤ maxAutoWait), HALT IN-SESSION: block (exit 2) with an instruction to run the
    watch script. The agent re-runs it each turn until quota frees — each run is a
    tool call (= progress), so the Stop-hook block cap never trips and no `claude -p`
    is needed. If the wait would be too long (weekly window — days) or the reset is
    unknown, fall back to a manual pause (allow stop)."""
    try:
        max_wait = int(os.environ.get("LOOP_USAGE_MAX_WAIT") or 21600)
    except Exception:
        max_wait = 21600
    remaining = None
    dt = _parse_iso(info.get("resumeAt"))
    if dt is not None:
        remaining = (dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()

    # Too long to hold a session open (or unknown reset) → manual pause.
    if remaining is None or remaining > max_wait:
        _pause(state_path, state, state_dir, info)  # exits 0

    # Short wait → keep the session and watch in-session.
    state["usageHold"] = {
        "window": info["window"],
        "util": {k: round(v, 1) for k, v in info.get("util", {}).items()},
        "resumeAt": info.get("resumeAt"),
        "sig": sig,
    }
    _save(state_path, state)  # NB: iteration NOT bumped while waiting
    watch = os.path.join(os.path.dirname(verify_hint), "watch-quota.sh")
    util_str = ", ".join("%s=%.0f%%" % (k, v) for k, v in info.get("util", {}).items())
    mins = int(remaining // 60) if remaining and remaining > 0 else 0
    _block(
        f"goal-loop: API usage is over the floor ({util_str}); the {info['window']} window "
        f"resets in ~{mins} min (at {info.get('resumeAt')}). HALT real work and wait for the "
        f"window — do not burn the rest of the quota. Run `bash {watch}` (allow up to 10 "
        f"minutes; set the Bash tool timeout to 600000): it sleeps one bounded chunk and "
        f"re-checks usage. Repeat it every turn until it prints `QUOTA FREED`, then continue "
        f"the loop. Do nothing else meanwhile. (no iteration is spent while waiting)")


def main():
    state_path = sys.argv[1]
    verdict_path = sys.argv[2]
    work_sha = sys.argv[3]
    verify_hint = sys.argv[4] if len(sys.argv) > 4 else "scripts/verify.sh"

    state = _load(state_path)
    if not isinstance(state, dict):
        sys.exit(0)  # not armed / unreadable → allow stop (fail-open)
    if state.get("status") != "running":
        sys.exit(0)

    state_dir = os.path.dirname(state_path)
    iteration = int(state.get("iteration", 0))
    cap = int(state.get("maxIterations", 20))
    same = int(state.get("sameFailureCount", 0))
    last_sig = state.get("lastFailureSig")
    max_rep = int(state.get("maxRepeatedFailures", 3))

    # Budget ceiling — bounded by construction. Allow stop + escalate.
    if iteration >= cap:
        state["status"] = "budget_exhausted"
        _save(state_path, state)
        _write_blocker(state_dir, "budget",
                       f"Reached maxIterations={cap} without the oracle passing. "
                       f"Last failing gate: {state.get('lastFailureSig')}.")
        sys.exit(0)

    # Usage guard: if a tracked window is at/above the floor, HALT instead of
    # forcing more work. Only the keep-working (exit 2) paths below honor it — a
    # fresh PASS still completes and a genuinely stuck loop still escalates.
    # Computed once; None unless we have data over floor.
    over_usage = _usage_over_floor()
    if not over_usage:
        state.pop("usageHold", None)  # clear any stale in-session-wait marker

    verdict = _load(verdict_path)
    fresh = isinstance(verdict, dict) and verdict.get("reviewedSha") == work_sha

    # Run ceiling (turns + output tokens). Checked before EVERY keep-working path —
    # including the usage hold — so a loop that cannot converge escalates instead of
    # buying itself another few hundred turns. A fresh PASS still completes normally.
    if not (fresh and verdict.get("pass") is True):
        over_run = _over_run_budget()
        if over_run:
            _run_budget_stop(state_path, state, state_dir, over_run,
                             _oracle_summary(verdict, fresh))

    # Oracle not run on the current working tree → must verify before stopping.
    if not fresh:
        if over_usage:
            _usage_hold(state_path, state, state_dir, over_usage, verify_hint)
        state["iteration"] = iteration + 1
        _save(state_path, state)
        why = "no verdict yet" if not isinstance(verdict, dict) else "the verdict is stale (the code changed since it was produced)"
        _block(
            f"goal-loop: the oracle has not been run on the current changes ({why}). "
            f"Before finishing, run `bash {verify_hint}` (and `/review-all gate` if it is a mandatory stage), "
            f"then address any failing gate. Do not stop until the oracle passes or you hit a real blocker. "
            f"(iteration {iteration + 1}/{cap})")

    if verdict.get("pass") is True:
        state["status"] = "complete"
        state.pop("usageHold", None)
        _save(state_path, state)
        sys.exit(0)

    # Oracle failed. Stuck-detector: same failure signature N times → escalate.
    failing = verdict.get("failingGate") or "unknown"
    evidence = str(verdict.get("evidence", ""))
    sig = failing + ":" + hashlib.sha256(evidence.encode("utf-8", "replace")).hexdigest()[:16]

    # A continuing usage wait is neither progress nor a fresh failed attempt: the
    # agent is only running watch-quota.sh, making no fix. Re-counting it toward
    # the stuck cap would false-escalate a long wait as "not making progress", so
    # freeze the counter while the SAME failure is held under an active usageHold.
    prev_hold = state.get("usageHold")
    continuing_wait = (over_usage and isinstance(prev_hold, dict)
                       and prev_hold.get("sig") == sig)

    if not continuing_wait:
        same = same + 1 if sig == last_sig else 1
        state["lastFailureSig"] = sig
        state["sameFailureCount"] = same

        if same >= max_rep:
            state["status"] = "blocked"
            _save(state_path, state)
            _write_blocker(state_dir, "stuck",
                           f"Gate `{failing}` failed {same} times with the same signature — "
                           f"the loop is not making progress.\n\nEvidence:\n{evidence}")
            sys.exit(0)  # allow stop → the skill surfaces the blocker via AskUserQuestion

    # Usage over the floor → halt here rather than push on. The failure signature
    # is recorded under usageHold so the next turn recognizes the wait and keeps
    # the stuck counter frozen until the window frees (or the failure changes).
    if over_usage:
        _usage_hold(state_path, state, state_dir, over_usage, verify_hint, sig)

    state["iteration"] = iteration + 1
    _save(state_path, state)
    _block(
        f"goal-loop: the oracle FAILED at gate `{failing}`. Fix it and re-run `bash {verify_hint}`. "
        f"Do not stop until the oracle passes or you hit a real blocker.\n\nEvidence:\n{evidence}\n"
        f"(iteration {iteration + 1}/{cap}, same-failure {same}/{max_rep})")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Absolute fail-open: never wedge a session on a harness bug.
        sys.exit(0)
