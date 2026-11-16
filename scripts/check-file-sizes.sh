#!/usr/bin/env bash
# Reject tracked files over 5 MB (HANDOFF Addendum A.9).
set -euo pipefail
cd "$(dirname "$0")/.."
big="$(git ls-files -z | xargs -0 -I{} sh -c 'test -f "{}" && [ "$(wc -c <"{}")" -gt 5242880 ] && echo "{}"' || true)"
[ -z "$big" ] || { echo "files over 5 MB:"; echo "$big"; exit 1; }
echo "file sizes ok"
