# SPDX-License-Identifier: BSD-2-Clause
from datetime import datetime, timedelta, timezone

from intelbrew.release_retention import active_release_tags, select_releases


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
