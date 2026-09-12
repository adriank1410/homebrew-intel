# SPDX-License-Identifier: BSD-2-Clause
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import G, archive, record
from intelbrew.core import Error
from intelbrew.recovery import discover_recovery_roots, validate_recovery


REPOSITORY = "adriank1410/homebrew-intel"
RUN_ID = "34689178258"
ROOT = "tool"


class RecoveryValidationTests(unittest.TestCase):
    def candidate(self, directory: Path, *, run_id=RUN_ID):
        bottle, package = archive(directory)
        source = directory / package["source"]["filename"]
        with tarfile.open(source, "w:gz") as bundle:
            data = b"{}\n"
            member = tarfile.TarInfo("sources.json")
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
        package["source"] = {
            "filename": source.name,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "size": source.stat().st_size,
        }
        package["run_id"] = run_id
        manifest = {
            "schema": 1,
            "root": ROOT,
            "core_commit": G,
            "brew_commit": G,
            "workflow_commit": G,
            "plan": {"schema": 1, "roots": [ROOT], "order": [], "nodes": {}},
            "packages": [package],
            "verified": True,
        }
        (directory / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        return manifest

    @staticmethod
    def run_payload(**changes):
        result = {
            "id": int(RUN_ID),
            "run_attempt": 1,
            "path": ".github/workflows/bottles.yml",
            "head_branch": "main",
            "head_sha": G,
            "status": "completed",
            "conclusion": "success",
            "head_repository": {"full_name": REPOSITORY},
        }
        result.update(changes)
        return result

    @staticmethod
    def successful_job(root=ROOT):
        return {"name": f"verify ({root})", "status": "completed", "conclusion": "success"}

    @staticmethod
    def failed_publisher_job(root=ROOT, attempt=1, conclusion="failure"):
        return {"name": f"publish ({root})", "status": "completed",
                "conclusion": conclusion, "run_attempt": attempt}

    @staticmethod
    def reusable_job(stage, root=ROOT, *, conclusion="success", attempt=None):
        job = {"name": f"root-pipeline ({root}) / {stage} ({root})",
               "status": "completed", "conclusion": conclusion}
        if attempt is not None:
            job["run_attempt"] = attempt
        return job

    def gh_fixture(self, run, *, jobs=None, previous_jobs=None):
        jobs = [self.successful_job(), self.failed_publisher_job()] if jobs is None else jobs
        previous_jobs = [] if previous_jobs is None else previous_jobs

        def fake(arguments):
            if arguments == ["api", f"repos/{REPOSITORY}/actions/runs/{RUN_ID}"]:
                return run
            latest = [
                f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/jobs?filter=latest&per_page=100",
            ]
            if arguments == ["api", "--paginate", "--slurp", latest[0]]:
                return [{"jobs": jobs}]
            prior = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/attempts/1/jobs?per_page=100"
            if arguments == ["api", "--paginate", "--slurp", prior]:
                return [{"jobs": previous_jobs}]
            raise AssertionError(f"unexpected GitHub API request: {arguments!r}")

        return fake

    def validate(self, *, source_run=RUN_ID, run=None, jobs=None, previous_jobs=None, env=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.candidate(directory)
            attestations = []
            with patch.dict(os.environ, {"INTELBREW_ATTESTATION_TOKEN": "read-token", **(env or {})}, clear=False), \
                 patch("intelbrew.recovery.gh_json", side_effect=self.gh_fixture(
                     run or self.run_payload(), jobs=jobs, previous_jobs=previous_jobs)), \
                 patch("intelbrew.recovery.attest", side_effect=lambda path, repository, commit, **kwargs: attestations.append((path.name, repository, commit, kwargs))):
                result = validate_recovery(directory, ROOT, source_run, manifest, REPOSITORY)
            return result, attestations

    def test_accepts_source_run_and_attests_every_candidate_file(self):
        result, attestations = self.validate()
        self.assertEqual(result, (G, G, RUN_ID, "1"))
        self.assertEqual({item[0] for item in attestations},
                         {"manifest.json", "tool--1.0.sequoia.bottle.tar.gz", "tool--1.0.sources.tar.gz"})
        self.assertEqual({item[1] for item in attestations}, {REPOSITORY})
        self.assertEqual({item[2] for item in attestations}, {G})
        self.assertEqual({item[3]["token"] for item in attestations}, {"read-token"})

    def test_accepts_completed_run_failed_only_by_publication_jobs(self):
        result, _ = self.validate(run=self.run_payload(conclusion="failure"))
        self.assertEqual(result, (G, G, RUN_ID, "1"))

    def test_discovers_failed_published_roots_with_live_verified_artifacts(self):
        run = self.run_payload(conclusion="failure")
        jobs = [self.failed_publisher_job(),
                {"name": "publish (other)", "status": "completed", "conclusion": "failure",
                 "run_attempt": 1},
                self.successful_job(),
                {"name": "verify (other)", "status": "completed", "conclusion": "success"}]
        artifacts = [{"name": "verified-tool", "expired": False},
                     {"name": "verified-other", "expired": True},
                     {"name": "verified-unpublished", "expired": False}]

        def fake(arguments):
            if arguments == ["api", f"repos/{REPOSITORY}/actions/runs/{RUN_ID}"]:
                return run
            jobs_endpoint = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/jobs?filter=latest&per_page=100"
            if arguments == ["api", "--paginate", "--slurp", jobs_endpoint]:
                return [{"jobs": jobs}]
            artifacts_endpoint = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/artifacts?per_page=100"
            if arguments == ["api", "--paginate", "--slurp", artifacts_endpoint]:
                return [{"artifacts": artifacts}]
            raise AssertionError(arguments)

        with patch("intelbrew.recovery.gh_json", side_effect=fake):
            self.assertEqual(discover_recovery_roots(REPOSITORY, RUN_ID), [ROOT])

    def test_discovery_uses_prior_successful_verification_attempt(self):
        run = self.run_payload(run_attempt=2, conclusion="failure")
        latest = [self.failed_publisher_job(attempt=2)]
        prior = [self.successful_job()]
        artifacts = [{"name": "verified-tool", "expired": False}]

        def fake(arguments):
            if arguments == ["api", f"repos/{REPOSITORY}/actions/runs/{RUN_ID}"]:
                return run
            latest_endpoint = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/jobs?filter=latest&per_page=100"
            if arguments == ["api", "--paginate", "--slurp", latest_endpoint]:
                return [{"jobs": latest}]
            prior_endpoint = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/attempts/1/jobs?per_page=100"
            if arguments == ["api", "--paginate", "--slurp", prior_endpoint]:
                return [{"jobs": prior}]
            artifacts_endpoint = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/artifacts?per_page=100"
            if arguments == ["api", "--paginate", "--slurp", artifacts_endpoint]:
                return [{"artifacts": artifacts}]
            raise AssertionError(arguments)

        with patch("intelbrew.recovery.gh_json", side_effect=fake):
            self.assertEqual(discover_recovery_roots(REPOSITORY, RUN_ID), [ROOT])

    def test_discovery_accepts_reusable_workflow_job_prefix(self):
        run = self.run_payload(conclusion="failure")
        jobs = [self.reusable_job("verify"),
                self.reusable_job("publish", conclusion="failure", attempt=1)]
        artifacts = [{"name": "verified-tool", "expired": False}]

        def fake(arguments):
            if arguments == ["api", f"repos/{REPOSITORY}/actions/runs/{RUN_ID}"]:
                return run
            jobs_endpoint = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/jobs?filter=latest&per_page=100"
            if arguments == ["api", "--paginate", "--slurp", jobs_endpoint]:
                return [{"jobs": jobs}]
            artifacts_endpoint = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/artifacts?per_page=100"
            if arguments == ["api", "--paginate", "--slurp", artifacts_endpoint]:
                return [{"artifacts": artifacts}]
            raise AssertionError(arguments)

        with patch("intelbrew.recovery.gh_json", side_effect=fake):
            self.assertEqual(discover_recovery_roots(REPOSITORY, RUN_ID), [ROOT])

    def test_reusable_workflow_job_prefix_must_match_stage_root(self):
        jobs = [self.reusable_job("verify"),
                {"name": "root-pipeline (other) / publish (tool)",
                 "status": "completed", "conclusion": "failure", "run_attempt": 1}]
        with self.assertRaisesRegex(Error, "publication job"):
            self.validate(jobs=jobs)

    def test_discovery_rejects_a_successful_source_run(self):
        with patch("intelbrew.recovery.gh_json", return_value=self.run_payload(conclusion="success")):
            with self.assertRaisesRegex(Error, "failed"):
                discover_recovery_roots(REPOSITORY, RUN_ID)

    def test_discovery_rejects_more_than_the_matrix_bound(self):
        roots = [f"formula{i}" for i in range(257)]
        jobs = ([{"name": f"publish ({root})", "status": "completed",
                  "conclusion": "failure", "run_attempt": 1}
                 for root in roots] +
                [{"name": f"verify ({root})", "status": "completed",
                  "conclusion": "success"}
                 for root in roots])
        artifacts = [{"name": f"verified-{root}", "expired": False} for root in roots]

        def fake(arguments):
            if arguments == ["api", f"repos/{REPOSITORY}/actions/runs/{RUN_ID}"]:
                return self.run_payload(conclusion="failure")
            jobs_endpoint = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/jobs?filter=latest&per_page=100"
            if arguments == ["api", "--paginate", "--slurp", jobs_endpoint]:
                return [{"jobs": jobs}]
            artifacts_endpoint = f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/artifacts?per_page=100"
            if arguments == ["api", "--paginate", "--slurp", artifacts_endpoint]:
                return [{"artifacts": artifacts}]
            raise AssertionError(arguments)

        with patch("intelbrew.recovery.gh_json", side_effect=fake):
            with self.assertRaisesRegex(Error, "bound"):
                discover_recovery_roots(REPOSITORY, RUN_ID)

    def test_rejects_foreign_workflow_path(self):
        with self.assertRaisesRegex(Error, "workflow path"):
            self.validate(run=self.run_payload(path=".github/workflows/other.yml"))

    def test_rejects_non_main_source_branch(self):
        with self.assertRaisesRegex(Error, "main branch"):
            self.validate(run=self.run_payload(head_branch="feature/recovery"))

    def test_rejects_workflow_sha_mismatch(self):
        with self.assertRaisesRegex(Error, "workflow commit"):
            self.validate(run=self.run_payload(head_sha="c" * 40))

    def test_rejects_source_run_id_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.candidate(directory)
            with patch.dict(os.environ, {"INTELBREW_ATTESTATION_TOKEN": "read-token"}), \
                 patch("intelbrew.recovery.gh_json") as gh_json:
                with self.assertRaisesRegex(Error, "run id"):
                    validate_recovery(directory, ROOT, "123", manifest, REPOSITORY)
            gh_json.assert_not_called()

    def test_rejects_non_ascii_source_run_id(self):
        with self.assertRaisesRegex(Error, "source run id"):
            self.validate(source_run="１２３")

    def test_rejects_package_run_id_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.candidate(directory, run_id="123")
            with patch.dict(os.environ, {"INTELBREW_ATTESTATION_TOKEN": "read-token"}), \
                 patch("intelbrew.recovery.gh_json") as gh_json:
                with self.assertRaisesRegex(Error, "Package run id"):
                    validate_recovery(directory, ROOT, RUN_ID, manifest, REPOSITORY)
            gh_json.assert_not_called()

    def test_rejects_failed_root_verification(self):
        failed = {"name": f"verify ({ROOT})", "status": "completed", "conclusion": "failure"}
        with self.assertRaisesRegex(Error, "verification job"):
            self.validate(run=self.run_payload(conclusion="failure"),
                          jobs=[failed, self.failed_publisher_job()])

    def test_reuses_prior_successful_root_when_latest_retry_omits_it(self):
        latest = [{"name": "verify (other)", "status": "completed", "conclusion": "success"},
                  self.failed_publisher_job(attempt=2)]
        result, _ = self.validate(run=self.run_payload(run_attempt=2), jobs=latest,
                                  previous_jobs=[self.successful_job()])
        self.assertEqual(result, (G, G, RUN_ID, "2"))

    def test_binds_release_attempt_to_failed_publisher_job(self):
        jobs = [self.successful_job(), self.failed_publisher_job(attempt=1)]
        result, _ = self.validate(run=self.run_payload(run_attempt=2), jobs=jobs)
        self.assertEqual(result, (G, G, RUN_ID, "1"))

    def test_rejects_missing_publisher_job(self):
        with self.assertRaisesRegex(Error, "publication job"):
            self.validate(jobs=[self.successful_job()])

    def test_rejects_successful_publisher_job(self):
        with self.assertRaisesRegex(Error, "publication job"):
            self.validate(jobs=[self.successful_job(), self.failed_publisher_job(conclusion="success")])

    def test_rejects_duplicate_publisher_jobs(self):
        with self.assertRaisesRegex(Error, "publication job"):
            self.validate(jobs=[self.successful_job(), self.failed_publisher_job(),
                                self.failed_publisher_job()])

    def test_rejects_publisher_attempt_after_source_run_attempt(self):
        with self.assertRaisesRegex(Error, "Publication job attempt"):
            self.validate(run=self.run_payload(run_attempt=1),
                          jobs=[self.successful_job(), self.failed_publisher_job(attempt=2)])

    def test_rejects_non_positive_publisher_attempt(self):
        publisher = self.failed_publisher_job(attempt=0)
        with self.assertRaisesRegex(Error, "publication job attempt"):
            self.validate(jobs=[self.successful_job(), publisher])

    def test_ignores_recovery_runner_identity(self):
        result, _ = self.validate(env={"GITHUB_SHA": "c" * 40,
                                       "GITHUB_RUN_ID": "99999999999",
                                       "GITHUB_RUN_ATTEMPT": "99"})
        self.assertEqual(result, (G, G, RUN_ID, "1"))

    def test_fails_closed_when_attestation_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.candidate(directory)
            with patch.dict(os.environ, {"INTELBREW_ATTESTATION_TOKEN": "read-token"}, clear=False), \
                 patch("intelbrew.recovery.gh_json", side_effect=self.gh_fixture(self.run_payload())), \
                 patch("intelbrew.recovery.attest", side_effect=Error("attestation failed")):
                with self.assertRaisesRegex(Error, "attestation failed"):
                    validate_recovery(directory, ROOT, RUN_ID, manifest, REPOSITORY)

    def test_requires_dedicated_attestation_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.candidate(directory)
            with patch.dict(os.environ, {"INTELBREW_ATTESTATION_TOKEN": ""}, clear=False), \
                 patch("intelbrew.recovery.gh_json", side_effect=self.gh_fixture(self.run_payload())), \
                 patch("intelbrew.recovery.attest") as attest_mock:
                with self.assertRaisesRegex(Error, "attestation token"):
                    validate_recovery(directory, ROOT, RUN_ID, manifest, REPOSITORY)
            attest_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
