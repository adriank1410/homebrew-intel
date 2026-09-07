# SPDX-License-Identifier: BSD-2-Clause
import hashlib
import io
import json
import tarfile
from pathlib import Path
from intelbrew.core import digest

H = 'a' * 64
G = 'b' * 40

def meta(name='tool', runtime=(), build=(), test=(), official=False, installed=False, **extra):
    result = dict(name=name, tap='homebrew/core', version='1.0', revision=0, version_scheme=0,
                  pkg_version='1.0', formula_sha256=H, license='MIT',
                  official_bottle={'tag': 'sonoma'} if official else None,
                  disabled=False, installed_current=installed, installed_newer=False,
                  installed_options=[], installed_head=False, foreign_install=False, pinned=False,
                  installed_versions=['1.0'] if installed else [], runtime=list(runtime), build=list(build), test=list(test))
    result.update(extra)
    return result


def record(name='tool', **extra):
    result = dict(schema=1, name=name, version='1.0', revision=0, version_scheme=0,
                  pkg_version='1.0', formula_sha256=H, recipe_sha256=H, core_commit=G,
                  brew_commit=G, tag='sequoia', arch='x86_64', cellar='any_skip_relocation',
                  filename=f'{name}--1.0.sequoia.bottle.tar.gz', sha256=H, size=100,
                  license='MIT', runtime_dependencies=[],
                  source={'filename': f'{name}--1.0.sources.tar.gz', 'sha256': H, 'size': 100},
                  release='intel-123-1-tool', workflow_commit=G, run_id='123')
    result.update(extra)
    return result


def archive(folder: Path, *, extra=None, bad_tab=False, omit_recipe=False):
    item = record(release=None)
    recipe = b'class Tool < Formula\n  def install\n    system "make"\n  end\nend\n'
    item['recipe_sha256'] = hashlib.sha256(recipe).hexdigest()
    path = folder / item['filename']
    with tarfile.open(path, 'w:gz') as tar:
        entries = [('tool/1.0/INSTALL_RECEIPT.json', json.dumps({'source': {'tap': 'evil/tap' if bad_tab else 'homebrew/core'}, 'built_as_bottle': True}).encode()),
                   ('tool/1.0/bin/tool', b'fake binary for validation test, never executed')]
        if not omit_recipe:
            entries.append(('tool/1.0/.brew/tool.rb', recipe))
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        if extra:
            member, data = extra
            tar.addfile(member, io.BytesIO(data) if data is not None else None)
    item['sha256'] = digest(path)
    item['size'] = path.stat().st_size
    return path, item
