"""Keep the interactive build and installer pointed at the same optimized artifact."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noir_project import ROOT, build_binary


class BuildProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="noir-build-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "checkout with spaces"
        scripts = self.root / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/build.sh", scripts / "build.sh")
        self.source = self.root / "upstream"
        (self.source / "codex-rs").mkdir(parents=True)
        (scripts / "prepare-source.py").write_text(
            "from pathlib import Path\n"
            "print(Path(__file__).resolve().parent.parent / 'upstream')\n"
        )
        self.tools = self.root / "tools"
        self.tools.mkdir()
        cargo = self.tools / "cargo"
        cargo.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\nfrom pathlib import Path\n"
            "profile = sys.argv[sys.argv.index('--profile') + 1]\n"
            "target = Path(os.environ.get('CARGO_TARGET_DIR') or 'target')\n"
            "binary = target / ('debug' if profile == 'dev' else profile) / 'codex'\n"
            "binary.parent.mkdir(parents=True, exist_ok=True)\n"
            "binary.write_text(json.dumps(sys.argv[1:]))\n"
        )
        cargo.chmod(0o755)

    def build(self, *, profile=None, target=None):
        env = os.environ.copy()
        env.pop("CODEX_NOIR_BUILD_PROFILE", None)
        env.pop("CARGO_TARGET_DIR", None)
        env["PATH"] = str(self.tools) + os.pathsep + env["PATH"]
        if profile is not None:
            env["CODEX_NOIR_BUILD_PROFILE"] = profile
        if target is not None:
            env["CARGO_TARGET_DIR"] = str(target)
        subprocess.run(
            [str(self.root / "scripts/build.sh")],
            env=env,
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        with patch.dict(os.environ, env, clear=True):
            binary = build_binary(self.source)
        self.assertTrue(
            binary.is_file(), f"Installer did not find the built artifact: {binary}"
        )
        command = json.loads(binary.read_text())
        self.assertIn("--locked", command)
        return binary, command

    def test_default_build_and_install_use_release(self):
        binary, command = self.build()
        self.assertEqual(binary.parent.name, "release")
        self.assertEqual(command[command.index("--profile") + 1], "release")

    def test_explicit_development_profile_and_external_cache_match(self):
        target = self.root / "separate target"
        binary, _ = self.build(profile="dev-small", target=target)
        self.assertEqual(binary, target / "dev-small/codex")

    def test_standard_dev_profile_uses_cargos_debug_directory(self):
        target = self.root / "another target"
        binary, _ = self.build(profile="dev", target=target)
        self.assertEqual(binary, target / "debug/codex")

    def test_relative_cache_is_resolved_from_cargos_working_directory(self):
        binary, _ = self.build(target=Path("relative cache"))
        self.assertEqual(binary, self.source / "codex-rs/relative cache/release/codex")


if __name__ == "__main__":
    unittest.main()
