#!/usr/bin/env python3
"""Reconstruct the pinned upstream checkout and apply this repo's customization."""

import argparse
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from noir_project import PIN, ROOT, sha256


@dataclass
class OverlayPlan:
    """Validated copies and removals, applied only after integration succeeds."""

    hashes: dict[str, str]
    copies: list[tuple[Path, Path]]
    removals: list[Path]

    def apply(self):
        for source, destination in self.copies:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for destination in self.removals:
            destination.unlink(missing_ok=True)


def patch(checkout, patch_file, *, reverse=False, dry_run=False):
    # git apply rejects a failed patch without leaving partially applied hunks.
    command = ["git", "apply", "--whitespace=nowarn"]
    if reverse:
        command.append("--reverse")
    if dry_run:
        command.append("--check")
    command.append(str(patch_file))
    subprocess.run(command, cwd=checkout, check=True, capture_output=True, text=True)


def managed_destination(checkout, relative):
    relative = Path(relative)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not (
            relative.is_relative_to(PIN["overlay_target"])
            and relative.suffix in (".rs", ".snap")
            or relative.is_relative_to(PIN["asset_target"])
            and relative.suffix == ".nrf"
        )
    ):
        raise RuntimeError(f"Unsafe managed checkout path: {relative}")
    destination = checkout / relative
    if any(
        checkout.joinpath(*relative.parts[:index]).is_symlink()
        for index in range(1, len(relative.parts) + 1)
    ):
        raise RuntimeError(f"Managed checkout path contains a symlink: {destination}")
    if destination.exists() and not destination.is_file():
        raise RuntimeError(f"Managed checkout path is not a file: {destination}")
    return destination


def ensure_archive(archive):
    """Download if needed and verify the archive before extracting any files."""
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_suffix(".download")
        try:
            with (
                urllib.request.urlopen(PIN["archive_url"], timeout=60) as response,
                temporary.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
            if sha256(temporary) != PIN["archive_sha256"]:
                raise RuntimeError(
                    "Downloaded source checksum does not match upstream.json."
                )
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256(archive) != PIN["archive_sha256"]:
        raise RuntimeError("Source archive checksum does not match upstream.json.")


def prepare_checkout(checkout, archive, integration_patch, *, adopt_existing):
    """Read existing sync state or create an isolated, patched upstream tree."""
    stamp = checkout / ".noir-source.json"
    if stamp.is_symlink() or (stamp.exists() and not stamp.is_file()):
        raise RuntimeError(f"Source stamp must be a regular file: {stamp}")
    if checkout.exists():
        if stamp.is_file():
            previous = json.loads(stamp.read_text())
            if previous.get("tag") != PIN["tag"]:
                raise RuntimeError("Checkout belongs to a different upstream version.")
            return previous
        if not adopt_existing:
            raise RuntimeError(
                "Checkout already exists. Use --adopt-existing to verify and reuse it."
            )
        return {}

    ensure_archive(archive)
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
        patch(extracted, integration_patch)
        extracted.rename(checkout)
    return {}


def plan_overlay(checkout, previous_files):
    """Validate incoming and obsolete paths without changing the checkout."""
    files = {}
    for source in sorted((ROOT / "src").rglob("*")):
        if source.is_file() and source.suffix in (".rs", ".snap"):
            files[
                str(Path(PIN["overlay_target"]) / source.relative_to(ROOT / "src"))
            ] = source
    for source in sorted((ROOT / "assets").glob("*.nrf")):
        files[str(Path(PIN["asset_target"]) / source.name)] = source

    # Check both incoming files and obsolete names before changing anything.
    # Only paths recorded by our previous sync are eligible for removal.
    destinations = {
        relative: managed_destination(checkout, relative)
        for relative in sorted(files.keys() | previous_files.keys())
    }
    incoming = {relative: sha256(source) for relative, source in files.items()}
    existing = {
        relative: sha256(path) if path.is_file() else None
        for relative, path in destinations.items()
    }
    for relative, destination in destinations.items():
        if existing[relative] not in (
            None,
            incoming.get(relative),
            previous_files.get(relative),
        ):
            raise RuntimeError(
                f"Local checkout edit needs to be preserved in src/ or assets/ first: {destination}"
            )
    return OverlayPlan(
        hashes=incoming,
        copies=[
            (source, destinations[relative])
            for relative, source in files.items()
            if existing[relative] != incoming[relative]
        ],
        removals=[
            destinations[relative]
            for relative in sorted(previous_files.keys() - files.keys())
        ],
    )


def update_integration(checkout, integration_patch, previous, previous_patch):
    """Verify the current integration or migrate with rollback on patch failure."""
    try:
        patch(checkout, integration_patch, reverse=True, dry_run=True)
    except subprocess.CalledProcessError as error:
        if previous_patch is None:
            raise RuntimeError(
                "Checkout integration differs from the current patch. Use a fresh "
                "--checkout, or --previous-patch with the patch used to prepare this checkout."
            ) from error
        previous_patch = previous_patch.resolve()
        previous_digest = previous.get("integration_sha256")
        if previous_digest is not None and sha256(previous_patch) != previous_digest:
            raise RuntimeError(
                "Previous patch differs from the checkout's recorded checksum."
            )
        try:
            patch(checkout, previous_patch, reverse=True, dry_run=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                "Previous patch does not match the checkout integration."
            ) from error
        patch(checkout, previous_patch, reverse=True)
        try:
            patch(checkout, integration_patch, dry_run=True)
            patch(checkout, integration_patch)
        except subprocess.CalledProcessError as error:
            patch(checkout, previous_patch)
            raise RuntimeError(
                "New integration patch could not be applied; restored the previous integration."
            ) from error


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
    parser.add_argument(
        "--previous-patch",
        type=Path,
        help="Previous integration patch, required to migrate an older patched checkout",
    )
    args = parser.parse_args()
    checkout, archive = args.checkout.resolve(), args.archive.resolve()
    integration_patch = ROOT / PIN["integration_patch"]
    if sha256(integration_patch) != PIN["integration_sha256"]:
        raise RuntimeError(
            "Integration patch differs from its pinned checksum; update upstream.json intentionally."
        )
    if not (ROOT / "src/noir_scene.rs").is_file():
        raise RuntimeError("Renderer source is missing from src/.")

    previous = prepare_checkout(
        checkout, archive, integration_patch, adopt_existing=args.adopt_existing
    )
    overlay = plan_overlay(checkout, previous.get("files", {}))
    update_integration(checkout, integration_patch, previous, args.previous_patch)
    overlay.apply()
    state = {
        "tag": PIN["tag"],
        "integration_sha256": PIN["integration_sha256"],
        "files": overlay.hashes,
    }
    (checkout / ".noir-source.json").write_text(json.dumps(state, indent=2) + "\n")
    print(checkout)


if __name__ == "__main__":
    main()
