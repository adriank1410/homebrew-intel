#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Dry-run or apply safe historical GitHub release retention."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelbrew.core import Error
from intelbrew.registry_pr import gh, gh_json
from intelbrew.release_retention import active_release_tags, referenced_releases, select_releases


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "adriank1410/homebrew-intel")
RELEASE_LIST_LIMIT = 100_000


def open_pull_requests() -> list[dict]:
    payload = gh_json([
        "api", "--paginate", "--slurp",
        f"repos/{REPOSITORY}/pulls?state=open&per_page=100",
    ])
    if not isinstance(payload, list):
        raise Error("GitHub returned an invalid pull-request page list")
    result = []
    for page in payload:
        if not isinstance(page, list):
            raise Error("GitHub returned an invalid pull-request page")
        for pull in page:
            if not isinstance(pull, dict):
                raise Error("GitHub returned an invalid pull request")
            result.append(pull)
    return result


def all_releases() -> list[dict]:
    """List every release, or fail instead of judging a partial list.

    ``gh release list`` returns the newest ``--limit`` releases and gives no
    sign that older ones were cut off. The old ones are the deletion
    candidates, so a list that reaches the limit is refused.
    """
    releases = gh_json(["release", "list", "--repo", REPOSITORY, "--limit", str(RELEASE_LIST_LIMIT),
                        "--json", "tagName,createdAt,isDraft,isPrerelease"])
    if not isinstance(releases, list):
        raise Error("GitHub CLI returned an invalid release list")
    if len(releases) >= RELEASE_LIST_LIMIT:
        raise Error("Release list reached its limit; refusing to select from a partial list")
    return releases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    # About 60 releases a day were published in early November 2026, so a
    # cap of 20 could not keep up. On 2026-11-11 about 990 releases were
    # deletable; at 100 a day that backlog lasts about 25 days, at 200 about 7.
    # 100 deletions took 3.5 minutes that day, two DELETE requests each.
    parser.add_argument("--max-delete", type=int, default=200)
    args = parser.parse_args()
    apply_mode = args.apply or os.environ.get("INTELBREW_RETENTION_APPLY", "").lower() == "true"
    releases = all_releases()
    prs = open_pull_requests()
    selected = select_releases(releases, referenced=referenced_releases(ROOT / "registry"),
                               active=active_release_tags(prs), now=datetime.now(timezone.utc))
    selected = selected[:max(0, args.max_delete)]
    mode = "APPLY" if apply_mode else "DRY-RUN"
    releases = "release" if len(selected) == 1 else "releases"
    print(f"{mode}: {len(selected)} {releases} selected for deletion")
    for release in selected:
        tag = release["tagName"]
        print(tag)
        if apply_mode:
            gh(["release", "delete", tag, "--repo", REPOSITORY,
                "--yes", "--cleanup-tag"], capture=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
