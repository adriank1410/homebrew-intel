# SPDX-License-Identifier: BSD-2-Clause
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
native_spec = importlib.util.spec_from_file_location(
    "native_plan", ROOT / "scripts/native-plan.py"
)
native_plan = importlib.util.module_from_spec(native_spec)
native_spec.loader.exec_module(native_plan)


def synthetic_meta(name, *, official=False, runtime=(), build=(), test=(), license="MIT"):
    return {
        "name": name,
        "tap": "homebrew/core",
        "version": "1.0",
        "revision": 0,
        "version_scheme": 0,
        "pkg_version": "1.0",
        "formula_sha256": "a" * 64,
        "runtime": list(runtime),
        "build": list(build),
        "test": list(test),
        "official_bottle": official,
        "license": license,
        "vcs_source": False,
    }


class SyntheticInspector:
    def __init__(self, items):
        self.items = dict(items)
        self.primed = []

    def prime(self, names):
        self.primed.append(list(names))

    def __call__(self, names):
        missing = [name for name in names if name not in self.items]
        if missing:
            raise native_plan.Error(f"Unknown synthetic formula: {missing[0]}")
        return {name: self.items[name] for name in names}


def base_config(*, max_source_builds=5, scheduled_batch_size=2):
    return {
        "schema": 1,
        "repository": "adriank1410/homebrew-intel",
        "brew_commit": "1" * 40,
        "core_commit": "2" * 40,
        "max_graph_nodes": 20,
        "max_source_builds": max_source_builds,
        "blocked_source_builds": [],
        "permissive_license_tokens": ["MIT"],
        "redistribution_exceptions": {},
        "scheduled_batch_size": scheduled_batch_size,
    }


def run_main(roots, items, *, config=None, include_summary=True):
    """Run the real planner/report path with synthetic formula metadata."""
    config = config or base_config()
    inspector = SyntheticInspector(items)
    with tempfile.TemporaryDirectory() as temp_dir:
        output_file = Path(temp_dir) / "output"
        summary_file = Path(temp_dir) / "summary.md"
        env = {
            "PLANNED_ROOTS": json.dumps({"root": roots}),
            "GITHUB_OUTPUT": str(output_file),
        }
        if include_summary:
            env["GITHUB_STEP_SUMMARY"] = str(summary_file)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.dict(os.environ, env, clear=True), \
             patch.object(native_plan, "load_config", return_value=config), \
             patch.object(native_plan, "sync_registry"), \
             patch.object(native_plan, "CachedInspector", return_value=inspector), \
             patch.object(native_plan, "registry", return_value={}), \
             patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            result = native_plan.main()
        return {
            "result": result,
            "stdout": json.loads(stdout.getvalue()),
            "stderr": stderr.getvalue(),
            "output": output_file.read_text(encoding="utf-8"),
            "summary": (summary_file.read_text(encoding="utf-8")
                        if include_summary else None),
        }


