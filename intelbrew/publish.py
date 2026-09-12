# SPDX-License-Identifier: BSD-2-Clause
"""Publish verified data from this workflow; never execute downloaded artifacts."""
from __future__ import annotations
import argparse
import copy
import json
import os
import sys
from pathlib import Path
from .ci import validate_candidate
from .core import ROOT, Error, canonical_name, load_config, require_sha, run, validate_record, digest
from .registry_pr import ensure_pr, gh_json


def release_tag(root: str, run_id: str, attempt: str) -> str:
    root = canonical_name(root)
    if not run_id.isdigit() or not attempt.isdigit():
        raise Error("Invalid run identity")
    return f"intel-{run_id}-{attempt}-{root.replace('@', '-at-')}"


def git(arguments: list[str]) -> str:
    return run(["git", "-c", "credential.helper=", "-c",
                "credential.https://github.com.helper=!gh auth git-credential", *arguments], cwd=ROOT)


def ensure_release(repo: str, tag: str, commit: str, title: str, notes: str,
                   assets: list[Path]) -> None:
    """Resume an interrupted upload without replacing any published bytes."""
    try:
        run(["gh", "release", "create", tag, "--repo", repo, "--target", commit,
             "--title", title, "--notes", notes, *map(str, assets)], capture=False)
        return
    except Error:
        # A failed request can have created the release before losing its
        # response. Read its actual state; a missing release still fails here.
        release = gh_json(["api", f"repos/{repo}/releases/tags/{tag}"])
    if (not isinstance(release, dict) or release.get("tag_name") != tag
            or type(release.get("draft")) is not bool or not isinstance(release.get("assets"), list)):
        raise Error("Existing release has an unexpected identity or state")
    expected = {p.name: p for p in assets}
    seen = set()
    for asset in release["assets"]:
        if not isinstance(asset, dict):
            raise Error("Existing release has malformed assets")
        name = asset.get("name")
        if not isinstance(name, str) or name not in expected or name in seen:
            raise Error("Existing release has unexpected or duplicate assets")
        local = expected[name]
        if asset.get("size") != local.stat().st_size or asset.get("digest") != "sha256:" + digest(local):
            raise Error("Existing release asset differs from the verified candidate")
        seen.add(name)
    refs = gh_json(["api", f"repos/{repo}/git/matching-refs/tags/{tag}"])
    if not isinstance(refs, list):
        raise Error("Existing release tag lookup is malformed")
    exact = [ref for ref in refs if isinstance(ref, dict) and ref.get("ref") == f"refs/tags/{tag}"]
    if exact:
        target = exact[0].get("object")
        if (len(exact) != 1 or not isinstance(target, dict)
                or target.get("type") != "commit" or target.get("sha") != commit):
            raise Error("Existing release tag differs from the verified pipeline commit")
    elif not release["draft"] or release.get("target_commitish") != commit:
        raise Error("Existing release has no matching tag or draft target")
    missing = [p for p in assets if p.name not in seen]
    if missing:
        run(["gh", "release", "upload", tag, "--repo", repo, *map(str, missing)], capture=False)
    if release["draft"]:
        # gh creates a temporary draft while uploading. Publish only once all
        # original candidate assets have been checked or uploaded successfully.
        run(["gh", "release", "edit", tag, "--repo", repo, "--draft=false"], capture=False)



