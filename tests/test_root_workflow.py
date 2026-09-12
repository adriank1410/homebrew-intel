# SPDX-License-Identifier: BSD-2-Clause
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return json.loads(subprocess.check_output(
        ["ruby", "-ryaml", "-rjson", "-e", "puts JSON.generate(YAML.load_file(ARGV[0]))",
         str(ROOT / ".github/workflows" / name)], text=True))


class RootWorkflowTests(unittest.TestCase):
    def test_caller_fans_out_complete_root_pipelines_with_bounded_concurrency(self):
        document = workflow("bottles.yml")
        job = document["jobs"]["root-pipeline"]
        self.assertEqual(job["uses"], "./.github/workflows/bottle-root.yml")
        self.assertEqual(job["strategy"]["max-parallel"], 5)
        self.assertFalse(job["strategy"]["fail-fast"])
        self.assertEqual(job["with"], {
            "root": "${{ matrix.root }}",
            "core_commit": "${{ needs.plan.outputs.core_commit }}",
        })
        self.assertEqual(job["secrets"], {
            "app_private_key": "${{ secrets.INTELBREW_APP_PRIVATE_KEY }}",
        })
        self.assertEqual(job["permissions"], {
            "contents": "read",
            "actions": "read",
            "id-token": "write",
            "attestations": "write",
        })
        self.assertNotIn("inherit", json.dumps(job))
        self.assertIn("github.event_name == 'schedule'", job["if"])
        self.assertIn("inputs.formula == 'all'", job["if"])

    def test_root_lock_uses_canonical_planned_root_not_raw_dispatch(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("root_lock_planner", ROOT / "scripts/plan-workflow.py")
        planner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(planner)
        for requested in ("simdutf", " simdutf ", "homebrew/core/simdutf"):
            self.assertEqual(planner.requested_roots(requested, ["simdutf"], allow_csv=True), ["simdutf"])
        job = workflow("bottles.yml")["jobs"]["root-pipeline"]
        self.assertEqual(job["concurrency"], {
            "group": "intelbrew-root-${{ github.ref }}-${{ matrix.root }}",
            "cancel-in-progress": False,
        })

    def test_manual_single_root_does_not_wait_for_sweep(self):
        group = workflow("bottles.yml")["concurrency"]["group"]
        self.assertIn("github.event_name == 'workflow_dispatch'", group)
        self.assertIn("inputs.formula != 'all'", group)
        self.assertIn("!contains(inputs.formula, ',')", group)
        self.assertIn("format('-{0}', inputs.formula)", group)
        self.assertIn("${{ github.ref }}", group)

    def test_root_workflow_declares_explicit_inputs_and_secret(self):
        document = workflow("bottle-root.yml")
        trigger = document.get("on", document.get("true"))
        call = trigger["workflow_call"]
        self.assertEqual(call["inputs"], {
            "root": {"description": "Formula root", "required": True, "type": "string"},
            "core_commit": {"description": "Pinned Homebrew/core commit", "required": True, "type": "string"},
        })
        self.assertEqual(call["secrets"], {"app_private_key": {"required": True}})

    def test_root_workflow_keeps_build_verify_publish_in_one_run(self):
        jobs = workflow("bottle-root.yml")["jobs"]
        self.assertEqual(set(jobs), {"build", "verify", "publish"})

        build = jobs["build"]
        self.assertEqual(build["name"], "build (${{ inputs.root }})")
        self.assertEqual(build["runs-on"], "macos-15-intel")
        self.assertNotIn("needs", build)
        build_upload = next(step for step in build["steps"]
                            if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(build_upload["with"]["name"], "candidate-${{ inputs.root }}")

        verify = jobs["verify"]
        self.assertEqual(verify["name"], "verify (${{ inputs.root }})")
        self.assertEqual(verify["needs"], "build")
        self.assertEqual(verify["runs-on"], "macos-15-intel")
        download = next(step for step in verify["steps"]
                        if step.get("uses", "").startswith("actions/download-artifact@"))
        self.assertEqual(download["with"]["name"], "candidate-${{ inputs.root }}")
        verify_upload = next(step for step in verify["steps"]
                             if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(verify_upload["with"]["name"], "verified-${{ inputs.root }}")

        publish = jobs["publish"]
        self.assertEqual(publish["name"], "publish (${{ inputs.root }})")
        self.assertEqual(publish["needs"], "verify")
        self.assertEqual(publish["runs-on"], "ubuntu-24.04")
        self.assertIn("refs/heads/main", publish["if"])
        self.assertIn("needs.verify.result == 'success'", publish["if"])
        self.assertIn("github.repository == 'adriank1410/homebrew-intel'", publish["if"])
        self.assertEqual(publish["permissions"], {
            "contents": "read",
            "actions": "read",
            "id-token": "write",
            "attestations": "write",
        })
        publish_download = next(step for step in publish["steps"]
                                if step.get("uses", "").startswith("actions/download-artifact@"))
        self.assertEqual(publish_download["with"]["name"], "verified-${{ inputs.root }}")
        self.assertTrue(any(step.get("uses", "").startswith("actions/attest@")
                            for step in publish["steps"]))
        token = next(step for step in publish["steps"]
                      if step.get("uses", "").startswith("actions/create-github-app-token@"))
        self.assertEqual(token["with"]["private-key"], "${{ secrets.app_private_key }}")
        operation = next(step for step in publish["steps"]
                         if "intelbrew.publish" in step.get("run", ""))
        self.assertEqual(operation["env"]["GH_TOKEN"], "${{ steps.app-token.outputs.token }}")


if __name__ == "__main__":
    unittest.main()
