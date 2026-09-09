# SPDX-License-Identifier: BSD-2-Clause
import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from intelbrew.cli import apply_plan, attest
from intelbrew.ci import (_build_env, _prepare_source_sets, _validate_source_bundle, allowed_redistribution, permissive_license,
                         require_ci_mac, runtime_closure, source_bundle)
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

    def test_attestation_token_is_scoped_to_subprocess_environment(self):
        with patch('intelbrew.cli.shutil.which',return_value='/bin/gh'), patch('intelbrew.cli.run') as run, patch.dict(os.environ, {}, clear=True):
            attest(Path('/file'), 'adriank1410/homebrew-intel', G, token='secret')
            args, kwargs = run.call_args
            self.assertNotIn('secret', args[0])
            self.assertEqual(kwargs['env']['GH_TOKEN'], 'secret')
            self.assertNotIn('GH_TOKEN', os.environ)
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
    def test_tor_license_combination_including_ncsa_is_allowed(self):
        allowed_redistribution(meta(license={'all_of': ['BSD-2-Clause', 'BSD-3-Clause', 'MIT', 'NCSA']}), load_config())
    def test_source_required_licenses_are_allowed_with_obligations(self):
        for license in ['GPL-3.0-only','LGPL-2.1-or-later','AGPL-3.0-only','MPL-2.0','libtiff','libpng-2.0','GFDL-1.3-no-invariants-only']:
            with self.subTest(license=license):self.assertEqual(allowed_redistribution(meta(license=license),load_config()),(license,))
    def test_any_of_uses_supported_branch_and_all_of_combines_obligations(self):
        cfg=load_config()
        self.assertEqual(allowed_redistribution(meta(license={'any_of':['MIT','GPL-3.0-only']}),cfg),())
        self.assertEqual(allowed_redistribution(meta(license={'all_of':['MIT','GPL-3.0-only','MPL-2.0']}),cfg),('GPL-3.0-only','MPL-2.0'))
    def test_spdx_string_precedence_parentheses_and_exact_with(self):
        cfg=load_config()
        cases={
            'MIT AND GPL-2.0-only':('GPL-2.0-only',),
            'GPL-2.0-only OR GPL-3.0-only':('GPL-2.0-only',),
            'MIT OR GPL-2.0-only AND MPL-2.0':(),
            '(MIT OR GPL-2.0-only) AND MPL-2.0':('MPL-2.0',),
            'Apache-2.0 WITH LLVM-exception':('Apache-2.0 WITH LLVM-exception',),
            '(GPL-2.0-only OR GPL-3.0-only) AND MPL-2.0':('GPL-2.0-only','MPL-2.0'),
        }
        for expression,requirements in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(allowed_redistribution(meta(license=expression),cfg),requirements)
    def test_spdx_string_rejects_unknown_or_malformed_compounds(self):
        cfg=load_config()
        expressions=['MIT AND Unknown','MIT OR','OR MIT','MIT WITH unknown','(MIT OR GPL-2.0-only',
                     'MIT GPL-2.0-only','MIT WITH LLVM-exception WITH LLVM-exception',
                     '(Apache-2.0) WITH LLVM-exception','MIT and GPL-2.0-only',
                     '('*13+'MIT'+')'*13,' OR '.join(['MIT']*129)]
        for expression in expressions:
            with self.subTest(expression=expression),self.assertRaises(Error):
                allowed_redistribution(meta(license=expression),cfg)
    def test_exact_reviewed_license_exception(self):
        expression={'GPL-2.0-only':{'with':'Classpath-exception-2.0'}}
        self.assertEqual(allowed_redistribution(meta(license=expression),load_config()),('GPL-2.0-only WITH Classpath-exception-2.0',))
        for expression in [None,'Proprietary',{'GPL-2.0-only':{'with':'unknown'}},{'all_of':['MIT','Proprietary']}]:
            with self.subTest(license=expression),self.assertRaises(Error):allowed_redistribution(meta(license=expression),load_config())
    def test_remaining_installed_spdx_families_are_source_supported(self):
        cfg=load_config()
        tokens=['AML-glslang','APSL-1.0','BSD-2-Clause-Darwin','BSD-2-Clause-Patent','BSD-2-Clause-Views',
                'BSD-3-Clause-Open-MPI','BSD-4-Clause-UC','EPL-1.0','EUPL-1.2','FSFULLR','GFDL-1.3-only',
                'HPND','HPND-sell-variant','ImageMagick','Info-ZIP','JasPer-2.0','LZMA-SDK-9.22','MIT-CMU',
                'MIT-Khronos-old','MIT-Modern-Variant','NCL','OLDAP-2.8','Ruby','SGI-B-2.0','SSH-OpenSSH',
                'SunPro','TCL','Unicode-DFS-2015','Unicode-TOU','X11-distribute-modifications-variant','public_domain']
        for token in tokens:
            with self.subTest(token=token):self.assertEqual(allowed_redistribution(meta(license=token),cfg),(token,))
        self.assertEqual(allowed_redistribution(meta(license={'Apache-2.0':{'with':'LLVM-exception'}}),cfg),('Apache-2.0 WITH LLVM-exception',))
        for token in ['CC-PDDC','blessing']:
            with self.subTest(token=token):self.assertEqual(allowed_redistribution(meta(license=token),cfg),())
    def test_current_special_recipe_expressions_except_nmap_are_supported(self):
        cfg=load_config()
        expressions=[{'all_of':['BSD-4-Clause-UC','APSL-1.0']},
                     {'all_of':['GFDL-1.3-only','GPL-2.0-only','GPL-3.0-only','LGPL-2.1-only','LGPL-3.0-only']},
                     'Info-ZIP']
        for expression in expressions:
            with self.subTest(expression=expression):self.assertTrue(allowed_redistribution(meta(license=expression),cfg))
        nmap=meta(name='nmap',license='cannot_represent',formula_sha256='d308fc0ef788a969c120462d850cd2f5928ef65cbfd9aba16ac85785f62f66dc')
        self.assertEqual(allowed_redistribution(nmap,cfg),('Nmap-Public-Source-License',))
        for changed in [{**nmap,'name':'other'},{**nmap,'license':'unknown'},{**nmap,'formula_sha256':'c'*64}]:
            with self.assertRaises(Error):allowed_redistribution(changed,cfg)
    def test_license_review_exact_hash(self):
        cfg=load_config();cfg['redistribution_exceptions']['tool']={'formula_sha256':H,'review':'Explicit upstream distribution obligations reviewed for this exact recipe.'}
        allowed_redistribution(meta(license='Proprietary',formula_sha256=H),cfg)
        with self.assertRaises(Error):allowed_redistribution(meta(license='Proprietary',formula_sha256='c'*64),cfg)
    def test_runtime_closure_excludes_build_and_test(self):
        nodes={'tool':meta(runtime=['dep'],build=['cmake'],test=['check']),'dep':meta('dep',runtime=['lib']),'lib':meta('lib')}
        self.assertEqual(runtime_closure('tool',nodes),['dep','lib'])
    def test_source_collection_uses_a_fresh_cache_per_package(self):
        with tempfile.TemporaryDirectory() as d,patch('intelbrew.ci.native',side_effect=lambda request,**kwargs:{'name':request['name']}) as native:
            caches,sources=_prepare_source_sets(['dep','tool'],Path(d))
            self.assertEqual(sources,{'dep':{'name':'dep'},'tool':{'name':'tool'}})
            self.assertNotEqual(caches['dep'],caches['tool'])
            for call,name in zip(native.call_args_list,['dep','tool']):
                self.assertEqual(call.kwargs,{'ci':True,'cache':caches[name]});self.assertTrue(caches[name].is_dir())
                self.assertEqual(_build_env(caches[name])['HOMEBREW_CACHE'],str(caches[name]))
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

