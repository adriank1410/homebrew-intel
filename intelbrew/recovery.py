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
    expected = f"verify ({root})"
    return any(job.get("name") == expected and
               job.get("status") == "completed" and
               job.get("conclusion") == "success" for job in jobs)


def _publication_attempt(jobs: list[dict[str, Any]], root: str,
                         source_attempt: int) -> str:
    expected = f"publish ({root})"
    matches = [job for job in jobs if job.get("name") == expected]
    if (len(matches) != 1 or matches[0].get("status") != "completed"
            or matches[0].get("conclusion") != "failure"):
        raise Error("Expected exactly one failed publication job for root")
    attempt = _number(matches[0].get("run_attempt"), "publication job attempt")
    if int(attempt) > source_attempt:
        raise Error("Publication job attempt exceeds source run attempt")
    return attempt


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
