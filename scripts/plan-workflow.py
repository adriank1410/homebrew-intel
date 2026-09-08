#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Resolve one official core commit and validate a bounded matrix of roots."""
import json
import os
import sys
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelbrew.core import ROOT, Error, canonical_name, read_json, registry, require_sha, run

def requested_roots(requested, targets, *, allow_csv, max_roots=50):
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
    if (not roots or (max_roots is not None and len(roots) > max_roots)
            or any(root not in targets for root in roots)):
        raise Error('Requested formula is outside the reviewed target list')
    return roots


def load_formula_index():
    """Load canonical Homebrew metadata used only by scheduled preflight."""
    request = urllib.request.Request(
        'https://formulae.brew.sh/api/formula.json',
        headers={'Accept': 'application/json', 'User-Agent': 'homebrew-intel-scheduler'},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (OSError, ValueError) as exc:
        raise Error(f'Cannot resolve Homebrew formula metadata: {exc}') from exc
    if not isinstance(payload, list):
        raise Error('Homebrew formula metadata is not a list')
    index = {}
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get('name'), str):
            index[item['name']] = item
    return index


def _api_snapshot(item):
    """Extract the fields that define a recipe snapshot, failing closed."""
    if not isinstance(item, dict) or not isinstance(item.get('name'), str):
        raise Error('Homebrew formula metadata contains an invalid item')
    try:
        version = item['versions']['stable']
        revision = item['revision']
        scheme = item['version_scheme']
        formula_sha = item['ruby_source_checksum']['sha256']
        dependencies = item['dependencies']
    except (KeyError, TypeError) as exc:
        raise Error(f'Incomplete Homebrew formula metadata: {item.get("name")}') from exc
    if (not isinstance(version, str) or type(revision) is not int or revision < 0
            or type(scheme) is not int or scheme < 0 or not isinstance(dependencies, list)
            or any(not isinstance(dep, str) for dep in dependencies)):
        raise Error(f'Invalid Homebrew formula metadata: {item["name"]}')
    require_sha(formula_sha)
    stable_bottle = item.get('bottle', {}).get('stable', {})
    bottle_file = stable_bottle.get('files', {}).get('x86_64_sequoia')
    official_bottle = False
    if isinstance(bottle_file, dict) and bottle_file.get('cellar') in {
            ':any', ':any_skip_relocation', 'any', 'any_skip_relocation', '/usr/local/Cellar'}:
        try:
            require_sha(bottle_file['sha256'])
        except (KeyError, Error):
            pass
        else:
            official_bottle = True
    return {
        'name': item['name'],
        'pkg_version': version + (f'_{revision}' if revision else ''),
        'version_scheme': scheme,
        'formula_sha256': formula_sha,
        'dependencies': sorted(set(dependencies)),
        'official_bottle': official_bottle,
        # The Linux API does not encode the target macOS conditional branch
        # used by the native bridge. Such formulas must remain eligible for
        # native planning rather than being skipped from stale assumptions.
        'conditional': bool(item.get('uses_from_macos') or item.get('requirements')
                            or item.get('variations') or item.get('pour_bottle_only_if')
                            or item.get('recommended_dependencies')),
    }


def scheduled_roots(roots, index, records, *, core_commit=None):
    """Return roots whose complete dependency graph needs a native build."""
    snapshots = {}
    loading = set()

    def snapshot(name):
        if name in snapshots:
            return snapshots[name]
        if name in loading:
            snapshots[name] = None
            return None
        item = index.get(name)
        if item is None:
            # A missing API entry is not proof of compatibility. Keep the
            # root eligible for native planning instead of skipping it.
            snapshots[name] = None
            return None
        if core_commit is not None and item.get('tap_git_head') != core_commit:
            # Never use API metadata from a different core revision to skip a
            # native verification run.
            snapshots[name] = None
            return None
        loading.add(name)
        try:
            value = _api_snapshot(item)
        except (Error, KeyError, TypeError, AttributeError):
            # Incomplete individual API records must not suppress other roots.
            # The native planner will inspect this formula on macOS instead.
            snapshots[name] = None
            loading.remove(name)
            return None
        snapshots[name] = value
        for dependency in value['dependencies']:
            snapshot(dependency)
        loading.remove(name)
        return value

    def personal_exact(name, value):
        if value is None or value['conditional']:
            return False
        record = records.get(name)
        if record is None or any(record.get(key) != value[key]
                                 for key in ('pkg_version', 'version_scheme', 'formula_sha256')):
            return False
        actual = {dependency['name'] for dependency in record['runtime_dependencies']}
        if not set(value['dependencies']).issubset(actual):
            return False
        closure = set()
        pending = list(value['dependencies'])
        while pending:
            dependency = pending.pop()
            if dependency in closure:
                continue
            closure.add(dependency)
            child = snapshots.get(dependency)
            if child is None or child['conditional']:
                return False
            pending.extend(child['dependencies'])
        if not actual.issubset(closure):
            return False
        return all(
            snapshots.get(dependency['name']) is not None
            and dependency['pkg_version'] == snapshots[dependency['name']]['pkg_version']
            and dependency['formula_sha256'] == snapshots[dependency['name']]['formula_sha256']
            for dependency in record['runtime_dependencies']
        )

    covered = {}

    def is_covered(name):
        if name in visiting:
            return False
        if name in covered:
            return covered[name]
        visiting.add(name)
        value = snapshot(name)
        if value is None:
            covered[name] = False
            visiting.remove(name)
            return False
        dependencies_covered = all(is_covered(dependency) for dependency in value['dependencies'])
        covered[name] = bool(not value['conditional'] and
                             (value['official_bottle'] or personal_exact(name, value))) and dependencies_covered
        visiting.remove(name)
        return covered[name]

    visiting = set()
    return [root for root in roots if not is_covered(root)]

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
    roots = requested_roots(
        requested,
        targets,
        allow_csv=source != 'push',
        # A scheduled sweep must inspect every reviewed target before selecting
        # its bounded batch. Manual and push-triggered matrices stay capped.
        max_roots=None if requested == 'all' and source in {'schedule', 'workflow_dispatch'} else 50,
    )
    upstream = run(['git', 'ls-remote', 'https://github.com/Homebrew/homebrew-core.git',
                    'refs/heads/main']).split()
    if len(upstream) != 2 or upstream[1] != 'refs/heads/main':
        raise Error('Cannot resolve the official main branch')
    commit = require_sha(upstream[0], git=True)
    if source == 'schedule':
        roots = scheduled_roots(roots, load_formula_index(), registry(), core_commit=commit)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
        handle.write('matrix=' + json.dumps({'root': roots}, separators=(',', ':')) + '\n')
        handle.write('has_work=' + str(bool(roots)).lower() + '\n')
        handle.write('core_commit=' + commit + '\n')
    print(json.dumps({'roots': roots, 'core_commit': commit}, indent=2))
    return 0
  except (Error, KeyError, OSError, ValueError) as exc:
    print(exc, file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
