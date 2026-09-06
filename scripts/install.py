#!/usr/bin/env python3
"""Stage and install the separately named Codex Noir package after validation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
from datetime import datetime, timezone


VERSION = "0.153.4"
TARGET = "aarch64-apple-darwin"
SOURCE_TAG = f"rust-v{VERSION}"
STAGING = Path(__file__).resolve().parent.parent
PIN = json.loads((STAGING / "upstream.json").read_text())
PACKAGE = Path.home() / ".local/share/codex-noir" / VERSION
LAUNCHER = Path.home() / ".local/bin/codex-noir"
STOCK = Path.home() / ".codex/packages/standalone/releases" / f"{VERSION}-{TARGET}"
COMPANIONS = (
    Path("bin/codex-code-mode-host"),
    Path("codex-resources/zsh/bin/zsh"),
    Path("codex-path/rg"),
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exists(path):
    return path.exists() or path.is_symlink()


def validate(args):
    failures = []
    required = [
        args.binary,
        args.patch,
        args.source_archive,
        STAGING / "bin/codex-noir",
        STAGING / "README.md",
        STAGING / "src/noir_dragon.rs",
        STAGING / "assets/flight.nrf",
        STAGING / "assets/transit.nrf",
        STOCK / "codex-package.json",
        *(STOCK / relative for relative in COMPANIONS),
    ]
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            failures.append(f"Missing or empty required file: {path}")
    if not args.source_root.is_dir():
        failures.append(f"Source checkout is missing: {args.source_root}")
    if (
        args.source_archive.is_file()
        and sha256(args.source_archive) != PIN["archive_sha256"]
    ):
        failures.append("Source archive differs from its pinned checksum.")
    if args.patch.is_file() and sha256(args.patch) != PIN["integration_sha256"]:
        failures.append("Integration patch differs from its pinned checksum.")
    for executable in [args.binary, *(STOCK / relative for relative in COMPANIONS)]:
        if executable.is_file() and not os.access(executable, os.X_OK):
            failures.append(f"File is not executable: {executable}")
    if args.binary.is_file():
        with args.binary.open("rb") as stream:
            header = stream.read(8)
        if len(header) != 8 or struct.unpack("<II", header) != (0xFEEDFACF, 0x0100000C):
            failures.append(f"Expected a native arm64 Mach-O build: {args.binary}")
    manifest_path = STOCK / "codex-package.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("version") != VERSION or manifest.get("target") != TARGET:
                failures.append(
                    f"Pinned companion package has unexpected version or target: {manifest_path}"
                )
        except (OSError, ValueError) as error:
            failures.append(f"Cannot read companion manifest: {error}")
    for destination in (PACKAGE, LAUNCHER):
        if exists(destination) and not args.replace:
            failures.append(
                f"Destination exists; use --replace to retain a backup: {destination}"
            )
    if LAUNCHER.is_dir() and not LAUNCHER.is_symlink():
        failures.append(
            f"Refusing to replace a directory with the launcher: {LAUNCHER}"
        )
    if PACKAGE.is_symlink():
        failures.append(f"Refusing a symlink at the package destination: {PACKAGE}")
    if exists(PACKAGE) and not PACKAGE.is_dir():
        failures.append(f"Refusing a non-directory package destination: {PACKAGE}")
    return failures


def build_package(stage, args):
    destination = stage / "package"
    (destination / "bin").mkdir(parents=True)
    shutil.copy2(args.binary, destination / "bin/codex")
    for relative in COMPANIONS:
        (destination / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(STOCK / relative, destination / relative)
    # Reconstruct metadata rather than copying upstream build provenance onto
    # a modified executable. Codex reads the semver and canonical layout here.
    manifest = {
        "layoutVersion": 1,
        "version": VERSION,
        "target": TARGET,
        "variant": "codex",
        "entrypoint": "bin/codex",
        "resourcesDir": "codex-resources",
        "pathDir": "codex-path",
    }
    (destination / "codex-package.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    shutil.copy2(STAGING / "README.md", destination / "README.md")
    shutil.copy2(STAGING / "bin/codex-noir", destination / "launcher.sh")
    (destination / "launcher.sh").chmod(0o755)
    # Keep the integration patch AND editable modules/assets together: the
    # small patch alone is not a reconstruction of the photographic renderer.
    source_files = [
        STAGING / name
        for name in (
            ".gitignore",
            ".gitattributes",
            "README.md",
            "CLAUDE.md",
            "AGENTS.md",
            "LICENSE",
            "NOTICE",
            "upstream.json",
            "bin/codex-noir",
        )
    ]
    extensions = {
        ".rs",
        ".snap",
        ".nrf",
        ".jpg",
        ".json",
        ".md",
        ".patch",
        ".terminal",
        ".tmTheme",
        ".zsh",
        ".py",
        ".sh",
        ".txt",
    }
    for folder in ("src", "assets", "patches", "terminal", "scripts"):
        source_files.extend(
            path
            for path in (STAGING / folder).rglob("*")
            if path.is_file()
            and path.suffix in extensions
            and "__pycache__" not in path.parts
        )
    for source in source_files:
        target = destination / "source" / source.relative_to(STAGING)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    provenance = {
        "customization": "Codex Noir terminal-only photographic ASCII build",
        "official_release_binary": False,
        "upstream_version": VERSION,
        "upstream_tag": SOURCE_TAG,
        "upstream_source_url": f"https://github.com/openai/codex/archive/refs/tags/{SOURCE_TAG}.tar.gz",
        "source_checkout": str(args.source_root.resolve()),
        "source_archive_sha256": sha256(args.source_archive),
        "patch_file": "source/patches/codex-integration.patch",
        "patch_sha256": sha256(args.patch),
        "built_binary_source": str(args.binary.resolve()),
        "binary_sha256": sha256(destination / "bin/codex"),
        "companion_source": str(STOCK),
        "companion_sha256": {
            str(relative): sha256(destination / relative) for relative in COMPANIONS
        },
        "launcher_sha256": sha256(destination / "launcher.sh"),
        "customization_repository": "https://github.com/Madhavan113/customcodexterminal",
        "source_files_sha256": {
            str(path.relative_to(STAGING)): sha256(path) for path in source_files
        },
        "installed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (destination / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    return destination


def install(args):
    PACKAGE.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    backups = []
    installed = []
    launcher_stage = None
    with tempfile.TemporaryDirectory(
        prefix=".codex-noir-stage-", dir=PACKAGE.parent
    ) as temporary:
        staged_package = build_package(Path(temporary), args)
        descriptor, launcher_name = tempfile.mkstemp(
            prefix=".codex-noir-stage-", dir=LAUNCHER.parent
        )
        os.close(descriptor)
        launcher_stage = Path(launcher_name)
        try:
            shutil.copy2(STAGING / "bin/codex-noir", launcher_stage)
            launcher_stage.chmod(0o755)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            for destination in (PACKAGE, LAUNCHER):
                if exists(destination):
                    if not args.replace:
                        raise RuntimeError(
                            f"Destination appeared during staging: {destination}"
                        )
                    backup = destination.with_name(f"{destination.name}.backup-{stamp}")
                    if exists(backup):
                        raise RuntimeError(f"Backup already exists: {backup}")
                    destination.rename(backup)
                    backups.append((destination, backup))
            staged_package.rename(PACKAGE)
            installed.append(PACKAGE)
            launcher_stage.rename(LAUNCHER)
            installed.append(LAUNCHER)
            launcher_stage = None
        except BaseException:
            # Only remove the newly created artifacts tracked by this run.
            for destination in reversed(installed):
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            for destination, backup in reversed(backups):
                backup.rename(destination)
            raise
        finally:
            if launcher_stage is not None and exists(launcher_stage):
                launcher_stage.unlink()
    print(f"Installed package: {PACKAGE}")
    print(f"Installed launcher: {LAUNCHER}")
    for destination, backup in backups:
        print(f"Backup for {destination}: {backup}")
    print(
        "No Codex task was started. Existing Codex configuration and authentication were preserved."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary",
        type=Path,
        default=STAGING / f"build/codex-{SOURCE_TAG}/codex-rs/target/dev-small/codex",
    )
    parser.add_argument(
        "--patch", type=Path, default=STAGING / "patches/codex-integration.patch"
    )
    parser.add_argument(
        "--source-root", type=Path, default=STAGING / f"build/codex-{SOURCE_TAG}"
    )
    parser.add_argument(
        "--source-archive",
        type=Path,
        default=STAGING / f"downloads/codex-{SOURCE_TAG}.tar.gz",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Install after validation; default is read-only preview",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Back up existing destinations before replacement",
    )
    args = parser.parse_args()
    print(f"Binary: {args.binary}")
    print(f"Pinned companions: {STOCK}")
    print(f"Package destination: {PACKAGE}")
    print(f"Launcher destination: {LAUNCHER}")
    failures = validate(args)
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    if not args.apply:
        print(
            "Validation passed. Preview only; rerun with --apply after the build and tests pass."
        )
        return 0
    install(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        sys.exit(1)
