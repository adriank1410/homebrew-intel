#!/bin/bash
# SPDX-License-Identifier: BSD-2-Clause
# Only wrap repeatable network fetches or idempotent tool bootstrapping,
# never runner preparation, formula installs or builds.
set -u
max_attempts="${INTELBREW_RETRY_ATTEMPTS:-3}"
base_delay="${INTELBREW_RETRY_DELAY:-5}"

# Validate before invoking the wrapped command. Keep decimal values with
# leading zeroes valid by normalizing through base-10 arithmetic below.
if ! [[ "$max_attempts" =~ ^[0-9]+$ ]] || ! [[ "$base_delay" =~ ^[0-9]+$ ]]; then
  echo "Invalid INTELBREW_RETRY_ATTEMPTS or INTELBREW_RETRY_DELAY" >&2
  exit 2
fi
max_attempts=$((10#$max_attempts))
base_delay=$((10#$base_delay))
if (( max_attempts < 1 || base_delay < 0 )); then
  echo "Invalid INTELBREW_RETRY_ATTEMPTS or INTELBREW_RETRY_DELAY" >&2
  exit 2
fi

for (( attempt=1; attempt<=max_attempts; attempt++ )); do
  "$@" && exit 0
  result=$?
  if (( attempt == max_attempts )); then exit "$result"; fi
  delay=$(( base_delay * (1 << (attempt - 1)) ))
  echo "Fetch failed; retrying ($attempt/$max_attempts), waiting ${delay}s..." >&2
  sleep "$delay"
done
