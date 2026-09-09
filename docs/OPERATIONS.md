# Maintainer operations

## Repository and settings

The repository has already been created. `python3 scripts/deploy.py` remains a
no-write validation/helper for a fresh deployment; do not run its fresh-create
path against this repository. Future source changes belong in reviewed PRs.

Recommended server-side settings are squash-only merge, update-branch support,
automatic deletion of merged topic branches, read-by-default Actions permissions,
required `tests`, no force-push/deletion of `main`, and protected `v*`/`intel-*`
tags. Workflow actions are pinned to reviewed full commit SHAs. Registry automation
uses a private GitHub App installed only on this repository. Configure repository
variable `INTELBREW_APP_CLIENT_ID` and secret `INTELBREW_APP_PRIVATE_KEY`. The App
needs Contents, Pull requests and Actions read/write permissions; Metadata read
is mandatory. It needs no webhook, OAuth user authorization or account permissions.
Only the main-branch publication and registry jobs create an installation token,
explicitly restricted to `homebrew-intel` and those three permissions. Tokens
expire after one hour and the pinned action revokes them at job completion.
The controller accepts only the exact `app/<slug>` identity returned by that
action. Missing identity or credentials do not fall back to a personal token.

Using the App to create/update PRs avoids the human workflow-approval gate that
GitHub applies to PRs created with `GITHUB_TOKEN`. The repository's "Allow GitHub
Actions to create and approve pull requests" switch alone does not remove that
gate. The controller never approves reviews. Attestation verification uses the
separate built-in token (`INTELBREW_ATTESTATION_TOKEN`); the App needs no extra
attestation permission. Its private key is not passed to builds or package tests.
Verify GitHub settings separately before treating them as a security boundary.

To request a reviewed native build through the repository, edit
`policy/build-request.json` on `main`, choose one name from `policy/targets.json`,
and increment `sequence`. Only that path is a push trigger for the expensive
bottle workflow; ordinary code/docs commits are not.

Before merging pipeline changes, dispatch `bottles.yml` on the review branch
with a small selection such as `formula=simdjson,gnupg`. This also exercises
partial failure if one selected root is blocked by policy. This runs the native source/guard tests, build and fresh
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
The publisher creates a registry PR and dispatches `checks.yml` explicitly.
The App also triggers ordinary PR checks without a human approval step. Only
PRs authored by the configured App,
same-repository registry changes matching the attested release manifest qualify
for immediate, head-pinned automated merging. The protected `main` still requires the `tests` check and an
up-to-date base. Do not bypass a missing check.

Source and workflow PRs still need explicit owner approval to merge. The narrowly
scoped owner-authored `coverage/intel-installed` PR is the exception described below. On a client run
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

`all` checks the reviewed target list. Manual dispatch also accepts a comma-separated
subset. Completed candidate and verified artifacts select the downstream matrices;
a failed root stays failed but does not block successful siblings. No artifact
means no downstream job for that root. Existing matching bottles are reused.
The workflow summary and failed root logs identify remaining coverage gaps.

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

Set `INTELBREW_ENABLE_SCHEDULE=true` after native validation. The bottle workflow
checks reviewed roots daily at 03:41 UTC. Linux preflight can omit fully covered
roots only when its API metadata matches the resolved core revision. Uncertain
roots are inspected together on a pinned Intel runner before allocating build
jobs. This native preflight reuses metadata, checks source policy and reports
blocked roots independently. Every eligible root needing a build enters the matrix,
with at most two concurrent build jobs. Planning fails explicitly if more than
GitHub's 256-job matrix limit need builds, rather than silently omitting roots.
Explicit small manual selections still force native verification; `all` uses the
same native preflight.
Registry maintenance runs
hourly at minute 17, revalidates eligible PRs, updates outdated branches, and
dispatches missing checks and retries cancelled or timed-out checks, with at most
three dispatch attempts per head commit. A test failure or exhausted retry budget
requires attention. GitHub can delay scheduled runs.
Disable the variable to pause both schedules; manual dispatch remains available.
Inter-job artifacts expire after one day. No paid-runner selection is automatic.
Conflicting registry changes or unreviewed licenses require attention; they do
not relax the policy or permit source fallback on clients.

Client updates remain manual. The following chain includes coverage publication
and requires the repository owner's authenticated `gh` account; other users omit
`brew intel sync --apply` (or use read-only `brew intel sync` separately):

```sh
brew update &&
brew intel sync --apply &&
brew intel upgrade --available --apply &&
brew upgrade --cask &&
brew cleanup
```

The Intel step includes official core bottles; the following step updates casks
with standard Homebrew dependency handling. Other taps are outside the client
scope. `--available` omits roots with missing bottle dependencies and reports them.
Without it, incomplete coverage stops the whole operation. A separate
`brew cleanup` can remove retained old kegs; it is never run by this client.

Only canonical candidate package names were retained from the local audit. No
Brewfile, machine configuration, cask inventory, user-directory path, credential,
original archive or full installed-info snapshot belongs in this public repo.

## Connector-triggered builds

A commit to `policy/build-request.json` on `main` starts the guarded workflow.
Set `formula` to one reviewed target and increment `sequence`; every edit is
visible in Git history. Registry PRs then follow the same attestation, required
checks and protected-branch merge path as scheduled builds.

## Updating installed-package coverage

Run `brew intel sync --json` for the detailed local eligibility report. Add
`--apply` to publish eligible additions using the repository owner's existing
GitHub CLI login. The client operates in a temporary checkout; it does not edit
the installed tap, change core remotes, remove existing targets, or install
packages. The fixed `coverage/intel-installed` branch avoids duplicate PRs.

Hourly coverage maintenance runs on trusted `main` with the same short-lived App
token. It accepts only the expected owner, branch, repository and base; exactly
`policy/targets.json` may change, strictly by adding canonical names. Current
official metadata must identify a stable, enabled core formula. Licensing and
source-build restrictions do not exclude monitoring. Required checks, an
up-to-date branch and the validated head commit remain
mandatory. It never approves a review or relaxes bottle publication checks.

A new target is monitoring intent, not a distribution exception. The build
planner still checks every dependency that must be compiled. For example, NumPy's
permissive license does not resolve a blocked GCC source build. Qt modules are
monitored too, while missing source-build and redistribution support is reported
by build planning. Removal or disabling upstream can stop a proposed addition.
