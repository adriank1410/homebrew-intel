# SPDX-License-Identifier: BSD-2-Clause
"""Real Homebrew linker checks in a disposable prefix, never the user's prefix."""
import json
import os
import unittest

from intelbrew.core import ROOT, brew_env, run


@unittest.skipUnless(os.environ.get("INTELBREW_NATIVE_TESTS") == "1",
                     "set INTELBREW_NATIVE_TESTS=1 on Intel Sequoia")
class NativeLinkingTests(unittest.TestCase):
    def probe(self, body):
        script = r'''require "json"; require "stringio"; require "formula_installer"; require "tmpdir"
$stdin = StringIO.new('{"mode":"inspect","names":[]}')
load BRIDGE
FormulaInstaller.prepend(IntelbrewPreservePrefix)
formula = Formulary.factory("zlib")
Dir.mktmpdir("intelbrew-isolated-link-") do |work|
  prefix = Pathname(work)
  {HOMEBREW_PREFIX: prefix, HOMEBREW_CELLAR: prefix/"Cellar",
   HOMEBREW_LINKED_KEGS: prefix/"var/homebrew/linked", HOMEBREW_CACHE: prefix/"cache"}.each do |key, value|
    Object.send(:remove_const, key)
    Object.const_set(key, value)
    value.mkpath
  end
  %w[bin lib opt Frameworks].each { |dir| (prefix/dir).mkpath }
  root = HOMEBREW_CELLAR/"zlib"/"0"
  (root/"bin").mkpath; (root/"lib").mkpath
  File.write(root/"bin/probe", "keg")
  File.write(root/"lib/libprobe.dylib", "library")
  keg = Keg.new(root)
BODY
end
'''.replace("BRIDGE", json.dumps(str(ROOT / "libexec/native.rb"))).replace("BODY", body)
        output = run(["brew", "ruby", "-e", script], env=brew_env())
        return json.loads(output.splitlines()[-1])

    def test_prefix_conflict_respects_keg_only_and_skip_link(self):
        for options in ("", "link_keg: true, skip_link: true"):
            with self.subTest(options=options):
                result = self.probe('''
  File.write(prefix/"bin/probe", "foreign")
  installer = FormulaInstaller.new(formula, OPTIONS)
  installer.link(keg)
  puts JSON.generate({keg_only: formula.keg_only?, linked: keg.linked?,
    library_link: (prefix/"lib/libprobe.dylib").symlink?, opt: keg.optlinked?,
    foreign: File.read(prefix/"bin/probe"), original: File.read(root/"bin/probe")})
'''.replace("OPTIONS", options))
                self.assertTrue(result["keg_only"])
                self.assertFalse(result["linked"])
                self.assertFalse(result["library_link"])
                self.assertTrue(result["opt"])
                self.assertEqual(result["foreign"], "foreign")
                self.assertEqual(result["original"], "keg")

    def test_prefix_conflict_can_relink_and_unlink_the_same_keg(self):
        result = self.probe('''
  File.write(prefix/"bin/probe", "foreign")
  installer = FormulaInstaller.new(formula, link_keg: true)
  installer.link(keg)
  installer.link(keg)
  linked = keg.linked? && (prefix/"lib/libprobe.dylib").symlink?
  keg.unlink
  puts JSON.generate({linked: linked, unlinked: !keg.linked?,
    library_removed: !(prefix/"lib/libprobe.dylib").symlink?,
    foreign: File.read(prefix/"bin/probe"), original: File.read(root/"bin/probe"),
    held: (root/".intelbrew-held-prefix").exist?})
''')
        self.assertTrue(result["linked"])
        self.assertTrue(result["unlinked"])
        self.assertTrue(result["library_removed"])
        self.assertEqual(result["foreign"], "foreign")
        self.assertEqual(result["original"], "keg")
        self.assertFalse(result["held"])

    def test_framework_conflict_preserves_foreign_path_and_links_libraries(self):
        result = self.probe('''
  (root/"Frameworks/Probe.framework").mkpath
  File.write(root/"Frameworks/Probe.framework/Probe", "framework")
  (prefix/"foreign-framework").mkpath
  File.symlink(prefix/"foreign-framework", prefix/"Frameworks/Probe.framework")
  installer = FormulaInstaller.new(formula, link_keg: true)
  installer.link(keg)
  linked = keg.linked? && (prefix/"lib/libprobe.dylib").symlink?
  foreign = File.readlink(prefix/"Frameworks/Probe.framework") == (prefix/"foreign-framework").to_s
  keg.unlink
  puts JSON.generate({linked: linked, foreign: foreign,
    original: File.read(root/"Frameworks/Probe.framework/Probe"),
    unlinked: !keg.linked?, held: (root/".intelbrew-held-prefix").exist?})
''')
        self.assertTrue(result["linked"])
        self.assertTrue(result["foreign"])
        self.assertTrue(result["unlinked"])
        self.assertEqual(result["original"], "framework")
        self.assertFalse(result["held"])

    def test_staging_restores_prior_entries_after_errors_and_interrupts(self):
        for error in ("RuntimeError", "Interrupt"):
            with self.subTest(error=error):
                result = self.probe('''
  File.write(prefix/"bin/probe", "foreign")
  File.write(root/"bin/second", "second keg file")
  File.write(prefix/"bin/second", "second foreign file")
  # The move boundary raises after one successful move, as a filesystem
  # failure or Ctrl-C can do. All other moves use real FileUtils and files.
  failure = Module.new do
    define_method(:mv) do |src, dst, **options|
      raise ERROR_CLASS, "staging interrupted" if src.to_s == (root/"bin/second").to_s
      super(src, dst, **options)
    end
  end
  FileUtils.singleton_class.prepend(failure)
  begin
    IntelbrewNative.hold_prefix_conflicts(keg, [prefix/"bin/probe", prefix/"bin/second"])
    abort "expected staging failure"
  rescue ERROR_CLASS
  end
  puts JSON.generate({restored: (root/"bin/probe").file?,
    second: File.read(root/"bin/second"), foreign: File.read(prefix/"bin/probe"),
    held: (root/".intelbrew-held-prefix").exist?})
'''.replace("ERROR_CLASS", error))
                self.assertTrue(result["restored"])
                self.assertEqual(result["second"], "second keg file")
                self.assertEqual(result["foreign"], "foreign")
                self.assertFalse(result["held"])

    def test_foreign_directory_and_symlink_are_preserved_through_link_lifecycle(self):
        result = self.probe('''
  (prefix/"foreign").mkpath
  (prefix/"share").mkpath
  (root/"share/probe").mkpath
  File.write(root/"share/probe/content", "keg directory")
  File.symlink(prefix/"foreign", prefix/"share/probe")
  File.symlink("/usr/bin/true", prefix/"bin/probe")
  File.symlink(prefix/"missing", prefix/"broken")
  File.symlink(root, prefix/"other-keg")
  classification = [prefix/"share/probe", prefix/"bin/probe", prefix/"broken", prefix/"other-keg"].map do |path|
    IntelbrewNative.preserve_prefix_path?(path)
  end
  installer = FormulaInstaller.new(formula, link_keg: true)
  installer.link(keg)
  linked = keg.linked? && (prefix/"lib/libprobe.dylib").symlink?
  keg.unlink
  puts JSON.generate({linked: linked, classification: classification,
    bin: File.readlink(prefix/"bin/probe"),
    directory: File.readlink(prefix/"share/probe") == (prefix/"foreign").to_s,
    original: File.read(root/"share/probe/content"), held: (root/".intelbrew-held-prefix").exist?})
''')
        self.assertTrue(result["linked"])
        self.assertEqual(result["classification"], [True, True, False, False])
        self.assertEqual(result["bin"], "/usr/bin/true")
        self.assertTrue(result["directory"])
        self.assertEqual(result["original"], "keg directory")
        self.assertFalse(result["held"])

    def test_sigint_after_move_does_not_strand_the_unrecorded_entry(self):
        result = self.probe('''
  File.write(prefix/"bin/probe", "foreign")
  interrupt_move = Module.new do
    define_method(:mv) do |src, dst, **options|
      result = super(src, dst, **options)
      if src.to_s == (root/"bin/probe").to_s
        Process.kill("INT", Process.pid)
        sleep 0.01
      end
      result
    end
  end
  FileUtils.singleton_class.prepend(interrupt_move)
  begin
    IntelbrewNative.hold_prefix_conflicts(keg, [prefix/"bin/probe"])
    abort "expected SIGINT"
  rescue Interrupt
  end
  puts JSON.generate({restored: (root/"bin/probe").file?,
    foreign: File.read(prefix/"bin/probe"), held: (root/".intelbrew-held-prefix").exist?})
''')
        self.assertTrue(result["restored"])
        self.assertEqual(result["foreign"], "foreign")
        self.assertFalse(result["held"])
