"""Smoke test for ProcessTreeSampler using a spawned `sleep` process.

Does not touch Zed at all — just verifies the sampler can attach to a
real PID, collect samples at roughly the configured interval, and report
sane RSS/tag values.
"""

import subprocess
import time
import unittest

from zpb.sampler import TAG_ZED, ProcessTreeSampler


class TestProcessTreeSampler(unittest.TestCase):
    def test_samples_a_real_process(self) -> None:
        proc = subprocess.Popen(["sleep", "5"])
        try:
            sampler = ProcessTreeSampler(proc.pid, interval=0.5)
            sampler.start()
            time.sleep(2.2)
            sampler.stop()

            samples = sampler.snapshot()
            self.assertGreaterEqual(len(samples), 2, "expected at least a couple of samples")

            first = samples[0]
            self.assertGreater(first.tree_rss_bytes, 0)
            self.assertEqual(len(first.processes), 1)
            self.assertEqual(first.processes[0].pid, proc.pid)
            self.assertEqual(first.processes[0].tag, TAG_ZED)

            # Elapsed time (`t`) should be monotonically non-decreasing.
            ts = [s.t for s in samples]
            self.assertEqual(ts, sorted(ts))
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
