"""Unit tests for scripts/install-hook.sh (settings.json hook registration)."""
import json
import os
import subprocess
import tempfile
import unittest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "skills", "goal-loop", "scripts", "install-hook.sh")


def stop_groups(settings):
    return settings.get("hooks", {}).get("Stop", [])


def has_goal_loop(settings):
    return any(
        "goal-loop-gate" in h.get("command", "")
        for g in stop_groups(settings) for h in g.get("hooks", []))


class TestInstallHook(unittest.TestCase):
    def setUp(self):
        self.cfg = tempfile.mkdtemp()
        self.settings = os.path.join(self.cfg, "settings.json")

    def run_hook(self, *args):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.cfg)
        return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=env)

    def load(self):
        with open(self.settings) as fh:
            return json.load(fh)

    def test_fresh_install_creates_settings(self):
        p = self.run_hook()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(has_goal_loop(self.load()))

    def test_install_is_idempotent(self):
        self.run_hook()
        self.run_hook()
        groups = [g for g in stop_groups(self.load())
                  if any("goal-loop-gate" in h.get("command", "") for h in g["hooks"])]
        self.assertEqual(len(groups), 1)

    def test_preserves_other_settings(self):
        with open(self.settings, "w") as fh:
            json.dump({"model": "opus",
                       "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/other.sh"}]}],
                                 "PreToolUse": []}}, fh)
        self.run_hook()
        s = self.load()
        self.assertEqual(s["model"], "opus")
        self.assertIn("PreToolUse", s["hooks"])
        self.assertTrue(any("/other.sh" in h.get("command", "")
                            for g in stop_groups(s) for h in g["hooks"]))
        self.assertTrue(has_goal_loop(s))

    def test_uninstall_removes_only_ours(self):
        with open(self.settings, "w") as fh:
            json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/other.sh"}]}]}}, fh)
        self.run_hook()
        self.run_hook("--uninstall")
        s = self.load()
        self.assertFalse(has_goal_loop(s))
        self.assertTrue(any("/other.sh" in h.get("command", "")
                            for g in stop_groups(s) for h in g["hooks"]))

    def test_refuses_to_clobber_invalid_json(self):
        with open(self.settings, "w") as fh:
            fh.write("{ not valid")
        p = self.run_hook()
        self.assertEqual(p.returncode, 1)
        # original content left intact
        with open(self.settings) as fh:
            self.assertIn("not valid", fh.read())


if __name__ == "__main__":
    unittest.main()
