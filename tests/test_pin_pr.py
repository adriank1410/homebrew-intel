# SPDX-License-Identifier: BSD-2-Clause
import json
import os
import unittest
from unittest.mock import patch

from intelbrew.core import Error
from intelbrew.pin_pr import main, reconcile_pin


REPOSITORY = "adriank1410/homebrew-intel"
BOT = "app/intel-bottle-publisher"
HEAD = "a" * 40
FRESH_HEAD = "b" * 40


def pin_pr(**changes):
    result = {
        "number": 816,
        "state": "OPEN",
        "author": {"login": BOT, "is_bot": True},
        "headRefName": "automation/homebrew-pins",
        "headRefOid": HEAD,
        "baseRefName": "main",
        "headRepository": {"nameWithOwner": REPOSITORY},
        "isCrossRepository": False,
        "statusCheckRollup": [{
            "name": "tests",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
        }],
        "autoMergeRequest": None,
        "mergeStateStatus": "CLEAN",
    }
    result.update(changes)
    return result


class PinPullRequestTests(unittest.TestCase):
    def setUp(self):
        self._bot = patch.dict(os.environ, {"INTELBREW_BOT_LOGIN": BOT})
        self._bot.start()

    def tearDown(self):
        self._bot.stop()

    def _run(self, responses):
        calls = []

        def fake(args, **kwargs):
            calls.append(args)
            if not responses:
                raise AssertionError(args)
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch("intelbrew.pin_pr.gh", side_effect=fake):
            outcome = reconcile_pin(REPOSITORY)
        self.assertFalse(responses)
        return outcome, calls

    def _listed(self, pr, files=None):
        if files is None:
            files = [[{"filename": "policy/config.json", "status": "modified"}]]
        return [
            json.dumps([pr]),
            json.dumps(files),
            "",
        ]

    def test_merges_the_exact_head_after_tests_succeed(self):
        outcome, calls = self._run(self._listed(pin_pr()))

        self.assertEqual(outcome, "merged")
        self.assertEqual(calls[-1], [
            "pr", "merge", "816", "--repo", REPOSITORY, "--squash",
            "--match-head-commit", HEAD,
        ])
        self.assertNotIn("--auto", calls[-1])
        self.assertNotIn("--delete-branch", calls[-1])

    def test_disables_queued_auto_merge_and_uses_the_refreshed_head(self):
        stale = pin_pr(autoMergeRequest={"commitHeadline": "old"})
        fresh = pin_pr(headRefOid=FRESH_HEAD)
        outcome, calls = self._run([
            json.dumps([stale]),
            "",
            json.dumps([fresh]),
            json.dumps([[{"filename": "policy/config.json", "status": "modified"}]]),
            "",
        ])

        self.assertEqual(outcome, "merged")
        self.assertEqual(calls[1][:4], ["pr", "merge", "816", "--repo"])
        self.assertIn("--disable-auto", calls[1])
        self.assertEqual(calls[-1][calls[-1].index("--match-head-commit") + 1], FRESH_HEAD)

    def test_waits_while_tests_are_pending(self):
        pending = pin_pr(statusCheckRollup=[{
            "name": "tests", "status": "IN_PROGRESS", "conclusion": "",
        }])
        outcome, calls = self._run(self._listed(pending)[:-1])

        self.assertEqual(outcome, "waiting")
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in calls))

    def test_leaves_a_failed_native_check_open(self):
        failed = pin_pr(statusCheckRollup=[{
            "name": "tests", "status": "COMPLETED", "conclusion": "FAILURE",
        }])
        outcome, calls = self._run(self._listed(failed)[:-1])

        self.assertEqual(outcome, "blocked")
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in calls))

    def test_does_not_merge_when_github_still_reports_the_pr_blocked(self):
        blocked = pin_pr(mergeStateStatus="BLOCKED")
        outcome, calls = self._run(self._listed(blocked)[:-1])

        self.assertEqual(outcome, "waiting")
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in calls))

    def test_updates_a_behind_branch_without_merging_the_old_head(self):
        outcome, calls = self._run([
            json.dumps([pin_pr(mergeStateStatus="BEHIND")]),
            json.dumps([[{"filename": "policy/config.json", "status": "modified"}]]),
            "",
        ])

        self.assertEqual(outcome, "updated")
        self.assertEqual(calls[-1][:3], ["api", "--method", "PUT"])
        self.assertIn(f"expected_head_sha={HEAD}", calls[-1])
        self.assertFalse(any(call[:2] == ["pr", "merge"] and "--squash" in call for call in calls))

    def test_absent_pin_pr_does_nothing(self):
        outcome, calls = self._run([json.dumps([])])

        self.assertEqual(outcome, "absent")
        self.assertEqual(len(calls), 1)

    def test_refuses_a_change_outside_the_pin_file(self):
        with self.assertRaisesRegex(Error, "outside policy/config.json"):
            self._run([
                json.dumps([pin_pr()]),
                json.dumps([[
                    {"filename": "policy/config.json", "status": "modified"},
                    {"filename": "README.md", "status": "modified"},
                ]]),
            ])

    def test_refuses_a_foreign_author_before_any_merge_call(self):
        foreign = pin_pr(author={"login": "someone", "is_bot": False})
        calls = []

        def fake(args, **kwargs):
            calls.append(args)
            return json.dumps([foreign])

        with patch("intelbrew.pin_pr.gh", side_effect=fake):
            with self.assertRaisesRegex(Error, "App-owned"):
                reconcile_pin(REPOSITORY)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in calls))

    def test_cli_refuses_to_run_outside_the_maintenance_job(self):
        env = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(main(["--reconcile", "--repository", REPOSITORY]), 1)

    def test_cli_reconciles_only_inside_the_hosted_main_job(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_REF": "refs/heads/main",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_OS": "Linux",
            "INTELBREW_BOT_LOGIN": BOT,
        }
        with patch.dict(os.environ, env, clear=True), patch(
                "intelbrew.pin_pr.reconcile_pin", return_value="absent") as reconcile:
            self.assertEqual(main(["--reconcile", "--repository", REPOSITORY]), 0)
        reconcile.assert_called_once_with(REPOSITORY)


if __name__ == "__main__":
    unittest.main()
