#!/bin/sh
set -eu

noir_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# prepare-source.py accepts --checkout/--archive to reuse a local source cache.
noir_source=$(python3 "$noir_root/scripts/prepare-source.py" "$@")
noir_profile=${CODEX_NOIR_BUILD_PROFILE:-release}
cd "$noir_source/codex-rs"
# dev-small disables optimization. Installed interactive builds use release;
# opt into dev-small explicitly when iterating on the renderer.
cargo build --locked --profile "$noir_profile" -p codex-cli --bin codex
