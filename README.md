# Personal Intel bottles for Homebrew

**Sequoia (macOS 15), x86_64, `/usr/local`. Source implementation 0.1.0 preview.**

This is a personal, deliberately bounded bottle service for `adriank1410`. It does
not replace Homebrew, fork `homebrew/core`, shadow core formulae, or rewrite
package dependencies. The tap supplies one external command, `brew intel`, and a
reviewed registry of bottles built from unchanged official core recipes.

**Initial state:** the registry is empty. The source has local unit/syntax checks;
no successful native macOS build is claimed by this distribution. Complete the
bootstrap CI run and review its independent pour/test job before relying on it.
A preview tag or a green Linux unit-test job is not evidence that a bottle exists.

[Polski](README.pl.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Operations](docs/OPERATIONS.md) · [Security](SECURITY.md) ·
[Third-party distribution](THIRD_PARTY.md)

## Why an external command instead of shadow formulae?

Homebrew can install a local `.bottle.tar.gz` using its embedded recipe and
receipt. A bottle built as `homebrew/core/foo` remains `homebrew/core/foo` after
installation. This avoids migrating same-name Cellar racks and avoids changing
all the parents of a shared library just to point at another tap.

The trade-off is explicit: **plain `brew upgrade` does not discover this registry**.
Use `brew intel upgrade --apply` for its bottle-only path. No alias, interception,
core remote replacement, global environment change or shell-profile edit occurs.

## Install the command after the repository has actually been deployed

```sh
brew tap adriank1410/intel
brew trust --command adriank1410/intel/intel
brew intel doctor
brew intel plan simdutf
brew intel upgrade simdutf --apply
```

The first two commands install/trust the external command, not every possible
future item in the tap. They still authorize this command's executable code with
your user privileges: **narrow trust is not a sandbox**. The repository must exist
and its registry must contain a valid personal bottle, or an official compatible
bottle must be available. `gh` must already be installed for personal-artifact
attestation verification. There is no verification bypass or automatic `gh` install.

Homebrew now blocks loading package paths by default. For a verified local
bottle, the Ruby bridge temporarily permits path loading inside that one process,
then restores the setting. An explicit `HOMEBREW_FORBID_PACKAGES_FROM_PATHS`
still stops installation; no global setting is changed.

All package-changing operations require `--apply`. `plan`, `doctor`, and
`upgrade` without `--apply` do not install packages. They can read/fetch Homebrew
metadata or bootstrap Homebrew's own Ruby dependencies; this is not a promise of
zero filesystem writes by Homebrew itself.

`brew intel upgrade` without names plans all unpinned outdated core formulae. If
any required bottle is unavailable, `--apply` stops the **entire plan before
installation**. An explicit subset, such as `brew intel upgrade simdutf --apply`,
avoids making a missing Qt bottle block an unrelated update.

## Build and publish

Run the **Sequoia Intel bottles** workflow on `main`, initially with `simdutf`.
The pipeline resolves one official core commit, pins the reviewed Homebrew
engine commit, and uses `macos-15-intel` for both building and fresh verification.
It builds missing runtime, build and test dependencies in dependency order; an
existing compatible official bottle is reused, including a usable older Intel
macOS bottle. `cellar: :any` is not confused with CPU-independent compatibility.

A separate, write-scoped Linux job validates the outputs, creates artifact
attestations, publishes a unique release with bottles and associated source
bundles, then publishes a registry review branch and compare link. An authenticated
owner or connector opens the PR after reviewing the native run; Actions itself
cannot approve or open that PR. Until the owner reviews and merges it, the new
binaries are not discovered by the client. The repository documents recommended
`main` protection and a required `tests` check, but server-side settings must be
verified separately.

The 44 package names in `policy/targets.json` are **candidates**, not 44 promised
working builds. Unsupported heavy source builds and unreviewed distribution
licenses fail explicitly. `all` creates a bounded matrix, but one failed matrix
job currently blocks downstream verification/publication for that run. Start
with one root. Shared dependencies are reused between merged releases, not
magically shared between concurrent unmerged jobs.

## Safety properties and limits

The client downloads and verifies all personal bottles before its first install.
It checks hashes, package/revision identity, embedded recipe/receipt, the original
core tap, dependency drift, and a GitHub attestation bound to this repository,
`main`, the exact workflow commit, and the expected workflow path.

Installation uses a **process-local** guard on Homebrew's source-build method;
if Homebrew requests compilation for the root or a dependency, it raises instead.
`--force-bottle` alone is not considered a no-source guarantee. The guard does
not sandbox formula evaluation or `post_install`, and depends on an internal
Homebrew interface. Native CI must validate compatibility after engine updates.

Existing versioned kegs are retained, cleanup is disabled for these subprocesses,
and same-version reinstallation, downgrades, foreign-tap migration, custom
options/HEAD and changes to pinned packages are refused. There is no `rm`,
uninstall, forced overwrite, automatic rollback or global configuration edit in
the deployment/client path. A multi-package installation can still partly succeed
before a later failure. A journal records that state; this is **not an atomic
transaction**. Reverse-dependent ABI changes are not proven safe merely by the
presence of a bottle; review linkage after shared-library upgrades.

## Costs and service lifetime

Standard GitHub-hosted runner minutes for public repositories are currently free,
but Actions artifact storage is a separate shared quota/billing category. Inter-job
artifacts have **one-day retention**, no Actions cache is used, and a candidate
transfer is bounded to 500 MB. Durable distribution uses Releases. This is **not a
guarantee of a zero bill**. No account billing or payment setting is changed.

Scheduled builds are disabled unless the repository variable
`INTELBREW_ENABLE_SCHEDULE` is explicitly set to `true`; the declared schedule is
weekly. No paid/larger or self-hosted runner is selected automatically. GitHub has
announced retirement of Intel macOS runners in August 2027. Reassess then; the code
must fail rather than quietly switch to your laptop or paid CI.

## Local validation and build requests

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check-project.py
python3 scripts/deploy.py  # dry-run / historical fresh-deployment helper
```

This repository has already been created. Do not re-run the fresh-deployment path
against it. To request one reviewed target through the connector-safe trigger,
change `policy/build-request.json` on `main` and increment `sequence`. Server-side
protections must be applied and verified separately. No deployment/build request
installs the tap or changes packages on the owner's Mac. See
[Operations](docs/OPERATIONS.md) for failure recovery and the distinction between
a dispatched workflow and a successful native build.

## License

Original project code: **BSD-2-Clause**, [LICENSE](LICENSE). Official Homebrew
recipe material: Homebrew contributors' BSD-2-Clause, preserved in
[LICENSES](LICENSES/Homebrew-BSD-2-Clause.txt). Each distributed program retains
its own license and obligations; see [THIRD_PARTY.md](THIRD_PARTY.md).
No code from `fabiomanz/intel-bottles` was copied into this implementation.

## Primary references

- [Homebrew bottles](https://docs.brew.sh/Bottles)
- [Native local-bottle loader, reviewed engine commit](https://github.com/Homebrew/brew/blob/71f6877d19f2179cf47caea84565083cdc950cc2/Library/Homebrew/formulary.rb)
- [External commands](https://docs.brew.sh/External-Commands)
- [Tap trust](https://docs.brew.sh/Tap-Trust)
- [Attestation verification](https://cli.github.com/manual/gh_attestation_verify)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Intel runner retirement announcement](https://github.com/actions/runner-images/issues/13045)
