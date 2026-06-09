# GOAL contract — `.claude/loop/GOAL.md`

The contract turns a vague objective into something the **oracle can prove done**. A weak contract is the #1 cause of a loop that runs forever (unsatisfiable) or stops early (no real finish line). Fill every field with the user before arming.

## Template

```markdown
# GOAL

## Outcome
<What must be TRUE when done — specific and checkable. Not "fix the checkout flow"
 but "guest, logged-in, discount, and retry checkout each pass their existing
 integration tests, and `npm test -- checkout` is green.">

## Verify
Mandatory oracle stages (must match .claude/loop.json oracle.mandatory):
<e.g. lint, test, reviewall>

## Scope
In-bounds directories/files: <e.g. src/checkout, src/payments, test/checkout>
Anything outside scope needs a justification appended to WORKLOG.md.

## Constraints (must not regress)
<e.g. do not change public API, DB schema, auth behaviour, prod config.>

## Autonomy rule
Continue through safe intermediate steps. Do NOT ask for confirmation after each
subtask. Make safe engineering assumptions and record them in WORKLOG.md.

## Stuck rule
Stop and escalate ONLY under the conditions in references/escalation.md.

## Budget
maxIterations: <N, default 20>   maxRepeatedFailures: <N, default 3>

## Artifacts
WORKLOG.md (append-only progress), verdict.json (oracle), and commits if asked.
```

## Authoring rules

- **Outcome must be verifiable by the oracle.** If you cannot point to a command (or `/review-all gate`) that proves it, the contract is too vague — push back and refine with the user.
- **Match Verify to the config.** `oracle.mandatory` in `.claude/loop.json` is the source of truth for which gates run; the contract's Verify section should name the same stages so the human and the machine agree.
- **Keep Scope tight.** A narrow scope is what lets the loop run autonomously without the agent wandering — the Stop hook does not police scope, the contract does.
- **The Outcome is also the `/goal` condition.** When you print the `/goal` one-liner (SKILL Step 4), summarise the Outcome into it so `/goal`'s evaluator and the oracle converge on the same definition of done.
