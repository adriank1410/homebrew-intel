#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Resolve one official core commit and validate a bounded matrix of roots."""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelbrew.core import ROOT, Error, canonical_name, read_json, require_sha, run


def requested_roots(requested, targets, *, allow_csv):
    if not isinstance(requested, str):
        raise Error('Requested formula must be a string')
    if requested == 'all':
        roots = list(targets)
    elif allow_csv and ',' in requested:
        parts = [part.strip() for part in requested.split(',')]
        if any(not part for part in parts):
            raise Error('Formula list contains an empty item')
        roots = [canonical_name(part) for part in parts]
        if len(roots) != len(set(roots)):
            raise Error('Formula list contains duplicates')
    else:
        roots = [canonical_name(requested.strip())]
    if not roots or len(roots) > 50 or any(root not in targets for root in roots):
        raise Error('Requested formula is outside the reviewed target list')
    return roots

def main():
  try:
    targets = read_json(ROOT / 'policy/targets.json')['formulae']
    requested = os.environ.get('REQUESTED_FORMULA', 'simdutf')
    source = os.environ.get('REQUEST_SOURCE')
    if source == 'push':
        request = read_json(ROOT / 'policy/build-request.json')
        if (set(request) != {'schema', 'formula', 'sequence'} or request['schema'] != 1
                or type(request['sequence']) is not int or request['sequence'] < 1):
            raise Error('Invalid checked-in build request')
        requested = request['formula']
    roots = requested_roots(requested, targets, allow_csv=source != 'push')
    upstream = run(['git', 'ls-remote', 'https://github.com/Homebrew/homebrew-core.git',
                    'refs/heads/main']).split()
    if len(upstream) != 2 or upstream[1] != 'refs/heads/main':
        raise Error('Cannot resolve the official main branch')
    commit = require_sha(upstream[0], git=True)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
        handle.write('matrix=' + json.dumps({'root': roots}, separators=(',', ':')) + '\n')
        handle.write('core_commit=' + commit + '\n')
    print(json.dumps({'roots': roots, 'core_commit': commit}, indent=2))
    return 0
  except (Error, KeyError, OSError, ValueError) as exc:
    print(exc, file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
