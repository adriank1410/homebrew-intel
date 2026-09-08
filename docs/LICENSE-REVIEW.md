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
