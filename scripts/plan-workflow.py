#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Resolve one official core commit and validate a bounded matrix of roots."""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelbrew.core import ROOT, Error, canonical_name, read_json, require_sha, run

try:
    targets = read_json(ROOT / 'policy/targets.json')['formulae']
    requested = os.environ.get('REQUESTED_FORMULA', 'simdutf')
    if os.environ.get('REQUEST_SOURCE') == 'push':
        request = read_json(ROOT / 'policy/build-request.json')
        if (set(request) != {'schema', 'formula', 'sequence'} or request['schema'] != 1
                or type(request['sequence']) is not int or request['sequence'] < 1):
            raise Error('Invalid checked-in build request')
        requested = request['formula']
    roots = targets if requested == 'all' else [canonical_name(requested)]
    if not roots or len(roots) > 50 or any(r not in targets for r in roots):
        raise Error('Requested formula is outside the reviewed target list')
    upstream = run(['git', 'ls-remote', 'https://github.com/Homebrew/homebrew-core.git',
                    'refs/heads/main']).split()
    if len(upstream) != 2 or upstream[1] != 'refs/heads/main':
        raise Error('Cannot resolve the official main branch')
    commit = require_sha(upstream[0], git=True)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
        handle.write('matrix=' + json.dumps({'root': roots}, separators=(',', ':')) + '\n')
        handle.write('core_commit=' + commit + '\n')
    print(json.dumps({'roots': roots, 'core_commit': commit}, indent=2))
except (Error, KeyError, OSError, ValueError) as exc:
    sys.exit(str(exc))
