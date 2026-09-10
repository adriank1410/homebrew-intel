# SPDX-License-Identifier: BSD-2-Clause
import copy, os, random, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from intelbrew.core import (Error, Planner, artifact_url, basename, brew_env, canonical_name,
                            download, ensure_complete, matching_record, read_json, require_sha,
                            validate_record, write_json_new, native)
from helpers import G,H,meta,record

class ValidationTests(unittest.TestCase):
    def test_native_can_use_an_isolated_build_cache_without_changing_process_env(self):
        with patch.dict(os.environ, {"HOMEBREW_CACHE": "/original-cache"}), patch('intelbrew.core.run', return_value='{}') as call:
            native({"mode":"build-context"}, ci=True, cache=Path('/isolated-cache'))
            self.assertEqual(call.call_args.kwargs['env']['HOMEBREW_CACHE'], '/isolated-cache')
            self.assertEqual(os.environ['HOMEBREW_CACHE'], '/original-cache')
    def test_canonical_names(self):
        for n in ['openssl@3','c++','python@3.14','foo-bar','gtk+3']:self.assertEqual(canonical_name('homebrew/core/'+n),n)
    def test_name_injection_rejected(self):
        for n in ['../foo','evil/tap/foo','--help','foo;id','foo\nbar','a..b','','A','foo@x']:
            with self.subTest(n=n),self.assertRaises(Error):canonical_name(n)
    def test_basename_rejects_paths(self):
        for n in ['../foo','/tmp/a','foo/bar','-bad\n','a\\b','.',None]:
            with self.subTest(n=n),self.assertRaises(Error):basename(n)
    def test_sha_lengths(self):
        self.assertEqual(require_sha(H),H);self.assertEqual(require_sha(G,git=True),G)
        for bad in [None,'a'*63,'g'*64,123]:
            with self.assertRaises(Error):require_sha(bad)
    def test_valid_record(self):self.assertEqual(validate_record(record())['name'],'tool')
    def test_exact_record_schema(self):
        r=record();r['unknown']=True
        with self.assertRaises(Error):validate_record(r)
    def test_wrong_target_rejected(self):
        for f,v in [('arch','arm64'),('tag','tahoe'),('cellar','/opt/homebrew/Cellar')]:
            with self.assertRaises(Error):validate_record(record(**{f:v}))
    def test_revision_consistency(self):
        with self.assertRaises(Error):validate_record(record(revision=1))
        validate_record(record(revision=1,pkg_version='1.0_1',filename='tool--1.0_1.sequoia.bottle.tar.gz'))
    def test_published_release_required(self):
        with self.assertRaises(Error):validate_record(record(release=None))
        validate_record(record(release=None),published=False)
    def test_matching_record(self):
        self.assertIsNotNone(matching_record(meta(),{'tool':record()}));self.assertIsNone(matching_record(meta(formula_sha256='c'*64),{'tool':record()}))
    def test_artifact_url_identity(self):
        self.assertIn('/releases/download/',artifact_url('adriank1410/homebrew-intel',record()))
        with self.assertRaises(Error):artifact_url('evil/repo',record())
    def test_safe_new_json(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x';write_json_new(p,{'a':1})
            with self.assertRaises(FileExistsError):write_json_new(p,{'a':2})
            self.assertEqual(read_json(p),{'a':1})
    def test_json_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x';write_json_new(p,{});q=Path(d)/'q';q.symlink_to(p)
            with self.assertRaises(Error):read_json(q)
    def test_bad_cache_retained(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'b';p.write_bytes(b'keep me')
            with self.assertRaises(Error):download('https://github.com/a/b',p,H,7)
            self.assertEqual(p.read_bytes(),b'keep me')
    def test_external_download_rejected(self):
        for u in ['http://github.com/a','https://evil.test/a','https://u:p@github.com/a']:
            with self.assertRaises(Error):download(u,Path('/unused'),H,100)
    def test_env_strips_tokens(self):
        with patch.dict(os.environ,{'GH_TOKEN':'x','GITHUB_TOKEN':'x','RUBYOPT':'bad','HOMEBREW_CORE_GIT_REMOTE':'unchanged'}):
            e=brew_env();self.assertNotIn('GH_TOKEN',e);self.assertNotIn('RUBYOPT',e);self.assertEqual(e['HOMEBREW_CORE_GIT_REMOTE'],'unchanged')

class PlannerTests(unittest.TestCase):
    def plan(self,nodes,roots=('tool',),records=None,**kwargs):return Planner(lambda names:{n:copy.deepcopy(nodes[n]) for n in names},records or {},**kwargs).make(roots)
    def test_official_older_bottle(self):self.assertEqual(self.plan({'tool':meta(official=True)})['nodes']['tool']['provider'],'official')
    def test_installed_current(self):self.assertEqual(self.plan({'tool':meta(installed=True)})['nodes']['tool']['provider'],'installed')
    def test_missing_no_compile(self):
        p=self.plan({'tool':meta()});self.assertEqual(p['nodes']['tool']['provider'],'missing')
        with self.assertRaises(Error):ensure_complete(p)
    def test_personal_match(self):self.assertEqual(self.plan({'tool':meta()},records={'tool':record()})['nodes']['tool']['provider'],'personal')
    def test_build_edges_only_for_source(self):
        p=self.plan({'tool':meta(official=True,build=['x'],test=['y'])});self.assertEqual(p['order'],['tool'])
        p=self.plan({'tool':meta(build=['x']),'x':meta('x',official=True)},build=True);self.assertEqual(p['order'],['x','tool'])
    def test_runtime_dependency_order(self):
        p=self.plan({'tool':meta(runtime=['dep'],official=True),'dep':meta('dep',official=True)});self.assertEqual(p['order'],['dep','tool'])
    def test_pinned_upgrade_blocked(self):
        with self.assertRaises(Error):self.plan({'tool':meta(pinned=True)})
    def test_foreign_options_head_newer_disabled_blocked(self):
        for f,v in [('foreign_install',True),('installed_options',['--x']),('installed_head',True),('installed_newer',True),('disabled',True)]:
            with self.subTest(f=f),self.assertRaises(Error):self.plan({'tool':meta(**{f:v})})
    def test_cycle_detected(self):
        with self.assertRaisesRegex(Error,'cycle'):self.plan({'tool':meta(runtime=['dep'],official=True),'dep':meta('dep',runtime=['tool'],official=True)})
    def test_graph_budget(self):
        with self.assertRaisesRegex(Error,'exceeds'):self.plan({'tool':meta(runtime=['dep'],official=True),'dep':meta('dep',official=True)},max_nodes=1)
    def test_blocked_heavy(self):
        with self.assertRaisesRegex(Error,'excluded'):self.plan({'tool':meta()},build=True,blocked=['tool'])
    def test_vcs_source_build_is_rejected_before_dependencies_are_inspected(self):
        calls=[]
        def inspect(names):
            calls.append(list(names))
            return {name: copy.deepcopy({'tool':meta('tool',build=['dep'],vcs_source=True),
                                         'dep':meta('dep')}[name]) for name in names}
        with self.assertRaisesRegex(Error,'VCS source needs review: tool'):
            Planner(inspect,{},build=True).make(['tool'])
        self.assertEqual(calls,[['tool']])
    def test_vcs_source_metadata_does_not_block_binary_providers(self):
        official=self.plan({'tool':meta(official=True,vcs_source=True)},build=True)
        personal=self.plan({'tool':meta(vcs_source=True)},records={'tool':record()},build=True)
        self.assertEqual(official['nodes']['tool']['provider'],'official')
        self.assertEqual(personal['nodes']['tool']['provider'],'personal')
    def test_pinned_git_source_can_be_built(self):
        result=self.plan({'tool':meta(vcs_source=True,pinned_git_source=True)},build=True)
        self.assertEqual(result['nodes']['tool']['provider'],'build')
    def test_string_pinned_git_flag_cannot_enable_source_build(self):
        with self.assertRaisesRegex(Error,'VCS source needs review'):
            self.plan({'tool':meta(vcs_source=True,pinned_git_source='true')},build=True)
    def test_alias_not_accepted(self):
        with self.assertRaises(Error):self.plan({'tool':meta('canonical')})
    def test_root_dedup(self):self.assertEqual(self.plan({'tool':meta(official=True)},roots=['tool','homebrew/core/tool'])['roots'],['tool'])
    def test_personal_runtime_drift(self):
        r=record(runtime_dependencies=[dict(name='dep',pkg_version='0.9',formula_sha256=H)])
        with self.assertRaisesRegex(Error,'drift'):self.plan({'tool':meta(runtime=['dep']),'dep':meta('dep',official=True)},records={'tool':r})
    def test_ci_replans_stale_personal(self):
        r=record(runtime_dependencies=[dict(name='dep',pkg_version='0.9',formula_sha256=H)])
        nodes={'tool':meta(runtime=['dep'],build=['cmake']),'dep':meta('dep',official=True),'cmake':meta('cmake',official=True)}
        p=self.plan(nodes,records={'tool':r},build=True);self.assertEqual(p['nodes']['tool']['provider'],'build');self.assertIn('cmake',p['order'])
    def test_random_dags_dependency_first(self):
        rng=random.Random(124718)
        for count in range(2,52):
            nodes={}
            for i in range(count):nodes[f'p{i}']=meta(f'p{i}',runtime=[f'p{j}' for j in range(i) if rng.random()<.3],official=True)
            p=self.plan(nodes,roots=list(nodes));pos={n:i for i,n in enumerate(p['order'])}
            for n,m in nodes.items():
                for dep in m['runtime']:self.assertLess(pos[dep],pos[n])
