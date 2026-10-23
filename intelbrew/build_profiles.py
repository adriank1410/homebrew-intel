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

from .core import APPROVED_BUILD_PROFILES, Error, LLVM_NO_PGO, OPENJDK_INTEL_BOOT_JDK_26


_LLVM_CLASS = re.compile(rb"(?m)^class Llvm < Formula$")
_LLVM_PGO_LINE = (
    b"    pgo_build = build.stable? && build.bottle? && OS.mac? && !versioned_formula?\n"
)
_LLVM_NO_PGO_LINES = (
    b"    # Intelbrew LLVM no-PGO profile disables the expensive PGO bootstrap.\n"
    b"    pgo_build = false\n"
)
_BOTTLE_BLOCK = re.compile(rb"  bottle do.+?end\n\n?", re.DOTALL)

# openjdk 27.0.1 switched the macOS boot JDK to JDK 27, which jdk.java.net
# publishes for macOS AArch64 only. The owner-approved Intel profile swaps that
# single resource for the official macOS x64 JDK 26.0.2.1 build that core used
# for Intel through openjdk 27. JDK 26 is a valid boot JDK only for JDK 27
# sources, so the profile is bounded to the jdk27u source tag.
OPENJDK_BOOT_JDK_URL = (
    "https://download.java.net/java/GA/jdk26.0.2.1/3b8e6c7ec6274148a7aa15e7e7dfb53c/1/GPL/"
    "openjdk-26.0.2.1_macos-x64_bin.tar.gz"
)
OPENJDK_BOOT_JDK_SHA256 = "5ec3817530ffa1c38f15a55609a2767335bbd0c59ea8ce41aad68d3cd620c0ae"
OPENJDK_BOOT_JDK_LABEL = "resource-boot-jdk"
_OPENJDK_CLASS = re.compile(rb"(?m)^class Openjdk < Formula$")
_OPENJDK_SOURCE = re.compile(
    rb'(?m)^  url "https://github\.com/openjdk/jdk27u/archive/refs/tags/jdk-27(?:\.[0-9]+)*-ga\.tar\.gz"$')
_OPENJDK_BOOT_RESOURCE = re.compile(rb'(?m)^  resource "boot-jdk" do$')
_OPENJDK_MACOS_BOOT_JDK = re.compile(
    rb'(?m)^(  resource "boot-jdk" do\n'
    rb'    on_macos do\n)'
    rb'      url "https://download\.java\.net/java/GA/jdk27[A-Za-z0-9./_-]*/'
    rb'openjdk-27[0-9.]*_macos-aarch64_bin\.tar\.gz"\n'
    rb'      sha256 "[0-9a-f]{64}"\n'
    rb'(    end\n)')
_OPENJDK_INTEL_BOOT_JDK_LINES = (
    b"      # Intelbrew openjdk-intel-boot-jdk-26 profile: upstream's macOS boot JDK is AArch64-only.\n"
    b'      url "' + OPENJDK_BOOT_JDK_URL.encode() + b'"\n'
    b'      sha256 "' + OPENJDK_BOOT_JDK_SHA256.encode() + b'"\n'
)
# Diagnostic label per formula; error texts for LLVM stay unchanged.
_LABELS = {"llvm": "LLVM", "openjdk": "openjdk"}


def _llvm_variant(upstream: bytes) -> bytes:
    if len(_LLVM_CLASS.findall(upstream)) != 1:
        raise Error("Unexpected LLVM formula class")
    if upstream.count(_LLVM_PGO_LINE) != 1:
        raise Error("Unexpected or ambiguous LLVM PGO recipe line")
    return upstream.replace(_LLVM_PGO_LINE, _LLVM_NO_PGO_LINES, 1)


def _openjdk_variant(upstream: bytes) -> bytes:
    # Any structural drift (new feature release, restored Intel boot JDK,
    # nested on_arm/on_intel, extra boot-jdk resource) stops before a build.
    if len(_OPENJDK_CLASS.findall(upstream)) != 1:
        raise Error("Unexpected openjdk formula class; review needed")
    if len(_OPENJDK_SOURCE.findall(upstream)) != 1:
        raise Error("openjdk source is not a single jdk27u release; review needed")
    if len(_OPENJDK_BOOT_RESOURCE.findall(upstream)) != 1:
        raise Error("Unexpected or ambiguous openjdk boot-jdk resource; review needed")
    matches = list(_OPENJDK_MACOS_BOOT_JDK.finditer(upstream))
    if len(matches) != 1:
        raise Error("openjdk macOS boot JDK is no longer the AArch64-only JDK 27 block; review needed")
    match = matches[0]
    return (upstream[:match.start()] + match.group(1) + _OPENJDK_INTEL_BOOT_JDK_LINES
            + match.group(2) + upstream[match.end():])


