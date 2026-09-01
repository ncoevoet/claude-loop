# Config — `.claude/loop.json`

Per-project oracle + budget config. All keys optional; defaults below apply when absent. No config at all → the oracle discovers `lint`/`test` from the toolchain and runs just those. Each default carries a **Why** (no voodoo constants).

## Keys

| Key | Type | Default | Meaning | Why this default |
|---|---|---|---|---|
| `oracle.lint` | string | discovery | Lint command. Overrides `detect-toolchain.sh`. | Discovery covers npm/cargo/go/etc.; override when the repo's real gate differs (e.g. a scoped eslint). |
| `oracle.test` | string | discovery | Test command. | Same. Prefer a CHANGED-FILES-scoped form (e.g. `vitest related --run`) so each iteration is fast. |
| `oracle.typecheck` | string | discovery | Typecheck command. | Off by default unless in `mandatory`; many repos fold this into build. |
| `oracle.architecture` | string | _(none)_ | Architecture/graph gate (e.g. `depcruise …`). Config-only — no discovery default. | Project-specific; there is no safe generic default, so it never runs unless you set it. |
| `oracle.build` | string | discovery | Build command. The heaviest shell stage. | Opt-in via `mandatory` — building every iteration is slow; many loops gate on tests + review only. |
| `oracle.mandatory` | string[] | `["lint","test"]` (of those discovered) | Stages that must pass to declare done, in order. | Conservative + fast default. Add `typecheck`/`architecture`/`build`/`reviewall` per project. |
| `reviewall.severityFloor` | string | `"important"` | Floor passed to `/review-all gate`. `important` → 🔴+🟠; `critical` → 🔴 only. | Block on real bugs (🔴) AND likely bugs / missing error handling (🟠) — the tiers worth stopping a loop for. Debt/style is recorded by review-all but never blocks. Drop to `critical` to gate on 🔴 only. |
| `budget.maxIterations` | number | `20` | Hard ceiling on oracle-fail / nudge cycles before the loop stops as `budget_exhausted`. | High enough for real multi-step work, low enough to bound a runaway. The Stop hook is bounded by this. |
| `budget.maxRepeatedFailures` | number | `3` | Same failing-gate signature this many times in a row → stop as `blocked` and escalate. | Two repeats can be noise; three signals the loop is not converging — escalate rather than burn budget. |
| `budget.maxTurns` | number | `3000` | Hard ceiling on assistant turns in the CURRENT session (its transcript + its subagent transcripts) before the loop stops as `budget_exhausted` and escalates. `0` disables. Env fallback: `LOOP_MAX_TURNS`. | `maxIterations` bounds how many times the Stop hook BLOCKS — not the work each block buys. The two measured runaways spent 4 823 and 4 293 turns inside their iteration cap; 3000 cuts both off with room to spare while clearing an ordinary multi-hour run. |
| `budget.maxOutputTokens` | number | `1000000` | Same ceiling on cumulative `output_tokens` for the session (subagents included). `0` disables. Env fallback: `LOOP_MAX_OUTPUT_TOKENS`. | Turns and tokens fail differently: a handful of huge turns costs as much as hundreds of small ones, so bound both. Sized just above the worst measured run (996 K) so a token-heavy loop trips this before it can reach 3000 turns. |
| `budget.usagePauseFloor` | number | `96` | Halt the loop when a tracked usage window (5-hour / weekly) reaches this % utilization. `0` disables. | Stop just shy of the 100% wall so an in-flight turn can't blow the cap; lower it for a bigger safety margin (the in-session wait itself costs a few cheap turns). |
| `budget.usageWindows` | string[] | `["five_hour","seven_day"]` | Which usage windows arm the halt. | Both the rolling 5-hour and the weekly quota matter; set `["five_hour"]` to ignore the weekly window. |
| `budget.maxAutoWait` | number | `21600` | Max seconds the in-session watch waits for a window to reset before falling back to a manual pause. | 6 h covers a full 5-hour window; a multi-day weekly wait can't hold a session open, so it becomes `paused` + a `/goal-loop resume` prompt instead. |
| `budget.usagePollBase` | number | `540` | Base sleep (sec) per `watch-quota.sh` call; the adaptive ladder scales around it, hard-capped at 540. | One chunk stays under the Bash-tool 10-minute ceiling; near a reset it shortens to catch the flip. Fewer/longer chunks = fewer cheap wait-turns. |
| `budget.usageCacheDir` | string | `/tmp/claude` | Dir for the plugin's own usage cache (env `LOOP_USAGE_CACHE_DIR`). | A present statusline cache is auto-detected as a fast-path; otherwise the plugin fetches the usage API itself. |

