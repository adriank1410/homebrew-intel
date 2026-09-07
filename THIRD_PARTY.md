# Third-party source, binary licenses and notices

The BSD-2-Clause license in this repository covers original project code. It does
**not** relicense Homebrew, formula recipes, downloaded software, bundled libraries,
fonts, data, or any binary produced by this pipeline.

Official Homebrew recipe material retains the Homebrew contributors' BSD-2-Clause
notice in `LICENSES/Homebrew-BSD-2-Clause.txt`. Builds use a pinned official core
commit without rewriting dependencies or installation recipes. This project was
written independently; no code was copied from the unlicensed `intel-bottles`
repository discussed during design.

Each published binary is accompanied by a source bundle containing the exact
recipe, Homebrew license notice, declared fetched source/resource/patch archives,
and a manifest of upstream URLs and SHA-256 hashes. Original upstream license and
copyright notices must be retained in those source materials and in required
binary distribution notices. The binary's manifest records the formula's declared
license and dependency closure; this is useful evidence, **not a complete
license/SBOM analysis of all vendored or statically linked components**.

Automatic publication accepts a small declared permissive-license allowlist.
That is a conservative scheduling gate, not a legal compliance certificate.
Unknown and copyleft declarations require an explicit review record tied to the
exact recipe SHA-256. Formula changes invalidate that exception. The review must
address corresponding source, modifications/build scripts, notices, linking
obligations and downstream access to source as applicable.

The generic collector refuses unpinned or VCS-only source resources rather than
pretending to archive them. It cannot automatically prove completeness of source
fetched by Go/Cargo/npm or other build systems during compilation. For packages
whose distribution obligations require more, add a tested source collector and a
substantive reviewed policy exception before publication. Never expand the
allowlist merely to suppress a failure.

Review reference for copied recipe notice:
https://github.com/Homebrew/homebrew-core/blob/main/LICENSE.txt

This policy deliberately makes some candidate builds fail pending review. It does
not assert that all requested programs may currently be republished.
