# SPDX-License-Identifier: BSD-2-Clause
import copy
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

import intelbrew.cli as cli
from helpers import meta
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
                         "Missing providers (1): bad\n"
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
            {"skipped": [{"root": "bad", "missing_dependencies": ["bad"]}]},
        )

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

    def test_available_is_only_valid_for_upgrade(self):
        for command in ("plan", "install", "doctor"):
            with self.subTest(command=command), patch.object(cli.platform, "system", return_value="Darwin"), \
                 patch.object(cli.platform, "machine", return_value="x86_64"):
                self.assertEqual(cli.main([command, "--available"]), 1)


if __name__ == "__main__":
    unittest.main()
