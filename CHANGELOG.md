# Changelog

## Unreleased

- Set `HOMEBREW_GITHUB_ACTIONS=1` and unset `GITHUB_ACTIONS_HOMEBREW_SELF_HOSTED` for every CI `brew test`, matching official Homebrew. Formulae such as `streamlink` then take their GitHub Actions test path, so a hash-pinned `formula_test_github_actions` exception is not required.
- Auto-renew a `source_required_formula_exceptions` entry when the formula hash changes only if the source archive checksum matches the new recipe and the configured license file is bit-identical to the reviewed hash stored in the exception. The Nmap 7.993 exception now records `nmap-*/LICENSE` (`9d9a9a763c0e6145172cfe7d8483e23b38ce60b6c79a82e4894242917bdae6d3`). Otherwise the planner still skips the root and a review is required. Renewals are written to the planner step summary.
- Hold a root at runtime when the same allowlisted compile failure (for example CA1307, an arm64 requirement, the hashcat Metal assertion, or a static assertion) occurs on the same formula-hash pair twice in a row, with at most two drifted dependencies. The first failure stays red and uploads an `intelbrew-failure-<root>` evidence artifact; the second exits 0 without a bottle and posts a notice. Later scheduled runs skip that pair until either hash changes, and sibling roots stay green. These runtime holds are not written back to `source_build_holds` on `main`.

- Refresh the Nmap source-required exception for recipe `75ebf9e178c521a5ce5723abdd6a7f89519b301eee784e729f8ec1101102d03a` (7.993). The 7.992 exception no longer matches, which blocks `nmap` and dependents such as `rustscan` before compilation. The upstream LICENSE is byte-identical to 7.992 (NPSL 0.95).
- Hold source builds of the `archi-steam-farm` 6.3.11.7 recipe `1d2fba0e814e9568b49f9f2d9454eca8ed7e15b9526608c6e3019d8303facac4` when its `dotnet` dependency is the 11.0.100 recipe `1db51a4ec77fdf642aedba497bd7045af1611c1ed3ac1dc738e215f54c4136aa`. Homebrew-core moved `dotnet` to 11 without changing ASF, so the recipe now publishes for `net11.0` and the .NET 11 analyzers fail the build with CA1307 errors (run 43456114064). The hold matches only that pair, expires when either file changes, and does not publish a bottle.
- Raise the daily release deletion cap from 100 to 200. The first run at 100, on 2026-11-11, took 3.5 minutes and left about 990 deletable releases, a backlog of about 25 days at that rate against about 60 new releases a day. At 200 it clears in about a week; after that the job deletes only what newly crosses the 30-day threshold.

## 0.2.10 – retention, source export and registry automation

