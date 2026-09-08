"""Shared project metadata and file checksums for build and packaging tools."""

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = json.loads((ROOT / "upstream.json").read_text())


def build_binary(source_root):
    """Locate the same profile and target directory used by build.sh."""
    target = Path(os.environ.get("CARGO_TARGET_DIR") or "target")
    if not target.is_absolute():
        target = Path(source_root) / "codex-rs" / target
    profile = os.environ.get("CODEX_NOIR_BUILD_PROFILE") or "release"
    return target / ("debug" if profile == "dev" else profile) / "codex"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
