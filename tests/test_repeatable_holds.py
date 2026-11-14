# SPDX-License-Identifier: BSD-2-Clause
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from intelbrew.ci import (
    _failure_dir, _maybe_convert_repeatable_hold, _maybe_raise_prior_hold, _write_github_output, main,
)
from intelbrew.core import Error, formula_test_env, brew_env
from intelbrew.repeatable_holds import (
    RepeatableHold, classify_build_failure, consecutive_matches, describe_hold,
    hold_descriptor, load_failure_history, make_failure_record, write_failure_record,
)


ROOT_SHA = "1d2fba0e814e9568b49f9f2d9454eca8ed7e15b9526608c6e3019d8303facac4"
DOTNET_SHA = "1db51a4ec77fdf642aedba497bd7045af1611c1ed3ac1dc738e215f54c4136aa"
OLD_DOTNET = "a" * 64


def asf_plan(dotnet_sha=DOTNET_SHA, extra_deps=None):
    runtime = ["dotnet", *(extra_deps or [])]
    nodes = {
        "archi-steam-farm": {
            "name": "archi-steam-farm",
            "formula_sha256": ROOT_SHA,
            "runtime": runtime,
            "build": [],
            "test": [],
        },
        "dotnet": {"name": "dotnet", "formula_sha256": dotnet_sha},
    }
    for name in extra_deps or []:
        nodes[name] = {"name": name, "formula_sha256": "c" * 64}
    return {"nodes": nodes}


def asf_records():
    return {
        "archi-steam-farm": {
            "runtime_dependencies": [
                {"name": "dotnet", "pkg_version": "10.0.403", "formula_sha256": OLD_DOTNET},
            ]
        }
    }


