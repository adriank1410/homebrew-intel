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
BASE_REF = "c" * 40
FRESH_BASE_REF = "d" * 40

BASE_POLICY = {
    "schema": 1,
    "brew_commit": "e" * 40,
    "core_commit": "f" * 40,
    "blocked_source_builds": ["llvm"],
    "redistribution_exceptions": {},
}
ONE_PIN_POLICY = {**BASE_POLICY, "brew_commit": "1" * 40}
BOTH_PIN_POLICY = {
    **BASE_POLICY,
    "brew_commit": "1" * 40,
    "core_commit": "2" * 40,
}


def pin_pr(**changes):
    result = {
        "number": 816,
        "state": "OPEN",
        "author": {"login": BOT, "is_bot": True},
        "headRefName": "automation/homebrew-pins",
        "headRefOid": HEAD,
        "baseRefName": "main",
        "baseRefOid": BASE_REF,
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

    def _run_policy(self, *, pr=None, pr_sequence=None, base_policy=None,
                    head_policy=None, policy_by_ref=None, policy_error=None):
        snapshots = list(pr_sequence or [pr or pin_pr()])
        policies = policy_by_ref if policy_by_ref is not None else {
            BASE_REF: BASE_POLICY if base_policy is None else base_policy,
            HEAD: ONE_PIN_POLICY if head_policy is None else head_policy,
        }
        calls = []
        self.last_policy_calls = calls

        def fake(args, **kwargs):
            calls.append(args)
            if args[:2] == ["pr", "list"]:
                if not snapshots:
                    raise AssertionError("unexpected additional PR listing")
                return json.dumps([snapshots.pop(0)])
            if args[:2] == ["api", "--paginate"]:
                return json.dumps([[{
                    "filename": "policy/config.json", "status": "modified",
                }]])
            if args and args[0] == "api" and "-H" in args:
                endpoint = args[args.index("-H") + 2]
                prefix = f"repos/{REPOSITORY}/contents/policy/config.json?ref="
                if not endpoint.startswith(prefix):
                    raise AssertionError(f"unexpected raw API endpoint: {endpoint}")
                ref = endpoint[len(prefix):]
                if policy_error is not None:
                    raise policy_error
                if ref not in policies:
                    raise AssertionError(f"unexpected policy ref: {ref}")
                return json.dumps(policies[ref])
            if args[:2] == ["pr", "merge"]:
                return ""
            raise AssertionError(args)

        with patch("intelbrew.pin_pr.gh", side_effect=fake):
            outcome = reconcile_pin(REPOSITORY)
        return outcome, calls

    def _listed(self, pr, files=None):
        if files is None:
            files = [[{"filename": "policy/config.json", "status": "modified"}]]
        result = [json.dumps([pr]), json.dumps(files)]
        if (pr.get("mergeStateStatus") == "CLEAN" and
                pr.get("statusCheckRollup", [{}])[0].get("conclusion") == "SUCCESS"):
            result.extend([json.dumps(BASE_POLICY), json.dumps(ONE_PIN_POLICY)])
        result.append("")
        return result

    def test_merges_the_exact_head_after_tests_succeed(self):
        outcome, calls = self._run(self._listed(pin_pr()))

        self.assertEqual(outcome, "merged")
        self.assertEqual(calls[-1], [
            "pr", "merge", "816", "--repo", REPOSITORY, "--squash",
            "--match-head-commit", HEAD,
        ])
        self.assertNotIn("--auto", calls[-1])
        self.assertNotIn("--delete-branch", calls[-1])

    def test_merges_when_one_or_both_pin_values_change(self):
        for policy in (ONE_PIN_POLICY, BOTH_PIN_POLICY):
            with self.subTest(changed_pins=[
                    key for key in ("brew_commit", "core_commit")
                    if policy[key] != BASE_POLICY[key]]):
                outcome, calls = self._run_policy(head_policy=policy)
                self.assertEqual(outcome, "merged")
                self.assertEqual(calls[-1][-2:], ["--match-head-commit", HEAD])

    def test_reads_policy_at_the_exact_base_and_head_commits(self):
        outcome, calls = self._run_policy()

        self.assertEqual(outcome, "merged")
        self.assertEqual(
            [call[-1] for call in calls if call[:2] == ["api", "-H"]],
            [
                f"repos/{REPOSITORY}/contents/policy/config.json?ref={BASE_REF}",
                f"repos/{REPOSITORY}/contents/policy/config.json?ref={HEAD}",
            ],
        )
        self.assertTrue(all(
            call[call.index("-H") + 1] == "Accept: application/vnd.github.raw+json"
            for call in calls if call[:2] == ["api", "-H"]
        ))
        list_call = next(call for call in calls if call[:2] == ["pr", "list"])
        self.assertIn("baseRefOid", list_call[list_call.index("--json") + 1].split(","))

    def test_refuses_a_pr_without_a_valid_base_commit(self):
        with self.assertRaisesRegex(Error, "valid base commit"):
            self._run_policy(pr=pin_pr(baseRefOid="main"))
        self.assertFalse(any(
            call[:2] == ["pr", "merge"] and "--squash" in call
            for call in self.last_policy_calls
        ))

    def test_refuses_added_removed_changed_or_retyped_non_pin_fields(self):
        removed = {key: value for key, value in BASE_POLICY.items()
                   if key != "blocked_source_builds"}
        changed = {**BASE_POLICY, "redistribution_exceptions": {"openssl": "reviewed"}}
        retyped = {**BASE_POLICY, "schema": True}
        cases = {
            "added field": {**BASE_POLICY, "new_policy": "unexpected"},
            "removed field": removed,
            "changed field": changed,
            "retyped field": retyped,
        }
        for case, head in cases.items():
            with self.subTest(case=case):
                with self.assertRaisesRegex(Error, "outside the pin commits"):
                    self._run_policy(head_policy={
                        **head,
                        "brew_commit": ONE_PIN_POLICY["brew_commit"],
                        "core_commit": ONE_PIN_POLICY["core_commit"],
                    })
                self.assertFalse(any(
                    call[:2] == ["pr", "merge"] and "--squash" in call
                    for call in self.last_policy_calls
                ))

    def test_refuses_malformed_policy_objects_or_pin_values(self):
        bad_values = (
            ("non-object head", BASE_POLICY, []),
            ("non-object base", [], ONE_PIN_POLICY),
            ("missing head pin", BASE_POLICY,
             {key: value for key, value in ONE_PIN_POLICY.items()
              if key != "brew_commit"}),
            ("invalid head SHA", BASE_POLICY,
             {**ONE_PIN_POLICY, "core_commit": "not-a-sha"}),
            ("missing base pin", {key: value for key, value in BASE_POLICY.items()
                                   if key != "core_commit"}, ONE_PIN_POLICY),
            ("invalid base SHA", {**BASE_POLICY, "brew_commit": "0" * 39},
             ONE_PIN_POLICY),
        )
        for label, base, head in bad_values:
            with self.subTest(case=label):
                with self.assertRaisesRegex(Error, "invalid policy"):
                    self._run_policy(base_policy=base, head_policy=head)
                self.assertFalse(any(
                    call[:2] == ["pr", "merge"] and "--squash" in call
                    for call in self.last_policy_calls
                ))

    def test_refuses_a_malformed_policy_response_and_failed_policy_reads(self):
        malformed_calls = []

        def malformed_transport(args, **kwargs):
            malformed_calls.append(args)
            if args[:2] == ["pr", "list"]:
                return json.dumps([pin_pr()])
            if args[:2] == ["api", "--paginate"]:
                return json.dumps([[{
                    "filename": "policy/config.json", "status": "modified",
                }]])
            if args and args[0] == "api" and "-H" in args:
                return "{invalid JSON"
            raise AssertionError(args)

        with patch("intelbrew.pin_pr.gh", side_effect=malformed_transport):
            with self.assertRaisesRegex(Error, "invalid policy"):
                reconcile_pin(REPOSITORY)
        self.assertFalse(any(
            call[:2] == ["pr", "merge"] and "--squash" in call
            for call in malformed_calls
        ))

        with self.assertRaises(OSError):
            self._run_policy(policy_error=OSError("policy fetch failed"))
        self.assertFalse(any(
            call[:2] == ["pr", "merge"] and "--squash" in call
            for call in self.last_policy_calls
        ))

    def test_validates_the_refreshed_head_after_disabling_auto_merge(self):
        stale = pin_pr(autoMergeRequest={"commitHeadline": "old"})
        fresh = pin_pr(headRefOid=FRESH_HEAD, baseRefOid=FRESH_BASE_REF)
        outcome, calls = self._run_policy(
            pr_sequence=[stale, fresh],
            policy_by_ref={
                FRESH_BASE_REF: BASE_POLICY,
                FRESH_HEAD: ONE_PIN_POLICY,
            },
        )

        self.assertEqual(outcome, "merged")
        self.assertIn("--disable-auto", calls[1])
        self.assertEqual(calls[-1][-2:], ["--match-head-commit", FRESH_HEAD])
        self.assertEqual(
            [call[-1] for call in calls if call[:2] == ["api", "-H"]],
            [
                f"repos/{REPOSITORY}/contents/policy/config.json?ref={FRESH_BASE_REF}",
                f"repos/{REPOSITORY}/contents/policy/config.json?ref={FRESH_HEAD}",
            ],
        )

    def test_disables_queued_auto_merge_and_uses_the_refreshed_head(self):
        stale = pin_pr(autoMergeRequest={"commitHeadline": "old"})
        fresh = pin_pr(headRefOid=FRESH_HEAD)
        outcome, calls = self._run([
            json.dumps([stale]),
            "",
            json.dumps([fresh]),
            json.dumps([[{"filename": "policy/config.json", "status": "modified"}]]),
            json.dumps(BASE_POLICY),
            json.dumps(ONE_PIN_POLICY),
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
