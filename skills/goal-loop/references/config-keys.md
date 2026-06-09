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
  "budget": { "maxIterations": 20, "maxRepeatedFailures": 3 }
}
```

(Here `build` is configured but left out of `mandatory` to keep iterations fast; `reviewall`'s Phase 1.5 runtime probe covers UI runtime. Add `build` to `mandatory` for release-gating runs.)
