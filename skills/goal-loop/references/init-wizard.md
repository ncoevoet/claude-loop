# Init wizard — write `.claude/loop.json`

Run on `/goal-loop init`, or automatically (with the user's confirmation) the first time a loop is armed in a project with no config. Goal: produce a correct, minimal `.claude/loop.json` so the oracle runs the RIGHT commands for this repo.

## Flow

1. **Discover.** Run `bash "$LOOP/detect-toolchain.sh" .` and show the user the detected `{ecosystem, framework, lint, test, typecheck, build}`. These become the starting defaults.

2. **Confirm the oracle commands.** For each of lint / test / typecheck / build, confirm or override. Strongly prefer a **changed-files-scoped** test command (e.g. `vitest related --run`, `go test ./<pkg>`, `pytest <dir>`) so each iteration is fast. Ask whether the repo has an **architecture/graph gate** (e.g. dependency-cruiser, import-linter) — there is no discovery default for it.

3. **Pick the mandatory set** (`oracle.mandatory`). Default `["lint","test"]`. Offer adding `typecheck`, `architecture`, `build`, and `reviewall`. Explain the trade-off: more gates = higher confidence, slower iterations. `build` is usually left out of the hot loop and added only for release-gating.

4. **review-all gate.** If the `review-all` plugin is installed and the user wants the semantic gate, add `reviewall` to `mandatory` and set `reviewall.severityFloor` (default `important` = 🔴+🟠; `critical` to gate on 🔴 only). If review-all is not installed, say so and leave `reviewall` out.

5. **Budget.** Confirm `budget.maxIterations` (default 20) and `budget.maxRepeatedFailures` (default 3).

6. **Write** `.claude/loop.json` (see `config-keys.md` for the schema) and show the final file. Remind the user `.claude/loop/` (run-state) is gitignored but `.claude/loop.json` (config) should be committed.

## Minimal output

```json
{
  "oracle": { "test": "<scoped test cmd>", "mandatory": ["lint", "test"] },
  "budget": { "maxIterations": 20, "maxRepeatedFailures": 3 }
}
```

Keep it minimal — every key is optional and discovery fills the rest. Only write what differs from discovery or what the user explicitly chose.
