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

## Limits of this result

This validates two published packages and the local client installation path for
`simdutf`, not every
target or future Homebrew revision. The 44 target names are candidates. Scheduled
builds are disabled, license review can block dependencies, and a failing root
in the current `all` matrix can block downstream verification for the batch.
Registry updates still require review. There is no claim of complete Intel or Qt
coverage, reproducible builds, atomic rollback, or support outside Intel Sequoia.
