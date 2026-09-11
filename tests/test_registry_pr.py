# SPDX-License-Identifier: BSD-2-Clause
import base64
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from helpers import G, record
from intelbrew.core import Error
from intelbrew.registry_pr import (allowed_pr, changed_registry_files,
                                   ensure_pr, validate_manifest_records, _record_from_content,
                                   _rebuild_conflicted_pr, _release_order, _workflow_state,
                                   reconcile, validate_pr)


REPOSITORY = "adriank1410/homebrew-intel"
BRANCH = "bottles/intel-123-1-tool"
RELEASE = "intel-123-1-tool"
HEAD = "c" * 40
BOT = "app/github-actions"


def pull_request(**changes):
    result = {
        "number": 7,
        "state": "OPEN",
        "author": {"login": BOT},
        "headRefName": BRANCH,
        "headRefOid": HEAD,
        "baseRefName": "main",
        "headRepository": {"nameWithOwner": REPOSITORY},
        "baseRepository": {"nameWithOwner": REPOSITORY},
        "isCrossRepository": False,
        "files": [{"path": "registry/tool.json", "status": "modified"}],
        "statusCheckRollup": [],
        "isAutoMergeEnabled": False,
        "autoMergeRequest": None,
        "mergeStateStatus": "CLEAN",
    }
    result.update(changes)
    return result


