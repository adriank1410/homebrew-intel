# SPDX-License-Identifier: BSD-2-Clause
import base64
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import meta
from intelbrew.core import Error
from intelbrew.coverage_sync import (_publish, _require_regular_target,
                                     coverage_report, reconcile, sync)


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
        self.assertEqual(report["eligible"], ["copyleft", "good", "llvm", "qt6"])
        self.assertEqual(report["excluded"], [{"name": "disabled", "reason": "disabled or non-stable formula"}])
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

    def test_monitoring_does_not_apply_source_build_or_redistribution_policy(self):
        metadata = {
            "blocked": meta("blocked", license="GPL-3.0-only"),
            "qt6": meta("qt6", license="Proprietary"),
        }
        report = coverage_report({"core": ["blocked", "qt6"], "external_taps": []}, [], self.config,
                                 lambda names: {name: metadata[name] for name in names})
        self.assertEqual(report["eligible"], ["blocked", "qt6"])
        self.assertEqual(report["excluded"], [])

    def test_publish_refuses_non_owner_identity_before_clone(self):
        with patch("intelbrew.coverage_sync.gh_json", return_value={"login": "someone-else"}), \
             patch("intelbrew.coverage_sync.gh") as gh_call, self.assertRaisesRegex(Error, "identity"):
            _publish("adriank1410/homebrew-intel", [], ["good"])
        gh_call.assert_not_called()

    def test_publish_rejects_desired_target_limit_before_github_io(self):
        targets = [f"formula-{number}" for number in range(2000)]
        with patch("intelbrew.coverage_sync.gh", side_effect=AssertionError("GitHub I/O must not run")), \
             self.assertRaisesRegex(Error, "target limit"):
            _publish("adriank1410/homebrew-intel", targets, ["overflow"])

    def test_publish_rejects_freshly_merged_target_limit_before_commit(self):
        current = [f"formula-{number}" for number in range(2000)]
        git_calls = []

        def github(args, **kwargs):
            if args[:2] == ["repo", "clone"]:
                directory = Path(args[3])
                (directory / "policy").mkdir(parents=True)
                (directory / "policy" / "targets.json").write_text(
                    json.dumps({"schema": 1, "formulae": current}))
                return ""
            raise AssertionError(args)

        def git(args, directory):
            git_calls.append(args)
            return ""

        with patch("intelbrew.coverage_sync.gh_json", return_value={"login": "adriank1410"}), \
             patch("intelbrew.coverage_sync._find_pr", return_value=None), \
             patch("intelbrew.coverage_sync.gh", side_effect=github), \
             patch("intelbrew.coverage_sync._git", side_effect=git), \
             self.assertRaisesRegex(Error, "target limit"):
            _publish("adriank1410/homebrew-intel", [], ["overflow"])
        self.assertFalse(any(call[0] in {"commit", "push"} for call in git_calls))

    def test_publish_reuses_existing_pr_when_it_already_contains_additions(self):
        pr = {"number": 12, "state": "OPEN", "headRefOid": "a" * 40,
              "headRefName": "coverage/intel-installed", "baseRefName": "main",
              "headRepository": {"nameWithOwner": "adriank1410/homebrew-intel"},
              "isCrossRepository": False, "author": {"login": "adriank1410"}}
        with patch("intelbrew.coverage_sync.gh_json", return_value={"login": "adriank1410"}), \
             patch("intelbrew.coverage_sync._find_pr", return_value=pr), \
             patch("intelbrew.coverage_sync._content", return_value={"schema": 1, "formulae": ["good"]}), \
             patch("intelbrew.coverage_sync.gh") as gh_call:
            url = _publish("adriank1410/homebrew-intel", [], ["good"])
        self.assertEqual(url, "https://github.com/adriank1410/homebrew-intel/pull/12")
        gh_call.assert_not_called()

    def test_target_policy_must_be_regular_blob(self):
        head = "a" * 40; tree = "b" * 40; policy = "c" * 40
        def api(args):
            endpoint = args[-1]
            if endpoint.endswith(head): return {"tree": {"sha": tree}}
            if endpoint.endswith(tree): return {"tree": [{"path": "policy", "mode": "040000", "type": "tree", "sha": policy}]}
            return {"tree": [{"path": "targets.json", "mode": "120000", "type": "blob", "sha": "d" * 40}]}
        with patch("intelbrew.coverage_sync.gh_json", side_effect=api), self.assertRaisesRegex(Error, "100644"):
            _require_regular_target("adriank1410/homebrew-intel", head)

    def _reconcile_fixture(self, *, merge_state="CLEAN", mode="100644", second_file=False,
                           auto_merge=True, base_formulae=None, proposed_formulae=None,
                           addition="good", license="MIT"):
        repository = "adriank1410/homebrew-intel"; head = "a" * 40
        root_tree = "b" * 40; policy_tree = "c" * 40
        pr = {"number": 9, "state": "OPEN", "author": {"login": "app/intelbrew"},
              "headRefName": "coverage/intel-installed", "headRefOid": head,
              "baseRefName": "main", "headRepository": {"nameWithOwner": repository},
              "isCrossRepository": False, "files": [{"path": "policy/targets.json"}],
              "statusCheckRollup": [{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}],
              "autoMergeRequest": {"enabledAt": "now"} if auto_merge else None,
              "mergeStateStatus": merge_state}
        files = [{"filename": "policy/targets.json", "status": "modified"}]
        if second_file: files.append({"filename": "README.md", "status": "modified"})
        def encoded(formulae):
            data = json.dumps({"schema": 1, "formulae": formulae}).encode()
            return {"content": base64.b64encode(data).decode()}
        calls = []
        def boundary(args, **kwargs):
            calls.append(args)
            joined = " ".join(args)
            if args[:2] == ["pr", "list"]: return json.dumps([pr])
            if args[:3] == ["pr", "merge", "9"] and "--disable-auto" in args: return ""
            if f"pulls/9/files?per_page=2" in joined: return json.dumps(files)
            if f"git/commits/{head}" in joined: return json.dumps({"tree": {"sha": root_tree}})
            if f"git/trees/{root_tree}" in joined: return json.dumps({"tree": [{"path": "policy", "mode": "040000", "type": "tree", "sha": policy_tree}]})
            if f"git/trees/{policy_tree}" in joined: return json.dumps({"tree": [{"path": "targets.json", "mode": mode, "type": "blob", "sha": "d" * 40}]})
            if "contents/policy/targets.json" in joined:
                base = ["known"] if base_formulae is None else base_formulae
                proposed = sorted([addition, "known"]) if proposed_formulae is None else proposed_formulae
                return json.dumps(encoded(proposed if f"ref={head}" in args else base))
            if "actions/runs?" in joined:
                return json.dumps({"workflow_runs": [{"head_sha": head, "path": ".github/workflows/checks.yml",
                                                       "status": "completed", "conclusion": "success"}]})
            if args[:3] == ["pr", "merge", "9"]: return ""
            if "update-branch" in joined: return ""
            raise AssertionError(args)
        catalog = json.dumps([{"name": addition, "tap": "homebrew/core", "versions": {"stable": "1.0"},
                               "disabled": False, "license": license}]).encode()
        return repository, calls, boundary, catalog

    def test_reconcile_real_validation_merges_or_updates_and_disables_auto_merge(self):
        for merge_state in ("CLEAN", "BEHIND"):
            with self.subTest(merge_state=merge_state):
                repository, calls, boundary, catalog = self._reconcile_fixture(merge_state=merge_state)
                with patch.dict(os.environ, {"INTELBREW_COVERAGE_LOGIN": "app/intelbrew"}), \
                     patch("intelbrew.registry_pr.gh", side_effect=boundary), \
                     patch("intelbrew.coverage_sync.gh", side_effect=boundary), \
                     patch("intelbrew.coverage_sync.urllib.request.urlopen", return_value=io.BytesIO(catalog)):
                    reconcile(repository)
                self.assertTrue(any(call[:4] == ["pr", "merge", "9", "--repo"] and "--disable-auto" in call for call in calls))
                if merge_state == "CLEAN":
                    self.assertTrue(any(call[:3] == ["pr", "merge", "9"] and "--match-head-commit" in call for call in calls))
                    self.assertFalse(any("update-branch" in " ".join(call) for call in calls))
                else:
                    self.assertTrue(any("update-branch" in " ".join(call) and "expected_head_sha=" + "a" * 40 in call for call in calls))
                    self.assertFalse(any("--match-head-commit" in call for call in calls))

    def test_reconcile_updates_behind_pr_before_mutable_main_policy_validation(self):
        repository, calls, boundary, catalog = self._reconcile_fixture(
            merge_state="BEHIND", auto_merge=False,
            base_formulae=["known", "other"], proposed_formulae=["good", "known"])
        with patch.dict(os.environ, {"INTELBREW_COVERAGE_LOGIN": "app/intelbrew"}), \
             patch("intelbrew.registry_pr.gh", side_effect=boundary), \
             patch("intelbrew.coverage_sync.gh", side_effect=boundary), \
             patch("intelbrew.coverage_sync.urllib.request.urlopen", return_value=io.BytesIO(catalog)):
            reconcile(repository)
        self.assertTrue(any("update-branch" in " ".join(call) and
                            "expected_head_sha=" + "a" * 40 in call for call in calls))

    def test_reconcile_real_validation_rejects_second_file_and_symlink(self):
        for merge_state in ("CLEAN", "BEHIND"):
            for options, message in (({"second_file": True}, "exactly one file"), ({"mode": "120000"}, "100644")):
                with self.subTest(options=options):
                    repository, _, boundary, catalog = self._reconcile_fixture(auto_merge=False, merge_state=merge_state, **options)
                    with patch.dict(os.environ, {"INTELBREW_COVERAGE_LOGIN": "app/intelbrew"}), \
                         patch("intelbrew.registry_pr.gh", side_effect=boundary), \
                         patch("intelbrew.coverage_sync.gh", side_effect=boundary), \
                         patch("intelbrew.coverage_sync.urllib.request.urlopen", return_value=io.BytesIO(catalog)), \
                         self.assertRaisesRegex(Error, message):
                        reconcile(repository)

    def test_reconcile_uses_monitoring_rules_for_qt_and_license(self):
        repository, calls, boundary, catalog = self._reconcile_fixture(
            auto_merge=False, addition="qt", license="GPL-3.0-only")
        with patch.dict(os.environ, {"INTELBREW_COVERAGE_LOGIN": "app/intelbrew"}), \
             patch("intelbrew.registry_pr.gh", side_effect=boundary), \
             patch("intelbrew.coverage_sync.gh", side_effect=boundary), \
             patch("intelbrew.coverage_sync.urllib.request.urlopen", return_value=io.BytesIO(catalog)):
            reconcile(repository)
        self.assertTrue(any("--match-head-commit" in call for call in calls))

    def test_reconcile_rejects_foreign_author_before_disabling_auto_merge(self):
        repository, calls, boundary, catalog = self._reconcile_fixture()
        original = boundary
        def foreign(args, **kwargs):
            value = original(args, **kwargs)
            if args[:2] == ["pr", "list"]:
                prs = json.loads(value); prs[0]["author"] = {"login": "attacker"}; return json.dumps(prs)
            return value
        with patch.dict(os.environ, {"INTELBREW_COVERAGE_LOGIN": "app/intelbrew"}), \
             patch("intelbrew.registry_pr.gh", side_effect=foreign), \
             patch("intelbrew.coverage_sync.gh", side_effect=foreign), \
             patch("intelbrew.coverage_sync.urllib.request.urlopen", return_value=io.BytesIO(catalog)), \
             self.assertRaisesRegex(Error, "owner and branch boundary"):
            reconcile(repository)
        self.assertFalse(any(call[:3] == ["pr", "merge", "9"] or "update-branch" in " ".join(call)
                             for call in calls))


if __name__ == "__main__": unittest.main()
