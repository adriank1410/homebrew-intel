# SPDX-License-Identifier: BSD-2-Clause
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("select_artifacts", ROOT / "scripts/select_artifacts.py")
select_artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(select_artifacts)


class SelectArtifactsTests(unittest.TestCase):
    def test_selects_only_completed_planned_roots(self):
        payload = [
            {"name": "candidate-good", "expired": False},
            {"name": "candidate-failed", "expired": True},
            {"name": "unrelated", "expired": False},
        ]
        self.assertEqual(
            select_artifacts.select_artifact_names(["failed", "good"], payload, "candidate"),
            ["candidate-good"],
        )

    def test_failed_or_missing_sibling_is_not_synthesized(self):
        self.assertEqual(
            select_artifacts.select_artifact_names(
                ["good", "missing"], [{"name": "candidate-good", "expired": False}], "candidate"
            ),
            ["candidate-good"],
        )

    def test_unplanned_matching_artifact_fails_closed(self):
        with self.assertRaisesRegex(select_artifacts.SelectionError, "unexpected"):
            select_artifacts.select_artifact_names(
                ["good"], [{"name": "candidate-other", "expired": False}], "candidate"
            )

    def test_duplicate_or_malformed_matching_artifact_fails_closed(self):
        with self.assertRaises(select_artifacts.SelectionError):
            select_artifacts.select_artifact_names(
                ["good"], [{"name": "candidate-good", "expired": False}, {"name": "candidate-good", "expired": False}], "candidate"
            )
        with self.assertRaises(select_artifacts.SelectionError):
            select_artifacts.select_artifact_names(["good"], [{"name": 3}], "candidate")

    def test_gh_boundary_reads_current_run_only(self):
        completed = [{"artifacts": [{"name": "verified-good", "expired": False}]}]
        result = type("Result", (), {"stdout": json.dumps(completed)})()
        env = {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "17"}
        with patch.dict(os.environ, env, clear=True), patch.object(select_artifacts.subprocess, "run", return_value=result) as run:
            self.assertEqual(select_artifacts.read_run_artifacts(), completed[0]["artifacts"])
        run.assert_called_once_with(
            [
                "gh", "api", "--paginate", "--slurp",
                "repos/owner/repo/actions/runs/17/artifacts?per_page=100",
            ],
            check=True, capture_output=True, text=True,
        )

    def test_workflow_checks_out_selector_and_guards_empty_matrices(self):
        workflow = (ROOT / ".github/workflows/bottles.yml").read_text()
        for job in ('collect-candidates', 'collect-verified'):
            block = workflow.split('  ' + job + ':\n', 1)[1]
            import re
            block = re.split(r'\n  [a-z-]+:', block, maxsplit=1)[0]
            self.assertIn('contents: read', block)
            self.assertIn('actions: read', block)
            self.assertLess(block.index('actions/checkout@'), block.index('python3 scripts/select_artifacts.py'))
        self.assertIn("needs.collect-candidates.outputs.has_artifacts == 'true'", workflow)
        self.assertIn("needs.collect-verified.outputs.has_artifacts == 'true'", workflow)


if __name__ == "__main__":
    unittest.main()
