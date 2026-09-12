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
from intelbrew.release_retention import active_release_tags, referenced_releases, select_releases


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "adriank1410/homebrew-intel")


def gh_json(arguments: list[str]):
    result = subprocess.run(["gh", *arguments], cwd=ROOT, check=True, text=True,
                            capture_output=True)
    return json.loads(result.stdout)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-delete", type=int, default=20)
    args = parser.parse_args()
    apply_mode = args.apply or os.environ.get("INTELBREW_RETENTION_APPLY", "").lower() == "true"
    releases = gh_json(["release", "list", "--repo", REPOSITORY, "--limit", "1000",
                        "--json", "tagName,createdAt,isDraft,isPrerelease"])
    prs = open_pull_requests()
    selected = select_releases(releases, referenced=referenced_releases(ROOT / "registry"),
                               active=active_release_tags(prs), now=datetime.now(timezone.utc))
    selected = selected[:max(0, args.max_delete)]
    mode = "APPLY" if apply_mode else "DRY-RUN"
    print(f"{mode}: {len(selected)} release(s) selected for deletion")
    for release in selected:
        tag = release["tagName"]
        print(tag)
        if apply_mode:
            subprocess.run(["gh", "release", "delete", tag, "--repo", REPOSITORY,
                            "--yes", "--cleanup-tag"], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
