# Source-build holds

These holds in `policy/config.json` apply only when the planner would select
`provider=build`. They are not successful builds and do not supply missing
bottles. A hold matches one formula file when `dependencies` is empty, or an
exact set of dependency recipe files when that list is present. An empty list
matches that formula file alone.
Scheduled native preflight reports the blocked root and continues with
eligible siblings. The CI build planner enforces the same policy for manually
requested roots and for transitive build, runtime and test dependencies.

Compatible official bottles and matching personal bottles remain eligible.
Personal-bottle dependency drift still forces re-planning; a stale bottle cannot
bypass a source hold. The client still refuses an incomplete bottle graph.
No target, existing registry record, release, source guard, signature check,
license requirement or recipe is removed or weakened by these holds.

`source_build_holds` is currently empty. The Qt 6.11.2 pair and the ocrmypdf
17.13.0 recipe below were removed from `source_build_holds` after publication.

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

The hold was the pair of recipe-file hashes in `source_build_holds`: Qt 6.11.2
`77fb639065c7f11b3c9c781a38013c2172f6c1fc3e5fe58efc1af975b6f861c5` together with
md4c 0.6.0 `de1668120c0626d17e55981476fb6169f112606c43e4bc35991d50aff21666cd`.
It applied only when a published package would be built from that exact pair.
Other Qt modules are not individually excluded. A complete, matching bottle
graph is still installed.

Pin maintenance checks upstream every three hours and moves `core_commit`
through the reviewed native gate when Homebrew's main branch moves.
The next bottle run then sees the new recipe files. If either hash differs,
the hold does not match and `qtbase` is built with the normal scheduler. No
policy edit is required for that retry. After a successful publication, delete
the stale hash entry so the policy file does not keep a pair that can no
longer occur.

That deletion is done. `qtbase` 6.12.0_1 is published as
`intel-39399228992-1-qtbase`. On core
`848bfa81ec12746fdd90a48d9bbc65e518e4cd39` the formula file SHA-256 is
`7964578a98c1d8e8d443cd2b83e1874cfecd280a5779a047f279db2321c1e97d`, which is
the bottle's `formula_sha256`. The 6.11.2 pair was removed from
`source_build_holds`.

Do not delete the assertion, patch the recipe silently, relabel an old bottle's
dependency hashes, or disable dependency drift detection to obtain a green run.

### ocrmypdf: pypdfium2 cannot fetch ctypesgen inside the sandbox

Evidence: [Sequoia Intel bottles run 36781482650](https://github.com/adriank1410/homebrew-intel/actions/runs/36781482650).
The pinned core was `c799c7ec3bc29124301389b207b3430368748fc0`.
`ocrmypdf` 17.13.0 installs `pypdfium2` 5.13.0 from
`pypdfium2-5.13.0.tar.gz`
(`7ca2d8e31bd8d0d40c496416b7d8bea423388669ffd494929f50e8c3a82326b8`).
That package's build requires `ctypesgen` from a git URL. Homebrew's isolated
pip install uses `--no-binary=:all:`, so the fetch fails and repeating the same
formula file does not succeed. The Qt hold was a pair because both recipe
files decided that failure. This one is the formula file alone:
`39b84c91d0b8af18ffcd5bddbcae49cabbac0ea6ac26e8d872083ead3f9f2499`.

The hold expired when `ocrmypdf.rb` changed. It did not publish a bottle by
itself. A manual dispatch of that exact file stopped in the planner with
`Source build excluded by policy: ocrmypdf` instead of starting the source
build. After a successful publication, delete the stale hash.

That deletion is done. `ocrmypdf` 17.13.0 is published as
`intel-39377814000-1-ocrmypdf`. On the same core pin the formula file SHA-256
is `1378b58fcd1888c0430167dc0c72a8b99202acc6667972b290e8fdb739119def`, which
matches the bottle. The previous hash was removed from `source_build_holds`.

### LLVM: bottle builds run the profile-guided suite; compiler-only installs do not

[`deno` job 106819757455](https://github.com/adriank1410/homebrew-intel/actions/runs/35743217955/job/106819757455)
was cancelled at the six-hour runner limit. Compilation of the early LLVM 23.1.1
stages had finished. At 19:35 UTC the formula started
`cmake --build . --target check-clang check-llvm`, and that command was still
running at 21:46 UTC. Deno itself had not started.

Homebrew enables that profile-guided path only for a stable macOS bottle build
of the unversioned formula. `llvm@22` 22.1.8 is already published because a
versioned formula skips the path. `--build-from-source` skips it as well.

`deno` needs `llvm` only while compiling. `lld` and `rust` need unversioned
`llvm` at runtime. The planner still classifies that LLVM and those runtime
dependents as transient when no published package needs them: the install mode
would be `--build-from-source`, and they would stay out of the candidate set.
CI does not start that combined root. The separately reviewed `llvm-no-pgo`
profile described below permits a standalone LLVM bottle build.

Skipping the profile-guided suite is not enough when another source build needs
that LLVM at runtime. On
[run 37103277240](https://github.com/adriank1410/homebrew-intel/actions/runs/37103277240)
`--build-from-source` compiled LLVM in about 3h49m (09:45–13:34 UTC) and then
`rust` in about 1h12m. `deno` reached `cargo install` at 14:58 UTC and was
cancelled at the six-hour limit. The same cancellation happened again on the
following sweep. CI now refuses that closure before compilation: a plan that
source-builds a `blocked_source_builds` formula and also source-builds a
formula that needs it at runtime or test time stops with
`Source build excluded by policy: llvm`. The schedule records the root as
blocked and continues with eligible siblings. A manual dispatch hits the same
error in the build planner. A matching LLVM bottle removes the source build,
so the root can be selected again. A build-only LLVM edge, with no source-built
runtime dependent, still uses `--build-from-source`.

### Approved LLVM recipe variant: `llvm-no-pgo`

The owner approved this explicit variant on 13 October 2026. It changes only
the upstream `pgo_build` assignment to `false`, retaining the rest of the recipe.
The PGO bootstrap and its conditional `check-clang`/`check-llvm` stage therefore
do not execute. This trades upstream PGO optimization for a build that can fit
the standard hosted runner budget; it does not claim upstream recipe identity.
An unfamiliar or ambiguous upstream assignment fails before compilation.

`policy/config.json` enables the variant only for unversioned `llvm`, now a
reviewed standalone target. The recipe is changed temporarily on the ephemeral
build runner, installed with `--build-bottle`, and restored on context exit.
The bottle record includes `build_profile: llvm-no-pgo`. Its `formula_sha256`
identifies the original pinned core recipe, while `recipe_sha256` identifies the
executed variant embedded in the bottle. The source bundle retains both
`recipe/llvm.rb` and `recipe/llvm-no-pgo.rb`, an explicit profile label and rebuild
instructions. Verification checks the exact permitted transformation and both
hashes before accepting the candidate. Clients need the updated tap to read the
new optional profile field.

The usual independent bottle pour, receipts, linkage checks, formula test,
source validation and artifact attestation remain mandatory. The raw LLVM hold
still rejects a closure that would compile LLVM and a source-built runtime
dependent in one job. A matching published LLVM bottle allows Rust and those
dependent roots to proceed in later waves. No hosted timeout is increased and
`llvm@22` is not substituted for the dependency named by core.

## Existing resource exclusions

`qt`, `qtwebengine` and `qtwebview` retain their existing resource-policy
exclusions. This incident does not change the supported platform or the client
installation policy. Normal source failures on otherwise eligible roots still
fail their jobs; no `continue-on-error` or blanket success conversion is added.
