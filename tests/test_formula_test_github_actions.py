# SPDX-License-Identifier: BSD-2-Clause
"""Coverage for formula-test GitHub Actions entries, including streamlink 8.6.3."""
import os
import unittest
from unittest.mock import patch

from helpers import H, meta
from intelbrew.core import (
    Error, formula_test_github_actions_env, formula_test_uses_github_actions,
    load_config, validate_formula_test_github_actions,
)

STREAMLINK_SHA = "dbdc697240b54f4828ad762af0e8c3c519fbf1de23474153bc3bb31ce6ba9a28"
LIVE_ENTRY = {"name": "streamlink", "formula_sha256": STREAMLINK_SHA}


class ValidationTests(unittest.TestCase):
    def test_empty_and_valid_entries_normalize(self):
        self.assertEqual(validate_formula_test_github_actions([]), [])
        self.assertEqual(validate_formula_test_github_actions(None), [])
        self.assertEqual(
            validate_formula_test_github_actions(
                [{"name": "homebrew/core/streamlink", "formula_sha256": STREAMLINK_SHA}]),
            [LIVE_ENTRY],
        )

    def test_rejects_invalid_shapes(self):
        cases = [
            {"not": "a list"},
            [{"name": "streamlink"}],
            [{"name": "streamlink", "formula_sha256": STREAMLINK_SHA, "extra": 1}],
            [{"name": "streamlink", "formula_sha256": "abc"}],
            [{"name": "bad name!", "formula_sha256": STREAMLINK_SHA}],
            [LIVE_ENTRY, dict(LIVE_ENTRY)],
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(Error):
                validate_formula_test_github_actions(value)

    def test_rejects_oversized_lists(self):
        entries = [{"name": f"tool{i}", "formula_sha256": H} for i in range(51)]
        with self.assertRaises(Error):
            validate_formula_test_github_actions(entries)


class LivePolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_live_policy_has_only_the_pinned_streamlink_entry(self):
        self.assertEqual(self.config["formula_test_github_actions"], [LIVE_ENTRY])

    def test_live_entry_matches_only_that_formula_hash(self):
        matching = meta("streamlink", formula_sha256=STREAMLINK_SHA)
        other_hash = meta("streamlink", formula_sha256=H)
        other_formula = meta("ffmpeg", formula_sha256=STREAMLINK_SHA)
        self.assertTrue(formula_test_uses_github_actions("streamlink", matching, self.config))
        self.assertFalse(formula_test_uses_github_actions("streamlink", other_hash, self.config))
        self.assertFalse(formula_test_uses_github_actions("ffmpeg", other_formula, self.config))
        self.assertFalse(formula_test_uses_github_actions("streamlink", "not-meta", self.config))


class EnvironmentTests(unittest.TestCase):
    def test_sets_flag_and_clears_self_hosted_only_for_a_matching_recipe(self):
        config = {"formula_test_github_actions": [LIVE_ENTRY]}
        matching = meta("streamlink", formula_sha256=STREAMLINK_SHA)
        extra = {"GITHUB_ACTIONS_HOMEBREW_SELF_HOSTED": "1"}
        with patch.dict(os.environ, extra, clear=False):
            os.environ.pop("HOMEBREW_GITHUB_ACTIONS", None)
            applied = formula_test_github_actions_env("streamlink", matching, config, ci=True)
            skipped = formula_test_github_actions_env(
                "streamlink", meta("streamlink", formula_sha256=H), config, ci=True)
            other = formula_test_github_actions_env("ffmpeg", meta("ffmpeg"), config, ci=True)
        self.assertEqual(applied["HOMEBREW_GITHUB_ACTIONS"], "1")
        self.assertNotIn("GITHUB_ACTIONS_HOMEBREW_SELF_HOSTED", applied)
        self.assertNotIn("HOMEBREW_GITHUB_ACTIONS", skipped)
        self.assertEqual(skipped.get("GITHUB_ACTIONS_HOMEBREW_SELF_HOSTED"), "1")
        self.assertNotIn("HOMEBREW_GITHUB_ACTIONS", other)
        self.assertEqual(other.get("GITHUB_ACTIONS_HOMEBREW_SELF_HOSTED"), "1")
        self.assertEqual(applied["HOMEBREW_NO_INSTALL_FROM_API"], "1")

    def test_does_not_set_the_flag_when_the_list_is_empty(self):
        env = formula_test_github_actions_env("streamlink", meta("streamlink", formula_sha256=STREAMLINK_SHA), {})
        self.assertNotIn("HOMEBREW_GITHUB_ACTIONS", env)


if __name__ == "__main__":
    unittest.main()
