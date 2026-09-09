# Redistribution review

## NCSA and Tor (2026-09-08)

The University of Illinois/NCSA Open Source License permits source and binary
redistribution, including modified and commercial versions. Redistributions must
retain copyright notices, conditions and disclaimers. Contributor/institution
names must not be used to imply endorsement without permission.

Reference: https://opensource.org/license/ncsa

The unchanged Homebrew Tor 0.4.9.12 recipe declares BSD-2-Clause, BSD-3-Clause,
MIT and NCSA. Its upstream source archive was downloaded from
https://dist.torproject.org/tor-0.4.9.12.tar.gz and matched the recipe SHA-256
`c0d307c9dcdaee4848a8ca53e9d6c4ec92823e4f30be12790b0fbddfc6515f5b`.
The top-level `LICENSE` contains the component notices, including the NCSA text
for `src/ext/mulodi4.c` (also offered under MIT).

NCSA is admitted to the permissive-license policy. This does not waive its notice
requirements or admit other licenses. The source bundle retains the original
source archive unchanged. Homebrew copies top-level license metadata into the
installed keg before bottling. Candidate validation before independent testing
and publication requires the bottled `LICENSE` to match the upstream file,
including all component notices. Its SHA-256 is pinned in
`policy/config.json` under `required_license_notices`; missing or changed notices
block publication until the new upstream text is reviewed.
Adding Tor to the candidate list does not guarantee that its dependency build
and independent installation tests will pass.

## Source-required licenses (2026-09-09)

Standard archive recipes may be built when their SPDX expression consists only
of reviewed permissive terms and the configured GPL, LGPL, AGPL, MPL, GFDL
without invariant sections, libtiff, or libpng terms. For `any_of`, one fully
supported branch is sufficient. Every branch of `all_of` must be supported.
License exceptions are admitted only as exact configured `LICENSE WITH
exception` pairs; an unknown term or exception still requires a recipe-specific
review pinned to its formula hash.

For a source-required result, publication requires a source bundle containing
the exact Homebrew recipe, every main/resource/patch archive reported by the
pinned Homebrew source collector, captured Go/Cargo build inputs, and an index
binding those files to their hashes and provenance. License and notice files
found inside upstream tar or ZIP archives are copied into the bundle without
extracting the archive. Candidate validation rejects missing notices, changed
notice bytes, links, special files, traversal names, unindexed files, or an
index that does not match the bottle record. Raw patch resources remain in the
bundle but are not treated as archives.

This profile covers unmodified standard archive source distribution. It does
not by itself establish compliance for applications with additional linking or
relinking obligations, recipes whose build downloads are not captured, Qt as a
whole, or sources from an unpinned VCS revision. Those cases remain blocked
until their concrete recipe path is verified.

Primary license texts and guidance:

- GNU GPL 2.0 and 3.0: https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html and https://www.gnu.org/licenses/gpl-3.0.en.html
- GNU LGPL 2.1 and 3.0: https://www.gnu.org/licenses/lgpl-2.1.html and https://www.gnu.org/licenses/lgpl.html
- GNU AGPL 3.0: https://www.gnu.org/licenses/agpl-3.0.html
- GNU FDL 1.3: https://www.gnu.org/licenses/fdl-1.3.en.html
- Mozilla Public License 2.0: https://www.mozilla.org/MPL/2.0/
- SPDX libtiff and libpng 2.0 texts: https://spdx.org/licenses/libtiff.html and https://spdx.org/licenses/libpng-2.0.html
