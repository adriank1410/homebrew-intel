import io
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from intelbrew.archive_notices import archive_notices
from intelbrew.core import Error


class ArchiveNoticeTests(unittest.TestCase):
    def test_reads_regular_notices_and_rejects_unsafe_or_symlink_members(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tar_path = root / "source.tar.gz"
            with tarfile.open(tar_path, "w:gz") as archive:
                for name, data in (("pkg/LICENSE.md", b"tar license"), ("../NOTICE", b"unsafe")):
                    member = tarfile.TarInfo(name); member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                link = tarfile.TarInfo("pkg/COPYING"); link.type = tarfile.SYMTYPE; link.linkname = "LICENSE.md"
                archive.addfile(link)
            self.assertEqual(archive_notices(tar_path, 1024, 4), [("pkg/LICENSE.md", b"tar license")])

            zip_path = root / "source.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("pkg/NOTICE", b"zip notice")
                regular = zipfile.ZipInfo("pkg/LICENSE.txt"); regular.external_attr = 0o100644 << 16
                archive.writestr(regular, b"regular notice")
                for name, mode in (("COPYING", 0o120777), ("LICENSE", 0o010644),
                                   ("LICENCE", 0o020644), ("COPYRIGHT", 0o140644)):
                    special = zipfile.ZipInfo("pkg/" + name); special.external_attr = mode << 16
                    archive.writestr(special, b"fake notice")
            self.assertEqual(archive_notices(zip_path, 1024, 4),
                             [("pkg/NOTICE", b"zip notice"), ("pkg/LICENSE.txt", b"regular notice")])

    def test_enforces_size_count_and_argument_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.tar"
            with tarfile.open(path, "w") as archive:
                for name, data in (("LICENSE", b"12345"), ("NOTICE", b"ok")):
                    member = tarfile.TarInfo(name); member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            self.assertEqual(archive_notices(path, 2, 2), [("NOTICE", b"ok")])
            with self.assertRaisesRegex(Error, "Too many"):
                archive_notices(path, 10, 1)
            for size, count in ((0, 1), (1, 0)):
                with self.assertRaisesRegex(Error, "limits"):
                    archive_notices(path, size, count)

    def test_unknown_resource_is_ignored_but_corrupt_supported_archives_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            patch = root / "fix.patch"; patch.write_bytes(b"not an archive")
            self.assertEqual(archive_notices(patch, 1024, 4), [])
            for name in ("bad.tar.gz", "bad.zip"):
                path = root / name; path.write_bytes(b"not an archive")
                with self.assertRaisesRegex(Error, "Invalid source archive"):
                    archive_notices(path, 1024, 4)
            special = root / "bad.tar.zst"; special.write_bytes(b"not an archive")
            with self.assertRaises(Error):
                archive_notices(special, 1024, 4)

    def test_sniffs_tar_and_zip_with_extensionless_cache_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for kind in ("tar", "zip"):
                path = root / ("a" * 64 + kind)
                if kind == "tar":
                    with tarfile.open(path, "w") as archive:
                        data = b"tar cache"; member = tarfile.TarInfo("pkg/LICENSE")
                        member.size = len(data); archive.addfile(member, io.BytesIO(data))
                else:
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr("pkg/LICENSE", b"zip cache")
                self.assertEqual(archive_notices(path, 1024, 4)[0][1], kind.encode() + b" cache")

    @unittest.skipUnless(Path("/usr/bin/bsdtar").exists() and shutil.which("zstd"),
                         "requires macOS bsdtar and zstd")
    def test_reads_real_zstd_tar_through_libarchive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); plain = root / "source.tar"; compressed = root / "source.tar.zst"
            with tarfile.open(plain, "w") as archive:
                data = b"zstd license"; member = tarfile.TarInfo("pkg/COPYING")
                member.size = len(data); archive.addfile(member, io.BytesIO(data))
            subprocess.run([shutil.which("zstd"), "-q", "-f", str(plain), "-o", str(compressed)], check=True)
            self.assertEqual(archive_notices(compressed, 1024, 4), [("pkg/COPYING", b"zstd license")])

    @unittest.skipUnless(Path("/usr/bin/bsdtar").exists() and shutil.which("7z"),
                         "requires macOS bsdtar and 7z")
    def test_reads_real_7z_through_libarchive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); notice = root / "NOTICE"; archive = root / "source.7z"
            notice.write_bytes(b"7z license")
            subprocess.run([shutil.which("7z"), "a", "-bd", "-y", str(archive), notice.name],
                           cwd=root, stdout=subprocess.DEVNULL, check=True)
            self.assertEqual(archive_notices(archive, 1024, 4), [("NOTICE", b"7z license")])


if __name__ == "__main__":
    unittest.main()
