# SPDX-License-Identifier: BSD-2-Clause
import tarfile
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from intelbrew.core import Error, check_bottle, digest, write_json_new, load_config
from intelbrew.ci import extract_bottle_metadata, validate_candidate
from helpers import G, archive

class ArchiveTests(unittest.TestCase):
    def test_required_license_notice_matches_exact_bytes(self):
        notice = b'Copyright example; all license conditions and disclaimers\n'
        expected = {'LICENSE': hashlib.sha256(notice).hexdigest()}
        with tempfile.TemporaryDirectory() as d:
            entry = tarfile.TarInfo('tool/1.0/LICENSE'); entry.size = len(notice)
            p, r = archive(Path(d), extra=(entry, notice))
            check_bottle(p, r, license_notices=expected)
            with self.assertRaises(Error):
                check_bottle(p, r, license_notices={'LICENSE': 'a' * 64})
        with tempfile.TemporaryDirectory() as d:
            p, r = archive(Path(d))
            with self.assertRaises(Error):
                check_bottle(p, r, license_notices=expected)

    def test_bottle_json_keeps_cellar_at_bottle_level(self):
        details = {
            'tool': {
                'bottle': {
                    'cellar': 'any_skip_relocation',
                    'tags': {'sequoia': {'sha256': 'a' * 64}},
                },
            },
        }
        self.assertEqual(extract_bottle_metadata(details, 'sequoia'),
                         ('a' * 64, 'any_skip_relocation'))

    def test_valid_native_identity_archive(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=archive(Path(d));check_bottle(p,r)
    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            entry=tarfile.TarInfo('tool/1.0/../../bad');entry.size=1
            p,r=archive(Path(d),extra=(entry,b'x'))
            with self.assertRaises(Error):check_bottle(p,r)
    def test_absolute_member_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            entry=tarfile.TarInfo('/tmp/bad');entry.size=1
            p,r=archive(Path(d),extra=(entry,b'x'))
            with self.assertRaises(Error):check_bottle(p,r)
    def test_outside_keg_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            entry=tarfile.TarInfo('other/1.0/bin/bad');entry.size=1
            p,r=archive(Path(d),extra=(entry,b'x'))
            with self.assertRaises(Error):check_bottle(p,r)
    def test_duplicate_recipe_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            entry=tarfile.TarInfo('tool/1.0/.brew/tool.rb');entry.size=1
            p,r=archive(Path(d),extra=(entry,b'x'))
            with self.assertRaises(Error):check_bottle(p,r)
    def test_in_keg_hardlink_to_previous_regular_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            entry=tarfile.TarInfo('tool/1.0/bin/tool-alias');entry.type=tarfile.LNKTYPE
            entry.linkname='tool/1.0/bin/tool'
            p,r=archive(Path(d),extra=(entry,None));check_bottle(p,r)
    def test_system_tar_hardlink_roundtrip(self):
        import os,subprocess
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);p,r=archive(folder);tree=folder/'tree';tree.mkdir()
            with tarfile.open(p) as source:source.extractall(tree)  # Only our own fixed fixture.
            original=tree/'tool/1.0/bin/tool';alias=tree/'tool/1.0/bin/tool-alias';os.link(original,alias)
            subprocess.run(['tar','-czf',str(p),'-C',str(tree),'tool'],check=True,
                           env={**os.environ, 'COPYFILE_DISABLE': '1'})
            r.update(sha256=digest(p),size=p.stat().st_size)
            with tarfile.open(p) as source:self.assertTrue(any(member.islnk() for member in source))
            check_bottle(p,r)
            poured=folder/'poured';poured.mkdir()
            subprocess.run(['tar','-xzf',str(p),'-C',str(poured)],check=True)
            self.assertEqual((poured/'tool/1.0/bin/tool').stat().st_ino,
                             (poured/'tool/1.0/bin/tool-alias').stat().st_ino)
    def test_unsafe_or_unresolved_hardlink_targets_are_rejected(self):
        for target in ['/tmp/outside','other/1.0/bin/tool','tool/1.0/../bin/tool',
                       'tool/1.0/missing','tool/1.0/bin/tool-alias']:
            with self.subTest(target=target),tempfile.TemporaryDirectory() as d:
                entry=tarfile.TarInfo('tool/1.0/bin/tool-alias');entry.type=tarfile.LNKTYPE;entry.linkname=target
                p,r=archive(Path(d),extra=(entry,None))
                with self.assertRaises(Error):check_bottle(p,r)
    def test_special_file_rejected(self):
        for kind in [tarfile.FIFOTYPE,tarfile.CHRTYPE,tarfile.LNKTYPE]:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                entry=tarfile.TarInfo('tool/1.0/special');entry.type=kind
                p,r=archive(Path(d),extra=(entry,None))
                with self.assertRaises(Error):check_bottle(p,r)
    def test_forged_tap_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=archive(Path(d),bad_tab=True)
            with self.assertRaises(Error):check_bottle(p,r)
    def test_missing_recipe_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=archive(Path(d),omit_recipe=True)
            with self.assertRaises(Error):check_bottle(p,r)
    def test_changed_recipe_digest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=archive(Path(d));r['recipe_sha256']='c'*64
            with self.assertRaises(Error):check_bottle(p,r)
    def test_normalized_duplicate_recipe_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            entry=tarfile.TarInfo('tool/1.0/./.brew/tool.rb');entry.size=1
            p,r=archive(Path(d),extra=(entry,b'x'))
            with self.assertRaisesRegex(Error,'duplicate'):check_bottle(p,r)
    def test_archive_symlink_parent_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            entry=tarfile.TarInfo('tool/1.0/bin');entry.type=tarfile.SYMTYPE;entry.linkname='/tmp/outside'
            p,r=archive(Path(d),extra=(entry,None))
            with self.assertRaisesRegex(Error,'beneath'):check_bottle(p,r)
    def test_changed_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=archive(Path(d));p.write_bytes(p.read_bytes()+b'x')
            with self.assertRaises(Error):check_bottle(p,r)
    def test_archive_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=archive(Path(d));link=Path(d)/'link';link.symlink_to(p)
            with self.assertRaises(Error):check_bottle(link,r)
    def candidate(self, folder, verified=True):
        p,r=archive(folder)
        source=folder/r['source']['filename'];source.write_bytes(b'source fixture')
        r['source']['sha256']=digest(source);r['source']['size']=source.stat().st_size
        m=dict(schema=1,root='tool',core_commit=G,brew_commit=G,workflow_commit=G,
               plan={'schema':1,'roots':['tool'],'order':[],'nodes':{}},packages=[r],verified=verified)
        write_json_new(folder/'manifest.json',m)
        return m
    def test_valid_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            self.candidate(Path(d));m=validate_candidate(Path(d),expected_root='tool',verified=True)
            self.assertEqual(len(m['packages']),1)
    def test_candidate_validation_enforces_configured_notice_before_publication(self):
        config=load_config()
        config['required_license_notices']={'tool': {'LICENSE': 'a' * 64}}
        for verified in (False, True):
            with self.subTest(verified=verified), tempfile.TemporaryDirectory() as d:
                self.candidate(Path(d), verified)
                with patch('intelbrew.ci.load_config', return_value=config):
                    with self.assertRaisesRegex(Error, 'license notice missing'):
                        validate_candidate(Path(d),expected_root='tool',verified=verified)
    def test_unverified_candidate_not_published(self):
        with tempfile.TemporaryDirectory() as d:
            self.candidate(Path(d),False)
            with self.assertRaises(Error):validate_candidate(Path(d),expected_root='tool',verified=True)
    def test_unexpected_executable_not_published(self):
        with tempfile.TemporaryDirectory() as d:
            self.candidate(Path(d));(Path(d)/'run.sh').write_text('evil')
            with self.assertRaises(Error):validate_candidate(Path(d),expected_root='tool',verified=True)
    def test_wrong_root_not_published(self):
        with tempfile.TemporaryDirectory() as d:
            self.candidate(Path(d))
            with self.assertRaises(Error):validate_candidate(Path(d),expected_root='other',verified=True)

    def test_candidate_validation_accepts_registry_commit(self):
        with tempfile.TemporaryDirectory() as d:
            m = self.candidate(Path(d))
            m['registry_commit'] = 'a' * 40
            (Path(d) / 'manifest.json').write_text(__import__('json').dumps(m) + '\n')
            validated = validate_candidate(Path(d), expected_root='tool', verified=True)
            self.assertEqual(validated['registry_commit'], 'a' * 40)

        with tempfile.TemporaryDirectory() as d:
            m = self.candidate(Path(d))
            m['registry_commit'] = 'invalid-sha'
            (Path(d) / 'manifest.json').write_text(__import__('json').dumps(m) + '\n')
            with self.assertRaises(Error):
                validate_candidate(Path(d), expected_root='tool', verified=True)
