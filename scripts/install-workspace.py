#!/usr/bin/env python3
"""Install the Noir terminal workspace in a private, versioned Python environment."""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import venv
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def install(prefix, apply=False):
    if sys.version_info < (3, 12):  # noqa: UP036 -- installer may run before a supported Python is selected
        raise RuntimeError("Noir requires Python 3.12 or newer")
    prefix = Path(prefix).expanduser().resolve()
    bundle = prefix / "share/noir"
    launcher = prefix / "bin/noir"
    sources = [
        ROOT / "scripts/noir-workspace.py",
        ROOT / "scripts/requirements-workspace.txt",
        *sorted((ROOT / "scripts/noir_workspace").glob("*.py")),
    ]
    if len(sources) < 3 or any(not source.is_file() for source in sources):
        raise RuntimeError("The workspace source bundle is incomplete")
    if launcher.exists() and not launcher.is_file():
        raise RuntimeError(f"The launcher path is not a file: {launcher}")
    print(f"Private package and Python environment: {bundle}/releases/")
    print(f"Terminal command: {launcher}")
    print("Dependencies: scripts/requirements-workspace.txt")
    if not apply:
        print("Preview only. Re-run with --apply to install.")
        return

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    release = bundle / "releases" / stamp
    release.mkdir(parents=True, mode=0o700)
    try:
        for source in sources:
            destination = release / source.relative_to(ROOT / "scripts")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        # Create at its final path: virtual-environment shebangs are absolute.
        venv.EnvBuilder(with_pip=True).create(release / "venv")
        python = release / "venv/bin/python"
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "--disable-pip-version-check",
                "install",
                "-r",
                str(release / "requirements-workspace.txt"),
            ],
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-c",
                "from noir_workspace.ui import WorkspaceApp; from noir_workspace.engine import Engine",
            ],
            cwd=release,
            check=True,
        )
        receipt = {
            "installed_at": stamp,
            "source": str(ROOT),
            "python": sys.version.split()[0],
            "files": {
                str(source.relative_to(ROOT / "scripts")): hashlib.sha256(
                    source.read_bytes()
                ).hexdigest()
                for source in sources
            },
        }
        (release / "installation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    except BaseException:
        shutil.rmtree(release)
        raise

    launcher.parent.mkdir(parents=True, exist_ok=True)
    if launcher.exists() or launcher.is_symlink():
        backup = launcher.with_name(f"noir.backup-{stamp}")
        shutil.copy2(launcher, backup, follow_symlinks=False)
        print(f"Previous launcher saved: {backup}")
    temporary = launcher.with_name(f".noir-{stamp}")
    try:
        temporary.write_text(
            "#!/bin/sh\n"
            f'exec {shlex.quote(str(python))} {shlex.quote(str(release / "noir-workspace.py"))} "$@"\n'
        )
        temporary.chmod(0o755)
        temporary.replace(launcher)
    finally:
        temporary.unlink(missing_ok=True)
    print("Installed. Run noir inside a project directory.")
    if str(launcher.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add {launcher.parent} to PATH, or run {launcher} directly.")
    print(
        "Existing Noir sessions keep their current process until shutdown and reconnect."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Install; default previews the paths"
    )
    parser.add_argument(
        "--prefix",
        type=Path,
        default=Path.home() / ".local",
        help="Installation prefix (default: ~/.local)",
    )
    args = parser.parse_args()
    install(args.prefix, args.apply)


if __name__ == "__main__":
    main()
