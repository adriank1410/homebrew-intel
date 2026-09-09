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
    def test_coverage_maintenance_is_independent_and_owner_scoped(self):
        steps = workflow("registry.yml")["jobs"]["reconcile"]["steps"]
        operation = next(step for step in steps if "sync-coverage.py --reconcile" in step.get("run", ""))
        self.assertEqual(operation["env"]["GH_TOKEN"], "${{ steps.app-token.outputs.token }}")
        self.assertEqual(operation["env"]["INTELBREW_COVERAGE_LOGIN"], "${{ github.repository_owner }}")
        self.assertIn("!cancelled()", operation["if"])
        self.assertIn("steps.app-token.outcome == 'success'", operation["if"])

    def test_private_key_is_only_used_by_main_publication_jobs(self):
        for filename, privileged_job in (("bottles.yml", "publish"), ("registry.yml", "reconcile")):
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
                self.assertEqual(granted, {"permission-contents": "write",
                                          "permission-pull-requests": "write",
                                          "permission-actions": "write"})

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
