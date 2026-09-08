# Security policy and threat model

This project is a **preview** until real Intel macOS lifecycle validation passes.
There is no security warranty or established response-time commitment. Report
sensitive issues through GitHub private vulnerability reporting when enabled;
otherwise contact the repository owner privately before publishing an exploit.
Do not include tokens, personal audit files or unrelated machine data in issues.

## Trust boundaries

The operator trusts official Homebrew recipes and their upstream source projects,
the reviewed pipeline commit, GitHub-hosted runners, GitHub/Sigstore attestation
infrastructure, their own GitHub account and installed Homebrew/gh/Python tools.
A malicious upstream recipe, binary, compromised owner account or runner can
still be harmful. Narrow Homebrew tap/command trust is **not a sandbox**.
A signed attestation identifies a producing workflow; it does not establish that
its output is benign, reproducible, legally distributable or free of CVEs.

The client only accepts registry entries for Sequoia x86_64/default Cellar, with
exact package/revision/recipe identities, associated sources, digest/size limits,
and an attestation from this repo's main workflow at the recorded commit. It
rejects archive path traversal, duplicate members, foreign-keg content, special
files, unexpected metadata and forged core identity. Archives are inspected,
not extracted by our Python code. Homebrew performs the actual extraction/pour.
Symlinks within a legitimate keg are permitted and are **not** a filesystem
sandbox; do not equate metadata inspection with a full binary security audit.

## Source fallback

The install subprocess prepends a guard to Homebrew's `FormulaInstaller.build`.
This is scoped to that process and not persisted in Homebrew. It blocks the
installer's source-fallback path for dependencies as well as the requested root.
It does not block arbitrary compilation/execution deliberately performed by a
formula's post-install code. An incompatible private Homebrew API must stop the
operation; do not add a guard-disable environment variable or fallback.

## CI

Build and native test jobs have read-only repo permissions. Publication uses a
separate fresh Linux job and is limited to the trusted main-branch workflow;
there is no `pull_request_target` or PR-triggered publication. Checkout tokens
are not persisted. External actions are pinned to full commits. No self-hosted
or paid runner is silently selected. The source-build environment strips job
GitHub/OIDC tokens, but arbitrary code on the same runner is not an isolation
boundary: untrusted inputs should not be promoted to trusted source.

Downloaded artifacts are validated as bounded data. The publishing stage never
sources a downloaded shell/Python/Ruby script. The writer creates a unique
release and registry branch. Registry automation opens PRs and enables auto-merge
only for same-repository bot PRs whose complete registry diff matches an attested
release manifest. Required tests and branch protection remain effective. It does
not approve reviews, replace release assets, move tags, or force-push. GitHub
bundles permission to create and approve PRs in one repository setting; the
workflow uses creation only. Inspect repository
protections separately; Markdown policy is not a server-side access control.

## Retained state and limitations

No cleanup/uninstall/global-profile changes happen on the client. Installations
are not atomic; old kegs and a journal are retained, but link changes and partial
updates remain possible. Already installed reverse dependencies require ABI and
linkage review. Concurrent ordinary `brew` use is unsupported. Pinning source
commits gives provenance, not bit-for-bit reproducibility (runner images and
external build dependencies can change). Tests with fixtures are not native
macOS integration tests. See the acceptance checklist in CONTRIBUTING.md.
