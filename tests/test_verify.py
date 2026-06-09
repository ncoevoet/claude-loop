"""Integration tests for the oracle (scripts/verify.sh) in throwaway git repos."""
import json
import os
import subprocess
import tempfile
import unittest

VERIFY = os.path.join(
    os.path.dirname(__file__), "..", "skills", "goal-loop", "scripts", "verify.sh")


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True,
                   capture_output=True, text=True)


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")

    def pkg(self, scripts):
        with open(os.path.join(self.repo, "package.json"), "w") as fh:
            json.dump({"scripts": scripts}, fh)

    def config(self, obj):
        d = os.path.join(self.repo, ".claude")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "loop.json"), "w") as fh:
            json.dump(obj, fh)

    def commit(self):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")

    def verify(self, *args):
        return subprocess.run(["bash", VERIFY, *args], cwd=self.repo,
                              capture_output=True, text=True)

    def verdict(self):
        with open(os.path.join(self.repo, ".claude", "loop", "verdict.json")) as fh:
            return json.load(fh)

    def test_clean_lint_and_test_pass(self):
        self.pkg({"lint": "echo LINT_OK", "test": "echo TEST_OK"})
        self.commit()
        p = self.verify()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        v = self.verdict()
        self.assertTrue(v["pass"])
        self.assertEqual(v["stages"], {"lint": "PASS", "test": "PASS"})

    def test_failing_test_short_circuits(self):
        self.pkg({"lint": "echo OK", "test": "echo NOPE; exit 1", "build": "echo SHOULD_NOT_RUN"})
        self.config({"oracle": {"mandatory": ["lint", "test", "build"]}})
        self.commit()
        p = self.verify()
        self.assertEqual(p.returncode, 1)
        v = self.verdict()
        self.assertFalse(v["pass"])
        self.assertEqual(v["failingGate"], "test")
        self.assertNotIn("build", v["stages"])  # short-circuited before build
        self.assertIn("NOPE", v["evidence"])

    def test_cache_reuse_on_unchanged_tree(self):
        self.pkg({"lint": "echo OK", "test": "echo OK"})
        self.commit()
        sha1 = self.verify("--print-sha").stdout.strip()
        self.verify()
        v1 = self.verdict()
        # second run reuses: same reviewedSha, verdict unchanged
        self.verify()
        v2 = self.verdict()
        self.assertEqual(v1["reviewedSha"], v2["reviewedSha"])
        self.assertEqual(v1["reviewedSha"], sha1)

    def test_print_sha_stable_then_moves_on_change(self):
        self.pkg({"lint": "echo OK", "test": "echo OK"})
        self.commit()
        a = self.verify("--print-sha").stdout.strip()
        self.verify()  # writes run-state; must NOT change the sha
        b = self.verify("--print-sha").stdout.strip()
        self.assertEqual(a, b, "run-state leaked into work-sha")
        with open(os.path.join(self.repo, "new.txt"), "w") as fh:
            fh.write("source change")
        c = self.verify("--print-sha").stdout.strip()
        self.assertNotEqual(a, c)

    def test_reviewall_missing_gate_verdict_fails(self):
        self.pkg({"lint": "echo OK", "test": "echo OK"})
        self.config({"oracle": {"mandatory": ["lint", "test", "reviewall"]},
                     "reviewall": {"severityFloor": "critical"}})
        self.commit()
        p = self.verify("--force")
        self.assertEqual(p.returncode, 1)
        v = self.verdict()
        self.assertFalse(v["pass"])
        self.assertEqual(v["failingGate"], "reviewall")
        self.assertIn("review-all gate", v["evidence"])

    def test_config_overrides_discovery(self):
        self.pkg({"test": "echo DISCOVERY_RAN; exit 1"})  # discovery would fail
        self.config({"oracle": {"test": "echo CONFIG_RAN", "mandatory": ["test"]}})
        self.commit()
        p = self.verify("--force")
        self.assertEqual(p.returncode, 0, p.stdout)  # config command (passing) won


if __name__ == "__main__":
    unittest.main()
