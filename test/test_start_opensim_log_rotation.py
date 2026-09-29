"""The log guard in `tools/start_opensim.sh`, driven as a script.

The guard exists because `OpenSim.log` reached 2.3 GB on 2026-09-11 on a root
filesystem with 12 GB free, essentially all of it one repeated stack trace. It
rotates at start when the log is too big.

Its first version tested `stat -c %s` -- the *apparent* size -- and reported
`du -h`, the disk usage. Those are the same number for every file except a
sparse one, and the same session that wrote the guard created a sparse one:
truncating the log while the simulator still held it open left the writer
appending at its old offset, so the file has read 2.39 GB to `ls` and 76 KB to
`du` ever since. The guard protects a disk; the disk holds 76 KB. Against the
very file it was written for, it would have rotated and printed
"OpenSim.log is 76K" as its reason.

These tests run the real script in a temporary tree with a stub `OpenSim`
binary and a stub .NET root, so what is exercised is the shipped shell rather
than a restatement of it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "start_opensim.sh"

#: The guard's own threshold. Kept here so a test that stops matching the
#: script fails rather than quietly testing a different rule.
MAX_LOG_BYTES = 256 * 1024 * 1024


class LogRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("bash") is None:  # pragma: no cover - not seen
            self.skipTest("bash unavailable")
        self.tmp = Path(tempfile.mkdtemp(prefix="vibestorm-start-opensim-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.bin_dir = self.tmp / "local" / "opensim" / "runtime" / "bin"
        self.bin_dir.mkdir(parents=True)
        tools = self.tmp / "tools"
        tools.mkdir()
        self.script = tools / "start_opensim.sh"
        shutil.copy2(SCRIPT, self.script)

        # A stub for the thing the script execs, so the run ends immediately
        # and with a recognisable marker rather than starting a simulator.
        stub = self.bin_dir / "OpenSim"
        stub.write_text("#!/usr/bin/env bash\necho STUB_OPENSIM_RAN\n")
        stub.chmod(0o755)

        # A stub .NET 8 root, which the script checks for before anything else.
        self.dotnet = self.tmp / "dotnet"
        (self.dotnet / "shared" / "Microsoft.NETCore.App" / "8.0.0").mkdir(parents=True)

        self.log = self.bin_dir / "OpenSim.log"

    def run_script(self) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, DOTNET_ROOT=str(self.dotnet))
        return subprocess.run(
            ["bash", str(self.script)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )

    def write_sparse_log(self, apparent_bytes: int, real_text: bytes) -> None:
        """A file that is huge to `ls` and small on disk.

        This is what `truncate -s 0` leaves behind when the writer holds the
        file open and carries on at its old offset.
        """
        with self.log.open("wb") as handle:
            handle.seek(apparent_bytes - len(real_text))
            handle.write(real_text)

    # -- the claim ---------------------------------------------------------

    def test_a_sparse_log_that_costs_nothing_is_not_rotated(self) -> None:
        """The regression. `ls` says 2.39 GB, the disk says kilobytes."""
        self.write_sparse_log(2_392_243_462, b"tail line\n" * 50)

        apparent = self.log.stat().st_size
        on_disk = self.log.stat().st_blocks * 512
        self.assertGreater(apparent, MAX_LOG_BYTES, "fixture is not big enough")
        self.assertLess(on_disk, MAX_LOG_BYTES, "fixture is not sparse")

        result = self.run_script()

        self.assertIn("STUB_OPENSIM_RAN", result.stdout)
        self.assertEqual(
            sorted(p.name for p in self.bin_dir.glob("*.gz")),
            [],
            f"rotated a log using {on_disk} bytes of disk: {result.stderr}",
        )
        self.assertEqual(
            self.log.stat().st_size,
            apparent,
            "truncated a log that was costing nothing",
        )

    def test_a_genuinely_large_log_is_still_rotated(self) -> None:
        """The control, without which the test above passes by never rotating."""
        # Written, not seeked: this one really does occupy the disk.
        with self.log.open("wb") as handle:
            handle.write(b"x" * 1024 * 1024)
            handle.write(b"\n")
            for index in range(4000):
                handle.write(f"line {index}\n".encode())
            handle.write(b"y" * (MAX_LOG_BYTES + 1 - handle.tell()))

        self.assertGreater(self.log.stat().st_blocks * 512, MAX_LOG_BYTES)

        result = self.run_script()

        self.assertIn("STUB_OPENSIM_RAN", result.stdout)
        self.assertEqual(len(list(self.bin_dir.glob("*.gz"))), 1, result.stderr)
        self.assertEqual(self.log.stat().st_size, 0, "log was not truncated")
        self.assertIn("using", result.stderr)

    def test_the_message_reports_the_number_the_condition_tested(self) -> None:
        """The two measures must not come apart again.

        The original defect was not the threshold, it was that the test and
        the explanation read different fields. A message that can disagree
        with its own reason is how a guard gets trusted while misfiring.
        """
        source = SCRIPT.read_text()
        guard = source.split("MAX_LOG_BYTES=", 1)[1].split("cd \"$BIN_DIR\"", 1)[0]
        # Comments only, stripped: the block deliberately *describes* the old
        # `stat -c %s` behaviour, and the first version of this test matched
        # that prose and failed against the fixed script. A test that reads a
        # file's comments is not reading its code.
        guard = "\n".join(
            line for line in guard.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn(
            "stat -c %s",
            guard,
            "the guard is testing apparent size again; a sparse log reads "
            "thirty thousand times larger than it costs",
        )
        self.assertIn("%b", guard)
        self.assertIn("log_disk_bytes", guard)
        # The message interpolates the same variable the condition tested.
        # The printf is split over two lines by a continuation, so the
        # argument is the line after the format string.
        lines = guard.splitlines()
        index = next(i for i, ln in enumerate(lines) if "printf 'OpenSim.log" in ln)
        argument = lines[index + 1]
        self.assertIn("log_disk_bytes", argument, f"message argument: {argument!r}")

    def test_no_log_at_all_is_not_an_error(self) -> None:
        """A fresh checkout has never started the simulator."""
        self.assertFalse(self.log.exists())
        result = self.run_script()
        self.assertIn("STUB_OPENSIM_RAN", result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
