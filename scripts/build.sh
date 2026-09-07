#!/bin/sh
set -eu

noir_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# prepare-source.py accepts --checkout/--archive to reuse a local source cache.
noir_source=$(python3 "$noir_root/scripts/prepare-source.py" "$@")
cd "$noir_source/codex-rs"
cargo build --locked --profile dev-small -p codex-cli --bin codex
