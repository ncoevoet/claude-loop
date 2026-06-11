# Escalation — when the loop stops and asks

The whole point of the loop is to **not** interrupt for low-value confirmations. Continue through safe intermediate steps. Stop and ask the human ONLY when defensible progress is no longer possible.

> **Not a blocker:** an API-usage halt (5-hour / weekly quota at `budget.usagePauseFloor`) is handled automatically — a short wait resumes itself in-session via `watch-quota.sh`; only a long (multi-day weekly) wait sets `status=paused` for a `/goal-loop resume`. It is none of the conditions below. See `config-keys.md`.

## The six blocker conditions

1. **Missing access** — a required secret, credential, account, or external service is unavailable.
2. **Destructive / irreversible** — the next step would be irreversible: any `git push`, a schema/data migration, a public-API change, deleting data, anything outside the contract's Scope that can't be undone.
3. **Ambiguous requirement** — two incompatible valid behaviours exist and choosing wrong wastes the work; the contract doesn't decide it.
4. **Same failure ×N** — the oracle fails with the same signature `maxRepeatedFailures` times after materially different attempts (the Stop hook detects this and sets `status=blocked`).
5. **Budget** — `maxIterations` reached (`status=budget_exhausted`).
6. **Security / privacy / compliance** boundary implicated.

For 4 and 5 the Stop hook already wrote `.claude/loop/BLOCKER.md` and released the gate. For 1–3 and 6, you (the agent) recognise the condition yourself, write the blocker, set `status=blocked`, and stop.

## Never do, to "make the gate pass"

- Never `git push`, commit past a commit gate, weaken a test, delete a failing spec, skip/disable a gate, or edit the oracle config to drop a failing stage. The gate is the product — defeating it defeats the loop. If a gate is genuinely wrong, that is an **ambiguous-requirement** escalation, not a thing to silently route around.

## How to escalate

Present via `AskUserQuestion` (not a wall of text). Include exactly:

- **Blocker** — one line, which of the six.
- **Evidence** — the failing gate + the relevant output (from `verdict.json` / `BLOCKER.md`).
- **Attempts** — what you already tried (from `WORKLOG.md`).
- **Recommended next decision** — the single smallest choice you need from the human (offer concrete options).

Then stop. Do not loop on the question.
