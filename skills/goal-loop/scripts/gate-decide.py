#!/usr/bin/env python3
"""gate-decide.py — the goal-loop Stop-hook decision engine.

Given the current STATE.json, the oracle verdict.json, and the current work-sha,
decide whether the session may stop. Pure + deterministic + FAIL-OPEN: any
unexpected error exits 0 (allow stop) so a harness bug can never wedge a session.

Exit 0  → allow stop (goal complete, blocked/escalated, budget, aborted, or no
          active loop). The bash wrapper prints nothing.
Exit 2  → block stop (keep working). The reason is printed to STDERR; Claude Code
          feeds it back to the model as the instruction for the next turn.

The hook NEVER runs the oracle itself (a Stop hook has a short timeout; a build +
review can take minutes). The AGENT runs scripts/verify.sh in-session; this hook
only checks the verdict it produced — fresh (sha matches the working tree) and
pass — which also makes the result unspoofable (it reads the file, not prose).

Usage: gate-decide.py STATE_JSON VERDICT_JSON WORK_SHA VERIFY_HINT [STOP_HOOK_ACTIVE]
"""
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

    verdict = _load(verdict_path)
    fresh = isinstance(verdict, dict) and verdict.get("reviewedSha") == work_sha

    # Oracle not run on the current working tree → must verify before stopping.
    if not fresh:
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
        _save(state_path, state)
        sys.exit(0)

    # Oracle failed. Stuck-detector: same failure signature N times → escalate.
    failing = verdict.get("failingGate") or "unknown"
    evidence = str(verdict.get("evidence", ""))
    sig = failing + ":" + hashlib.sha256(evidence.encode("utf-8", "replace")).hexdigest()[:16]
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
