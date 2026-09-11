# Homebrew Intel bottles

**Preview:** bottle builds, independent verification and automated publication
have passed production tests. Coverage remains limited; see the current
[bottle registry](registry/), [validation results](docs/VALIDATION.md) and
[acceptance checklist](CONTRIBUTING.md).

Prebuilt Homebrew packages for Intel Macs running macOS Sequoia (15).
`brew intel` uses official bottles when available and verified builds from this
repository when they are missing. Packages keep their `homebrew/core` identity;
Homebrew stays in `/usr/local`.

[Polski](README.pl.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Operations](docs/OPERATIONS.md) · [Validation](docs/VALIDATION.md) ·
[Security](SECURITY.md) · [Third-party distribution](THIRD_PARTY.md)

## Install and use

```sh
brew tap adriank1410/intel
brew trust --command adriank1410/intel/intel
brew intel doctor
brew intel plan simdutf
brew intel upgrade simdutf --apply
```

The first two commands add the tap and trust its `intel` command.
An existing `gh` installation is required to verify GitHub attestations.

`plan`, `doctor`, and `upgrade` without `--apply` are non-installing client
operations. Package changes require `--apply`. Add `-v` / `--verbose` for
detailed attestation verification diagnostics, source URLs, and transaction journal paths.

## How updates are selected

For each requested formula, the client prefers:

1. the current formula version, if already installed;
2. an official Homebrew bottle compatible with the Mac;
3. a reviewed bottle from this repository's registry.

If no usable bottle exists, the operation stops. It never compiles on the Mac.
Existing versioned kegs are preserved, and the client does not uninstall,
force-overwrite, clean up, or roll back packages. A multi-package operation is
not atomic, so a later failure can leave earlier packages installed.

Normal `brew upgrade` does not read this registry. Use `brew intel plan` and
`brew intel upgrade --apply` when this bottle service should participate in an
upgrade. An explicit package list is useful when another candidate is missing;
for example, `brew intel upgrade simdutf --apply` does not wait for Qt.

To update only packages whose entire dependency chain has a bottle:

```sh
brew update &&
brew intel upgrade --available --apply &&
brew upgrade --cask &&
brew cleanup
```

The Intel command covers official and repository bottles for `homebrew/core`.
Casks use standard Homebrew, including its dependency handling. Formulae from
other taps need separate updates. Unavailable roots are reported and skipped. Without `--available`, incomplete
coverage still stops the entire operation. Installation remains manual; no local
background service is installed. `brew cleanup` is a separate user choice and
can remove the old versions retained by this client.

Every repository bottle is checked before installation for its hash, formula and
revision, recipe and dependency metadata, original core identity, and GitHub
attestation. The source-build guard is process-local and fails closed if
Homebrew tries to compile during a client operation. This is an installer
policy, not an operating-system sandbox.

Verified local bottles are loaded through a process-scoped Homebrew exception.
An explicit `HOMEBREW_FORBID_PACKAGES_FROM_PATHS` still blocks installation;
global Homebrew settings are not changed.

## Coverage and builds

[The registry](registry/) lists the bottles available to the client.
[The target list](policy/targets.json) contains monitored build candidates, not
guaranteed bottles. Monitoring includes stable, correctly identified installed
`homebrew/core` formulae regardless of license or source-build feasibility.
Redistribution policy, VCS sources, blocked source builds and Qt constraints are
enforced later when planning a source build; they do not prevent use of an
available official or personal bottle.

GPL, LGPL, AGPL and MPL packages use a source-required publication profile.
Their releases include the recipe, declared sources and patches, upstream license
notices and downloaded Go/Cargo source inputs, with a checked source index.
The client provides a link to the matching source bundle (displayed with `-v` / `--verbose`). Git sources pinned to a
full commit, including submodules pinned by that commit, can be archived; unpinned VCS and other VCS strategies still require further
support. See [source publication requirements](docs/LICENSE-REVIEW.md).

`brew intel coverage` compares installed formulae with the target list locally.
`brew intel sync` additionally checks current metadata and installation state. It
excludes aliases and non-core names, disabled formulae or those without a stable
version, options or HEAD installs, foreign installs, versions newer than stable,
and missing or invalid metadata. Both are read-only without `--apply`; `--json`
includes individual reasons.

`brew intel sync --apply` publishes eligible new core names through one additive
coverage PR. It requires the repository owner's authenticated `gh` account. The owner can
insert `brew intel sync --apply &&` after `brew update &&` in the update chain
above. Other users can inspect coverage with `brew intel sync` without publishing. The
five-minute maintenance job rechecks eligibility and merges only the exact validated
head after protected checks pass. A later `brew update` brings the new list to the
Mac. Repeated sync reuses the pending PR; uninstalling a formula does not remove
it from monitoring. External-tap names, casks, local paths and full inventory
snapshots are not uploaded. Package installation remains a separate command.

Set `INTELBREW_ENABLE_SCHEDULE=true` to enable daily candidate checks and five-minute
PR maintenance. Scheduled checks inspect uncertain candidates together on one
Intel runner and select every eligible root needing a build. Already covered roots
do not receive separate build/verification runners. Blocked roots are reported
without stopping eligible siblings. Build and verification jobs remain limited to
five concurrent Intel runners. Planning fails explicitly if more than GitHub's
256-job matrix limit need builds, rather than silently omitting roots.

The workflow builds and verifies on `macos-15-intel`, then publishes through a
separate validation step. It uses official core recipes, a pinned Homebrew
engine, fresh-runner bottle installation, formula tests, linkage checks, and
attestation. Each successful root can proceed even if another root fails.
Publication creates a registry PR. Automation checks its records against the
attested release manifest, dispatches tests, and merges the exact validated head
once the protected branch checks pass. It never queues GitHub auto-merge. The client sees the records after that merge.

To request one reviewed target, edit `policy/build-request.json` on `main` and
increment `sequence`. See [Operations](docs/OPERATIONS.md) for the review and
failure-recovery procedure. Local checks are:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check-project.py
```

## Scope

The supported client is Intel Sequoia Homebrew under `/usr/local`. The project
does not cover Apple Silicon, other prefixes, casks, foreign taps, HEAD or
custom-option builds, unattended client scheduling, paid-runner fallback, or a
general ABI-compatibility guarantee.

## License

Project code is [BSD-2-Clause](LICENSE). Homebrew recipe material retains the
license and notices in [LICENSES](LICENSES/Homebrew-BSD-2-Clause.txt) and
[THIRD_PARTY.md](THIRD_PARTY.md).
Distributed programs retain their own licenses.
