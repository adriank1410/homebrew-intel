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

### LLVM: the hosted-runner source-build budget is insufficient

[`deno` job 106819757455](https://github.com/adriank1410/homebrew-intel/actions/runs/35743217955/job/106819757455)
was cancelled after approximately six hours while compiling LLVM 23.1.1 and
running `check-clang check-llvm`. It had not reached Deno compilation. The
pinned LLVM formula has no Intel macOS bottle, so a source dependency was being
built even though the root was named `deno`.

The `llvm` hold covers direct roots and transitive dependencies, including the
isolated build-only compiler path. A matching verified LLVM bottle or a
compatible official bottle can still satisfy the graph without lifting it.
This does not assert that every LLVM version is inherently impossible to build;
it bounds the current supported service based on the observed failure.

Removal criteria: demonstrate a reviewed source build that fits the supported
runner's time, disk and publication limits, followed by independent verification;
or obtain a compatible bottle through the existing verified provider paths.
Do not raise `timeout-minutes` beyond the hosted-job limit, skip upstream tests,
substitute a different-version compiler as the declared dependency, or switch
to paid/self-hosted runners without a separately reviewed scope change.

## Existing resource exclusions

`qt`, `qtwebengine` and `qtwebview` retain their existing resource-policy
exclusions. This incident does not change the supported platform or the client
installation policy. Normal source failures on otherwise eligible roots still
fail their jobs; no `continue-on-error` or blanket success conversion is added.
