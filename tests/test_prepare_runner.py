# SPDX-License-Identifier: BSD-2-Clause
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "quarantine_links", ROOT / "scripts/quarantine-runner-links.py")
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


class FrameworkPythonLinkTests(unittest.TestCase):
    def test_moves_only_absolute_and_relative_framework_python_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "usr/local/bin"
            backup = root / "backup"
            framework = root / "Library/Frameworks/Python.framework"
            bin_dir.mkdir(parents=True)
            backup.mkdir()
            absolute = framework / "Versions/3.13/bin/python3.13"
            (bin_dir / "python3.13").symlink_to(absolute)
            relative_target = os.path.relpath(
                framework / "Versions/3.12/bin/pip3.12", bin_dir)
            (bin_dir / "pip3.12").symlink_to(relative_target)

            moved = links.quarantine_links(bin_dir, backup, framework_root=framework)

            self.assertEqual(moved, ["pip3.12", "python3.13"])
            for name in moved:
                self.assertFalse((bin_dir / name).is_symlink())
                self.assertTrue((backup / "runner-links" / name).is_symlink())

    def test_retains_regular_files_directories_and_unrelated_or_nested_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            backup = root / "backup"
            framework = root / "framework"
            bin_dir.mkdir()
            backup.mkdir()
            (bin_dir / "regular").write_text("keep")
            (bin_dir / "directory").mkdir()
            (bin_dir / "other").symlink_to("/tmp/tool")
            (bin_dir / "nested").symlink_to(
                framework / "Versions/3.13/bin/subdir/python")

            self.assertEqual(links.quarantine_links(
                bin_dir, backup, framework_root=framework), [])
            self.assertEqual({path.name for path in bin_dir.iterdir()},
                             {"regular", "directory", "other", "nested"})

    def test_broken_framework_link_moves_and_existing_backup_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            backup = root / "backup"
            framework = root / "framework"
            destination = backup / "runner-links"
            bin_dir.mkdir()
            destination.mkdir(parents=True)
            (bin_dir / "python3.13").symlink_to(
                framework / "Versions/3.13/bin/python3.13")
            (destination / "python3.13").symlink_to("original")

            with self.assertRaisesRegex(links.Error, "backup destination exists"):
                links.quarantine_links(bin_dir, backup, framework_root=framework)
            self.assertTrue((bin_dir / "python3.13").is_symlink())
            self.assertEqual(os.readlink(destination / "python3.13"), "original")

    def test_non_ci_guard_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(links.Error):
            links.require_ci_runner()

    def test_moves_absolute_and_relative_runner_dotnet_links(self):
        for relative in (False, True):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bin_dir = root / "usr/local/bin"
                backup = root / "backup"
                runner_home = root / "runner"
                bin_dir.mkdir(parents=True)
                backup.mkdir()
                target = runner_home / ".dotnet/dotnet"
                link_target = os.path.relpath(target, bin_dir) if relative else target
                (bin_dir / "dotnet").symlink_to(link_target)

                self.assertEqual(links.quarantine_links(
                    bin_dir, backup, runner_home=runner_home), ["dotnet"])
                self.assertFalse((bin_dir / "dotnet").is_symlink())
                self.assertTrue((backup / "runner-links/dotnet").is_symlink())

    def test_dotnet_uses_account_home_and_retains_spoofed_or_regular_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            backup = root / "backup"
            runner_home = root / "runner"
            bin_dir.mkdir()
            backup.mkdir()
            (bin_dir / "dotnet").symlink_to(root / "spoofed/.dotnet/dotnet")
            (bin_dir / "dotnet-file").write_text("keep")
            with patch.dict(os.environ, {"HOME": str(root / "spoofed")}, clear=False):
                self.assertEqual(links.quarantine_links(
                    bin_dir, backup, runner_home=runner_home), [])
            self.assertTrue((bin_dir / "dotnet").is_symlink())
            self.assertEqual((bin_dir / "dotnet-file").read_text(), "keep")


class FetchRetryTests(unittest.TestCase):
    def test_retries_then_succeeds_and_bounds_permanent_failure(self):
        helper = ROOT / "scripts/retry-fetch.sh"
        for succeeds, expected in ((True, 0), (False, 17)):
            with self.subTest(succeeds=succeeds), tempfile.TemporaryDirectory() as folder:
                counter = Path(folder) / "count"
                command = "n=0; test ! -f \"$1\" || n=$(cat \"$1\"); n=$((n+1)); echo $n > \"$1\"; test \"$2\" = True && test $n -eq 3 && exit 0; exit 17"
                result = subprocess.run(["bash", str(helper), "bash", "-c", command, "test", str(counter), str(succeeds)], env={**os.environ, "INTELBREW_RETRY_DELAY": "0"}, capture_output=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(counter.read_text().strip(), "3")

    def test_exponential_backoff_delays(self):
        helper = ROOT / "scripts/retry-fetch.sh"
        with tempfile.TemporaryDirectory() as folder:
            f = Path(folder)
            bin_dir = f / "bin"
            bin_dir.mkdir()
            mock_sleep = bin_dir / "sleep"
            mock_sleep.write_text("#!/bin/bash\necho \"$1\" >> \"$SLEEP_LOG\"\nexit 0\n")
            mock_sleep.chmod(0o755)
            log = f / "delays.log"
            counter = f / "count"
            command = "n=0; test ! -f \"$1\" || n=$(cat \"$1\"); n=$((n+1)); echo $n > \"$1\"; test $n -eq 3 && exit 0; exit 1"
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "SLEEP_LOG": str(log),
                "INTELBREW_RETRY_DELAY": "5",
            }
            result = subprocess.run(["bash", str(helper), "bash", "-c", command, "test", str(counter)], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(counter.read_text().strip(), "3")
            self.assertEqual(log.read_text().splitlines(), ["5", "10"])

    def test_invalid_retry_settings_fail_before_running_command(self):
        helper = ROOT / "scripts/retry-fetch.sh"
        for variable, value in (("INTELBREW_RETRY_ATTEMPTS", "0"),
                                ("INTELBREW_RETRY_ATTEMPTS", "-1"),
                                ("INTELBREW_RETRY_DELAY", "-1"),
                                ("INTELBREW_RETRY_DELAY", "9223372036854775808"),
                                ("INTELBREW_RETRY_DELAY", "not-a-number")):
            with self.subTest(variable=variable, value=value), tempfile.TemporaryDirectory() as folder:
                marker = Path(folder) / "ran"
                env = {key: value for key, value in os.environ.items()
                       if key not in {"INTELBREW_RETRY_ATTEMPTS", "INTELBREW_RETRY_DELAY"}}
                env[variable] = value
                result = subprocess.run(
                    ["bash", str(helper), "bash", "-c", "printf ran > \"$1\"", "test", str(marker)],
                    env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(marker.exists())

    def test_decimal_leading_zero_settings_are_supported(self):
        helper = ROOT / "scripts/retry-fetch.sh"
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "ran"
            env = {**os.environ, "INTELBREW_RETRY_ATTEMPTS": "03", "INTELBREW_RETRY_DELAY": "08"}
            result = subprocess.run(
                ["bash", str(helper), "bash", "-c", "printf ran > \"$1\"", "test", str(marker)],
                env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(), "ran")

    def test_prepare_runner_bootstraps_portable_ruby_with_retry(self):
        script = (ROOT / "scripts/prepare-runner.sh").read_text()
        self.assertIn('bash "$project_dir/scripts/retry-fetch.sh" brew vendor-install ruby', script)
        self.assertIn("HOMEBREW_CURL_RETRIES=", script)


if __name__ == "__main__":
    unittest.main()
