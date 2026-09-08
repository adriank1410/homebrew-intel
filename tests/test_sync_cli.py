import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from intelbrew import cli
from helpers import meta


class SyncCommandTests(unittest.TestCase):
    def test_sync_json_uses_real_eligibility_without_publishing(self):
        def native(request):
            if request["mode"] == "coverage":
                return {"core": ["newtool", "private-head"], "external_taps": ["external/private/tool"]}
            return {name: meta(name, installed_head=name == "private-head") for name in request["names"]}
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch("intelbrew.coverage_sync.native", side_effect=native), \
             patch("intelbrew.coverage_sync.gh", side_effect=AssertionError("dry run must not publish")):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(["sync", "--json"]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["eligible"], ["newtool"])
        self.assertEqual(report["excluded"], [{"name": "private-head", "reason": "options or HEAD install requires review"}])
        self.assertIsNone(report["pr_url"])
        self.assertNotIn("external/private/tool", output.getvalue())

    def test_sync_summary_groups_exclusions_and_explains_dry_run(self):
        report = {"monitored_core": ["curl"], "eligible": ["zlib"],
                  "excluded": [{"name": "one", "reason": "license requires review"},
                               {"name": "two", "reason": "license requires review"}],
                  "pr_url": None}
        output = StringIO()
        with redirect_stdout(output):
            cli.render_sync(report, apply=False)
        self.assertIn("1 monitored, 1 eligible additions, 2 excluded", output.getvalue())
        self.assertIn("license requires review: 2", output.getvalue())
        self.assertIn("--apply", output.getvalue())
        self.assertNotIn("one", output.getvalue())

    def test_sync_rejects_package_names_and_upgrade_only_flags(self):
        with patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="x86_64"), \
             patch.object(cli, "load_config", return_value={}), \
             patch.object(cli, "registry", return_value={}):
            for arguments in (["sync", "curl"], ["sync", "--available"]):
                with self.subTest(arguments=arguments):
                    self.assertEqual(cli.main(arguments), 1)
