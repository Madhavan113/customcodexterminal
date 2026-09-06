#!/usr/bin/env python3
"""Reconstruct the pinned upstream checkout and apply this repo's customization."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
PIN = json.loads((ROOT / "upstream.json").read_text())


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def patch(checkout, *, reverse=False, dry_run=False):
    command = ["patch", "--batch", "-p1", "-i", str(ROOT / PIN["integration_patch"])]
    if reverse:
        command.append("--reverse")
    if dry_run:
        command.append("--dry-run")
    subprocess.run(command, cwd=checkout, check=True, capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout", type=Path, default=ROOT / "build" / f"codex-{PIN['tag']}"
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=ROOT / "downloads" / f"codex-{PIN['tag']}.tar.gz",
    )
    parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="Verify and reuse an already patched checkout",
    )
    args = parser.parse_args()
    checkout, archive = args.checkout.resolve(), args.archive.resolve()
    stamp = checkout / ".noir-source.json"
    if digest(ROOT / PIN["integration_patch"]) != PIN["integration_sha256"]:
        raise RuntimeError(
            "Integration patch differs from its pinned checksum; update upstream.json intentionally."
        )
    if not (ROOT / "src/noir_dragon.rs").is_file():
        raise RuntimeError("Renderer source is missing from src/.")

    previous = {}
    if checkout.exists():
        if stamp.is_file():
            previous = json.loads(stamp.read_text())
            if previous.get("tag") != PIN["tag"]:
                raise RuntimeError("Checkout belongs to a different upstream version.")
        elif not args.adopt_existing:
            raise RuntimeError(
                "Checkout already exists. Use --adopt-existing to verify and reuse it."
            )
        # Verify all integration hunks before refreshing our separate modules.
        patch(checkout, reverse=True, dry_run=True)
    else:
        if not archive.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            temporary = archive.with_suffix(".download")
            try:
                with (
                    urllib.request.urlopen(PIN["archive_url"], timeout=60) as response,
                    temporary.open("wb") as output,
                ):
                    shutil.copyfileobj(response, output)
                if digest(temporary) != PIN["archive_sha256"]:
                    raise RuntimeError(
                        "Downloaded source checksum does not match upstream.json."
                    )
                temporary.replace(archive)
            finally:
                temporary.unlink(missing_ok=True)
        if digest(archive) != PIN["archive_sha256"]:
            raise RuntimeError("Source archive checksum does not match upstream.json.")
        checkout.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".noir-source-", dir=checkout.parent
        ) as temporary:
            with tarfile.open(archive) as source:
                source.extractall(temporary, filter="data")
            extracted = Path(temporary) / f"codex-{PIN['tag']}"
            # Upstream format/fix commands use Git to discover source files.
            # Keep the generated checkout separate from this repository's Git.
            subprocess.run(["git", "init", "--quiet", str(extracted)], check=True)
            subprocess.run(["git", "add", "--all"], cwd=extracted, check=True)
            patch(extracted)
            extracted.rename(checkout)

    files = {}
    for source in sorted((ROOT / "src").rglob("*")):
        if source.is_file() and source.suffix in (".rs", ".snap"):
            files[source] = (
                checkout / PIN["overlay_target"] / source.relative_to(ROOT / "src")
            )
    for source in sorted((ROOT / "assets").glob("*.nrf")):
        files[source] = checkout / PIN["asset_target"] / source.name

    # Reject accidental overwrites of edits made directly in the build checkout.
    for source, destination in files.items():
        relative = str(destination.relative_to(checkout))
        old_digest = previous.get("files", {}).get(relative)
        if destination.is_file() and digest(destination) != digest(source):
            if old_digest is None or digest(destination) != old_digest:
                raise RuntimeError(
                    f"Local checkout edit needs to be copied into src/ first: {destination}"
                )
    for source, destination in files.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    state = {
        "tag": PIN["tag"],
        "files": {
            str(path.relative_to(checkout)): digest(path) for path in files.values()
        },
    }
    stamp.write_text(json.dumps(state, indent=2) + "\n")
    print(checkout)


if __name__ == "__main__":
    main()
