# SPDX-License-Identifier: BSD-2-Clause
import unittest
from pathlib import Path
from unittest.mock import patch
from intelbrew.cli import attest
from intelbrew.core import Error

class AttestationWorkflowTests(unittest.TestCase):
    def test_legacy_signer_fallback_keeps_all_identity_constraints(self):
        with patch("intelbrew.cli.shutil.which", return_value="gh"), patch("intelbrew.cli.run", side_effect=[Error("different signer"), "verified"]) as run:
            attest(Path("bottle"), "owner/repo", "a" * 40)
            self.assertEqual(run.call_count, 2)
            signers = []
            for call in run.call_args_list:
                args = call.args[0]
                signers.append(args[args.index("--signer-workflow") + 1])
                self.assertEqual(args[args.index("--source-digest") + 1], "a" * 40)
                self.assertEqual(args[args.index("--signer-digest") + 1], "a" * 40)
                self.assertEqual(args[args.index("--source-ref") + 1], "refs/heads/main")
                self.assertIn("--deny-self-hosted-runners", args)
            self.assertEqual(signers, ["owner/repo/.github/workflows/bottle-root.yml", "owner/repo/.github/workflows/bottles.yml"])

    def test_rejects_when_neither_trusted_signer_verifies(self):
        with patch("intelbrew.cli.shutil.which", return_value="gh"), patch("intelbrew.cli.run", side_effect=Error("invalid")) as run:
            with self.assertRaises(Error):
                attest(Path("bottle"), "owner/repo", "a" * 40)
            self.assertEqual(run.call_count, 2)
