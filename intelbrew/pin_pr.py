# SPDX-License-Identifier: BSD-2-Clause
"""Merge the reviewed Homebrew pin after its exact head passes tests."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

from .core import ROOT, Error, retry_transient, run

PIN_BRANCH = "automation/homebrew-pins"
PIN_PATH = "policy/config.json"
MAIN = "main"
APP_LOGIN_RE = re.compile(r"app/[a-z0-9][a-z0-9-]*\Z")
HEAD_RE = re.compile(r"[0-9a-f]{40}\Z")
FAILED_CONCLUSIONS = {
    "FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE",
    "STALE", "NEUTRAL", "SKIPPED",
}


def gh(arguments: list[str], *, capture: bool = True) -> str:
    """Run gh with no credential helper or shell interpolation."""
    return retry_transient(lambda: run(["gh", *arguments], capture=capture, cwd=ROOT))


def gh_json(arguments: list[str]) -> Any:
    try:
        return json.loads(gh(arguments))
    except (ValueError, TypeError) as exc:
        raise Error("GitHub CLI returned invalid JSON") from exc


def _one_pin_pr(repository: str) -> dict[str, Any] | None:
    payload = gh_json([
        "pr", "list", "--repo", repository, "--head", PIN_BRANCH, "--base", MAIN,
        "--state", "open", "--limit", "20", "--json",
        "number,state,author,headRefName,headRefOid,baseRefName,headRepository,"
        "isCrossRepository,statusCheckRollup,autoMergeRequest,mergeStateStatus",
    ])
    if not isinstance(payload, list):
        raise Error("GitHub CLI returned an invalid PR list")
    if not payload:
        return None
    if len(payload) != 1 or not isinstance(payload[0], dict):
        raise Error("Multiple open Homebrew pin PRs")
    return payload[0]


def _require_owned(pr: dict[str, Any], repository: str) -> None:
    bot = os.environ.get("INTELBREW_BOT_LOGIN")
    author = pr.get("author") or {}
    head = pr.get("headRepository") or {}
    if (not isinstance(bot, str) or APP_LOGIN_RE.fullmatch(bot) is None or
            pr.get("state", "OPEN") != "OPEN" or
            pr.get("baseRefName") != MAIN or
            pr.get("headRefName") != PIN_BRANCH or
            head.get("nameWithOwner") != repository or
            pr.get("isCrossRepository") is not False or
            author.get("login") != bot or
            author.get("is_bot") is not True):
        raise Error("Refusing a Homebrew pin PR outside the App-owned branch")
    if not isinstance(pr.get("headRefOid"), str) or HEAD_RE.fullmatch(pr["headRefOid"]) is None:
        raise Error("Pin PR has no valid head commit")
    if not isinstance(pr.get("number"), int):
        raise Error("Pin PR has no number")


def _require_pin_files(repository: str, pr: dict[str, Any]) -> None:
    payload = gh_json(["api", "--paginate", "--slurp",
                       f"repos/{repository}/pulls/{pr['number']}/files"])
    if not isinstance(payload, list):
        raise Error("GitHub returned an invalid pin file list")
    files: list[Any] = []
    for page in payload:
        if not isinstance(page, list):
            raise Error("GitHub returned an invalid pin file page")
        files.extend(page)
    item = files[0] if len(files) == 1 and isinstance(files[0], dict) else None
    path = item.get("filename", item.get("path")) if item else None
    status = str(item.get("status", "")).lower() if item else ""
    if (item is None or path != PIN_PATH or status != "modified" or
            item.get("previous_filename")):
        raise Error("Homebrew pin PR changes a file outside policy/config.json")


def _tests_conclusion(pr: dict[str, Any]) -> str | None:
    rollup = pr.get("statusCheckRollup")
    if not isinstance(rollup, list):
        return None
    found = [item for item in rollup if isinstance(item, dict) and item.get("name") == "tests"]
    if len(found) != 1:
        return None
    if str(found[0].get("status", "")).upper() != "COMPLETED":
        return "PENDING"
    conclusion = str(found[0].get("conclusion", "")).upper()
    return conclusion or "PENDING"


def reconcile_pin(repository: str) -> str:
    """Squash-merge the open pin PR, or leave it until its head is ready."""
    pr = _one_pin_pr(repository)
    if pr is None:
        return "absent"
    _require_owned(pr, repository)
    if pr.get("autoMergeRequest"):
        gh(["pr", "merge", str(pr["number"]), "--repo", repository, "--disable-auto"],
           capture=False)
        pr = _one_pin_pr(repository)
        if pr is None:
            raise Error("Pin PR disappeared after disabling auto-merge")
        _require_owned(pr, repository)
    _require_pin_files(repository, pr)
    number, head = pr["number"], pr["headRefOid"]
    state = pr.get("mergeStateStatus")
    if state == "BEHIND":
        gh(["api", "--method", "PUT", f"repos/{repository}/pulls/{number}/update-branch",
            "-f", f"expected_head_sha={head}"], capture=False)
        return "updated"
    if state == "DIRTY":
        raise Error("Homebrew pin PR conflicts with main")
    if state != "CLEAN":
        return "waiting"
    conclusion = _tests_conclusion(pr)
    if conclusion == "SUCCESS":
        gh(["pr", "merge", str(number), "--repo", repository, "--squash",
            "--match-head-commit", head], capture=False)
        return "merged"
    if conclusion in FAILED_CONCLUSIONS:
        return "blocked"
    return "waiting"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--reconcile", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.reconcile:
            raise Error("Use --reconcile from the trusted main workflow")
        if (os.environ.get("GITHUB_ACTIONS") != "true" or
                os.environ.get("GITHUB_REPOSITORY") != args.repository or
                os.environ.get("GITHUB_REF") != "refs/heads/main" or
                os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted" or
                os.environ.get("RUNNER_OS") != "Linux"):
            raise Error("Pin merge is restricted to the main-branch GitHub-hosted Linux job")
        print(f"Homebrew pin: {reconcile_pin(args.repository)}")
        return 0
    except (Error, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"intelbrew pin-pr: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
