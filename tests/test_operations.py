# SPDX-License-Identifier: BSD-2-Clause
import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from intelbrew.cli import apply_plan, attest
from intelbrew.ci import allowed_redistribution, permissive_license, require_ci_mac, runtime_closure
from intelbrew.core import ROOT, Error, Planner, load_config
from intelbrew.publish import release_tag
from helpers import G, H, meta, record

class OperationTests(unittest.TestCase):
    def plan(self,m,records=None):
        return Planner(lambda names:{n:copy.deepcopy(m[n]) for n in names}, records or {}).make(['tool'])
    def test_missing_package_prevents_every_install(self):
        p=self.plan({'tool':meta(runtime=['dep'],official=True),'dep':meta('dep')})
        with tempfile.TemporaryDirectory() as d,patch('intelbrew.cli.native') as native:
            with self.assertRaises(Error):apply_plan(p,{},load_config(),cache=Path(d)/'cache')
            native.assert_not_called()
    def test_attestation_failure_precedes_installs(self):
        r={'tool':record()};p=self.plan({'tool':meta()},r)
        with tempfile.TemporaryDirectory() as d,patch('intelbrew.cli.native') as native, patch('intelbrew.cli.download'),patch('intelbrew.cli.attest',side_effect=Error('bad signature')):
            with self.assertRaisesRegex(Error,'signature'):apply_plan(p,r,load_config(),cache=Path(d)/'cache')
            native.assert_not_called()
    def test_metadata_race_prevents_install(self):
        m=meta(official=True);p=self.plan({'tool':m});changed=copy.deepcopy(m);changed['formula_sha256']='c'*64
        with tempfile.TemporaryDirectory() as d,patch('intelbrew.cli.run'), patch('intelbrew.cli.native',return_value={'tool':changed}) as native:
            with self.assertRaisesRegex(Error,'state changed'):apply_plan(p,{},load_config(),cache=Path(d)/'cache')
            self.assertEqual(native.call_count,1)
    def test_already_installed_performs_no_install(self):
        m=meta(installed=True);p=self.plan({'tool':m})
        with tempfile.TemporaryDirectory() as d,patch('intelbrew.cli.native',return_value={'tool':m}) as native:
            apply_plan(p,{},load_config(),cache=Path(d)/'cache');self.assertEqual(native.call_count,1)
    def test_partial_failure_is_reported_not_rolled_back(self):
        nodes={'tool':meta(runtime=['dep'],official=True),'dep':meta('dep',official=True)};p=self.plan(nodes)
        def action(request,**kwargs):
            if request['mode']=='inspect':return nodes
            if request['mode']=='receipt':return {'tap':'homebrew/core','poured_from_bottle':True}
            if request['mode']=='install' and request['name']=='tool':raise Error('network failure')
        with tempfile.TemporaryDirectory() as d,patch('intelbrew.cli.run'),patch('intelbrew.cli.native',side_effect=action):
            with self.assertRaisesRegex(Error,'Stopped after 1 package'):apply_plan(p,{},load_config(),cache=Path(d)/'cache')
    def test_attestation_identity_pinned(self):
        with patch('intelbrew.cli.shutil.which',return_value='/bin/gh'),patch('intelbrew.cli.run') as run:
            attest(Path('/file'), 'adriank1410/homebrew-intel',G);args=run.call_args.args[0]
            self.assertIn('--deny-self-hosted-runners',args);self.assertIn('refs/heads/main',args);self.assertIn(G,args)
    def test_no_attestation_bypass_without_gh(self):
        with patch('intelbrew.cli.shutil.which',return_value=None),self.assertRaises(Error):attest(Path('/file'),'adriank1410/homebrew-intel',G)
    def test_ci_refuses_normal_machine(self):
        with patch.dict(os.environ,{},clear=True),self.assertRaises(Error):require_ci_mac()
    def test_ci_refuses_self_hosted(self):
        env={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'self-hosted','RUNNER_OS':'macOS','RUNNER_ARCH':'X64'}
        with patch.dict(os.environ,env,clear=True),self.assertRaises(Error):require_ci_mac()
    def test_ci_accepts_correct_runner_identity(self):
        env={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'macOS','RUNNER_ARCH':'X64','INTELBREW_CORE_COMMIT':G,'GITHUB_SHA':G}
        with patch.dict(os.environ,env,clear=True):require_ci_mac()
    def test_permissive_declared_license(self):
        for license in ['MIT','BSD-2-Clause','MIT OR Apache-2.0']:allowed_redistribution(meta(license=license),load_config())
    def test_unknown_or_copyleft_requires_review(self):
        for license in [None,'GPL-3.0-only','LGPL-2.1-or-later','MPL-2.0',{'any_of':['MIT','GPL-3.0-only']}]:
            with self.subTest(license=license),self.assertRaises(Error):allowed_redistribution(meta(license=license),load_config())
    def test_license_review_exact_hash(self):
        cfg=load_config();cfg['redistribution_exceptions']['tool']={'formula_sha256':H,'review':'Explicit upstream distribution obligations reviewed for this exact recipe.'}
        allowed_redistribution(meta(license='GPL-3.0-only'),cfg)
        with self.assertRaises(Error):allowed_redistribution(meta(license='GPL-3.0-only',formula_sha256='c'*64),cfg)
    def test_runtime_closure_excludes_build_and_test(self):
        nodes={'tool':meta(runtime=['dep'],build=['cmake'],test=['check']),'dep':meta('dep',runtime=['lib']),'lib':meta('lib')}
        self.assertEqual(runtime_closure('tool',nodes),['dep','lib'])
    def test_release_name_no_mutable_latest(self):
        self.assertEqual(release_tag('python@3.14','123','2'),'intel-123-2-python-at-3.14')
        for root,run_id,attempt in [('foo','bad','2'),('../foo','123','1'),('foo','123','')]:
            with self.assertRaises(Error):release_tag(root,run_id,attempt)
    def test_ruby_guard_blocks_before_original_build(self):
        script='''require ARGV.fetch(0)\nclass InstallerFixture\n  def build; raise "ORIGINAL BUILD WAS EXECUTED"; end\nend\nInstallerFixture.prepend(IntelbrewBottleOnly)\nbegin\n  InstallerFixture.new.build\n  abort "guard absent"\nrescue IntelbrewBottleOnly::SourceBuildRefused\n  puts "source blocked"\nend\n'''
        result=subprocess.run(['ruby','-e',script,str(ROOT/'libexec/bottle_only.rb')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'source blocked')

class LicenseFormatTests(unittest.TestCase):
    def test_real_simdutf_dual_license(self):
        allowed_redistribution(meta(license={'any_of':['Apache-2.0','MIT']}),load_config())
    def test_nested_all_of_and_any_of(self):
        allowed_redistribution(meta(license={'all_of':['BSD-2-Clause',{'any_of':['MIT','ISC']}]}),load_config())
    def test_invalid_or_unreviewed_license_expressions(self):
        expressions=['MIT OR','AND MIT','MIT MIT','(MIT','MIT)','MIT | ISC','MIT WITH unknown','',{}, {'any_of':[]},{'any_of':'MIT'},{'any_of':['MIT','GPL-3.0-only']},{'unknown':['MIT']},{'any_of':['MIT'],'extra':[]},None,3,True]
        for expression in expressions:
            with self.subTest(expression=expression):self.assertFalse(permissive_license(expression,set(load_config()['permissive_license_tokens'])))
    def test_parenthesized_spdx_string(self):
        self.assertTrue(permissive_license('MIT OR (BSD-2-Clause AND Apache-2.0)',set(load_config()['permissive_license_tokens'])))
    def test_nesting_is_bounded(self):
        expression='MIT'
        for _ in range(14):expression={'all_of':[expression]}
        self.assertFalse(permissive_license(expression,{'MIT'}))
