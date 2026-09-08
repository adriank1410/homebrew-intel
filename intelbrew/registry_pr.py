# SPDX-License-Identifier: BSD-2-Clause
"""Create and reconcile the narrowly-scoped automated registry pull request."""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from .cli import attest
from .core import (MAX_JSON, ROOT, Error,
                   download, load_config, read_json, require_sha, run,
                   canonical_name, validate_record)

BOT = "github-actions[bot]"
BRANCH_RE = re.compile(r"bottles/intel-[0-9]+-[0-9]+-[a-z0-9_.+@-]+\Z")
MAIN = "main"


def gh(arguments: list[str], *, capture: bool = True) -> str:
    """Run gh with no credential helper or shell interpolation."""
    return run(["gh", *arguments], capture=capture, cwd=ROOT)


def gh_json(arguments: list[str]) -> Any:
    try:
        return json.loads(gh(arguments))
    except (ValueError, TypeError) as exc:
        raise Error("GitHub CLI returned invalid JSON") from exc


def allowed_pr(pr: dict[str, Any], *, repository: str) -> bool:
    """Return whether a PR is exactly the bot-owned registry surface."""
    head = pr.get("headRepository") or {}
    base = pr.get("baseRepository") or {}
    author = pr.get("author") or {}
    head_repo = head.get("nameWithOwner") or pr.get("headRepositoryNameWithOwner")
    base_repo = (base.get("nameWithOwner") or pr.get("baseRepositoryNameWithOwner") or
                 (repository if not pr.get("isCrossRepository", False) else None))
    return (pr.get("state", "OPEN") == "OPEN" and
            pr.get("baseRefName") == MAIN and
            pr.get("headRefName", "") and BRANCH_RE.fullmatch(pr["headRefName"]) and
            head_repo == repository and base_repo == repository and
            not pr.get("isCrossRepository", False) and
            author.get("login") == BOT)


def changed_registry_files(pr: dict[str, Any]) -> list[str]:
    """Validate the PR file list and return its canonical registry paths."""
    files = pr.get("files")
    if not isinstance(files, list) or not files:
        raise Error("Registry PR has no file list")
    paths: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            raise Error("Registry PR contains an invalid file entry")
        change = item.get("status", item.get("changeType", "")).lower()
        if change not in {"added", "modified"}:
            raise Error("Registry PR contains a deletion or unsupported file change")
        path = item.get("path")
        if not isinstance(path, str) or not re.fullmatch(r"registry/[a-z0-9][a-z0-9+_.-]*(?:@[0-9][a-z0-9+_.-]*)?\.json", path):
            raise Error("Registry PR changes a file outside registry/*.json")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise Error("Registry PR file list contains duplicates")
    return paths


def _record_from_content(payload: dict[str, Any], path: str) -> dict[str, Any]:
    if payload.get("type") != "file" or not isinstance(payload.get("content"), str):
        raise Error(f"Registry path is not a regular file: {path}")
    try:
        encoded = b"".join(payload["content"].encode("ascii").split())
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_JSON:
            raise Error("Registry JSON exceeds size limit")
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise Error(f"Invalid registry JSON: {path}") from exc
    if not isinstance(value, dict):
        raise Error(f"Registry JSON is not an object: {path}")
    validate_record(value, published=True)
    if path != f"registry/{value['name']}.json":
        raise Error(f"Registry filename does not match record name: {path}")
    return value


def _manifest_asset(repository: str, release: str) -> tuple[str, int, str]:
    data = gh_json(["api", f"repos/{repository}/releases/tags/{release}"])
    assets = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(assets, list):
        raise Error("Release has no bounded asset list")
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == "manifest.json"]
    if len(matches) != 1:
        raise Error("Release must contain exactly one manifest.json")
    asset = matches[0]
    # The API ``url`` returns asset metadata, whereas the browser URL is the
    # immutable release payload consumed by the bounded downloader.
    url = asset.get("browser_download_url")
    digest = asset.get("digest")
    size = asset.get("size")
    if not isinstance(url, str) or not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise Error("Release manifest lacks an immutable SHA-256 asset digest")
    if not isinstance(size, int) or not 0 < size <= MAX_JSON:
        raise Error("Release manifest exceeds the bounded size limit")
    return url, size, require_sha(digest.removeprefix("sha256:"))


