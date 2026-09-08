# SPDX-License-Identifier: BSD-2-Clause
"""Opt-in integration checks against a real Intel Sequoia Homebrew installation."""
import json
import os
import unittest
from pathlib import Path

from intelbrew.core import brew_env, digest, native, run
from intelbrew.ci import BOTTLE_OPTIONS


@unittest.skipUnless(os.environ.get("INTELBREW_NATIVE_TESTS") == "1",
                     "set INTELBREW_NATIVE_TESTS=1 on Intel Sequoia")
class NativeTests(unittest.TestCase):
    def test_ci_bottle_options_are_accepted_by_homebrew(self):
        # Parse the actual CI options without running the packaging operation.
        options = json.dumps([*BOTTLE_OPTIONS, "homebrew/core/simdutf"])
        run(["brew", "ruby", "-e",
             'require "dev-cmd/bottle"; Homebrew::DevCmd::Bottle.new(' + options + ')'],
            env=brew_env())

    def test_source_collection_verifies_real_simdutf_archive(self):
        sources = native({"mode": "sources", "name": "simdutf"})
        self.assertEqual(sources["formula_sha256"], digest(Path(sources["formula_path"])))
        self.assertTrue(sources["resources"])
        for resource in sources["resources"]:
            self.assertEqual(resource["sha256"], digest(Path(resource["path"])))

    def test_real_installer_source_guard(self):
        self.assertEqual(native({"mode": "guard-test"}), {"guard": "passed"})
