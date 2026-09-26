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

    def test_build_context_uses_homebrew_package_manager_caches(self):
        result = native({"mode": "build-context"})
        cache = Path(result["homebrew_cache"])
        self.assertEqual(Path(result["go_mod_cache"]), cache / "go_mod_cache")
        self.assertEqual(Path(result["cargo_cache"]), cache / "cargo_cache")

    def test_installed_versions_use_homebrew_pkg_version_order(self):
        script = f'''
require "json"; require "stringio"
$stdin = StringIO.new('{{"mode":"inspect","names":[]}}')
load {json.dumps(str(ROOT / "libexec/native.rb"))}
KegFixture = Struct.new(:version)
formula = Struct.new(:installed_kegs).new([
  KegFixture.new(PkgVersion.parse("1.10")),
  KegFixture.new(PkgVersion.parse("1.9"))
])
puts JSON.generate(IntelbrewNative.installed_versions(formula))
'''
        output = run(["brew", "ruby", "-e", script], env=brew_env())
        self.assertEqual(json.loads(output.splitlines()[-1]), ["1.9", "1.10"])

    def test_real_inspect_classifies_vcs_stable_sources_without_fetching(self):
        result = native({"mode": "inspect", "names": ["aom", "archi-steam-farm", "simdutf"]})
        self.assertTrue(all(type(result[name]["vcs_source"]) is bool for name in result))
        self.assertTrue(all(type(result[name]["pinned_git_source"]) is bool for name in result))
        self.assertTrue(all(type(result[name]["pinned_svn_source"]) is bool for name in result))

    def test_real_vcs_roots_are_rejected_before_dependency_inspection(self):
        inspected = native({"mode": "inspect", "names": ["aom", "archi-steam-farm"]})
        for root in ("aom", "archi-steam-farm"):
            if not inspected[root]["vcs_source"] or inspected[root]["official_bottle"] or inspected[root]["pinned_git_source"] or inspected[root]["pinned_svn_source"]:
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
pinned = Resource.new("pinned"); pinned.url("https://example.invalid/source.git", using: :git, revision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
pinned_svn = Resource.new("pinned_svn"); pinned_svn.url("https://example.invalid/source.svn", using: :svn, revision: "1234")
stable = Struct.new(:resource); patch = Struct.new(:resource)
formula = Struct.new(:stable, :resources, :patchlist)
cases = [
  formula.new(stable.new(curl), [], []),
  formula.new(stable.new(git), [], []),
  formula.new(stable.new(curl), [git], []),
  formula.new(stable.new(curl), [], [patch.new(git)])
]
pins = [formula.new(stable.new(pinned), [], []),
        formula.new(stable.new(curl), [pinned], []),
        formula.new(stable.new(curl), [], [patch.new(pinned)]),
        formula.new(stable.new(pinned), [git], [])]
svn_pins = [formula.new(stable.new(pinned_svn), [], []),
            formula.new(stable.new(curl), [pinned_svn], []),
            formula.new(stable.new(curl), [], [patch.new(pinned_svn)]),
            formula.new(stable.new(pinned_svn), [git], [])]
puts JSON.generate([cases.map {{ |item| IntelbrewNative.vcs_source?(item) }},
                    pins.map {{ |item| IntelbrewNative.pinned_git_source?(item) }},
                    svn_pins.map {{ |item| IntelbrewNative.pinned_svn_source?(item) }}])
'''
        output = run(["brew", "ruby", "-e", script], env=brew_env())
        self.assertEqual(json.loads(output.splitlines()[-1]), [
            [False, True, True, True],
            [True, True, True, False],
            [True, True, True, False]
        ])

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

    def test_foreign_prefix_conflict_links_everything_else(self):
        # A poured bottle must not replace an unmanaged prefix file. The rest
        # of that keg still has to be linked, or dependents cannot find it.
        script = f'''
require "json"; require "stringio"; require "formula_installer"; require "digest"
$stdin = StringIO.new('{{"mode":"inspect","names":[]}}')
load {json.dumps(str(ROOT / "libexec/native.rb"))}
def conflicts(name)
  f = Formulary.factory(name)
  return [] unless f.installed_kegs.any?
  keg = Keg.new(f.latest_installed_prefix.realpath)
  IntelbrewNative.prefix_link_conflicts(keg)
end
gpg = "/usr/local/bin/gpg"
has_gpg_symlink = File.symlink?(gpg)
before = has_gpg_symlink ? File.readlink(gpg) : nil
during = nil
restored = nil
FormulaInstaller.prepend(IntelbrewPreservePrefix)
if has_gpg_symlink && Formulary.factory("gnupg").installed_kegs.any?
  keg = Keg.new(Formulary.factory("gnupg").latest_installed_prefix.realpath)
  keg_gpg = Pathname(keg.to_path)/"bin/gpg"
  keg_agent = Pathname(keg.to_path)/"bin/gpg-agent"
  digest_before = Digest::SHA256.file(keg_gpg).hexdigest
  optlinked = false
  keg.define_singleton_method(:optlink) {{ |**_| optlinked = true }}
  keg.define_singleton_method(:link) do |**_|
    during = {{
      "gpg_visible" => keg_gpg.exist? || keg_gpg.symlink?,
      "agent_visible" => keg_agent.exist? || keg_agent.symlink?
    }}
  end
  installer = FormulaInstaller.allocate
  installer.define_singleton_method(:verbose?) {{ false }}
  installer.define_singleton_method(:overwrite?) {{ false }}
  installer.link(keg)
  restored = Digest::SHA256.file(keg_gpg).hexdigest == digest_before
  abort "link replaced #{{gpg}}" unless File.readlink(gpg) == before
  abort "optlink skipped" unless optlinked
  abort "remaining keg files were not linked" unless during
end
owned = Object.new
def owned.link_overwrite?(path) = Pathname(path).to_s == "/usr/local/bin/gpg"
puts JSON.generate({{
  "has_gpg_symlink" => has_gpg_symlink,
  "gnupg" => conflicts("gnupg"),
  "pinentry" => conflicts("pinentry"),
  "xz" => conflicts("xz"),
  "gpg" => before,
  "during" => during,
  "restored" => restored,
  "filtered" => IntelbrewNative.blocking_prefix_conflicts(["/usr/local/bin/gpg", "/usr/local/bin/other"], owned),
  "unfiltered" => IntelbrewNative.blocking_prefix_conflicts(["/usr/local/bin/gpg"], nil)
}})
'''
        result = json.loads(run(["brew", "ruby", "-e", script], env=brew_env()).splitlines()[-1])
        if result["has_gpg_symlink"]:
            self.assertEqual(result["gnupg"], ["/usr/local/bin/gpg"])
            self.assertEqual(result["gpg"], "/usr/local/MacGPG2/bin/gpg2")
            self.assertEqual(result["during"], {"gpg_visible": False, "agent_visible": True})
            self.assertTrue(result["restored"])
        self.assertEqual(result["pinentry"], [])
        self.assertEqual(result["xz"], [])
        self.assertEqual(result["filtered"], ["/usr/local/bin/other"])
        self.assertEqual(result["unfiltered"], ["/usr/local/bin/gpg"])

    def test_prefix_conflict_links_libraries_around_foreign_directories(self):
        # GitHub's Intel image keeps foreign directory symlinks such as
        # share/gettext and include/X11. Those paths stay put; libraries and
        # other keg files are still linked. A directory symlink into another
        # keg is left for Homebrew's own linker to merge.
        script = f'''
require "json"; require "stringio"; require "formula_installer"; require "tmpdir"; require "fileutils"
$stdin = StringIO.new('{{"mode":"inspect","names":[]}}')
load {json.dumps(str(ROOT / "libexec/native.rb"))}
FormulaInstaller.prepend(IntelbrewPreservePrefix)
probe_bin = HOMEBREW_PREFIX/"bin/intelbrew-link-probe"
probe_dir = HOMEBREW_PREFIX/"share/intelbrew-link-probe"
cellar_probe = HOMEBREW_CELLAR/"intelbrew-link-probe"/"0"
abort "probe already exists" if [probe_bin, probe_dir, cellar_probe.parent].any? {{ |path| path.exist? || path.symlink? }}
work = Pathname(Dir.mktmpdir("intelbrew-link-probe"))
foreign_dir = work/"image-gettext"
foreign_dir.mkpath
keg_root = work/"keg"
(keg_root/"bin").mkpath
(keg_root/"lib").mkpath
(keg_root/"share/intelbrew-link-probe").mkpath
File.write(keg_root/"bin/intelbrew-link-probe", "probe-bin")
File.write(keg_root/"lib/libintelbrew-link-probe.dylib", "probe-lib")
File.write(keg_root/"share/intelbrew-link-probe/msg", "probe-msg")
File.symlink("/usr/bin/true", probe_bin)
File.symlink(foreign_dir, probe_dir)
cellar_probe.mkpath
begin
  preserved = {{
    "foreign_dir" => IntelbrewNative.preserve_prefix_path?(probe_dir),
    "broken" => begin
      broken = work/"broken"
      File.symlink(work/"missing", broken)
      IntelbrewNative.preserve_prefix_path?(broken)
    end,
    "file" => IntelbrewNative.preserve_prefix_path?(probe_bin),
    "keg_dir" => begin
      keg_link = work/"keg-link"
      File.symlink(cellar_probe, keg_link)
      IntelbrewNative.preserve_prefix_path?(keg_link)
    end
  }}
  fake = Object.new
  optlinked = false
  during = nil
  fake.define_singleton_method(:to_path) {{ keg_root.to_s }}
  fake.define_singleton_method(:/) {{ |other| keg_root/other }}
  fake.define_singleton_method(:name) {{ "intelbrew-link-probe" }}
  fake.define_singleton_method(:optlink) {{ |**_| optlinked = true }}
  fake.define_singleton_method(:link) do |**_|
    during = {{
      "probe_visible" => (keg_root/"bin/intelbrew-link-probe").exist?,
      "library_visible" => (keg_root/"lib/libintelbrew-link-probe.dylib").exist?,
      "directory_visible" => (keg_root/"share/intelbrew-link-probe").exist?,
      "directory_child_visible" => (keg_root/"share/intelbrew-link-probe/msg").exist?
    }}
  end
  installer = FormulaInstaller.allocate
  installer.define_singleton_method(:verbose?) {{ false }}
  installer.define_singleton_method(:overwrite?) {{ false }}
  installer.link(fake)
  fake.define_singleton_method(:link) {{ |**_| raise "link failed" }}
  raised = false
  begin
    installer.link(fake)
  rescue RuntimeError => e
    raised = e.message == "link failed"
  end
  (cellar_probe/"bin").mkpath
  (cellar_probe/"lib").mkpath
  (cellar_probe/"share/intelbrew-link-probe").mkpath
  File.write(cellar_probe/"bin/intelbrew-link-kept", "kept")
  File.write(cellar_probe/"bin/intelbrew-link-probe", "probe-bin")
  File.write(cellar_probe/"lib/libintelbrew-link-probe.dylib", "probe-lib")
  File.write(cellar_probe/"share/intelbrew-link-probe/msg", "probe-msg")
  real = Keg.new(cellar_probe)
  installer.link(real)
  kept_link = HOMEBREW_PREFIX/"bin/intelbrew-link-kept"
  lib_link = HOMEBREW_PREFIX/"lib/libintelbrew-link-probe.dylib"
  real_result = {{
    "kept" => kept_link.symlink? && Utils::Path.resolved_path(kept_link).cleanpath == (cellar_probe/"bin/intelbrew-link-kept").cleanpath,
    "library" => lib_link.symlink? && Utils::Path.resolved_path(lib_link).cleanpath == (cellar_probe/"lib/libintelbrew-link-probe.dylib").cleanpath,
    "bin_untouched" => File.readlink(probe_bin) == "/usr/bin/true",
    "dir_untouched" => File.readlink(probe_dir) == foreign_dir.to_s,
    "probe_restored" => File.read(cellar_probe/"bin/intelbrew-link-probe") == "probe-bin",
    "msg_restored" => File.read(cellar_probe/"share/intelbrew-link-probe/msg") == "probe-msg"
  }}
  real.unlink
  puts JSON.generate({{
    "preserved" => preserved,
    "during" => during,
    "optlinked" => optlinked,
    "prefix_bin" => File.readlink(probe_bin),
    "prefix_dir" => File.readlink(probe_dir),
    "restored_probe" => File.read(keg_root/"bin/intelbrew-link-probe"),
    "restored_directory" => File.read(keg_root/"share/intelbrew-link-probe/msg"),
    "hold_left" => (keg_root/".intelbrew-held-prefix").exist?,
    "raised" => raised,
    "restored_after_error" => File.read(keg_root/"bin/intelbrew-link-probe"),
    "real" => real_result
  }})
ensure
  if cellar_probe.directory?
    begin
      installed = Keg.new(cellar_probe)
      installed.unlink
      installed.opt_record.delete if installed.opt_record.symlink? || installed.opt_record.exist?
      installed.linked_keg_record.delete if installed.linked_keg_record.symlink? || installed.linked_keg_record.directory?
    rescue StandardError
    end
  end
  [HOMEBREW_PREFIX/"bin/intelbrew-link-kept", HOMEBREW_PREFIX/"lib/libintelbrew-link-probe.dylib"].each do |link|
    next unless link.symlink?
    link.unlink if Utils::Path.resolved_path(link).to_s.include?("/intelbrew-link-probe/")
  end
  probe_bin.unlink if probe_bin.symlink? && File.readlink(probe_bin) == "/usr/bin/true"
  probe_dir.unlink if probe_dir.symlink? && File.readlink(probe_dir) == foreign_dir.to_s
  FileUtils.rm_rf cellar_probe.parent
  FileUtils.rm_rf work
end
'''
        result = json.loads(run(["brew", "ruby", "-e", script], env=brew_env()).splitlines()[-1])
        self.assertEqual(result["preserved"], {
            "foreign_dir": True,
            "broken": False,
            "file": True,
            "keg_dir": False,
        })
        self.assertEqual(result["during"], {
            "probe_visible": False,
            "library_visible": True,
            "directory_visible": False,
            "directory_child_visible": False,
        })
        self.assertTrue(result["optlinked"])
        self.assertEqual(result["prefix_bin"], "/usr/bin/true")
        self.assertTrue(result["prefix_dir"].endswith("image-gettext"))
        self.assertEqual(result["restored_probe"], "probe-bin")
        self.assertEqual(result["restored_directory"], "probe-msg")
        self.assertFalse(result["hold_left"])
        self.assertTrue(result["raised"])
        self.assertEqual(result["restored_after_error"], "probe-bin")
        self.assertEqual(result["real"], {
            "kept": True,
            "library": True,
            "bin_untouched": True,
            "dir_untouched": True,
            "probe_restored": True,
            "msg_restored": True,
        })
        self.assertFalse((Path("/usr/local/bin/intelbrew-link-probe")).exists())
        self.assertFalse((Path("/usr/local/share/intelbrew-link-probe")).exists())
        self.assertFalse((Path("/usr/local/Cellar/intelbrew-link-probe")).exists())


class InstallConflictSourceTests(unittest.TestCase):
    def test_bottle_install_preserves_foreign_prefix_files(self):
        source = (ROOT / "libexec/native.rb").read_text()
        install = source.split("def bottle_only_install", 1)[1].split("\n  def ", 1)[0]
        self.assertIn("IntelbrewPreservePrefix", install)
        self.assertIn("prefix_link_conflicts", source)
        self.assertNotIn("--overwrite", install)
