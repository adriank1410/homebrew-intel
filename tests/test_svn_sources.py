# SPDX-License-Identifier: BSD-2-Clause
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from intelbrew.core import ROOT, brew_env, run


@unittest.skipUnless(os.environ.get("INTELBREW_NATIVE_TESTS") == "1",
                     "set INTELBREW_NATIVE_TESTS=1 for brew ruby integration tests")
class SvnSourcesTests(unittest.TestCase):
    def _ruby(self, script, extra_env=None):
        environment = brew_env()
        environment.update(extra_env or {})
        return run(["brew", "ruby", "-e", script], env=environment)

    def _svn_repo(self, root, *, option_named=False):
        repo = root / "repo"
        wc = root / "wc"
        conf = root / "svn-conf"
        conf.mkdir(parents=True, exist_ok=True)
        subprocess.run(["svnadmin", "create", str(repo)], check=True)
        subprocess.run(["svn", "checkout", f"file://{repo}", str(wc),
                        "--config-dir", str(conf), "--non-interactive"], check=True)
        (wc / "README").write_text("fixture\n")
        subprocess.run(["svn", "add", str(wc / "README"),
                        "--config-dir", str(conf), "--non-interactive"], check=True)
        if option_named:
            option_named_file = wc / "--exclude=README"
            option_named_file.write_text("option-named\n")
            subprocess.run(["svn", "add", str(option_named_file),
                            "--config-dir", str(conf), "--non-interactive"], check=True)
        subprocess.run(["svn", "commit", "-m", "fixture", str(wc),
                        "--config-dir", str(conf), "--non-interactive"], check=True)
        # In Subversion, working copy needs update to reflect commit revision
        subprocess.run(["svn", "update", str(wc),
                        "--config-dir", str(conf), "--non-interactive"], check=True)
        return repo, wc

    def _resource_script(self, repo_url, revision, out):
        return f'''require "json"; require "resource"; load {json.dumps(str(ROOT / "libexec/svn_sources.rb"))}
r = Resource.new("fixture")
r.define_singleton_method(:cache) {{ Pathname.new({json.dumps(str(out.parent / "brew-cache"))}) }}
r.url({json.dumps(repo_url)}, using: :svn, revision: {json.dumps(revision)})
puts JSON.generate(SvnSources.export(r, {json.dumps(str(out))}))'''

    def _cached_resource_script(self, repo_url, revision, cache, out):
        return f'''require "json"; require "pathname"; load {json.dumps(str(ROOT / "libexec/svn_sources.rb"))}
d = Struct.new(:cached_location).new(Pathname.new({json.dumps(str(cache))}))
r = Struct.new(:download_strategy, :specs, :downloader, :url).new(
  SubversionDownloadStrategy, {{revision: {json.dumps(revision)}}}, d, {json.dumps(repo_url)})
def r.fetch; end
puts JSON.generate(SvnSources.export(r, {json.dumps(str(out))}))'''

    def test_supports_pinned_numeric_revision(self):
        script = f'''require "json"; load {json.dumps(str(ROOT / "libexec/svn_sources.rb"))}
r_valid = Struct.new(:download_strategy, :specs).new(SubversionDownloadStrategy, {{revision: "5319"}})
r_int = Struct.new(:download_strategy, :specs).new(SubversionDownloadStrategy, {{revision: 5319}})
r_head = Struct.new(:download_strategy, :specs).new(SubversionDownloadStrategy, {{revision: "HEAD"}})
r_alpha = Struct.new(:download_strategy, :specs).new(SubversionDownloadStrategy, {{revision: "r5319"}})
r_none = Struct.new(:download_strategy, :specs).new(SubversionDownloadStrategy, {{}})
r_git = Struct.new(:download_strategy, :specs).new(GitDownloadStrategy, {{revision: "5319"}})
puts JSON.generate([
  SvnSources.supported?(r_valid),
  SvnSources.supported?(r_int),
  SvnSources.supported?(r_head),
  SvnSources.supported?(r_alpha),
  SvnSources.supported?(r_none),
  SvnSources.supported?(r_git)
])'''
        result = json.loads(self._ruby(script).splitlines()[-1])
        self.assertEqual(result, [True, True, False, False, False, False])

    def test_exports_exact_pinned_revision_and_hash_record(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo, wc = self._svn_repo(root)
            out = root / "out"
            out.mkdir()
            cache = wc  # use working copy directly as pre-fetched cache
            result = json.loads(self._ruby(
                self._cached_resource_script(f"file://{repo}", "1", cache, out)
            ).splitlines()[-1])
            archive = Path(result["path"])
            self.assertEqual(result["label"], "svn")
            self.assertEqual(result["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            with tarfile.open(archive) as tar:
                names = tar.getnames()
                self.assertIn("README", {Path(name).name for name in names})
                self.assertFalse(any(".svn" in name for name in names))

    def test_exports_option_named_child_without_treating_it_as_tar_flag(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo, wc = self._svn_repo(root, option_named=True)
            out = root / "out"
            out.mkdir()
            result = json.loads(self._ruby(
                self._cached_resource_script(f"file://{repo}", "1", wc, out)
            ).splitlines()[-1])
            with tarfile.open(Path(result["path"])) as tar:
                names = {Path(name).name for name in tar.getnames()}
            self.assertIn("README", names)
            self.assertIn("--exclude=README", names)

    def test_rejects_cached_revision_mismatch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo, wc = self._svn_repo(root)
            out = root / "out"
            out.mkdir()
            cache = wc
            # wc is revision 1, but requested revision is 999
            script = self._cached_resource_script(f"file://{repo}", "999", cache, out)
            with self.assertRaisesRegex(RuntimeError, "revision mismatch|revision"):
                self._ruby(script)


if __name__ == "__main__":
    unittest.main()
