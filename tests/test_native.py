# SPDX-License-Identifier: BSD-2-Clause
"""Opt-in integration checks against a real Intel Sequoia Homebrew installation."""
import json
import os
import unittest
from pathlib import Path

from intelbrew.core import ROOT, Error, Planner, brew_env, digest, native, run
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

    def test_real_inspect_classifies_vcs_stable_sources_without_fetching(self):
        result = native({"mode": "inspect", "names": ["aom", "archi-steam-farm", "simdutf"]})
        self.assertTrue(all(type(result[name]["vcs_source"]) is bool for name in result))

    def test_real_vcs_roots_are_rejected_before_dependency_inspection(self):
        inspected = native({"mode": "inspect", "names": ["aom", "archi-steam-farm"]})
        for root in ("aom", "archi-steam-farm"):
            if not inspected[root]["vcs_source"] or inspected[root]["official_bottle"]:
                continue
            calls = []
            def inspect(names):
                calls.append(list(names))
                return {name: inspected[name] for name in names}
            with self.subTest(root=root), self.assertRaisesRegex(Error, f"VCS source needs review: {root}"):
                Planner(inspect, {}, build=True).make([root])
            self.assertEqual(calls, [[root]])

    def test_homebrew_resource_objects_cover_main_named_and_patch_vcs_sources(self):
        script = f'''
require "json"; require "stringio"; require "resource"
$stdin = StringIO.new('{{"mode":"inspect","names":[]}}')
load {json.dumps(str(ROOT / "libexec/native.rb"))}
def resource(url, using=nil)
  Resource.new("fixture").tap {{ |r| using ? r.url(url, using: using) : r.url(url) }}
end
curl = resource("https://example.invalid/source.tar.gz")
git = resource("https://example.invalid/source.git", :git)
stable = Struct.new(:resource); patch = Struct.new(:resource)
formula = Struct.new(:stable, :resources, :patchlist)
cases = [
  formula.new(stable.new(curl), [], []),
  formula.new(stable.new(git), [], []),
  formula.new(stable.new(curl), [git], []),
  formula.new(stable.new(curl), [], [patch.new(git)])
]
puts JSON.generate(cases.map {{ |item| IntelbrewNative.vcs_source?(item) }})
'''
        output = run(["brew", "ruby", "-e", script], env=brew_env())
        self.assertEqual(json.loads(output.splitlines()[-1]), [False, True, True, True])

    def test_planner_rejects_missing_or_non_boolean_source_strategy_metadata(self):
        current = native({"mode": "inspect", "names": ["simdutf"]})["simdutf"]
        for value in (None, "false"):
            item = dict(current)
            if value is None:
                item.pop("vcs_source")
            else:
                item["vcs_source"] = value
            with self.subTest(value=value), self.assertRaisesRegex(Error, "Invalid source strategy metadata"):
                Planner(lambda names: {"simdutf": item}, {}, build=True).make(["simdutf"])
