# SPDX-License-Identifier: BSD-2-Clause
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "update_homebrew_pins", ROOT / "scripts/update-homebrew-pins.py")
pins = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pins)


class HomebrewPinUpdateTests(unittest.TestCase):
    def test_maintenance_workflow_updates_a_current_branch_with_app_auth(self):
        workflow = json.loads(subprocess.check_output([
            "ruby", "-ryaml", "-rjson", "-e",
            "puts JSON.generate(YAML.load_file(ARGV[0]))",
            str(ROOT / ".github/workflows/homebrew-pins.yml")], text=True))
        job = workflow["jobs"]["refresh"]
        self.assertEqual(workflow["concurrency"], {
            "group": "homebrew-pin-maintenance", "cancel-in-progress": False})
        script = "\n".join(step.get("run", "") for step in job["steps"])
        self.assertIn("git fetch origin main", script)
        self.assertIn("git merge --no-edit origin/main", script)
        self.assertIn("git ls-remote --exit-code --heads origin", script)
        self.assertNotIn("|| true", script)
        self.assertNotIn("pr merge", script)
        self.assertTrue(any(step.get("id") == "app-token" for step in job["steps"]))

    def test_resolve_pins_reads_both_heads_before_returning(self):
        responses = iter(("a" * 40 + "\trefs/heads/main\n",
                          "b" * 40 + "\trefs/heads/main\n"))
        runner = Mock(side_effect=lambda command: next(responses))

        self.assertEqual(pins.resolve_pins(runner), {
            "brew_commit": "a" * 40,
            "core_commit": "b" * 40,
        })
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args_list[0].args[0][-1], "refs/heads/main")
        self.assertEqual(runner.call_args_list[1].args[0][-1], "refs/heads/main")

    def test_resolve_pins_rejects_malformed_remote_without_writing(self):
        with self.assertRaisesRegex(pins.Error, "Cannot resolve"):
            pins.resolve_pins(Mock(return_value="not-a-sha refs/heads/main\n"))

    def test_second_remote_failure_leaves_policy_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            original = '{"schema": 1, "repository": "adriank1410/homebrew-intel"}\n'
            config.write_text(original)
            with patch.object(pins, "CONFIG_PATH", config), patch.object(
                    pins.subprocess, "check_output", side_effect=[
                        "a" * 40 + " refs/heads/main\n",
                        subprocess.CalledProcessError(128, ["git", "ls-remote"])]):
                with self.assertRaisesRegex(pins.Error, "Cannot resolve core_commit"):
                    pins.main(["--write"])
            self.assertEqual(config.read_text(), original)

    def test_update_config_changes_only_the_two_pin_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = {"schema": 1, "repository": "adriank1410/homebrew-intel",
                        "brew_commit": "c" * 40, "max_graph_nodes": 4}
            path.write_text(json.dumps(original, indent=2) + "\n")

            self.assertTrue(pins.update_config(path, {
                "brew_commit": "a" * 40, "core_commit": "b" * 40,
            }))
            updated = json.loads(path.read_text())
            self.assertEqual(updated["brew_commit"], "a" * 40)
            self.assertEqual(updated["core_commit"], "b" * 40)
            self.assertEqual(updated["max_graph_nodes"], 4)
            self.assertFalse(pins.update_config(path, {
                "brew_commit": "a" * 40, "core_commit": "b" * 40,
            }))

    def test_update_config_rejects_unexpected_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"schema": 1, "repository": "other"}))
            with self.assertRaisesRegex(pins.Error, "Unexpected configuration"):
                pins.update_config(path, {"brew_commit": "a" * 40,
                                          "core_commit": "b" * 40})


if __name__ == "__main__":
    unittest.main()
