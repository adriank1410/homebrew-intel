# SPDX-License-Identifier: BSD-2-Clause
"""Temporary, auditable recipe variants used for narrowly scoped CI builds."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from .core import Error, LLVM_NO_PGO


_LLVM_CLASS = re.compile(rb"(?m)^class Llvm < Formula$")
_LLVM_PGO_LINE = (
    b"    pgo_build = build.stable? && build.bottle? && OS.mac? && !versioned_formula?\n"
)
_LLVM_NO_PGO_LINES = (
    b"    # Intelbrew LLVM no-PGO profile disables the expensive PGO bootstrap.\n"
    b"    pgo_build = false\n"
)
_BOTTLE_BLOCK = re.compile(rb"  bottle do.+?end\n\n?", re.DOTALL)


def variant_recipe(upstream: bytes) -> bytes:
    """Disable only LLVM's PGO gate, refusing unfamiliar upstream recipes."""
    if not isinstance(upstream, bytes):
        raise Error("LLVM profile recipe must be bytes")
    if len(_LLVM_CLASS.findall(upstream)) != 1:
        raise Error("Unexpected LLVM formula class")
    if upstream.count(_LLVM_PGO_LINE) != 1:
        raise Error("Unexpected or ambiguous LLVM PGO recipe line")
    return upstream.replace(_LLVM_PGO_LINE, _LLVM_NO_PGO_LINES, 1)


def recipe_sha256(text: bytes) -> str:
    """Hash the recipe using the bottle-block exclusion used by native.rb."""
    if not isinstance(text, bytes):
        raise Error("Recipe must be bytes")
    return hashlib.sha256(_BOTTLE_BLOCK.sub(b"", text)).hexdigest()


def _read_regular_recipe(path: Path) -> tuple[bytes, int]:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise Error("LLVM source recipe must be a regular, non-symlink file")
        stream = os.fdopen(descriptor, "rb")
        descriptor = -1
        with stream:
            contents = stream.read()
        path_info = path.lstat()
    except OSError as exc:
        raise Error("LLVM source recipe is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (stat.S_ISLNK(path_info.st_mode) or not stat.S_ISREG(path_info.st_mode)
            or (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino)):
        raise Error("LLVM source recipe must be a regular, non-symlink file")
    return contents, stat.S_IMODE(info.st_mode)


def _write_new(path: Path, contents: bytes) -> None:
    """Create a profile artifact without replacing any existing path."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise Error(f"Refusing to overwrite build-profile artifact: {path}") from exc
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
    except BaseException:
        # Remove only the file this call created; retain any path concurrently
        # replaced by another process.
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                path.unlink()
        except OSError:
            pass
        raise


def _atomic_replace(path: Path, contents: bytes, mode: int) -> None:
    """Replace a recipe in one step so interruption cannot leave a partial file."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.intelbrew-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(mode)
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def prepare_build_profile(name: str, sources: Mapping[str, object], work: Path,
                          profile: str) -> Iterator[dict[str, object]]:
    """Temporarily apply the supported LLVM no-PGO recipe profile for one build.

    ``sources`` is copied, not modified. The original source recipe is restored
    on every context exit; an unexpected concurrent edit is preserved and
    reported instead of being overwritten.
    """
    if name != "llvm" or profile != LLVM_NO_PGO:
        raise Error("Unsupported build profile")
    if not isinstance(sources, Mapping):
        raise Error("Build sources must be a mapping")
    formula_value = sources.get("formula_path")
    expected_sha = sources.get("formula_sha256")
    if not isinstance(formula_value, (str, os.PathLike)) or not isinstance(expected_sha, str):
        raise Error("Build sources lack a local recipe path or checksum")

    formula_path = Path(formula_value)
    original, original_mode = _read_regular_recipe(formula_path)
    if hashlib.sha256(original).hexdigest() != expected_sha:
        raise Error("LLVM source recipe checksum mismatch")
    variant = variant_recipe(original)

    work_path = Path(work)
    try:
        if work_path.is_symlink():
            raise Error("Build-profile work directory must not be a symlink")
        work_path.mkdir(parents=True, exist_ok=True)
        if not work_path.is_dir():
            raise Error("Build-profile work path is not a directory")
    except OSError as exc:
        raise Error("Build-profile work directory could not be prepared") from exc

    canonical_copy = work_path / "llvm-core.rb"
    variant_copy = work_path / "llvm-no-pgo.rb"
    if any(path.exists() or path.is_symlink() for path in (canonical_copy, variant_copy)):
        raise Error("Refusing to overwrite build-profile artifacts")
    _write_new(canonical_copy, original)
    try:
        _write_new(variant_copy, variant)
    except BaseException:
        try:
            canonical_copy.unlink()
        except OSError:
            pass
        raise

    # Confirm the exact file still matches the collected source before making
    # the temporary in-place change.
    current, current_mode = _read_regular_recipe(formula_path)
    if current != original or current_mode != original_mode:
        raise Error("LLVM source recipe changed before profile preparation")

    try:
        _atomic_replace(formula_path, variant, original_mode)
        prepared = dict(sources)
        prepared.update({
            "formula_path": str(canonical_copy),
            "profile_recipe_path": str(variant_copy),
            "build_profile": profile,
            "recipe_sha256": recipe_sha256(variant),
        })
        yield prepared
    finally:
        current, current_mode = _read_regular_recipe(formula_path)
        if current == variant and current_mode == original_mode:
            _atomic_replace(formula_path, original, original_mode)
        elif current != original or current_mode != original_mode:
            raise Error("LLVM source recipe changed during profile build; concurrent edit preserved")