def _download_manifest(repository: str, release: str, directory: Path) -> dict[str, Any]:
    url, size, digest = _manifest_asset(repository, release)
    path = directory / "manifest.json"
    download(url, path, digest, size)
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise Error("Release manifest is not an object")
    return manifest


def validate_manifest_records(manifest: dict[str, Any], records: list[dict[str, Any]], *,
                              release: str) -> None:
    """Bind PR records to the signed release manifest, ignoring only release."""
    if manifest.get("schema") != 1 or manifest.get("verified") is not True:
        raise Error("Release manifest is not a verified candidate manifest")
    canonical_name(manifest.get("root"))
    for field in ("core_commit", "brew_commit", "workflow_commit"):
        require_sha(manifest.get(field), git=True)
    if manifest.get("release") not in (None, release):
        raise Error("Manifest release identity mismatch")
    packages = manifest.get("packages")
    if not isinstance(packages, list) or not packages:
        raise Error("Manifest has no package records")
    normalized_manifest: dict[str, dict[str, Any]] = {}
    for package in packages:
        if not isinstance(package, dict):
            raise Error("Manifest contains an invalid package record")
        item = copy.deepcopy(package)
        item["release"] = None
        validate_record(item, published=False)
        if item["name"] in normalized_manifest:
            raise Error("Manifest contains duplicate package records")
        normalized_manifest[item["name"]] = item
    normalized_records: dict[str, dict[str, Any]] = {}
    for record in records:
        validate_record(record, published=True)
        item = copy.deepcopy(record)
        if item.get("release") != release:
            raise Error("Registry record release does not match branch release")
        item["release"] = None
        normalized_records[item["name"]] = item
    if normalized_records != normalized_manifest:
        raise Error("Registry records do not match the immutable release manifest")


def _workflow_run_exists(repository: str, head_sha: str) -> bool:
    data = gh_json(["api", f"repos/{repository}/actions/runs?head_sha={head_sha}&event=workflow_dispatch&per_page=100"])
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    if not isinstance(runs, list):
        raise Error("GitHub returned an invalid workflow run list")
    for item in runs:
        if not isinstance(item, dict) or item.get("head_sha") != head_sha:
            continue
        path = item.get("path", "")
        name = item.get("name", "")
        if str(path).endswith(".github/workflows/checks.yml") or name == "Checks":
            return True
    return False


def _dispatch_checks(repository: str, branch: str, pr: dict[str, Any]) -> None:
    head_sha = pr.get("headRefOid")
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise Error("PR has no valid head commit")
    if not _workflow_run_exists(repository, head_sha):
        gh(["workflow", "run", "checks.yml", "--repo", repository, "--ref", branch], capture=False)


def _enable_auto_merge(repository: str, pr: dict[str, Any]) -> None:
    if pr.get("isAutoMergeEnabled") or pr.get("autoMergeRequest"):
        return
    number = pr.get("number")
    if not isinstance(number, int):
        raise Error("PR has no number")
    head = pr.get("headRefOid")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise Error("PR has no valid head commit")
    gh(["pr", "merge", str(number), "--repo", repository, "--auto", "--squash",
        "--match-head-commit", head], capture=False)


def _fetch_pr_files(repository: str, pr: dict[str, Any]) -> list[dict[str, Any]]:
    paths = changed_registry_files(pr)
    sha = pr.get("headRefOid")
    if not isinstance(sha, str):
        raise Error("PR has no head commit")
    records = []
    for path in paths:
        payload = gh_json(["api", "--method", "GET", f"repos/{repository}/contents/{path}",
                           "-f", f"ref={sha}"])
        records.append(_record_from_content(payload, path))
    return records


def validate_pr(repository: str, pr: dict[str, Any]) -> None:
    if not allowed_pr(pr, repository=repository):
        raise Error("Refusing a PR outside the bot-owned same-repository registry surface")
    release = pr["headRefName"].removeprefix("bottles/")
    records = _fetch_pr_files(repository, pr)
    with tempfile.TemporaryDirectory(prefix="intelbrew-manifest-") as temp:
        manifest = _download_manifest(repository, release, Path(temp))
        workflow_commit = manifest.get("workflow_commit")
        require_sha(workflow_commit, git=True)
        attest(Path(temp) / "manifest.json", repository, workflow_commit)
        validate_manifest_records(manifest, records, release=release)


