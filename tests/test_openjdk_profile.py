# SPDX-License-Identifier: BSD-2-Clause
"""The approved openjdk Intel boot-JDK variant swaps one pinned resource only."""
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import G, meta, record
from intelbrew import build_profiles
from intelbrew.build_profiles import (OPENJDK_BOOT_JDK_SHA256, OPENJDK_BOOT_JDK_URL,
                                      prepare_build_profile, recipe_sha256, variant_recipe)
from intelbrew.ci import _validate_source_bundle, build, source_bundle, validate_candidate
from intelbrew.core import (Error, LLVM_NO_PGO, OPENJDK_INTEL_BOOT_JDK_26, load_config,
                            validate_record)

PROFILE = OPENJDK_INTEL_BOOT_JDK_26
ARM_URL = ("https://download.java.net/java/GA/jdk27/55ce5470a6294008af0057ff4626d0e5/35/GPL/"
           "openjdk-27_macos-aarch64_bin.tar.gz")
ARM_SHA = "ff42134c1bc5ac81c5a91a95fd4df87479db5d0499ba875d6adb5bda5626c3e8"
LINUX = (b'    on_linux do\n'
         b'      on_intel do\n'
         b'        url "https://download.java.net/java/GA/jdk27/55ce5470a6294008af0057ff4626d0e5/35/GPL/openjdk-27_linux-x64_bin.tar.gz"\n'
         b'        sha256 "95fc37eb3a18a27a26d5904c2d89d52bace8dafa9a078ca27f4747fbc4bf070b"\n'
         b'      end\n'
         b'    end\n')
MACOS = (b'    on_macos do\n'
         b'      url "' + ARM_URL.encode() + b'"\n'
         b'      sha256 "' + ARM_SHA.encode() + b'"\n'
         b'    end\n')
# Mirrors the structure of homebrew-core's openjdk.rb since 27.0.1 (a7f6b0e).
FORMULA = (b'class Openjdk < Formula\n'
           b'  desc "Development kit for the Java programming language"\n'
           b'  url "https://github.com/openjdk/jdk27u/archive/refs/tags/jdk-27.0.1-ga.tar.gz"\n'
           b'  sha256 "e97f539ba547ab533b511ce1f937211c1e9795e7c1092321f8ba23c658a11dd9"\n'
           b'  license "GPL-2.0-only" => { with: "Classpath-exception-2.0" }\n\n'
           b'  bottle do\n'
           b'    sha256 cellar: :any, arm64_sequoia: "d5ce6b2e54adb56c443e6c9fa8083fc4b7d6ea3b494fb7f2ad822cafb271c344"\n'
           b'  end\n\n'
           b'  # From https://jdk.java.net/archive/\n'
           b'  resource "boot-jdk" do\n' + MACOS + LINUX +
           b'  end\n\n'
           b'  def install\n'
           b'    resource("boot-jdk").stage buildpath/"boot-jdk"\n'
           b'    system "make", "images"\n'
           b'  end\n'
           b'end\n')
INTEL_MACOS = (b'    on_macos do\n'
               b"      # Intelbrew openjdk-intel-boot-jdk-26 profile: upstream's macOS boot JDK is AArch64-only.\n"
               b'      url "' + OPENJDK_BOOT_JDK_URL.encode() + b'"\n'
               b'      sha256 "' + OPENJDK_BOOT_JDK_SHA256.encode() + b'"\n'
               b'    end\n')
VARIANT = FORMULA.replace(MACOS, INTEL_MACOS)


