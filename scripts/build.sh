#!/bin/sh
set -eu

noir_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# prepare-source.py accepts --checkout/--archive to reuse a local source cache.
python3 "$noir_root/scripts/prepare-source.py" "$@" > "$noir_root/build-source.log"
noir_source=$(tail -n 1 "$noir_root/build-source.log")
cd "$noir_source/codex-rs"
cargo build --locked --profile dev-small -p codex-cli --bin codex
