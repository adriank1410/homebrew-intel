# SPDX-License-Identifier: BSD-2-Clause
"""Pinned source-required formula exceptions, including license-file auto-renewal."""
from __future__ import annotations

import hashlib
import re
import tempfile
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from .core import Error, canonical_name, digest, require_sha

# A path component is a literal, or contains at most one '*' (which never crosses '/').
LICENSE_FILE_RE = re.compile(
    r"\A(?:[A-Za-z0-9._-]+|[A-Za-z0-9._-]*\*[A-Za-z0-9._-]*)"
    r"(?:/(?:[A-Za-z0-9._-]+|[A-Za-z0-9._-]*\*[A-Za-z0-9._-]*))*\Z")
NMAP_DISTFILE = re.compile(
    r"\Ahttps://nmap\.org/dist/(nmap-[0-9]+(?:\.[0-9]+){1,4}(?:_[0-9]+)?\.tar\.bz2)\Z")
MAX_EXCEPTION_REVIEW = 4000
MAX_LICENSE_FILE = 2 * 1024 * 1024
MAX_SOURCE_ARCHIVE = 64 * 1024 * 1024
REQUIRED_EXCEPTION_KEYS = {"formula_sha256", "license", "requirement", "review"}
OPTIONAL_LICENSE_KEYS = {"license_file", "license_sha256"}


def validate_source_required_formula_exceptions(entries):
    if not entries:
        return {}
    if not isinstance(entries, dict) or len(entries) > 50:
        raise Error("Invalid source-required formula exceptions")
    normalized = {}
    for raw_name, raw in entries.items():
        name = canonical_name(raw_name)
        if name in normalized:
            raise Error("Duplicate source-required formula exception")
        allowed = REQUIRED_EXCEPTION_KEYS | OPTIONAL_LICENSE_KEYS
        if not isinstance(raw, dict) or not REQUIRED_EXCEPTION_KEYS <= set(raw) <= allowed:
            raise Error("Invalid source-required formula exception: " + name)
        require_sha(raw["formula_sha256"])
        if not isinstance(raw["license"], str) or not raw["license"] or len(raw["license"]) > 200:
            raise Error("Invalid source-required formula exception: " + name)
        if not isinstance(raw["requirement"], str) or not raw["requirement"] or len(raw["requirement"]) > 200:
            raise Error("Invalid source-required formula exception: " + name)
        review = raw["review"]
        if not isinstance(review, str) or len(review.strip()) < 30 or len(review) > MAX_EXCEPTION_REVIEW:
            raise Error("Invalid source-required formula exception: " + name)
        entry = {key: raw[key] for key in REQUIRED_EXCEPTION_KEYS}
        has_file = "license_file" in raw
        has_hash = "license_sha256" in raw
        if has_file != has_hash:
            raise Error("Invalid source-required formula exception: " + name)
        if has_file:
            license_file = raw["license_file"]
            if not isinstance(license_file, str) or LICENSE_FILE_RE.fullmatch(license_file) is None:
                raise Error("Invalid source-required formula exception: " + name)
            require_sha(raw["license_sha256"])
            entry["license_file"] = license_file
            entry["license_sha256"] = raw["license_sha256"]
        normalized[name] = entry
    return normalized


def match_license_members(names, pattern):
    """Return archive members matching a slash-anchored pattern (* never crosses /)."""
    expected = pattern.split("/")
    matches = []
    for name in names:
        parts = PurePosixPath(name).parts
        if len(parts) != len(expected):
            continue
        if all(_component_matches(part, token) for part, token in zip(parts, expected)):
            matches.append(name)
    return matches


def _component_matches(part, token):
    if token == "*":
        return True
    if "*" not in token:
        return part == token
    if token.count("*") != 1:
        return False
    prefix, suffix = token.split("*", 1)
    return part.startswith(prefix) and part.endswith(suffix) and len(part) >= len(prefix) + len(suffix)


def extract_license_bytes(archive, pattern):
    members = _archive_files(archive)
    matches = match_license_members(members, pattern)
    if len(matches) != 1:
        raise Error("License file in the source archive is missing or not unique")
    data = _read_archive_member(archive, matches[0])
    if not data or len(data) > MAX_LICENSE_FILE:
        raise Error("License file in the source archive is empty or too large")
    return data


def _archive_files(archive):
    name = archive.name
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            return [info.filename for info in zf.infolist() if not info.is_dir()]
    with tarfile.open(archive) as tf:
        return [member.name for member in tf.getmembers() if member.isfile()]


