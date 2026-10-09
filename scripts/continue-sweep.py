#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Dispatch the next full bottle sweep after this run's registry pulls merge."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelbrew.core import Error
from intelbrew.recovery import _attempt_jobs
from intelbrew.registry_pr import gh, gh_json
from intelbrew.sweep_continue import dispatch_arguments, pulls_endpoint, run_continuation


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")

    def fetch_jobs():
        return _attempt_jobs(repository, run_id)

    def fetch_pulls(branch):
        return gh_json(["api", pulls_endpoint(repository, branch)])

    def dispatch(wave):
        gh(dispatch_arguments(repository, wave), capture=False)

    try:
        return run_continuation(
            os.environ,
            fetch_jobs=fetch_jobs,
            fetch_pulls=fetch_pulls,
            dispatch=dispatch,
            sleep=time.sleep,
            clock=time.monotonic,
        )
    except (Error, OSError, TypeError, ValueError, KeyError) as exc:
        print(f"Sweep continuation stopped: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
