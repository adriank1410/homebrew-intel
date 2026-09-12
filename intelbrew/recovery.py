# SPDX-License-Identifier: BSD-2-Clause
"""Validate an already-verified candidate against its completed source run."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .ci import validate_candidate
from .cli import attest
from .core import Error, canonical_name, load_config, require_sha
from .registry_pr import gh_json


WORKFLOW_PATH = ".github/workflows/bottles.yml"
MAX_RECOVERY_ROOTS = 24
MAX_AUTOMATIC_RECOVERY_ROOTS = 256


def _number(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise Error(f"Invalid {label}")
    text = str(value) if isinstance(value, int) else value
    if (not isinstance(text, str) or not text.isascii() or not text.isdigit()
            or int(text) <= 0):
        raise Error(f"Invalid {label}")
    return text


def _jobs(payload: Any) -> list[dict[str, Any]]:
    pages = payload if isinstance(payload, list) else [payload]
    result: list[dict[str, Any]] = []
    for page in pages:
        if isinstance(page, list):
            result.extend(_jobs(page))
            continue
        if not isinstance(page, dict) or not isinstance(page.get("jobs"), list):
            raise Error("GitHub returned an invalid workflow job list")
        if any(not isinstance(item, dict) for item in page["jobs"]):
            raise Error("GitHub returned an invalid workflow job")
        result.extend(page["jobs"])
    return result


def _attempt_jobs(repository: str, run_id: str, attempt: int | None = None) -> list[dict[str, Any]]:
    if attempt is None:
        endpoint = f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"
    else:
        endpoint = f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    return _jobs(gh_json(["api", "--paginate", "--slurp", endpoint]))


def _has_successful_root(jobs: list[dict[str, Any]], root: str) -> bool:
    return any(_job_root(job.get("name"), "verify") == root and
               job.get("status") == "completed" and
               job.get("conclusion") == "success" for job in jobs)


def _job_root(name: Any, stage: str) -> str | None:
    """Return a root only for a known legacy or reusable-workflow job name."""
    if not isinstance(name, str):
        return None
    legacy_prefix = f"{stage} ("
    if name.startswith(legacy_prefix) and name.endswith(")"):
        try:
            return canonical_name(name[len(legacy_prefix):-1])
        except Error:
            return None
    prefix = "root-pipeline ("
    marker = f") / {stage} ("
    if not name.startswith(prefix) or marker not in name or not name.endswith(")"):
        return None
    pipeline_root, stage_root = name[len(prefix):].split(marker, 1)
    if not stage_root.endswith(")"):
        return None
    try:
        pipeline_root = canonical_name(pipeline_root)
        stage_root = canonical_name(stage_root[:-1])
    except Error:
        return None
    return stage_root if pipeline_root == stage_root else None


def _publication_attempt(jobs: list[dict[str, Any]], root: str,
                         source_attempt: int) -> str:
    matches = [job for job in jobs if _job_root(job.get("name"), "publish") == root]
    if (len(matches) != 1 or matches[0].get("status") != "completed"
            or matches[0].get("conclusion") != "failure"):
        raise Error("Expected exactly one failed publication job for root")
    attempt = _number(matches[0].get("run_attempt"), "publication job attempt")
    if int(attempt) > source_attempt:
        raise Error("Publication job attempt exceeds source run attempt")
    return attempt


def _job_roots(jobs: list[dict[str, Any]], stage: str, conclusion: str) -> set[str]:
    roots: set[str] = set()
    for job in jobs:
        if (job.get("status") != "completed"
                or job.get("conclusion") != conclusion):
            continue
        root = _job_root(job.get("name"), stage)
        if root is not None:
            roots.add(root)
    return roots


def _artifact_roots(payload: Any) -> set[str]:
    pages = payload if isinstance(payload, list) else [payload]
    roots: set[str] = set()
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("artifacts"), list):
            raise Error("GitHub returned an invalid workflow artifact list")
        for artifact in page["artifacts"]:
            if (not isinstance(artifact, dict) or artifact.get("expired") is not False
                    or not isinstance(artifact.get("name"), str)
                    or not artifact["name"].startswith("verified-")):
                continue
            try:
                roots.add(canonical_name(artifact["name"].removeprefix("verified-")))
            except Error:
                continue
    return roots


def discover_recovery_roots(repository: str, source_run: str | int) -> list[str]:
    """Find failed publication roots with retained verified artifacts.

    This is a read-only discovery pass for the ``workflow_run`` trigger.  The
    per-root validator remains authoritative before any release is created.
    """
    run_id = _number(source_run, "source run id")
    if repository != load_config()["repository"]:
        raise Error("Recovery repository mismatch")
    run = gh_json(["api", f"repos/{repository}/actions/runs/{run_id}"])
    if not isinstance(run, dict) or _number(run.get("id"), "workflow run id") != run_id:
        raise Error("Workflow run id mismatch")
    if run.get("path") != WORKFLOW_PATH or run.get("head_branch") != "main":
        raise Error("Workflow run is outside the main bottles workflow")
    head_repository = run.get("head_repository")
    if not isinstance(head_repository, dict) or head_repository.get("full_name") != repository:
        raise Error("Workflow run is from another repository")
    if str(run.get("status", "")).lower() != "completed":
        raise Error("Workflow run is not completed")
    if str(run.get("conclusion", "")).lower() != "failure":
        raise Error("Workflow run has no failed publication to recover")
    source_attempt = int(_number(run.get("run_attempt"), "workflow run attempt"))
    jobs = _attempt_jobs(repository, run_id)
    failed = _job_roots(jobs, "publish", "failure")
    if len(failed) > MAX_AUTOMATIC_RECOVERY_ROOTS:
        raise Error("Recovery root count exceeds bound")
    for root in failed:
        _publication_attempt(jobs, root, source_attempt)
    if not failed:
        return []
    verified = _job_roots(jobs, "verify", "success")
    pending = failed - verified
    for previous in range(source_attempt - 1, 0, -1):
        if not pending:
            break
        verified.update(_job_roots(_attempt_jobs(repository, run_id, previous), "verify", "success"))
        pending = failed - verified
    if not verified.intersection(failed):
        return []
    artifacts = gh_json(["api", "--paginate", "--slurp",
                         f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100"])
    roots = sorted(failed & verified & _artifact_roots(artifacts))
    if len(roots) > MAX_AUTOMATIC_RECOVERY_ROOTS:
        raise Error("Recovery root count exceeds bound")
    return roots


def validate_recovery(candidate: Path, root: str, source_run: str | int,
                     manifest: dict[str, Any], repository: str) -> tuple[str, str, str, str]:
    """Bind a verified artifact to its completed source run and root verification.

    The source run and its attempt are read from GitHub's API.  The recovery
    runner's ``GITHUB_*`` environment is deliberately not consulted.
    """
    root = canonical_name(root)
    run_id = _number(source_run, "source run id")
    if repository != load_config()["repository"]:
        raise Error("Recovery repository mismatch")
    token = os.environ.get("INTELBREW_ATTESTATION_TOKEN")
    if not isinstance(token, str) or not token:
        raise Error("Recovery requires a dedicated attestation token")
    checked = validate_candidate(candidate, expected_root=root, verified=True)
    if checked != manifest:
        raise Error("Recovery manifest does not match candidate")
    workflow_commit = require_sha(checked["workflow_commit"], git=True)
    core_commit = require_sha(checked["core_commit"], git=True)
    packages = checked.get("packages")
    if not isinstance(packages, list):
        raise Error("Recovery manifest has no package list")
    for package in packages:
        if not isinstance(package, dict) or package.get("run_id") != run_id:
            raise Error("Package run id differs from source run")

    run = gh_json(["api", f"repos/{repository}/actions/runs/{run_id}"])
    if not isinstance(run, dict) or _number(run.get("id"), "workflow run id") != run_id:
        raise Error("Workflow run id mismatch")
    if run.get("path") != WORKFLOW_PATH:
        raise Error("Workflow run has an unexpected workflow path")
    if run.get("head_branch") != "main":
        raise Error("Workflow run is not from the main branch")
    head_repository = run.get("head_repository")
    if not isinstance(head_repository, dict) or head_repository.get("full_name") != repository:
        raise Error("Workflow run is from another repository")
    if run.get("head_sha") != workflow_commit:
        raise Error("Workflow run workflow commit differs from manifest")
    if str(run.get("status", "")).lower() != "completed":
        raise Error("Workflow run is not completed")
    if str(run.get("conclusion", "")).lower() not in {"success", "failure"}:
        raise Error("Workflow run has an unsupported conclusion")
    attempt_text = _number(run.get("run_attempt"), "workflow run attempt")
    attempt = int(attempt_text)

    jobs = _attempt_jobs(repository, run_id)
    publication_attempt = _publication_attempt(jobs, root, attempt)
    if not _has_successful_root(jobs, root):
        for previous in range(attempt - 1, 0, -1):
            if _has_successful_root(_attempt_jobs(repository, run_id, previous), root):
                break
        else:
            raise Error("No successful verification job for root")

    for path in sorted(candidate.iterdir(), key=lambda item: item.name):
        attest(path, repository, workflow_commit, token=token)
    return workflow_commit, core_commit, run_id, publication_attempt
