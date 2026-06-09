---
name: goal-loop
description: "Drive an objective to DONE under a hard, deterministic oracle. Wraps Claude Code's /goal with a real verifier (lint/test/typecheck/build + /review-all gate) enforced by a Stop hook, plus stuck-detection and human escalation. Use for: 'loop until done', 'autonomous run that stops only when verified or blocked', /goal-loop, drive a task to completion without stopping after every subtask."
argument-hint: "\"<objective>\" | init | status | abort"
allowed-tools: Bash(bash:*) Bash(git:*) Bash(python3:*) Bash(mkdir:*) Bash(ls:*) Bash(cat:*) Read Glob Grep Write Edit AskUserQuestion
---

# goal-loop — a hard-verifier + escalation layer for `/goal`

You orchestrate an autonomous loop that keeps working toward an objective and **stops only** when a deterministic oracle confirms it is done, the loop is genuinely stuck, or budget is exhausted. You are the front door; the real enforcement is a **Stop hook** (`goal-loop-gate.sh`, installed) that blocks the session from ending until the oracle passes.

**Surface: Claude Code only** — uses git, bash, python3, and a Stop hook. Not portable to claude.ai/API.

## Division of labour (do NOT rebuild what `/goal` already does)

| Concern | Owner |
|---|---|
| keep-working loop, completion evaluator, cross-turn state, resume | **Claude Code `/goal`** (optional, recommended) |
| **deterministic oracle** — actually runs lint/test/typecheck/build, trusts exit codes | **`scripts/verify.sh`** (you run it) |
| **semantic gate** — confirmed-critical findings block | **`/review-all gate`** (if installed + configured) |
| **enforcement** — block stop until the oracle is fresh + pass | **Stop hook** (`goal-loop-gate.sh`) |
| **stuck-detection + budget + escalation** | this skill + the Stop hook |

`/goal`'s own evaluator only reads the transcript — it cannot run your tests. This layer makes the stop-decision trustworthy by running the real commands and gating on their exit codes.

## Step 0 — Resolve the script directory

The scripts live next to this skill. Resolve their absolute path once and reuse it:

```bash
LOOP="$HOME/.claude/skills/goal-loop/scripts"
[ -d "$LOOP" ] || LOOP="${CLAUDE_PLUGIN_ROOT:-}/skills/goal-loop/scripts"
echo "$LOOP"   # verify it exists
```

All later commands use `"$LOOP/verify.sh"` etc. The loop's run-state lives in the **target project** at `.claude/loop/` (resolved by the scripts from the current git root).

## Step 1 — Route on `$ARGUMENTS`

- `init` → load **`references/init-wizard.md`** and write `.claude/loop.json`. Exit.
- `status` → read `.claude/loop/STATE.json` + `.claude/loop/verdict.json` and report status, iteration/cap, last failing gate, and the GOAL outcome. Exit.
- `abort` → `touch .claude/loop/ABORT`; tell the user the loop is released (the Stop hook will allow stopping on the next turn) and to run `/goal clear` if they started a native goal. Exit.
- Anything else → treat as the **objective**; continue below.

## Step 2 — Author the GOAL contract

Read **`references/contract.md`** and fill it in WITH the user's objective. Do not accept a vague goal — convert it into a measurable contract. Write it to `.claude/loop/GOAL.md`:

- **Outcome** — what must be true when done (specific, checkable).
- **Verify** — which oracle stages are mandatory (must match `.claude/loop.json` `oracle.mandatory`).
- **Scope** — directories in bounds.
- **Constraints** — what must not regress.
- **Autonomy rule** — continue through safe intermediate steps; do NOT ask after each subtask.
- **Stuck rule** — the escalation conditions in `references/escalation.md`.
- **Budget** — `maxIterations`, `maxRepeatedFailures`.

If `.claude/loop.json` does not exist, run the **init wizard** first (Step 1 `init`) so the oracle commands are configured — otherwise the oracle falls back to toolchain discovery (lint/test only).

## Step 3 — Arm the loop

Create the run-state and ensure it is gitignored in the target project:

```bash
mkdir -p .claude/loop
grep -qxF '.claude/loop/' .gitignore 2>/dev/null || echo '.claude/loop/' >> .gitignore
```

Seed `STATE.json` (read `references/state-file.md` for the schema). Pull `maxIterations`/`maxRepeatedFailures` from `.claude/loop.json` `budget` (defaults 20 / 3):

```bash
python3 - "$PWD/.claude/loop/STATE.json" <<'PY'
import json, sys
json.dump({
    "status": "running", "iteration": 0,
    "maxIterations": 20, "maxRepeatedFailures": 3,
    "sameFailureCount": 0, "lastFailureSig": None,
}, open(sys.argv[1], "w"), indent=2)
PY
```

Initialise `WORKLOG.md` with the objective and a timestamp. The moment `STATE.status=running`, the Stop hook is **armed** — the session will not end until the oracle passes or the loop blocks.

## Step 4 — Engage `/goal` (recommended) and announce the protocol

Print the native-goal one-liner for the user to run (the skill cannot invoke `/goal` itself — it is a built-in command). Phrase the condition to reference the oracle so `/goal`'s evaluator and the Stop hook agree:

> Run this to add Claude's native goal evaluator + cross-turn persistence:
> `/goal The objective in .claude/loop/GOAL.md is complete AND scripts/verify.sh reports pass:true on the current changes (.claude/loop/verdict.json fresh, pass true).`

Then state the **loop protocol** you (the agent) will follow, and begin working immediately:

1. Do the next smallest useful unit of work toward the Outcome. Append a line to `.claude/loop/WORKLOG.md` (what changed, what remains).
2. Run the oracle: `bash "$LOOP/verify.sh"`. If `reviewall` is a mandatory stage, also run `/review-all gate --severity <floor>` first (it writes the gate verdict the oracle reads).
3. If the oracle FAILS, read `failingGate` + evidence from `.claude/loop/verdict.json`, fix it, and re-run. Do **not** stop — the Stop hook will block you anyway and tell you what failed.
4. Continue autonomously through safe steps. Only stop to ask when a real blocker hits (Step 5).
5. When the oracle passes, the Stop hook sets `status=complete` and allows the session to end. Give a final summary (Outcome met, files changed, gates passed).

## Step 5 — Escalation

When you hit a genuine blocker (see `references/escalation.md` for the exact conditions — missing secret, destructive/irreversible step, ambiguous requirement, same oracle failure 3×, budget, security boundary), the Stop hook writes `.claude/loop/BLOCKER.md` and allows the stop. At that point present the blocker to the user via `AskUserQuestion`: the blocker, the evidence, what you tried, and the smallest decision you need. Never silently give up; never push or do anything destructive to "make the gate pass".

## Requirements

`git`, `bash`, `python3` (defaults on macOS/Linux). The `reviewall` stage additionally needs the `review-all` plugin installed; if absent, drop `reviewall` from `oracle.mandatory` (the oracle skips it and says so).
