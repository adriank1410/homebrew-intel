# SPDX-License-Identifier: BSD-2-Clause
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from helpers import record
from intelbrew.core import Error
from intelbrew.release_retention import referenced_releases

from intelbrew.release_retention import active_release_tags, select_releases


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "release_retention_script", ROOT / "scripts/release-retention.py"
)
retention_script = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(retention_script)


NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def release(tag, days, *, draft=False):
    created = NOW - timedelta(days=days)
    return {"tagName": tag, "createdAt": created.isoformat().replace("+00:00", "Z"), "isDraft": draft}


def test_retention_keeps_referenced_active_and_newest_unreferenced():
    releases = [
        release("intel-1-1-tool", 40),
        release("intel-2-1-tool", 35),
        release("intel-6-1-tool", 10),
        release("intel-3-1-tool", 40),
        release("intel-4-1-other", 40),
        release("intel-5-1-draft", 10, draft=True),
        release("intel-7-1-draft", 1, draft=True),
    ]
    selected = select_releases(releases, referenced={"intel-1-1-tool"},
                               active={"intel-3-1-tool"}, now=NOW)
    assert [item["tagName"] for item in selected] == ["intel-2-1-tool", "intel-5-1-draft"]


def test_active_release_tags_come_from_open_bottle_prs():
    assert active_release_tags([{"headRefName": "bottles/intel-2-1-tool"},
                                {"headRefName": "feature/nope"}]) == {"intel-2-1-tool"}


class RetentionSafetyTests(unittest.TestCase):
    def test_active_release_tags_accepts_github_api_head_shape(self):
        self.assertEqual(
            active_release_tags([{"head": {"ref": "bottles/intel-2-1-tool"}},
                                 {"head": {"ref": "feature/nope"}}]),
            {"intel-2-1-tool"},
        )

    def test_referenced_releases_rejects_missing_registry_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(Error, "registry"):
                referenced_releases(Path(directory) / "registry")

    def test_referenced_releases_rejects_malformed_registry_record(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry"
            registry.mkdir()
            (registry / "tool.json").write_text(
                json.dumps(record(release=None)), encoding="utf-8"
            )

            with self.assertRaises(Error):
                referenced_releases(registry)

    def test_referenced_releases_validates_and_collects_records(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry"
            registry.mkdir()
            for name, release in (("tool", "intel-1-1-tool"), ("other", "intel-2-1-other")):
                (registry / f"{name}.json").write_text(
                    json.dumps(record(name=name, release=release)), encoding="utf-8"
                )

            self.assertEqual(
                referenced_releases(registry),
                {"intel-1-1-tool", "intel-2-1-other"},
            )

    def test_open_pull_requests_uses_paginated_api_and_flattens_pages(self):
        pages = [
            [{"head": {"ref": "bottles/intel-1-1-tool"}}],
            [{"head": {"ref": "bottles/intel-2-1-tool"}}],
        ]
        with patch.object(retention_script, "gh_json", return_value=pages) as gh_json:
            pulls = retention_script.open_pull_requests()

        self.assertEqual(
            active_release_tags(pulls), {"intel-1-1-tool", "intel-2-1-tool"}
        )
        gh_json.assert_called_once_with([
            "api", "--paginate", "--slurp",
            "repos/adriank1410/homebrew-intel/pulls?state=open&per_page=100",
        ])
