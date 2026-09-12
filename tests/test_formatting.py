# SPDX-License-Identifier: BSD-2-Clause
import io
import os
import unittest
from unittest.mock import patch

import intelbrew.formatting as fmt
from helpers import meta
import intelbrew.cli as cli


class TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


class FormattingTests(unittest.TestCase):
    def test_is_color_enabled_respects_tty_and_env(self):
        stream = TtyStream()
        plain_stream = io.StringIO()

        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(fmt.is_color_enabled(stream))
            self.assertFalse(fmt.is_color_enabled(plain_stream))

        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertFalse(fmt.is_color_enabled(stream))

        with patch.dict(os.environ, {"HOMEBREW_NO_COLOR": "1"}):
            self.assertFalse(fmt.is_color_enabled(stream))

        with patch.dict(os.environ, {"TERM": "dumb"}):
            self.assertFalse(fmt.is_color_enabled(stream))

        with patch.dict(os.environ, {"HOMEBREW_COLOR": "1"}):
            self.assertTrue(fmt.is_color_enabled(plain_stream))

    def test_style_applies_ansi_only_when_color_enabled(self):
        tty = TtyStream()
        plain = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(fmt.style("test", fmt.BOLD_RED, tty), "\033[1;31mtest\033[0m")
            self.assertEqual(fmt.style("test", fmt.BOLD_RED, plain), "test")

    def test_ohai_prints_brew_header(self):
        tty = TtyStream()
        plain = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            fmt.ohai("Updating", "details", stream=tty)
            fmt.ohai("Updating", "details", stream=plain)

        self.assertIn("\033[1;34m==>\033[0m \033[1mUpdating\033[0m", tty.getvalue())
        self.assertIn("    details", tty.getvalue())

        self.assertIn("==> Updating", plain.getvalue())
        self.assertNotIn("\033[", plain.getvalue())
        self.assertIn("    details", plain.getvalue())

    def test_opoo_prints_warning(self):
        tty = TtyStream()
        plain = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            fmt.opoo("something skipped", stream=tty)
            fmt.opoo("something skipped", stream=plain)

        self.assertIn("\033[1;33mWarning:\033[0m something skipped", tty.getvalue())
        self.assertEqual(plain.getvalue().strip(), "Warning: something skipped")

    def test_onoe_prints_error_on_stderr(self):
        tty = TtyStream()
        plain = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            fmt.onoe("critical failure", stream=tty)
            fmt.onoe("critical failure", stream=plain)

        self.assertIn("\033[1;31mError:\033[0m critical failure", tty.getvalue())
        self.assertEqual(plain.getvalue().strip(), "Error: critical failure")

    def test_render_plan_applies_colors_to_providers_on_tty(self):
        plan = {
            "order": ["tool-personal", "tool-official", "tool-missing"],
            "nodes": {
                "tool-personal": meta("tool-personal", provider="personal"),
                "tool-official": meta("tool-official", provider="official"),
                "tool-missing": meta("tool-missing", provider="missing"),
            },
        }
        tty = TtyStream()
        plain = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            cli.render(plan, stream=tty)
            cli.render(plan, stream=plain)

        # On TTY, provider names must be colorized
        tty_out = tty.getvalue()
        self.assertIn("\033[1;36mpersonal", tty_out)
        self.assertIn("\033[1;32mofficial", tty_out)
        self.assertIn("\033[1;31mmissing", tty_out)

        # On plain stream (non-TTY), no escape sequences
        plain_out = plain.getvalue()
        self.assertNotIn("\033[", plain_out)
        self.assertIn("tool-personal", plain_out)
        self.assertIn("personal", plain_out)
        self.assertIn("official", plain_out)
        self.assertIn("missing", plain_out)

    def test_render_upgrade_formats_outdated_packages_and_skips_installed(self):
        plan = {
            "roots": ["simdutf", "nss"],
            "order": ["ca-certificates", "simdutf", "nss"],
            "nodes": {
                "ca-certificates": meta("ca-certificates", provider="installed", installed=True),
                "simdutf": {
                    "provider": "personal", "pkg_version": "9.1.2",
                    "installed_versions": ["9.1.1"],
                },
                "nss": {
                    "provider": "personal", "pkg_version": "3.129",
                    "installed_versions": ["3.128"],
                },
            },
        }
        plain = io.StringIO()
        tty = TtyStream()
        with patch.dict(os.environ, {}, clear=True):
            cli.render_upgrade(plan, stream=plain)
            cli.render_upgrade(plan, stream=tty)

        plain_out = plain.getvalue()
        self.assertIn("==> Upgrading 2 outdated packages:", plain_out)
        self.assertIn("simdutf 9.1.1 -> 9.1.2", plain_out)
        self.assertIn("nss 3.128 -> 3.129", plain_out)
        self.assertNotIn("ca-certificates", plain_out)
        self.assertNotIn("installed", plain_out)

        tty_out = tty.getvalue()
        self.assertIn("\033[1;34m==>\033[0m \033[1mUpgrading 2 outdated packages:\033[0m", tty_out)
        self.assertIn("\033[1msimdutf\033[0m 9.1.1 -> 9.1.2", tty_out)
        self.assertIn("\033[1mnss\033[0m 3.128 -> 3.129", tty_out)

    def test_render_upgrade_with_new_dependency(self):
        plan = {
            "roots": ["tool"],
            "order": ["new-dep", "tool"],
            "nodes": {
                "new-dep": {"provider": "official", "pkg_version": "1.5", "installed_versions": []},
                "tool": {"provider": "personal", "pkg_version": "2.0", "installed_versions": ["1.0"]},
            },
        }
        plain = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            cli.render_upgrade(plan, stream=plain)

        plain_out = plain.getvalue()
        self.assertIn("==> Upgrading 1 outdated package:", plain_out)
        self.assertIn("tool 1.0 -> 2.0", plain_out)
        self.assertIn("==> Installing 1 dependency:", plain_out)
        self.assertIn("new-dep 1.5", plain_out)

    def test_render_install_formats_packages_and_dependencies(self):
        plan = {
            "roots": ["app"],
            "order": ["lib", "app"],
            "nodes": {
                "lib": {"provider": "official", "pkg_version": "1.0", "installed_versions": []},
                "app": {"provider": "personal", "pkg_version": "2.0", "installed_versions": []},
            },
        }
        plain = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            cli.render_install(plan, stream=plain)

        plain_out = plain.getvalue()
        self.assertIn("==> Installing dependencies for app: lib", plain_out)
        self.assertIn("==> Installing 1 package:", plain_out)
        self.assertIn("app 2.0", plain_out)

    def test_main_upgrade_calls_render_upgrade(self):
        plan = {
            "roots": ["simdutf"],
            "order": ["simdutf"],
            "nodes": {"simdutf": {"provider": "personal", "pkg_version": "9.1.2", "installed_versions": ["9.1.1"]}},
            "schema": 1,
        }
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value={"max_graph_nodes": 400, "repository": "adriank1410/homebrew-intel"}), \
             patch.object(cli, "registry", return_value={}), \
             patch.object(cli, "native", side_effect=[["simdutf"], {"core": [], "external_taps": []}, {}]), \
             patch.object(cli, "coverage_report", return_value={"unmonitored_core": []}), \
             patch.object(cli, "Planner") as mock_planner, \
             patch.object(cli, "render_upgrade") as mock_render_upgrade:
            mock_planner.return_value.make.return_value = plan
            self.assertEqual(cli.main(["upgrade"]), 0)
            mock_render_upgrade.assert_called_once_with(plan)


if __name__ == "__main__":
    unittest.main()
