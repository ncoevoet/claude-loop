"""Tests for the in-session usage watch (scripts/watch-quota.sh) and the pure
helpers in scripts/usage-lib.sh.

The adaptive-interval and max-util helpers are exercised by sourcing the bash
lib; watch-quota.sh is driven offline via the LOOP_TEST_USAGE seam (no network)
and LOOP_TEST_NOSLEEP (skip the real sleep)."""
import os
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "skills", "goal-loop", "scripts")
ULIB = os.path.join(SCRIPTS, "usage-lib.sh")
WATCH = os.path.join(SCRIPTS, "watch-quota.sh")


def sh(snippet):
    return subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)


class TestPollInterval(unittest.TestCase):
    def iv(self, rem, base=900):
        r = sh('. "%s"; loop_poll_interval %s %s' % (ULIB, rem, base))
        return int(r.stdout.strip())

    def test_far_polls_rarely(self):
        self.assertEqual(self.iv(10800), 1800)   # 3h away

    def test_base_band(self):
        self.assertEqual(self.iv(3600), 900)     # 1h away → base

    def test_near_polls_often(self):
        self.assertEqual(self.iv(1200), 300)     # 20m away → <30m bucket

    def test_reset_cap(self):
        self.assertEqual(self.iv(60), 90)        # capped to remaining + 30s

    def test_past_reset(self):
        self.assertEqual(self.iv(-5), 300)

    def test_custom_base(self):
        self.assertEqual(self.iv(3600, 600), 600)

    def test_nonnumeric_is_base(self):
        self.assertEqual(self.iv("x"), 900)


class TestMaxUtil(unittest.TestCase):
    def mx(self, a, b):
        return int(sh('. "%s"; usage_max_util "%s" "%s"' % (ULIB, a, b)).stdout.strip())

    def test_first_wins(self):
        self.assertEqual(self.mx("96.7", "10"), 96)

    def test_second_wins(self):
        self.assertEqual(self.mx("5", "99.2"), 99)

    def test_empty_is_zero(self):
        self.assertEqual(self.mx("", ""), 0)


class TestWatchQuota(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.proj, ".git"))   # project-root marker

    def run_watch(self, **env_extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LOOP_")}
        env.update(env_extra)
        return subprocess.run(["bash", WATCH], cwd=self.proj,
                              capture_output=True, text=True, env=env, timeout=30)

    def test_freed_signals_continue(self):
        p = self.run_watch(LOOP_TEST_USAGE="10||10|")   # below floor
        self.assertEqual(p.returncode, 0)
        self.assertIn("QUOTA FREED", p.stdout)

    def test_over_floor_waits(self):
        # Over floor → prints WAITING and (with NOSLEEP) returns without sleeping.
        p = self.run_watch(LOOP_TEST_USAGE="99||10|", LOOP_TEST_NOSLEEP="1")
        self.assertEqual(p.returncode, 0)
        self.assertIn("WAITING", p.stdout)

    def test_no_data_fails_open(self):
        # Empty usage line → cannot gate → continue (fail-open, never wait forever).
        p = self.run_watch(LOOP_TEST_USAGE="|||")
        self.assertEqual(p.returncode, 0)
        self.assertIn("QUOTA FREED", p.stdout)


if __name__ == "__main__":
    unittest.main()
