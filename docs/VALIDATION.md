# Validation

Verified on 2026-09-08 for Intel macOS Sequoia (15.7.9), Homebrew in `/usr/local`.

## Source checks

```sh
INTELBREW_NATIVE_TESTS=1 python3.11 -m unittest discover -s tests -q
python3.11 scripts/check-project.py
git diff --check
```

All 86 tests passed with native tests enabled. Repository checks also passed.
Regressions cover the source-download API, executable command discovery, bottle
CLI arguments, generated JSON layout, and scoped local-bottle loading. Native
checks verify that explicit path restrictions remain effective and temporary
settings are restored after errors.

## Published bottles

The [simdutf pipeline](https://github.com/adriank1410/homebrew-intel/actions/runs/34242870987)
passed build, independent installation on a fresh Intel runner, receipt checks,
`brew linkage --test`, formula tests, attestation and publication.
The [release](https://github.com/adriank1410/homebrew-intel/releases/tag/intel-34242870987-1-simdutf)
contains the bottle, source bundle and manifest.

The [pkgconf pipeline](https://github.com/adriank1410/homebrew-intel/actions/runs/34243895959)
independently passed the same build, fresh-runner verification and publication
stages for version 3.0.7. Its
[release](https://github.com/adriank1410/homebrew-intel/releases/tag/intel-34243895959-1-pkgconf)
was also downloaded on the local Mac: SHA-256, attestation, archive contents and
local formula identity checks passed. The local installation was already current,
so it was not reinstalled.

The [simdjson pipeline](https://github.com/adriank1410/homebrew-intel/actions/runs/34253631041)
passed build and fresh-runner installation for 4.6.11. Publication succeeded on
attempt 2 after a transient GitHub artifact-download HTTP 403. The
[release](https://github.com/adriank1410/homebrew-intel/releases/tag/intel-34253631041-2-simdjson)
was downloaded and verified locally (SHA-256, attestation, archive checks).
`brew intel upgrade simdjson --apply` installed zero packages because 4.6.11 was
already installed. No local reinstall or compilation was forced.

The publisher created [PR #19](https://github.com/adriank1410/homebrew-intel/pull/19).
Its required PR workflow needed a one-time human approval under `GITHUB_TOKEN`.
After approval, [registry maintenance](https://github.com/adriank1410/homebrew-intel/actions/runs/34255586336)
validated and merged it as `github-actions[bot]` through the protected branch.
This proves the controller's merge path, but not a fully unattended cycle.

The private App subsequently completed the unattended path for `mpdecimal` 4.0.1:
[build, fresh-runner tests and publication](https://github.com/adriank1410/homebrew-intel/actions/runs/34270876910),
[PR #21](https://github.com/adriank1410/homebrew-intel/pull/21) with automatically
started required tests, and
[registry maintenance](https://github.com/adriank1410/homebrew-intel/actions/runs/34272007473)
which merged it as `app/intel-bottle-publisher`. No human CI approval or manual
merge was used. Local download, SHA-256, attestation and archive checks passed;
`brew intel upgrade mpdecimal --apply` correctly installed zero packages because
4.0.1 was already installed. The complete local suite with native tests enabled
passed all 124 tests at that revision.

The [Tor and libmaxminddb pipeline](https://github.com/adriank1410/homebrew-intel/actions/runs/34280013654)
passed build, independent runner verification and publication for Tor 0.4.9.12,
libmaxminddb 1.14.0 and the missing OpenSSL 3.6.4 dependency bottle. The App merged
registry PRs #24 and #25. Local verification checked hashes, attestations and
archives, including Tor's required LICENSE bytes. The Mac then poured Tor
0.4.9.11 -> 0.4.9.12 and libmaxminddb 1.13.3 -> 1.14.0; both receipts retained
`homebrew/core` and `poured_from_bottle: true`. `brew linkage --test tor libmaxminddb`
and version checks passed; the second apply installed zero bottles. OpenSSL was
already current locally. A stale sibling-PR merge-state error observed during
registry reconciliation was fixed in PR #26 with a regression test.

## Local client verification

```sh
brew intel doctor
brew intel plan simdutf
brew intel upgrade simdutf --apply
brew linkage --test homebrew/core/simdutf
brew intel upgrade simdutf --apply
/usr/local/Cellar/simdutf/9.1.1/bin/sutf-benchmark --random-utf8 10240 -I 10
```

The client downloaded the published bottle, verified its attestation and metadata,
and upgraded `simdutf` from 9.1.0 to 9.1.1 by pouring the archive. The receipt
reports `poured_from_bottle: true`, `built_as_bottle: true`, and `homebrew/core`.
The old keg remains, while the executable and `opt` links point to 9.1.1.
The second apply installed zero packages.

The new x86_64 executable also converted `Zażółć gęślą jaźń 😀` from UTF-8 to
UTF-16LE and back with an exact byte comparison. Test files were temporary.

A local `brew test homebrew/core/simdutf` invocation selected the older 9.1.0
formula from the local core checkout, so it is not counted as evidence for the
new version. The version-specific benchmark and conversion tests above exercised
9.1.1 directly. Fresh-runner CI tested 9.1.1 through `brew test`.

## Additional local acceptance checks

`node` 26.8.1 passed UTF-8 conversion checks after the library update. A run with
`DYLD_PRINT_LIBRARIES=1` confirmed that it loaded
`/usr/local/Cellar/simdutf/9.1.1/lib/libsimdutf.35.0.0.dylib`.

`HOMEBREW_NO_AUTO_UPDATE=1 brew intel upgrade qt --apply` exited with status 1 and
`No compatible matching bottle; nothing installed`. No installation was started.
The new `--available` mode was exercised locally through `python3.11 -m
intelbrew.cli upgrade --available --apply`: all 39 unavailable Qt roots were
reported and skipped, with zero installations. Its `--json` output parsed as JSON.

## Limits of this result

This validates seven published package records and local bottle installation for
`simdutf`, `tor` and `libmaxminddb`, not every target or future Homebrew revision. The target names are candidates. Scheduled
builds and hourly registry maintenance were enabled on 2026-09-08. License
review can block dependencies. A separate branch run confirmed that a blocked
`gnupg` root did not suppress building and verifying `simdjson`. The private App
production cycle passed as described above. There is no claim of complete Intel or Qt
coverage, reproducible builds, atomic rollback, or support outside Intel Sequoia.

## Installed coverage expansion (2026-09-09)

The real read-only `python3.11 -m intelbrew.cli sync --json` inspected the Mac's
installed core metadata: 46 existing monitored names, 168 eligible additions and
196 exclusions. The exclusions comprised 150 requiring license review, 39 Qt
family exclusions, four blocked source roots, one HEAD/options installation, one
disabled recipe and one unavailable current recipe (`openssl@1.1`). NumPy was
eligible for monitoring; the current recipe had no Intel bottle and a blocked GCC
source dependency. This is an eligibility audit, not proof that every added target
can already be built or installed.

The implemented expansion passed all 172 tests with native Homebrew integration
enabled on the deployed revision, plus `python3.11 scripts/check-project.py` and
`git diff --check`. PR #27 added shared native preflight, bounded additive sync,
behind-branch recovery and runner preparation fixes.

The [corrected Pydantic trial](https://github.com/adriank1410/homebrew-intel/actions/runs/34291062114)
built `lz4` 1.10.0 and `pydantic` 2.13.5, then installed and verified them on a
separate fresh Intel runner. The runner preparation moved 23 preinstalled
framework-Python symlinks into its ephemeral backup; Python 3.13 and 3.14 bottles
then poured and linked successfully. Publication was correctly skipped on this
source branch. An earlier trial had exposed this collision; its preflight and
`pcre2`/`protobuf` builds passed before the superseded run was cancelled.

The real coverage publication lifecycle completed in two batches. The App updated
[PR #28](https://github.com/adriank1410/homebrew-intel/pull/28) onto the current base,
started the required checks and merged its 167 additions through branch protection.
A normal local `brew intel sync --apply` then safely reused the retained branch to
propose `yyjson` in [PR #29](https://github.com/adriank1410/homebrew-intel/pull/29);
the App validated and merged that PR too. No manual merge or bypass was used for
either coverage PR. Both changes were pulled into the installed tap on `main`.

A subsequent `brew intel sync --apply` reported **214 monitored, zero eligible
additions, 196 exclusions**, and created no PR. Exclusions still comprise 150
license reviews, 39 Qt entries, four blocked source roots, one options/HEAD
installation, one disabled formula and one unavailable current recipe. Local
`brew intel doctor` passed; `brew intel upgrade --available --json` returned an
empty installation plan and reported NumPy plus 39 Qt roots as unavailable.
The local verification did not trigger source compilation or broad upgrades.


The production `all` run on the expanded list used one native preflight for all
214 roots. It found 129 already covered roots, 45 eligible build candidates and
40 blocked roots, then selected `you-get`, `yyjson`, `aom` and `archi-steam-farm`
under the four-root rotating batch limit. NumPy's GCC dependency remained an
explicit source-build exclusion. This confirms the full-list selection path;
build and publication outcomes are recorded separately below.


The `aom` build stopped at the source-provenance boundary: its upstream recipe
fetches a Git checkout instead of a checksum-backed archive, so the native bridge
rejected it with `Non-archive/VCS resource needs review`. The source-build policy
now excludes `aom` before runner allocation for that root. It stays monitored and
can still use an available official bottle; the source guard was not weakened.


`archi-steam-farm` exposed another preinstalled runner link: the official .NET
bottle poured, but `/usr/local/bin/dotnet` still pointed into the runner account's
`.dotnet` directory. Runner preparation now also retains that exact symlink in
its backup, deriving the account home from the OS account database. Other links
and regular files remain untouched. The helper was renamed to
`scripts/quarantine-runner-links.py` to describe its expanded scope.