def publish(candidate: Path, root: str, *, source_run: str | None = None) -> None:
    config = load_config()
    repo = config["repository"]
    if (os.environ.get("GITHUB_ACTIONS") != "true" or
        os.environ.get("GITHUB_REPOSITORY") != repo or
        os.environ.get("GITHUB_REF") != "refs/heads/main" or
        os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted" or
        os.environ.get("RUNNER_OS") != "Linux"):
        raise Error("Publication is restricted to the main-branch GitHub-hosted Linux job")
    commit = require_sha(os.environ["GITHUB_SHA"], git=True)
    manifest = validate_candidate(candidate, expected_root=root, verified=True)
    if source_run is not None:
        from .recovery import validate_recovery
        commit, core_commit, run_id, attempt = validate_recovery(
            candidate, root, source_run, manifest, repo)
    else:
        core_commit = require_sha(os.environ["INTELBREW_CORE_COMMIT"], git=True)
        run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    if (manifest["workflow_commit"] != commit or manifest["core_commit"] != core_commit
            or manifest["brew_commit"] != config["brew_commit"]):
        raise Error("Candidate belongs to another source/workflow snapshot")
    if not manifest["packages"]:
        print("No new bottles; no release or review branch created.")
        return
    tag = release_tag(root, run_id, attempt)
    branch = f"bottles/{tag}"
    assets = [candidate / "manifest.json"]
    for item in manifest["packages"]:
        if item["run_id"] != run_id:
            raise Error("Candidate run id differs")
        assets.extend((candidate / item["filename"], candidate / item["source"]["filename"]))
    notes = (f"Sequoia Intel bottles for `{root}`.\n\n"
             f"Official core commit: `{core_commit}`. Pipeline: `{commit}`.\n\n"
             "Built from unchanged official recipes, poured and tested on a separate macOS runner. "
             "SHA-256 and associated source bundles are in manifest.json. Artifact attestations "
             "identify the publishing workflow; they are not a reproducibility or security certificate. "
             "Package licenses remain independent from the tap's BSD-2-Clause license.\n\n"
             "These bottles become discoverable after the registry PR passes manifest validation and required checks, then merges.")
    previous = gh_json(["pr", "list", "--repo", repo, "--head", branch, "--base", "main",
                        "--state", "closed", "--json", "number,state,headRepository,headRefName,isCrossRepository", "--limit", "100"])
    if not isinstance(previous, list):
        raise Error("Invalid previous publication PR response")
    if any(isinstance(pr, dict) and pr.get("headRefName") == branch
           and not pr.get("isCrossRepository", True)
           and (pr.get("headRepository") or {}).get("nameWithOwner") == repo for pr in previous):
        print("Publication PR was already merged or closed; not recreating it.")
        return
    ensure_release(repo, tag, commit, f"Intel bottles: {root} ({run_id}/{attempt})", notes, assets)
    if git(["ls-remote", "--heads", "origin", f"refs/heads/{branch}"]).strip():
        # A previous attempt may have pushed successfully before PR creation
        # failed. Reuse it only through the existing manifest/ownership/check
        # validation, without force-pushing over registry reconciliation.
        ensure_pr(repo, branch, tag, title=f"Publish Intel bottles: {root} ({run_id}/{attempt})",
                  body=notes)
        return
    git(["switch", "--create", branch])
    changed = []
    for item in manifest["packages"]:
        record = copy.deepcopy(item)
        record["release"] = tag
        validate_record(record)
        path = ROOT / "registry" / (record["name"] + ".json")
        if path.is_symlink():
            raise Error("Registry path is a symlink")
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        changed.append(path.relative_to(ROOT).as_posix())
    git(["config", "user.name", "github-actions[bot]"])
    git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    git(["add", "--", *changed])
    git(["commit", "-m", f"bottles: {root} on Sequoia Intel ({run_id}/{attempt})"])
    git(["push", "origin", f"HEAD:refs/heads/{branch}"])
    ensure_pr(repo, branch, tag, title=f"Publish Intel bottles: {root} ({run_id}/{attempt})",
              body=notes)
    message = ("Verified release and registry PR published. Required checks are dispatched; "
               "registry maintenance merges only the validated passing head.")
    print(f"::notice::{message}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("\n" + message + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source-run", help="Recover original attested artifacts from a completed main run")
    args = parser.parse_args()
    try:
        publish(args.candidate.resolve(), canonical_name(args.root), source_run=args.source_run)
        return 0
    except (Error, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"intelbrew publish: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
