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
found inside supported tar, ZIP, lzip, zstd, and 7z archives are copied into the
bundle without extracting into the filesystem. Exact-revision Git sources and
their submodules are exported with pinned immutable commit identities. Candidate validation rejects missing notices, changed
notice bytes, links, special files, traversal names, unindexed files, or an
index that does not match the bottle record. Raw patch resources remain in the
bundle but are not treated as archives.

This profile covers standalone bottles whose complete build inputs are captured.
It does not decide the separate obligations of a later application that embeds
or links these packages. Collection covers recipe-declared archives, pinned Git
exports, and Go/Cargo caches. A recipe using another undeclared downloader needs
specific collection support before its bundle can be described as complete.

Primary license texts and guidance:

- GNU GPL 2.0 and 3.0: https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html and https://www.gnu.org/licenses/gpl-3.0.en.html
- GNU LGPL 2.1 and 3.0: https://www.gnu.org/licenses/lgpl-2.1.html and https://www.gnu.org/licenses/lgpl.html
- GNU AGPL 3.0: https://www.gnu.org/licenses/agpl-3.0.html
- GNU FDL 1.3: https://www.gnu.org/licenses/fdl-1.3.en.html
- Mozilla Public License 2.0: https://www.mozilla.org/MPL/2.0/
- SPDX libtiff and libpng 2.0 texts: https://spdx.org/licenses/libtiff.html and https://spdx.org/licenses/libpng-2.0.html

## Additional installed license families (2026-09-09)

The installed inventory also uses named SPDX licenses beyond the common
permissive and GNU families. They are admitted through the same source-required
path when their terms require attribution, notices, source availability, or
preservation of modification information. The source bundle keeps the complete
upstream archive, the exact Homebrew recipe and patch archives, copied upstream
notices, and build instructions. Public-domain declarations (`CC-PDDC` and
`blessing`) have no source-publication condition and remain permissive terms.
Homebrew's `public_domain` pseudo-token stays in the source-required profile so
composite archives still carry their surrounding component notices.

This covers APSL, EPL, EUPL, GFDL 1.3, the named BSD/HPND/MIT variants,
ImageMagick, Info-ZIP, OpenLDAP, OpenSSH, Ruby, Tcl, Unicode, and the other
explicit identifiers in `source_required_license_tokens`. An Apache 2.0 license
with the LLVM exception is accepted only as that exact configured pair.

The current unusual recipes have concrete accompanying material:

- `unzip` 6.0 is visibly modified by the Ubuntu patch archive listed in its
  recipe. Both original archives and the recipe are shipped, so recipients can
  identify every change. Its generated `BUILDING.txt` explicitly marks the
  binary as a Homebrew-patched build rather than an unmodified Info-ZIP release.
- `telnet` uses Apple's tagged `remote_cmds` and `libtelnet` archives without a
  source patch. Their sources and APSL/BSD notices accompany the bottle.
- `qt@5` keeps the complete Qt source archive and every Homebrew patch. This
  preserves the GFDL-covered documentation, including any invariant-section or
  cover-text declarations present in that exact release, alongside the modified
  source.

`nmap` remains the single inventory expression represented by Homebrew as
`cannot_represent`. Its Nmap Public Source License permits standalone source and
binary distribution when the corresponding source and license accompany it,
but the symbolic metadata does not identify that license. It should use a
formula-hash-pinned source-required exception, rather than treating every future
`cannot_represent` formula as NPSL. The current recipe builds standalone Nmap,
does not bundle Npcap, and its source bundle records the small Homebrew packaging
changes. Reference: https://nmap.org/npsl/

Additional primary texts:

- EUPL 1.2: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017D0863
- APSL 1.0: https://spdx.org/licenses/APSL-1.0.html
- Info-ZIP: https://spdx.org/licenses/Info-ZIP.html
- OpenLDAP 2.8: https://spdx.org/licenses/OLDAP-2.8.html
- OpenSSH: https://spdx.org/licenses/SSH-OpenSSH.html
- ImageMagick: https://imagemagick.org/script/license.php