class BuildReportingTests(unittest.TestCase):
    def test_roots_needing_build_preserves_two_value_contract(self):
        items = {
            "covered": synthetic_meta("covered", official=True),
            "needed": synthetic_meta("needed"),
        }
        inspector = SyntheticInspector(items)
        config = base_config()
        result = native_plan.roots_needing_build(["covered", "needed"], inspector, {}, config)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        needed, blocked = result
        self.assertEqual(needed, ["needed"])
        self.assertEqual(blocked, {})

    def test_profiled_llvm_root_is_scheduled_but_rust_source_build_stays_blocked(self):
        items = {
            "llvm": synthetic_meta("llvm"),
            "rust": synthetic_meta("rust", runtime=("llvm",)),
        }
        config = base_config()
        config["blocked_source_builds"] = ["llvm"]
        config["build_profiles"] = {"llvm": "llvm-no-pgo"}

        run = run_main(["llvm", "rust"], items, config=config)

        self.assertEqual(run["result"], 0)
        self.assertEqual(run["stdout"]["selected_roots"], ["llvm"])
        self.assertIn("rust", run["stdout"]["blocked_roots"])
        self.assertIn("matrix={\"root\":[\"llvm\"]}", run["output"].splitlines())

    def test_unlimited_coverage_and_compatibility_wrapper_allow_over_256_roots(self):
        roots = [f"formula-{idx:03d}" for idx in range(300)]
        inspector = SyntheticInspector({
            name: synthetic_meta(name) for name in roots
        })
        config = base_config(scheduled_batch_size=24)

        needed, blocked = native_plan.roots_needing_build(roots, inspector, {}, config)
        self.assertEqual(needed, roots)
        self.assertEqual(blocked, {})

        coverage = native_plan.evaluate_roots_coverage(roots, inspector, {}, config)
        self.assertEqual(len(coverage["eligible_roots"]), 300)
        self.assertEqual(coverage["selected_roots"], roots)
        self.assertEqual(coverage["waiting_roots"], [])

    def test_coverage_all_covered(self):
        items = {
            "root-a": synthetic_meta("root-a", official=True),
            "root-b": synthetic_meta("root-b", official=True),
        }
        inspector = SyntheticInspector(items)
        config = base_config()
        coverage = native_plan.evaluate_roots_coverage(
            ["root-a", "root-b"], inspector, {}, config
        )
        self.assertEqual(coverage["covered_roots"], ["root-a", "root-b"])
        self.assertEqual(coverage["eligible_roots"], [])
        self.assertEqual(coverage["selected_roots"], [])
        self.assertEqual(coverage["waiting_roots"], [])
        self.assertEqual(coverage["deferred_roots"], {})
        self.assertEqual(coverage["blocked_roots"], {})
        self.assertTrue(coverage["coverage_complete"])
        self.assertEqual(coverage["counts"]["covered"], 2)
        self.assertEqual(coverage["counts"]["inspected"], 2)

        run = run_main(["root-a", "root-b"], items, config=config)
        self.assertEqual(run["result"], 0)
        self.assertEqual(run["stderr"], "")  # No warning on complete coverage
        out_lines = run["output"].splitlines()
        self.assertIn("matrix={\"root\":[]}", out_lines)
        self.assertIn("has_work=false", out_lines)
        self.assertIn("coverage_complete=true", out_lines)
        self.assertEqual(run["stdout"]["inspected_roots"], 2)
        self.assertEqual(run["stdout"]["counts"]["covered"], 2)
        self.assertIn("Coverage Complete | Yes", run["summary"])
        self.assertIn("*(no blocked roots)*", run["summary"])

    def test_coverage_all_blocked_no_selected(self):
        items = {
            "bad-1": synthetic_meta("bad-1", license="GPL-3.0-only"),
            "bad-2": synthetic_meta("bad-2", license="GPL-3.0-only"),
        }
        inspector = SyntheticInspector(items)
        config = base_config()
        coverage = native_plan.evaluate_roots_coverage(
            ["bad-1", "bad-2"], inspector, {}, config
        )
        self.assertEqual(coverage["covered_roots"], [])
        self.assertEqual(coverage["eligible_roots"], [])
        self.assertEqual(coverage["selected_roots"], [])
        self.assertEqual(len(coverage["blocked_roots"]), 2)
        self.assertIn("bad-1", coverage["blocked_roots"])
        self.assertIn("bad-2", coverage["blocked_roots"])
        self.assertFalse(coverage["coverage_complete"])
        self.assertEqual(coverage["counts"]["blocked"], 2)

        run = run_main(["bad-1", "bad-2"], items, config=config)
        self.assertEqual(run["result"], 0)
        self.assertIn("::warning::Incomplete bottle coverage (2 blocked)", run["stderr"])
        out_lines = run["output"].splitlines()
        self.assertIn("matrix={\"root\":[]}", out_lines)
        self.assertIn("has_work=false", out_lines)
        self.assertIn("coverage_complete=false", out_lines)
        self.assertEqual(run["stdout"]["blocked_roots"].keys(), {"bad-1", "bad-2"})
        self.assertIn("Coverage Complete | No", run["summary"])
        self.assertIn("| bad&#45;1 |", run["summary"])
        self.assertIn("| bad&#45;2 |", run["summary"])

    def test_coverage_mixed_blocked_and_eligible(self):
        items = {
            "blocked-formula": synthetic_meta("blocked-formula", license="GPL-3.0-only"),
            "valid-formula": synthetic_meta("valid-formula"),
        }
        inspector = SyntheticInspector(items)
        config = base_config()
        coverage = native_plan.evaluate_roots_coverage(
            ["blocked-formula", "valid-formula"], inspector, {}, config
        )
        self.assertEqual(coverage["eligible_roots"], ["valid-formula"])
        self.assertEqual(coverage["selected_roots"], ["valid-formula"])
        self.assertIn("blocked-formula", coverage["blocked_roots"])
        self.assertFalse(coverage["coverage_complete"])

        run = run_main(["blocked-formula", "valid-formula"], items, config=config)
        self.assertEqual(run["result"], 0)
        self.assertIn("::warning::Incomplete bottle coverage", run["stderr"])
        out_lines = run["output"].splitlines()
        self.assertIn("matrix={\"root\":[\"valid-formula\"]}", out_lines)
        self.assertIn("has_work=true", out_lines)
        self.assertIn("coverage_complete=false", out_lines)
        self.assertEqual(run["stdout"]["counts"]["blocked"], 1)

    def test_coverage_shared_dependency_deferral(self):
        items = {
            "base": synthetic_meta("base"),
            "app": synthetic_meta("app", runtime=("base",)),
        }
        inspector = SyntheticInspector(items)
        config = base_config()
        coverage = native_plan.evaluate_roots_coverage(["app", "base"], inspector, {}, config)
        self.assertEqual(coverage["eligible_roots"], ["base"])
        self.assertEqual(coverage["selected_roots"], ["base"])
        self.assertEqual(coverage["deferred_roots"], {"app": ["base"]})
        self.assertEqual(coverage["covered_roots"], [])
        self.assertFalse(coverage["coverage_complete"])
        self.assertEqual(coverage["counts"]["deferred"], 1)

        run = run_main(["app", "base"], items, config=config)
        self.assertEqual(run["result"], 0)
        self.assertIn("::warning::Incomplete bottle coverage (1 deferred", run["stderr"])
        out_lines = run["output"].splitlines()
        self.assertIn("matrix={\"root\":[\"base\"]}", out_lines)
        self.assertIn("coverage_complete=false", out_lines)
        self.assertEqual(run["stdout"]["counts"]["deferred"], 1)
        self.assertIn("- app (dependencies: base)", run["summary"])

    def test_coverage_batch_cap_waiting(self):
        items = {
            f"formula-{idx}": synthetic_meta(f"formula-{idx}")
            for idx in range(5)
        }
        inspector = SyntheticInspector(items)
        config = base_config(scheduled_batch_size=2)
        roots = list(items.keys())
        coverage = native_plan.evaluate_roots_coverage(
            roots, inspector, {}, config, max_roots=config["scheduled_batch_size"]
        )
        self.assertEqual(len(coverage["eligible_roots"]), 5)
        self.assertEqual(len(coverage["selected_roots"]), 2)
        self.assertEqual(len(coverage["waiting_roots"]), 3)
        self.assertEqual(coverage["selected_roots"], roots[:2])
        self.assertEqual(coverage["waiting_roots"], roots[2:])
        self.assertFalse(coverage["coverage_complete"])
        self.assertEqual(coverage["counts"]["waiting"], 3)

        run = run_main(roots, items, config=config)
        self.assertEqual(run["result"], 0)
        self.assertIn("3 waiting", run["stderr"])
        out_lines = run["output"].splitlines()
        self.assertIn("matrix={\"root\":[\"formula-0\",\"formula-1\"]}", out_lines)
        self.assertIn("coverage_complete=false", out_lines)
        self.assertEqual(run["stdout"]["counts"]["waiting"], 3)
        self.assertIn("Waiting Roots (Batch Cap)", run["summary"])
        self.assertIn("- formula&#45;2", run["summary"])
        self.assertIn("- formula&#45;3", run["summary"])
        self.assertIn("- formula&#45;4", run["summary"])

    def test_summary_markdown_injection_safety(self):
        dangerous_cause = (
            "Error | with | pipes\nand multiple\r\nnewlines "
            "<script>alert('xss')</script> [markdown link](http://evil.test) **bold** `code`"
        )
        for count in range(5):
            with self.subTest(backslashes=count):
                raw_cell = "before" + "\\" * count + "|after"
                self.assertEqual(
                    native_plan.escape_markdown_table_cell(raw_cell),
                    "before" + "&#92;" * count + "&#124;after",
                )

        escaped = native_plan.escape_markdown_table_cell(dangerous_cause)
        self.assertNotIn("\n", escaped)
        self.assertNotIn("\r", escaped)
        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)
        self.assertIn("&#91;markdown link&#93;&#40;http&#58;//evil&#46;test&#41;", escaped)
        self.assertIn("&#42;&#42;bold&#42;&#42;", escaped)
        self.assertIn("&#96;code&#96;", escaped)
        self.assertNotIn("|", escaped)
        self.assertNotIn("\\", escaped)

        config = base_config()
        coverage = {
            "counts": {"inspected": 1, "blocked": 1},
            "coverage_complete": False,
            "selected_roots": [],
            "waiting_roots": [],
            "deferred_roots": {},
            "blocked_roots": {"injected\\|pkg": dangerous_cause},
        }
        summary = native_plan.generate_step_summary(config, coverage)
        table_lines = [
            line for line in summary.splitlines()
            if line.startswith("| injected&#92;&#124;pkg |")
        ]
        self.assertEqual(len(table_lines), 1)
        row = table_lines[0]
        # Encoded pipes and backslashes cannot alter the GFM table structure.
        self.assertEqual(row.count("|"), 3)

    def test_summary_file_absent(self):
        items = {"valid": synthetic_meta("valid")}
        config = base_config()
        run = run_main(["valid"], items, config=config, include_summary=False)
        self.assertEqual(run["result"], 0)
        self.assertTrue(run["output"])
        self.assertIsNone(run["summary"])

    def test_stdout_json_unpolluted_by_stderr_annotations(self):
        items = {
            "blocked": synthetic_meta("blocked", license="GPL-3.0-only"),
            "valid": synthetic_meta("valid"),
        }
        config = base_config()
        run = run_main(["blocked", "valid"], items, config=config, include_summary=False)
        self.assertEqual(run["result"], 0)
        self.assertIn("::warning::", run["stderr"])
        self.assertNotIn("::warning::", json.dumps(run["stdout"]))

        data = run["stdout"]
        self.assertEqual(data["inspected_roots"], 2)
        self.assertEqual(data["build_candidates"], 1)
        self.assertEqual(data["selected_roots"], ["valid"])
        self.assertIn("blocked", data["blocked_roots"])
        self.assertEqual(data["covered_roots"], [])
        self.assertEqual(data["eligible_roots"], ["valid"])
        self.assertEqual(data["waiting_roots"], [])
        self.assertEqual(data["deferred_roots"], {})
        self.assertFalse(data["coverage_complete"])
        self.assertEqual(data["counts"]["blocked"], 1)
        self.assertNotIn("blocked_count", data)
        self.assertNotIn("covered_count", data)
        self.assertNotIn("eligible_count", data)
        self.assertNotIn("selected_count", data)
        self.assertNotIn("waiting_count", data)
        self.assertNotIn("deferred_count", data)
        self.assertNotIn("deferred_reasons", data)
        self.assertEqual(
            set(data),
            {"inspected_roots", "build_candidates", "selected_roots", "blocked_roots",
             "covered_roots", "eligible_roots", "waiting_roots", "deferred_roots",
             "coverage_complete", "counts"},
        )

    def test_summary_includes_pinned_hashes_and_lag_disclaimer(self):
        config = base_config()
        coverage = {
            "counts": {"inspected": 1, "covered": 1},
            "coverage_complete": True,
            "selected_roots": [],
            "waiting_roots": [],
            "deferred_roots": {},
            "blocked_roots": {},
        }
        summary = native_plan.generate_step_summary(config, coverage)
        self.assertIn(config["core_commit"], summary)
        self.assertIn(config["brew_commit"], summary)
        self.assertIn("Coverage is measured against the pinned Homebrew Core snapshot", summary)
        self.assertIn("may lag the client's latest core", summary)


if __name__ == "__main__":
    unittest.main()
