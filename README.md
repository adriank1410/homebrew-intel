# Homebrew Intel bottles

**Preview:** two packages have passed build and bottle verification; broader
release acceptance is still incomplete. See [validation results](docs/VALIDATION.md)
and the [acceptance checklist](CONTRIBUTING.md).

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
operations. Package changes require `--apply`.

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
The 44 names in [the target list](policy/targets.json) are build candidates,
not guaranteed coverage. New package names are not added automatically.
Qt and other heavy builds listed in [the policy](policy/config.json) are excluded.
License review, dependency availability and build limits can also block a target.
Scheduled builds are disabled by default.

The workflow builds and verifies on `macos-15-intel`, then publishes through a
separate validation step. It uses official core recipes, a pinned Homebrew
engine, fresh-runner bottle installation, formula tests, linkage checks, and
attestation. Publication creates a registry review branch; the registry is not
client-visible until the reviewed change is merged.

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
