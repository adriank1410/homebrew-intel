# SPDX-License-Identifier: BSD-2-Clause
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import record
from intelbrew.core import Error, native
from intelbrew.ci import _prepare_source_sets, _previous_source_seeds

CONFIG = {"repository": "adriank1410/homebrew-intel"}
PAYLOAD = b"upstream-archive-bytes"
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
INPUT = "inputs/000-" + "d" * 64 + "--tool-1.0.tar.gz"


def bundle_bytes(resources, members):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        index = json.dumps({"schema": 1, "resources": resources}).encode()
        for name, content in [("sources.json", index), *members]:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return data.getvalue()


def fake_download(payload):
    def write(url, target, sha, size):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target
    return write


class PreviousSourceSeedTests(unittest.TestCase):
    def seeds(self, payload, rec=None):
        rec = rec or record()
        with patch("intelbrew.ci.download", side_effect=fake_download(payload)) as download, \
             patch("intelbrew.ci.attest") as attest:
            folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
            result = _previous_source_seeds("tool", {"tool": rec}, CONFIG, folder)
        return result, download, attest

    def test_extracts_inputs_by_sha256_after_download_and_attestation(self):
        payload = bundle_bytes([{"filename": INPUT, "sha256": PAYLOAD_SHA}], [(INPUT, PAYLOAD)])
        seeds, download, attest = self.seeds(payload)
        self.assertIsNotNone(seeds)
        self.assertEqual((seeds / PAYLOAD_SHA).read_bytes(), PAYLOAD)
        self.assertEqual(sorted(path.name for path in seeds.iterdir()), [PAYLOAD_SHA])
        url = download.call_args.args[0]
        self.assertEqual(url, "https://github.com/adriank1410/homebrew-intel/releases/download/"
                              "intel-123-1-tool/tool--1.0.sources.tar.gz")
        self.assertEqual(attest.call_args.args[1:], ("adriank1410/homebrew-intel", "b" * 40))

    def test_rejects_member_whose_bytes_do_not_match_the_index(self):
        payload = bundle_bytes([{"filename": INPUT, "sha256": "e" * 64}], [(INPUT, PAYLOAD)])
        seeds, _, _ = self.seeds(payload)
        self.assertIsNone(seeds)

    def test_rejects_paths_outside_inputs_and_missing_members(self):
        for resources, members in (
            ([{"filename": "../escape", "sha256": PAYLOAD_SHA}], [("../escape", PAYLOAD)]),
            ([{"filename": "recipe/tool.rb", "sha256": PAYLOAD_SHA}], [("recipe/tool.rb", PAYLOAD)]),
            ([{"filename": INPUT, "sha256": PAYLOAD_SHA}], []),
        ):
            with self.subTest(resources=resources):
                seeds, _, _ = self.seeds(bundle_bytes(resources, members))
                self.assertIsNone(seeds)

    def test_no_previous_release_or_failed_attestation_means_no_seeds(self):
        with patch("intelbrew.ci.download") as download, tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(_previous_source_seeds("tool", {}, CONFIG, Path(folder)))
            self.assertIsNone(_previous_source_seeds("tool", {"tool": record(release=None)}, CONFIG, Path(folder)))
            download.assert_not_called()
        payload = bundle_bytes([{"filename": INPUT, "sha256": PAYLOAD_SHA}], [(INPUT, PAYLOAD)])
        with patch("intelbrew.ci.download", side_effect=fake_download(payload)), \
             patch("intelbrew.ci.attest", side_effect=Error("attestation failed")), \
             tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(_previous_source_seeds("tool", {"tool": record()}, CONFIG, Path(folder)))
            self.assertFalse((Path(folder) / "tool-source-seeds").exists())


class SourceSetRetryTests(unittest.TestCase):
    def test_failed_fetch_is_retried_once_with_seeds(self):
        calls = []
        def fake_native(request, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise Error("brew failed (1): intelbrew: ChecksumMismatchError: SHA-256 mismatch")
            return {"name": request["name"]}
        with tempfile.TemporaryDirectory() as folder, \
             patch("intelbrew.ci.native", side_effect=fake_native), \
             patch("intelbrew.ci._previous_source_seeds", return_value=Path(folder) / "seeds") as seeder:
            caches, sources = _prepare_source_sets(["tool"], Path(folder), records={"tool": record()}, config=CONFIG)
        self.assertEqual(sources, {"tool": {"name": "tool"}})
        self.assertEqual(calls[0], {"ci": True, "cache": caches["tool"]})
        self.assertEqual(calls[1], {"ci": True, "cache": caches["tool"], "source_seeds": Path(folder) / "seeds"})
        self.assertEqual(seeder.call_args.args[0], "tool")

    def test_original_error_is_kept_without_seeds_and_retry_errors_propagate(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch("intelbrew.ci.native", side_effect=Error("first")), \
             patch("intelbrew.ci._previous_source_seeds", return_value=None):
            with self.assertRaisesRegex(Error, "first"):
                _prepare_source_sets(["tool"], Path(folder))
        with tempfile.TemporaryDirectory() as folder, \
             patch("intelbrew.ci.native", side_effect=[Error("first"), Error("second")]), \
             patch("intelbrew.ci._previous_source_seeds", return_value=Path(folder)):
            with self.assertRaisesRegex(Error, "second"):
                _prepare_source_sets(["tool"], Path(folder))

    def test_native_passes_seed_directory_only_when_requested(self):
        seen = []
        def fake_run(args, **kwargs):
            seen.append(kwargs["env"].get("HOMEBREW_INTELBREW_SOURCE_SEEDS"))
            return "{}"
        with patch("intelbrew.core.run", side_effect=fake_run), \
             patch.dict("os.environ", {"HOMEBREW_INTELBREW_SOURCE_SEEDS": "/inherited"}):
            native({"mode": "sources", "name": "tool"}, ci=True)
            native({"mode": "sources", "name": "tool"}, ci=True, source_seeds=Path("/tmp/seeds"))
        self.assertEqual(seen, [None, "/tmp/seeds"])


if __name__ == "__main__":
    unittest.main()
