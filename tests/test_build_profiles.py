# SPDX-License-Identifier: BSD-2-Clause
import hashlib
import tempfile
import unittest
from pathlib import Path

from intelbrew.build_profiles import prepare_build_profile, recipe_sha256, variant_recipe
from intelbrew.core import Error, LLVM_NO_PGO


ANCHOR = b"    pgo_build = build.stable? && build.bottle? && OS.mac? && !versioned_formula?\n"
FORMULA = (
    b"class Llvm < Formula\n"
    b"  # Keep all the upstream checks in this fixture.\n"
    b"  def install\n"
    b"    check 'check-all'\n"
    + ANCHOR
    + b"    lto_build = pgo_build && OS.mac?\n"
    + b"    check 'check-clang'\n"
    + b"  end\n"
    + b"end\n"
    + b"\n"
    + b"  bottle do\n"
    + b"    sha256 cellar: :any, arm64_sonoma: \"a\" * 64\n"
    + b"  end\n"
    + b"\n"
)
VARIANT = FORMULA.replace(
    ANCHOR,
    b"    # Intelbrew LLVM no-PGO profile disables the expensive PGO bootstrap.\n"
    b"    pgo_build = false\n",
)


def source_fixture(formula_path: Path):
    formula_path.write_bytes(FORMULA)
    return {"formula_path": str(formula_path), "formula_sha256": hashlib.sha256(FORMULA).hexdigest(),
            "resources": [{"label": "llvm", "sha256": "1" * 64}]}


class BuildProfilesTests(unittest.TestCase):
    def test_variant_replaces_only_the_expected_pgo_line_and_keeps_checks(self):
        result = variant_recipe(FORMULA)
        self.assertEqual(result, VARIANT)
        self.assertEqual(result.count(b"check 'check-all'"), 1)
        self.assertEqual(result.count(b"check 'check-clang'"), 1)
        self.assertEqual(result.count(b"pgo_build = false"), 1)
        self.assertNotIn(ANCHOR, result)

    def test_variant_rejects_unexpected_or_ambiguous_upstream_recipes(self):
        with self.assertRaises(Error):
            variant_recipe(FORMULA.replace(ANCHOR, b"    pgo_build = true\n"))
        with self.assertRaises(Error):
            variant_recipe(FORMULA.replace(ANCHOR, ANCHOR + ANCHOR))
        with self.assertRaises(Error):
            variant_recipe(FORMULA.replace(b"class Llvm < Formula", b"class Other < Formula"))

    def test_recipe_digest_uses_the_native_bridge_bottle_stripping_rule(self):
        stripped = FORMULA.replace(FORMULA[FORMULA.index(b"  bottle do\n"):], b"")
        expected = hashlib.sha256(stripped).hexdigest()
        self.assertEqual(recipe_sha256(FORMULA), expected)

    def test_context_yields_fresh_canonical_and_variant_copies_then_restores_recipe(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            recipe = base / "llvm.rb"
            sources = source_fixture(recipe)
            sources_before = dict(sources)
            with prepare_build_profile("llvm", sources, base / "work", LLVM_NO_PGO) as prepared:
                canonical = Path(prepared["formula_path"])
                variant = Path(prepared["profile_recipe_path"])
                self.assertEqual(canonical.read_bytes(), FORMULA)
                self.assertEqual(variant.read_bytes(), VARIANT)
                self.assertEqual(recipe.read_bytes(), VARIANT)
                self.assertEqual(prepared["build_profile"], LLVM_NO_PGO)
                self.assertEqual(prepared["recipe_sha256"], recipe_sha256(VARIANT))
                self.assertEqual(prepared["formula_sha256"], sources["formula_sha256"])
                self.assertEqual(prepared["resources"], sources["resources"])
                self.assertEqual(sources, sources_before)
            self.assertEqual(recipe.read_bytes(), FORMULA)

    def test_context_restores_after_exception_and_keyboard_interrupt(self):
        for failure in (RuntimeError("fixture failure"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as raw:
                base = Path(raw)
                recipe = base / "llvm.rb"
                sources = source_fixture(recipe)
                with self.assertRaises(type(failure)):
                    with prepare_build_profile("llvm", sources, base / "work", LLVM_NO_PGO):
                        self.assertEqual(recipe.read_bytes(), VARIANT)
                        raise failure
                self.assertEqual(recipe.read_bytes(), FORMULA)

    def test_rejects_hash_mismatch_symlink_and_existing_work_outputs_before_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            recipe = base / "llvm.rb"
            sources = source_fixture(recipe)
            bad_hash = {**sources, "formula_sha256": "0" * 64}
            with self.assertRaises(Error):
                with prepare_build_profile("llvm", bad_hash, base / "bad-work", LLVM_NO_PGO):
                    self.fail("mismatched source must not enter the profile context")
            self.assertEqual(recipe.read_bytes(), FORMULA)
            self.assertFalse((base / "bad-work").exists())

            missing_sources = {**sources, "formula_path": str(base / "missing.rb")}
            with self.assertRaises(Error):
                with prepare_build_profile("llvm", missing_sources, base / "missing-work", LLVM_NO_PGO):
                    self.fail("missing local source recipe must not enter the profile context")
            self.assertFalse((base / "missing-work").exists())

            link = base / "linked.rb"
            link.symlink_to(recipe)
            symlink_sources = {**sources, "formula_path": str(link)}
            with self.assertRaises(Error):
                with prepare_build_profile("llvm", symlink_sources, base / "symlink-work", LLVM_NO_PGO):
                    self.fail("symlinked source must not enter the profile context")
            self.assertEqual(recipe.read_bytes(), FORMULA)

            occupied = base / "occupied"
            occupied.mkdir()
            (occupied / "llvm-core.rb").write_bytes(b"keep me")
            with self.assertRaises(Error):
                with prepare_build_profile("llvm", sources, occupied, LLVM_NO_PGO):
                    self.fail("profile outputs must not overwrite existing files")
            self.assertEqual(recipe.read_bytes(), FORMULA)
            self.assertEqual((occupied / "llvm-core.rb").read_bytes(), b"keep me")

    def test_rejects_unsupported_formula_and_profile_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            recipe = base / "llvm.rb"
            sources = source_fixture(recipe)
            for name, profile in (("clang", LLVM_NO_PGO), ("llvm", "another-profile")):
                with self.subTest(name=name, profile=profile), self.assertRaises(Error):
                    with prepare_build_profile(name, sources, base / f"work-{name}-{profile}", profile):
                        self.fail("unsupported profile requests must fail closed")
            self.assertEqual(recipe.read_bytes(), FORMULA)

    def test_detects_concurrent_recipe_edit_instead_of_clobbering_it(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            recipe = base / "llvm.rb"
            sources = source_fixture(recipe)
            unexpected = FORMULA + b"# concurrent edit\n"
            with self.assertRaises(Error):
                with prepare_build_profile("llvm", sources, base / "work", LLVM_NO_PGO):
                    recipe.write_bytes(unexpected)
            self.assertEqual(recipe.read_bytes(), unexpected)


if __name__ == "__main__":
    unittest.main()