- List every release before selecting old ones for deletion. `gh release list --limit 1000` returned only the newest 1000, which on 2026-11-10 covered about 17 days. No listed release reached the 30-day threshold, so the daily retention job reported "0 releases selected for deletion" while more than 2800 releases existed. The listing now asks for up to 100000 and stops with an error if it reaches that limit. Retention still deleted 20 releases on 2026-10-12 and stopped once the repository passed 1000.
- Raise the daily release deletion cap from 20 to 100 and the job timeout from 10 to 20 minutes. About 60 releases a day were published in early November 2026.
- Apply release retention only on `main`. A manual dispatch from another ref is always a dry-run, because referenced releases are read from the checked-out registry.
- Stop asking GitHub about a registry pull that has already merged while a sweep waits to start the next wave. One request per root per poll could reach 1464 requests in 20 minutes on the workflow token.
- After "Re-run failed jobs", look for each registry pull on the branch of the run attempt that published it. The continuation used the current attempt for every root, never found the earlier pulls, waited 20 minutes and started no wave.
- Exit with status 130 and a one-line message when a client command is interrupted with Ctrl-C, instead of a Python traceback. An interrupt during installation also prints the journal path and the number of completed packages.
- Report a malformed candidate during publication or recovery as a one-line error, as `intelbrew.ci` already does. `publish` did not catch `AttributeError`, and recovery has no earlier validation step, so it ended in a traceback. The exit status was 1 either way.
- Make `tests/test_source_seeds.py` run on Python 3.9, the macOS system Python. It used `enterContext`, which needs 3.11, so the documented local test command failed with five errors.
- Stop triggering the environment workflow for `implementation/sequoia-bottles`, a branch that no longer exists.
- Remove the `.partial-<pid>` name after a verified download. It was a second hard link to the bottle, so deleting the cache entry did not free the space. A failed verification still keeps the partial file.
- `brew intel upgrade --available` now skips only the roots that need a formula blocked by the Mac's own state: pinned, installed with options or HEAD, a foreign install, or a newer installed version. One such dependency used to stop the whole operation with an error, although unavailable roots are meant to be reported and skipped. Without `--available`, and in CI planning, these states still stop the plan.
- `brew intel upgrade --apply` no longer marks upgraded formulae as installed on request. Without names, every outdated formula is a root, and roots were installed without `--as-dependency`, which makes Homebrew set the flag. An upgrade of an installed formula now passes `--as-dependency`, so Homebrew keeps the old keg's flag as `brew upgrade` does. Checked against Homebrew `95d4938` source (`cmd/install.rb:212`, `install.rb:100`, `upgrade.rb:603`); not run on a Mac.
- Export pinned Git sources with every tracked file, unchanged. `git archive` left out paths marked `export-ignore` and expanded `$Format:$` in paths marked `export-subst`, while Homebrew builds from the full checkout, so a published source archive could differ from the built source. The export now switches both attributes off through `$GIT_DIR/info/attributes` for that one command, in the repository and in each submodule, and restores the file afterwards. Archives of repositories that use these attributes get a different SHA-256 than before; published bundles are not changed.
- Run `tests/test_git_sources.py` and `tests/test_source_mirrors.py` in the native Homebrew check. They need `INTELBREW_NATIVE_TESTS=1`, and no workflow pattern matched them, so they never ran. `tests/test_svn_sources.py` still does not run in CI: it needs `svn` and `svnadmin`, which the runner does not provide.
- Give a manual multi-root bottle request and a `policy/build-request.json` push their own concurrency group. They shared the sweep group, where a waiting run is replaced by the next one, so the hourly schedule cancelled them while they waited behind a sweep. The per-root lock still serializes work on one root.
- Ignore pull requests from forks when looking up the registry or pin pull request for a branch. `gh pr list --head` matches the branch name in any repository, so a fork pull request from a branch named like an automation branch made the lookup fail with "Multiple open" and stopped that automation until it was closed.
- Rebuild a conflicted registry pull request instead of closing it when `main` still holds, unchanged since the branch point, the records it replaces. A root update whose only conflict was a dependency that a sibling pull merged first was closed as stale, and the finished build was discarded. A record that `main` changed after the branch point is still treated as stale. Covered by fixtures only; it cannot be exercised outside production.
- Free each package's scratch space as soon as a build is done with it: its Homebrew cache, collected build sources, bottle work directory, any seed material, and downloaded dependency bottles after they are poured. A root with several source builds kept all of it until the job ended, on a runner with about 14 GB free. Removal is best-effort, limited to the build's own work directory, and cannot fail a build.
- Run two release-retention selection tests that `unittest discover` never collected because they were module-level functions.
- Load explicitly selected local bottles with `Formulary.from_bottle` when Homebrew provides it. Homebrew/brew `ec249da5f` (pinned in `d928780`) made `Formulary.factory` refuse file paths and turned the internal path opt-out into a no-op, so the native bridge check failed with "local bottles still forbidden" and every root build stopped before compiling. Older Homebrew pins keep the previous scoped opt-out, and an explicit `HOMEBREW_FORBID_PACKAGES_FROM_PATHS` still refuses local bottles.
- Apply the same published-source fallback to GitHub `.patch` and `.diff` resources when index-hash normalization cannot repair the download, for example a rate-limit or challenge page. The seed is still used only when its SHA-256 equals the formula checksum.
- When an upstream host serves an HTML bot challenge or refuses the download, retry source collection once with the inputs of the root's previously published source bundle. The bundle is checked against its registry SHA-256 and size and its build attestation, and a file is used only when its SHA-256 equals the pinned formula checksum. `zbar` 0.23.95 failed in every scheduled run since linuxtv.org started answering with an Anubis challenge page. A root without a previous release, or with a different source archive, still fails closed.
- Hold source builds of the `openjdk` 27.0.1 recipe `0fea82ef0501c239c6c228105f59f9c01a4895b6c98db274955213d8c79329ad`. Homebrew-core `2d52dc6671` added `depends_on arch: :arm64` on macOS, so the build stops with "The arm64 architecture is required" even with the boot JDK variant. The requirement is not stripped.
- Hold source builds of the `hashcat` 8.0.0 recipe `106cea3252302ae8e17386d19f9f025e70d0e21e7210d5d4a33f9a2dec28e797`. Its formula test aborts on the Intel runner with the Metal assertion "Target device architecture is nil". Both holds match the formula file alone, expire when it changes, and do not publish a bottle.
- Build `openjdk` 27.x on the Intel runner with the owner-approved `openjdk-intel-boot-jdk-26` recipe variant. Since 27.0.1, homebrew-core's macOS boot JDK is AArch64-only and `configure` rejects it on x86_64. The variant replaces only that resource with the official macOS x64 JDK 26.0.2.1 build from jdk.java.net, pinned by URL and SHA-256. Any other upstream layout, including JDK 28 or a restored upstream Intel boot JDK, stops before compilation with "review needed". The bottle record carries `build_profile`, and the source bundle keeps both recipes and the executed boot JDK.

