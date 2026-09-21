# SPDX-License-Identifier: BSD-2-Clause
"""Exercise the Homebrew pin maintenance workflow against a real Git remote."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/homebrew-pins.yml"
UPDATE_SCRIPT = ROOT / "scripts/update-homebrew-pins.py"
BRANCH = "automation/homebrew-pins"
INITIAL_PINS = {
    "brew_commit": "1" * 40,
    "core_commit": "2" * 40,
}
NEXT_PINS = {
    "brew_commit": "3" * 40,
    "core_commit": "4" * 40,
}


def maintenance_script():
    payload = json.loads(subprocess.check_output([
        "ruby", "-ryaml", "-rjson", "-e",
        "puts JSON.generate(YAML.load_file(ARGV[0]))", str(WORKFLOW),
    ], text=True))
    return next(
        step["run"] for step in payload["jobs"]["refresh"]["steps"]
        if step.get("name") == "Open or update the reviewed pin PR"
    )


class PinMaintenanceLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = maintenance_script()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pin-maintenance-")
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.gh_log = self.root / "gh-calls.jsonl"
        self.pr_url_file = self.root / "pr-url"
        self.script_marker = self.root / "update-script-executed"
        self._make_git_adapter()
        self._make_gh_stub()
        self._make_python3_link()
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(self.root / "home"),
            "RUNNER_TEMP": str(self.root / "runner-temp"),
            "GITHUB_REPOSITORY": "example/homebrew-intel",
            "PIN_TEST_GH_LOG": str(self.gh_log),
            "PIN_TEST_PR_URL_FILE": str(self.pr_url_file),
            "PIN_TEST_EXECUTED_MARKER": str(self.script_marker),
            "PIN_TEST_BREW_COMMIT": NEXT_PINS["brew_commit"],
            "PIN_TEST_CORE_COMMIT": NEXT_PINS["core_commit"],
            "PIN_TEST_REAL_GIT": shutil.which("git") or "git",
            "GH_TOKEN": "test-token",
        }
        (self.root / "home").mkdir()
        (self.root / "runner-temp").mkdir()
        self._git("init", "--bare", "--initial-branch=main", str(self.remote), cwd=self.root)
        seed = self.root / "seed"
        self._git("clone", str(self.remote), str(seed), cwd=self.root)
        self._configure_git(seed)
        self._write_project(seed, INITIAL_PINS)
        self._git("add", ".", cwd=seed)
        self._git("commit", "-m", "initial pin policy", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)

    def tearDown(self):
        self.temp.cleanup()

    def _make_git_adapter(self):
        real_git = shutil.which("git") or "git"
        adapter = self.bin / "git"
        adapter.write_text(
            "#!%s\n"
            "import os, sys\n"
            "official = {\n"
            "    'https://github.com/Homebrew/brew.git': 'PIN_TEST_BREW_COMMIT',\n"
            "    'https://github.com/Homebrew/homebrew-core.git': 'PIN_TEST_CORE_COMMIT',\n"
            "}\n"
            "args = sys.argv[1:]\n"
            "if len(args) == 3 and args[0] == 'ls-remote' and args[2] == 'refs/heads/main' and args[1] in official:\n"
            "    key = official[args[1]]\n"
            "    print(os.environ[key] + '\\trefs/heads/main')\n"
            "    raise SystemExit(0)\n"
            "os.execv(os.environ['PIN_TEST_REAL_GIT'], [os.environ['PIN_TEST_REAL_GIT'], *args])\n"
            % sys.executable,
            encoding="utf-8",
        )
        adapter.chmod(0o755)

    def _make_gh_stub(self):
        stub = self.bin / "gh"
        stub.write_text(
            "#!%s\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['PIN_TEST_GH_LOG'], 'a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(args) + '\\n')\n"
            "if args[:2] == ['auth', 'setup-git']:\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['pr', 'list']:\n"
            "    path = os.environ['PIN_TEST_PR_URL_FILE']\n"
            "    if os.path.isfile(path):\n"
            "        print(open(path, encoding='utf-8').read(), end='')\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['pr', 'create']:\n"
            "    if os.environ.get('PIN_TEST_FAIL_PR_CREATE') == '1':\n"
            "        print('simulated PR creation failure', file=sys.stderr)\n"
            "        raise SystemExit(17)\n"
            "    url = 'https://github.com/example/homebrew-intel/pull/999'\n"
            "    open(os.environ['PIN_TEST_PR_URL_FILE'], 'w', encoding='utf-8').write(url + '\\n')\n"
            "    print(url)\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['pr', 'edit']:\n"
            "    raise SystemExit(0)\n"
            "print('unexpected gh invocation: ' + ' '.join(args), file=sys.stderr)\n"
            "raise SystemExit(19)\n"
            % sys.executable,
            encoding="utf-8",
        )
        stub.chmod(0o755)

    def _make_python3_link(self):
        (self.bin / "python3").symlink_to(sys.executable)

    def _configure_git(self, directory):
        self._git("config", "user.name", "pin-maintenance-test", cwd=directory)
        self._git("config", "user.email", "pin-maintenance@example.invalid", cwd=directory)
        self._git("config", "commit.gpgsign", "false", cwd=directory)

    def _git(self, *args, cwd):
        return subprocess.check_output(
            ["git", *args], cwd=cwd, env=self.env, text=True,
            stderr=subprocess.STDOUT,
        ).strip()

    def _write_project(self, directory, pins):
        config = {
            "schema": 1,
            "repository": "adriank1410/homebrew-intel",
            **pins,
        }
        (directory / "policy").mkdir(exist_ok=True)
        (directory / "scripts").mkdir(exist_ok=True)
        (directory / "policy/config.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(UPDATE_SCRIPT, directory / "scripts/update-homebrew-pins.py")

    def _fresh_clone(self, name):
        directory = self.root / name
        self._git("clone", str(self.remote), str(directory), cwd=self.root)
        self._configure_git(directory)
        return directory

    def _run_workflow(self, name, *, fail_pr_create=False):
        directory = self._fresh_clone(name)
        env = {**self.env, "PIN_TEST_FAIL_PR_CREATE": "1" if fail_pr_create else "0"}
        before = self._gh_calls()
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", self.script],
            cwd=directory, env=env, text=True, capture_output=True,
        )
        return directory, result, self._gh_calls()[len(before):]

    def _gh_calls(self):
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    def _remote_tip(self, ref):
        return self._git("--git-dir", str(self.remote), "rev-parse", ref, cwd=self.root)

    def _remote_config(self, ref="refs/heads/main"):
        return json.loads(self._git(
            "--git-dir", str(self.remote), "show", f"{ref}:policy/config.json",
            cwd=self.root,
        ))

    def test_creates_owned_branch_with_paired_pins_without_writing_main(self):
        main_before = self._remote_tip("refs/heads/main")
        directory, result, calls = self._run_workflow("create")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._remote_config()["brew_commit"], INITIAL_PINS["brew_commit"])
        self.assertEqual(self._remote_config()["core_commit"], INITIAL_PINS["core_commit"])
        branch_config = self._remote_config(f"refs/heads/{BRANCH}")
        self.assertEqual(branch_config["brew_commit"], NEXT_PINS["brew_commit"])
        self.assertEqual(branch_config["core_commit"], NEXT_PINS["core_commit"])
        self.assertEqual(self._remote_tip("refs/heads/main"), main_before)
        self.assertEqual(calls[0][:2], ["auth", "setup-git"])
        self.assertTrue(any(call[:2] == ["pr", "list"] for call in calls))
        self.assertTrue(any(call[:2] == ["pr", "create"] for call in calls))
        self.assertEqual(self._git("status", "--porcelain", cwd=directory), "")

    def test_repeat_with_current_pins_creates_no_new_commit(self):
        self._run_workflow("first")
        before = self._remote_tip(f"refs/heads/{BRANCH}")

        _, result, calls = self._run_workflow("repeat")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._remote_tip(f"refs/heads/{BRANCH}"), before)
        self.assertEqual(calls[0], ["auth", "setup-git"])
        self.assertTrue(any(call[:2] == ["pr", "list"] for call in calls))
        self.assertTrue(any(call[:2] == ["pr", "edit"] for call in calls))
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in calls))

    def test_advanced_main_is_merged_and_pushed_even_when_pins_are_current(self):
        self._run_workflow("first")
        branch_before = self._remote_tip(f"refs/heads/{BRANCH}")
        main = self._fresh_clone("advance-main")
        self._write_project(main, NEXT_PINS)
        (main / "README.md").write_text("main advanced\n", encoding="utf-8")
        self._git("add", "policy/config.json", "README.md", cwd=main)
        self._git("commit", "-m", "advance main", cwd=main)
        self._git("push", "origin", "main", cwd=main)
        main_before_workflow = self._remote_tip("refs/heads/main")

        branch_clone, result, _ = self._run_workflow("merge-current")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self._remote_tip(f"refs/heads/{BRANCH}"), branch_before)
        self.assertTrue(
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", "origin/main", f"refs/remotes/origin/{BRANCH}"],
                cwd=branch_clone, env=self.env,
            ).returncode == 0
        )
        self.assertEqual(
            self._git("show", f"refs/remotes/origin/{BRANCH}:README.md", cwd=branch_clone),
            "main advanced",
        )
        self.assertEqual(self._remote_config(f"refs/heads/{BRANCH}")["brew_commit"], NEXT_PINS["brew_commit"])
        self.assertEqual(self._remote_tip("refs/heads/main"), main_before_workflow)

    def test_failed_pr_create_is_retried_after_successful_push(self):
        _, first, first_calls = self._run_workflow("failed-create", fail_pr_create=True)
        self.assertEqual(first.returncode, 17, first.stderr)
        branch_before_retry = self._remote_tip(f"refs/heads/{BRANCH}")
        self.assertTrue(any(call[:2] == ["pr", "create"] for call in first_calls))

        _, second, second_calls = self._run_workflow("retry-create")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self._remote_tip(f"refs/heads/{BRANCH}"), branch_before_retry)
        self.assertTrue(any(call[:2] == ["pr", "list"] for call in second_calls))
        self.assertTrue(any(call[:2] == ["pr", "create"] for call in second_calls))

    def test_existing_proposal_with_script_change_is_rejected_before_update(self):
        self._run_workflow("first")
        tampered = self._fresh_clone("tampered")
        self._git("checkout", "-B", BRANCH, f"origin/{BRANCH}", cwd=tampered)
        (tampered / "scripts/update-homebrew-pins.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['PIN_TEST_EXECUTED_MARKER']).write_text('executed\\n')\n",
            encoding="utf-8",
        )
        self._git("add", "scripts/update-homebrew-pins.py", cwd=tampered)
        self._git("commit", "-m", "tamper maintenance script", cwd=tampered)
        self._git("push", "origin", f"HEAD:{BRANCH}", cwd=tampered)
        branch_before = self._remote_tip(f"refs/heads/{BRANCH}")

        _, result, calls = self._run_workflow("reject-tampered")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contains changes outside", result.stderr)
        self.assertFalse(self.script_marker.exists())
        self.assertEqual(self._remote_tip(f"refs/heads/{BRANCH}"), branch_before)
        self.assertFalse(any(call[:2] == ["pr", "list"] for call in calls))
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
