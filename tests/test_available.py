# SPDX-License-Identifier: BSD-2-Clause
import copy
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

import intelbrew.cli as cli
from helpers import meta, record
from intelbrew.core import Planner


def make_plan(nodes, roots):
    return Planner(lambda names: {name: copy.deepcopy(nodes[name]) for name in names}, {}).make(roots)


class AvailablePlanTests(unittest.TestCase):
    def test_keeps_root_with_complete_runtime_closure(self):
        plan = make_plan(
            {
                "good": meta("good", runtime=["dep"], official=True),
                "dep": meta("dep", official=True),
                "bad": meta("bad", runtime=["qt"], official=True),
                "qt": meta("qt"),
            },
            ["good", "bad"],
        )

        filtered, skipped = cli.filter_available_plan(plan)

        self.assertEqual(filtered["roots"], ["good"])
        self.assertEqual(filtered["order"], ["dep", "good"])
        self.assertEqual(set(filtered["nodes"]), {"dep", "good"})
        self.assertEqual(skipped, [{"root": "bad", "missing_dependencies": ["qt"]}])

    def test_shared_missing_dependency_blocks_both_roots(self):
        plan = make_plan(
            {
                "first": meta("first", runtime=["shared"], official=True),
                "second": meta("second", runtime=["shared"], official=True),
                "shared": meta("shared"),
            },
            ["first", "second"],
        )

        filtered, skipped = cli.filter_available_plan(plan)

        self.assertEqual(filtered["roots"], [])
        self.assertEqual(filtered["order"], [])
        self.assertEqual(filtered["nodes"], {})
        self.assertEqual(
            skipped,
            [
                {"root": "first", "missing_dependencies": ["shared"]},
                {"root": "second", "missing_dependencies": ["shared"]},
            ],
        )

    def test_all_missing_has_no_apply(self):
        nodes = {"bad": meta("bad")}
        config = {"repository": "adriank1410/homebrew-intel", "max_graph_nodes": 400}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value={}), \
             patch.object(cli, "native", side_effect=[["bad"], {"core": [], "external_taps": []}, nodes]), \
             patch.object(cli, "apply_plan") as apply:
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(cli.main(["upgrade", "--available", "--apply"]), 0)
        apply.assert_not_called()
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(stdout.getvalue(),
                         "Skipped unavailable upgrade roots (1): bad\n"
                         "Missing provider reasons (1): no published record: 1\n"
                         "Run `brew intel upgrade --available --json` for individual details.\n"
                         "No fully available upgrade roots; nothing installed.\n")

    def test_all_missing_json_keeps_machine_readable_skip_output(self):
        nodes = {"bad": meta("bad")}
        config = {"repository": "adriank1410/homebrew-intel", "max_graph_nodes": 400}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value={}), \
             patch.object(cli, "native", side_effect=[["bad"], {"core": [], "external_taps": []}, nodes]):
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(cli.main(["upgrade", "--available", "--json"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"schema": 1, "roots": [], "order": [], "nodes": {}})
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "skipped": [{"root": "bad", "missing_dependencies": ["bad"]}],
                "missing_providers": [{
                    "name": "bad",
                    "target_pkg_version": "1.0",
                    "published_pkg_version": None,
                    "reason": "no_published_record",
                }],
            },
        )

    def test_available_json_reports_unique_missing_provider_reasons(self):
        records = {
            "version_mismatch": record("version_mismatch", pkg_version="0.9", version="0.9",
                                        filename="version_mismatch--0.9.sequoia.bottle.tar.gz"),
            "scheme_mismatch": record("scheme_mismatch", version_scheme=1),
            "recipe_mismatch": record("recipe_mismatch", formula_sha256="0" * 64),
            "drifted": record(
                "drifted",
                runtime_dependencies=[{"name": "changed_dep", "pkg_version": "1.0",
                                       "formula_sha256": "a" * 64}],
            ),
            "shared_root_a": record("shared_root_a"),
            "shared_root_b": record("shared_root_b"),
        }
        nodes = {
            "absent": meta("absent"),
            "version_mismatch": meta("version_mismatch"),
            "scheme_mismatch": meta("scheme_mismatch"),
            "recipe_mismatch": meta("recipe_mismatch", formula_sha256="2" * 64),
            "drifted": meta("drifted", runtime=["changed_dep"]),
            "changed_dep": meta("changed_dep", version="2.0", pkg_version="2.0",
                                formula_sha256="b" * 64, official=True),
            "shared_root_a": meta("shared_root_a", runtime=["shared"]),
            "shared_root_b": meta("shared_root_b", runtime=["shared"]),
            "shared": meta("shared"),
        }
        roots = ["absent", "version_mismatch", "scheme_mismatch", "recipe_mismatch",
                 "drifted", "shared_root_a", "shared_root_b"]

        def fake_native(payload):
            mode = payload.get("mode")
            if mode == "outdated":
                return roots
            if mode == "coverage":
                return {"core": [], "external_taps": []}
            if mode == "inspect":
                return {name: nodes[name] for name in payload["names"]}
            raise ValueError(f"Unexpected mode: {mode}")

        config = {"repository": "adriank1410/homebrew-intel", "max_graph_nodes": 400}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value=records), \
             patch.object(cli, "native", side_effect=fake_native):
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(cli.main(["upgrade", "--available", "--json"]), 0)

        self.assertEqual(json.loads(stdout.getvalue()),
                         {"schema": 1, "roots": [], "order": [], "nodes": {}})
        report = json.loads(stderr.getvalue())
        self.assertEqual(set(report), {"skipped", "missing_providers"})
        details = {item["name"]: item for item in report["missing_providers"]}
        self.assertEqual(details, {
            "absent": {"name": "absent", "target_pkg_version": "1.0",
                       "published_pkg_version": None, "reason": "no_published_record"},
            "version_mismatch": {"name": "version_mismatch", "target_pkg_version": "1.0",
                                  "published_pkg_version": "0.9",
                                  "reason": "version_or_scheme_differs"},
            "scheme_mismatch": {"name": "scheme_mismatch", "target_pkg_version": "1.0",
                                "published_pkg_version": "1.0",
                                "reason": "version_or_scheme_differs"},
            "recipe_mismatch": {"name": "recipe_mismatch", "target_pkg_version": "1.0",
                                "published_pkg_version": "1.0", "reason": "recipe_differs"},
            "drifted": {"name": "drifted", "target_pkg_version": "1.0",
                        "published_pkg_version": "1.0", "reason": "runtime_dependency_drift"},
            "shared": {"name": "shared", "target_pkg_version": "1.0",
                       "published_pkg_version": None, "reason": "no_published_record"},
        })
        self.assertEqual(sum(item["name"] == "shared" for item in report["missing_providers"]), 1)
        shared_skips = [item for item in report["skipped"]
                        if item["root"] in {"shared_root_a", "shared_root_b"}]
        self.assertEqual(shared_skips, [
            {"root": "shared_root_a", "missing_dependencies": ["shared"]},
            {"root": "shared_root_b", "missing_dependencies": ["shared"]},
        ])

    def test_available_all_covered_human_output_is_unchanged(self):
        nodes = {"clean": meta("clean", official=True, build=["compiler"], test=["testdep"])}
        config = {"repository": "adriank1410/homebrew-intel", "max_graph_nodes": 400}

        def fake_native(payload):
            mode = payload.get("mode")
            if mode == "outdated":
                return ["clean"]
            if mode == "coverage":
                return {"core": [], "external_taps": []}
            if mode == "inspect":
                return {name: nodes[name] for name in payload["names"]}
            raise ValueError(f"Unexpected mode: {mode}")

        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value={}), \
             patch.object(cli, "native", side_effect=fake_native):
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(cli.main(["upgrade", "--available"]), 0)

        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(stdout.getvalue(),
                         "==> Upgrading 1 outdated package:\n"
                         "clean 1.0\n"
                         "\nDry run only. Add --apply to install. Missing bottles cause an error, not compilation.\n")

    def test_coverage_reports_candidates_and_keeps_external_taps_separate(self):
        config = {
            "repository": "adriank1410/homebrew-intel",
            "blocked_source_builds": ["llvm"],
            "target_exclusions": {"zlib": "test exclusion"},
            "max_graph_nodes": 400,
        }
        installed = {"core": ["curl", "llvm", "qt", "zlib"],
                     "external_taps": ["thirdparty/mytool", "other/curl"]}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value={}), \
             patch.object(cli, "read_json", return_value={"schema": 1, "formulae": ["curl"]}), \
             patch.object(cli, "native", return_value=installed):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(["coverage", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {
            "schema": 1,
            "monitored_core": ["curl"],
            "unmonitored_core": ["llvm", "qt", "zlib"],
            "policy_exclusions": [],
            "proposed_candidates": ["llvm", "qt", "zlib"],
            "external_tap_formulae": ["other/curl", "thirdparty/mytool"],
        })

    def test_coverage_default_is_human_readable(self):
        config = {"repository": "adriank1410/homebrew-intel", "blocked_source_builds": [],
                  "max_graph_nodes": 400}
        installed = {"core": ["zlib"], "external_taps": ["tap/tool"]}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value={}), \
             patch.object(cli, "read_json", return_value={"schema": 1, "formulae": ["curl"]}), \
             patch.object(cli, "native", return_value=installed):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(["coverage"]), 0)
        self.assertEqual(output.getvalue(),
                         "Monitored core formulae (0): none\n"
                         "Core formulae not explicitly on target list (1)\n"
                         "Monitoring exclusions (0): none\n"
                         "Proposed candidates (1): zlib\n"
                         "External tap formulae (1): tap/tool\n")

    def test_json_upgrade_has_no_human_coverage_notice(self):
        nodes = {"bad": meta("bad", official=True)}
        config = {"repository": "adriank1410/homebrew-intel", "blocked_source_builds": [],
                  "max_graph_nodes": 400}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value={}), \
             patch.object(cli, "read_json", return_value={"schema": 1, "formulae": ["curl"]}), \
             patch.object(cli, "native", side_effect=[["bad"], {"core": ["other"], "external_taps": []}, nodes]):
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(cli.main(["upgrade", "--json"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["roots"], ["bad"])
        self.assertEqual(stderr.getvalue(), "")

    def test_default_upgrade_remains_all_or_nothing(self):
        plan = make_plan({"bad": meta("bad")}, ["bad"])
        filtered, skipped = cli.filter_available_plan(plan)
        self.assertEqual(filtered, {"schema": 1, "roots": [], "order": [], "nodes": {}})
        self.assertEqual(skipped, [{"root": "bad", "missing_dependencies": ["bad"]}])
        self.assertEqual(plan["roots"], ["bad"])
        self.assertEqual(plan["order"], ["bad"])

    def test_default_cli_does_not_filter(self):
        nodes = {"bad": meta("bad")}
        config = {"repository": "adriank1410/homebrew-intel", "max_graph_nodes": 400}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value={}), \
             patch.object(cli, "native", side_effect=[["bad"], {"core": [], "external_taps": []}, nodes]), \
             patch.object(cli, "filter_available_plan") as available, \
             patch.object(cli, "apply_plan") as apply:
            self.assertEqual(cli.main(["upgrade", "--apply"]), 0)
        available.assert_not_called()
        apply.assert_called_once()
        self.assertEqual(apply.call_args.args[0]["roots"], ["bad"])

    def test_available_skips_drifted_personal_bottle_and_applies_clean_root(self):
        records = {
            "drifted": record(
                "drifted",
                formula_sha256="0" * 64,
                runtime_dependencies=[{"name": "dep", "pkg_version": "1.0", "formula_sha256": "1" * 64}],
            ),
            "clean": record(
                "clean",
                formula_sha256="3" * 64,
                runtime_dependencies=[],
            ),
        }
        nodes = {
            "drifted": meta("drifted", formula_sha256="0" * 64, runtime=["dep"]),
            "dep": meta("dep", official=True, formula_sha256="2" * 64),
            "clean": meta("clean", formula_sha256="3" * 64),
        }
        def fake_native(payload):
            mode = payload.get("mode")
            if mode == "outdated":
                return ["drifted", "clean"]
            if mode == "coverage":
                return {"core": [], "external_taps": []}
            if mode == "inspect":
                return {name: nodes[name] for name in payload["names"]}
            raise ValueError(f"Unexpected mode: {mode}")

        config = {"repository": "adriank1410/homebrew-intel", "max_graph_nodes": 400}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value=records), \
             patch.object(cli, "native", side_effect=fake_native), \
             patch.object(cli, "apply_plan") as apply:
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(cli.main(["upgrade", "--available", "--apply"]), 0)
        apply.assert_called_once()
        self.assertEqual(apply.call_args.args[0]["roots"], ["clean"])
        self.assertIn("Skipped unavailable upgrade roots (1): drifted", stdout.getvalue())
        self.assertNotIn("Missing providers", stdout.getvalue())

    def test_skipped_render_keeps_distinct_missing_provider_details(self):
        output = StringIO()
        cli.render_skipped(
            [{"root": "app", "missing_dependencies": ["lib"]}],
            stream=output,
        )
        self.assertEqual(
            output.getvalue(),
            "Skipped unavailable upgrade roots (1): app\n"
            "Missing providers (1): lib\n",
        )

    def test_upgrade_without_available_still_raises_on_dependency_drift(self):
        records = {
            "drifted": record(
                "drifted",
                formula_sha256="0" * 64,
                runtime_dependencies=[{"name": "dep", "pkg_version": "1.0", "formula_sha256": "1" * 64}],
            ),
        }
        nodes = {
            "drifted": meta("drifted", formula_sha256="0" * 64, runtime=["dep"]),
            "dep": meta("dep", official=True, formula_sha256="2" * 64),
        }
        def fake_native(payload):
            mode = payload.get("mode")
            if mode == "outdated":
                return ["drifted"]
            if mode == "coverage":
                return {"core": [], "external_taps": []}
            if mode == "inspect":
                return {name: nodes[name] for name in payload["names"]}
            raise ValueError(f"Unexpected mode: {mode}")

        config = {"repository": "adriank1410/homebrew-intel", "max_graph_nodes": 400}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value=config), \
             patch.object(cli, "registry", return_value=records), \
             patch.object(cli, "native", side_effect=fake_native):
            stderr = StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(cli.main(["upgrade", "--apply"]), 1)
            self.assertIn("Personal bottle dependency drift: drifted -> dep", stderr.getvalue())

    def test_available_is_only_valid_for_upgrade(self):
        for command in ("plan", "install", "doctor"):
            with self.subTest(command=command), patch.object(cli.platform, "system", return_value="Darwin"), \
                 patch.object(cli.platform, "machine", return_value="x86_64"):
                self.assertEqual(cli.main([command, "--available"]), 1)


if __name__ == "__main__":
    unittest.main()
