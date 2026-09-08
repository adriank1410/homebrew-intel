#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Select completed per-root artifacts from the current GitHub Actions run."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


class SelectionError(ValueError):
    """The run metadata cannot be safely mapped to the planned roots."""


def select_artifact_names(planned_roots: object, payload: object, prefix: str) -> list[str]:
    """Return exact, non-expired artifacts for planned roots in stable order."""
    if not isinstance(planned_roots, list) or not planned_roots or any(
        not isinstance(root, str) or not root for root in planned_roots
    ) or len(set(planned_roots)) != len(planned_roots):
        raise SelectionError("planned roots must be a non-empty list of unique names")
    if not isinstance(prefix, str) or not prefix or "/" in prefix or "-" in prefix:
        raise SelectionError("invalid artifact prefix")
    if not isinstance(payload, list):
        raise SelectionError("artifact metadata must be a list")

    expected = {f"{prefix}-{root}" for root in planned_roots}
    found: dict[str, bool] = {}
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise SelectionError("artifact metadata contains an invalid item")
        name = item["name"]
        if not name.startswith(prefix + "-"):
            continue
        if name not in expected:
            raise SelectionError(f"unexpected {prefix} artifact: {name}")
        if name in found:
            raise SelectionError(f"duplicate artifact: {name}")
        if item.get("expired") is True:
            continue
        if item.get("expired") is not False:
            raise SelectionError(f"invalid expiration state for artifact: {name}")
        found[name] = True
    return [name for name in sorted(expected) if name in found]


def read_run_artifacts() -> list[dict[str, object]]:
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not repository or not run_id or not run_id.isdigit():
        raise SelectionError("GitHub run identity is unavailable")
    try:
        result = subprocess.run(
            [
                "gh", "api", "--paginate", "--slurp",
                f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100",
            ],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SelectionError(f"cannot read current run artifacts: {exc}") from exc
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SelectionError("gh returned invalid JSON") from exc
    if not isinstance(data, list):
        raise SelectionError("gh response has no artifact pages")
    artifacts: list[dict[str, object]] = []
    for page in data:
        if not isinstance(page, dict) or not isinstance(page.get("artifacts"), list):
            raise SelectionError("gh response has an invalid artifact page")
        artifacts.extend(page["artifacts"])
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--planned-roots", required=True)
    args = parser.parse_args()
    try:
        planned = json.loads(args.planned_roots)
        if isinstance(planned, dict):
            planned = planned.get("root")
        names = select_artifact_names(planned, read_run_artifacts(), args.prefix)
        output = {"root": [name.removeprefix(args.prefix + "-") for name in names]}
        output_path = os.environ.get("GITHUB_OUTPUT")
        if not output_path:
            raise SelectionError("GITHUB_OUTPUT is unavailable")
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write("matrix=" + json.dumps(output, separators=(",", ":")) + "\n")
            handle.write(f"has_artifacts={str(bool(names)).lower()}\n")
        print(json.dumps(output, separators=(",", ":")))
        return 0
    except (SelectionError, json.JSONDecodeError) as exc:
        print(f"select artifacts: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
