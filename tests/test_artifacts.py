# SPDX-License-Identifier: BSD-2-Clause
import tarfile
import tempfile
import unittest
from pathlib import Path
from intelbrew.core import Error, check_bottle, digest, write_json_new
from intelbrew.ci import extract_bottle_metadata, validate_candidate
from helpers import G, archive

class ArchiveTests(unittest.TestCase):
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
