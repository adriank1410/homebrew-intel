# SPDX-License-Identifier: BSD-2-Clause
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("plan_workflow", ROOT / "scripts/plan-workflow.py")
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)


TARGETS = ["simdjson", "gnupg", "curl"]


class PlanWorkflowTests(unittest.TestCase):
    def test_manual_csv_strips_and_preserves_order(self):
        self.assertEqual(
            plan.requested_roots(" simdjson, gnupg ", TARGETS, allow_csv=True),
            ["simdjson", "gnupg"],
        )

    def test_manual_csv_rejects_empty_duplicate_and_unreviewed(self):
        for value in ["simdjson,", "simdjson,,gnupg", "simdjson,simdjson", "simdjson,evil"]:
            with self.subTest(value=value), self.assertRaises(plan.Error):
                plan.requested_roots(value, TARGETS, allow_csv=True)

    def test_manual_csv_rejects_more_than_fifty(self):
        targets = [f"f{i}" for i in range(51)]
        with self.assertRaises(plan.Error):
            plan.requested_roots(",".join(targets), targets, allow_csv=True)

    def test_push_request_remains_single_root(self):
        with self.assertRaises(plan.Error):
            plan.requested_roots("simdjson,gnupg", TARGETS, allow_csv=False)

    def test_main_writes_matrix_and_uses_pinned_core_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            env = {
                "REQUESTED_FORMULA": "simdjson,gnupg",
                "REQUEST_SOURCE": "workflow_dispatch",
                "GITHUB_OUTPUT": str(output),
            }
            with patch.dict(os.environ, env, clear=True), patch.object(
                plan, "run", return_value="b" * 40 + " refs/heads/main\n"
            ) as run:
                self.assertEqual(plan.main(), 0)
            run.assert_called_once_with([
                "git", "ls-remote", "https://github.com/Homebrew/homebrew-core.git",
                "refs/heads/main",
            ])
            lines = output.read_text().splitlines()
            self.assertEqual(json.loads(lines[0].split("=", 1)[1]), {"root": TARGETS[:2]})


if __name__ == "__main__":
    unittest.main()
