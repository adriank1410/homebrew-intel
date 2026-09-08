# SPDX-License-Identifier: BSD-2-Clause
import base64
import copy
import json
import unittest
from unittest.mock import patch

from helpers import G, record
from intelbrew.core import Error
from intelbrew.registry_pr import (BOT, allowed_pr, changed_registry_files,
                                   ensure_pr, validate_manifest_records, _record_from_content)


REPOSITORY = "adriank1410/homebrew-intel"
BRANCH = "bottles/intel-123-1-tool"
RELEASE = "intel-123-1-tool"
HEAD = "c" * 40


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
            if args[:2] == ["api", "--method"] and "contents/registry/tool.json" in args[3]:
                return json.dumps({"type": "file", "content": content})
            if args[:2] == ["api", "repos/adriank1410/homebrew-intel/releases/tags/intel-123-1-tool"]:
                return json.dumps({"assets": [asset]})
            if args[:2] == ["api", "repos/adriank1410/homebrew-intel/actions/runs?head_sha=" + HEAD + "&event=workflow_dispatch&per_page=100"]:
                return json.dumps({"workflow_runs": list(workflow_runs)})
            return ""
        return fake, manifest_bytes

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

    def test_contents_api_base64_whitespace_is_accepted(self):
        value = json.dumps(record(release=RELEASE)).encode()
        wrapped = base64.b64encode(value).decode().replace("a", "a\n", 1)
        self.assertEqual(_record_from_content({"type": "file", "content": wrapped}, "registry/tool.json")["name"], "tool")

    def test_existing_checks_and_auto_merge_are_not_dispatched_again(self):
        pr = pull_request(isAutoMergeEnabled=True)
        fake, manifest_bytes = self._gh_fixture(pr, workflow_runs=[{"head_sha": HEAD, "name": "Checks"}])
        with patch("intelbrew.registry_pr.gh", side_effect=fake) as gh_mock, \
             patch("intelbrew.registry_pr.download", side_effect=lambda url, target, digest, size: target.write_bytes(manifest_bytes)), \
             patch("intelbrew.registry_pr.attest"):
            ensure_pr(REPOSITORY, BRANCH, RELEASE)
        self.assertFalse(any(call.args[0][:2] == ["workflow", "run"] for call in gh_mock.call_args_list))

    def test_new_pr_dispatches_checks_and_matches_head_for_auto_merge(self):
        pr = pull_request()
        fake, manifest_bytes = self._gh_fixture(pr)
        with patch("intelbrew.registry_pr.gh", side_effect=fake) as gh_mock, \
             patch("intelbrew.registry_pr.download", side_effect=lambda url, target, digest, size: target.write_bytes(manifest_bytes)), \
             patch("intelbrew.registry_pr.attest"):
            ensure_pr(REPOSITORY, BRANCH, RELEASE)
        calls = [call.args[0] for call in gh_mock.call_args_list]
        self.assertIn(["workflow", "run", "checks.yml", "--repo", REPOSITORY, "--ref", BRANCH], calls)
        merge = next(call for call in calls if call[:3] == ["pr", "merge", "7"])
        self.assertIn("--auto", merge)
        self.assertIn("--squash", merge)
        self.assertEqual(merge[-1], HEAD)


if __name__ == "__main__":
    unittest.main()
