# SPDX-License-Identifier: BSD-2-Clause
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intelbrew.ci import _prefetch_build_inputs
from intelbrew.core import Error


class BuildSourceFetchTests(unittest.TestCase):
    def test_prefetch_retries_transient_homebrew_fetch_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            calls = root / "calls"
            brew = root / "brew"
            brew.write_text(
                f"#!{sys.executable}\n"
                "from pathlib import Path\n"
                "import sys\n"
                f"calls = Path({str(calls)!r})\n"
                "count = int(calls.read_text()) if calls.exists() else 0\n"
                "calls.write_text(str(count + 1))\n"
                "if count == 0:\n"
                "    print('dial tcp: lookup proxy.golang.org: i/o timeout')\n"
                "    print('unrelated stderr', file=sys.stderr)\n"
                "    raise SystemExit(1)\n"
                "print('fetch complete')\n"
            )
            brew.chmod(0o755)
            env = {
                "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                "HOMEBREW_CACHE": str(root / "cache"),
            }
            with patch.dict(os.environ, {"INTELBREW_RETRY_ATTEMPTS": "3", "INTELBREW_RETRY_DELAY": "0"}):
                _prefetch_build_inputs("docker-compose", env)

            self.assertEqual(calls.read_text(), "2")

    def test_prefetch_uses_expected_homebrew_command(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return ""

        with patch("intelbrew.ci.run", side_effect=fake_run), \
             patch.dict(os.environ, {"INTELBREW_RETRY_ATTEMPTS": "3", "INTELBREW_RETRY_DELAY": "0"}):
            _prefetch_build_inputs("docker-compose", {"HOMEBREW_CACHE": "/tmp/cache"})

        self.assertEqual(calls, [(
            ["brew", "fetch", "--build-bottle", "--retry", "--formula", "homebrew/core/docker-compose"],
            {"env": {"HOMEBREW_CACHE": "/tmp/cache"}, "echo": True},
        )])

    def test_prefetch_does_not_retry_checksum_failure(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            raise Error("Checksum/size mismatch")

        with patch("intelbrew.ci.run", side_effect=fake_run), \
             patch.dict(os.environ, {"INTELBREW_RETRY_ATTEMPTS": "3", "INTELBREW_RETRY_DELAY": "0"}):
            with self.assertRaisesRegex(Error, "Checksum/size mismatch"):
                _prefetch_build_inputs("docker-compose", {"HOMEBREW_CACHE": "/tmp/cache"})

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
