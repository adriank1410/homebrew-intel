#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Dependency-free checks runnable locally and in read-only pull-request CI."""
import ast, re, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from intelbrew.core import load_config,registry,canonical_name,read_json
try:
    load_config();registry();targets=read_json(ROOT/'policy/targets.json')['formulae']
    assert len(targets)==len(set(targets));assert all(canonical_name(x)==x for x in targets)
    for path in list((ROOT/'intelbrew').glob('*.py'))+list((ROOT/'scripts').glob('*.py'))+list((ROOT/'tests').glob('*.py')):ast.parse(path.read_text(),filename=str(path))
    for path in (ROOT/'libexec').glob('*.rb'):subprocess.run(['ruby','-c',str(path)],check=True)
    for path in [ROOT/'cmd/brew-intel',ROOT/'scripts/prepare-runner.sh']:subprocess.run(['bash','-n',str(path)],check=True)
    workflows=sorted((ROOT/'.github/workflows').glob('*.yml'));subprocess.run(['ruby','-ryaml','-e','ARGV.each { |p| YAML.parse_file(p) }',*[str(p) for p in workflows]],check=True)
    for path in workflows:
        text=path.read_text();assert 'pull_request_target' not in text;assert 'persist-credentials: true' not in text
        for action in re.findall(r'uses:\s*(\S+)',text):
            # A local reusable workflow resolves at the caller's exact commit.
            assert action == './.github/workflows/bottle-root.yml' or re.fullmatch(r'(?:actions|github)/[\w-]+@[0-9a-f]{40}',action),f'Unpinned action: {action}'
        assert not re.search(r'run:.*\$\{\{\s*(?:inputs|matrix)\.',text)
    for path in [ROOT/'SECURITY.md',ROOT/'THIRD_PARTY.md',ROOT/'LICENSE',ROOT/'.github/CODEOWNERS',ROOT/'README.md']:assert path.is_file()
    print(f'Project checks passed; {len(targets)} reviewed candidate roots, {len(registry())} published records.')
except (Exception,AssertionError) as exc:sys.exit(f'Project check failed: {exc}')