def _read_archive_member(archive, member):
    name = archive.name
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            with zf.open(member) as handle:
                return handle.read(MAX_LICENSE_FILE + 1)
    with tarfile.open(archive) as tf:
        extracted = tf.extractfile(member)
        if extracted is None:
            raise Error("License file in the source archive is missing or not unique")
        return extracted.read(MAX_LICENSE_FILE + 1)


def nmap_fallback_url(url):
    match = NMAP_DISTFILE.fullmatch(url)
    if match is None:
        return None
    return "https://distfiles.macports.org/nmap/" + match.group(1)


def fetch_verified_https(url, expected_sha, destination, fallbacks=None):
    require_sha(expected_sha)
    candidates = [url, *(fallbacks or [])]
    errors = []
    for candidate in candidates:
        try:
            _download_https(candidate, expected_sha, destination)
            return destination
        except (Error, OSError, urllib.error.URLError) as exc:
            errors.append(candidate + ": " + str(exc))
            if destination.exists():
                destination.unlink()
    raise Error("Unable to fetch a source archive that matches the recipe checksum: " + "; ".join(errors[:4]))


def _download_https(url, expected_sha, destination):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise Error("Source archive URL is not a credential-free HTTPS URL")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(_HttpsOnlyRedirect())
    with opener.open(request, timeout=60) as response:
        hasher = hashlib.sha256()
        total = 0
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SOURCE_ARCHIVE:
                    raise Error("Source archive exceeds the license-renewal size bound")
                hasher.update(chunk)
                handle.write(chunk)
    if hasher.hexdigest() != expected_sha:
        raise Error("Downloaded source archive does not match the recipe checksum")


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise Error("Refusing a non-HTTPS or credentialed redirect while fetching a source archive")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def stable_source_from_native(name, native):
    data = native({"mode": "stable-source", "name": name}, ci=True)
    if not isinstance(data, dict) or data.get("name") != name:
        raise Error("Stable source metadata is incomplete")
    url = data.get("url")
    sha = data.get("sha256")
    if not isinstance(url, str) or not url:
        raise Error("Stable source metadata is incomplete")
    require_sha(sha)
    return {"url": url, "sha256": sha}


def try_renew_source_exception(name, meta, exception, stable_source=None, fetch_archive=None):
    """Return an audit event when the new recipe source still carries the reviewed license."""
    if exception.get("license") != meta.get("license"):
        return None
    license_file = exception.get("license_file")
    license_sha = exception.get("license_sha256")
    if not license_file or not license_sha:
        return None
    if stable_source is None or fetch_archive is None:
        return None
    try:
        source = stable_source(name)
        fallbacks = []
        fallback = nmap_fallback_url(source["url"])
        if fallback:
            fallbacks.append(fallback)
        with tempfile.TemporaryDirectory(prefix="intelbrew-license-") as folder:
            archive = fetch_archive(
                source["url"], source["sha256"], Path(folder) / "source.bin", fallbacks=fallbacks)
            if digest(archive) != source["sha256"]:
                return None
            actual = hashlib.sha256(extract_license_bytes(archive, license_file)).hexdigest()
        if actual != license_sha:
            return None
    except (Error, OSError, KeyError, TypeError, ValueError, urllib.error.URLError):
        return None
    return {
        "name": name,
        "previous_formula_sha256": exception["formula_sha256"],
        "formula_sha256": meta["formula_sha256"],
        "source_sha256": source["sha256"],
        "license_file": license_file,
        "license_sha256": license_sha,
        "requirement": exception["requirement"],
    }


def resolve_source_requirement(meta, config, log=None, stable_source=None, fetch_archive=None):
    exception = config.get("source_required_formula_exceptions", {}).get(meta["name"], {})
    if not exception:
        return None
    if (exception.get("formula_sha256") == meta.get("formula_sha256")
            and exception.get("license") == meta.get("license")
            and isinstance(exception.get("requirement"), str) and exception["requirement"]
            and isinstance(exception.get("review"), str) and len(exception["review"].strip()) >= 30):
        return (exception["requirement"],)
    event = try_renew_source_exception(
        meta["name"], meta, exception, stable_source=stable_source, fetch_archive=fetch_archive)
    if event is None:
        return None
    if log is not None:
        log.append(event)
    return (exception["requirement"],)
