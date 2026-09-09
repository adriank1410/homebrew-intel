import tempfile
import unittest
import json
import subprocess
from pathlib import Path

from intelbrew.build_sources import collect_build_sources
from intelbrew.core import Error


class BuildSourcesTests(unittest.TestCase):
    def test_collects_go_and_cargo_sources_without_duplicate_registry_or_credentials(self):
        with tempfile.TemporaryDirectory() as raw:
            cache = Path(raw) / "cache"; go = cache / "go_mod_cache"; cargo = cache / "cargo_cache"
            files = {
                go / "pkg/mod/cache/download/example.org/mod/@v/v1.0.0.zip": b"zip",
                go / "pkg/mod/cache/download/example.org/mod/@v/v1.0.0.mod": b"module",
                go / "pkg/mod/cache/download/example.org/mod/@v/v1.0.0.info": b"info",
                go / "pkg/mod/cache/download/example.org/mod/@v/v1.0.0.ziphash": b"hash",
                go / "pkg/mod/cache/download/example.org/target/@v/v1.0.0.zip": b"target module",
                cargo / "registry/cache/index/pkg-1.0.0.crate": b"crate",
                cargo / "registry/src/index/pkg-1.0.0/src/lib.rs": b"source",
                cargo / "credentials.toml": b"secret",
            }
            for path, data in files.items(): path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            checkout = cargo / "git/checkouts/repo/rev"; checkout.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.name", "test"], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "commit.gpgsign", "false"], check=True)
            subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin",
                            "https://github.com/example/project"], check=True)
            (checkout / "src").mkdir(); (checkout / "src/main.rs").write_bytes(b"git source")
            helper = checkout / "scripts/helper.sh"
            helper.parent.mkdir(); helper.write_bytes(b"#!/bin/sh\nexit 0\n"); helper.chmod(0o755)
            (checkout / "docs/target").mkdir(parents=True); (checkout / "docs/target/guide.md").write_bytes(b"docs")
            subprocess.run(["git", "-C", str(checkout), "add", "src/main.rs", "scripts/helper.sh",
                            "docs/target/guide.md"], check=True)
            subprocess.run(["git", "-C", str(checkout), "commit", "-q", "-m", "fixture"], check=True)
            result = collect_build_sources({"homebrew_cache": str(cache), "go_mod_cache": str(go),
                                            "cargo_cache": str(cargo)}, Path(raw) / "out")
            labels = [item["label"] for item in result["files"]]
            self.assertEqual(labels, sorted(labels))
            self.assertIn("go/pkg/mod/cache/download/example.org/mod/@v/v1.0.0.zip", labels)
            self.assertIn("go/pkg/mod/cache/download/example.org/mod/@v/v1.0.0.info", labels)
            self.assertIn("go/pkg/mod/cache/download/example.org/mod/@v/v1.0.0.ziphash", labels)
            self.assertIn("go/pkg/mod/cache/download/example.org/target/@v/v1.0.0.zip", labels)
            self.assertIn("cargo/registry/cache/index/pkg-1.0.0.crate", labels)
            self.assertIn("cargo/git/checkouts/repo/rev/src/main.rs", labels)
            self.assertIn("cargo/git/checkouts/repo/rev/docs/target/guide.md", labels)
            helper_record = next(item for item in result["files"]
                                 if item["label"] == "cargo/git/checkouts/repo/rev/scripts/helper.sh")
            self.assertEqual(helper_record["mode"], 0o755)
            self.assertEqual(Path(helper_record["path"]).stat().st_mode & 0o777, 0o755)
            self.assertTrue(all(item["mode"] in {0o644, 0o755} for item in result["files"]))
            revision = "cargo/git-revisions/repo/rev.json"
            self.assertIn(revision, labels)
            metadata = json.loads(Path(next(item["path"] for item in result["files"]
                                            if item["label"] == revision)).read_text())
            self.assertRegex(metadata["commit"], r"^[0-9a-f]{40}$")
            self.assertEqual(metadata["origin"], "https://github.com/example/project")
            self.assertFalse(any("registry/src" in item or "credentials" in item
                                 for item in labels))
            self.assertTrue(all(Path(item["path"]).read_bytes() for item in result["files"]))

    def test_excludes_git_file_and_rejects_broken_cache_symlink(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw); cache = base / "cache"; go = cache / "go_mod_cache"; cargo = cache / "cargo_cache"
            checkout = cargo / "git/checkouts/repo/rev"; checkout.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.name", "test"], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(checkout), "config", "commit.gpgsign", "false"], check=True)
            (checkout / "source").write_text("source")
            subprocess.run(["git", "-C", str(checkout), "add", "source"], check=True)
            subprocess.run(["git", "-C", str(checkout), "commit", "-q", "-m", "fixture"], check=True)
            git_dir = base / "checkout.git"; (checkout / ".git").rename(git_dir)
            (checkout / ".git").write_text(f"gitdir: {git_dir}\n")
            result = collect_build_sources({"homebrew_cache": str(cache), "go_mod_cache": str(go),
                                            "cargo_cache": str(cargo)}, base / "out")
            self.assertFalse(any(item["label"].endswith("/.git") for item in result["files"]))
            broken = go / "pkg/mod/cache/download"; broken.parent.mkdir(parents=True)
            broken.symlink_to(base / "missing")
            with self.assertRaisesRegex(Error, "Unsafe"):
                collect_build_sources({"homebrew_cache": str(cache), "go_mod_cache": str(go),
                                       "cargo_cache": str(cargo)}, base / "out2")

    def test_rejects_cache_escape_and_symlink_entries(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw); cache = base / "cache"; cache.mkdir()
            with self.assertRaisesRegex(Error, "escapes"):
                collect_build_sources({"homebrew_cache": str(cache), "go_mod_cache": str(base / "outside"),
                                       "cargo_cache": str(cache / "cargo")}, base / "out")
            go = cache / "go_mod_cache"; cargo = cache / "cargo_cache"; target = base / "source"; target.write_text("x")
            link = go / "pkg/mod/cache/download/link"; link.parent.mkdir(parents=True); link.symlink_to(target)
            with self.assertRaisesRegex(Error, "Unsafe"):
                collect_build_sources({"homebrew_cache": str(cache), "go_mod_cache": str(go),
                                       "cargo_cache": str(cargo)}, base / "out2")

    def test_rejects_intermediate_directory_symlink_escape(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw); cache = base / "cache"; go = cache / "go_mod_cache"; cargo = cache / "cargo_cache"
            outside = base / "outside/mod/cache/download"; outside.mkdir(parents=True)
            (outside / "source.zip").write_bytes(b"source")
            go.mkdir(parents=True); (go / "pkg").symlink_to(base / "outside")
            with self.assertRaisesRegex(Error, "Unsafe"):
                collect_build_sources({"homebrew_cache": str(cache), "go_mod_cache": str(go),
                                       "cargo_cache": str(cargo)}, base / "out")


if __name__ == "__main__": unittest.main()