class FormulaTestEnvTests(unittest.TestCase):
    def test_ci_brew_test_matches_official_github_actions(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS_HOMEBREW_SELF_HOSTED": "1", "GH_TOKEN": "secret"}, clear=False):
            env = formula_test_env(ci=True)
            self.assertEqual(env["HOMEBREW_GITHUB_ACTIONS"], "1")
            self.assertNotIn("GITHUB_ACTIONS_HOMEBREW_SELF_HOSTED", env)
            self.assertNotIn("GH_TOKEN", env)
            other = brew_env(ci=True)
            self.assertNotIn("HOMEBREW_GITHUB_ACTIONS", other)
            self.assertNotIn("GH_TOKEN", other)
        local = formula_test_env(ci=False)
        self.assertNotIn("HOMEBREW_GITHUB_ACTIONS", local)

    def test_ci_brew_test_timeout_covers_slow_intel_test_resources(self):
        with patch.dict(os.environ, {"HOMEBREW_TEST_TIMEOUT_SECS": "1"}, clear=False):
            env = formula_test_env(ci=True)
            self.assertEqual(env["HOMEBREW_TEST_TIMEOUT_SECS"], "1200")
            self.assertGreater(int(env["HOMEBREW_TEST_TIMEOUT_SECS"]), 5 * 60)
            self.assertNotEqual(brew_env(ci=True).get("HOMEBREW_TEST_TIMEOUT_SECS"), "1200")


class RepeatableHoldTests(unittest.TestCase):
    def test_classifier_accepts_only_the_allowlisted_signatures(self):
        self.assertEqual(
            classify_build_failure("error CA1307: Specify IFormatProvider"),
            "dotnet_analyzer_CA1307",
        )
        self.assertEqual(
            classify_build_failure("Error: The arm64 architecture is required for this software"),
            "arch_arm64_required",
        )
        self.assertEqual(
            classify_build_failure("failed assertion `Target device architecture is nil'"),
            "metal_device_arch_nil",
        )
        self.assertEqual(
            classify_build_failure("static assertion failed: DialectGitHub"),
            "static_assertion",
        )
        self.assertIsNone(classify_build_failure("CA1307 plus curl: (28) Timeout"))
        self.assertIsNone(classify_build_failure("error CA1307 and static assertion failed"))
        self.assertIsNone(classify_build_failure("unrelated compiler error"))
        self.assertIsNone(classify_build_failure(""))

    def test_hold_pair_is_the_root_plus_at_most_two_drifted_dependencies(self):
        descriptor = hold_descriptor("archi-steam-farm", asf_plan(), asf_records())
        self.assertEqual(descriptor["name"], "archi-steam-farm")
        self.assertEqual(descriptor["formula_sha256"], ROOT_SHA)
        self.assertEqual(descriptor["dependencies"], [{"name": "dotnet", "formula_sha256": DOTNET_SHA}])
        self.assertIsNone(hold_descriptor(
            "archi-steam-farm", asf_plan(extra_deps=["one", "two", "three"]), asf_records()))
        unchanged = hold_descriptor(
            "archi-steam-farm", asf_plan(dotnet_sha=OLD_DOTNET), asf_records())
        self.assertEqual(unchanged["dependencies"], [])

    def test_first_classified_failure_stays_red_and_writes_evidence(self):
        descriptor = hold_descriptor("archi-steam-farm", asf_plan(), asf_records())
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"INTELBREW_FAILURE_DIR": folder, "GITHUB_ACTIONS": "true"}, clear=False
        ), patch("intelbrew.ci.load_failure_history", return_value=[]):
            _maybe_convert_repeatable_hold(
                "archi-steam-farm", asf_plan(), asf_records(),
                {"repository": "adriank1410/homebrew-intel"},
                "error CA1307: Specify IFormatProvider",
            )
            record = json.loads((Path(folder) / "intelbrew-failure" / "intelbrew-failure.json").read_text())
            self.assertEqual(record["signature"], "dotnet_analyzer_CA1307")
            self.assertEqual(record["formula_sha256"], ROOT_SHA)
            self.assertEqual(record["dependencies"], descriptor["dependencies"])

    def test_second_matching_failure_converts_to_a_repeatable_hold(self):
        descriptor = hold_descriptor("archi-steam-farm", asf_plan(), asf_records())
        previous = make_failure_record(descriptor, "dotnet_analyzer_CA1307", "1")
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"INTELBREW_FAILURE_DIR": folder, "GITHUB_ACTIONS": "true"}, clear=False
        ), patch("intelbrew.ci.load_failure_history", return_value=[previous]):
            with self.assertRaises(RepeatableHold) as raised:
                _maybe_convert_repeatable_hold(
                    "archi-steam-farm", asf_plan(), asf_records(),
                    {"repository": "adriank1410/homebrew-intel"},
                    "error CA1307: Specify IFormatProvider",
                )
            self.assertIn("2 consecutive classified failures", str(raised.exception))
            self.assertIn("archi-steam-farm", str(raised.exception))
            self.assertTrue((Path(folder) / "intelbrew-failure" / "intelbrew-failure.json").is_file())

    def test_prior_matching_history_skips_the_compile(self):
        descriptor = hold_descriptor("archi-steam-farm", asf_plan(), asf_records())
        history = [
            make_failure_record(descriptor, "dotnet_analyzer_CA1307", "2"),
            make_failure_record(descriptor, "dotnet_analyzer_CA1307", "1"),
        ]
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"INTELBREW_FAILURE_DIR": folder, "GITHUB_ACTIONS": "true"}, clear=False
        ), patch("intelbrew.ci.load_failure_history", return_value=history):
            with self.assertRaises(RepeatableHold):
                _maybe_raise_prior_hold(
                    "archi-steam-farm", asf_plan(), asf_records(),
                    {"repository": "adriank1410/homebrew-intel"},
                )

    def test_changed_formula_hash_or_transient_error_is_not_held(self):
        descriptor = hold_descriptor("archi-steam-farm", asf_plan(), asf_records())
        previous = make_failure_record(descriptor, "dotnet_analyzer_CA1307", "1")
        changed = asf_plan()
        changed["nodes"]["archi-steam-farm"]["formula_sha256"] = "e" * 64
        self.assertEqual(consecutive_matches([previous], hold_descriptor(
            "archi-steam-farm", changed, asf_records()), "dotnet_analyzer_CA1307"), 0)
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"INTELBREW_FAILURE_DIR": folder, "GITHUB_ACTIONS": "true"}, clear=False
        ), patch("intelbrew.ci.load_failure_history", return_value=[previous]):
            _maybe_convert_repeatable_hold(
                "archi-steam-farm", asf_plan(), asf_records(),
                {"repository": "adriank1410/homebrew-intel"},
                "error CA1307 plus curl: (28) Timeout",
            )
            self.assertFalse((Path(folder) / "intelbrew-failure" / "intelbrew-failure.json").exists())

    def test_main_returns_zero_for_a_confirmed_hold_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "github-output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}, clear=False), patch(
                "intelbrew.ci.build", side_effect=RepeatableHold(describe_hold(
                    hold_descriptor("archi-steam-farm", asf_plan(), asf_records()),
                    "dotnet_analyzer_CA1307", 2))
            ), patch("sys.argv", ["intelbrew.ci", "build", "--root", "archi-steam-farm",
                                   "--output", str(Path(folder) / "candidate")]):
                self.assertEqual(main(), 0)
            text = output.read_text()
            self.assertIn("held=true", text)
            self.assertIn("repeatable_hold=true", text)

    def test_history_loader_reads_newest_artifact_records_and_keeps_tokens_out_of_argv(self):
        descriptor = hold_descriptor("archi-steam-farm", asf_plan(), asf_records())
        record = make_failure_record(descriptor, "dotnet_analyzer_CA1307", "9")
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "artifact.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("intelbrew-failure.json", json.dumps(record))
            calls = []

            def fake_run(args, **kwargs):
                calls.append(args)
                self.assertNotIn("secret-token", args)
                if args[:3] == ["gh", "api", "repos/adriank1410/homebrew-intel/actions/artifacts"]:
                    return json.dumps({"artifacts": [
                        {"id": 1, "name": "intelbrew-failure-archi-steam-farm",
                         "expired": False, "created_at": "2026-11-13T10:00:00Z"},
                    ]})
                if "--output" in args:
                    Path(args[args.index("--output") + 1]).write_bytes(archive.read_bytes())
                    return ""
                raise AssertionError(args)

            history = load_failure_history(
                "adriank1410/homebrew-intel", "archi-steam-farm", run=fake_run,
                env={"GH_TOKEN": "secret-token"},
            )
            self.assertEqual(history[0]["run_id"], "9")
            self.assertTrue(any(arg == "--output" for args in calls for arg in args))

    def test_history_loader_does_not_call_github_without_a_token(self):
        def boom(*args, **kwargs):
            raise AssertionError("should not call gh")
        self.assertEqual(
            load_failure_history("adriank1410/homebrew-intel", "archi-steam-farm", run=boom, env={}),
            [],
        )


if __name__ == "__main__":
    unittest.main()
