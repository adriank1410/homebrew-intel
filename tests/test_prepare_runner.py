# SPDX-License-Identifier: BSD-2-Clause
import importlib.util
import os
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


if __name__ == "__main__":
    unittest.main()
