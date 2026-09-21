#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Resolve and optionally update the reviewed Homebrew engine/core pair."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "policy/config.json"
REMOTE_REFS = (
    ("brew_commit", "https://github.com/Homebrew/brew.git"),
    ("core_commit", "https://github.com/Homebrew/homebrew-core.git"),
)


class Error(Exception):
    """A checked failure that must leave the policy file unchanged."""


def _run(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)


def _resolve(output):
    fields = output.split()
    if (len(fields) != 2 or fields[1] != "refs/heads/main"
            or not re.fullmatch(r"[0-9a-f]{40}", fields[0])):
        raise Error("Cannot resolve a full Homebrew main-branch commit")
    return fields[0]


def resolve_pins(runner=_run):
    """Resolve both upstream refs before the caller writes either pin."""
    resolved = {}
    for key, remote in REMOTE_REFS:
        try:
            output = runner(["git", "ls-remote", remote, "refs/heads/main"])
            resolved[key] = _resolve(output)
        except (OSError, subprocess.CalledProcessError, Error) as exc:
            if isinstance(exc, Error):
                raise
            raise Error(f"Cannot resolve {key} from {remote}: {exc}") from exc
    return resolved


def update_config(path, pins):
    """Write only the paired pins, returning whether the file changed."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise Error(f"Cannot read policy configuration: {exc}") from exc
    if payload.get("schema") != 1 or payload.get("repository") != "adriank1410/homebrew-intel":
        raise Error("Unexpected configuration")
    for key in ("brew_commit", "core_commit"):
        value = pins.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise Error("Invalid Homebrew pin")
        payload[key] = value
    updated = json.dumps(payload, indent=2) + "\n"
    if path.read_text(encoding="utf-8") == updated:
        return False
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise Error(f"Cannot write policy configuration: {exc}") from exc
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="write both resolved commits to policy/config.json")
    args = parser.parse_args(argv)
    pins = resolve_pins()
    changed = update_config(CONFIG_PATH, pins) if args.write else False
    print(json.dumps({"changed": changed, **pins}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Error as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