def _find_pr(repository: str, branch: str) -> dict[str, Any] | None:
    prs = gh_json(["pr", "list", "--repo", repository, "--limit", "100", "--state", "open", "--head", branch,
                   "--base", MAIN, "--json",
                   "number,state,author,headRefName,headRefOid,baseRefName,headRepository,"
                   "isCrossRepository,files,autoMergeRequest,mergeStateStatus"])
    if not isinstance(prs, list):
        raise Error("GitHub CLI returned an invalid PR list")
    if len(prs) > 1:
        raise Error("Multiple open registry PRs exist for one branch")
    return prs[0] if prs else None


def ensure_pr(repository: str, branch: str, release: str, *, title: str | None = None,
              body: str | None = None) -> dict[str, Any]:
    """Create/find, validate, check, and auto-merge one registry PR."""
    if not BRANCH_RE.fullmatch(branch) or not re.fullmatch(r"intel-[0-9]+-[0-9]+-[a-z0-9_.+@-]+", release):
        raise Error("Invalid registry branch or release")
    pr = _find_pr(repository, branch)
    if pr is None:
        gh(["pr", "create", "--repo", repository, "--base", MAIN, "--head", branch,
            "--title", title or f"Publish Intel bottles: {release}",
            "--body", body or "Automated registry update; owner review remains required."], capture=False)
        pr = _find_pr(repository, branch)
        if pr is None:
            raise Error("PR creation succeeded but the open PR was not found")
    validate_pr(repository, pr)
    _dispatch_checks(repository, branch, pr)
    _enable_auto_merge(repository, pr)
    return pr


def reconcile(repository: str) -> None:
    """Revalidate and advance every eligible open bot registry PR."""
    prs = gh_json(["pr", "list", "--repo", repository, "--limit", "100", "--state", "open", "--base", MAIN,
                   "--json", "number,state,author,headRefName,headRefOid,baseRefName,headRepository,"
                   "isCrossRepository,files,"
                   "autoMergeRequest,mergeStateStatus"])
    if not isinstance(prs, list):
        raise Error("GitHub CLI returned an invalid PR list")
    errors: list[str] = []
    for candidate in prs:
        if not isinstance(candidate, dict):
            continue
        branch = candidate.get("headRefName", "")
        if not BRANCH_RE.fullmatch(branch):
            continue
        if not allowed_pr(candidate, repository=repository):
            errors.append(f"PR {candidate.get('number', '?')}: outside the allowed bot surface")
            continue
        try:
            pr = candidate
            if pr.get("mergeStateStatus") == "BEHIND":
                number = pr.get("number")
                head = pr.get("headRefOid")
                if not isinstance(number, int) or not isinstance(head, str):
                    raise Error("Behind registry PR lacks update identity")
                gh(["api", "--method", "PUT", f"repos/{repository}/pulls/{number}/update-branch",
                    "-f", f"expected_head_sha={head}"], capture=False)
                refreshed = _find_pr(repository, branch)
                if refreshed is None:
                    raise Error("Registry PR disappeared during branch update")
                pr = refreshed
                if pr.get("headRefOid") == head:
                    # GitHub may acknowledge update-branch before the PR object
                    # exposes its new head.  The next hourly run will continue.
                    continue
            elif pr.get("mergeStateStatus") in {"DIRTY", "UNKNOWN"}:
                raise Error(f"Registry PR {pr.get('number')} cannot be safely reconciled: {pr.get('mergeStateStatus')}")
            validate_pr(repository, pr)
            _dispatch_checks(repository, branch, pr)
            _enable_auto_merge(repository, pr)
        except Error as exc:
            errors.append(f"PR {candidate.get('number', '?')}: {exc}")
    if errors:
        raise Error("; ".join(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=load_config()["repository"])
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
            raise Error("Reconciliation is restricted to the main-branch GitHub-hosted Linux job")
        reconcile(args.repository)
        return 0
    except (Error, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"intelbrew registry-pr: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