class OpenjdkVariantRecipeTests(unittest.TestCase):
    def test_pinned_boot_jdk_is_the_official_x64_jdk_26_0_2_1(self):
        self.assertEqual(OPENJDK_BOOT_JDK_URL,
                         "https://download.java.net/java/GA/jdk26.0.2.1/3b8e6c7ec6274148a7aa15e7e7dfb53c/1/GPL/"
                         "openjdk-26.0.2.1_macos-x64_bin.tar.gz")
        self.assertEqual(OPENJDK_BOOT_JDK_SHA256,
                         "5ec3817530ffa1c38f15a55609a2767335bbd0c59ea8ce41aad68d3cd620c0ae")

    def test_variant_replaces_only_the_macos_boot_jdk(self):
        result = variant_recipe(FORMULA, PROFILE)
        self.assertEqual(result, VARIANT)
        self.assertNotIn(ARM_URL.encode(), result)
        self.assertEqual(result.count(LINUX), 1)
        self.assertEqual(result.count(b'system "make", "images"'), 1)

    def test_variant_keeps_applying_to_later_jdk27_updates(self):
        bumped = (FORMULA.replace(b"jdk-27.0.1-ga", b"jdk-27.0.1.0.2-ga")
                  .replace(b"jdk27/55ce5470a6294008af0057ff4626d0e5/35", b"jdk27.0.1/0123456789abcdef0123456789abcdef/9")
                  .replace(b"openjdk-27_macos", b"openjdk-27.0.1_macos")
                  .replace(ARM_SHA.encode(), b"1" * 64))
        result = variant_recipe(bumped, PROFILE)
        self.assertIn(OPENJDK_BOOT_JDK_URL.encode(), result)
        self.assertIn(b"jdk-27.0.1.0.2-ga", result)

    def test_variant_fails_closed_when_upstream_structure_changes(self):
        restored_intel = MACOS.replace(
            b'    on_macos do\n',
            b'    on_macos do\n      on_intel do\n        url "x"\n        sha256 "' + b"2" * 64 + b'"\n      end\n')
        cases = {
            "next feature release": FORMULA.replace(b"jdk27u/archive/refs/tags/jdk-27.0.1-ga",
                                                    b"jdk28u/archive/refs/tags/jdk-28-ga"),
            "upstream Intel boot JDK restored": FORMULA.replace(MACOS, restored_intel),
            "x64 boot JDK upstream": FORMULA.replace(b"_macos-aarch64_bin", b"_macos-x64_bin"),
            "JDK 28 boot JDK": FORMULA.replace(b"/GA/jdk27/", b"/GA/jdk28/").replace(b"openjdk-27_", b"openjdk-28_"),
            "second boot-jdk resource": FORMULA.replace(b"  def install\n",
                                                        b'  resource "boot-jdk" do\n' + MACOS + b"  end\n\n  def install\n"),
            "renamed class": FORMULA.replace(b"class Openjdk < Formula", b"class OpenjdkAT27 < Formula"),
            "missing sha": FORMULA.replace(b'      sha256 "' + ARM_SHA.encode() + b'"\n', b""),
        }
        for label, upstream in cases.items():
            with self.subTest(label), self.assertRaisesRegex(Error, "review needed|Unexpected"):
                variant_recipe(upstream, PROFILE)

    def test_profiles_are_not_interchangeable(self):
        with self.assertRaises(Error):
            variant_recipe(FORMULA, LLVM_NO_PGO)
        with self.assertRaises(Error):
            variant_recipe(FORMULA, "unknown")

    def test_context_restores_core_recipe_and_names_artifacts_by_profile(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            recipe = base / "openjdk.rb"; recipe.write_bytes(FORMULA)
            sources = {"formula_path": str(recipe), "formula_sha256": hashlib.sha256(FORMULA).hexdigest(),
                       "resources": []}
            with prepare_build_profile("openjdk", sources, base / "work", PROFILE) as prepared:
                self.assertEqual(recipe.read_bytes(), VARIANT)
                self.assertEqual(Path(prepared["formula_path"]).name, "openjdk-core.rb")
                self.assertEqual(Path(prepared["profile_recipe_path"]).name, "openjdk-intel-boot-jdk-26.rb")
                self.assertEqual(prepared["recipe_sha256"], recipe_sha256(VARIANT))
            self.assertEqual(recipe.read_bytes(), FORMULA)
            for name, profile in (("openjdk", LLVM_NO_PGO), ("llvm", PROFILE), ("openjdk@21", PROFILE)):
                with self.subTest(name=name, profile=profile), self.assertRaises(Error):
                    with prepare_build_profile(name, sources, base / f"w-{name}-{profile}", profile):
                        self.fail("mismatched profile must fail closed")
            self.assertEqual(recipe.read_bytes(), FORMULA)


class OpenjdkProfilePolicyTests(unittest.TestCase):
    def test_config_enables_the_profile_only_for_openjdk(self):
        config = load_config()
        self.assertEqual(config["build_profiles"]["openjdk"], PROFILE)
        for value in ({"openjdk": LLVM_NO_PGO}, {"llvm": PROFILE}, {"openjdk@21": PROFILE},
                      {"openjdk": PROFILE, "rust": PROFILE}):
            with self.subTest(profiles=value), patch("intelbrew.core.read_json",
                    return_value={**config, "build_profiles": value}), self.assertRaises(Error):
                load_config()

    def test_record_accepts_profile_only_for_openjdk(self):
        item = record("openjdk", build_profile=PROFILE)
        self.assertIs(validate_record(item), item)
        for name, profile in (("llvm", PROFILE), ("openjdk", LLVM_NO_PGO), ("openjdk", ["x"])):
            with self.subTest(name=name, profile=profile), self.assertRaises(Error):
                validate_record(record(name, build_profile=profile))


class OpenjdkProfileBuildTests(unittest.TestCase):
    LICENSE = "GPL-2.0-only WITH Classpath-exception-2.0"

    @staticmethod
    def tarball(path, data):
        with tarfile.open(path, "w:gz") as archive:
            member = tarfile.TarInfo("jdk/Contents/Home/release"); member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        return path

    def files(self, folder):
        source = folder / "jdk-27.0.1-ga.tar.gz"
        with tarfile.open(source, "w:gz") as archive:
            data = b"GPL-2.0 with Classpath exception"
            member = tarfile.TarInfo("jdk27u-jdk-27.0.1-ga/LICENSE"); member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        arm = self.tarball(folder / "openjdk-27_macos-aarch64_bin.tar.gz", b"arm64 boot jdk")
        x64 = self.tarball(folder / "openjdk-26.0.2.1_macos-x64_bin.tar.gz", b"x64 boot jdk")
        main = {"label": "main", "path": str(source), "url": "https://example.test/jdk-27.0.1-ga.tar.gz",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        def boot(path, url):
            return {"label": "resource-boot-jdk", "path": str(path), "url": url,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return main, boot(arm, ARM_URL), boot(x64, OPENJDK_BOOT_JDK_URL)

    def run_build(self, folder, *, variant_boot=None):
        original_path = folder / "core" / "openjdk.rb"; original_path.parent.mkdir()
        original_path.write_bytes(FORMULA)
        main, arm_boot, x64_boot = self.files(folder)
        sha = hashlib.sha256(FORMULA).hexdigest()
        item = meta("openjdk", license=self.LICENSE, formula_sha256=sha)
        cache = folder / "homebrew-cache"
        context = {"homebrew_cache": str(cache), "go_mod_cache": str(cache / "go_mod_cache"),
                   "cargo_cache": str(cache / "cargo_cache")}
        for value in context.values(): Path(value).mkdir(parents=True, exist_ok=True)
        executed = []

        def native_io(request, **kwargs):
            if request["mode"] == "inspect": return {"openjdk": item}
            if request["mode"] == "sources":
                text = original_path.read_bytes()
                boot = arm_boot if text == FORMULA else (variant_boot or x64_boot)
                return {"formula_path": str(original_path), "formula_sha256": hashlib.sha256(text).hexdigest(),
                        "recipe_sha256": recipe_sha256(text), "resources": [main, boot]}
            if request["mode"] == "build-context": return context
            raise AssertionError(request)

        def brew_io(args, **kwargs):
            if args[1:2] == ["install"]:
                self.assertIn("--build-bottle", args)
                executed.append(original_path.read_bytes())
                self.assertIn(OPENJDK_BOOT_JDK_URL.encode(), executed[-1])
            if args[1:2] == ["bottle"]:
                self.assertEqual(original_path.read_bytes(), FORMULA)
                cwd = Path(kwargs["cwd"])
                bottle = cwd / "openjdk--1.0.sequoia.bottle.tar.gz"
                # Homebrew embeds the executed recipe without its bottle block.
                embedded = build_profiles._BOTTLE_BLOCK.sub(b"", executed[-1])
                entries = [("openjdk/1.0/.brew/openjdk.rb", embedded),
                           ("openjdk/1.0/INSTALL_RECEIPT.json", json.dumps({
                               "source": {"tap": "homebrew/core"}, "built_as_bottle": True}).encode())]
                with tarfile.open(bottle, "w:gz") as archive:
                    for name, data in entries:
                        member = tarfile.TarInfo(name); member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
                (cwd / "openjdk.bottle.json").write_text(json.dumps({"formula": {"bottle": {
                    "cellar": ":any", "tags": {"sequoia": {
                        "sha256": hashlib.sha256(bottle.read_bytes()).hexdigest()}}}}}))
            return ""

        env = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
               "RUNNER_OS": "macOS", "RUNNER_ARCH": "X64", "RUNNER_TEMP": str(folder),
               "INTELBREW_CORE_COMMIT": G, "GITHUB_SHA": G, "GITHUB_RUN_ID": "123"}
        output = folder / "candidate"
        with patch.dict(os.environ, env), patch("intelbrew.ci.native", side_effect=native_io), \
             patch("intelbrew.ci.run", side_effect=brew_io), \
             patch("intelbrew.ci.sync_registry", return_value=G), \
             patch("intelbrew.ci.registry", return_value={}), \
             patch.object(build_profiles, "OPENJDK_BOOT_JDK_SHA256", x64_boot["sha256"]):
            build("openjdk", output)
            manifest = validate_candidate(output, expected_root="openjdk", verified=False)
        return output, manifest, executed, original_path, x64_boot

    def test_build_publishes_the_executed_boot_jdk_and_both_recipes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            output, manifest, executed, original_path, x64_boot = self.run_build(folder)
            rec = manifest["packages"][0]
            self.assertEqual(rec["build_profile"], PROFILE)
            self.assertEqual(rec["formula_sha256"], hashlib.sha256(FORMULA).hexdigest())
            self.assertEqual(rec["recipe_sha256"], recipe_sha256(VARIANT))
            self.assertEqual(executed, [VARIANT])
            self.assertEqual(original_path.read_bytes(), FORMULA)
            with tarfile.open(output / rec["source"]["filename"]) as archive:
                index = json.load(archive.extractfile("sources.json"))
                self.assertEqual(index["build_profile"], PROFILE)
                boots = [r for r in index["resources"] if r["label"] == "resource-boot-jdk"]
                self.assertEqual([(r["url"], r["sha256"]) for r in boots],
                                 [(OPENJDK_BOOT_JDK_URL, x64_boot["sha256"])])
                self.assertEqual(archive.extractfile("recipe/openjdk.rb").read(), FORMULA)
                self.assertEqual(archive.extractfile("recipe/openjdk-intel-boot-jdk-26.rb").read(), VARIANT)
                instructions = archive.extractfile("BUILDING.txt").read().decode()
                self.assertIn("openjdk-intel-boot-jdk-26", instructions)
                self.assertIn("Formula/o/openjdk.rb", instructions)

    def test_build_stops_before_compilation_when_the_boot_jdk_is_not_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            stray = self.tarball(folder / "other.tar.gz", b"unexpected")
            bad = {"label": "resource-boot-jdk", "path": str(stray), "url": OPENJDK_BOOT_JDK_URL,
                   "sha256": hashlib.sha256(stray.read_bytes()).hexdigest()}
            with self.assertRaisesRegex(Error, "review needed"):
                self.run_build(folder, variant_boot=bad)
            self.assertEqual((folder / "core" / "openjdk.rb").read_bytes(), FORMULA)

    def test_validation_rejects_an_unpinned_boot_jdk_in_the_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            canonical = folder / "openjdk-core.rb"; canonical.write_bytes(FORMULA)
            variant = folder / "openjdk-intel-boot-jdk-26.rb"; variant.write_bytes(VARIANT)
            main, arm_boot, x64_boot = self.files(folder)
            sha = hashlib.sha256(FORMULA).hexdigest()
            item = meta("openjdk", license=self.LICENSE, formula_sha256=sha)
            rec = record("openjdk", formula_sha256=sha, recipe_sha256=recipe_sha256(VARIANT),
                         build_profile=PROFILE, license=self.LICENSE)
            sources = {"formula_path": str(canonical), "formula_sha256": sha,
                       "recipe_sha256": recipe_sha256(VARIANT), "build_profile": PROFILE,
                       "profile_recipe_path": str(variant), "resources": [main, x64_boot]}
            with patch.object(build_profiles, "OPENJDK_BOOT_JDK_SHA256", x64_boot["sha256"]):
                with self.assertRaisesRegex(Error, "review needed"):
                    source_bundle("openjdk", item, {**sources, "resources": [main, arm_boot]},
                                  folder / "rejected", G, G, requirements=(self.LICENSE,))
                bundle = source_bundle("openjdk", item, sources, folder, G, G, requirements=(self.LICENSE,))
                _validate_source_bundle(bundle, rec, load_config())
            # Without the test-only digest the real pin no longer matches the fixture.
            with self.assertRaisesRegex(Error, "Build profile source mismatch"):
                _validate_source_bundle(bundle, rec, load_config())


if __name__ == "__main__":
    unittest.main()
