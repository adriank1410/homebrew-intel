# Source-build holds

These holds in `policy/config.json` apply only when the planner would select
`provider=build`. They are not successful builds and do not supply missing
bottles. Scheduled native preflight reports the blocked root and continues with
eligible siblings. The CI build planner enforces the same policy for manually
requested roots and for transitive build, runtime and test dependencies.

Compatible official bottles and matching personal bottles remain eligible.
Personal-bottle dependency drift still forces re-planning; a stale bottle cannot
bypass a source hold. The client still refuses an incomplete bottle graph.
No target, existing registry record, release, source guard, signature check,
license requirement or recipe is removed or weakened by these holds.

## Incident: 22 September 2026

Evidence: [Sequoia Intel bottles run 35743217955](https://github.com/adriank1410/homebrew-intel/actions/runs/35743217955).
The pinned Homebrew engine was `1d86792829d8f5986edb6272fc5b36a38d055f5c`
and core was `5447a4b46cb84f266998a4e5e25e3e26941c9447`.
Both affected jobs passed runner preparation and the 16 native smoke tests.

### Qt: an upstream dependency incompatibility, not a timeout

[`qtbase` job 106810243028](https://github.com/adriank1410/homebrew-intel/actions/runs/35743217955/job/106810243028)
built Qt 6.11.2 after installing md4c 0.6.0. Compilation of
`src/gui/text/qtextmarkdownimporter.cpp` failed at the `DialectGitHub` static
assertion with `1068812 == 1593100` (false). Repeating the same pinned recipe and
dependency graph does not resolve this source incompatibility.

`qtbase` is therefore temporarily listed in `blocked_source_builds`. This is a
name-based hold requiring review to remove, **not** an automatically expiring
version range. Other Qt modules are not individually excluded, but cannot
rebuild a held Qt dependency. Existing matching bottle graphs are unaffected.

Removal criteria: identify the official core/Qt/md4c change that resolves the
incompatibility; update the reviewed engine/core pair as needed; remove only
the `qtbase` hold in a PR; and demonstrate a real Intel source build, independent
pour, formula test, linkage check and normal attested publication. A green
planner/unit check alone does not prove this. Update the incident regression
expectations and documentation to match that reviewed recovery.

Do not delete the assertion, patch the recipe silently, relabel an old bottle's
dependency hashes, or disable dependency drift detection to obtain a green run.

### LLVM: bottle builds run the profile-guided suite; compiler-only installs do not

[`deno` job 106819757455](https://github.com/adriank1410/homebrew-intel/actions/runs/35743217955/job/106819757455)
was cancelled at the six-hour runner limit. Compilation of the early LLVM 23.1.1
stages had finished. At 19:35 UTC the formula started
`cmake --build . --target check-clang check-llvm`, and that command was still
running at 21:46 UTC. Deno itself had not started.

Homebrew enables that profile-guided path only for a stable macOS bottle build
of the unversioned formula. `llvm@22` 22.1.8 is already published because a
versioned formula skips the path. `--build-from-source` skips it as well.

`deno` needs `llvm` only while compiling, but `lld` needs `llvm` at runtime.
The planner therefore installs both from source and leaves them out of the
candidate set whenever no published package needs them at runtime or in its
formula test. A request whose published packages need a new `llvm` bottle,
including a direct `llvm` root, stays source-held.

Removal criteria for the direct hold: publish a reviewed Intel bottle through
the normal attested path, including independent pour, formula test and linkage
checks. Do not raise `timeout-minutes` beyond the hosted-job limit, patch the
formula to delete `check-clang`/`check-llvm` while still passing
`--build-bottle`, or substitute `llvm@22` for the dependency the recipe names.

## Existing resource exclusions

`qt`, `qtwebengine` and `qtwebview` retain their existing resource-policy
exclusions. This incident does not change the supported platform or the client
installation policy. Normal source failures on otherwise eligible roots still
fail their jobs; no `continue-on-error` or blanket success conversion is added.
