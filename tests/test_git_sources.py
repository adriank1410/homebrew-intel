# SPDX-License-Identifier: BSD-2-Clause
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
class GitSourcesTests(unittest.TestCase):
    def _ruby(self, script, extra_env=None):
        environment = brew_env(); environment.update(extra_env or {})
        return run(["brew", "ruby", "-e", script], env=environment)

    def _repo(self, root):
        repo = root / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
        (repo / "README").write_text("fixture\n")
        subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fixture"], check=True)
        return repo, subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    def _resource_script(self, repo, revision, out):
        return f'''require "json"; require "resource"; load {json.dumps(str(ROOT / "libexec/git_sources.rb"))}
r = Resource.new("fixture"); r.define_singleton_method(:cache) {{ Pathname.new({json.dumps(str(out.parent / "brew-cache"))}) }}; r.url({json.dumps(repo.as_uri())}, using: :git, revision: {json.dumps(revision)})
puts JSON.generate(GitSources.export(r, {json.dumps(str(out))}))'''

    def _cached_resource_script(self, repo, revision, cache, out):
        return f'''require "json"; require "pathname"; load {json.dumps(str(ROOT / "libexec/git_sources.rb"))}
d = Struct.new(:cached_location).new(Pathname.new({json.dumps(str(cache))})); r = Struct.new(:download_strategy, :specs, :downloader, :url).new(GitDownloadStrategy, {{revision: {json.dumps(revision)}}}, d, {json.dumps(repo.as_uri())}); def r.fetch; end
puts JSON.generate(GitSources.export(r, {json.dumps(str(out))}))'''

    def _child_repo(self, root, name, content=None):
        child = root / name
        subprocess.run(["git", "init", "-q", str(child)], check=True)
        for key, value in (("user.email", "test@example.invalid"), ("user.name", "Test"), ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", str(child), "config", key, value], check=True)
        if content is not None:
            (child / "CHILD").write_text(content)
            subprocess.run(["git", "-C", str(child), "add", "CHILD"], check=True)
        subprocess.run(["git", "-C", str(child), "commit", "--allow-empty", "-q", "-m", "child"], check=True)
        return child, subprocess.check_output(["git", "-C", str(child), "rev-parse", "HEAD"], text=True).strip()

    def _add_gitlink(self, repo, child_revision, path):
        subprocess.run(["git", "-C", str(repo), "update-index", "--add", "--cacheinfo",
                        f"160000,{child_revision},{path}"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "gitlink"], check=True)

    def test_exports_exact_pinned_revision_and_hash_record(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); repo, revision = self._repo(root); out = root / "out"; out.mkdir()
            result = json.loads(self._ruby(self._resource_script(repo, revision, out)).splitlines()[-1])
            archive = Path(result["path"])
            self.assertEqual(result["sha256"], __import__("hashlib").sha256(archive.read_bytes()).hexdigest())
            listing = subprocess.check_output(["tar", "-tf", str(archive)], text=True)
            self.assertEqual(listing.strip().splitlines(), ["README"])

    def test_rejects_cached_revision_mismatch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); repo, revision = self._repo(root); out = root / "out"; out.mkdir()
            other = "0" * 40
            script = self._resource_script(repo, other, out)
            with self.assertRaisesRegex(RuntimeError, "revision|checkout|Git"):
                self._ruby(script)

    def test_gitlink_newline_path_is_archived(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); repo, _ = self._repo(root); out = root / "out"; out.mkdir()
            child, child_revision = self._child_repo(root, "child", "newline\n")
            subpath = "fixture\npart"; (repo / subpath).mkdir()
            subprocess.run(["git", "clone", "-q", str(child), str(repo / subpath)], check=True)
            self._add_gitlink(repo, child_revision, subpath)
            revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            cache = root / "cached"; subprocess.run(["git", "clone", "-q", str(repo), str(cache)], check=True)
            subprocess.run(["git", "clone", "-q", str(child), str(cache / subpath)], check=True)
            result = json.loads(self._ruby(self._cached_resource_script(repo, revision, cache, out)).splitlines()[-1])
            with tarfile.open(result["path"]) as archive:
                self.assertIn(f"{subpath}/CHILD", archive.getnames())

    def test_two_empty_gitlinks_get_distinct_archives(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); repo, _ = self._repo(root); out = root / "out"; out.mkdir()
            children = [self._child_repo(root, f"child-{i}")[1] for i in range(2)]
            for i, revision in enumerate(children):
                path = f"empty-{i}"; (repo / path).mkdir()
                subprocess.run(["git", "clone", "-q", str(root / f"child-{i}"), str(repo / path)], check=True)
                self._add_gitlink(repo, revision, path)
            revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            cache = root / "cached"; subprocess.run(["git", "clone", "-q", str(repo), str(cache)], check=True)
            for i in range(2):
                subprocess.run(["git", "clone", "-q", str(root / f"child-{i}"), str(cache / f"empty-{i}")], check=True)
            result = json.loads(self._ruby(self._cached_resource_script(repo, revision, cache, out)).splitlines()[-1])
            self.assertTrue(Path(result["path"]).is_file())

    def test_exports_submodule_content_and_rejects_wrong_submodule_head(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); child = root / "child"; repo, _ = self._repo(root); out = root / "out"; out.mkdir()
            subprocess.run(["git", "init", "-q", str(child)], check=True)
            for key, value in (("user.email", "test@example.invalid"), ("user.name", "Test"), ("commit.gpgsign", "false")):
                subprocess.run(["git", "-C", str(child), "config", key, value], check=True)
            (child / "CHILD").write_text("child\n")
            subprocess.run(["git", "-C", str(child), "add", "CHILD"], check=True)
            subprocess.run(["git", "-C", str(child), "commit", "-q", "-m", "child"], check=True)
            child_revision = subprocess.check_output(["git", "-C", str(child), "rev-parse", "HEAD"], text=True).strip()
            subprocess.run(["git", "-C", str(child), "commit", "--allow-empty", "-q", "-m", "second"], check=True)
            child_second = subprocess.check_output(["git", "-C", str(child), "rev-parse", "HEAD"], text=True).strip()
            subpath = "fixture"
            subprocess.run(["git", "-C", str(repo), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(child), subpath], check=True)
            subprocess.run(["git", "-C", str(repo / subpath), "checkout", "-q", child_revision], check=True)
            subprocess.run(["git", "-C", str(repo), "add", ".gitmodules", subpath], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "submodule"], check=True)
            revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            cache = root / "cached"; subprocess.run(["git", "clone", "-q", str(repo), str(cache)], check=True)
            subprocess.run(["git", "-C", str(cache), "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive"], check=True)
            fake = f'''require "json"; require "pathname"; load {json.dumps(str(ROOT / "libexec/git_sources.rb"))}
d = Struct.new(:cached_location).new(Pathname.new({json.dumps(str(cache))})); r = Struct.new(:download_strategy, :specs, :downloader, :url).new(GitDownloadStrategy, {{revision: {json.dumps(revision)}}}, d, {json.dumps(repo.as_uri())}); def r.fetch; end
puts JSON.generate(GitSources.export(r, {json.dumps(str(out))}))'''
            result = json.loads(self._ruby(fake).splitlines()[-1])
            listing = subprocess.check_output(["tar", "-tf", result["path"]], text=True)
            self.assertIn(f"{subpath}/CHILD", listing.splitlines())
            subprocess.run(["git", "-C", str(cache / subpath), "checkout", "--detach", child_second], check=True)
            mismatch_out = root / "mismatch-out"; mismatch_out.mkdir()
            mismatch = "\n".join(line if not line.startswith("puts JSON.generate")
                                     else f"GitSources.export(r, {json.dumps(str(mismatch_out))})"
                                     for line in fake.splitlines())
            with self.assertRaisesRegex(RuntimeError, "submodule revision mismatch"):
                self._ruby(mismatch)

    def test_tag_only_and_non_git_resources_are_unsupported(self):
        script = f'''require "resource"; load {json.dumps(str(ROOT / "libexec/git_sources.rb"))}
tag = Resource.new("tag"); tag.url("https://example.invalid/source.git", using: :git, tag: "v1")
tar = Resource.new("tar"); tar.url("https://example.invalid/source.tar.gz", revision: "{'a' * 40}")
abort unless !GitSources.supported?(tag) && !GitSources.supported?(tar)'''
        self._ruby(script)


if __name__ == "__main__":
    unittest.main()
