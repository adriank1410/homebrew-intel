# SPDX-License-Identifier: BSD-2-Clause
"""Regression coverage for the failures in Actions run 35743217955.

These tests exercise planning, not successful compilation of Qt or LLVM.
"""
import copy
import importlib.util
import unittest
from pathlib import Path

from helpers import meta, record
from intelbrew.ci import transient_builds
from intelbrew.core import Error, Planner, ensure_complete, load_config

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "native_plan_build_holds", ROOT / "scripts/native-plan.py")
native_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native_plan)


class Inspector:
    def __init__(self, nodes):
        self.nodes = nodes
        self.calls = []

    def prime(self, names):
        # The fixture already holds its complete metadata snapshot.
        pass

    def __call__(self, names):
        self.calls.append(list(names))
        return {name: copy.deepcopy(self.nodes[name]) for name in names}


class SourceBuildHoldTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.blocked = self.config["blocked_source_builds"]

    def plan(self, nodes, roots, records=None, *, build=True):
        return Planner(Inspector(nodes), records or {}, build=build,
                       blocked=self.blocked).make(roots)

    def test_incident_holds_are_explicit_and_do_not_block_all_qt_or_rust(self):
        for name in ("llvm", "qtbase"):
            self.assertIn(name, self.blocked)
        for name in ("deno", "rust", "qtsvg", "qtdeclarative", "qttools"):
            self.assertNotIn(name, self.blocked)

    def test_direct_source_holds_stop_before_fetching_dependency_metadata(self):
        for name in ("llvm", "qtbase"):
            with self.subTest(name=name):
                inspector = Inspector({name: meta(name, build=["must-not-inspect"])})
                with self.assertRaisesRegex(Error, f"Source build excluded by policy: {name}"):
                    Planner(inspector, {}, build=True, blocked=self.blocked).make([name])
                self.assertEqual(inspector.calls, [[name]])

    def test_deno_builds_its_compiler_toolchain_without_bottling_llvm(self):
        nodes = {
            "deno": meta("deno", build=["lld", "llvm", "rust"]),
            "lld": meta("lld", runtime=["llvm"]),
            "llvm": meta("llvm"),
            "rust": meta("rust", official=True),
        }
        plan = self.plan(nodes, ["deno"])
        self.assertEqual(transient_builds(plan), {"lld", "llvm"})
        self.assertEqual(plan["nodes"]["deno"]["provider"], "build")
        self.assertEqual(plan["nodes"]["rust"]["provider"], "official")

    def test_qt_dependents_cannot_reintroduce_the_held_source_build(self):
        nodes = {
            "qtsvg": meta("qtsvg", runtime=["qtbase"]),
            "qtbase": meta("qtbase", runtime=["md4c"]),
            "md4c": meta("md4c", official=True),
        }
        with self.assertRaisesRegex(Error, "Source build excluded by policy: qtbase"):
            self.plan(nodes, ["qtsvg"])

    def test_schedule_reports_holds_without_starving_an_eligible_sibling(self):
        nodes = {
            "deno": meta("deno", build=["llvm"]),
            "llvm": meta("llvm"),
            "qtbase": meta("qtbase"),
            "qtsvg": meta("qtsvg", runtime=["qtbase"]),
            "simdutf": meta("simdutf"),
        }
        selected, blocked = native_plan.roots_needing_build(
            ["deno", "qtbase", "qtsvg", "simdutf"], Inspector(nodes), {}, self.config)
        self.assertEqual(selected, ["simdutf", "deno"])
        self.assertEqual(set(blocked), {"qtbase", "qtsvg"})
        self.assertIn("qtbase", blocked["qtsvg"])

    def test_compatible_official_bottles_are_not_source_blocked(self):
        for name in ("llvm", "qtbase"):
            with self.subTest(name=name):
                result = self.plan({name: meta(name, official=True,
                                               build=["must-not-inspect"])}, [name])
                self.assertEqual(result["nodes"][name]["provider"], "official")
                self.assertEqual(result["order"], [name])

    def test_matching_personal_bottles_are_not_source_blocked(self):
        for name in ("llvm", "qtbase"):
            with self.subTest(name=name):
                result = self.plan({name: meta(name)}, [name], {name: record(name)})
                self.assertEqual(result["nodes"][name]["provider"], "personal")

    def test_a_verified_llvm_dependency_allows_deno_to_build(self):
        nodes = {"deno": meta("deno", build=["llvm"]), "llvm": meta("llvm")}
        result = self.plan(nodes, ["deno"], {"llvm": record("llvm")})
        self.assertEqual(result["nodes"]["llvm"]["provider"], "personal")
        self.assertEqual(result["nodes"]["deno"]["provider"], "build")

    def test_dependency_drift_cannot_bypass_the_hold_on_recursive_replanning(self):
        nodes = {
            "qtbase": meta("qtbase", runtime=["md4c"]),
            "md4c": meta("md4c", official=True),
        }
        old = record("qtbase", runtime_dependencies=[{
            "name": "md4c", "pkg_version": "0.5.2", "formula_sha256": "a" * 64,
        }])
        with self.assertRaisesRegex(Error, "Source build excluded by policy: qtbase"):
            self.plan(nodes, ["qtbase"], {"qtbase": old})

    def test_client_still_reports_missing_bottles_instead_of_success(self):
        for name in ("llvm", "qtbase"):
            with self.subTest(name=name):
                result = self.plan({name: meta(name)}, [name], build=False)
                self.assertEqual(result["nodes"][name]["provider"], "missing")
                with self.assertRaisesRegex(Error, "No compatible matching bottle"):
                    ensure_complete(result)


if __name__ == "__main__":
    unittest.main()
