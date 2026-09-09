# SPDX-License-Identifier: BSD-2-Clause
"""Read bounded license notices from source archives without extracting them."""
from __future__ import annotations

import os
import re
import subprocess
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath

from .core import Error

NOTICE_NAME = re.compile(r"(?:LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)(?:[._-].*)?\Z", re.I)
PYTHON_TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tbz2", ".tar.xz", ".txz")
LIBARCHIVE_SUFFIXES = (".tar.lz", ".tlz", ".tar.zst", ".tzst", ".7z")
MAX_ARCHIVE_STREAM = 2_000_000_000
ARCHIVE_TIMEOUT = 60


def _safe_name(value: str) -> bool:
    name = PurePosixPath(value)
    return bool(value) and not name.is_absolute() and ".." not in name.parts


def _read_tar(archive: tarfile.TarFile, max_size: int, max_count: int):
    found = []
    for member in archive:
        if (member.isfile() and _safe_name(member.name)
                and NOTICE_NAME.fullmatch(PurePosixPath(member.name).name)
                and 0 < member.size <= max_size):
            if len(found) >= max_count:
                raise Error("Too many upstream license notices")
            handle = archive.extractfile(member)
            data = handle.read(max_size + 1) if handle else b""
            if len(data) != member.size:
                raise Error("Invalid upstream license notice")
            found.append((member.name, data))
    return found


class _BoundedReader:
    def __init__(self, stream):
        self.stream, self.total = stream, 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.total += len(data)
        if self.total > MAX_ARCHIVE_STREAM:
            raise Error("Source archive stream exceeds limit")
        return data


def _libarchive_notices(path: Path, max_size: int, max_count: int):
    command = ["/usr/bin/bsdtar", "-cf", "-", "@" + os.fspath(path.resolve())]
    with tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr)
        except OSError as exc:
            raise Error(f"Cannot inspect source archive: {path.name}") from exc
        timed_out = threading.Event()
        def terminate():
            timed_out.set()
            process.kill()
        timer = threading.Timer(ARCHIVE_TIMEOUT, terminate); timer.start()
        try:
            assert process.stdout is not None
            with tarfile.open(fileobj=_BoundedReader(process.stdout), mode="r|") as archive:
                found = _read_tar(archive, max_size, max_count)
            return_code = process.wait()
        except BaseException:
            process.kill(); process.wait()
            raise
        finally:
            timer.cancel()
            if process.stdout:
                process.stdout.close()
        if timed_out.is_set():
            raise Error(f"Source archive inspection timed out: {path.name}")
        if return_code:
            raise Error(f"Invalid source archive: {path.name}")
        return found


def archive_notices(path: Path, max_notice_size: int, max_notices: int):
    """Return safe regular notice members as ``(archive_name, bytes)`` pairs."""
    path = Path(path)
    if max_notice_size <= 0 or max_notices <= 0:
        raise Error("Invalid archive notice limits")
    if path.is_symlink() or not path.is_file():
        raise Error(f"Not a regular source archive: {path}")
    lower = path.name.lower()
    try:
        if lower.endswith(PYTHON_TAR_SUFFIXES) or (
                not lower.endswith(LIBARCHIVE_SUFFIXES) and tarfile.is_tarfile(path)):
            with tarfile.open(path, "r:*") as archive:
                return _read_tar(archive, max_notice_size, max_notices)
        if lower.endswith(".zip") or (
                not lower.endswith(LIBARCHIVE_SUFFIXES) and zipfile.is_zipfile(path)):
            found = []
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    mode = (member.external_attr >> 16) & 0o170000
                    if (not member.is_dir() and mode in (0, 0o100000) and _safe_name(member.filename)
                            and NOTICE_NAME.fullmatch(PurePosixPath(member.filename).name)
                            and 0 < member.file_size <= max_notice_size):
                        if len(found) >= max_notices:
                            raise Error("Too many upstream license notices")
                        data = archive.read(member)
                        if len(data) != member.file_size:
                            raise Error("Invalid upstream license notice")
                        found.append((member.filename, data))
            return found
        if lower.endswith(LIBARCHIVE_SUFFIXES):
            return _libarchive_notices(path, max_notice_size, max_notices)
        return []
    except Error:
        raise
    except (tarfile.TarError, zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
        raise Error(f"Invalid source archive: {path.name}") from exc
