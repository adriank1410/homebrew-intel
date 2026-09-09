# SPDX-License-Identifier: BSD-2-Clause
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("plan_workflow", ROOT / "scripts/plan-workflow.py")
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)
native_spec = importlib.util.spec_from_file_location(
    "native_plan", ROOT / "scripts/native-plan.py")
native_plan = importlib.util.module_from_spec(native_spec)
native_spec.loader.exec_module(native_plan)


TARGETS = ["simdjson", "gnupg", "curl"]


class PlanWorkflowTests(unittest.TestCase):
    def test_schedule_batch_is_bounded_and_rotates_without_dropping_candidates(self):
        candidates = [f"formula-{number}" for number in range(19)]
        batches = [native_plan.bounded_schedule(candidates, rotation_key=run_number, limit=8)
                   for run_number in range(1, 4)]

        self.assertTrue(all(len(batch) <= 8 for batch in batches))
        self.assertEqual(batches[0], candidates[:8])
        self.assertEqual(batches[1], candidates[8:16])
        self.assertEqual(batches[2], candidates[16:] + candidates[:5])
        self.assertEqual(set().union(*map(set, batches)), set(candidates))

    def test_native_schedule_default_is_two_parallel_waves(self):
        candidates = [f"formula-{number}" for number in range(9)]
        self.assertEqual(native_plan.SCHEDULE_BATCH_SIZE, 4)
        self.assertEqual(
            native_plan.bounded_schedule(candidates, rotation_key=2),
            candidates[4:8],
        )

    def test_schedule_batch_rejects_invalid_rotation_inputs(self):
        for run_number, limit in ((0, 8), (-1, 8), (1, 0), (1, -1)):
            with self.subTest(run_number=run_number, limit=limit), self.assertRaises(plan.Error):
                native_plan.bounded_schedule(["root"], rotation_key=run_number, limit=limit)

    def test_recommended_dependency_keeps_root_eligible(self):
        item = {"name": "root", "versions": {"stable": "1.0"}, "revision": 0,
                "version_scheme": 0, "ruby_source_checksum": {"sha256": "a" * 64},
                "dependencies": [], "recommended_dependencies": ["missing"],
                "bottle": {"stable": {"files": {"x86_64_sequoia": {
                    "sha256": "f" * 64, "cellar": ":any"}}}}}
        self.assertEqual(plan.scheduled_roots(["root"], {"root": item}, {}), ["root"])

    def test_incomplete_api_entry_does_not_abort_siblings(self):
        good = {"name": "good", "versions": {"stable": "1.0"}, "revision": 0,
                "version_scheme": 0, "ruby_source_checksum": {"sha256": "a" * 64},
                "dependencies": [], "bottle": {"stable": {"files": {"x86_64_sequoia": {
                    "sha256": "f" * 64, "cellar": ":any"}}}}}
        for broken in ({"name": "bad"}, dict(good, name="bad", ruby_source_checksum=None),
                       dict(good, name="bad", bottle=None)):
            with self.subTest(broken=broken):
                self.assertEqual(plan.scheduled_roots(["bad", "good"],
                    {"bad": broken, "good": good}, {}), ["bad"])

    def test_top_level_pour_condition_requires_native_planning(self):
        item = {"name": "root", "versions": {"stable": "1.0"}, "revision": 0,
                "version_scheme": 0, "ruby_source_checksum": {"sha256": "a" * 64},
                "dependencies": [], "pour_bottle_only_if": "clt_installed",
                "bottle": {"stable": {"files": {"x86_64_sequoia": {
                    "sha256": "f" * 64, "cellar": ":any"}}}}}
        self.assertEqual(plan.scheduled_roots(["root"], {"root": item}, {}), ["root"])

    def test_scheduled_preflight_requires_exact_complete_graph(self):
        def item(name, sha, *, deps=(), official=False):
            return {
                "name": name,
                "versions": {"stable": "1.0"},
                "revision": 0,
                "version_scheme": 0,
                "ruby_source_checksum": {"sha256": sha},
                "dependencies": list(deps),
                "bottle": {"stable": {"files": {"x86_64_sequoia": {
                    "url": "https://example.test", "sha256": "f" * 64, "cellar": ":any"
                }}}} if official else {},
            }

        index = {
            "official": item("official", "a" * 64, official=True),
            "dep": item("dep", "b" * 64, official=True),
            "personal": item("personal", "c" * 64, deps=("dep",)),
            "stale": item("stale", "d" * 64, deps=("dep",)),
            "missing": item("missing", "e" * 64),
        }
        records = {
            "personal": {"pkg_version": "1.0", "version_scheme": 0,
                         "formula_sha256": "c" * 64,
                         "runtime_dependencies": [{"name": "dep", "pkg_version": "1.0",
                                                    "formula_sha256": "b" * 64}]},
            "stale": {"pkg_version": "1.0", "version_scheme": 0,
                      "formula_sha256": "d" * 64,
                      "runtime_dependencies": [{"name": "dep", "pkg_version": "0.9",
                                                 "formula_sha256": "f" * 64}]},
        }
        self.assertEqual(
            plan.scheduled_roots(["official", "personal", "stale", "missing"], index, records),
            ["stale", "missing"],
        )

    def test_schedule_accepts_transitive_registry_dependencies(self):
        def item(name, sha, deps=(), official=True):
            return {"name": name, "versions": {"stable": "1.0"}, "revision": 0,
                    "version_scheme": 0, "ruby_source_checksum": {"sha256": sha},
                    "dependencies": list(deps),
                    "bottle": {"stable": {"files": {"x86_64_sequoia": {
                        "url": "x", "sha256": "f" * 64, "cellar": ":any"
                    }}}}
                    if official else {}}
        index = {"root": item("root", "a" * 64, ("dep",), official=False),
                 "dep": item("dep", "b" * 64, ("leaf",)),
                 "leaf": item("leaf", "c" * 64)}
        records = {"root": {"pkg_version": "1.0", "version_scheme": 0,
                             "formula_sha256": "a" * 64,
                             "runtime_dependencies": [
                                 {"name": "dep", "pkg_version": "1.0", "formula_sha256": "b" * 64},
                                 {"name": "leaf", "pkg_version": "1.0", "formula_sha256": "c" * 64},
                             ]}}
        self.assertEqual(plan.scheduled_roots(["root"], index, records), [])

    def test_schedule_fails_closed_for_missing_dependency_and_cycles(self):
        def item(name, sha, deps=()):
            return {"name": name, "versions": {"stable": "1.0"}, "revision": 0,
                    "version_scheme": 0, "ruby_source_checksum": {"sha256": sha},
                    "dependencies": list(deps), "bottle": {}}
        missing = {"root": item("root", "a" * 64, ("gone",))}
        cycle = {"a": item("a", "b" * 64, ("b",)), "b": item("b", "c" * 64, ("a",))}
        self.assertEqual(plan.scheduled_roots(["root"], missing, {}), ["root"])
        self.assertEqual(plan.scheduled_roots(["a"], cycle, {}), ["a"])

    def test_manual_csv_strips_and_preserves_order(self):
        self.assertEqual(
            plan.requested_roots(" simdjson, gnupg ", TARGETS, allow_csv=True),
            ["simdjson", "gnupg"],
        )

    def test_manual_csv_rejects_empty_duplicate_and_unreviewed(self):
        for value in ["simdjson,", "simdjson,,gnupg", "simdjson,simdjson", "simdjson,evil"]:
            with self.subTest(value=value), self.assertRaises(plan.Error):
                plan.requested_roots(value, TARGETS, allow_csv=True)

    def test_manual_csv_rejects_more_than_fifty(self):
        targets = [f"f{i}" for i in range(51)]
        with self.assertRaises(plan.Error):
            plan.requested_roots(",".join(targets), targets, allow_csv=True)

    def test_push_request_remains_single_root(self):
        with self.assertRaises(plan.Error):
            plan.requested_roots("simdjson,gnupg", TARGETS, allow_csv=False)

    def test_main_writes_matrix_and_uses_pinned_core_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            env = {
                "REQUESTED_FORMULA": "simdjson,gnupg",
                "REQUEST_SOURCE": "workflow_dispatch",
                "GITHUB_OUTPUT": str(output),
            }
            with patch.dict(os.environ, env, clear=True), patch.object(
                plan, "run", return_value="b" * 40 + " refs/heads/main\n"
            ) as run:
                self.assertEqual(plan.main(), 0)
            run.assert_called_once_with([
                "git", "ls-remote", "https://github.com/Homebrew/homebrew-core.git",
                "refs/heads/main",
            ])
            lines = output.read_text().splitlines()
            self.assertEqual(json.loads(lines[0].split("=", 1)[1]), {"root": TARGETS[:2]})

    def test_schedule_filters_covered_roots_and_reports_empty_work(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            env = {
                "REQUESTED_FORMULA": "all",
                "REQUEST_SOURCE": "schedule",
                "GITHUB_OUTPUT": str(output),
            }
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(plan, "load_formula_index", return_value={}), \
                    patch.object(plan, "registry", return_value={}), \
                    patch.object(plan, "scheduled_roots", return_value=[]), \
                    patch.object(plan, "run", return_value="b" * 40 + " refs/heads/main\n"):
                self.assertEqual(plan.main(), 0)
            self.assertIn("matrix={\"root\":[]}", output.read_text())
            self.assertIn("has_work=false", output.read_text())

    def test_linux_schedule_passes_all_candidates_to_native_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            candidates = [f"formula-{number}" for number in range(300)]
            env = {
                "REQUESTED_FORMULA": "all",
                "REQUEST_SOURCE": "schedule",
                "GITHUB_OUTPUT": str(output),
            }
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(plan, "read_json", return_value={"formulae": candidates}), \
                    patch.object(plan, "load_formula_index", return_value={}), \
                    patch.object(plan, "registry", return_value={}), \
                    patch.object(plan, "scheduled_roots", return_value=candidates), \
                    patch.object(plan, "run", return_value="b" * 40 + " refs/heads/main\n"):
                self.assertEqual(plan.main(), 0)
            matrix = json.loads(output.read_text().splitlines()[0].split("=", 1)[1])
            self.assertEqual(matrix, {"root": candidates})

    def test_native_inspector_primes_large_catalog_in_bounded_batches(self):
        names = [f"formula-{number}" for number in range(214)]
        calls = []

        def inspect(request, *, ci):
            calls.append((request, ci))
            return {name: {"name": name} for name in request["names"]}

        with patch.object(native_plan, "native", side_effect=inspect):
            inspector = native_plan.CachedInspector()
            inspector.prime(names)
        self.assertEqual([len(call[0]["names"]) for call in calls], [100, 100, 14])
        self.assertTrue(all(call[0]["mode"] == "inspect" and call[1] for call in calls))
        self.assertEqual(set(inspector.cache), set(names))

    def test_native_inspector_isolates_unknown_root_without_dropping_sibling(self):
        def inspect(request, *, ci):
            if "unknown" in request["names"]:
                raise native_plan.Error("unknown formula")
            return {name: {"name": name} for name in request["names"]}

        with patch.object(native_plan, "native", side_effect=inspect):
            inspector = native_plan.CachedInspector()
            inspector.prime(["good", "unknown"])
        self.assertIn("good", inspector.cache)
        self.assertEqual(inspector.errors, {"unknown": "unknown formula"})
        with self.assertRaisesRegex(native_plan.Error, "unknown: native inspection failed"):
            inspector(["unknown"])

    def test_native_preflight_selects_only_roots_that_really_need_builds(self):
        def meta(name, *, official=False):
            return {
                "name": name, "tap": "homebrew/core", "version": "1.0",
                "revision": 0, "version_scheme": 0, "pkg_version": "1.0",
                "formula_sha256": (("a" if name == "missing" else "b") * 64),
                "runtime": [], "build": [],
                "test": [], "official_bottle": official, "license": "MIT",
            }

        class Inspector:
            def __init__(self):
                self.items = {"missing": meta("missing"), "official": meta("official", official=True)}
                self.primed = []

            def prime(self, names):
                self.primed.append(list(names))

            def __call__(self, names):
                return {name: self.items[name] for name in names}

        inspector = Inspector()
        config = {"max_graph_nodes": 20, "max_source_builds": 5,
                  "blocked_source_builds": [], "permissive_license_tokens": ["MIT"],
                  "redistribution_exceptions": {}}
        self.assertEqual(native_plan.roots_needing_build(
            ["missing", "official"], inspector, {}, config), (["missing"], {}))
        self.assertEqual(inspector.primed, [["missing", "official"]])

    def test_native_preflight_reports_blocked_root_without_hiding_valid_sibling(self):
        class Inspector:
            def prime(self, names):
                pass

            def __call__(self, names):
                return {name: {
                    "name": name, "tap": "homebrew/core", "version": "1.0",
                    "revision": 0, "version_scheme": 0, "pkg_version": "1.0",
                    "formula_sha256": ("a" if name == "valid" else "b") * 64,
                    "runtime": [], "build": [], "test": [], "official_bottle": False,
                    "license": "MIT" if name == "valid" else "GPL-3.0-only",
                } for name in names}

        config = {"max_graph_nodes": 20, "max_source_builds": 5,
                  "blocked_source_builds": [], "permissive_license_tokens": ["MIT"],
                  "redistribution_exceptions": {}}
        needed, blocked = native_plan.roots_needing_build(
            ["blocked", "valid"], Inspector(), {}, config)
        self.assertEqual(needed, ["valid"])
        self.assertIn("redistribution review required", blocked["blocked"])


if __name__ == "__main__":
    unittest.main()
