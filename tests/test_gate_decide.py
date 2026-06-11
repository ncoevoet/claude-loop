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

    def decide(self, work_sha=SHA, usage_cache=None, floor="96", windows=None):
        # Hermetic: strip any inherited LOOP_USAGE_* so the usage guard is off
        # unless a test opts in by passing usage_cache.
        env = {k: v for k, v in os.environ.items() if not k.startswith("LOOP_USAGE_")}
        if usage_cache is not None:
            env["LOOP_USAGE_CACHE"] = usage_cache
            env["LOOP_USAGE_FLOOR"] = str(floor)
            if windows:
                env["LOOP_USAGE_WINDOWS"] = windows
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
