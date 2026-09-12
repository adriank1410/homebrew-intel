# SPDX-License-Identifier: BSD-2-Clause
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from intelbrew.core import Error, digest
from intelbrew.publish import ensure_release

class PublicationRetryTests(unittest.TestCase):
    def test_existing_identical_release_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "manifest.json"
            asset.write_text("verified")
            release = {"tag_name": "tag", "draft": False, "assets": [{"name": asset.name, "size": asset.stat().st_size, "digest": "sha256:" + digest(asset)}]}
            with patch("intelbrew.publish.run", side_effect=Error("already exists")), patch("intelbrew.publish.gh_json", side_effect=[release, [{"ref": "refs/tags/tag", "object": {"type": "commit", "sha": "a" * 40}}]]):
                ensure_release("owner/repo", "tag", "a" * 40, "title", "notes", [asset])

    def test_existing_release_with_changed_asset_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "manifest.json"
            asset.write_text("verified")
            release = {"tag_name": "tag", "draft": False, "assets": [{"name": asset.name, "size": asset.stat().st_size, "digest": "sha256:" + "b" * 64}]}
            with patch("intelbrew.publish.run", side_effect=Error("already exists")), patch("intelbrew.publish.gh_json", return_value=release):
                with self.assertRaises(Error):
                    ensure_release("owner/repo", "tag", "a" * 40, "title", "notes", [asset])

    def test_new_release_succeeds_without_recovery_lookup(self):
        with patch("intelbrew.publish.run") as run, patch("intelbrew.publish.gh_json") as api:
            ensure_release("owner/repo", "tag", "a" * 40, "title", "notes", [])
            run.assert_called_once()
            api.assert_not_called()

    def test_missing_asset_is_uploaded_without_clobber(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "manifest.json"
            asset.write_text("verified")
            release = {"tag_name": "tag", "draft": False, "assets": []}
            with patch("intelbrew.publish.run", side_effect=[Error("interrupted"), ""]) as run, patch("intelbrew.publish.gh_json", side_effect=[release, [{"ref": "refs/tags/tag", "object": {"type": "commit", "sha": "a" * 40}}]]):
                ensure_release("owner/repo", "tag", "a" * 40, "title", "notes", [asset])
                self.assertEqual(run.call_args.args[0], ["gh", "release", "upload", "tag", "--repo", "owner/repo", str(asset)])

    def test_wrong_tag_never_uploads(self):
        with patch("intelbrew.publish.run", side_effect=Error("exists")) as run, patch("intelbrew.publish.gh_json", side_effect=[{"tag_name": "tag", "draft": False, "assets": []}, [{"ref": "refs/tags/tag", "object": {"type": "commit", "sha": "b" * 40}}]]):
            with self.assertRaises(Error):
                ensure_release("owner/repo", "tag", "a" * 40, "title", "notes", [])
            self.assertEqual(run.call_count, 1)

    def test_existing_branch_is_validated_without_force_push(self):
        self.check_existing_publication([], "abc refs/heads/bottles/tag", expected_pr=True)

    def test_closed_publication_is_not_recreated(self):
        self.check_existing_publication([{"number": 1, "state": "MERGED", "headRefName": "bottles/intel-34689178258-1-tool", "headRepository": {"nameWithOwner": "adriank1410/homebrew-intel"}, "isCrossRepository": False}], "", expected_pr=False)

    def check_existing_publication(self, previous, remote, *, expected_pr):
        import os
        import test_recovery
        from helpers import G
        from intelbrew.publish import publish
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory)
            test_recovery.RecoveryValidationTests().candidate(candidate)
            environment = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": test_recovery.REPOSITORY,
                           "GITHUB_REF": "refs/heads/main", "RUNNER_ENVIRONMENT": "github-hosted",
                           "RUNNER_OS": "Linux", "GITHUB_SHA": G, "INTELBREW_CORE_COMMIT": G,
                           "GITHUB_RUN_ID": test_recovery.RUN_ID, "GITHUB_RUN_ATTEMPT": "1"}
            with patch.dict(os.environ, environment), patch("intelbrew.publish.load_config", return_value={"repository": test_recovery.REPOSITORY, "brew_commit": G}), patch("intelbrew.publish.run") as release_run, patch("intelbrew.publish.gh_json", return_value=previous), patch("intelbrew.publish.git", return_value=remote) as git, patch("intelbrew.publish.ensure_pr") as ensure:
                publish(candidate, "tool")
                self.assertEqual(ensure.call_count, int(expected_pr))
                if not expected_pr:
                    release_run.assert_not_called()
                self.assertTrue(all(call.args[0][0] == "ls-remote" for call in git.call_args_list))

    def test_abandoned_draft_is_published_only_after_missing_assets_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "manifest.json"
            asset.write_text("verified")
            release = {"tag_name": "tag", "draft": True, "target_commitish": "a" * 40, "assets": []}
            with patch("intelbrew.publish.run", side_effect=[Error("exists"), "", ""]) as run, patch("intelbrew.publish.gh_json", side_effect=[release, []]):
                ensure_release("owner/repo", "tag", "a" * 40, "title", "notes", [asset])
                self.assertEqual([c.args[0][2] for c in run.call_args_list], ["create", "upload", "edit"])

    def test_upload_failure_never_publishes_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "manifest.json"
            asset.write_text("verified")
            release = {"tag_name": "tag", "draft": True, "target_commitish": "a" * 40, "assets": []}
            with patch("intelbrew.publish.run", side_effect=Error("offline")) as run, patch("intelbrew.publish.gh_json", side_effect=[release, []]):
                with self.assertRaises(Error):
                    ensure_release("owner/repo", "tag", "a" * 40, "title", "notes", [asset])
                self.assertEqual(run.call_count, 2)
