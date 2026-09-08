# SPDX-License-Identifier: BSD-2-Clause
"""Opt-in integration checks against a real Intel Sequoia Homebrew installation."""
import os
import unittest
from pathlib import Path

from intelbrew.core import digest, native


@unittest.skipUnless(os.environ.get("INTELBREW_NATIVE_TESTS") == "1",
                     "set INTELBREW_NATIVE_TESTS=1 on Intel Sequoia")
class NativeTests(unittest.TestCase):
    def test_source_collection_verifies_real_simdutf_archive(self):
        sources = native({"mode": "sources", "name": "simdutf"})
        self.assertEqual(sources["formula_sha256"], digest(Path(sources["formula_path"])))
        self.assertTrue(sources["resources"])
        for resource in sources["resources"]:
            self.assertEqual(resource["sha256"], digest(Path(resource["path"])))

    def test_real_installer_source_guard(self):
        self.assertEqual(native({"mode": "guard-test"}), {"guard": "passed"})
