# Changelog

## Unreleased

- Recognize the official `sequoia` Intel bottle tag during scheduled preflight, while retaining native checks for conditional recipes.
- Order installed kegs using Homebrew's package versions so upgrade output does not show `1.9` as newer than `1.10`.
- Report missing bottles and already-current packages explicitly in install and upgrade previews.
- Retry Git connection failures and preserve attestation diagnostics in verbose mode so transient failures remain retryable.
- Retry Homebrew fetch hooks before compilation, keeping source builds outside the retry loop.
- Reconcile partially accepted release uploads against their verified hashes before sending only the remaining assets.
- Preserve option-like filenames in SVN source archives and reject invalid shell retry settings before running a command.
- Correct scheduling documentation to describe dependency waves, bounded batches, and five-minute coverage maintenance.
- Document a dedicated ruleset for the standing `coverage/intel-installed` branch: admin-only create/push/delete, no required PR or force-push block.
- Monitor popular Homebrew formulae that currently lack Sequoia Intel bottles: `uv`, `pnpm`, `docker`, `docker-compose`, `just`, `neovim`, `glab`, `helm`, `lazygit`, `rclone`, `git-lfs`, and `cloudflared`.

## 0.2.1 – CI reliability

- Unified transient network and GitHub CLI retries behind `retry_transient()`.
- Retry truncated bottle downloads (`IncompleteRead`) instead of failing the fetch.
- Classify only `HTTP 404` / `401` / `403` as permanent; a SHA or URL containing those digits no longer blocks retry.
- Restore Subversion bootstrap for pinned SVN sources when `subversion` is not already a planned bottle install.
- Bound Homebrew formula API payloads at 64 MiB.

## 0.2.0 – native experience & visual polish

- Native Homebrew output format: `upgrade` shows `==> Upgrading N outdated package(s):` with `<formula> <old> -> <new>` and hides already-installed dependencies.
- Retain full dependency resolution table exclusively for `brew intel plan`.
- Native SLSA Provenance verification indicator: clean `✔ Attestation verified (SLSA Provenance v1)`.
- Added `-v` / `--verbose` CLI flag to access detailed attestation policy diagnostics, source bundle URLs, and transaction journal paths.
- Removed redundant pouring and cellar prints in Python installer wrapper to rely directly on Homebrew's native status messages.

## 0.1.0 – source preview

Initial core-identity bottle registry and explicit `brew intel` client. Includes
native planning, no-source installer guard, read-only build/fresh verification,
attested publication and review-required registry changes, conservative source
and licensing policy, and adversarial-input unit tests.

Validation corrections before first native run:

- accept bounded Homebrew `any_of` / `all_of` license structures only when every leaf is allowlisted, including simdutf's Apache-2.0/MIT declaration;
- add an explicit reviewed `policy/build-request.json` trigger so connector-driven builds do not require a local GitHub CLI;
- keep Actions PR creation/approval disabled: publication stops at a verified review branch and compare link for owner/connector review;
- update maintainer documentation for the already-created repository state;
- retain the source preview status until native Intel CI and older-keg upgrade acceptance actually pass.

No third-party bottle is pre-published or invented by this release.
