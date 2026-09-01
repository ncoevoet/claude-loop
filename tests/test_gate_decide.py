"""Unit tests for the Stop-hook decision engine (scripts/gate-decide.py).

Pure logic: WORK_SHA is passed as an arg, so no git is needed. Each test crafts
STATE.json + verdict.json in a temp dir and asserts the exit code + the STATE
mutation. Exit 0 = allow stop, 2 = block (keep working)."""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "skills", "goal-loop", "scripts", "gate-decide.py")
SHA = "abc123"


class TestGateDecide(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.state = os.path.join(self.d, "STATE.json")
        self.verdict = os.path.join(self.d, "verdict.json")

    def write(self, path, obj):
        with open(path, "w") as fh:
            json.dump(obj, fh)

    def write_cache(self, **wins):
        """Craft a usage cache file. Kwargs: five, seven (utilizations);
        five_reset, seven_reset (ISO strings)."""
        path = os.path.join(self.d, "usage.json")
        data = {}
        if "five" in wins:
            data["five_hour"] = {"utilization": wins["five"], "resets_at": wins.get("five_reset")}
        if "seven" in wins:
            data["seven_day"] = {"utilization": wins["seven"], "resets_at": wins.get("seven_reset")}
        with open(path, "w") as fh:
            json.dump({"data": data}, fh)
        return path

    def write_transcript(self, msgs, path=None):
        """Craft a session transcript. `msgs` is a list of (message_id, output_tokens)
        pairs — repeat an id to emulate Claude Code splitting one assistant message
        across several content-block lines (each repeating the SAME usage object)."""
        path = path or os.path.join(self.d, "session.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(json.dumps({"type": "user", "message": {"role": "user"}}) + "\n")
            for mid, out in msgs:
                fh.write(json.dumps({
                    "type": "assistant",
                    "message": {"id": mid, "role": "assistant",
                                "usage": {"output_tokens": out}}}) + "\n")
        return path

    def decide(self, work_sha=SHA, usage_cache=None, floor="96", windows=None,
               transcript=None, max_turns=None, max_output_tokens=None):
        # Hermetic: strip any inherited LOOP_* so the usage guard and the run budget
        # are off unless a test opts in.
        env = {k: v for k, v in os.environ.items() if not k.startswith("LOOP_")}
        if usage_cache is not None:
            env["LOOP_USAGE_CACHE"] = usage_cache
            env["LOOP_USAGE_FLOOR"] = str(floor)
            if windows:
                env["LOOP_USAGE_WINDOWS"] = windows
        if transcript is not None:
            # A cap left as None is NOT exported, so the engine's own default applies
            # (that is what test_run_budget_ships_calibrated_defaults pins).
            env["LOOP_TRANSCRIPT"] = transcript
            if max_turns is not None:
                env["LOOP_MAX_TURNS"] = str(max_turns)
            if max_output_tokens is not None:
                env["LOOP_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
        return subprocess.run(
            [sys.executable, SCRIPT, self.state, self.verdict, work_sha, "/x/verify.sh"],
            capture_output=True, text=True, env=env)

    def state_now(self):
        with open(self.state) as fh:
            return json.load(fh)

    def test_not_running_allows(self):
        self.write(self.state, {"status": "complete"})
        self.assertEqual(self.decide().returncode, 0)

    def test_no_state_allows_fail_open(self):
        # no STATE.json at all
        self.assertEqual(self.decide().returncode, 0)

    def test_malformed_state_allows_fail_open(self):
        with open(self.state, "w") as fh:
            fh.write("not json")
        self.assertEqual(self.decide().returncode, 0)

    def test_missing_verdict_blocks_and_bumps(self):
        self.write(self.state, {"status": "running", "iteration": 0, "maxIterations": 20})
        p = self.decide()
        self.assertEqual(p.returncode, 2)
        self.assertIn("oracle has not been run", p.stderr)
        self.assertEqual(self.state_now()["iteration"], 1)

    def test_stale_verdict_blocks(self):
        self.write(self.state, {"status": "running", "iteration": 1, "maxIterations": 20})
        self.write(self.verdict, {"reviewedSha": "OTHER", "pass": True})
        self.assertEqual(self.decide().returncode, 2)
        self.assertEqual(self.state_now()["iteration"], 2)

    def test_fresh_pass_allows_and_completes(self):
        self.write(self.state, {"status": "running", "iteration": 2, "maxIterations": 20})
        self.write(self.verdict, {"reviewedSha": SHA, "pass": True})
        self.assertEqual(self.decide().returncode, 0)
        self.assertEqual(self.state_now()["status"], "complete")

    def test_fresh_fail_blocks_first_time(self):
        self.write(self.state, {"status": "running", "iteration": 2, "maxIterations": 20,
                                "maxRepeatedFailures": 3})
        self.write(self.verdict, {"reviewedSha": SHA, "pass": False,
                                  "failingGate": "test", "evidence": "boom"})
        p = self.decide()
        self.assertEqual(p.returncode, 2)
        self.assertIn("FAILED at gate `test`", p.stderr)
        s = self.state_now()
        self.assertEqual(s["sameFailureCount"], 1)
        self.assertEqual(s["iteration"], 3)
        self.assertTrue(s["lastFailureSig"].startswith("test:"))

    def test_same_failure_thrice_blocks_status(self):
        # prior state already saw the same signature twice
        sig = "test:" + __import__("hashlib").sha256(b"boom").hexdigest()[:16]
        self.write(self.state, {"status": "running", "iteration": 5, "maxIterations": 20,
                                "maxRepeatedFailures": 3, "sameFailureCount": 2,
                                "lastFailureSig": sig})
        self.write(self.verdict, {"reviewedSha": SHA, "pass": False,
                                  "failingGate": "test", "evidence": "boom"})
        p = self.decide()
        self.assertEqual(p.returncode, 0)  # allow stop → escalate
        self.assertEqual(self.state_now()["status"], "blocked")
        self.assertTrue(os.path.exists(os.path.join(self.d, "BLOCKER.md")))

    def test_budget_exhausted_allows(self):
        self.write(self.state, {"status": "running", "iteration": 20, "maxIterations": 20})
        self.write(self.verdict, {"reviewedSha": SHA, "pass": False,
                                  "failingGate": "test", "evidence": "x"})
        p = self.decide()
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.state_now()["status"], "budget_exhausted")
        self.assertTrue(os.path.exists(os.path.join(self.d, "BLOCKER.md")))

    def test_different_failure_resets_counter(self):
        sig = "test:" + __import__("hashlib").sha256(b"old").hexdigest()[:16]
        self.write(self.state, {"status": "running", "iteration": 1, "maxIterations": 20,
                                "maxRepeatedFailures": 3, "sameFailureCount": 2,
                                "lastFailureSig": sig})
        self.write(self.verdict, {"reviewedSha": SHA, "pass": False,
                                  "failingGate": "lint", "evidence": "new"})
        self.assertEqual(self.decide().returncode, 2)
        self.assertEqual(self.state_now()["sameFailureCount"], 1)  # reset, not 3

    # --- usage-aware halt --------------------------------------------------

    def iso_in(self, secs):
        dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=secs)
        return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    def test_usage_hold_in_session_on_missing_verdict(self):
        # Would block (no verdict) but 5h is over the floor and resets soon →
        # HALT IN-SESSION: block with the watch instruction, stay running.
        self.write(self.state, {"status": "running", "iteration": 2, "maxIterations": 20})
        cache = self.write_cache(five=97, five_reset=self.iso_in(3600), seven=10)
        p = self.decide(usage_cache=cache)
        self.assertEqual(p.returncode, 2)             # block (keep session alive)
        self.assertIn("watch-quota.sh", p.stderr)
        s = self.state_now()
        self.assertEqual(s["status"], "running")      # NOT paused
        self.assertEqual(s["usageHold"]["window"], "five_hour")
        self.assertEqual(s["iteration"], 2)           # NOT bumped while waiting

    def test_usage_hold_in_session_on_fresh_fail(self):
        self.write(self.state, {"status": "running", "iteration": 3, "maxIterations": 20,
                                "maxRepeatedFailures": 3})
        self.write(self.verdict, {"reviewedSha": SHA, "pass": False,
                                  "failingGate": "test", "evidence": "boom"})
        cache = self.write_cache(five=10, seven=96, seven_reset=self.iso_in(1800))
        p = self.decide(usage_cache=cache)
        self.assertEqual(p.returncode, 2)
        s = self.state_now()
        self.assertEqual(s["status"], "running")
        self.assertEqual(s["usageHold"]["window"], "seven_day")
        self.assertEqual(s["iteration"], 3)           # not bumped
        self.assertEqual(s["sameFailureCount"], 1)    # failure still recorded

    def test_usage_pause_when_reset_too_far(self):
        # Reset beyond maxAutoWait (weekly window) → can't hold a session that
        # long → manual pause (allow stop).
        self.write(self.state, {"status": "running", "iteration": 2, "maxIterations": 20})
        cache = self.write_cache(five=97, five_reset=self.iso_in(5 * 86400), seven=10)
        p = self.decide(usage_cache=cache)
        self.assertEqual(p.returncode, 0)
        s = self.state_now()
        self.assertEqual(s["status"], "paused")
        self.assertEqual(s["iteration"], 2)
        self.assertTrue(os.path.exists(os.path.join(self.d, "PAUSE.md")))

    def test_usage_pause_when_reset_unknown(self):
        # Over floor but no reset timestamp → can't bound the wait → manual pause.
        self.write(self.state, {"status": "running", "iteration": 2, "maxIterations": 20})
        cache = self.write_cache(five=98, seven=10)   # no resets_at
        p = self.decide(usage_cache=cache)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.state_now()["status"], "paused")

    def test_usage_under_floor_no_halt(self):
        self.write(self.state, {"status": "running", "iteration": 0, "maxIterations": 20})
        cache = self.write_cache(five=50, seven=20)
        p = self.decide(usage_cache=cache)            # missing verdict → block as usual
        self.assertEqual(p.returncode, 2)
        self.assertIn("oracle has not been run", p.stderr)
        self.assertEqual(self.state_now()["iteration"], 1)

    def test_usage_under_floor_clears_stale_hold(self):
        self.write(self.state, {"status": "running", "iteration": 1, "maxIterations": 20,
                                "usageHold": {"window": "five_hour"}})
        cache = self.write_cache(five=10, seven=10)
        self.decide(usage_cache=cache)
        self.assertNotIn("usageHold", self.state_now())

    def test_usage_missing_cache_fail_open(self):
        self.write(self.state, {"status": "running", "iteration": 0, "maxIterations": 20})
        p = self.decide(usage_cache=os.path.join(self.d, "nope.json"))
        self.assertEqual(p.returncode, 2)             # no data → behave as before
        self.assertEqual(self.state_now()["iteration"], 1)

    def test_usage_pass_completes_despite_high_usage(self):
        # A fresh pass must complete, never halt — the usage guard only intercepts
        # keep-working decisions.
        self.write(self.state, {"status": "running", "iteration": 2, "maxIterations": 20})
        self.write(self.verdict, {"reviewedSha": SHA, "pass": True})
        cache = self.write_cache(five=99, five_reset=self.iso_in(3600), seven=99)
        p = self.decide(usage_cache=cache)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.state_now()["status"], "complete")

    def test_usage_wait_does_not_bleed_stuck_counter(self):
        # A multi-turn in-session wait must NOT advance sameFailureCount: the agent
        # is only running watch-quota.sh (no fix attempt), so re-counting it would
        # false-escalate a long wait as "stuck". The counter stays frozen across
        # every wait turn while the same failure is held.
        self.write(self.state, {"status": "running", "iteration": 3, "maxIterations": 20,
                                "maxRepeatedFailures": 3})
        self.write(self.verdict, {"reviewedSha": SHA, "pass": False,
                                  "failingGate": "test", "evidence": "boom"})
        cache = self.write_cache(five=99, five_reset=self.iso_in(3600), seven=10)
        for _ in range(5):
            p = self.decide(usage_cache=cache)
            self.assertEqual(p.returncode, 2)            # still holding, never escalates
            s = self.state_now()
            self.assertEqual(s["status"], "running")
            self.assertEqual(s["sameFailureCount"], 1)   # frozen, not climbing to 3
        # Quota frees → hold clears; the still-failing oracle now counts as a real
        # post-wait attempt (counter resumes, no longer frozen).
        freed = self.write_cache(five=40, seven=10)
        self.decide(usage_cache=freed)
        s = self.state_now()
        self.assertNotIn("usageHold", s)
        self.assertEqual(s["sameFailureCount"], 2)

    # --- run budget (turns + output tokens) --------------------------------

    def running(self, **extra):
        s = {"status": "running", "iteration": 2, "maxIterations": 20,
             "maxRepeatedFailures": 3}
        s.update(extra)
        self.write(self.state, s)

    def system_message(self, proc):
        return json.loads(proc.stdout)["systemMessage"]

    def test_run_budget_turns_exceeded_escalates(self):
        self.running()
        t = self.write_transcript([("m%d" % i, 10) for i in range(5)])
        p = self.decide(transcript=t, max_turns=5)
        self.assertEqual(p.returncode, 0)                  # allow stop, do NOT loop on
        msg = self.system_message(p)
        self.assertIn("budget exceeded (5 turns / 50 output tokens", msg)
        self.assertIn("escalating to human", msg)
        self.assertIn("UNVERIFIED", msg)                   # never silently passes
        s = self.state_now()
        self.assertEqual(s["status"], "budget_exhausted")
        self.assertEqual(s["runBudget"]["hit"], ["turns"])
        self.assertEqual(s["iteration"], 2)                # not bumped on escalation
        self.assertTrue(os.path.exists(os.path.join(self.d, "BLOCKER.md")))

    def test_run_budget_output_tokens_exceeded_escalates(self):
        self.running()
        self.write(self.verdict, {"reviewedSha": SHA, "pass": False,
                                  "failingGate": "test", "evidence": "boom"})
        t = self.write_transcript([("m1", 900), ("m2", 900)])
        p = self.decide(transcript=t, max_turns=3000, max_output_tokens=1500)
        self.assertEqual(p.returncode, 0)
        msg = self.system_message(p)
        self.assertIn("1800 output tokens", msg)
        self.assertIn("fresh FAIL at gate `test`", msg)    # oracle state reported
        self.assertEqual(self.state_now()["runBudget"]["hit"], ["outputTokens"])

    def test_run_budget_dedupes_split_messages(self):
        # One logical message split over 3 content-block lines: 1 turn / 100 tokens,
        # not 3 / 300 — so a 2-turn, 150-token budget is NOT reached.
        self.running()
        t = self.write_transcript([("m1", 100), ("m1", 100), ("m1", 100)])
        p = self.decide(transcript=t, max_turns=2, max_output_tokens=150)
        self.assertEqual(p.returncode, 2)                  # normal block, under budget
        self.assertIn("oracle has not been run", p.stderr)

    def test_run_budget_counts_subagent_transcripts(self):
        # Subagent runs live in <session>/subagents/*.jsonl, where most of a
        # marathon's cost is; the ceiling must see them.
        self.running()
        t = self.write_transcript([("main1", 10)])
        self.write_transcript([("sub%d" % i, 10) for i in range(4)],
                              path=os.path.join(self.d, "session", "subagents",
                                                "agent-abc.jsonl"))
        p = self.decide(transcript=t, max_turns=5)
        self.assertEqual(p.returncode, 0)
        self.assertIn("5 turns", self.system_message(p))

    def test_run_budget_under_cap_behaves_normally(self):
        self.running()
        t = self.write_transcript([("m%d" % i, 10) for i in range(4)])
        p = self.decide(transcript=t, max_turns=5, max_output_tokens=1000000)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.state_now()["iteration"], 3)

    def test_run_budget_zero_disables(self):
        self.running()
        t = self.write_transcript([("m%d" % i, 10) for i in range(50)])
        p = self.decide(transcript=t, max_turns=0, max_output_tokens=0)
        self.assertEqual(p.returncode, 2)

    def test_run_budget_missing_transcript_fail_open(self):
        self.running()
        p = self.decide(transcript=os.path.join(self.d, "nope.jsonl"), max_turns=1)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.state_now()["iteration"], 3)

    def test_run_budget_fresh_pass_still_completes(self):
        # A verified goal is done — the ceiling only intercepts keep-working paths.
        self.running()
        self.write(self.verdict, {"reviewedSha": SHA, "pass": True})
        t = self.write_transcript([("m%d" % i, 10) for i in range(50)])
        p = self.decide(transcript=t, max_turns=5)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.state_now()["status"], "complete")

    def test_run_budget_ships_calibrated_defaults(self):
        # Pins the shipped defaults (3000 turns / 1 000 000 output tokens), calibrated
        # to bind on the two measured runaways (4823 turns / 996K, 4293 / 769K).
        self.running()
        t = self.write_transcript([("m%d" % i, 1) for i in range(3000)])
        p = self.decide(transcript=t)              # no cap env → engine defaults
        self.assertEqual(p.returncode, 0)
        self.assertIn("caps 3000/1000000", self.system_message(p))
        self.assertEqual(self.state_now()["runBudget"]["hit"], ["turns"])

    def test_run_budget_default_turns_not_reached_one_below(self):
        self.running()
        t = self.write_transcript([("m%d" % i, 1) for i in range(2999)])
        self.assertEqual(self.decide(transcript=t).returncode, 2)

    def test_run_budget_wins_over_usage_hold(self):
        # Over budget AND over the usage floor → escalate, do not wait 6h to keep
        # burning the rest of the budget.
        self.running()
        t = self.write_transcript([("m%d" % i, 10) for i in range(5)])
        cache = self.write_cache(five=99, five_reset=self.iso_in(3600), seven=10)
        p = self.decide(usage_cache=cache, transcript=t, max_turns=5)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.state_now()["status"], "budget_exhausted")

    def test_run_budget_defaults_agree_with_the_hook_wrapper(self):
        # goal-loop-gate.sh ALWAYS exports the caps, so its fallbacks — not the
        # engine's — are what ships. Drift between the two would silently run a
        # different budget than every doc and test claims.
        gate = os.path.join(os.path.dirname(SCRIPT), "goal-loop-gate.sh")
        with open(gate) as fh:
            src = fh.read()
        self.assertEqual(src.count("3000"), 3)          # :-3000, || echo, case-guard
        self.assertEqual(src.count("1000000"), 3)
        self.assertNotIn("5000", src)
        self.assertNotIn("2000000", src)

    def test_stuck_wins_over_hold(self):
        # A genuinely stuck loop escalates even when usage is high.
        sig = "test:" + __import__("hashlib").sha256(b"boom").hexdigest()[:16]
        self.write(self.state, {"status": "running", "iteration": 5, "maxIterations": 20,
                                "maxRepeatedFailures": 3, "sameFailureCount": 2,
                                "lastFailureSig": sig})
        self.write(self.verdict, {"reviewedSha": SHA, "pass": False,
                                  "failingGate": "test", "evidence": "boom"})
        cache = self.write_cache(five=99, five_reset=self.iso_in(3600), seven=99)
        p = self.decide(usage_cache=cache)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.state_now()["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
