# SPDX-License-Identifier: BSD-2-Clause
"""Opt-in integration checks against a real Intel Sequoia Homebrew installation."""
import json
import os
import unittest
from pathlib import Path

from intelbrew.core import ROOT, brew_env, digest, native, run
from intelbrew.ci import BOTTLE_OPTIONS


@unittest.skipUnless(os.environ.get("INTELBREW_NATIVE_TESTS") == "1",
                     "set INTELBREW_NATIVE_TESTS=1 on Intel Sequoia")
class NativeTests(unittest.TestCase):
    def test_verified_local_bottle_scope_uses_homebrew_opt_out(self):
        script = ('require ' + json.dumps(str(ROOT / 'libexec/bottle_only.rb')) + '; '
                  'before = ENV["HOMEBREW_INTERNAL_ALLOW_PACKAGES_FROM_PATHS"]; '
                  'IntelbrewBottleOnly.with_local_bottle do; '
                  'abort "local bottles still forbidden" if Homebrew::EnvConfig.forbid_packages_from_paths?; '
                  'end; abort "scope leaked" unless '
                  'ENV["HOMEBREW_INTERNAL_ALLOW_PACKAGES_FROM_PATHS"] == before')
        run(["brew", "ruby", "-e", script], env=brew_env())

    def test_local_bottle_scope_preserves_explicit_deny_and_restores_on_error(self):
        script = ('require ' + json.dumps(str(ROOT / 'libexec/bottle_only.rb')) + '; '
                  'before = ENV["HOMEBREW_INTERNAL_ALLOW_PACKAGES_FROM_PATHS"]; '
                  'with_env(HOMEBREW_FORBID_PACKAGES_FROM_PATHS: "1") do; '
                  'begin; IntelbrewBottleOnly.with_local_bottle { abort "explicit deny bypassed" }; '
                  'abort "expected refusal"; rescue RuntimeError => e; '
                  'raise unless e.message.start_with?("Explicit HOMEBREW_FORBID"); end; end; '
                  'begin; IntelbrewBottleOnly.with_local_bottle { raise "test failure" }; '
                  'rescue RuntimeError => e; raise unless e.message == "test failure"; end; '
                  'abort "scope leaked" unless ENV["HOMEBREW_INTERNAL_ALLOW_PACKAGES_FROM_PATHS"] == before')
        run(["brew", "ruby", "-e", script], env=brew_env())

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

    def test_real_coverage_separates_core_and_full_external_tap_names(self):
        result = native({"mode": "coverage"})
        self.assertEqual(set(result), {"core", "external_taps"})
        self.assertEqual(result["core"], sorted(set(result["core"])))
        self.assertEqual(result["external_taps"], sorted(set(result["external_taps"])))
        self.assertTrue(all("/" in name for name in result["external_taps"]))