## Run ceiling (`maxTurns` / `maxOutputTokens`)

`maxIterations` counts Stop-hook **blocks**, and one block can buy hundreds of turns before the
agent next tries to stop — so on its own it does not bound cost. `maxTurns` / `maxOutputTokens`
close that gap: whichever is reached first, the hook **allows the stop** (it does not force another
iteration), sets `status=budget_exhausted` with a `runBudget` record, writes `BLOCKER.md`, and
emits a `systemMessage` to the user:

```
goal-loop: budget exceeded (3000 turns / 612480 output tokens; caps 3000/1000000) — escalating
to human; oracle state: fresh FAIL at gate `test` — goal UNVERIFIED. The objective is NOT done:
stop here and report it as unverified.
```

It never passes the oracle: the message always states the goal is unverified. A **fresh passing**
verdict still completes normally — the ceiling only intercepts keep-working paths, and it is
checked *before* the usage halt (over budget → escalate, don't wait for quota to keep burning).

**How the counts are computed.** The Stop hook receives `transcript_path` on stdin and hands it to
`gate-decide.py`, which reads that `.jsonl` plus `<session-id>/subagents/agent-*.jsonl` (Claude Code
stores Task-tool runs in separate files, and that is where most of a marathon's cost lives). A turn
is one `type:"assistant"` record; both counts **dedupe by `message.id`**, because Claude Code writes
one line per content block and each line repeats the *same* `usage` object — summing naively
inflated the same session 4.5× (10 602 turns / 4.47 M tokens raw vs 4 823 / 996 K deduped).
Everything is fail-open: no transcript, an unparseable line, or a changed format simply skips the
check. The transcript format is internal to Claude Code and may change between versions.

Scope is **per session**: a `/goal-loop resume` in a new session starts from zero. Cost: 0.2 s to
read a 52 MB / 40-file transcript, against the hook's 30 s timeout.

> **Calibration.** The two runaways that motivated this feature measured 4 823 turns / 996 K output
> tokens and 4 293 turns / 769 K (deduped, ~12 h each). The defaults are set to **bind on both**:
> at 3000 turns each would have been cut off roughly a third of the way in. The turn ceiling is
> what stops those two — neither reached 1 M output tokens (996 K came within 0.4 %), so
> `maxOutputTokens` is the secondary guard that catches a token-heavy run before it accumulates
> 3000 turns. Raise both if your loops legitimately run that long: a genuine 12-hour refactor will
> hit these ceilings and escalate as unverified, which is a false stop, not a runaway. Prefer
> raising them in `.claude/loop.json` per project over disabling them with `0`.

## Usage-aware halt (5-hour + weekly quota)

When a loop is active and a tracked window reaches `usagePauseFloor`, the loop **stops burning quota** instead of pushing more work:

- **Short wait** (window resets within `maxAutoWait`, e.g. the 5-hour window): the Stop hook blocks and tells the agent to run `watch-quota.sh` — a bounded watch that sleeps one chunk and re-checks. The agent re-runs it each turn (each run counts as *progress*, so the Stop-hook block cap never trips) until usage drops below the floor, then the loop **continues itself in the same session** — no `claude -p`, no extra process. It costs a few cheap turns while waiting, and the session must stay open.
- **Long wait** (reset beyond `maxAutoWait`, e.g. the multi-day weekly window): the hook sets `status=paused` and allows the stop (a session can't be held open for days). Run `/goal-loop resume` once the window resets.

The plugin reads usage from Claude Code's own OAuth usage endpoint + credentials (`~/.claude/.credentials.json`) — **it does not require a status line**. It only activates for subscription (Pro/Max) logins; API-key-only users have no such quota, so the feature stays dormant and every read fails open (the loop runs exactly as it would without it). `touch .claude/loop/ABORT` cancels.

## Example — Angular monorepo

```json
{
  "oracle": {
    "lint": "eslint",
    "test": "vitest related --run --config vitest.config.ts --pool=forks --maxWorkers=15",
    "architecture": "npm run test:architecture",
    "build": "npm run build",
    "mandatory": ["lint", "test", "architecture", "reviewall"]
  },
  "reviewall": { "severityFloor": "important" },
  "budget": { "maxIterations": 20, "maxRepeatedFailures": 3,
              "maxTurns": 3000, "maxOutputTokens": 1000000 }
}
```

(Here `build` is configured but left out of `mandatory` to keep iterations fast; `reviewall`'s Phase 1.5 runtime probe covers UI runtime. Add `build` to `mandatory` for release-gating runs.)
