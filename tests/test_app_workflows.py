import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return json.loads(subprocess.check_output(
        ["ruby", "-ryaml", "-rjson", "-e", "puts JSON.generate(YAML.load_file(ARGV[0]))",
         str(ROOT / ".github/workflows" / name)], text=True))


class AppWorkflowTests(unittest.TestCase):
    def test_recovery_never_builds_or_reattests_source_artifacts(self):
        doc = workflow("recover.yml")
        job = doc["jobs"]["recover"]
        self.assertIn("refs/heads/main", job["if"])
        self.assertEqual(job["permissions"]["attestations"], "read")
        self.assertEqual(job["strategy"]["max-parallel"], 1)
        steps = job["steps"]
        self.assertFalse(any(s.get("uses", "").startswith("actions/attest@") for s in steps))
        download = next(s for s in steps if s.get("uses", "").startswith("actions/download-artifact@"))
        self.assertEqual(download["with"]["run-id"], "${{ inputs.source_run }}")
        publish = next(s for s in steps if "intelbrew.publish" in s.get("run", ""))
        self.assertIn('--source-run "$SOURCE_RUN"', publish["run"])
        self.assertNotIn("GITHUB_SHA", publish.get("env", {}))

    def test_registry_resumes_after_checks_without_recursive_dispatch(self):
        document = workflow("registry.yml")
        trigger = document.get("on", document.get("true"))
        self.assertEqual(trigger["workflow_run"]["workflows"], ["Checks"])
        self.assertEqual(trigger["workflow_run"]["types"], ["completed"])
        job = document["jobs"]["reconcile"]
        self.assertIn("github.event.workflow_run.conclusion == 'success'", job["if"])
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", job["if"])
        self.assertNotIn("gh workflow run registry.yml", str(job["steps"]))

    def test_candidate_artifacts_outlive_the_maximum_workflow_duration(self):
        document = workflow("bottles.yml")
        for stage in ("build", "verify"):
            upload = next(step for step in document["jobs"][stage]["steps"]
                          if step.get("uses", "").startswith("actions/upload-artifact@"))
            self.assertGreaterEqual(upload["with"]["retention-days"], 35)

    def test_source_branch_trials_do_not_queue_behind_main_publication(self):
        document = workflow("bottles.yml")
        self.assertIn("${{ github.ref }}", document["concurrency"]["group"])
        self.assertFalse(document["concurrency"]["cancel-in-progress"])
        for stage in ("build", "verify"):
            self.assertEqual(document["jobs"][stage]["strategy"]["max-parallel"], 5)
        self.assertIn("refs/heads/main", document["jobs"]["publish"]["if"])

    def test_coverage_maintenance_is_independent_and_owner_scoped(self):
        steps = workflow("registry.yml")["jobs"]["reconcile"]["steps"]
        operation = next(step for step in steps if "sync-coverage.py --reconcile" in step.get("run", ""))
        self.assertEqual(operation["env"]["GH_TOKEN"], "${{ steps.app-token.outputs.token }}")
        self.assertEqual(operation["env"]["INTELBREW_COVERAGE_LOGIN"], "${{ github.repository_owner }}")
        self.assertIn("!cancelled()", operation["if"])
        self.assertIn("steps.app-token.outcome == 'success'", operation["if"])

    def test_private_key_is_only_used_by_main_publication_jobs(self):
        for filename, privileged_job in (("bottles.yml", "publish"), ("registry.yml", "reconcile"), ("recover.yml", "recover")):
            jobs = workflow(filename)["jobs"]
            for name, job in jobs.items():
                tokens = [step for step in job["steps"]
                          if step.get("uses", "").startswith("actions/create-github-app-token@")]
                if name != privileged_job:
                    self.assertFalse(tokens)
                    self.assertNotIn("APP_PRIVATE_KEY", json.dumps(job))
                    continue
                self.assertIn("refs/heads/main", job["if"])
                self.assertEqual(len(tokens), 1)
                token = tokens[0]
                self.assertEqual(token["id"], "app-token")
                inputs = token["with"]
                self.assertEqual(inputs["private-key"], "${{ secrets.INTELBREW_APP_PRIVATE_KEY }}")
                self.assertEqual(inputs["repositories"], "homebrew-intel")
                self.assertNotEqual(inputs.get("skip-token-revoke"), True)
                granted = {k: v for k, v in inputs.items() if k.startswith("permission-")}
                expected = {"permission-contents": "write",
                            "permission-pull-requests": "write",
                            "permission-actions": "write"}
                if filename in ("bottles.yml", "recover.yml"):
                    # Releases retain the verified SHA even if main's workflows
                    # have changed while a long-running build was in progress.
                    expected["permission-workflows"] = "write"
                self.assertEqual(granted, expected)

    def test_registry_operations_use_the_issued_token_and_its_bot_identity(self):
        for filename, job_name, module in (("bottles.yml", "publish", "intelbrew.publish"),
                                           ("registry.yml", "reconcile", "intelbrew.registry_pr")):
            steps = workflow(filename)["jobs"][job_name]["steps"]
            operation = next(step for step in steps if module in step.get("run", ""))
            self.assertEqual(operation["env"]["GH_TOKEN"], "${{ steps.app-token.outputs.token }}")
            self.assertEqual(operation["env"]["INTELBREW_BOT_LOGIN"],
                             "app/${{ steps.app-token.outputs.app-slug }}")
            self.assertEqual(operation["env"]["INTELBREW_ATTESTATION_TOKEN"], "${{ github.token }}")
            self.assertIn(workflow(filename)["jobs"][job_name]["permissions"]["attestations"],
                          ("read", "write"))
            token_index = next(i for i, step in enumerate(steps) if step.get("id") == "app-token")
            self.assertLess(token_index, steps.index(operation))
