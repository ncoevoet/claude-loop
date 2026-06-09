# Run-state — `.claude/loop/`

One active loop per project (v1). All files live under the target project's `.claude/loop/` (gitignored). The scripts resolve this from the git root.

## `STATE.json`

The authoritative loop state. Read/written by the skill (setup) and the Stop hook (each turn).

```json
{
  "status": "running",
  "iteration": 3,
  "maxIterations": 20,
  "maxRepeatedFailures": 3,
  "sameFailureCount": 1,
  "lastFailureSig": "test:81f52337ebb4cb16"
}
```

| Field | Meaning |
|---|---|
| `status` | `running` (armed) · `complete` (oracle passed) · `blocked` (stuck, escalate) · `budget_exhausted` (hit cap) · `aborted` (kill switch) |
| `iteration` | Count of Stop-hook blocks (oracle fail or "not verified yet"). Bumped by the hook; at `maxIterations` → `budget_exhausted`. |
| `sameFailureCount` / `lastFailureSig` | Stuck-detector: a signature is `failingGate:sha256(evidence)[:16]`. Same signature `maxRepeatedFailures` times → `blocked`. |

The Stop hook (`gate-decide.py`) is the only writer once `running`. **Only `status=running` arms the hook** — any terminal status releases it (the session may stop).

## `verdict.json`

Written by `scripts/verify.sh` (the oracle). The Stop hook reads it but never writes it. Schema in `oracle.md`. The hook compares `verdict.reviewedSha` to the live work-sha to decide freshness.

## `WORKLOG.md`

Append-only progress ledger the agent maintains: what changed each iteration, what was verified, what remains, and any in-scope assumptions. Survives context summarization — it is the loop's memory on disk.

## `BLOCKER.md`

Written by the Stop hook when the loop stops as `blocked` or `budget_exhausted`. Contains the blocker kind + evidence. The skill surfaces it to the user via `AskUserQuestion` (see `escalation.md`).

## `ABORT`

Kill switch. `touch .claude/loop/ABORT` (or `/goal-loop abort`) → the Stop hook sets `status=aborted` and releases on the next turn. Remove the file to re-arm a fresh loop.

## Lifecycle

```
arm (skill) ──> running ──oracle pass──> complete        (hook allows stop)
                  │  ├─ same failure ×N ─> blocked        (hook allows stop + BLOCKER)
                  │  └─ iteration ≥ cap ──> budget_exhausted (hook allows stop + BLOCKER)
                  └─ ABORT / "stop loop" ─> aborted        (hook allows stop)
```
