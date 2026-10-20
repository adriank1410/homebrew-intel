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
from intelbrew.core import (
    Error, Planner, ensure_complete, load_config, source_build_held,
    validate_source_build_holds,
)

ROOT = Path(__file__).resolve().parents[1]

# The September 2026 qtbase and ocrmypdf recipe holds. Live policy drops them
# after publication; these tests keep the hashes so the mechanism stays covered.
INCIDENT_HOLDS = [
    {
        "name": "qtbase",
        "formula_sha256": "77fb639065c7f11b3c9c781a38013c2172f6c1fc3e5fe58efc1af975b6f861c5",
        "dependencies": [{
            "name": "md4c",
            "formula_sha256": "de1668120c0626d17e55981476fb6169f112606c43e4bc35991d50aff21666cd",
        }],
    },
    {
        "name": "ocrmypdf",
        "formula_sha256": "39b84c91d0b8af18ffcd5bddbcae49cabbac0ea6ac26e8d872083ead3f9f2499",
        "dependencies": [],
    },
]

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

    def plan(self, nodes, roots, records=None, *, build=True, holds=None):
        if holds is None:
            holds = self.config["source_build_holds"]
        return Planner(Inspector(nodes), records or {}, build=build,
                       blocked=self.blocked, holds=holds).make(roots)

    def held_qt_pair(self):
        hold = INCIDENT_HOLDS[0]
        md4c = hold["dependencies"][0]
        return {
            "qtbase": meta("qtbase", formula_sha256=hold["formula_sha256"], runtime=["md4c"]),
            "md4c": meta("md4c", formula_sha256=md4c["formula_sha256"], official=True),
        }

    def incident_config(self):
        config = copy.deepcopy(self.config)
        config["source_build_holds"] = copy.deepcopy(INCIDENT_HOLDS)
        return config

    def test_retired_publication_holds_leave_only_heavy_name_blocks(self):
        self.assertEqual(self.config["source_build_holds"], [])
        self.assertIn("llvm", self.blocked)
        self.assertIn("qt", self.blocked)
        self.assertIn("qtwebengine", self.blocked)
        self.assertIn("qtwebview", self.blocked)
        self.assertNotIn("qtbase", self.blocked)
        self.assertNotIn("ocrmypdf", self.blocked)
        for name in ("deno", "rust", "qtsvg", "qtdeclarative", "qttools"):
            self.assertNotIn(name, self.blocked)

    def test_direct_source_holds_stop_before_fetching_dependency_metadata(self):
        inspector = Inspector({"llvm": meta("llvm", build=["must-not-inspect"])})
        with self.assertRaisesRegex(Error, "Source build excluded by policy: llvm"):
            Planner(inspector, {}, build=True, blocked=self.blocked,
                    holds=self.config["source_build_holds"]).make(["llvm"])
        self.assertEqual(inspector.calls, [["llvm"]])

    def test_qtbase_recipe_hold_fetches_md4c_before_stopping(self):
        nodes = self.held_qt_pair()
        inspector = Inspector(nodes)
        with self.assertRaisesRegex(Error, "Source build excluded by policy: qtbase"):
            Planner(inspector, {}, build=True, blocked=self.blocked,
                    holds=INCIDENT_HOLDS).make(["qtbase"])
        self.assertEqual(inspector.calls, [["qtbase"], ["md4c"]])

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
        nodes = self.held_qt_pair()
        nodes["qtsvg"] = meta("qtsvg", runtime=["qtbase"])
        with self.assertRaisesRegex(Error, "Source build excluded by policy: qtbase"):
            self.plan(nodes, ["qtsvg"], holds=INCIDENT_HOLDS)

    def test_schedule_reports_holds_without_starving_an_eligible_sibling(self):
        nodes = {
            "deno": meta("deno", build=["llvm"]),
            "llvm": meta("llvm"),
            "qtbase": meta("qtbase", formula_sha256=INCIDENT_HOLDS[0]["formula_sha256"], runtime=["md4c"]),
            "md4c": meta("md4c", formula_sha256=INCIDENT_HOLDS[0]["dependencies"][0]["formula_sha256"], official=True),
            "qtsvg": meta("qtsvg", runtime=["qtbase"]),
            "simdutf": meta("simdutf"),
        }
        selected, blocked = native_plan.roots_needing_build(
            ["deno", "qtbase", "qtsvg", "simdutf"], Inspector(nodes), {}, self.incident_config())
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

    def test_qtbase_hold_expires_when_either_recipe_changes(self):
        hold = INCIDENT_HOLDS[0]
        md4c_sha = hold["dependencies"][0]["formula_sha256"]
        for changed in (
            {"qtbase": meta("qtbase", formula_sha256="b" * 64, runtime=["md4c"]),
             "md4c": meta("md4c", formula_sha256=md4c_sha, official=True)},
            {"qtbase": meta("qtbase", formula_sha256=hold["formula_sha256"], runtime=["md4c"]),
             "md4c": meta("md4c", formula_sha256="b" * 64, official=True)},
        ):
            with self.subTest(changed=sorted(changed)):
                result = self.plan(changed, ["qtbase"], holds=INCIDENT_HOLDS)
                self.assertEqual(result["nodes"]["qtbase"]["provider"], "build")

    def test_dependency_drift_cannot_bypass_the_hold_on_recursive_replanning(self):
        nodes = self.held_qt_pair()
        old = record("qtbase", formula_sha256=nodes["qtbase"]["formula_sha256"], runtime_dependencies=[{
            "name": "md4c", "pkg_version": "0.5.2", "formula_sha256": "a" * 64,
        }])
        with self.assertRaisesRegex(Error, "Source build excluded by policy: qtbase"):
            self.plan(nodes, ["qtbase"], {"qtbase": old}, holds=INCIDENT_HOLDS)

    def test_client_still_reports_missing_bottles_instead_of_success(self):
        for name in ("llvm", "qtbase"):
            with self.subTest(name=name):
                result = self.plan({name: meta(name)}, [name], build=False)
                self.assertEqual(result["nodes"][name]["provider"], "missing")
                with self.assertRaisesRegex(Error, "No compatible matching bottle"):
                    ensure_complete(result)

    def test_qualified_hold_names_are_normalized_without_mutating_config(self):
        nodes = self.held_qt_pair()
        for qualify_root, qualify_dependency in ((True, False), (False, True), (True, True)):
            with self.subTest(root=qualify_root, dependency=qualify_dependency):
                holds = copy.deepcopy(INCIDENT_HOLDS)
                if qualify_root:
                    holds[0]["name"] = "homebrew/core/qtbase"
                if qualify_dependency:
                    holds[0]["dependencies"][0]["name"] = "homebrew/core/md4c"
                original = copy.deepcopy(holds)
                with self.assertRaisesRegex(Error, "Source build excluded by policy: qtbase"):
                    Planner(Inspector(nodes), {}, build=True, holds=holds).make(["qtbase"])
                self.assertEqual(holds, original)

    def test_formula_only_hold_matches_that_recipe_file(self):
        sha = "c" * 64
        holds = validate_source_build_holds([{
            "name": "ocrmypdf",
            "formula_sha256": sha,
            "dependencies": [],
        }])
        nodes = {"ocrmypdf": {"formula_sha256": sha}}
        self.assertTrue(source_build_held(holds, "ocrmypdf", nodes))
        self.assertFalse(source_build_held(
            holds, "ocrmypdf", {"ocrmypdf": {"formula_sha256": "d" * 64}}))
        with self.assertRaisesRegex(Error, "Source build excluded by policy: ocrmypdf"):
            Planner(Inspector({"ocrmypdf": meta("ocrmypdf", formula_sha256=sha)}),
                    {}, build=True, holds=holds).make(["ocrmypdf"])

    def test_formula_file_hold_blocks_only_that_recipe(self):
        holds = [item for item in INCIDENT_HOLDS if item["name"] == "ocrmypdf"]
        self.assertEqual(holds, [{
            "name": "ocrmypdf",
            "formula_sha256": "39b84c91d0b8af18ffcd5bddbcae49cabbac0ea6ac26e8d872083ead3f9f2499",
            "dependencies": [],
        }])
        held = {"ocrmypdf": meta("ocrmypdf", formula_sha256=holds[0]["formula_sha256"])}
        with self.assertRaisesRegex(Error, "Source build excluded by policy: ocrmypdf"):
            self.plan(held, ["ocrmypdf"], holds=holds)
        changed = self.plan(
            {"ocrmypdf": meta("ocrmypdf", formula_sha256="b" * 64)}, ["ocrmypdf"], holds=holds)
        self.assertEqual(changed["nodes"]["ocrmypdf"]["provider"], "build")
        client = self.plan(held, ["ocrmypdf"], build=False, holds=holds)
        self.assertEqual(client["nodes"]["ocrmypdf"]["provider"], "missing")
        config = self.incident_config()
        config["source_build_holds"] = holds
        selected, blocked = native_plan.roots_needing_build(
            ["ocrmypdf", "simdutf"], Inspector({
                **held,
                "simdutf": meta("simdutf"),
            }), {}, config)
        self.assertEqual(selected, ["simdutf"])
        self.assertIn("ocrmypdf", blocked)

    def test_schedule_skips_a_root_that_compiles_blocked_llvm_for_a_runtime_dependent(self):
        nodes = {
            "deno": meta("deno", build=["lld", "llvm", "rust"]),
            "lld": meta("lld", runtime=["llvm"]),
            "llvm": meta("llvm"),
            "rust": meta("rust", runtime=["llvm"]),
            "simdutf": meta("simdutf"),
        }
        selected, blocked = native_plan.roots_needing_build(
            ["deno", "simdutf"], Inspector(nodes), {}, self.config)
        self.assertEqual(selected, ["simdutf"])
        self.assertIn("Source build excluded by policy: llvm", blocked["deno"])

    def test_a_bottled_llvm_still_lets_the_schedule_select_deno(self):
        nodes = {
            "deno": meta("deno", build=["llvm", "rust"]),
            "llvm": meta("llvm"),
            "rust": meta("rust", runtime=["llvm"]),
        }
        selected, blocked = native_plan.roots_needing_build(
            ["deno"], Inspector(nodes), {"llvm": record("llvm")}, self.config)
        self.assertEqual(selected, ["deno"])
        self.assertEqual(blocked, {})

    def test_ci_build_stops_before_compiling_a_blocked_runtime_toolchain(self):
        import os
        import tempfile
        from unittest.mock import patch
        from intelbrew.ci import build
        nodes = {
            "deno": meta("deno", build=["lld", "llvm", "rust"]),
            "lld": meta("lld", runtime=["llvm"]),
            "llvm": meta("llvm"),
            "rust": meta("rust", runtime=["llvm"]),
        }
        env = {
            "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_OS": "macOS", "RUNNER_ARCH": "X64",
            "INTELBREW_CORE_COMMIT": "a" * 40, "GITHUB_SHA": "b" * 40,
            "GITHUB_RUN_ID": "123",
        }

        def fake_native(request, **kwargs):
            if request["mode"] == "inspect":
                return {name: nodes[name] for name in request["names"]}
            raise AssertionError(request["mode"])

        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, env, clear=True), \
             patch("intelbrew.ci.sync_registry", return_value="c" * 40), \
             patch("intelbrew.ci.registry", return_value={}), \
             patch("intelbrew.ci.read_json", return_value={"formulae": ["deno"]}), \
             patch("intelbrew.ci.native", side_effect=fake_native), \
             patch("intelbrew.ci.run") as run:
            with self.assertRaisesRegex(Error, "Source build excluded by policy: llvm"):
                build("deno", Path(directory) / "candidate")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
