"""Keep fresh-checkout packages independent of ignored contributor documents."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import install as installer


class PackageSourceTests(unittest.TestCase):
    def test_package_retains_attribution_without_shipping_local_documents(self):
        license_text = (installer.ROOT / "licenses/codex.txt").read_bytes()
        local_names = ("AGENTS.md", "CLAUDE.md", "LICENSE", "NOTICE")
        with tempfile.TemporaryDirectory(prefix="noir-package-") as temporary:
            base = Path(temporary)
            root, stock = base / "checkout", base / "stock"
            root.mkdir()
            for name in (
                ".gitignore",
                ".gitattributes",
                "README.md",
                "upstream.json",
                "ruff.toml",
                "bin/codex-noir",
                "src/noir_scene.rs",
                "patches/codex-integration.patch",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"Public fixture: {name}\n")
            license_path = root / "licenses/codex.txt"
            license_path.parent.mkdir()
            license_path.write_bytes(license_text)
            for relative in installer.COMPANIONS:
                path = stock / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"companion fixture")
            binary, archive = base / "codex", base / "source.tar.gz"
            binary.write_bytes(b"binary fixture")
            archive.write_bytes(b"archive fixture")
            args = SimpleNamespace(
                binary=binary,
                source_archive=archive,
                source_root=root,
                patch=root / "patches/codex-integration.patch",
            )
            with (
                patch.object(installer, "ROOT", root),
                patch.object(installer, "STOCK", stock),
            ):
                for local_documents in (False, True):
                    with self.subTest(local_documents=local_documents):
                        if local_documents:
                            for name in local_names:
                                (root / name).write_text("Private local instructions\n")
                        package = installer.build_package(
                            base / f"stage-{local_documents}", args
                        )
                        source = package / "source"
                        self.assertEqual(
                            (source / "licenses/codex.txt").read_bytes(), license_text
                        )
                        self.assertEqual(
                            (package / "bin/codex").read_bytes(), binary.read_bytes()
                        )
                        provenance = json.loads(
                            (package / "provenance.json").read_text()
                        )
                        self.assertEqual(
                            provenance["source_files_sha256"]["licenses/codex.txt"],
                            installer.sha256(license_path),
                        )
                        for name in local_names:
                            self.assertFalse((source / name).exists(), name)
                            self.assertNotIn(name, provenance["source_files_sha256"])


if __name__ == "__main__":
    unittest.main()