## 0.2.9 – retired source holds

- When `nmap.org` presents an expired TLS certificate, fetch the same `nmap-<version>.tar.bz2` from `distfiles.macports.org` and keep it only when its SHA-256 matches the formula. Other TLS failures still fail closed. Homebrew does not try a mirror when certificate resolution fails, so this seeds the download cache before the retry.
- Refresh the Nmap source-required exception for recipe `00a1d74c9e243d46184a8b5e8537f8ff4200b0928f78e8898545383475a820f7` (7.992). The 7.991_1 exception no longer matches, which blocked `nmap` and `rustscan` before compilation.
- After a sweep merges at least one bottle registry pull, start the next dependency wave immediately, for up to eight waves. A sweep that publishes nothing, a one-root repair, and the eighth wave leave the rest to the hourly schedule.
- Remove the Qt 6.11.2 / md4c 0.6.0 pair and the ocrmypdf 17.13.0 recipe from `source_build_holds` after matching Sequoia bottles were published (`qtbase` 6.12.0_1, `ocrmypdf` 17.13.0). The list is empty. Name blocks for `llvm`, `qt`, `qtwebengine`, and `qtwebview` stay.

## 0.2.8 – skip blocked runtime toolchains

- Do not start a CI root that source-builds a policy-blocked compiler and also source-builds a formula that needs it at runtime, such as `rust` or `lld` on `llvm`. The schedule reports the root as blocked and keeps going. A manual dispatch stops in the build planner before compilation. A matching compiler bottle makes the root eligible again. A build-only use of the compiler still installs it with `--build-from-source`.

## 0.2.7 – transient prefetch and artifact upload recovery

- Retry Cloudflare HTTP 522 during source prefetch, including curl's `returned error: 522`. A bare `522` inside another token and every `curl: (56)` stay non-transient. Formula installation stays outside the retry loop.
- Retry candidate and verified artifact uploads twice after a failed attempt. Later attempts replace an artifact name left behind by a failed create. The job fails when none of the three attempts succeeds.
- Hold source builds of the ocrmypdf 17.13.0 recipe `39b84c91d0b8af18ffcd5bddbcae49cabbac0ea6ac26e8d872083ead3f9f2499`. The hold matches that formula file alone and expires when the file changes. It does not publish a bottle.

## 0.2.6 – safe linking and dependency planning

- Preserve Homebrew's keg-only, skip-link and relinking behavior around foreign prefix paths; restore temporarily held keg files even when interrupted, including during a move.
- Keep both build and test roles when inspecting local recipes, so verification does not omit a compiler needed by formula tests.
- Restrict automated pin merges to the two commit fields, normalize qualified source-hold names, and align colored plan headers.
- Link the rest of a poured keg when an existing prefix path would be overwritten. A GPG Suite `gpg` symlink and Intel-runner directories such as `share/gettext` and `include/X11` stay in place, while that keg's libraries and tools are linked.

## 0.2.5 – prefix conflict resilience & core synchronization

- Pour a bottle even when an existing prefix file, such as a GPG Suite `gpg` symlink, would be overwritten. The keg stays unlinked, those files stay in place, and the rest of the transaction continues.
- Bootstrap Homebrew formula-testing Bundler gems (`formula_test`) with backoff retries before bottle verification, and set Bundler network retries in CI environments so transient RubyGems outages do not fail package verification.
- Squash-merge the App-authored Homebrew pin PR from registry maintenance after the required `tests` check on that exact head, without queuing GitHub auto-merge. A failed native check leaves the proposal open.
- Hold `qtbase` source builds only for the Qt 6.11.2 and md4c 0.6.0 recipe hashes, so the next scheduled run rebuilds it when Homebrew changes either file.
- Build LLVM from source without a bottle when published packages need it only as a compiler, so Homebrew skips the profile-guided `check-clang`/`check-llvm` suite. A request to publish `llvm` itself stays source-held.
- Pin Homebrew/brew and homebrew-core together, with daily update PRs and a required native formula/Cargo compatibility check, so core updates cannot silently outpace the reviewed engine.
- Advance Homebrew/brew to `a83186e02a6c4b98cd44a290cdb56e136feaa596`, which defines `std_cargo_fetch_args` used by current Cargo formulae.
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
