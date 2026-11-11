# SPDX-License-Identifier: BSD-2-Clause
"""Start the next bottle wave after this sweep's registry pulls merge.

A scheduled run and a manual ``formula=all`` run publish one dependency layer.
Waiting for the next GitHub cron leaves that layer idle for hours, because
scheduled events are dropped. This module dispatches the following full sweep
only after at least one published pull from this run is on main and none
are still open or missing. A pull closed without merging does not block the
next wave. The module stops when the run published nothing or the wave cap is
reached. One-root repairs do not continue into a full sweep.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode

from .core import Error, load_config
from .publish import release_tag
from .recovery import _job_root


MAX_SWEEP_WAVES = 8
MAX_POLLS = 300
PUBLISH_STEP = "Publish immutable release and attested registry PR"
_WAITING = {"missing", "open", "unknown"}


def _published_jobs(jobs: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(root, job)`` for each publish job that created a registry pull."""
    published: list[tuple[str, dict[str, Any]]] = []
    for job in jobs:
        if not isinstance(job, dict) or job.get("status") != "completed":
            continue
        name = job.get("name")
        if not isinstance(name, str) or not name.startswith("root-pipeline ("):
            continue
        root = _job_root(name, "publish")
        if root is None:
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        if any(isinstance(step, dict) and step.get("name") == PUBLISH_STEP
               and step.get("conclusion") == "success" for step in steps):
            published.append((root, job))
    return published


def published_roots(jobs: list[Any]) -> list[str]:
    """Return roots whose publish step created a registry pull."""
    return sorted({root for root, _job in _published_jobs(jobs)})


def publication_attempts(jobs: list[Any], current: str) -> dict[str, str]:
    """Map each published root to the run attempt that published it.

    After "Re-run failed jobs" the latest job list still holds the publish
    jobs that succeeded in an earlier attempt. Their pulls are on branches
    named for that attempt, not for the attempt running this continuation.
    A job without a usable attempt keeps ``current``.
    """
    limit = _digits(current, maximum=1000, minimum=1)
    attempts: dict[str, str] = {}
    for root, job in _published_jobs(jobs):
        value = job.get("run_attempt")
        if limit is None or type(value) is not int or not 1 <= value <= limit:
            value = current
        if root not in attempts or str(value) == current:
            attempts[root] = str(value)
    return attempts


def branch_for(root: str, run_id: str, attempt: str) -> str:
    return "bottles/" + release_tag(root, run_id, attempt)


def pulls_endpoint(repository: str, branch: str) -> str:
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name or "/" in name or not branch.startswith("bottles/"):
        raise Error("Invalid continuation pull query")
    query = urlencode({"head": f"{owner}:{branch}", "state": "all", "per_page": "20"})
    return f"repos/{repository}/pulls?{query}"


def pull_status(payload: Any, *, repository: str, branch: str) -> str:
    """Classify this repository's pull for one publication branch."""
    if not isinstance(payload, list):
        return "unknown"
    found: list[str] = []
    for pull in payload:
        if not isinstance(pull, dict):
            return "unknown"
        head = pull.get("head")
        if not isinstance(head, dict) or head.get("ref") != branch:
            continue
        repo = head.get("repo")
        if not isinstance(repo, dict) or repo.get("full_name") != repository:
            continue
        merged_at = pull.get("merged_at")
        if isinstance(merged_at, str) and merged_at:
            found.append("merged")
        elif pull.get("state") == "open":
            found.append("open")
        elif pull.get("state") == "closed":
            found.append("closed")
        else:
            return "unknown"
    if "open" in found:
        return "open"
    if "merged" in found:
        return "merged"
    if "closed" in found:
        return "closed"
    return "missing"


def _digits(value: Any, *, maximum: int, minimum: int = 0) -> int | None:
    if (not isinstance(value, str) or not value.isascii() or not value.isdigit()
            or (len(value) > 1 and value.startswith("0")) or len(value) > 18):
        return None
    number = int(value)
    if number < minimum or number > maximum:
        return None
    return number


def _bounded(value: Any, *, default: int, low: int, high: int) -> int:
    parsed = _digits(value, maximum=10**9)
    if parsed is None:
        return default
    return min(max(parsed, low), high)


def chainable(event: str, formula: str, wave: int | None) -> bool:
    if type(wave) is not int:
        return False
    if event == "schedule":
        full_sweep = formula in {"all", ""}
    elif event == "workflow_dispatch":
        full_sweep = formula == "all"
    else:
        full_sweep = False
    return full_sweep and 1 <= wave < MAX_SWEEP_WAVES


