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


class RetentionSafetyTests(unittest.TestCase):
    # These two were module-level functions. CI runs ``unittest discover``,
    # which collects TestCase methods only, so they never ran.
    def test_retention_keeps_referenced_active_and_newest_unreferenced(self):
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
        self.assertEqual([item["tagName"] for item in selected],
                         ["intel-2-1-tool", "intel-5-1-draft"])

    def test_active_release_tags_come_from_open_bottle_prs(self):
        self.assertEqual(
            active_release_tags([{"headRefName": "bottles/intel-2-1-tool"},
                                 {"headRefName": "feature/nope"}]),
            {"intel-2-1-tool"},
        )

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

    def test_release_listing_is_not_cut_off_at_the_newest_thousand(self):
        # 1000 releases covered about 17 days in November 2026, less than the
        # 30-day threshold, so a 1000-release list never held a candidate.
        listed = [{"tagName": f"intel-{i}-1-tool"} for i in range(1500)]
        with patch.object(retention_script, "gh_json", return_value=listed) as gh_json:
            self.assertEqual(retention_script.all_releases(), listed)
        arguments = gh_json.call_args.args[0]
        self.assertEqual(arguments[:2], ["release", "list"])
        limit = int(arguments[arguments.index("--limit") + 1])
        self.assertEqual(limit, retention_script.RELEASE_LIST_LIMIT)
        self.assertGreaterEqual(limit, 100_000)

    def test_release_listing_refuses_a_list_that_reaches_the_limit(self):
        listed = [{"tagName": f"intel-{i}-1-tool"} for i in range(3)]
        with patch.object(retention_script, "RELEASE_LIST_LIMIT", 3), \
                patch.object(retention_script, "gh_json", return_value=listed):
            with self.assertRaisesRegex(Error, "partial list"):
                retention_script.all_releases()
        with patch.object(retention_script, "gh_json", return_value={"tagName": "x"}):
            with self.assertRaisesRegex(Error, "invalid release list"):
                retention_script.all_releases()

    def test_main_selects_from_the_complete_release_list(self):
        import io, sys
        # main() compares against the real clock, not the fixed NOW above.
        today = datetime.now(timezone.utc)
        def listed(tag, days):
            created = today - timedelta(days=days)
            return {"tagName": tag, "createdAt": created.isoformat().replace("+00:00", "Z"),
                    "isDraft": False}
        old = [listed(f"intel-{i}-1-tool", 40 + i) for i in range(1, 4)]
        recent = [listed(f"intel-{100 + i}-1-tool", 1) for i in range(1200)]
        with patch.object(sys, "argv", ["release-retention.py"]), \
                patch.dict("os.environ", {"INTELBREW_RETENTION_APPLY": "false"}), \
                patch.object(retention_script, "gh_json", return_value=recent + old), \
                patch.object(retention_script, "open_pull_requests", return_value=[]), \
                patch.object(retention_script, "referenced_releases", return_value=set()), \
                patch.object(retention_script, "gh") as gh, \
                patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(retention_script.main(), 0)
        self.assertIn("DRY-RUN: 3 releases selected for deletion", out.getvalue())
        gh.assert_not_called()

    def test_workflow_deletes_only_from_main(self):
        workflow = (ROOT / ".github/workflows/release-retention.yml").read_text(encoding="utf-8")
        self.assertIn(
            "INTELBREW_RETENTION_APPLY: ${{ github.ref == 'refs/heads/main' && "
            "(github.event_name == 'schedule' || inputs.apply) }}", workflow)
        self.assertEqual(workflow.count("INTELBREW_RETENTION_APPLY:"), 1)

    def test_main_summary_pluralization(self):
        import io, sys
        for count, expected in [(1, "DRY-RUN: 1 release selected for deletion"),
                                (2, "DRY-RUN: 2 releases selected for deletion"),
                                (0, "DRY-RUN: 0 releases selected for deletion")]:
            with self.subTest(count=count), \
                    patch.object(sys, "argv", ["release-retention.py"]), \
                    patch.object(retention_script, "gh_json", return_value=[]), \
                    patch.object(retention_script, "open_pull_requests", return_value=[]), \
                    patch.object(retention_script, "referenced_releases", return_value=set()), \
                    patch.object(retention_script, "select_releases", return_value=[{"tagName": f"t{i}"} for i in range(count)]), \
                    patch("sys.stdout", new_callable=io.StringIO) as out:
                retention_script.main()
                self.assertIn(expected, out.getvalue())
                self.assertNotIn("release(s)", out.getvalue())

