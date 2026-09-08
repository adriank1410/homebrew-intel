# SPDX-License-Identifier: BSD-2-Clause
"""Propose and safely reconcile additive installed-core target updates."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .ci import permissive_license
from .core import MAX_JSON, ROOT, Error, canonical_name, load_config, native, read_json, run
from .registry_pr import (_disable_existing_auto_merge, _dispatch_checks,
                          _find_pr, _merge_if_ready, gh, gh_json)

BRANCH = "coverage/intel-installed"
MAIN = "main"
MAX_INSTALLED = 2000


def _exclusion_reason(name: str, meta: dict[str, Any], config: dict[str, Any]) -> str | None:
    configured = config.get("target_exclusions", {})
    if not isinstance(configured, dict):
        raise Error("target_exclusions must be an object of exact formula names and reasons")
    if name in configured:
        reason = configured[name]
        if not isinstance(reason, str) or not reason.strip():
            raise Error(f"Invalid target exclusion reason: {name}")
        return reason.strip()
    if name in config.get("blocked_source_builds", ()): return "source build blocked by policy"
    if meta.get("name") != name or meta.get("tap") != "homebrew/core": return "non-core or renamed formula"
    if meta.get("disabled") or not meta.get("version"): return "disabled or non-stable formula"
    if meta.get("installed_options") or meta.get("installed_head"): return "options or HEAD install requires review"
    if meta.get("foreign_install"): return "foreign install"
    if meta.get("installed_newer"): return "installed version is newer than stable"
    if not permissive_license(meta.get("license"), set(config["permissive_license_tokens"])):
        return "license requires review"
    return None


def coverage_report(installed: dict[str, Any], targets: list[str], config: dict[str, Any],
                    inspect: Callable[[list[str]], dict[str, Any]]) -> dict[str, Any]:
    """Classify installed core formulae using bounded, current native metadata."""
    core = installed.get("core")
    external = installed.get("external_taps", [])
    if not isinstance(core, list) or len(core) > MAX_INSTALLED or not isinstance(external, list):
        raise Error("Invalid or excessive installed coverage inventory")
    names = sorted(set(canonical_name(item) for item in core))
    target_set = {canonical_name(item) for item in targets}
    candidates = [name for name in names if name not in target_set]
    metadata: dict[str, Any] = {}; metadata_errors: set[str] = set()
    batch_size = min(int(config.get("max_graph_nodes", 400)), 400)
    if batch_size < 1: raise Error("Invalid metadata batch limit")
    def fetch(batch: list[str]) -> None:
        try:
            fetched = inspect(batch)
            if not isinstance(fetched, dict): raise Error("Native metadata response is not an object")
            metadata.update(fetched)
        except (Error, OSError, ValueError):
            if len(batch) == 1: metadata_errors.add(batch[0]); return
            middle = len(batch) // 2; fetch(batch[:middle]); fetch(batch[middle:])
    for offset in range(0, len(candidates), batch_size):
        fetch(candidates[offset:offset + batch_size])
    eligible, excluded = [], []
    for name in candidates:
        meta = metadata.get(name)
        reason = ("metadata inspection failed" if name in metadata_errors else
                  "metadata missing" if not isinstance(meta, dict) else _exclusion_reason(name, meta, config))
        if reason: excluded.append({"name": name, "reason": reason})
        else: eligible.append(name)
    return {"schema": 1, "monitored_core": sorted(set(names) & target_set),
            "unmonitored_core": candidates, "eligible": eligible, "excluded": excluded,
            "external_tap_count": len(set(external))}


def _git(arguments: list[str], directory: Path) -> str:
    return run(["git", "-c", "credential.helper=", "-c",
                "credential.https://github.com.helper=!gh auth git-credential",
                "-c", "commit.gpgsign=false", "-c", "rebase.updateRefs=false", *arguments], cwd=directory)


def _publish(repository: str, targets: list[str], additions: list[str]) -> str | None:
    desired = sorted(set(targets) | set(additions))
    if desired == targets or set(desired) == set(targets): return None
    identity = gh_json(["api", "user"])
    expected_owner = repository.partition("/")[0]
    if not isinstance(identity, dict) or identity.get("login") != expected_owner:
        raise Error("Authenticated GitHub identity is not the configured repository owner")
    existing = _find_pr(repository, BRANCH)
    if existing:
        if not _owned_pr(repository, existing, expected_owner):
            raise Error("Existing coverage PR is outside the repository-owner boundary")
        current = _content(repository, "policy/targets.json", existing["headRefOid"])["formulae"]
        if set(additions) <= set(current):
            return f"https://github.com/{repository}/pull/{existing['number']}"
    with tempfile.TemporaryDirectory(prefix="intelbrew-coverage-") as raw:
        directory = Path(raw) / "repo"
        gh(["repo", "clone", repository, str(directory), "--", "--filter=blob:none"])
        if existing:
            _git(["fetch", "origin", f"refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}"], directory)
            _git(["switch", "--create", BRANCH, f"origin/{BRANCH}"], directory)
            _git(["rebase", "origin/main"], directory)
        else:
            if _git(["ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}"], directory).strip():
                raise Error("Coverage branch exists without its expected open PR")
            _git(["switch", "--create", BRANCH, "origin/main"], directory)
        path = directory / "policy" / "targets.json"
        current = read_json(path)
        if current.get("schema") != 1 or not isinstance(current.get("formulae"), list):
            raise Error("Invalid target policy")
        current_names = [canonical_name(item) for item in current["formulae"]]
        merged = sorted(set(current_names) | set(additions))
        if merged == current_names: return None
        path.write_text(json.dumps({"schema": 1, "formulae": merged}, indent=2) + "\n", encoding="utf-8")
        _git(["config", "user.name", "intelbrew coverage sync"], directory)
        _git(["config", "user.email", "coverage-sync@users.noreply.github.com"], directory)
        _git(["add", "--", "policy/targets.json"], directory)
        _git(["commit", "-m", "policy: add installed Intel formulae"], directory)
        lease = f"--force-with-lease=refs/heads/{BRANCH}:{existing['headRefOid']}" if existing else "--force-with-lease=refs/heads/coverage/intel-installed:"
        _git(["push", lease, "origin", f"HEAD:refs/heads/{BRANCH}"], directory)
    if not existing:
        gh(["pr", "create", "--repo", repository, "--base", MAIN, "--head", BRANCH,
            "--title", "Add installed Intel formulae to coverage",
            "--body", "Automated additive target update from local installed-core coverage. Protected checks must pass before merge."])
    pr = _find_pr(repository, BRANCH)
    if pr is None or not isinstance(pr.get("url"), str):
        # Older gh field sets used by tests/workflows may omit URL.
        return f"https://github.com/{repository}/pull/{pr['number']}" if pr and isinstance(pr.get("number"), int) else None
    return pr["url"]


def sync(config: dict[str, Any] | None = None, *, apply: bool = False, installed: dict[str, Any] | None = None,
         targets: list[str] | None = None,
         inspect: Callable[[list[str]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Inspect coverage and optionally publish one deterministic additive PR."""
    config = config or load_config()
    if targets is None:
        doc = read_json(ROOT / "policy" / "targets.json")
        if doc.get("schema") != 1 or not isinstance(doc.get("formulae"), list): raise Error("Invalid target policy")
        targets = doc["formulae"]
    installed = installed if installed is not None else native({"mode": "coverage"})
    inspect = inspect or (lambda batch: native({"mode": "inspect", "names": batch}))
    report = coverage_report(installed, targets, config, inspect)
    pr_url = _publish(config["repository"], targets, report["eligible"]) if apply and report["eligible"] else None
    report["added"] = list(report["eligible"]) if pr_url else []
    report["pr_url"] = pr_url
    return report


