# Validation record for source preview 0.1.0

## Executed before publication

- 88 Python unittest methods passed, including 50 deterministic generated dependency DAGs.
- Python parsing, Ruby syntax, Bash syntax and Ruby/Psych workflow YAML parsing passed.
- Repository policy checks passed: full-commit action pins, no `pull_request_target`, no persisted checkout credentials, canonical target list and strict registry validation.
- Deployment helper dry-run tests cover allowlist-only transfers, changed files, traversal, 403/404 distinction, no-write settings recommendations and Git credential use.
- The source-build guard was executed against a Ruby fixture installer.

## Remote/native validation status

The repository was created by the owner and implementation is published through
the connected GitHub write API. Remote Linux and native Intel macOS workflow
results are not inferred from local tests; GitHub Actions job conclusions and
logs are authoritative. Real package installation on the owner's Mac remains
intentionally outside deployment.

## Initial distribution contents

44 reviewed candidate formula names; zero published bottle records; no third-party
binary or original personal audit export. A native build must succeed before the
registry can contain a real bottle.
