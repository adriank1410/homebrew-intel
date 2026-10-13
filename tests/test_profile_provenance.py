# SPDX-License-Identifier: BSD-2-Clause
"""The approved LLVM variant must retain canonical and executed recipe proof."""
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import G, meta, record
from intelbrew.core import (Error, Planner, blocked_toolchain_source, load_config,
                            source_build_blocklist, validate_record)
from intelbrew.ci import _validate_source_bundle, source_bundle, build, validate_candidate


class ProfileProvenanceTests(unittest.TestCase):
    def test_published_profile_bottle_unblocks_rust_without_source_llvm(self):
        config = load_config()
        metadata = {'llvm': meta('llvm'), 'rust': meta('rust', runtime=['llvm'])}
        records = {'llvm': record('llvm', build_profile='llvm-no-pgo')}
        plan = Planner(lambda names: {name: metadata[name] for name in names}, records,
                       build=True, blocked=source_build_blocklist(config)).make(['rust'])
        self.assertEqual(plan['nodes']['llvm']['provider'], 'personal')
        self.assertEqual(plan['nodes']['rust']['provider'], 'build')
        self.assertIsNone(blocked_toolchain_source(plan, config['blocked_source_builds']))

    def test_config_accepts_only_the_approved_profile_mapping(self):
        config = load_config()
        for value in ([], None, {'rust': 'llvm-no-pgo'}, {'llvm': 'unknown'},
                      {'llvm': 'llvm-no-pgo', 'rust': 'llvm-no-pgo'}):
            with self.subTest(profiles=value), patch('intelbrew.core.read_json',
                    return_value={**config, 'build_profiles': value}), self.assertRaises(Error):
                load_config()

    def test_build_executes_variant_restores_core_and_validates_embedded_recipe(self):
        # Native Homebrew and subprocesses are I/O boundaries. The actual
        # planner, profile lifecycle, source packaging and candidate checks run.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            item, sources, _ = self.fixture(folder)
            original_path = Path(sources['formula_path'])
            original = original_path.read_bytes()
            sources.pop('build_profile'); sources.pop('profile_recipe_path')
            from intelbrew.build_profiles import recipe_sha256
            sources['recipe_sha256'] = recipe_sha256(original)
            cache = folder / 'homebrew-cache'
            context = {'homebrew_cache': str(cache), 'go_mod_cache': str(cache / 'go_mod_cache'),
                       'cargo_cache': str(cache / 'cargo_cache')}
            for value in context.values(): Path(value).mkdir(parents=True, exist_ok=True)
            executed = []

            def native_io(request, **kwargs):
                if request['mode'] == 'inspect': return {'llvm': item}
                if request['mode'] == 'sources': return sources
                if request['mode'] == 'build-context': return context
                raise AssertionError(request)

            def brew_io(args, **kwargs):
                if args[1:2] == ['install']:
                    self.assertIn('--build-bottle', args)
                    executed.append(original_path.read_bytes())
                    self.assertIn(b'pgo_build = false', executed[-1])
                if args[1:2] == ['bottle']:
                    self.assertEqual(original_path.read_bytes(), original)
                    cwd = Path(kwargs['cwd'])
                    bottle = cwd / 'llvm--1.0.sequoia.bottle.tar.gz'
                    entries = [('llvm/1.0/.brew/llvm.rb', executed[-1]),
                               ('llvm/1.0/INSTALL_RECEIPT.json', json.dumps({
                                   'source': {'tap': 'homebrew/core'}, 'built_as_bottle': True}).encode())]
                    with tarfile.open(bottle, 'w:gz') as archive:
                        for name, data in entries:
                            member = tarfile.TarInfo(name); member.size = len(data)
                            archive.addfile(member, io.BytesIO(data))
                    (cwd / 'llvm.bottle.json').write_text(json.dumps({'formula': {'bottle': {
                        'cellar': ':any_skip_relocation', 'tags': {'sequoia': {
                            'sha256': hashlib.sha256(bottle.read_bytes()).hexdigest()}}}}}))
                return ''

            env = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                   'RUNNER_OS': 'macOS', 'RUNNER_ARCH': 'X64', 'RUNNER_TEMP': directory,
                   'INTELBREW_CORE_COMMIT': G, 'GITHUB_SHA': G, 'GITHUB_RUN_ID': '123'}
            with patch.dict(os.environ, env), patch('intelbrew.ci.native', side_effect=native_io), \
                 patch('intelbrew.ci.run', side_effect=brew_io), \
                 patch('intelbrew.ci.sync_registry', return_value=G), \
                 patch('intelbrew.ci.registry', return_value={}):
                output = folder / 'candidate'
                build('llvm', output)
            manifest = validate_candidate(output, expected_root='llvm', verified=False)
            rec = manifest['packages'][0]
            self.assertEqual(rec['build_profile'], 'llvm-no-pgo')
            self.assertEqual(rec['recipe_sha256'], hashlib.sha256(executed[0]).hexdigest())
            self.assertEqual(rec['formula_sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual(original_path.read_bytes(), original)

    def test_record_accepts_only_the_explicit_llvm_profile(self):
        item = record("llvm", build_profile="llvm-no-pgo")
        self.assertIs(validate_record(item), item)
        for name, profile in (("rust", "llvm-no-pgo"), ("llvm", "unknown"),
                              ("llvm", True), ("llvm", None)):
            with self.subTest(name=name, profile=profile), self.assertRaises(Error):
                validate_record(record(name, build_profile=profile))

    def fixture(self, folder):
        from intelbrew.build_profiles import variant_recipe, recipe_sha256
        upstream = (b'class Llvm < Formula\n  def install\n'
                    b'    pgo_build = build.stable? && build.bottle? && OS.mac? && !versioned_formula?\n'
                    b'    system "cmake", "--build", "stage2", "--target", "check-clang", "check-llvm" if pgo_build\n'
                    b'  end\nend\n')
        original = folder / 'llvm-core.rb'; original.write_bytes(upstream)
        variant = folder / 'llvm-no-pgo.rb'; variant.write_bytes(variant_recipe(upstream))
        source = folder / 'llvm-source.tar.gz'
        with tarfile.open(source, 'w:gz') as archive:
            data = b'LLVM source license notice'
            member = tarfile.TarInfo('llvm/LICENSE.TXT'); member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        sha = hashlib.sha256(upstream).hexdigest()
        item = meta('llvm', license='Apache-2.0 WITH LLVM-exception', formula_sha256=sha)
        sources = {'formula_path': str(original), 'formula_sha256': sha,
                   'recipe_sha256': recipe_sha256(variant.read_bytes()),
                   'build_profile': 'llvm-no-pgo', 'profile_recipe_path': str(variant),
                   'resources': [{'label': 'main', 'path': str(source),
                                  'url': 'https://example.test/llvm-source.tar.gz',
                                  'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}
        rec = record('llvm', formula_sha256=sha, recipe_sha256=sources['recipe_sha256'],
                     build_profile='llvm-no-pgo', license=item['license'])
        return item, sources, rec

    def bundle(self, folder):
        item, sources, rec = self.fixture(folder)
        bundle = source_bundle('llvm', item, sources, folder, G, G,
                               requirements=('Apache-2.0 WITH LLVM-exception',))
        return bundle, rec

    def replace_member(self, bundle, name, data):
        with tarfile.open(bundle, 'r:gz') as archive:
            entries = [(member, archive.extractfile(member).read()) for member in archive]
        with tarfile.open(bundle, 'w:gz') as archive:
            for member, content in entries:
                if member.name == name:
                    content = data; member.size = len(data)
                archive.addfile(member, io.BytesIO(content))

    def test_bundle_preserves_both_recipes_and_exact_profile_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, rec = self.bundle(Path(directory))
            with tarfile.open(bundle) as archive:
                index = json.load(archive.extractfile('sources.json'))
                self.assertEqual(index['build_profile'], 'llvm-no-pgo')
                original = archive.extractfile('recipe/llvm.rb').read()
                variant = archive.extractfile('recipe/llvm-no-pgo.rb').read()
                from intelbrew.build_profiles import variant_recipe
                self.assertEqual(variant, variant_recipe(original))
                instructions = archive.extractfile('BUILDING.txt').read().decode()
                self.assertIn('llvm-no-pgo', instructions)
                self.assertIn('recipe/llvm-no-pgo.rb', instructions)
            _validate_source_bundle(bundle, rec, load_config())

    def test_validation_rejects_any_other_change_to_the_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, rec = self.bundle(Path(directory))
            self.replace_member(bundle, 'recipe/llvm-no-pgo.rb', b'unsafe arbitrary recipe')
            with self.assertRaisesRegex(Error, 'profile|variant'):
                _validate_source_bundle(bundle, rec, load_config())

    def test_validation_rejects_an_unlabelled_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, rec = self.bundle(Path(directory))
            del rec['build_profile']
            with self.assertRaises(Error):
                _validate_source_bundle(bundle, rec, load_config())

    def test_validation_rejects_a_wrong_executed_recipe_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, rec = self.bundle(Path(directory))
            rec['recipe_sha256'] = '0' * 64
            with self.assertRaisesRegex(Error, 'profile|variant|recipe'):
                _validate_source_bundle(bundle, rec, load_config())


if __name__ == '__main__':
    unittest.main()
