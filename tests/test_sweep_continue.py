# SPDX-License-Identifier: BSD-2-Clause
import json
import subprocess
import unittest
from pathlib import Path

from intelbrew.publish import release_tag


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "adriank1410/homebrew-intel"
RUN_ID = "37957112869"
ATTEMPT = "1"
PUBLISH_STEP = "Publish immutable release and attested registry PR"


def workflow(name):
    return json.loads(subprocess.check_output(
        ["ruby", "-ryaml", "-rjson", "-e", "puts JSON.generate(YAML.load_file(ARGV[0]))",
         str(ROOT / ".github/workflows" / name)], text=True))


def publish_job(root, step="success", *, conclusion="success"):
    return {
        "name": f"root-pipeline ({root}) / publish ({root})",
        "status": "completed",
        "conclusion": conclusion,
        "steps": [{"name": PUBLISH_STEP, "conclusion": step}],
    }


def merged_pr(root):
    branch = "bottles/" + release_tag(root, RUN_ID, ATTEMPT)
    return {
        "state": "closed",
        "merged_at": "2026-10-09T17:20:00Z",
        "head": {"ref": branch, "repo": {"full_name": REPOSITORY}},
    }


class SweepContinueTests(unittest.TestCase):
    def test_only_a_successful_publish_step_counts_as_published(self):
        from intelbrew.sweep_continue import published_roots

        jobs = [
            publish_job("aom"),
            publish_job("dotnet", "failure", conclusion="failure"),
            publish_job("go", "skipped"),
            {"name": "publish (legacy)", "status": "completed", "conclusion": "success",
             "steps": [{"name": PUBLISH_STEP, "conclusion": "success"}]},
            {"name": "root-pipeline (aom) / build (aom)", "status": "completed",
             "conclusion": "success", "steps": [{"name": PUBLISH_STEP, "conclusion": "success"}]},
            {"name": "root-pipeline (left) / publish (right)", "status": "completed",
             "conclusion": "success", "steps": [{"name": PUBLISH_STEP, "conclusion": "success"}]},
            # A skipped reusable job keeps the input expression as its name.
            {"name": "root-pipeline (dotnet) / publish (${{ inputs.root }})",
             "status": "completed", "conclusion": "skipped", "steps": []},
        ]
        self.assertEqual(published_roots(jobs), ["aom"])

    def test_branch_uses_the_publication_tag(self):
        from intelbrew.sweep_continue import branch_for

        self.assertEqual(
            branch_for("python@3.14", RUN_ID, ATTEMPT),
            "bottles/" + release_tag("python@3.14", RUN_ID, ATTEMPT),
        )

    def test_open_or_foreign_pulls_do_not_count_as_merged(self):
        from intelbrew.sweep_continue import pull_status

        branch = "bottles/" + release_tag("aom", RUN_ID, ATTEMPT)
        open_pr = {
            "state": "open",
            "merged_at": None,
            "head": {"ref": branch, "repo": {"full_name": REPOSITORY}},
        }
        foreign = {
            "state": "closed",
            "merged_at": "2026-10-09T17:20:00Z",
            "head": {"ref": branch, "repo": {"full_name": "other/homebrew-intel"}},
        }
        self.assertEqual(pull_status([open_pr], repository=REPOSITORY, branch=branch), "open")
        self.assertEqual(pull_status([foreign], repository=REPOSITORY, branch=branch), "missing")
        self.assertEqual(pull_status([merged_pr("aom")], repository=REPOSITORY, branch=branch), "merged")
        self.assertEqual(pull_status({"message": "nope"}, repository=REPOSITORY, branch=branch), "unknown")

    def test_chain_waits_for_merge_and_stops_without_a_publication(self):
        from intelbrew.sweep_continue import MAX_SWEEP_WAVES, continuation_decision

        self.assertEqual(MAX_SWEEP_WAVES, 8)
        self.assertEqual(
            continuation_decision("schedule", "all", 1, ["aom"], {"aom": "open"}),
            "wait",
        )
        self.assertEqual(
            continuation_decision("schedule", "all", 1, ["aom", "go"], {"aom": "merged", "go": "missing"}),
            "wait",
        )
        self.assertEqual(
            continuation_decision("schedule", "all", 1, ["aom"], {"aom": "merged"}),
            "dispatch",
        )
        self.assertEqual(
            continuation_decision(
                "schedule", "all", 1, ["aom", "go"], {"aom": "merged", "go": "closed"}),
            "dispatch",
        )
        self.assertEqual(
            continuation_decision("schedule", "all", 1, ["aom"], {"aom": "closed"}),
            "stop",
        )
        self.assertEqual(continuation_decision("schedule", "all", 1, [], {}), "stop")
        self.assertEqual(continuation_decision("workflow_dispatch", "simdutf", 1, ["aom"], {"aom": "merged"}), "stop")
        self.assertEqual(continuation_decision("push", "all", 1, ["aom"], {"aom": "merged"}), "stop")
        self.assertEqual(continuation_decision("schedule", "all", MAX_SWEEP_WAVES, ["aom"], {"aom": "merged"}), "stop")
        self.assertEqual(continuation_decision("workflow_dispatch", "all", 7, ["aom"], {"aom": "merged"}), "dispatch")

    def test_dispatch_is_a_full_sweep_on_main_without_shell_interpolation(self):
        from intelbrew.core import Error
        from intelbrew.sweep_continue import dispatch_arguments

        arguments = dispatch_arguments(REPOSITORY, 2)
        self.assertEqual(arguments, [
            "workflow", "run", "bottles.yml", "--repo", REPOSITORY, "--ref", "main",
            "-f", "formula=all", "-f", "sweep_wave=2",
        ])
        with self.assertRaises(Error):
            dispatch_arguments("other/homebrew-intel", 2)
        with self.assertRaises(Error):
            dispatch_arguments(REPOSITORY, 9)
        with self.assertRaises(Error):
            dispatch_arguments(REPOSITORY, True)

    def test_runner_dispatches_the_next_wave_only_after_every_pull_merges(self):
        from intelbrew.sweep_continue import run_continuation

        calls = {"pulls": 0, "dispatched": []}
        branch = "bottles/" + release_tag("python@3.14", RUN_ID, ATTEMPT)

        def fetch_jobs():
            return [publish_job("python@3.14")]

        def fetch_pulls(requested):
            self.assertEqual(requested, branch)
            calls["pulls"] += 1
            if calls["pulls"] == 1:
                return [{
                    "state": "open",
                    "merged_at": None,
                    "head": {"ref": branch, "repo": {"full_name": REPOSITORY}},
                }]
            return [merged_pr("python@3.14")]

        def dispatch(wave):
            calls["dispatched"].append(wave)

        clock = {"now": 0}

        def sleep(seconds):
            clock["now"] += seconds

        result = run_continuation({
            "GITHUB_EVENT_NAME": "schedule",
            "REQUESTED_FORMULA": "all",
            "INTELBREW_SWEEP_WAVE": "1",
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_RUN_ATTEMPT": ATTEMPT,
            "GITHUB_REPOSITORY": REPOSITORY,
            "INTELBREW_CONTINUE_TIMEOUT": "50",
            "INTELBREW_CONTINUE_INTERVAL": "20",
        }, fetch_jobs=fetch_jobs, fetch_pulls=fetch_pulls, dispatch=dispatch,
           sleep=sleep, clock=lambda: clock["now"])
        self.assertEqual(result, 0)
        self.assertEqual(calls["dispatched"], [2])
        self.assertEqual(calls["pulls"], 2)

    def test_runner_stops_when_nothing_published_or_the_deadline_passes(self):
        from intelbrew.sweep_continue import run_continuation

        dispatched = []

        def run(env, fetch_jobs):
            return run_continuation(
                env, fetch_jobs=fetch_jobs, fetch_pulls=lambda _branch: [],
                dispatch=dispatched.append, sleep=lambda _seconds: None,
                clock=lambda: 0,
            )

        env = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "REQUESTED_FORMULA": "all",
            "INTELBREW_SWEEP_WAVE": "3",
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_RUN_ATTEMPT": ATTEMPT,
            "GITHUB_REPOSITORY": REPOSITORY,
            "INTELBREW_CONTINUE_TIMEOUT": "0",
            "INTELBREW_CONTINUE_INTERVAL": "20",
        }
        self.assertEqual(run(env, lambda: [publish_job("aom", "skipped")]), 0)
        self.assertEqual(dispatched, [])
        self.assertEqual(run(env, lambda: [publish_job("aom")]), 0)
        self.assertEqual(dispatched, [])

    def test_runner_does_not_fail_the_sweep_when_dispatch_errors(self):
        from intelbrew.core import Error
        from intelbrew.sweep_continue import run_continuation

        def fetch_pulls(branch):
            return [merged_pr("aom")]

        def dispatch(_wave):
            raise Error("gh failed (1): boom")

        result = run_continuation({
            "GITHUB_EVENT_NAME": "schedule",
            "REQUESTED_FORMULA": "all",
            "INTELBREW_SWEEP_WAVE": "1",
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_RUN_ATTEMPT": ATTEMPT,
            "GITHUB_REPOSITORY": REPOSITORY,
            "INTELBREW_CONTINUE_TIMEOUT": "0",
        }, fetch_jobs=lambda: [publish_job("aom")], fetch_pulls=fetch_pulls,
           dispatch=dispatch, sleep=lambda _seconds: None, clock=lambda: 0)
        self.assertEqual(result, 0)

    def test_readmes_document_the_self_started_wave_cap(self):
        english = (ROOT / "README.md").read_text()
        polish = (ROOT / "README.pl.md").read_text()
        self.assertIn("eight waves", english)
        self.assertIn("publishes nothing", english)
        self.assertIn("osiem fal", polish)
        self.assertIn("nic nie opublikował", polish)

    def test_workflow_chains_a_full_sweep_after_partial_publication(self):
        from intelbrew.sweep_continue import PUBLISH_STEP as step_name

        document = workflow("bottles.yml")
        trigger = document.get("on", document.get("true"))
        wave = trigger["workflow_dispatch"]["inputs"]["sweep_wave"]
        self.assertEqual(wave["default"], "1")
        self.assertFalse(wave["required"])
        job = document["jobs"]["continue-sweep"]
        self.assertEqual(job["needs"], ["root-pipeline"])
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertLessEqual(job["timeout-minutes"], 30)
        self.assertIn("always()", job["if"])
        self.assertIn("!cancelled()", job["if"])
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertIn("github.event_name == 'schedule'", job["if"])
        self.assertIn("inputs.formula == 'all'", job["if"])
        self.assertIn("needs.root-pipeline.result == 'failure'", job["if"])
        self.assertIn("needs.root-pipeline.result == 'success'", job["if"])
        self.assertEqual(job["permissions"]["actions"], "write")
        self.assertNotIn("APP_PRIVATE_KEY", json.dumps(job))
        self.assertNotIn("app_private_key", json.dumps(job))
        run = next(step for step in job["steps"] if "continue-sweep.py" in step.get("run", ""))
        self.assertNotRegex(run["run"], r"\$\{\{")
        self.assertEqual(run["env"]["INTELBREW_SWEEP_WAVE"], "${{ inputs.sweep_wave || '1' }}")
        self.assertIn("schedule", run["env"]["REQUESTED_FORMULA"])
        publish_names = [
            step.get("name")
            for step in workflow("bottle-root.yml")["jobs"]["publish"]["steps"]
        ]
        self.assertIn(step_name, publish_names)
