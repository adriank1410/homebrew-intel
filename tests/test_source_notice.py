# SPDX-License-Identifier: BSD-2-Clause
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import meta, record
from intelbrew.cli import apply_plan
from intelbrew.core import Error, Planner, artifact_url, load_config


class SourceNoticeTests(unittest.TestCase):
    def test_personal_install_identifies_corresponding_source(self):
        item = meta()
        records = {"tool": record()}
        plan = Planner(lambda names: {"tool": item}, records).make(["tool"])
        def native(request, **kwargs):
            if request["mode"] == "inspect": return {"tool": item}
            if request["mode"] == "receipt": return {"poured_from_bottle": True}
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output), \
                patch("intelbrew.cli.native", side_effect=native), \
                patch("intelbrew.cli.download"), patch("intelbrew.cli.attest"), \
                patch("intelbrew.cli.check_bottle"):
            apply_plan(plan, records, load_config(), cache=Path(directory) / "cache")
        expected = artifact_url(load_config()["repository"], records["tool"], records["tool"]["source"]["filename"])
        self.assertNotIn("Source and license notices for tool:", output.getvalue())
        self.assertNotIn("Installation journal:", output.getvalue())
        self.assertIn("Attestation verified (SLSA Provenance v1)", output.getvalue())
        self.assertNotIn("Pouring ", output.getvalue())
        self.assertNotIn("/usr/local/Cellar/", output.getvalue())

    def test_personal_install_verbose_passes_verbose_to_attest(self):
        item = meta()
        records = {"tool": record()}
        plan = Planner(lambda names: {"tool": item}, records).make(["tool"])
        def native(request, **kwargs):
            if request["mode"] == "inspect": return {"tool": item}
            if request["mode"] == "receipt": return {"poured_from_bottle": True}
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output), \
                patch("intelbrew.cli.native", side_effect=native), \
                patch("intelbrew.cli.download"), patch("intelbrew.cli.attest") as attest_mock, \
                patch("intelbrew.cli.check_bottle"):
            apply_plan(plan, records, load_config(), cache=Path(directory) / "cache", verbose=True)
        self.assertTrue(attest_mock.call_args.kwargs.get("verbose"))
        expected = artifact_url(load_config()["repository"], records["tool"], records["tool"]["source"]["filename"])
        self.assertIn("Source and license notices for tool:", output.getvalue())
        self.assertIn(expected, output.getvalue())
        self.assertIn("Installation journal:", output.getvalue())

    def test_install_failure_includes_journal_path_in_error(self):
        item = meta()
        records = {"tool": record()}
        plan = Planner(lambda names: {"tool": item}, records).make(["tool"])
        def native_fail(request, **kwargs):
            if request["mode"] == "inspect": return {"tool": item}
            if request["mode"] == "install": raise Error("bottle pour failed")
        with tempfile.TemporaryDirectory() as directory, \
                patch("intelbrew.cli.native", side_effect=native_fail), \
                patch("intelbrew.cli.download"), patch("intelbrew.cli.attest"), \
                patch("intelbrew.cli.check_bottle"):
            with self.assertRaises(Exception) as ctx:
                apply_plan(plan, records, load_config(), cache=Path(directory) / "cache", verbose=False)
            self.assertIn("Journal: ", str(ctx.exception))
            self.assertIn("Stopped after 0 packages;", str(ctx.exception))

    def test_summary_bottle_count_pluralization(self):
        records = {"tool": record("tool"), "tool2": record("tool2")}
        def native(request, **kwargs):
            if request["mode"] == "inspect":
                return {name: meta(name) for name in ("tool", "tool2")}
            if request["mode"] == "receipt":
                return {"poured_from_bottle": True}

        # Case 1: single bottle installed -> "1 bottle."
        plan_single = Planner(lambda names: {"tool": meta("tool")}, records).make(["tool"])
        output_single = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output_single), \
                patch("intelbrew.cli.native", side_effect=native), \
                patch("intelbrew.cli.download"), patch("intelbrew.cli.attest"), \
                patch("intelbrew.cli.check_bottle"):
            apply_plan(plan_single, records, load_config(), cache=Path(directory) / "cache")
        self.assertIn("🍺  Installed 1 bottle. No Homebrew source build was permitted.", output_single.getvalue())

        # Case 2: multiple bottles installed -> "2 bottles."
        plan_multi = Planner(lambda names: {name: meta(name) for name in ("tool", "tool2")}, records).make(["tool", "tool2"])
        output_multi = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output_multi), \
                patch("intelbrew.cli.native", side_effect=native), \
                patch("intelbrew.cli.download"), patch("intelbrew.cli.attest"), \
                patch("intelbrew.cli.check_bottle"):
            apply_plan(plan_multi, records, load_config(), cache=Path(directory) / "cache")
        self.assertIn("🍺  Installed 2 bottles. No Homebrew source build was permitted.", output_multi.getvalue())

        # Case 3: 0 bottles installed -> "0 bottles."
        plan_zero = Planner(lambda names: {"tool": meta("tool", installed=True)}, records).make(["tool"])
        output_zero = io.StringIO()
        def native_zero(request, **kwargs):
            if request["mode"] == "inspect":
                return {"tool": meta("tool", installed=True)}
            if request["mode"] == "receipt":
                return {"poured_from_bottle": True}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output_zero), \
                patch("intelbrew.cli.native", side_effect=native_zero), \
                patch("intelbrew.cli.download"), patch("intelbrew.cli.attest"), \
                patch("intelbrew.cli.check_bottle"):
            apply_plan(plan_zero, records, load_config(), cache=Path(directory) / "cache")
        self.assertIn("🍺  Installed 0 bottles. No Homebrew source build was permitted.", output_zero.getvalue())