def continuation_decision(event: str, formula: str, wave: int, roots: list[str],
                          statuses: Mapping[str, str]) -> str:
    """Return ``dispatch``, ``wait``, or ``stop`` for one poll."""
    if not chainable(event, formula, wave) or not roots:
        return "stop"
    if any(statuses.get(root) in _WAITING for root in roots):
        return "wait"
    if any(statuses.get(root) == "merged" for root in roots):
        return "dispatch"
    return "stop"


def dispatch_arguments(repository: str, wave: int) -> list[str]:
    if repository != load_config()["repository"] or type(wave) is not int or not 1 <= wave <= MAX_SWEEP_WAVES:
        raise Error("Continuation dispatch is outside the reviewed sweep chain")
    return [
        "workflow", "run", "bottles.yml", "--repo", repository, "--ref", "main",
        "-f", "formula=all", "-f", f"sweep_wave={wave}",
    ]


def run_continuation(
    env: Mapping[str, str],
    *,
    fetch_jobs: Callable[[], list[Any]],
    fetch_pulls: Callable[[str], Any],
    dispatch: Callable[[int], None],
    sleep: Callable[[int], None],
    clock: Callable[[], float],
) -> int:
    """Poll until this run's bottle pulls merge, then dispatch the next wave."""
    event = env.get("GITHUB_EVENT_NAME", "")
    formula = env.get("REQUESTED_FORMULA", "")
    wave = _digits(env.get("INTELBREW_SWEEP_WAVE", ""), maximum=100)
    repository = env.get("GITHUB_REPOSITORY", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "")
    timeout = _bounded(env.get("INTELBREW_CONTINUE_TIMEOUT"), default=1200, low=0, high=1200)
    interval = _bounded(env.get("INTELBREW_CONTINUE_INTERVAL"), default=20, low=5, high=60)
    if repository != load_config()["repository"] or not chainable(event, formula, wave):
        print("Sweep continuation stopped: this run does not start another wave.")
        return 0
    if (_digits(run_id, maximum=10**18, minimum=1) is None
            or _digits(attempt, maximum=1000, minimum=1) is None):
        print("Sweep continuation stopped: the run identity is unusable.")
        return 0
    deadline = clock() + timeout
    roots: list[str] | None = None
    attempts: dict[str, str] = {}
    for _poll in range(MAX_POLLS):
        try:
            jobs = fetch_jobs()
            roots = published_roots(jobs)
            attempts = publication_attempts(jobs, attempt)
            break
        except (Error, OSError, TypeError, ValueError, KeyError) as exc:
            print(f"Sweep continuation could not read jobs: {exc}")
            if clock() >= deadline:
                break
            sleep(interval)
    if roots is None:
        print("Sweep continuation stopped: the job list stayed unavailable.")
        return 0
    if not roots:
        print("Sweep continuation stopped: no bottle registry pull was published.")
        return 0
    # A merged pull stays merged, so it is not requested again. One request per
    # root per poll would be 24 roots x 61 polls = 1464 requests in 20 minutes
    # on the workflow token, which registry maintenance also uses.
    merged: set[str] = set()
    for _poll in range(MAX_POLLS):
        statuses: dict[str, str] = {root: "merged" for root in merged}
        try:
            for root in roots:
                if root in merged:
                    continue
                branch = branch_for(root, run_id, attempts.get(root, attempt))
                statuses[root] = pull_status(
                    fetch_pulls(branch), repository=repository, branch=branch)
                if statuses[root] == "merged":
                    merged.add(root)
        except (Error, OSError, TypeError, ValueError, KeyError) as exc:
            print(f"Sweep continuation could not read pulls: {exc}")
            statuses = {root: "merged" if root in merged else "unknown" for root in roots}
        decision = continuation_decision(event, formula, wave, roots, statuses)
        if decision == "dispatch":
            try:
                dispatch(wave + 1)
            except (Error, OSError, TypeError, ValueError, KeyError) as exc:
                print(f"Sweep continuation could not start the next wave: {exc}")
            else:
                print(f"Sweep continuation dispatched wave {wave + 1}.")
            return 0
        if decision == "stop":
            print("Sweep continuation stopped: published pulls closed without merging.")
            return 0
        if clock() >= deadline:
            print("Sweep continuation stopped: registry pulls were not merged before the deadline.")
            return 0
        print("Sweep continuation waiting for registry pulls to merge.")
        sleep(interval)
    print("Sweep continuation stopped: polling limit reached.")
    return 0