class SourceBundleTests(unittest.TestCase):
    def _replace_member(self,bundle,name,replacement):
        with tarfile.open(bundle,'r:gz') as source:
            entries=[(member,source.extractfile(member).read() if member.isfile() else None) for member in source]
        with tarfile.open(bundle,'w:gz') as target:
            for member,data in entries:
                if member.name==name:data=replacement;member.size=len(data)
                target.addfile(member,io.BytesIO(data) if data is not None else None)
    def _fixture(self,folder,*,zip_source=False,notice=True):
        recipe=folder/'tool.rb';recipe.write_text('class Tool < Formula\nend\n')
        source=folder/('source.zip' if zip_source else 'source.tar.gz')
        if zip_source:
            with zipfile.ZipFile(source,'w') as archive:
                archive.writestr('tool/COPYING' if notice else 'tool/main.c',b'license text')
        else:
            with tarfile.open(source,'w:gz') as archive:
                data=b'license text';member=tarfile.TarInfo('tool/COPYING' if notice else 'tool/main.c');member.size=len(data);archive.addfile(member,io.BytesIO(data))
        item=meta(license='GPL-3.0-only',formula_sha256=hashlib.sha256(recipe.read_bytes()).hexdigest())
        sources={'formula_sha256':item['formula_sha256'],'formula_path':str(recipe),'resources':[{'label':'main','url':'https://example.test/source','path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}
        return item,sources
    def test_source_required_bundle_copies_and_validates_notice(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);item,sources=self._fixture(folder)
            patch_file=folder/'fix.patch';patch_file.write_text('raw patch resource')
            sources['resources'].append({'label':'patch-0','url':'https://example.test/fix.patch','path':str(patch_file),'sha256':hashlib.sha256(patch_file.read_bytes()).hexdigest()})
            bundle=source_bundle('tool',item,sources,folder,item['formula_sha256'][:40],G,requirements=('GPL-3.0-only',))
            rec=record(formula_sha256=item['formula_sha256'],core_commit=item['formula_sha256'][:40],license='GPL-3.0-only')
            _validate_source_bundle(bundle,rec,load_config())
            with tarfile.open(bundle) as archive:
                index=json.load(archive.extractfile('sources.json'))
                self.assertEqual(index['license_requirements'],['GPL-3.0-only']);self.assertEqual(len(index['notices']),1)
                self.assertIn(f'https://github.com/Homebrew/brew/tree/{G}',archive.extractfile('BUILDING.txt').read().decode())
            with self.assertRaisesRegex(Error,'identity mismatch'):
                _validate_source_bundle(bundle,{**rec,'core_commit':'b'*40},load_config())
            self._replace_member(bundle,'recipe/tool.rb',b'changed recipe')
            with self.assertRaisesRegex(Error,'recipe checksum'):_validate_source_bundle(bundle,rec,load_config())
    def test_candidate_validation_hashes_indexed_source_archives(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);item,sources=self._fixture(folder)
            bundle=source_bundle('tool',item,sources,folder,item['formula_sha256'][:40],G,requirements=('GPL-3.0-only',))
            rec=record(formula_sha256=item['formula_sha256'],core_commit=item['formula_sha256'][:40],license='GPL-3.0-only')
            self._replace_member(bundle,'inputs/000-source.tar.gz',b'changed source')
            with self.assertRaisesRegex(Error,'resource checksum'):_validate_source_bundle(bundle,rec,load_config())
    def test_candidate_validation_rejects_changed_build_instructions(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);item,sources=self._fixture(folder)
            bundle=source_bundle('tool',item,sources,folder,item['formula_sha256'][:40],G,requirements=('GPL-3.0-only',))
            rec=record(formula_sha256=item['formula_sha256'],core_commit=item['formula_sha256'][:40],license='GPL-3.0-only')
            self._replace_member(bundle,'BUILDING.txt',b'brew install something-else')
            with self.assertRaisesRegex(Error,'instructions mismatch'):_validate_source_bundle(bundle,rec,load_config())
    def test_pinned_nmap_exception_still_requires_a_valid_source_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);item,sources=self._fixture(folder);recipe=folder/'nmap.rb';Path(sources['formula_path']).rename(recipe)
            sha=hashlib.sha256(recipe.read_bytes()).hexdigest();item.update(name='nmap',license='cannot_represent',formula_sha256=sha)
            sources.update(formula_sha256=sha,formula_path=str(recipe))
            cfg=load_config();cfg['source_required_formula_exceptions']['nmap']['formula_sha256']=sha
            requirements=allowed_redistribution(item,cfg)
            bundle=source_bundle('nmap',item,sources,folder,sha[:40],G,requirements=requirements)
            rec=record(name='nmap',formula_sha256=sha,core_commit=sha[:40],license='cannot_represent')
            _validate_source_bundle(bundle,rec,cfg)
    def test_build_input_executable_mode_is_indexed_and_verified(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);item,sources=self._fixture(folder);helper=folder/'helper.sh'
            helper.write_bytes(b'#!/bin/sh\nexit 0\n');helper.chmod(0o755)
            entry={'path':str(helper),'label':'cargo/git/checkouts/repo/rev/helper.sh',
                   'sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),'size':helper.stat().st_size,'mode':0o755}
            bundle=source_bundle('tool',item,sources,folder,item['formula_sha256'][:40],G,
                                 requirements=('GPL-3.0-only',),build_sources=[entry])
            rec=record(formula_sha256=item['formula_sha256'],core_commit=item['formula_sha256'][:40],license='GPL-3.0-only')
            with tarfile.open(bundle) as archive:
                index=json.load(archive.extractfile('sources.json'))
                self.assertEqual(index['build_sources'][0]['mode'],0o755)
                self.assertEqual(archive.getmember(index['build_sources'][0]['filename']).mode,0o755)
            _validate_source_bundle(bundle,rec,load_config())
            index['build_sources'][0]['mode']=0o644
            self._replace_member(bundle,'sources.json',json.dumps(index).encode())
            with self.assertRaisesRegex(Error,'mode'):_validate_source_bundle(bundle,rec,load_config())
    def test_infozip_build_instructions_mark_the_patched_distribution(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);item,sources=self._fixture(folder);item['license']='Info-ZIP'
            bundle=source_bundle('tool',item,sources,folder,item['formula_sha256'][:40],G,requirements=('Info-ZIP',))
            with tarfile.open(bundle) as archive:
                text=archive.extractfile('BUILDING.txt').read().decode()
            self.assertIn('not an unmodified upstream Info-ZIP release',text)
    def test_source_required_bundle_rejects_missing_notice(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);item,sources=self._fixture(folder,zip_source=True,notice=False)
            with self.assertRaisesRegex(Error,'notice missing'):source_bundle('tool',item,sources,folder,item['formula_sha256'][:40],G,requirements=('GPL-3.0-only',))
    def test_zip_symlink_is_not_accepted_as_notice(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);item,sources=self._fixture(folder,zip_source=True)
            source=Path(sources['resources'][0]['path'])
            with zipfile.ZipFile(source,'w') as archive:
                member=zipfile.ZipInfo('COPYING');member.external_attr=0o120777<<16;archive.writestr(member,b'target')
            sources['resources'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
            with self.assertRaisesRegex(Error,'notice missing'):source_bundle('tool',item,sources,folder,item['formula_sha256'][:40],G,requirements=('GPL-3.0-only',))
