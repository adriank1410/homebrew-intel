# SPDX-License-Identifier: BSD-2-Clause
"""User entry point. Mutations require --apply, and never include source builds."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import platform
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import __version__
from .core import (Error, Planner, artifact_url, brew_env, check_bottle,
                   download, ensure_complete, load_config, matching_record,
                   native, read_json, registry, require_sha, run, write_json_new)


def attest(path: Path, repository: str, workflow_commit: str, *, token: str | None = None) -> None:
    require_sha(workflow_commit, git=True)
    if not shutil.which("gh"):
        raise Error("GitHub CLI (gh) is required to verify personal bottles; no verification bypass exists")
    env = None
    if token is not None:
        env = os.environ.copy()
        env["GH_TOKEN"] = token
    run(["gh", "attestation", "verify", str(path), "--repo", repository,
         "--signer-workflow", f"{repository}/.github/workflows/bottles.yml",
         "--source-ref", "refs/heads/main", "--source-digest", workflow_commit,
         "--signer-digest", workflow_commit, "--deny-self-hosted-runners"],
        capture=False, env=env)


def render(plan: dict) -> None:
    print("Formula                     Version             Source")
    print("-" * 70)
    for name in plan["order"]:
        item = plan["nodes"][name]
        print(f'{name:27} {item["pkg_version"]:19} {item["provider"]}')
    print("\nNo core remote, formula definitions or dependency names are changed.")


def filter_available_plan(plan: dict) -> tuple[dict, list[dict]]:
    """Keep only roots whose complete runtime plan has no missing provider."""
    nodes = plan["nodes"]
    order = plan["order"]
    skipped: list[dict] = []
    retained_roots: list[str] = []
    retained_nodes: set[str] = set()

    def closure(root: str) -> set[str]:
        reachable: set[str] = set()
        pending = [root]
        while pending:
            name = pending.pop()
            if name in reachable:
                continue
            if name not in nodes:
                raise Error(f"Incomplete plan: missing node {name}")
            reachable.add(name)
            pending.extend(nodes[name].get("dependencies", ()))
        return reachable

    for root in plan["roots"]:
        reachable = closure(root)
        missing = [
            name for name in order
            if name in reachable and nodes[name]["provider"] == "missing"
        ]
        if missing:
            skipped.append({"root": root, "missing_dependencies": missing})
            continue
        retained_roots.append(root)
        retained_nodes.update(reachable)

    filtered = {
        "schema": plan["schema"],
        "roots": retained_roots,
        "order": [name for name in order if name in retained_nodes],
        "nodes": {name: nodes[name] for name in order if name in retained_nodes},
    }
    return filtered, skipped


def coverage_report(installed: dict, targets: list[str]) -> dict:
    core = set(installed.get("core", ()))
    target_set = set(targets)
    monitored = core & target_set
    unmonitored = core - target_set
    return {
        "schema": 1,
        "monitored_core": sorted(monitored),
        "unmonitored_core": sorted(unmonitored),
        "policy_exclusions": [],
        "proposed_candidates": sorted(unmonitored),
        "external_tap_formulae": sorted(set(installed.get("external_taps", ()))),
    }


def render_coverage(report: dict) -> None:
    monitored = report["monitored_core"]
    unmonitored = report["unmonitored_core"]
    exclusions = report["policy_exclusions"]
    proposed = report["proposed_candidates"]
    external = report["external_tap_formulae"]
    print(f"Monitored core formulae ({len(monitored)}): "
          f'{", ".join(monitored) if monitored else "none"}')
    print(f"Core formulae not explicitly on target list ({len(unmonitored)})")
    print(f"Monitoring exclusions ({len(exclusions)}): "
          f'{", ".join(exclusions) if exclusions else "none"}')
    print(f"Proposed candidates ({len(proposed)}): "
          f'{", ".join(proposed) if proposed else "none"}')
    print(f"External tap formulae ({len(external)}): "
          f'{", ".join(external) if external else "none"}')


def render_skipped(skipped: list[dict]) -> None:
    roots = [item["root"] for item in skipped]
    missing = sorted({name for item in skipped for name in item["missing_dependencies"]})
    print(f"Skipped unavailable upgrade roots ({len(roots)}): {', '.join(roots)}")
    print(f"Missing providers ({len(missing)}): {', '.join(missing) if missing else 'none'}")


def render_sync(report: dict, *, apply: bool) -> None:
    print(f"Coverage: {len(report['monitored_core'])} monitored, "
          f"{len(report['eligible'])} eligible additions, {len(report['excluded'])} excluded.")
    if report["eligible"]:
        print("Eligible additions: " + ", ".join(report["eligible"]))
    reasons = Counter(item["reason"] for item in report["excluded"])
    if reasons:
        print("Excluded: " + "; ".join(f"{reason}: {count}" for reason, count in sorted(reasons.items())))
        print("Use `brew intel sync --json` for individual exclusion reasons.")
    if report.get("pr_url"):
        print(f"Coverage PR: {report['pr_url']} (eligible additions await protected checks and maintenance).")
    elif not apply:
        print("Dry run. Add --apply to propose eligible names to GitHub; no packages are installed.")
    else:
        print("No new coverage PR needed.")


def apply_plan(plan: dict, records: dict, config: dict, *, cache: Path) -> None:
    ensure_complete(plan)
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    if cache.is_symlink():
        raise Error("Cache directory may not be a symlink")
    lock = cache / "operation.lock"
    if lock.is_symlink():
        raise Error("Refusing a symlink lock file")
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Error("Another intelbrew operation is running") from exc
        downloaded: dict[str, Path] = {}
        for name in plan["order"]:
            item = plan["nodes"][name]
            if item["provider"] != "personal":
                continue
            record = matching_record(item, records)
            if record is None:
                raise Error("Registry changed after planning")
            path = cache / record["sha256"] / record["filename"]
            download(artifact_url(config["repository"], record), path,
                     record["sha256"], record["size"])
            attest(path, config["repository"], record["workflow_commit"])
            check_bottle(path, record)
            source_url = artifact_url(config["repository"], record, record["source"]["filename"])
            print(f"Source and license notices for {name}: {source_url}")
            downloaded[name] = path
        for name in plan["order"]:
            if plan["nodes"][name]["provider"] == "official":
                run(["brew", "fetch", "--force-bottle", f"homebrew/core/{name}"],
                    capture=False, env=brew_env())
        fresh = native({"mode": "inspect", "names": plan["order"]})
        for name in plan["order"]:
            old, now = plan["nodes"][name], fresh[name]
            for field in ("formula_sha256", "pkg_version", "installed_versions", "pinned",
                          "foreign_install", "installed_options", "installed_head"):
                if old[field] != now[field]:
                    raise Error(f"Homebrew state changed for {name}; re-run the plan")
        journal_dir = Path(tempfile.mkdtemp(prefix="transaction-", dir=cache))
        write_json_new(journal_dir / "plan.json", plan)
        print(f"Installation journal: {journal_dir}")
        completed = 0
        try:
            for name in plan["order"]:
                item = plan["nodes"][name]
                if item["provider"] == "installed":
                    continue
                request = {
                    "mode": "install", "name": name,
                    "target": str(downloaded[name]) if name in downloaded else f"homebrew/core/{name}",
                    "formula_sha256": item["formula_sha256"], "pkg_version": item["pkg_version"],
                    "as_dependency": name not in plan["roots"],
                }
                if name in downloaded:
                    request["sha256"] = records[name]["sha256"]
                native(request, capture=False)
                receipt = native({"mode": "receipt", "name": name})
                write_json_new(journal_dir / f"{completed:04d}-{name}.json", receipt)
                completed += 1
        except Error as exc:
            raise Error(f"Stopped after {completed} package(s); this is not a transaction rollback. "
                        f"Old versioned kegs are retained. Journal: {journal_dir}. {exc}") from exc
        print(f"Installed {completed} bottle(s). No Homebrew source build was permitted.")
        print("No cleanup was run. Existing reverse dependencies may still need a linkage review.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("command", choices=("plan", "install", "upgrade", "doctor", "coverage", "sync"))
    parser.add_argument("formulae", nargs="*")
    parser.add_argument("--apply", action="store_true", help="Install the complete validated plan (otherwise dry run)")
    parser.add_argument("--json", action="store_true", help="Print plan JSON")
    parser.add_argument("--available", action="store_true",
                        help="For upgrade, omit roots with missing runtime providers")
    args = parser.parse_args(argv)
    try:
        if platform.system() != "Darwin" or platform.machine() != "x86_64":
            raise Error("Supported client: native Intel macOS 15 (Sequoia), /usr/local Homebrew")
        config = load_config()
        records = registry()
        if args.available and args.command != "upgrade":
            raise Error("--available is only supported with upgrade")
        if args.command == "sync":
            if args.formulae:
                raise Error("sync takes installed core formulae, not explicit names")
            from .coverage_sync import sync
            report = sync(config, apply=args.apply)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                render_sync(report, apply=args.apply)
            return 0
        if args.command == "coverage":
            if args.apply or args.formulae or args.available:
                raise Error("coverage takes neither formula names, --apply nor --available")
            targets_doc = read_json(Path(__file__).resolve().parents[1] / "policy/targets.json")
            if targets_doc.get("schema") != 1 or not isinstance(targets_doc.get("formulae"), list):
                raise Error("Invalid coverage target policy")
            installed = native({"mode": "coverage"})
            report = coverage_report(installed, targets_doc["formulae"])
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                render_coverage(report)
            return 0
        if args.command == "doctor":
            if args.apply or args.formulae:
                raise Error("doctor takes neither formula names nor --apply")
            guard = native({"mode": "guard-test"})
            print(json.dumps({"version": __version__, "repository": config["repository"],
                              "published_records": len(records), "guard": guard,
                              "gh_available": bool(shutil.which("gh")),
                              "raw_brew_upgrade_uses_personal_bottles": False}, indent=2))
            return 0
        names = args.formulae
        if not names:
            if args.command == "install":
                raise Error("install needs at least one formula name")
            names = native({"mode": "outdated"})
            if args.command == "upgrade":
                targets_doc = read_json(Path(__file__).resolve().parents[1] / "policy/targets.json")
                installed = native({"mode": "coverage"})
                report = coverage_report(installed, targets_doc["formulae"])
                if report["unmonitored_core"] and not args.json:
                    stream = sys.stdout
                    print("Coverage notice: "
                          f'{len(report["unmonitored_core"])} installed core formulae are not explicitly '
                          "on the target list; run `brew intel coverage` to review.", file=stream)
        if not names:
            print("No unpinned, outdated core formulae.")
            return 0
        inspector = lambda batch: native({"mode": "inspect", "names": batch})
        plan = Planner(inspector, records, max_nodes=config["max_graph_nodes"],
                       allow_drift_as_missing=bool(args.available)).make(names)
        skipped: list[dict] = []
        if args.available:
            plan, skipped = filter_available_plan(plan)
            if skipped:
                if args.json:
                    print(json.dumps({"skipped": skipped}, indent=2), file=sys.stderr)
                else:
                    render_skipped(skipped)
            if not plan["roots"]:
                if args.json:
                    print(json.dumps(plan, indent=2))
                else:
                    print("No fully available upgrade roots; nothing installed.")
                return 0
        if args.json:
            print(json.dumps(plan, indent=2))
        else:
            render(plan)
        if args.apply:
            if args.command == "plan":
                raise Error("plan is read-only; use install/upgrade --apply")
            apply_plan(plan, records, config,
                       cache=Path.home() / "Library/Caches/homebrew-intel")
        elif not args.json:
            print("\nDry run only. Add --apply to install. Missing bottles cause an error, not compilation.")
        return 0
    except (Error, KeyError, TypeError, OSError) as exc:
        print(f"intelbrew: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