def _allowed_pr(repository: str, pr: dict[str, Any]) -> bool:
    login = os.environ.get("INTELBREW_COVERAGE_LOGIN")
    author = pr.get("author") or {}
    head_repo = (pr.get("headRepository") or {}).get("nameWithOwner")
    return bool(login and pr.get("state", "OPEN") == "OPEN" and pr.get("headRefName") == BRANCH and
                pr.get("baseRefName") == MAIN and head_repo == repository and
                not pr.get("isCrossRepository", False) and author.get("login") == login)


def _owned_pr(repository: str, pr: dict[str, Any], login: str) -> bool:
    author = pr.get("author") or {}; head_repo = pr.get("headRepository") or {}
    return (pr.get("state", "OPEN") == "OPEN" and pr.get("headRefName") == BRANCH and
            pr.get("baseRefName") == MAIN and head_repo.get("nameWithOwner") == repository and
            not pr.get("isCrossRepository", False) and author.get("login") == login and
            isinstance(pr.get("headRefOid"), str) and re.fullmatch(r"[0-9a-f]{40}", pr["headRefOid"]) is not None)


def _content(repository: str, path: str, ref: str) -> dict[str, Any]:
    import base64
    payload = gh_json(["api", "--method", "GET", f"repos/{repository}/contents/{path}", "-f", f"ref={ref}"])
    try:
        encoded = b"".join(payload["content"].encode("ascii").split())
        if len(encoded) > MAX_JSON * 2: raise Error("Target policy content exceeds size limit")
        value = json.loads(base64.b64decode(encoded, validate=True))
    except (KeyError, TypeError, ValueError) as exc: raise Error("Invalid target policy content from GitHub") from exc
    if set(value) != {"schema", "formulae"} or value.get("schema") != 1 or not isinstance(value.get("formulae"), list) or len(value["formulae"]) > MAX_INSTALLED:
        raise Error("Invalid target policy content")
    return value


def _pr_files(repository: str, number: int) -> list[dict[str, Any]]:
    payload = gh_json(["api", f"repos/{repository}/pulls/{number}/files?per_page=2"])
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise Error("Invalid coverage PR file response")
    return payload


