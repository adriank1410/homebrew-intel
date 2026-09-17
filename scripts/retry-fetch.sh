#!/bin/bash
# SPDX-License-Identifier: BSD-2-Clause
# Only wrap repeatable network fetches or idempotent tool bootstrapping,
# never runner preparation, formula installs or builds.
set -u
max_attempts="${INTELBREW_RETRY_ATTEMPTS:-3}"
base_delay="${INTELBREW_RETRY_DELAY:-5}"
for (( attempt=1; attempt<=max_attempts; attempt++ )); do
  "$@" && exit 0
  result=$?
  if (( attempt == max_attempts )); then exit "$result"; fi
  delay=$(( base_delay * (1 << (attempt - 1)) ))
  echo "Fetch failed; retrying ($attempt/$max_attempts), waiting ${delay}s..." >&2
  sleep "$delay"
done
