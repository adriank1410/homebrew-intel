# SPDX-License-Identifier: BSD-2-Clause
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from intelbrew.core import ROOT, brew_env, run


@unittest.skipUnless(os.environ.get("INTELBREW_NATIVE_TESTS") == "1" and shutil.which("brew"),
                     "set INTELBREW_NATIVE_TESTS=1 on a host with Homebrew")
class NativeSourcesTests(unittest.TestCase):
    def _ruby(self, script):
        return run(["brew", "ruby", "-e", script], env=brew_env())

    def test_normalize_rejects_tampering_outside_index_lines(self):
        raw = b"diff --git a/a b/a\nindex 0123456789abcd..fedcba98765432 100644\n-old\n+new\n"
        repaired = b"diff --git a/a b/a\nindex 0123456789abc..fedcba9876543 100644\n-old\n+new\n"
        expected = hashlib.sha256(repaired).hexdigest()
        tampered = raw.replace(b"-old", b"-tampered")
        script = f'''require "digest"; load {json.dumps(str(ROOT / "libexec/native_sources.rb"))}
raw = {json.dumps(tampered.decode())}.b
abort "tampering was accepted" if IntelbrewNativeSources.normalize(raw, {json.dumps(expected)})
puts "ok"'''
        self.assertEqual(self._ruby(script).splitlines()[-1], "ok")

    def test_fetch_preserves_oversized_and_symlinked_cache_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            script = f'''require "resource"; require "pathname"
load {json.dumps(str(ROOT / "libexec/native_sources.rb"))}
directory = Pathname.new({json.dumps(folder)})
raw = "index 0123456789abcd..fedcba98765432 100644\\n"
repaired = "index 0123456789abc..fedcba9876543 100644\\n"
regular = directory / "regular.patch"; regular.binwrite(raw)
link = directory / "link.patch"; File.symlink(regular.to_s, link.to_s)
large = directory / "large.patch"
padding = "x" * IntelbrewNativeSources::MAX_NORMALIZE_BYTES
large.binwrite(raw + padding)
[link, large].each do |cached|
  expected = Digest::SHA256.hexdigest(repaired + (cached == large ? padding : ""))
  resource = Object.new
  resource.define_singleton_method(:url) {{ "https://github.com/example/repo/commit/abc.patch" }}
  resource.define_singleton_method(:cached_download) {{ cached }}
  resource.define_singleton_method(:fetch) do |**_options|
    raise ChecksumMismatchError.new(cached, Checksum.new(expected), Checksum.new("0" * 64))
  end
  begin
    IntelbrewNativeSources.fetch(resource, verify_download_integrity: true)
    abort "unsafe cache entry accepted"
  rescue ChecksumMismatchError
    nil
  end
end
abort "link changed" unless link.symlink? && regular.binread == raw
abort "large cache changed" unless large.binread == raw + padding
abort "partial left behind" unless Dir.glob(directory.to_s + "/.*.partial").empty?
puts "ok"'''
            self.assertEqual(self._ruby(script).splitlines()[-1], "ok")

    def test_fetch_rewrites_cache_atomically_and_rechecks_with_homebrew(self):
        raw = b"diff --git a/a b/a\nindex 0123456789abcd..fedcba98765432 100644\n@@ -1 +1 @@\n-old\n+new\n"
        repaired = b"diff --git a/a b/a\nindex 0123456789abc..fedcba9876543 100644\n@@ -1 +1 @@\n-old\n+new\n"
        expected = hashlib.sha256(repaired).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "fixture.patch"
            cache.write_bytes(raw)
            cache.chmod(0o755)
            script = f'''require "json"; require "digest"; require "pathname"; require "resource"
load {json.dumps(str(ROOT / "libexec/native_sources.rb"))}
class FixtureResource
  attr_reader :calls, :url
  def initialize(path, expected)
    @path = Pathname.new(path); @expected = expected; @url = "https://github.com/example/project/compare/a...b.patch"; @calls = 0
  end
  def cached_download = @path
  def fetch(**options)
    @calls += 1
    if @calls == 1
      raise ChecksumMismatchError.new(@path, Checksum.new(@expected), Checksum.new(Digest::SHA256.file(@path).hexdigest))
    end
    abort "verification was disabled" unless options[:verify_download_integrity]
    Downloadable.verification_cache.verify(@path, Checksum.new(@expected))
    @path
  end
end
r = FixtureResource.new({json.dumps(str(cache))}, {json.dumps(expected)})
IntelbrewNativeSources.fetch(r, verify_download_integrity: true)
            puts JSON.generate({{"calls" => r.calls, "sha256" => Digest::SHA256.file(r.cached_download).hexdigest,
                    "mode" => r.cached_download.stat.mode & 0o777, "partials" => Dir.glob({json.dumps(str(cache.parent))} + "/.*.partial").length}})'''
            result = json.loads(self._ruby(script).splitlines()[-1])
            self.assertEqual(result, {"calls": 2, "sha256": expected, "mode": 0o755, "partials": 0})

    def test_rejects_wrong_expected_hash_and_unrelated_urls_without_mutating_cache(self):
        raw = b"diff --git a/a b/a\nindex 0123456789abcd..fedcba98765432 100644\n"
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "fixture.patch"
            cache.write_bytes(raw)
            before = hashlib.sha256(raw).hexdigest()
            zero_sha = "0" * 64
            script = f'''require "json"; require "digest"; require "pathname"; require "resource"
load {json.dumps(str(ROOT / "libexec/native_sources.rb"))}
class FixtureResource
  attr_reader :calls, :url
  def initialize(path, url, expected)
    @path = Pathname.new(path); @url = url; @expected = expected; @calls = 0
  end
  def cached_download = @path
  def fetch(**_options)
    @calls += 1
    raise ChecksumMismatchError.new(@path, Checksum.new(@expected), Checksum.new(Digest::SHA256.file(@path).hexdigest))
  end
end
items = [
  ["https://example.invalid/project/compare/a...b.patch", "non-github"],
  ["https://github.com/example/project/compare/a...b.tar.gz", "non-patch"],
  ["https://github.com/example/project/compare/a...b.patch", "wrong-hash"]
]
items.each do |url, label|
  r = FixtureResource.new({json.dumps(str(cache))}, url, {json.dumps(zero_sha)})
  begin
    IntelbrewNativeSources.fetch(r, verify_download_integrity: true)
    abort "#{{label}} unexpectedly recovered"
  rescue ChecksumMismatchError
    abort "#{{label}} retried" unless r.calls == 1
  end
end
puts JSON.generate("sha256" => Digest::SHA256.file({json.dumps(str(cache))}).hexdigest)
'''
            result = json.loads(self._ruby(script).splitlines()[-1])
            self.assertEqual(result, {"sha256": before})


if __name__ == "__main__":
    unittest.main()
