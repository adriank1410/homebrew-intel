# Architecture decision: preserve core identity

Status: implemented in source; native lifecycle validation is a release gate.

## Rejected alternatives

Replacing `HOMEBREW_CORE_GIT_REMOTE` changes the authority for the whole core
catalog. A bottle-domain mirror alone cannot add metadata for a missing platform.
Same-name shadow formulae avoid replacing the whole catalog, but introduce rack,
provenance and cross-tap dependency migration issues, especially for shared
libraries. Renamed `foo-intel` formulae alter paths and dependency identity.
None of those is required for a personal bottle cache.

## Selected design

An external command runs a small Python planner and a native Ruby bridge under
`brew ruby`. Homebrew itself resolves conditional dependencies, bottle
compatibility, platform requirements and installed-state semantics. The planner
selects an already-installed current core package, an official compatible bottle,
an exact personal registry match, a new source build **only on an ephemeral CI
runner**, or a client error.

Runtime edges are always visited. Build/test edges are visited only when that
node will be built in CI. Personal records contain the complete runtime closure
at build time. Recipe hash, package revision, version scheme or runtime dependency
drift invalidates reuse. CI re-plans stale records as builds; the client refuses
them. This is intentionally conservative, not an ABI solver.

## Pipeline

`plan -> build on Intel Sequoia -> fresh Intel Sequoia pour/tests -> Linux publish`

The core snapshot is resolved once per workflow. The Homebrew engine is pinned
in policy. Build jobs have read-only repository permissions. Preseeded kegs and
links are moved into a fresh runner-only backup so they cannot silently satisfy
undeclared build dependencies. Every source-built dependency receives its own
`--build-bottle` invocation.

The verifier starts with a fresh Cellar, installs only bottles, checks receipts,
linkage and formula tests, then confirms idempotent installed state. Publication
validates expected filenames, sizes, hashes, recipe and core receipt before
signing outputs and creating a unique release. Artifacts are never treated as
shell scripts. Completed artifacts determine independent downstream matrices,
so a failed sibling cannot suppress verified packages. The publisher opens a
registry-only PR. A trusted main-branch reconciler verifies its records against
the attested release manifest, updates an outdated base, dispatches required
tests and enables ruleset-compliant auto-merge. Source changes still require
manual review; this does not approve or auto-merge arbitrary PRs.

## Client source-build guard

`--force-bottle` is not a sufficient no-compile policy. A process-local module
prepended to `FormulaInstaller` refuses `build`. The bridge checks the expected
method before use. A guard unit test verifies the Ruby interception; the native
`doctor` and verifier check it against the installed Homebrew engine. No Homebrew
source file is edited. An internal API change must fail closed.

This does not prevent executable recipes, post-install steps or a compromised
binary from running arbitrary user-level code. It is an installer fallback
policy, not an operating-system sandbox. It also cannot make multi-package
updates atomic or prove ABI compatibility for already-installed reverse deps.

## Lifecycle release gate

A real macOS run is required for the exact Homebrew APIs, initial build and pour,
upgrades from a pre-existing older locally built keg, preservation of old kegs/
links, attestation propagation and repository behavior. A fresh-runner test alone
does not prove the older-keg upgrade scenario. Keep the project a preview until
those checks pass.

## Scope

No casks, HEAD/default-option overrides, foreign taps, ARM/Tahoe client,
nonstandard prefix, automatic same-version rebuild migration, reverse-ABI repair,
unattended client scheduler, paid runner fallback, full-core rebuild or general
binary compatibility guarantee.
