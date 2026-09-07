# Contributing and release acceptance

Keep the project personal, bounded and understandable. Use a PR for changes to
code, workflow pins, targets, licensing exceptions or registry entries. Explain
the failure and add a regression test; do not disable source guards, attestation
verification, native validation or provenance checks to make a build pass. Follow
upstream recipe licenses. Do not submit secrets or full user audits.

Run `python3 -m unittest discover -s tests -v` and
`python3 scripts/check-project.py`. Tests use synthetic metadata and archives;
native tests belong on ephemeral Intel Sequoia runners. No self-hosted runner
attached to a daily-use Mac is accepted by default.

Before changing preview status, verify actual build -> bottle -> independent
pour -> core identity -> formula test -> linkage -> attestation -> registry PR ->
client discovery. Additionally exercise an upgrade from an older already
installed core version, check retained old kegs and changed links, and test an
installed reverse-dependent application. Include an intentionally missing bottle
proving the source guard fires without compiling.

Review action updates by full commit, including runtime and required permissions.
Do not auto-merge dependency PRs. A tag describes a source release, not evidence
that a specific platform binary has been verified.