_VARIANTS = {LLVM_NO_PGO: _llvm_variant, OPENJDK_INTEL_BOOT_JDK_26: _openjdk_variant}


def variant_recipe(upstream: bytes, profile: str = LLVM_NO_PGO) -> bytes:
    """Apply only the approved profile's change, refusing unfamiliar upstream recipes."""
    transform = _VARIANTS.get(profile) if isinstance(profile, str) else None
    if transform is None:
        raise Error("Unsupported build profile")
    if not isinstance(upstream, bytes):
        raise Error("LLVM profile recipe must be bytes" if profile == LLVM_NO_PGO
                    else "Build-profile recipe must be bytes")
    return transform(upstream)


def profile_resources_error(profile: object, resources: object) -> str | None:
    """Describe why collected sources do not match the profile's pinned inputs."""
    if profile != OPENJDK_INTEL_BOOT_JDK_26:
        return None
    if not isinstance(resources, list):
        return "openjdk sources are not a resource list"
    boot = [item for item in resources if isinstance(item, dict) and item.get("label") == OPENJDK_BOOT_JDK_LABEL]
    if (len(boot) != 1 or boot[0].get("url") != OPENJDK_BOOT_JDK_URL
            or boot[0].get("sha256") != OPENJDK_BOOT_JDK_SHA256):
        return "openjdk boot JDK source is not the pinned macOS x64 JDK 26.0.2.1"
    return None


def profile_recipe_name(profile: str) -> str:
    """Name of the executed variant inside work directories and source bundles."""
    if profile not in _VARIANTS:
        raise Error("Unsupported build profile")
    return f"{profile}.rb"


def recipe_sha256(text: bytes) -> str:
    """Hash the recipe using the bottle-block exclusion used by native.rb."""
    if not isinstance(text, bytes):
        raise Error("Recipe must be bytes")
    return hashlib.sha256(_BOTTLE_BLOCK.sub(b"", text)).hexdigest()


def _read_regular_recipe(path: Path, label: str = "LLVM") -> tuple[bytes, int]:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise Error(f"{label} source recipe must be a regular, non-symlink file")
        stream = os.fdopen(descriptor, "rb")
        descriptor = -1
        with stream:
            contents = stream.read()
        path_info = path.lstat()
    except OSError as exc:
        raise Error(f"{label} source recipe is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (stat.S_ISLNK(path_info.st_mode) or not stat.S_ISREG(path_info.st_mode)
            or (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino)):
        raise Error(f"{label} source recipe must be a regular, non-symlink file")
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
    """Temporarily apply an owner-approved recipe profile for one build.

    Supported pairs are ``APPROVED_BUILD_PROFILES``: LLVM no-PGO and the
    openjdk Intel boot-JDK profile.

    ``sources`` is copied, not modified. The original source recipe is restored
    on every context exit; an unexpected concurrent edit is preserved and
    reported instead of being overwritten.
    """
    if not isinstance(name, str) or APPROVED_BUILD_PROFILES.get(name) != profile:
        raise Error("Unsupported build profile")
    label = _LABELS[name]
    if not isinstance(sources, Mapping):
        raise Error("Build sources must be a mapping")
    formula_value = sources.get("formula_path")
    expected_sha = sources.get("formula_sha256")
    if not isinstance(formula_value, (str, os.PathLike)) or not isinstance(expected_sha, str):
        raise Error("Build sources lack a local recipe path or checksum")

    formula_path = Path(formula_value)
    original, original_mode = _read_regular_recipe(formula_path, label)
    if hashlib.sha256(original).hexdigest() != expected_sha:
        raise Error(f"{label} source recipe checksum mismatch")
    variant = variant_recipe(original, profile)

    work_path = Path(work)
    try:
        if work_path.is_symlink():
            raise Error("Build-profile work directory must not be a symlink")
        work_path.mkdir(parents=True, exist_ok=True)
        if not work_path.is_dir():
            raise Error("Build-profile work path is not a directory")
    except OSError as exc:
        raise Error("Build-profile work directory could not be prepared") from exc

    canonical_copy = work_path / f"{name}-core.rb"
    variant_copy = work_path / profile_recipe_name(profile)
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
    current, current_mode = _read_regular_recipe(formula_path, label)
    if current != original or current_mode != original_mode:
        raise Error(f"{label} source recipe changed before profile preparation")

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
        current, current_mode = _read_regular_recipe(formula_path, label)
        if current == variant and current_mode == original_mode:
            _atomic_replace(formula_path, original, original_mode)
        elif current != original or current_mode != original_mode:
            raise Error(f"{label} source recipe changed during profile build; concurrent edit preserved")

