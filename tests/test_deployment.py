# SPDX-License-Identifier: BSD-2-Clause
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from intelbrew.core import ROOT

spec=importlib.util.spec_from_file_location('intelbrew_deployment_script',ROOT/'scripts/deploy.py')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)

class DeploymentTests(unittest.TestCase):
    def test_external_brew_command_is_executable(self):
        self.assertTrue((ROOT/'cmd/brew-intel').stat().st_mode & 0o111,
                        'Homebrew external commands must be executable')
    def test_default_is_dry_run_without_gh_or_auth(self):
        with patch.object(deploy,'checked_files',return_value=[Path('README.md')]), patch.object(deploy,'command') as command,patch.object(deploy,'api') as api, patch('sys.argv',['deploy.py']):
            self.assertEqual(deploy.main(),0);command.assert_not_called();api.assert_not_called()
    def test_distribution_is_an_allowlist_not_directory_sweep(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=b'public code';(root/'public.py').write_bytes(data);(root/'private-audit.json').write_text('never publish')
            (root/'distribution-files.json').write_text(json.dumps({'schema':1,'files':{'public.py':hashlib.sha256(data).hexdigest()}}))
            with patch.object(deploy,'SOURCE',root):self.assertEqual(deploy.checked_files(),[Path('public.py'),Path('distribution-files.json')])
    def test_changed_source_refuses_deployment(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'public.py').write_text('changed');(root/'distribution-files.json').write_text(json.dumps({'schema':1,'files':{'public.py':'a'*64}}))
            with patch.object(deploy,'SOURCE',root),self.assertRaises(deploy.Failure):deploy.checked_files()
    def test_distribution_path_traversal_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'distribution-files.json').write_text(json.dumps({'schema':1,'files':{'../secret':'a'*64}}))
            with patch.object(deploy,'SOURCE',root),self.assertRaises(deploy.Failure):deploy.checked_files()
    def test_403_not_treated_as_absent_repository(self):
        with patch.object(deploy,'api',side_effect=deploy.Failure('HTTP 403')),self.assertRaises(deploy.Failure):deploy.exists()
    def test_404_is_absent_repository(self):
        with patch.object(deploy,'api',side_effect=deploy.Failure('HTTP 404')):self.assertFalse(deploy.exists())
    def test_existing_private_repo_is_not_converted(self):
        with patch.object(deploy,'api',return_value={'full_name':deploy.REPO,'private':True}),self.assertRaises(deploy.Failure):deploy.exists()
    def test_configuration_helper_does_not_mutate_server_settings(self):
        report={}
        with patch.object(deploy,'api') as api:deploy.configure(report);api.assert_not_called()
        self.assertEqual(report['settings'],'manual-verification-required')
    def test_git_does_not_persist_credentials(self):
        with patch.object(deploy,'command',return_value='') as command:
            deploy.git(['push','origin','main'],Path('/work'));args=command.call_args.args[0]
            self.assertIn('credential.helper=',args);self.assertIn('credential.https://github.com.helper=!gh auth git-credential',args);self.assertNotIn('--global',args)
