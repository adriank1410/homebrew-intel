#!/bin/bash
# SPDX-License-Identifier: BSD-2-Clause
# Only wrap repeatable network fetches, never runner preparation or installs.
set -u
for attempt in 1 2 3; do
  "$@" && exit 0
  result=$?
  if [[ "$attempt" == 3 ]]; then exit "$result"; fi
  echo "Fetch failed; retrying ($attempt/3)." >&2
  sleep "${INTELBREW_RETRY_DELAY:-5}"
done