def _require_regular_target(repository: str, head: str) -> None:
    commit = gh_json(["api", f"repos/{repository}/git/commits/{head}"])
    root_sha = (commit.get("tree") or {}).get("sha") if isinstance(commit, dict) else None
    if not isinstance(root_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", root_sha):
        raise Error("Coverage head has no valid root tree")
    root = gh_json(["api", f"repos/{repository}/git/trees/{root_sha}"])
    root_items = root.get("tree") if isinstance(root, dict) else None
    if not isinstance(root_items, list) or len(root_items) > 10000: raise Error("Invalid coverage root tree")
    policy = [item for item in root_items if isinstance(item, dict) and item.get("path") == "policy"]
    if len(policy) != 1 or policy[0].get("mode") != "040000" or policy[0].get("type") != "tree":
        raise Error("Coverage policy path is not a tree")
    policy_sha = policy[0].get("sha")
    if not isinstance(policy_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", policy_sha):
        raise Error("Coverage policy tree has invalid identity")
    tree = gh_json(["api", f"repos/{repository}/git/trees/{policy_sha}"])
    items = tree.get("tree") if isinstance(tree, dict) else None
    if not isinstance(items, list) or len(items) > 10000: raise Error("Invalid coverage policy tree")
    target = [item for item in items if isinstance(item, dict) and item.get("path") == "targets.json"]
    if len(target) != 1 or target[0].get("mode") != "100644" or target[0].get("type") != "blob":
        raise Error("Coverage target policy is not a regular 100644 blob")


def _official_metadata() -> dict[str, dict[str, Any]]:
    url = "https://formulae.brew.sh/api/formula.json"
    limit = 64 * 1024 * 1024
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            raw = response.read(limit + 1)
        if len(raw) > limit: raise Error("Official formula metadata exceeds size limit")
        values = json.loads(raw)
    except (OSError, ValueError) as exc: raise Error("Cannot load official formula metadata") from exc
    if not isinstance(values, list) or len(values) > 10000: raise Error("Invalid official formula metadata")
    result = {}
    for value in values:
        if not isinstance(value, dict): raise Error("Invalid official formula metadata entry")
        name = canonical_name(value.get("name")); versions = value.get("versions")
        result[name] = {"name": name, "tap": value.get("tap", "homebrew/core"),
                        "version": versions.get("stable") if isinstance(versions, dict) else None,
                        "disabled": bool(value.get("disabled")), "license": value.get("license")}
    return result


def validate_pr(repository: str, pr: dict[str, Any]) -> None:
    if not _allowed_pr(repository, pr): raise Error("Refusing coverage PR outside its exact owner and branch boundary")
    number = pr.get("number")
    if not isinstance(number, int): raise Error("Coverage PR has invalid number")
    files = _pr_files(repository, number)
    if len(files) != 1: raise Error("Coverage PR must change exactly one file")
    item = files[0]
    if item.get("path", item.get("filename")) != "policy/targets.json" or str(item.get("changeType", item.get("status", ""))).lower() != "modified":
        raise Error("Coverage PR changes outside policy/targets.json")
    head = pr.get("headRefOid")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head): raise Error("Coverage PR has invalid head")
    _require_regular_target(repository, head)
    base = _content(repository, "policy/targets.json", MAIN)["formulae"]
    proposed = _content(repository, "policy/targets.json", head)["formulae"]
    base_names = [canonical_name(x) for x in base]; proposed_names = [canonical_name(x) for x in proposed]
    if proposed_names != sorted(set(proposed_names)) or not set(base_names) < set(proposed_names):
        raise Error("Coverage target change is not canonical, deterministic, and strictly additive")
    config = load_config()
    official = _official_metadata()
    for name in sorted(set(proposed_names) - set(base_names)):
        meta = official.get(name)
        if not meta or _exclusion_reason(name, meta, config): raise Error(f"Coverage target is no longer eligible: {name}")


def reconcile(repository: str) -> None:
    pr = _find_pr(repository, BRANCH)
    if pr is None: return
    pr = _disable_existing_auto_merge(repository, pr)
    validate_pr(repository, pr)
    if pr.get("mergeStateStatus") == "BEHIND":
        gh(["api", "--method", "PUT", f"repos/{repository}/pulls/{pr['number']}/update-branch",
            "-f", f"expected_head_sha={pr['headRefOid']}"], capture=False)
        return
    if pr.get("mergeStateStatus") == "DIRTY": raise Error("Coverage PR has conflicts")
    state = _dispatch_checks(repository, BRANCH, pr)
    _merge_if_ready(repository, pr, state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--reconcile", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.reconcile: raise Error("Use --reconcile")
        expected = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "adriank1410/homebrew-intel",
                    "GITHUB_REF": "refs/heads/main", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux"}
        if any(os.environ.get(key) != value for key, value in expected.items()):
            raise Error("Reconciliation is restricted to the main-branch GitHub-hosted Linux job")
        config = load_config(); reconcile(config["repository"]); return 0
    except (Error, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"intelbrew coverage-sync: {exc}", file=__import__('sys').stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
