# Changelog

## 0.1.0 – source preview

Initial core-identity bottle registry and explicit `brew intel` client. Includes
native planning, no-source installer guard, read-only build/fresh verification,
attested publication and review-required registry changes, conservative source
and licensing policy, and adversarial-input unit tests.

Validation corrections before first native run:

- accept bounded Homebrew `any_of` / `all_of` license structures only when every leaf is allowlisted, including simdutf's Apache-2.0/MIT declaration;
- add an explicit reviewed `policy/build-request.json` trigger so connector-driven builds do not require a local GitHub CLI;
- keep Actions PR creation/approval disabled: publication stops at a verified review branch and compare link for owner/connector review;
- update maintainer documentation for the already-created repository state;
- retain the source preview status until native Intel CI and older-keg upgrade acceptance actually pass.

No third-party bottle is pre-published or invented by this release.
