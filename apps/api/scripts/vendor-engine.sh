#!/usr/bin/env bash
# Copy the lifecycle engine into apps/api so a Vercel deploy (which uploads only this
# folder) can import it. The copy is gitignored; run before every `vercel deploy`/`vercel build`.
set -euo pipefail
api="$(cd "$(dirname "$0")/.." && pwd)"
src="$api/../../services/lifecycle-engine/lifecycle_engine"
rm -rf "$api/lifecycle_engine"
cp -r "$src" "$api/lifecycle_engine"
find "$api/lifecycle_engine" -name __pycache__ -type d -prune -exec rm -rf {} +
echo "vendored lifecycle_engine into $api"
