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
| `status` | `running` (armed; also covers a short in-session usage wait) · `paused` (long usage wait — manual resume) · `complete` (oracle passed) · `blocked` (stuck, escalate) · `budget_exhausted` (hit cap) · `aborted` (kill switch) |
| `iteration` | Count of Stop-hook blocks (oracle fail or "not verified yet"). Bumped by the hook; at `maxIterations` → `budget_exhausted`. A usage wait/pause does **not** bump it. |
| `sameFailureCount` / `lastFailureSig` | Stuck-detector: a signature is `failingGate:sha256(evidence)[:16]`. Same signature `maxRepeatedFailures` times → `blocked`. |
| `usageHold` | Present while the loop is HALTED IN-SESSION for usage (status stays `running`): `{window, util, resumeAt}`. The hook blocks each turn telling the agent to run `watch-quota.sh`; cleared once usage drops below the floor. |
| `pausedReason` / `pausedWindow` / `pausedUtil` / `resumeAt` / `pausedAt` | Written for the long-wait fallback (`status=paused`, reset beyond `maxAutoWait` — e.g. the weekly window): why (`usage`), which window (`five_hour` · `seven_day` · `both`), per-window % at pause, the binding reset ISO, and when. Cleared on resume. |

The Stop hook (`gate-decide.py`) is the only writer once `running`. **Only `status=running` arms the hook** — any other status (including `paused`) releases it. A short usage wait keeps `status=running` (the hook blocks each turn for `watch-quota.sh`); only a wait longer than `maxAutoWait` sets `status=paused` for a manual `/goal-loop resume`.

## `verdict.json`

Written by `scripts/verify.sh` (the oracle). The Stop hook reads it but never writes it. Schema in `oracle.md`. The hook compares `verdict.reviewedSha` to the live work-sha to decide freshness.

## `WORKLOG.md`

Append-only progress ledger the agent maintains: what changed each iteration, what was verified, what remains, and any in-scope assumptions. Survives context summarization — it is the loop's memory on disk.

## `BLOCKER.md`

Written by the Stop hook when the loop stops as `blocked` or `budget_exhausted`. Contains the blocker kind + evidence. The skill surfaces it to the user via `AskUserQuestion` (see `escalation.md`).

## `PAUSE.md`

Written by the Stop hook for the **long-wait fallback** (`status=paused`): the tripped window + %, the reset time, and the `/goal-loop resume` instruction. Removed on resume. A **short in-session wait** writes no marker — it lives in `STATE.usageHold` plus the per-turn hook message that tells the agent to run `watch-quota.sh`. Run-state under `.claude/loop/` (gitignored).

## `ABORT`

Kill switch. `touch .claude/loop/ABORT` (or `/goal-loop abort`) → the Stop hook sets `status=aborted` and releases on the next turn. Remove the file to re-arm a fresh loop.

## Lifecycle

```
arm (skill) ──> running ──oracle pass──> complete            (hook allows stop)
                  │  ├─ same failure ×N ─> blocked            (hook allows stop + BLOCKER)
                  │  ├─ iteration ≥ cap ──> budget_exhausted  (hook allows stop + BLOCKER)
                  │  ├─ usage ≥ floor, reset ≤ maxAutoWait ─> (stays running) hook blocks each
                  │  │       turn → agent runs watch-quota.sh → freed → resumes in-session
                  │  ├─ usage ≥ floor, reset > maxAutoWait ─> paused ──/goal-loop resume──> running
                  │  └─ ABORT / "stop loop" ─> aborted        (hook allows stop)
```
