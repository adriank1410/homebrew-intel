# SPDX-License-Identifier: BSD-2-Clause
import hashlib
import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from intelbrew.core import Error, load_config
from intelbrew.license_exceptions import (
    extract_license_bytes,
    match_license_members,
    nmap_fallback_url,
    resolve_source_requirement,
    try_renew_source_exception,
    validate_source_required_formula_exceptions,
)
from helpers import meta


LICENSE = b"Nmap Public Source License Version 0.95\n"
LICENSE_SHA = hashlib.sha256(LICENSE).hexdigest()
SOURCE_SHA = "b" * 64


def exception_entry(**overrides):
    entry = {
        "formula_sha256": "a" * 64,
        "license": "cannot_represent",
        "requirement": "Nmap-Public-Source-License",
        "review": "Reviewed standalone distribution with the corresponding source and NPSL text.",
        "license_file": "nmap-*/LICENSE",
        "license_sha256": LICENSE_SHA,
    }
    entry.update(overrides)
    return entry


def write_tar(path, members):
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


class LicenseExceptionTests(unittest.TestCase):
    def test_star_does_not_cross_a_slash_and_requires_a_unique_match(self):
        names = [
            "nmap-7.993/LICENSE",
            "nmap-7.993/docs/LICENSE",
            "nmap-7.993/libpcap/LICENSE",
            "other/LICENSE",
        ]
        self.assertEqual(match_license_members(names, "nmap-*/LICENSE"), ["nmap-7.993/LICENSE"])
        self.assertEqual(match_license_members(names, "LICENSE"), [])
        self.assertEqual(match_license_members(["LICENSE"], "LICENSE"), ["LICENSE"])
        with tempfile.TemporaryDirectory() as folder:
            archive = write_tar(Path(folder) / "src.tar.gz", [
                ("nmap-7.993/LICENSE", LICENSE),
                ("nmap-7.993/docs/LICENSE", b"other"),
                ("nmap-7.993/libpcap/LICENSE", b"pcap"),
            ])
            self.assertEqual(extract_license_bytes(archive, "nmap-*/LICENSE"), LICENSE)
            other = write_tar(Path(folder) / "both.tar.gz", [
                ("nmap-7.993/LICENSE", LICENSE),
                ("other-1/LICENSE", b"other"),
            ])
            with self.assertRaises(Error):
                extract_license_bytes(other, "*/LICENSE")

    def test_zip_and_tar_extract_the_configured_member(self):
        with tempfile.TemporaryDirectory() as folder:
            tar = write_tar(Path(folder) / "src.tar.gz", [("tool-1/LICENSE", LICENSE)])
            zip_path = Path(folder) / "src.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("tool-1/LICENSE", LICENSE)
            self.assertEqual(extract_license_bytes(tar, "tool-*/LICENSE"), LICENSE)
            self.assertEqual(extract_license_bytes(zip_path, "tool-*/LICENSE"), LICENSE)

    def test_validation_requires_both_or_neither_license_fields(self):
        valid = {"nmap": exception_entry()}
        self.assertEqual(
            validate_source_required_formula_exceptions(valid)["nmap"]["license_sha256"],
            LICENSE_SHA,
        )
        incomplete = exception_entry()
        del incomplete["license_sha256"]
        with self.assertRaises(Error):
            validate_source_required_formula_exceptions({"nmap": incomplete})
        incomplete = exception_entry()
        del incomplete["license_file"]
        with self.assertRaises(Error):
            validate_source_required_formula_exceptions({"nmap": incomplete})
        with self.assertRaises(Error):
            validate_source_required_formula_exceptions(
                {"nmap": exception_entry(license_file=".. /LICENSE")}
            )

    def test_exact_hash_still_short_circuits_without_fetching(self):
        item = meta(name="nmap", license="cannot_represent", formula_sha256="a" * 64)
        config = {"source_required_formula_exceptions": {"nmap": exception_entry()}}
        calls = []
        requirements = resolve_source_requirement(
            item, config, log=calls, stable_source=lambda name: (_ for _ in ()).throw(AssertionError()),
            fetch_archive=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()),
        )
        self.assertEqual(requirements, ("Nmap-Public-Source-License",))
        self.assertEqual(calls, [])

    def test_renewal_requires_matching_source_checksum_and_identical_license(self):
        item = meta(name="nmap", license="cannot_represent", formula_sha256="c" * 64)
        entry = exception_entry()
        log = []
        with tempfile.TemporaryDirectory() as folder:
            archive = write_tar(Path(folder) / "src.tar.gz", [
                ("nmap-7.994/LICENSE", LICENSE),
                ("nmap-7.994/docs/LICENSE", b"other"),
            ])
            source_sha = hashlib.sha256(archive.read_bytes()).hexdigest()

            def stable_source(name):
                self.assertEqual(name, "nmap")
                return {"url": "https://nmap.org/dist/nmap-7.994.tar.bz2", "sha256": source_sha}

            def fetch_archive(url, expected, destination, fallbacks=None):
                self.assertEqual(expected, source_sha)
                self.assertEqual(url, "https://nmap.org/dist/nmap-7.994.tar.bz2")
                self.assertEqual(fallbacks, ["https://distfiles.macports.org/nmap/nmap-7.994.tar.bz2"])
                destination.write_bytes(archive.read_bytes())
                return destination

            event = try_renew_source_exception(
                "nmap", item, entry, stable_source=stable_source, fetch_archive=fetch_archive)
            self.assertEqual(event["formula_sha256"], "c" * 64)
            self.assertEqual(event["previous_formula_sha256"], "a" * 64)
            self.assertEqual(event["license_sha256"], LICENSE_SHA)
            self.assertEqual(
                resolve_source_requirement(
                    item, {"source_required_formula_exceptions": {"nmap": entry}},
                    log=log, stable_source=stable_source, fetch_archive=fetch_archive,
                ),
                ("Nmap-Public-Source-License",),
            )
            self.assertEqual(log[-1]["license_file"], "nmap-*/LICENSE")

    def test_renewal_fails_closed_when_the_license_or_source_does_not_match(self):
        item = meta(name="nmap", license="cannot_represent", formula_sha256="c" * 64)
        entry = exception_entry()
        with tempfile.TemporaryDirectory() as folder:
            changed = write_tar(Path(folder) / "changed.tar.gz", [("nmap-7.994/LICENSE", b"new text\n")])

            def stable_source(name):
                return {"url": "https://example.test/nmap.tar.bz2", "sha256": SOURCE_SHA}

            def fetch_changed(url, expected, destination, fallbacks=None):
                destination.write_bytes(changed.read_bytes())
                return destination

            self.assertIsNone(try_renew_source_exception(
                "nmap", item, entry, stable_source=stable_source, fetch_archive=fetch_changed))
            self.assertIsNone(try_renew_source_exception(
                "nmap", item, exception_entry(license_file=None, license_sha256=None),
                stable_source=stable_source, fetch_archive=fetch_changed))
            self.assertIsNone(try_renew_source_exception("nmap", item, entry))
            self.assertIsNone(try_renew_source_exception(
                "nmap", {**item, "license": "unknown"}, entry,
                stable_source=stable_source, fetch_archive=fetch_changed))

            def boom(*args, **kwargs):
                raise Error("download failed")

            self.assertIsNone(try_renew_source_exception(
                "nmap", item, entry, stable_source=stable_source, fetch_archive=boom))
            self.assertIsNone(resolve_source_requirement(
                item, {"source_required_formula_exceptions": {"nmap": entry}}))

    def test_current_nmap_exception_stores_the_reviewed_license_hash(self):
        entry = load_config()["source_required_formula_exceptions"]["nmap"]
        self.assertEqual(entry["formula_sha256"], "75ebf9e178c521a5ce5723abdd6a7f89519b301eee784e729f8ec1101102d03a")
        self.assertEqual(entry["license_file"], "nmap-*/LICENSE")
        self.assertEqual(
            entry["license_sha256"],
            "9d9a9a763c0e6145172cfe7d8483e23b38ce60b6c79a82e4894242917bdae6d3",
        )

    def test_nmap_fallback_url_is_limited_to_the_official_distfile(self):
        self.assertEqual(
            nmap_fallback_url("https://nmap.org/dist/nmap-7.993.tar.bz2"),
            "https://distfiles.macports.org/nmap/nmap-7.993.tar.bz2",
        )
        self.assertIsNone(nmap_fallback_url("https://evil.test/nmap-7.993.tar.bz2"))
        self.assertIsNone(nmap_fallback_url("https://nmap.org/dist/../nmap-7.993.tar.bz2"))


if __name__ == "__main__":
    unittest.main()
