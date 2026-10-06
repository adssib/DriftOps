#!/usr/bin/env bash
# Digest of the Python source, so an image can be checked against the checkout it was built from.
#   scripts/source_digest.sh            → digest of ./driftops
#   scripts/source_digest.sh IMAGE      → digest of /app/driftops inside IMAGE
set -euo pipefail
cmd='cd "$0" && find driftops -name "*.py" -o -name "*.sql" | LC_ALL=C sort | xargs cat | sha256sum | cut -c1-16'
if [ $# -eq 0 ]; then
  sh -c "$cmd" "$(cd "$(dirname "$0")/.." && pwd)"
else
  docker run --rm --entrypoint sh "$1" -c "$cmd" /app
fi
