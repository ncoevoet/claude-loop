"""Unit tests for the Stop-hook decision engine (scripts/gate-decide.py).

Pure logic: WORK_SHA is passed as an arg, so no git is needed. Each test crafts
STATE.json + verdict.json in a temp dir and asserts the exit code + the STATE
mutation. Exit 0 = allow stop, 2 = block (keep working)."""
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

    def decide(self, work_sha=SHA):
        return subprocess.run(
            [sys.executable, SCRIPT, self.state, self.verdict, work_sha, "/x/verify.sh"],
            capture_output=True, text=True)

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


if __name__ == "__main__":
    unittest.main()
