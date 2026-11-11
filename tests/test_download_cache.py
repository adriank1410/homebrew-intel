# SPDX-License-Identifier: BSD-2-Clause
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intelbrew.core import download


class Response:
    def __init__(self, payload):
        self.payload = payload
        self.reads = 0
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def getcode(self): return 200
    @property
    def headers(self): return {}
    def read(self, size):
        self.reads += 1
        return self.payload if self.reads == 1 else b""


class Opener:
    def __init__(self, payload): self.payload = payload
    def open(self, request, timeout): return Response(self.payload)


class DownloadCacheTests(unittest.TestCase):
    def test_successful_download_leaves_one_name_for_the_file(self):
        payload = b"bottle payload"
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory, \
             patch("intelbrew.core.urllib.request.build_opener", return_value=Opener(payload)):
            target = Path(directory) / "cache" / "artifact"
            download("https://github.com/a/b", target, expected, len(payload))
            self.assertEqual([path.name for path in target.parent.iterdir()], ["artifact"])
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(os.stat(target).st_nlink, 1)

    def test_failed_verification_keeps_the_partial_for_inspection(self):
        payload = b"bottle payload"
        with tempfile.TemporaryDirectory() as directory, \
             patch("intelbrew.core.urllib.request.build_opener", return_value=Opener(payload)):
            target = Path(directory) / "cache" / "artifact"
            with self.assertRaisesRegex(Exception, "retained at"):
                download("https://github.com/a/b", target, "0" * 64, len(payload))
            self.assertEqual([path.name for path in target.parent.iterdir()],
                             [f"artifact.partial-{os.getpid()}"])


if __name__ == "__main__":
    unittest.main()
