# SPDX-License-Identifier: BSD-2-Clause
"""Collect source material downloaded by Go and Cargo during a bottle build."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from .core import Error

MAX_FILES = 100_000
MAX_BYTES = 4 * 1024 * 1024 * 1024


def _regular_files(root: Path, includes: tuple[Path, ...]):
    for base in includes:
        if base.is_symlink():
            raise Error(f"Unsafe package-manager cache path: {base}")
        if not base.exists():
            continue
        if not base.is_dir() or root not in base.resolve().parents:
            raise Error(f"Unsafe package-manager cache path: {base}")
        for directory, names, files in os.walk(base, followlinks=False):
            current = Path(directory)
            symlinks = [name for name in names if (current / name).is_symlink()]
            if symlinks:
                raise Error(f"Unsafe package-manager cache entry: {current / symlinks[0]}")
            names[:] = sorted(name for name in names if name != ".git")
            for name in sorted(files):
                if name == ".git":
                    continue
                path = current / name
                if path.is_symlink() or not path.is_file():
                    raise Error(f"Unsafe package-manager cache entry: {path}")
                yield root, path


def collect_build_sources(context: dict[str, Any], output: Path) -> dict[str, Any]:
    """Copy bounded Go module and Cargo source caches into a deterministic tree."""
    if not isinstance(context, dict):
        raise Error("Invalid build context")
    try:
        cache = Path(context["homebrew_cache"])
        go = Path(context["go_mod_cache"])
        cargo = Path(context["cargo_cache"])
    except (KeyError, TypeError):
        raise Error("Invalid build context") from None
    if not all(path.is_absolute() for path in (cache, go, cargo)):
        raise Error("Build context paths must be absolute")
    cache_real = cache.resolve(strict=True)
    roots = (go.resolve(strict=False), cargo.resolve(strict=False))
    if roots != (cache_real / "go_mod_cache", cache_real / "cargo_cache"):
        raise Error("Package-manager cache escapes Homebrew cache")
    selections = ((roots[0], (roots[0] / "pkg/mod/cache/download",)),
                  (roots[1], (roots[1] / "registry/cache", roots[1] / "git/checkouts")))
    output.mkdir(parents=True, exist_ok=False)
    candidates = []
    for root, includes in selections:
        kind = "go" if root == roots[0] else "cargo"
        for _, source in _regular_files(root, includes):
            relative = source.relative_to(root)
            candidates.append((f"{kind}/{relative.as_posix()}", kind, relative, source))
            if len(candidates) > MAX_FILES:
                raise Error("Package-manager source cache exceeds collection limit")
    records, total = [], 0
    for label, kind, relative, source in sorted(candidates):
            descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            source_handle = os.fdopen(descriptor, "rb")
            source_stat = os.fstat(descriptor)
            if not stat.S_ISREG(source_stat.st_mode):
                source_handle.close()
                raise Error(f"Unsafe package-manager cache entry: {source}")
            size = source_stat.st_size
            total += size
            if len(records) >= MAX_FILES or total > MAX_BYTES:
                source_handle.close()
                raise Error("Package-manager source cache exceeds collection limit")
            destination = output / kind / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            copied = 0
            with source_handle, destination.open("xb") as handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    handle.write(chunk)
                    copied += len(chunk)
                final_stat = os.fstat(source_handle.fileno())
            if copied != size or (final_stat.st_size, final_stat.st_mtime_ns) != (size, source_stat.st_mtime_ns):
                raise Error(f"Package-manager cache entry changed during collection: {source}")
            records.append({"label": label, "path": str(destination),
                            "sha256": digest.hexdigest(), "size": size})
    revisions = sorted({roots[1] / Path(*relative.parts[:4])
                        for _, kind, relative, _ in candidates
                        if kind == "cargo" and relative.parts[:2] == ("git", "checkouts") and
                        len(relative.parts) >= 5})
    for checkout in revisions:
        try:
            commit = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                                    text=True, capture_output=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            raise Error(f"Cannot identify Cargo checkout revision: {checkout}") from None
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise Error(f"Invalid Cargo checkout revision: {checkout}")
        remote_result = subprocess.run(["git", "-C", str(checkout), "remote", "get-url", "origin"],
                                       text=True, capture_output=True)
        remote = remote_result.stdout.strip() if remote_result.returncode == 0 else ""
        metadata = {"commit": commit}
        if len(remote) <= 2048 and re.fullmatch(r"https://[^/@:]+(?:/[^/?#]+)+", remote):
            metadata["origin"] = remote
        relative = checkout.relative_to(roots[1] / "git/checkouts")
        label = f"cargo/git-revisions/{relative.as_posix()}.json"
        destination = output / label
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n").encode()
        total += len(data)
        if len(records) >= MAX_FILES or total > MAX_BYTES:
            raise Error("Package-manager source cache exceeds collection limit")
        destination.write_bytes(data)
        records.append({"label": label, "path": str(destination),
                        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    records.sort(key=lambda item: item["label"])
    return {"schema": 1, "files": records}
