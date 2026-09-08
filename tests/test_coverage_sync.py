# SPDX-License-Identifier: BSD-2-Clause
import unittest
from unittest.mock import patch

from helpers import meta
from intelbrew.core import Error
from intelbrew.coverage_sync import _publish, coverage_report, sync


class CoverageSyncTests(unittest.TestCase):
    def setUp(self):
        self.config = {"repository": "adriank1410/homebrew-intel", "max_graph_nodes": 2,
                       "blocked_source_builds": ["llvm"], "target_exclusions": {"qt6": "Qt policy"},
                       "permissive_license_tokens": ["MIT"]}

    def test_report_batches_and_filters_current_metadata(self):
        calls = []
        metadata = {"good": meta("good"), "llvm": meta("llvm"), "qt6": meta("qt6"),
                    "copyleft": meta("copyleft", license="GPL-3.0-only"),
                    "disabled": meta("disabled", disabled=True)}
        def inspect(names): calls.append(names); return {name: metadata[name] for name in names}
        report = coverage_report({"core": ["known", "qt6", "good", "llvm", "copyleft", "disabled"],
                                  "external_taps": ["tap/private"]}, ["known"], self.config, inspect)
        self.assertEqual(report["eligible"], ["good"])
        self.assertEqual([x["name"] for x in report["excluded"]], ["copyleft", "disabled", "llvm", "qt6"])
        self.assertEqual(calls, [["copyleft", "disabled"], ["good", "llvm"], ["qt6"]])
        self.assertEqual(report["external_tap_count"], 1)
        self.assertNotIn("tap/private", str(report))

    def test_apply_publishes_only_when_eligible(self):
        with patch("intelbrew.coverage_sync._publish", return_value="https://github.test/pr/1") as publish:
            report = sync(self.config, apply=True, installed={"core": ["good"], "external_taps": []}, targets=[],
                          inspect=lambda names: {"good": meta("good")})
        publish.assert_called_once_with("adriank1410/homebrew-intel", [], ["good"])
        self.assertEqual(report["added"], ["good"])
        self.assertEqual(report["pr_url"], "https://github.test/pr/1")

    def test_failed_batch_isolates_only_bad_root(self):
        calls = []
        def inspect(names):
            calls.append(names)
            if "gone" in names: raise Error("formula unavailable")
            return {name: meta(name) for name in names}
        report = coverage_report({"core": ["good", "gone"], "external_taps": []}, [], self.config, inspect)
        self.assertEqual(report["eligible"], ["good"])
        self.assertEqual(report["excluded"], [{"name": "gone", "reason": "metadata inspection failed"}])
        self.assertEqual(calls, [["gone", "good"], ["gone"], ["good"]])

    def test_inventory_is_bounded(self):
        with self.assertRaisesRegex(Error, "excessive"):
            coverage_report({"core": ["x"] * 2001, "external_taps": []}, [], self.config, lambda _: {})

    def test_nonstandard_install_state_is_excluded(self):
        report = coverage_report({"core": ["head"], "external_taps": []}, [], self.config,
                                 lambda _: {"head": meta("head", installed_head=True)})
        self.assertEqual(report["eligible"], [])
        self.assertEqual(report["excluded"][0]["reason"], "options or HEAD install requires review")

    def test_publish_refuses_non_owner_identity_before_clone(self):
        with patch("intelbrew.coverage_sync.gh_json", return_value={"login": "someone-else"}), \
             patch("intelbrew.coverage_sync.gh") as gh_call, self.assertRaisesRegex(Error, "identity"):
            _publish("adriank1410/homebrew-intel", [], ["good"])
        gh_call.assert_not_called()


if __name__ == "__main__": unittest.main()
