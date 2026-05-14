#!/usr/bin/env bash
# Regenerate expected_output/ from flow.clio and verify there is no drift.
# Exit 0 if expected_output/ matches a fresh compile, exit 1 otherwise.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

(
  cd "$repo_root"
  uv run python -m clio compile \
    "$here/flow.clio" \
    --target python \
    --output "$tmp"
)

if diff -r --brief "$here/expected_output/" "$tmp" > /dev/null; then
  echo "expected_output/ is up to date."
  exit 0
fi

echo "Drift detected between expected_output/ and a fresh compile."
echo "To accept the new output:"
echo "  rm -rf '$here/expected_output' && cp -r '$tmp' '$here/expected_output'"
exit 1
