# The oracle — `scripts/verify.sh`

The deterministic verifier. Runs the project's gates fast→slow, **short-circuits on the first failure**, and writes `.claude/loop/verdict.json`:

```json
{ "tool": "goal-loop", "mode": "verify", "generated_at": "<iso>",
  "reviewedSha": "<work-sha>", "pass": false, "failingGate": "test",
  "partial": false, "stages": {"lint": "PASS", "test": "FAIL(rc=1)"},
  "evidence": "<tail of the failing command's output>" }
```

Exit 0 = pass, 1 = a gate failed, 2 = bad setup. The **agent** runs it (in-session, no hook timeout, visible to `/goal`'s evaluator); the **Stop hook** only reads the verdict it produces.

## Stages

| # | Stage | Command source | Notes |
|---|---|---|---|
| 1 | lint | `oracle.lint` → discovery `lint` | fast |
| 2 | test | `oracle.test` → discovery `test` | scope to changed files where the command supports it |
| 3 | typecheck | `oracle.typecheck` → discovery `typecheck` | |
| 4 | architecture | `oracle.architecture` (config-only) | e.g. dependency-cruiser; no discovery default |
| 5 | build | `oracle.build` → discovery `build` | heaviest shell stage (600s cap) |
| 6 | reviewall | reads `.claude/review-all/gate-verdict.json` | semantic gate; see below |

Commands come from `.claude/loop.json` `oracle.*`; where unset, from `scripts/detect-toolchain.sh` (ecosystem → `{lint,test,typecheck,build}`). `architecture` has no discovery default — it is project-specific, config-only.

**Mandatory stages** = `oracle.mandatory` (a list). If absent, the default is whichever of `lint`/`test` was discovered — conservative and fast. Heavier gates (`typecheck`, `architecture`, `build`, `reviewall`) are opt-in via `oracle.mandatory`.

## The `reviewall` stage (semantic gate)

`verify.sh` is a shell script and cannot invoke the `/review-all` skill. So the **agent** runs `/review-all gate --severity <floor>` (which writes `.claude/review-all/gate-verdict.json`), and verify.sh's `reviewall` stage **reads** that artifact:

- missing → fail, evidence = "Run: /review-all gate --severity <floor>".
- `reviewedSha` ≠ current work-sha → stale → fail, "Re-run /review-all gate".
- `pass:false` → fail, evidence = the blocking findings.
- `pass:true` and fresh → pass.

review-all's own Phase 1.5 runtime probe covers UI/runtime regressions (dead routes, NG0100-class errors, visual diffs), so the oracle needs no separate browser stage. Requires the `review-all` plugin; if it is not installed, drop `reviewall` from `oracle.mandatory`.

## Work-sha & caching

`reviewedSha` is a **work-sha**: a fingerprint of HEAD + the tracked diff + untracked (non-ignored) file contents, EXCLUDING `.claude/loop/` and `.claude/review-all/` (run-state must not change the sha). A loop usually has uncommitted changes and no new commits between iterations, so a commit sha is not enough.

Re-running verify.sh on an unchanged tree **reuses** the cached verdict (near-zero cost). `--force` recomputes. `--print-sha` prints the current work-sha (used to check gate-verdict freshness).

## Timeouts

Per stage (seconds): lint 120, test 300, typecheck 180, architecture 180, build 600. A timeout (rc 124) is recorded as a failing gate with a `TIMEOUT after Ns` evidence prefix. Missing `timeout(1)` (bare macOS) → stages run uncapped.
