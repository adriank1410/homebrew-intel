# SPDX-License-Identifier: BSD-2-Clause
import json
import os
import tempfile
import unittest
from pathlib import Path

from intelbrew.core import ROOT, brew_env, run


@unittest.skipUnless(os.environ.get("INTELBREW_NATIVE_TESTS") == "1",
                     "set INTELBREW_NATIVE_TESTS=1 for brew ruby integration tests")
class SourceMirrorTests(unittest.TestCase):
    def _ruby(self, script, extra=None):
        environment = brew_env(); environment.update(extra or {})
        return run(["brew", "ruby", "-e", script], env=environment)

    def test_only_gnu_ftpmirror_gets_canonical_fallback(self):
        script = f'''require "json"; require "resource"; load {json.dumps(str(ROOT / "libexec/source_mirrors.rb"))}
good = Resource.new("good"); good.url("https://ftpmirror.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz")
SourceMirrors.add_gnu_fallback(good)
bad = Resource.new("bad"); bad.url("https://example.invalid/gnu/gmp.tar.xz")
puts JSON.generate([SourceMirrors.add_gnu_fallback(good), good.mirrors, SourceMirrors.add_gnu_fallback(bad), bad.mirrors])'''
        result = json.loads(self._ruby(script).splitlines()[-1])
        self.assertEqual(result, ["https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz",
                                  ["https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz"], None, []])

    @unittest.skipUnless(os.environ.get("INTELBREW_NETWORK_TESTS") == "1",
                         "set INTELBREW_NETWORK_TESTS=1 for canonical GNU download")
    def test_canonical_gmp_archive_matches_formula_checksum(self):
        with tempfile.TemporaryDirectory() as raw:
            script = f'''require "json"; require "digest"; require "pathname"; require "resource"
r = Resource.new("gmp"); r.define_singleton_method(:cache) {{ Pathname.new({json.dumps(raw)}) }}
r.url("https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz"); r.sha256("a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898"); r.fetch
puts JSON.generate([r.cached_download.to_s, Digest::SHA256.file(r.cached_download).hexdigest])'''
            path, checksum = json.loads(self._ruby(script, {"HOMEBREW_CACHE": raw}).splitlines()[-1])
            self.assertTrue(Path(path).is_file())
            self.assertEqual(checksum, "a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898")


if __name__ == "__main__":
    unittest.main()
