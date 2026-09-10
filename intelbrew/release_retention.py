# SPDX-License-Identifier: BSD-2-Clause
"""Select historical Intel releases that are safe to remove."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


RELEASE_RE = re.compile(r"intel-[0-9]+-[0-9]+-(?P<formula>.+)\Z")


def _created_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def release_formula(tag: str) -> str | None:
    match = RELEASE_RE.fullmatch(tag)
    return match.group("formula") if match else None


def referenced_releases(registry_dir: Path) -> set[str]:
    import json

    result: set[str] = set()
    for path in registry_dir.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        release = value.get("release") if isinstance(value, dict) else None
        if isinstance(release, str):
            result.add(release)
    return result


def active_release_tags(prs: Iterable[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for pr in prs:
        branch = pr.get("headRefName") if isinstance(pr, dict) else None
        if isinstance(branch, str) and branch.startswith("bottles/"):
            result.add(branch.removeprefix("bottles/"))
    return result


def select_releases(
    releases: Iterable[dict[str, Any]],
    *,
    referenced: set[str],
    active: set[str],
    now: datetime,
    published_days: int = 30,
    draft_days: int = 7,
    keep_unreferenced_per_formula: int = 1,
) -> list[dict[str, Any]]:
    """Return only old, unreferenced, inactive releases safe for deletion.

    The newest unreferenced release for each formula is retained as a rollback
    cushion even after it exceeds the age threshold.
    """
    candidates: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for release in releases:
        tag = release.get("tagName") if isinstance(release, dict) else None
        created = release.get("createdAt") if isinstance(release, dict) else None
        if not isinstance(tag, str) or not isinstance(created, str):
            continue
        if tag in referenced or tag in active:
            continue
        formula = release_formula(tag)
        if formula is None:
            continue
        grouped.setdefault(formula, []).append(release)
    for formula_releases in grouped.values():
        formula_releases.sort(key=lambda item: item["createdAt"], reverse=True)
        for release in formula_releases[keep_unreferenced_per_formula:]:
            age_days = (now - _created_at(release["createdAt"]).astimezone(timezone.utc)).days
            threshold = draft_days if release.get("isDraft") else published_days
            if age_days >= threshold:
                candidates.append(release)
    return sorted(candidates, key=lambda item: item["createdAt"])
