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
from .core import ROOT, Error, canonical_name, load_config, require_sha, run, validate_record


def release_tag(root: str, run_id: str, attempt: str) -> str:
    root = canonical_name(root)
    if not run_id.isdigit() or not attempt.isdigit():
        raise Error("Invalid run identity")
    return f"intel-{run_id}-{attempt}-{root.replace('@', '-at-')}"


def git(arguments: list[str]) -> str:
    return run(["git", "-c", "credential.helper=", "-c",
                "credential.https://github.com.helper=!gh auth git-credential", *arguments], cwd=ROOT)


def publish(candidate: Path, root: str) -> None:
    config = load_config()
    repo = config["repository"]
    if (os.environ.get("GITHUB_ACTIONS") != "true" or
        os.environ.get("GITHUB_REPOSITORY") != repo or
        os.environ.get("GITHUB_REF") != "refs/heads/main" or
        os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted" or
        os.environ.get("RUNNER_OS") != "Linux"):
        raise Error("Publication is restricted to the main-branch GitHub-hosted Linux job")
    commit = require_sha(os.environ["GITHUB_SHA"], git=True)
    core_commit = require_sha(os.environ["INTELBREW_CORE_COMMIT"], git=True)
    manifest = validate_candidate(candidate, expected_root=root, verified=True)
    if (manifest["workflow_commit"] != commit or manifest["core_commit"] != core_commit
            or manifest["brew_commit"] != config["brew_commit"]):
        raise Error("Candidate belongs to another source/workflow snapshot")
    if not manifest["packages"]:
        print("No new bottles; no release or review branch created.")
        return
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
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
             "These bottles become discoverable only after the registry PR is reviewed and merged.")
    run(["gh", "release", "create", tag, "--repo", repo, "--target", commit,
         "--title", f"Intel bottles: {root} ({run_id}/{attempt})", "--notes", notes,
         *[str(p) for p in assets]], capture=False)
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
    compare_url = f"https://github.com/{repo}/compare/main...{branch}?expand=1"
    message = ("Verified release and registry branch published. "
               f"Owner review PR required: {compare_url}")
    print(f"::notice::{message}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("\n" + message + "\n")
    run(["gh", "workflow", "run", "checks.yml", "--repo", repo, "--ref", branch], capture=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    try:
        publish(args.candidate.resolve(), canonical_name(args.root))
        return 0
    except (Error, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"intelbrew publish: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
