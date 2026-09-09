#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Resolve scheduled build roots with the pinned native Homebrew bridge."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelbrew.ci import allowed_redistribution
from intelbrew.core import Error, Planner, canonical_name, load_config, native, registry

# Keep the scheduled source-build allocation small and predictable.
SCHEDULE_BATCH_SIZE = 4
INSPECT_BATCH_SIZE = 100


class CachedInspector:
    def __init__(self):
        self.cache = {}
        self.errors = {}

    def prime(self, names):
        names = [name for name in dict.fromkeys(names)
                 if name not in self.cache and name not in self.errors]
        for offset in range(0, len(names), INSPECT_BATCH_SIZE):
            self._prime_batch(names[offset:offset + INSPECT_BATCH_SIZE])

    def _prime_batch(self, batch):
        try:
            result = native({"mode": "inspect", "names": batch}, ci=True)
            if not isinstance(result, dict) or set(result) != set(batch):
                raise Error('Incomplete metadata/alias')
            self.cache.update(result)
        except (Error, KeyError, OSError, TypeError, ValueError) as exc:
            if len(batch) > 1:
                middle = len(batch) // 2
                self._prime_batch(batch[:middle])
                self._prime_batch(batch[middle:])
            elif batch:
                self.errors[batch[0]] = str(exc)

    def __call__(self, names):
        missing = [name for name in names if name not in self.cache]
        if missing:
            self.prime(missing)
        failed = [name for name in names if name in self.errors]
        if failed:
            name = failed[0]
            raise Error(f'{name}: native inspection failed: {self.errors[name]}')
        return {name: self.cache[name] for name in names}


def bounded_schedule(candidates, *, rotation_key, limit=SCHEDULE_BATCH_SIZE):
    if type(rotation_key) is not int or rotation_key < 1 or type(limit) is not int or limit < 1:
        raise Error('Invalid scheduled rotation parameters')
    if len(candidates) <= limit:
        return list(candidates)
    start = ((rotation_key - 1) * limit) % len(candidates)
    rotated = list(candidates[start:]) + list(candidates[:start])
    return rotated[:limit]


def roots_needing_build(roots, inspector, records, config):
    inspector.prime(roots)
    needed = []
    blocked = {}
    for root in roots:
        try:
            plan = Planner(
                inspector,
                records,
                build=True,
                max_nodes=config['max_graph_nodes'],
                blocked=config['blocked_source_builds'],
            ).make([root])
            builds = [name for name in plan['order'] if plan['nodes'][name]['provider'] == 'build']
            if not builds:
                continue
            if len(builds) > config['max_source_builds']:
                raise Error(f'{root}: source build budget exceeded')
            for name in builds:
                allowed_redistribution(plan['nodes'][name], config)
            needed.append(root)
        except (Error, KeyError, OSError, TypeError, ValueError) as exc:
            blocked[root] = str(exc)
    return needed, blocked


def main():
    try:
        payload = json.loads(os.environ['PLANNED_ROOTS'])
        roots = payload.get('root') if isinstance(payload, dict) else None
        if (not isinstance(roots, list) or not roots or len(roots) > 2000
                or any(not isinstance(root, str) for root in roots)):
            raise Error('Invalid planned roots')
        roots = [canonical_name(root) for root in roots]
        if len(roots) != len(set(roots)):
            raise Error('Planned roots contain duplicates')
        candidates, blocked = roots_needing_build(
            roots, CachedInspector(), registry(), load_config())
        selected = bounded_schedule(
            candidates, rotation_key=datetime.now(timezone.utc).date().toordinal())
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
            handle.write('matrix=' + json.dumps({'root': selected}, separators=(',', ':')) + '\n')
            handle.write('has_work=' + str(bool(selected)).lower() + '\n')
        print(json.dumps({
            'inspected_roots': len(roots),
            'build_candidates': len(candidates),
            'selected_roots': selected,
            'blocked_roots': blocked,
        }, indent=2))
        return 0
    except (Error, KeyError, OSError, TypeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