class RegistryPullRequestTests(unittest.TestCase):
    def setUp(self):
        self._bot_env = patch.dict(os.environ, {"INTELBREW_BOT_LOGIN": BOT})
        self._bot_env.start()

    def tearDown(self):
        self._bot_env.stop()

    def _gh_fixture(self, pr, *, workflow_runs=()):
        manifest_record = copy.deepcopy(record(release=None))
        manifest = {"schema": 1, "verified": True, "root": "tool", "core_commit": G,
                    "brew_commit": G, "workflow_commit": G, "packages": [manifest_record]}
        manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
        asset = {"name": "manifest.json", "browser_download_url": "https://github.com/x",
                 "digest": "sha256:" + __import__("hashlib").sha256(manifest_bytes).hexdigest(),
                 "size": len(manifest_bytes)}
        content = base64.b64encode(json.dumps(record(release=RELEASE)).encode()).decode()
        def fake(args, **kwargs):
            if args[:3] == ["pr", "list", "--repo"]:
                return json.dumps([pr])
            if args[:3] == ["api", "--paginate", "--slurp"]:
                return json.dumps([[{"filename": "registry/tool.json", "status": "modified"}]])
            if args[:2] == ["api", "--method"] and "contents/registry/tool.json" in args[3]:
                return json.dumps({"type": "file", "content": content})
            if args[:2] == ["api", "repos/adriank1410/homebrew-intel/releases/tags/intel-123-1-tool"]:
                return json.dumps({"assets": [asset]})
            if args[:2] == ["api", "repos/adriank1410/homebrew-intel/actions/runs?head_sha=" + HEAD + "&event=workflow_dispatch&per_page=100"]:
                return json.dumps({"workflow_runs": list(workflow_runs)})
            return ""
        return fake, manifest_bytes

    def test_accepts_actual_gh_cli_actions_author(self):
        self.assertTrue(allowed_pr(pull_request(author={"is_bot": True, "login": "app/github-actions"}), repository=REPOSITORY))

    def test_release_order_puts_newer_duplicate_formula_first(self):
        older = pull_request(headRefName="bottles/intel-34338885775-1-go")
        newer = pull_request(headRefName="bottles/intel-34388221074-1-go")
        self.assertLess(_release_order(older), _release_order(newer))

    def test_requires_exact_configured_app_login(self):
        with patch.dict(os.environ, {"INTELBREW_BOT_LOGIN": "app/intelbrew-publisher"}):
            self.assertTrue(allowed_pr(pull_request(author={"is_bot": True, "login": "app/intelbrew-publisher"}), repository=REPOSITORY))
            self.assertFalse(allowed_pr(pull_request(author={"is_bot": True, "login": BOT}), repository=REPOSITORY))

    def test_missing_or_malformed_app_login_fails_closed(self):
        for value in (None, "", "intelbrew-publisher", "app/", "app/foo/bar", "app/UPPER"):
            with self.subTest(value=value):
                env = {} if value is None else {"INTELBREW_BOT_LOGIN": value}
                with patch.dict(os.environ, env, clear=True):
                    self.assertFalse(allowed_pr(pull_request(), repository=REPOSITORY))

    def test_validate_pr_forwards_optional_attestation_token(self):
        pr = pull_request()
        fake, manifest_bytes = self._gh_fixture(pr)
        with patch.dict(os.environ, {"INTELBREW_ATTESTATION_TOKEN": "secret"}), \
             patch("intelbrew.registry_pr.gh", side_effect=fake), \
             patch("intelbrew.registry_pr.download", side_effect=lambda url, target, digest, size: target.write_bytes(manifest_bytes)), \
             patch("intelbrew.registry_pr.attest") as attest_mock:
            validate_pr(REPOSITORY, pr)
        attest_mock.assert_called_once_with(ANY, REPOSITORY, G, token="secret")

    def test_only_same_repo_bot_registry_pr_is_allowed(self):
        self.assertTrue(allowed_pr(pull_request(), repository=REPOSITORY))
        for changes in (
            {"author": {"login": "someone"}},
            {"headRepository": {"nameWithOwner": "other/repo"}},
            {"isCrossRepository": True},
            {"headRefName": "feature/unsafe"},
            {"baseRefName": "develop"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(allowed_pr(pull_request(**changes), repository=REPOSITORY))

    def test_file_allowlist_rejects_deletion_and_non_registry_change(self):
        with self.assertRaises(Error):
            changed_registry_files(pull_request(files=[{"path": "registry/tool.json", "status": "removed"}]))
        with self.assertRaises(Error):
            changed_registry_files(pull_request(files=[{"path": ".github/workflows/checks.yml", "status": "modified"}]))
        self.assertEqual(changed_registry_files(pull_request()), ["registry/tool.json"])

    def test_manifest_records_are_bound_after_normalizing_release(self):
        published = record(release=RELEASE)
        manifest_record = copy.deepcopy(published)
        manifest_record["release"] = None
        manifest = {"schema": 1, "verified": True, "root": "tool", "core_commit": G,
                    "brew_commit": G, "workflow_commit": G, "packages": [manifest_record]}
        validate_manifest_records(manifest, [published], release=RELEASE)

        changed = copy.deepcopy(manifest_record)
        changed["sha256"] = "d" * 64
        with self.assertRaisesRegex(Error, "do not match"):
            validate_manifest_records(manifest, [{**published, "sha256": "d" * 64}], release=RELEASE)

    def test_manifest_rejects_extra_or_missing_registry_record(self):
        item = record(release=None)
        manifest = {"schema": 1, "verified": True, "root": "tool", "core_commit": G,
                    "brew_commit": G, "workflow_commit": G, "packages": [item]}
        with self.assertRaises(Error):
            validate_manifest_records(manifest, [record(release=RELEASE), record(name="other", release=RELEASE)], release=RELEASE)

    def test_manifest_accepts_matching_published_dependency_with_different_artifact(self):
        dependency = record(release=None)
        root = record(name="root", release=None, runtime_dependencies=[{
            "name": "tool", "pkg_version": dependency["pkg_version"],
            "formula_sha256": dependency["formula_sha256"]}])
        manifest = {"schema": 1, "verified": True, "root": "root", "core_commit": G,
                    "brew_commit": G, "workflow_commit": G, "packages": [root, dependency]}
        existing = record(release="intel-999-1-tool", sha256="d" * 64, size=999,
                          source={**dependency["source"], "sha256": "e" * 64, "size": 888})
        validate_manifest_records(manifest, [{**root, "release": RELEASE}], release=RELEASE,
                                  existing=[existing])
        for field, value in (("formula_sha256", "d" * 64), ("filename", "other.tar.gz")):
            with self.subTest(field=field), self.assertRaises(Error):
                validate_manifest_records(manifest, [{**root, "release": RELEASE}], release=RELEASE,
                                          existing=[{**existing, field: value}])

    def test_manifest_never_replaces_root_from_main(self):
        item = record(release=None)
        manifest = {"schema": 1, "verified": True, "root": "tool", "core_commit": G,
                    "brew_commit": G, "workflow_commit": G, "packages": [item]}
        with self.assertRaisesRegex(Error, "do not match"):
            validate_manifest_records(manifest, [], release=RELEASE,
                                      existing=[record(release="intel-999-1-tool")])

    def test_conflicted_rebuild_keeps_main_dependency_and_exactly_replaces_branch(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw); origin = base / "origin.git"; seed = base / "seed"
            subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
            subprocess.run(["git", "init", "-q", str(seed)], check=True)
            for key, value in (("user.name", "test"), ("user.email", "test@example.invalid"),
                               ("commit.gpgsign", "false")):
                subprocess.run(["git", "-C", str(seed), "config", key, value], check=True)
            dependency = record(release="intel-999-1-tool", sha256="d" * 64, size=999)
            (seed / "registry").mkdir(); (seed / "registry/tool.json").write_text(json.dumps(dependency))
            (seed / "README").write_text("base")
            subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "base"], check=True)
            subprocess.run(["git", "-C", str(seed), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(origin)], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)
            subprocess.run(["git", "-C", str(seed), "switch", "-q", "-c", BRANCH], check=True)
            (seed / "conflict").write_text("old")
            subprocess.run(["git", "-C", str(seed), "add", "conflict"], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "branch"], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", BRANCH], check=True)
            head = subprocess.run(["git", "-C", str(seed), "rev-parse", "HEAD"], text=True,
                                  capture_output=True, check=True).stdout.strip()
            root = record(name="root", release=RELEASE)
            proposed_dependency = {**dependency, "release": RELEASE, "sha256": "e" * 64, "size": 1000}
            manifest = {"schema": 1, "verified": True, "root": "root", "core_commit": G,
                        "brew_commit": G, "workflow_commit": G,
                        "packages": [{**root, "release": None}, {**proposed_dependency, "release": None}]}
            def boundary(args, **kwargs):
                self.assertEqual(args[:2], ["repo", "clone"])
                subprocess.run(["git", "clone", "-q", str(origin), args[3]], check=True)
                return ""
            with patch("intelbrew.registry_pr.gh", side_effect=boundary):
                _rebuild_conflicted_pr(REPOSITORY, pull_request(headRefOid=head),
                                       [root, proposed_dependency], [dependency], manifest)
            inspect = base / "inspect"
            subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(origin), str(inspect)], check=True)
            self.assertEqual(json.loads((inspect / "registry/tool.json").read_text()), dependency)
            self.assertEqual(json.loads((inspect / "registry/root.json").read_text()), root)
            self.assertFalse((inspect / "conflict").exists())
            rebuilt_head = subprocess.run(["git", "--git-dir", str(origin), "rev-parse", BRANCH],
                                          text=True, capture_output=True, check=True).stdout.strip()
            stale_snapshot = {**dependency, "release": "intel-998-1-tool"}
            with patch("intelbrew.registry_pr.gh", side_effect=boundary), self.assertRaisesRegex(
                    Error, "main changed"):
                _rebuild_conflicted_pr(REPOSITORY, pull_request(headRefOid=rebuilt_head),
                                       [root, proposed_dependency], [stale_snapshot], manifest)
            self.assertEqual(subprocess.run(["git", "--git-dir", str(origin), "rev-parse", BRANCH],
                                            text=True, capture_output=True, check=True).stdout.strip(), rebuilt_head)

    def test_conflicted_rebuild_refuses_incompatible_existing_record_before_git_io(self):
        dependency = record(release=None)
        root = record(name="root", release=RELEASE)
        manifest = {"schema": 1, "verified": True, "root": "root", "core_commit": G,
                    "brew_commit": G, "workflow_commit": G,
                    "packages": [{**root, "release": None}, dependency]}
        incompatible = {**record(release="intel-999-1-tool"), "formula_sha256": "d" * 64}
        with patch("intelbrew.registry_pr.gh") as boundary, self.assertRaisesRegex(
                Error, "incompatible registry dependency"):
            _rebuild_conflicted_pr(REPOSITORY, pull_request(headRefOid=HEAD),
                                   [root, {**dependency, "release": RELEASE}],
                                   [incompatible], manifest)
        boundary.assert_not_called()

    def test_contents_api_base64_whitespace_is_accepted(self):
        value = json.dumps(record(release=RELEASE)).encode()
        wrapped = base64.b64encode(value).decode().replace("a", "a\n", 1)
        self.assertEqual(_record_from_content({"type": "file", "content": wrapped}, "registry/tool.json")["name"], "tool")

    def test_cancelled_checks_are_retried_until_three_attempts(self):
        run = {"head_sha": HEAD, "path": ".github/workflows/checks.yml",
               "status": "completed", "conclusion": "cancelled"}
        with patch("intelbrew.registry_pr.gh_json", return_value={"workflow_runs": [run]}):
            self.assertEqual(_workflow_state(REPOSITORY, HEAD), "dispatch")
        runs = [dict(run, run_number=n) for n in (1, 2, 3)]
        with patch("intelbrew.registry_pr.gh_json", return_value={"workflow_runs": runs}), \
             self.assertRaisesRegex(Error, "three transient"):
            _workflow_state(REPOSITORY, HEAD)

    def test_unhandled_terminal_conclusions_and_malformed_status_fail_visibly(self):
        for conclusion in ('neutral', 'skipped', 'startup_failure', None, 'unknown'):
            data = {"workflow_runs": [{"head_sha": HEAD, "path": ".github/workflows/checks.yml", "status": "completed", "conclusion": conclusion}]}
            with self.subTest(conclusion=conclusion), patch("intelbrew.registry_pr.gh", return_value=json.dumps(data)), self.assertRaises(Error):
                _workflow_state(REPOSITORY, HEAD)
        data = {"workflow_runs": [{"head_sha": HEAD, "path": ".github/workflows/checks.yml", "status": "unexpected", "conclusion": None}]}
        with patch("intelbrew.registry_pr.gh", return_value=json.dumps(data)), self.assertRaisesRegex(Error, "malformed"):
            _workflow_state(REPOSITORY, HEAD)

    def test_completed_failure_requires_review_and_is_not_retried(self):
        run = {"head_sha": HEAD, "path": ".github/workflows/checks.yml",
               "status": "completed", "conclusion": "failure"}
        with patch("intelbrew.registry_pr.gh_json", return_value={"workflow_runs": [run]}), \
             self.assertRaisesRegex(Error, "completed with failure"):
            _workflow_state(REPOSITORY, HEAD)

    def test_reconcile_retries_cancelled_check_through_real_validation(self):
        pr = pull_request()
        fake, manifest_bytes = self._gh_fixture(pr, workflow_runs=[{
            "head_sha": HEAD, "path": ".github/workflows/checks.yml",
            "status": "completed", "conclusion": "cancelled"}])
        with patch("intelbrew.registry_pr.gh", side_effect=fake) as boundary, \
             patch("intelbrew.registry_pr.download", side_effect=lambda url, target, digest, size: target.write_bytes(manifest_bytes)), \
             patch("intelbrew.registry_pr.attest"):
            reconcile(REPOSITORY)
        calls = [call.args[0] for call in boundary.call_args_list]
        self.assertIn(["workflow", "run", "checks.yml", "--repo", REPOSITORY, "--ref", BRANCH], calls)
        self.assertFalse(any(call[:3] == ["pr", "merge", "7"] for call in calls))

    def test_reconcile_continues_after_conflicting_sibling(self):
        pr = pull_request(statusCheckRollup=[{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}])
        bad = pull_request(number=8, headRefName="bottles/intel-123-1-conflict", mergeStateStatus="DIRTY")
        fake, manifest_bytes = self._gh_fixture(pr, workflow_runs=[{"head_sha": HEAD, "path": ".github/workflows/checks.yml",
                                                                     "status": "completed", "conclusion": "success"}])
        def boundary(args, **kwargs):
            if args[:2] == ["pr", "list"]:
                if "--head" not in args:
                    return json.dumps([bad, pr])
                branch = args[args.index("--head") + 1]
                return json.dumps([bad if branch == bad["headRefName"] else pr])
            return fake(args, **kwargs)
        with patch("intelbrew.registry_pr.gh", side_effect=boundary) as calls, \
             patch("intelbrew.registry_pr.download", side_effect=lambda url, target, digest, size: target.write_bytes(manifest_bytes)), \
             patch("intelbrew.registry_pr.attest"), self.assertRaisesRegex(Error, "PR 8"):
            reconcile(REPOSITORY)
        self.assertTrue(any(call.args[0][:3] == ["pr", "merge", "7"] for call in calls.call_args_list))

    def test_reconcile_closes_stale_registry_conflict_and_merges_next_pr(self):
        stale = pull_request(number=7)
        next_pr = pull_request(
            number=8,
            headRefName="bottles/intel-124-1-next",
            headRefOid="d" * 40,
            statusCheckRollup=[{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        )
        with patch("intelbrew.registry_pr.gh_json", return_value=[stale, next_pr]), \
             patch("intelbrew.registry_pr._find_pr", side_effect=[stale, next_pr]), \
             patch("intelbrew.registry_pr.validate_pr",
                   side_effect=[Error("Refusing to replace a registry root already present on main"),
                                ([], [], {})]), \
             patch("intelbrew.registry_pr._dispatch_checks", return_value="ready"), \
             patch("intelbrew.registry_pr.gh") as gh_mock:
            reconcile(REPOSITORY)
        calls = [call.args[0] for call in gh_mock.call_args_list]
        self.assertIn(["pr", "close", "7", "--repo", REPOSITORY, "--delete-branch"], calls)
        merge = next(call for call in calls if call[:3] == ["pr", "merge", "8"])
        self.assertIn("--match-head-commit", merge)

    def test_reconcile_closes_root_dependency_conflict(self):
        stale = pull_request(number=7)
        with patch("intelbrew.registry_pr.gh_json", return_value=[stale]), \
             patch("intelbrew.registry_pr._find_pr", return_value=stale), \
             patch("intelbrew.registry_pr.validate_pr",
                   side_effect=Error("Refusing to replace a registry root with an existing dependency record")), \
             patch("intelbrew.registry_pr.gh") as gh_mock:
            reconcile(REPOSITORY)
        self.assertIn(["pr", "close", "7", "--repo", REPOSITORY, "--delete-branch"],
                      [call.args[0] for call in gh_mock.call_args_list])

    def test_reconcile_closes_stale_pr_on_manifest_mismatch(self):
        stale = pull_request(number=7)
        with patch("intelbrew.registry_pr.gh_json", return_value=[stale]), \
             patch("intelbrew.registry_pr._find_pr", return_value=stale), \
             patch("intelbrew.registry_pr.validate_pr",
                   side_effect=Error("Registry records do not match the immutable release manifest")), \
             patch("intelbrew.registry_pr.gh") as gh_mock:
            reconcile(REPOSITORY)
        self.assertIn(["pr", "close", "7", "--repo", REPOSITORY, "--delete-branch"],
                      [call.args[0] for call in gh_mock.call_args_list])

    def test_async_branch_update_waits_for_new_head_before_any_merge(self):
        pr = pull_request(mergeStateStatus="BEHIND")
        fake, _ = self._gh_fixture(pr)
        with patch("intelbrew.registry_pr.gh", side_effect=fake) as boundary:
            reconcile(REPOSITORY)
        calls = [call.args[0] for call in boundary.call_args_list]
        self.assertTrue(any("expected_head_sha=" + HEAD in call for call in calls))
        self.assertFalse(any(call[:2] == ["workflow", "run"] or call[:2] == ["pr", "merge"] for call in calls))

    def test_reconcile_refreshes_each_candidate_after_an_earlier_merge(self):
        branch25 = "bottles/intel-123-1-first"
        branch24 = "bottles/intel-123-1-second"
        head25 = "d" * 40
        head24 = "e" * 40
        first = pull_request(number=25, headRefName=branch25, headRefOid=head25,
                             statusCheckRollup=[{"name": "tests", "status": "COMPLETED",
                                                 "conclusion": "SUCCESS"}])
        second = pull_request(number=24, headRefName=branch24, headRefOid=head24,
                              statusCheckRollup=[{"name": "tests", "status": "COMPLETED",
                                                  "conclusion": "SUCCESS"}])
        refreshed_second = dict(second, mergeStateStatus="BEHIND")
        release_data = {}
        for branch, pr in ((branch25, first), (branch24, second)):
            release = branch.removeprefix("bottles/")
            item = record(release=None)
            manifest = {"schema": 1, "verified": True, "root": "tool", "core_commit": G,
                        "brew_commit": G, "workflow_commit": G, "packages": [item]}
            manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
            asset_url = f"https://github.com/{release}.json"
            asset = {"name": "manifest.json", "browser_download_url": asset_url,
                     "digest": "sha256:" + __import__("hashlib").sha256(manifest_bytes).hexdigest(),
                     "size": len(manifest_bytes)}
            content = base64.b64encode(json.dumps(record(release=release)).encode()).decode()
            release_data[branch] = (release, asset, manifest_bytes, content, pr["headRefOid"])

        merged25 = False

        def boundary(args, **kwargs):
            nonlocal merged25
            if args[:3] == ["pr", "list", "--repo"]:
                if "--head" not in args:
                    return json.dumps([first, second])
                branch = args[args.index("--head") + 1]
                if branch == branch25:
                    return json.dumps([] if merged25 else [first])
                return json.dumps([refreshed_second if merged25 else second])
            if args[:2] == ["pr", "merge"] and args[2] == "25":
                merged25 = True
                return ""
            if args[:2] == ["pr", "merge"] and args[2] == "24":
                raise AssertionError(f"stale reconcile attempted merge: {args}")
            if args[:2] == ["api", "--method"] and "update-branch" in args[3]:
                return "{}"
            if args[:2] == ["api", "--paginate"]:
                return json.dumps([[{"filename": "registry/tool.json", "status": "modified"}]])
            if args[:3] == ["api", "--method", "GET"]:
                ref = next(value.removeprefix("ref=") for value in args[4:] if value.startswith("ref="))
                branch = branch25 if ref == head25 else branch24
                return json.dumps({"type": "file", "content": release_data[branch][3]})
            if args[0:2] == ["api", f"repos/{REPOSITORY}/releases/tags/{release_data[branch25][0]}"]:
                return json.dumps({"assets": [release_data[branch25][1]]})
            if args[0:2] == ["api", f"repos/{REPOSITORY}/releases/tags/{release_data[branch24][0]}"]:
                return json.dumps({"assets": [release_data[branch24][1]]})
            if args[0] == "api" and f"repos/{REPOSITORY}/actions/runs?head_sha=" in args[1]:
                head = args[1].split("head_sha=", 1)[1].split("&", 1)[0]
                return json.dumps({"workflow_runs": [{"head_sha": head, "path": ".github/workflows/checks.yml",
                                                       "status": "completed", "conclusion": "success"}]})
            raise AssertionError(f"unexpected gh call: {args}")

        manifests = {value[1]["browser_download_url"]: value[2] for value in release_data.values()}
        with patch("intelbrew.registry_pr.gh", side_effect=boundary) as gh_mock, \
             patch("intelbrew.registry_pr.download", side_effect=lambda url, target, digest, size: target.write_bytes(manifests[url])), \
             patch("intelbrew.registry_pr.attest"):
            reconcile(REPOSITORY)

        calls = [call.args[0] for call in gh_mock.call_args_list]
        self.assertTrue(merged25)
        self.assertTrue(any("pulls/24/update-branch" in " ".join(call) for call in calls))
        self.assertFalse(any(call[:3] == ["pr", "merge", "24"] for call in calls))

    def test_reconcile_rechecks_refreshed_pr_ownership(self):
        candidate = pull_request()
        refreshed = dict(candidate, author={"login": "someone"})

        def boundary(args, **kwargs):
            if args[:3] == ["pr", "list", "--repo"]:
                if "--head" not in args:
                    return json.dumps([candidate])
                return json.dumps([refreshed])
            return ""

        with patch("intelbrew.registry_pr.gh", side_effect=boundary) as gh_mock, \
             self.assertRaisesRegex(Error, "outside the bot-owned"):
            reconcile(REPOSITORY)
        self.assertFalse(any(call.args[0][:3] == ["pr", "merge", "7"]
                             for call in gh_mock.call_args_list))

    def test_existing_checks_and_auto_merge_are_not_dispatched_again(self):
        pr = pull_request(autoMergeRequest={"enabledAt": "now"},
                          statusCheckRollup=[{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}])
        fake, manifest_bytes = self._gh_fixture(pr, workflow_runs=[{"head_sha": HEAD, "name": "Checks",
                                                                     "path": ".github/workflows/checks.yml",
                                                                     "status": "completed", "conclusion": "success"}])
        with patch("intelbrew.registry_pr.gh", side_effect=fake) as gh_mock, \
             patch("intelbrew.registry_pr.download", side_effect=lambda url, target, digest, size: target.write_bytes(manifest_bytes)), \
             patch("intelbrew.registry_pr.attest"):
            ensure_pr(REPOSITORY, BRANCH, RELEASE)
        calls = [call.args[0] for call in gh_mock.call_args_list]
        disable = next(call for call in calls if "--disable-auto" in call)
        merge = next(call for call in calls if call[:3] == ["pr", "merge", "7"] and "--disable-auto" not in call)
        self.assertIn("--disable-auto", disable)
        self.assertIn("--match-head-commit", merge)
        self.assertNotIn("--auto", merge)

    def test_new_pr_dispatches_checks_and_matches_head_for_auto_merge(self):
        pr = pull_request()
        fake, manifest_bytes = self._gh_fixture(pr)
        with patch("intelbrew.registry_pr.gh", side_effect=fake) as gh_mock, \
             patch("intelbrew.registry_pr.download", side_effect=lambda url, target, digest, size: target.write_bytes(manifest_bytes)), \
             patch("intelbrew.registry_pr.attest"):
            ensure_pr(REPOSITORY, BRANCH, RELEASE)
        calls = [call.args[0] for call in gh_mock.call_args_list]
        self.assertIn(["workflow", "run", "checks.yml", "--repo", REPOSITORY, "--ref", BRANCH], calls)
        self.assertFalse(any(call[:3] == ["pr", "merge", "7"] for call in calls))

    def test_clean_successful_checks_merge_immediately_at_exact_head(self):
        pr = pull_request(statusCheckRollup=[{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}])
        fake, manifest_bytes = self._gh_fixture(pr, workflow_runs=[{"head_sha": HEAD, "name": "Checks",
                                                                     "path": ".github/workflows/checks.yml",
                                                                     "status": "completed", "conclusion": "success"}])
        with patch("intelbrew.registry_pr.gh", side_effect=fake) as gh_mock, \
             patch("intelbrew.registry_pr.download", side_effect=lambda url, target, digest, size: target.write_bytes(manifest_bytes)), \
             patch("intelbrew.registry_pr.attest"):
            ensure_pr(REPOSITORY, BRANCH, RELEASE)
        calls = [call.args[0] for call in gh_mock.call_args_list]
        merge = next(call for call in calls if call[:3] == ["pr", "merge", "7"])
        self.assertNotIn("--auto", merge)
        self.assertIn("--squash", merge)
        self.assertIn(HEAD, merge)


if __name__ == "__main__":
    unittest.main()
