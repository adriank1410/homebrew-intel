#!/usr/bin/python3
# SPDX-License-Identifier: BSD-2-Clause
"""Move conflicting Apple/GitHub Python framework links on ephemeral runners."""
import os
import sys
from pathlib import Path


class Error(RuntimeError):
    pass


def require_ci_runner():
    expected = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "macOS",
        "RUNNER_ARCH": "X64",
    }
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise Error("Refusing link quarantine outside GitHub-hosted macOS Intel")


def quarantine_links(bin_dir, backup_dir, *, framework_root=Path(
        "/Library/Frameworks/Python.framework")):
    bin_dir = Path(bin_dir)
    backup_dir = Path(backup_dir)
    framework_root = Path(os.path.abspath(str(framework_root)))
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise Error("Invalid runner backup directory")
    destination = backup_dir / "framework-python-links"
    if destination.is_symlink():
        raise Error("Invalid framework link backup directory")

    candidates = []
    for entry in sorted(bin_dir.iterdir(), key=lambda item: item.name):
        if not entry.is_symlink():
            continue
        raw_target = os.readlink(entry)
        target = Path(raw_target)
        if not target.is_absolute():
            target = bin_dir / target
        target = Path(os.path.abspath(str(target)))
        try:
            relative = target.relative_to(framework_root)
        except ValueError:
            continue
        parts = relative.parts
        if len(parts) != 4 or parts[0] != "Versions" or parts[2] != "bin":
            continue
        candidates.append(entry)

    for entry in candidates:
        target = destination / entry.name
        if target.exists() or target.is_symlink():
            raise Error(f"Framework link backup destination exists: {entry.name}")
    if candidates:
        destination.mkdir()
        for entry in candidates:
            os.rename(entry, destination / entry.name)
    return [entry.name for entry in candidates]


def main():
    try:
        require_ci_runner()
        if len(sys.argv) != 2:
            raise Error("Expected runner backup directory")
        moved = quarantine_links(Path("/usr/local/bin"), Path(sys.argv[1]))
        print("Framework Python links retained: " + (", ".join(moved) if moved else "none"))
        return 0
    except (Error, OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
