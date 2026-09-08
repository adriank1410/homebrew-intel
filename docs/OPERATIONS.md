# Maintainer operations

## Repository and settings

The repository has already been created. `python3 scripts/deploy.py` remains a
no-write validation/helper for a fresh deployment; do not run its fresh-create
path against this repository. Future source changes belong in reviewed PRs.

Recommended server-side settings are squash-only merge, update-branch support,
automatic deletion of merged topic branches, read-by-default Actions permissions,
required `tests`, no force-push/deletion of `main`, and protected `v*`/`intel-*`
tags. Workflow actions are pinned to reviewed full commit SHAs. Actions should
remain unable to create/approve pull requests; the publisher stops at a registry
review branch and compare link, and the owner/connector opens the PR. Verify
GitHub settings separately before treating them as a security boundary.

To request a reviewed native build through the repository, edit
`policy/build-request.json` on `main`, choose one name from `policy/targets.json`,
and increment `sequence`. Only that path is a push trigger for the expensive
bottle workflow; ordinary code/docs commits are not.

Before merging pipeline changes, dispatch `bottles.yml` on the review branch
with `formula=simdutf`. This runs the native source/guard tests, build and fresh
runner verification. Publication remains restricted to `main`; a branch run
cannot produce client-trusted releases. On an Intel Sequoia Mac, the read-only
native tests (which may download source archives into Homebrew's cache) run with:

```sh
INTELBREW_NATIVE_TESTS=1 python3.11 -m unittest discover -s tests -p test_native.py -v
```

## Review a build

Open the workflow run on GitHub. Verify both Intel jobs, native receipts,
`brew linkage --test`, `brew test` and attestation. Inspect package versions/
revisions, recipe hashes, core/engine commits, dependency closure and licenses.
The publisher dispatches `checks.yml` on the review branch and emits a compare
link. Open the PR only after reviewing the native run. Do not bypass a missing
check.

Merge only after reviewing source and verification results. On a client run
`brew update`, then `brew intel doctor` and `brew intel plan NAME`. Apply a small
subset first. Build dispatch or release creation is not permission to change the
user's Cellar.

## Missing or stale bottles

A missing bottle on the client is an error, not a source fallback. Trigger a
build for the desired root. A compatible official bottle is preferred. Recipe,
revision or runtime drift triggers rebuilding. Unreviewed licensing obligations,
dynamic/VCS resources, cycles, excluded heavy builds, >60 source nodes or >500 MB
of candidate artifacts stop the run. Never remove a safety check merely to make
CI green.

`all` is for a consciously selected batch, not initial bootstrap. One failed
matrix root can block downstream publication; single-root runs avoid that.

## Client failure and recovery

The plan is checked for complete coverage before installation. Personal bottles
are downloaded and attested first, then metadata/state is checked again. A client
lock prevents two `brew intel` applications from overlapping; it does not lock
ordinary `brew` in another Terminal.

Completed packages get journal entries under
`~/Library/Caches/homebrew-intel/transaction-*`. A later failure does not roll
back prior installs. Old versioned kegs are retained and cleanup disabled, but
links can change. Do not blindly run uninstall/reinstall/cleanup/reset commands.
A corrupt cached download is retained for inspection; move an identified bad
file to Trash manually after checking its path.

## Scheduling, costs and privacy

Scheduling is disabled by default. Set `INTELBREW_ENABLE_SCHEDULE=true` only
after native validation and checking Actions storage. Inter-job artifacts expire
after one day. No paid-runner selection is automatic.

Only canonical candidate package names were retained from the local audit. No
Brewfile, machine configuration, cask inventory, user-directory path, credential,
original archive or full installed-info snapshot belongs in this public repo.

## Connector-triggered builds

A commit to `policy/build-request.json` on `main` starts the guarded workflow.
Set `formula` to one reviewed target and increment `sequence`; every edit is
visible in Git history. The publisher deliberately does not grant `GITHUB_TOKEN`
pull-request approval/creation capability. It retains the verified release and
registry branch and emits a compare link for owner/connector review.
