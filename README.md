# goal-loop

A hard-verifier + escalation layer for [Claude Code](https://code.claude.com/docs)'s built-in [`/goal`](https://code.claude.com/docs/en/goal).

`/goal` keeps Claude working toward a natural-language condition and judges "done" each turn with a small evaluator — but that evaluator only reads the **conversation**, it never runs your tests. So a goal can be declared done because the transcript *says* "tests pass". `goal-loop` closes that gap: it drives an objective to completion under a **deterministic oracle** (lint / test / typecheck / build + an optional `/review-all` semantic gate), enforced by a **Stop hook** that blocks the session from ending until the oracle is fresh and green — with stuck-detection, a hard budget, and human escalation.

> Claude already shipped the loop. The missing product is the hard verifier. This is that layer.

## What it adds over `/goal`

| `/goal` (reused) | `goal-loop` (this) |
|---|---|
| keep-working loop, evaluator, state, resume | a real **oracle** that runs commands and trusts exit codes |
| — | **enforcement**: a Stop hook blocks "done" until the oracle passes |
| — (loops forever if unsatisfiable) | **stuck-detector**: same failure ×N → stop + escalate |
| — | **budget**: hard `maxIterations` ceiling |
| — | **escalation**: six blocker conditions → `BLOCKER.md` + a focused question |
| — | **usage-aware halt**: stops at the 5h/weekly quota floor and resumes in-session when it frees |
| — | an optional **semantic gate** via `/review-all gate` |

The oracle is **project-agnostic**: gate commands are discovered per repo (`detect-toolchain.sh`) and overridable in `.claude/loop.json`.

## Install

### Plugin (recommended)

```
/plugin marketplace add ncoevoet/claude-loop
/plugin install goal-loop@ncoevoet-loop
```

The skill and the Stop hook (`hooks/hooks.json`) register automatically.

### Manual (`make install`)

```bash
git clone https://github.com/ncoevoet/claude-loop.git
cd claude-loop
make install      # rsync skills/goal-loop → ~/.claude/skills/ + register the Stop hook
```

`make uninstall` removes both. Honors `$CLAUDE_CONFIG_DIR`. The optional `reviewall` stage needs the [`review-all`](https://github.com/ncoevoet/claude-review-all) plugin (≥ 0.4.0, for its headless `gate` mode).

## Use

```
/goal-loop "make guest + logged-in checkout pass their integration tests"
/goal-loop init      # configure the oracle for this repo (.claude/loop.json)
/goal-loop status    # where is the loop now?
/goal-loop abort     # release the loop (kill switch)
```

`/goal-loop "<objective>"`:

1. helps you write a measurable **GOAL contract** (`.claude/loop/GOAL.md`),
2. arms the **Stop hook**,
3. prints a `/goal` one-liner to add Claude's native evaluator + cross-turn persistence,
4. then works autonomously — after each change it runs `scripts/verify.sh`; the Stop hook refuses to let the session stop until the verdict is fresh + `pass:true`, the loop is stuck, or budget is spent.

## How it works

```
/goal-loop "<objective>"  → GOAL.md + STATE.json (armed) + .claude/loop.json
        │
        ▼  each turn
   agent works → runs scripts/verify.sh (+ /review-all gate if mandatory)
        │
   turn ends → Stop hook (goal-loop-gate.sh):
        not armed / aborted / complete ............ allow stop (exit 0)
        verdict missing or stale (sha ≠ worktree) .. BLOCK (exit 2): "run verify.sh"
        verdict fresh, pass:true ................... allow stop → status=complete
        verdict fresh, pass:false ................. BLOCK (exit 2): inject failing gate
        usage ≥ floor (5h / weekly) ............... HALT → in-session watch resumes on reset
        same failure ×N ........................... allow stop → status=blocked + BLOCKER.md
        iteration ≥ cap ........................... allow stop → status=budget_exhausted
```

The hook is **fast** (reads a file + a git sha — never runs the oracle, which could take minutes and exceed a hook timeout) and **fail-open** (any error → allow stop, so a harness bug can never wedge a session). The oracle's `reviewedSha` is a **work-sha** over HEAD + tracked diff + untracked files (excluding run-state), so it tracks uncommitted changes correctly across a loop that makes no commits.

## The oracle

`scripts/verify.sh` runs gates fast→slow, short-circuits, and writes `.claude/loop/verdict.json`. Stages: `lint · test · typecheck · architecture · build · reviewall`. Commands come from `.claude/loop.json` (`oracle.*`) or toolchain discovery. `reviewall` reads the verdict written by the agent's `/review-all gate` run (a shell script can't invoke a skill). See `skills/goal-loop/references/oracle.md`.

```json
{
  "oracle": {
    "test": "vitest related --run",
    "mandatory": ["lint", "test", "reviewall"]
  },
  "reviewall": { "severityFloor": "important" },
  "budget": { "maxIterations": 20, "maxRepeatedFailures": 3 }
}
```

## Usage-aware halt (5h + weekly)

When a loop is active and your **5-hour** or **weekly** usage reaches `budget.usagePauseFloor` (default **96%**), the loop **stops burning quota** — with no `claude -p` and no background daemon:

- **5-hour window (short wait):** the Stop hook blocks each turn and tells the agent to run `watch-quota.sh` — a bounded watch that sleeps one chunk (≤ ~9 min) and re-checks. The agent re-runs it each turn (every run is a tool call = *progress*, so Claude Code's Stop-hook block cap never trips) until usage drops below the floor, then the loop **continues itself in the same session**. Costs a few cheap turns while waiting; the session must stay open.
- **Weekly window (long wait, beyond `budget.maxAutoWait`):** a session can't be held open for days, so the hook sets `status=paused` and notifies; run `/goal-loop resume` once the window resets.

The plugin reads usage from Claude Code's own OAuth usage API + credentials — **no status line required**. It only activates for subscription (Pro/Max) logins; with API-key auth it stays dormant and the loop behaves exactly as before. Tunables: `budget.usagePauseFloor`, `usageWindows`, `maxAutoWait`, `usagePollBase` — see [`config-keys.md`](skills/goal-loop/references/config-keys.md).

## Requirements

- [Claude Code](https://code.claude.com/docs) with built-in `/goal`
- `git`, `bash`, `python3` (defaults on macOS/Linux)
- optional: the `review-all` plugin for the semantic `reviewall` stage

## Layout

```
claude-loop/
├── .claude-plugin/{plugin.json, marketplace.json}
├── hooks/hooks.json                     # plugin Stop-hook manifest
├── skills/goal-loop/
│   ├── SKILL.md                         # front door
│   ├── references/                      # contract · oracle · config-keys · state-file · escalation · init-wizard
│   └── scripts/
│       ├── goal-loop-gate.sh            # Stop hook
│       ├── gate-decide.py               # hook decision engine (fail-open, bounded)
│       ├── verify.sh                    # the oracle
│       ├── loop-lib.sh                  # shared helpers (work-sha, config)
│       ├── usage-lib.sh                 # self-contained usage fetch + cache (5h/weekly)
│       ├── watch-quota.sh               # agent-run bounded usage watch (in-session wait)
│       ├── detect-toolchain.sh          # project-agnostic command discovery
│       └── install-hook.sh              # settings.json hook registration (manual path)
├── tests/                               # python unittests + shell integration (no API key)
└── .github/workflows/ci.yml             # shellcheck + tests
```

## Development

```bash
make test        # tests/run.sh — shellcheck (if present) + python unittests, no API key
```

## License

MIT — see [LICENSE](LICENSE).
