# SPDX-License-Identifier: BSD-2-Clause
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return json.loads(subprocess.check_output([
        "ruby", "-ryaml", "-rjson", "-e", "puts JSON.generate(YAML.load_file(ARGV[0]))",
        str(ROOT / ".github/workflows/checks.yml")], text=True))


class CompatibilityGateTests(unittest.TestCase):
    def test_required_tests_fails_when_native_is_missing_failed_or_cancelled(self):
        jobs = workflow()["jobs"]
        job = jobs["tests"]
        self.assertEqual(set(job["needs"]), {"unit", "changes", "homebrew"})
        self.assertEqual(job["if"], "${{ always() }}")
        script = job["steps"][0]["run"]
        for unit, changes, needed, native, succeeds in [
            ("success", "success", "true", "success", True),
            ("success", "success", "false", "skipped", True),
            ("success", "success", "true", "skipped", False),
            ("success", "success", "true", "failure", False),
            ("success", "success", "true", "cancelled", False),
            ("failure", "success", "false", "skipped", False),
            ("success", "failure", "false", "skipped", False),
        ]:
            with self.subTest(unit=unit, changes=changes, needed=needed, native=native):
                result = subprocess.run(["bash", "-e", "-c", script], env={**os.environ,
                    "UNIT_RESULT": unit, "CHANGES_RESULT": changes,
                    "NATIVE_REQUIRED": needed, "NATIVE_RESULT": native}, capture_output=True)
                self.assertEqual(result.returncode == 0, succeeds)

    def test_change_detection_uses_real_git_diff_and_skips_registry_dispatches(self):
        step = next(s for s in workflow()["jobs"]["changes"]["steps"] if s.get("id") == "changes")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
            git("init", "-b", "main")
            git("config", "commit.gpgsign", "false")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (root / "README.md").write_text("base")
            git("add", "."); git("commit", "-m", "base")
            base = git("rev-parse", "HEAD")
            git("update-ref", "refs/remotes/origin/main", base)
            def detect(event, sha=base, force="false"):
                output = root / "output"
                output.write_text("")
                result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]], cwd=root,
                    env={**os.environ, "EVENT": event, "BASE_SHA": sha,
                         "FORCE_NATIVE": force, "GITHUB_OUTPUT": str(output)}, text=True, capture_output=True)
                return result, output.read_text().strip()
            (root / "registry").mkdir(); (root / "registry/a.json").write_text("{}")
            git("add", "."); git("commit", "-m", "registry")
            for event in ("pull_request", "push", "workflow_dispatch"):
                result, output = detect(event)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, "native=false")
            (root / "policy").mkdir(); (root / "policy/config.json").write_text("{}")
            git("add", "policy"); git("commit", "-m", "pair")
            for event in ("pull_request", "push", "workflow_dispatch"):
                result, output = detect(event)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, "native=true")
            self.assertNotEqual(detect("pull_request", "f" * 40)[0].returncode, 0)
            self.assertEqual(detect("push", "0" * 40)[1], "native=true")
            self.assertEqual(detect("workflow_dispatch", force="true")[1], "native=true")

    def test_native_job_uses_disposable_intel_runner_and_real_fetch_script(self):
        job = workflow()["jobs"]["homebrew"]
        self.assertEqual(job["runs-on"], "macos-15-intel")
        scripts = "\n".join(s.get("run", "") for s in job["steps"])
        self.assertIn("bash scripts/prepare-runner.sh", scripts)
        self.assertIn("bash scripts/verify-homebrew-pins.sh", scripts)
        self.assertNotIn("secrets.", json.dumps(job))


if __name__ == "__main__":
    unittest.main()
