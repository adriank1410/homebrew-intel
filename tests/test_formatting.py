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


if __name__ == "__main__":
    unittest.main()
