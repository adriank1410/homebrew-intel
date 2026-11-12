# SPDX-License-Identifier: BSD-2-Clause
"""Git source exports must hold every tracked file, unchanged.

These run with plain Ruby and Git, so they also run on the Linux check.
The full export through `brew ruby` is covered in test_git_sources.py.
"""
import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = '''
require "json"; require "pathname"; require "tmpdir"
load ARGV[0]
repo = Pathname(ARGV[1]); out = Pathname(ARGV[3])
archives = GitSources.send(:collect_archives, repo, ARGV[2], out, "", 0, [0], [0])
puts JSON.generate(archives.map(&:to_s))
'''


@unittest.skipUnless(shutil.which("ruby") and shutil.which("git"), "needs ruby and git")
class GitExportAttributeTests(unittest.TestCase):
    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def init(self, repo):
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for key, value in (("user.email", "test@example.invalid"), ("user.name", "Test"),
                           ("commit.gpgsign", "false")):
            self.git(repo, "config", key, value)

    def write(self, repo, files):
        for name, content in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "fixture")
        return self.git(repo, "rev-parse", "HEAD")

    def members(self, repo, revision, out):
        archives = json.loads(subprocess.run(
            ["ruby", "-e", SCRIPT, str(ROOT / "libexec/git_sources.rb"), str(repo), revision, str(out)],
            check=True, capture_output=True, text=True).stdout.splitlines()[-1])
        files = {}
        for archive in archives:
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        files[member.name] = tar.extractfile(member).read().decode()
        return files

    def test_export_keeps_ignored_paths_and_unexpanded_keywords(self):
        files = {
            ".gitattributes": "tests export-ignore\ndocs/** export-ignore\n/ci/ export-ignore\n"
                              "version.txt export-subst\n*.orig export-ignore\n",
            "README": "readme\n",
            "tests/unit/a.txt": "a\n",
            "docs/guide/deep/page.md": "page\n",
            "ci/run.sh": "run\n",
            "version.txt": "rev $Format:%H$\n",
            "src/patch.orig": "orig\n",
            "src/.gitattributes": "*.gen export-ignore\n",
            "src/table.gen": "gen\n",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); repo = root / "repo"; out = root / "out"; out.mkdir()
            self.init(repo)
            revision = self.write(repo, files)
            # Confirm the fixture: a plain archive really does lose these.
            plain = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", revision],
                                   check=True, capture_output=True).stdout
            (root / "plain.tar").write_bytes(plain)
            with tarfile.open(root / "plain.tar") as tar:
                plain_names = {m.name for m in tar.getmembers() if m.isfile()}
            self.assertEqual(plain_names, {".gitattributes", "README", "version.txt", "src/.gitattributes"})

            self.assertEqual(self.members(repo, revision, out), files)
            self.assertFalse((repo / ".git/info/attributes").exists())
            self.assertEqual(self.git(repo, "status", "--porcelain"), "")

    def test_existing_info_attributes_is_restored_even_when_archiving_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); repo = root / "repo"; out = root / "out"; out.mkdir()
            self.init(repo)
            revision = self.write(repo, {".gitattributes": "hidden export-ignore\n",
                                         "hidden": "h\n", "kept": "k\n"})
            info = repo / ".git/info/attributes"
            info.parent.mkdir(exist_ok=True)
            info.write_text("kept export-ignore\n")
            # The repository's own info/attributes loses to the appended line too.
            self.assertEqual(set(self.members(repo, revision, out)), {".gitattributes", "hidden", "kept"})
            self.assertEqual(info.read_text(), "kept export-ignore\n")
            failed = subprocess.run(
                ["ruby", "-e", SCRIPT, str(ROOT / "libexec/git_sources.rb"), str(repo), "0" * 40, str(out)],
                capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(info.read_text(), "kept export-ignore\n")

    def test_submodule_export_ignores_are_switched_off_in_its_own_git_dir(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); child = root / "child"; repo = root / "repo"; out = root / "out"; out.mkdir()
            self.init(child)
            child_revision = self.write(child, {".gitattributes": "private export-ignore\n",
                                                "private/key.txt": "k\n", "CHILD": "c\n"})
            self.init(repo)
            self.write(repo, {"README": "r\n"})
            subprocess.run(["git", "-C", str(repo), "-c", "protocol.file.allow=always", "submodule",
                            "add", "-q", str(child), "vendor/child"], check=True, capture_output=True)
            self.git(repo, "commit", "-q", "-m", "submodule")
            revision = self.git(repo, "rev-parse", "HEAD")
            self.assertEqual(self.git(repo / "vendor/child", "rev-parse", "HEAD"), child_revision)
            files = self.members(repo, revision, out)
            self.assertIn("vendor/child/private/key.txt", files)
            self.assertIn("vendor/child/CHILD", files)
            self.assertFalse((repo / ".git/modules/vendor/child/info/attributes").exists())


if __name__ == "__main__":
    unittest.main()
