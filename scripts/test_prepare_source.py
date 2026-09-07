"""Regression tests for source preparation and checkout-preserving migrations."""

import difflib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("prepare-source.py")
OVERLAY = Path("codex-rs/tui/src/bottom_pane/chat_composer")
ASSETS = Path("codex-rs/tui/assets/noir")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def integration_patch(before, after):
    return "".join(
        difflib.unified_diff(
            f"first\n{before}\nlast\n".splitlines(keepends=True),
            f"first\n{after}\nlast\n".splitlines(keepends=True),
            fromfile="a/integration.txt",
            tofile="b/integration.txt",
        )
    )


class PrepareSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="noir-source-test-")
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name)
        self.fixture_count = 0
        spec = importlib.util.spec_from_file_location("prepare_source", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def fixture(self, *, existing=True, stamp_digest=True):
        self.fixture_count += 1
        case = self.temporary / str(self.fixture_count)
        self.root = case / "repository"
        self.checkout = case / "checkout"
        self.archive = case / "source.tar.gz"
        self.stamp = self.checkout / ".noir-source.json"
        self.previous_patch = write(
            case / "previous.patch", integration_patch("original", "dragon")
        )
        self.current_patch = write(
            self.root / "patches/integration.patch",
            integration_patch("original", "scene"),
        )
        self.module.ROOT = self.root
        self.module.PIN = {
            "tag": "rust-v1.2.3",
            "integration_patch": "patches/integration.patch",
            "integration_sha256": sha256(self.current_patch),
            "overlay_target": str(OVERLAY),
            "asset_target": str(ASSETS),
        }
        pristine = case / "pristine" / "codex-rust-v1.2.3"
        write(pristine / "integration.txt", "first\noriginal\nlast\n")
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.add(pristine, arcname=pristine.name)
        self.module.PIN["archive_sha256"] = sha256(self.archive)
        write(self.root / "src/noir_scene.rs", "pub struct NoirScene;\n")
        write(self.root / "src/noir_photo.rs", "pub struct Photo;\n")
        write(self.root / "src/snapshots/scene.snap", "scene snapshot\n")
        write(self.root / "assets/coast.nrf", "coast pixels\n")
        self.stale = (
            OVERLAY / "noir_dragon.rs",
            OVERLAY / "snapshots/dragon.snap",
            ASSETS / "retired.nrf",
        )
        if not existing:
            return
        write(self.checkout / "integration.txt", "first\noriginal\nlast\n")
        subprocess.run(["git", "init", "--quiet", str(self.checkout)], check=True)
        subprocess.run(["git", "add", "--all"], cwd=self.checkout, check=True)
        write(self.checkout / "integration.txt", "first\ndragon\nlast\n")
        old_files = {
            self.stale[0]: "pub struct NoirDragon;\n",
            self.stale[1]: "dragon snapshot\n",
            self.stale[2]: "retired pixels\n",
            OVERLAY / "noir_photo.rs": "pub struct Photo;\n",
            ASSETS / "coast.nrf": "coast pixels\n",
        }
        state = {
            "tag": self.module.PIN["tag"],
            "files": {
                str(relative): sha256(write(self.checkout / relative, content))
                for relative, content in old_files.items()
            },
        }
        if stamp_digest:
            state["integration_sha256"] = sha256(self.previous_patch)
        self.stamp.write_text(json.dumps(state))

    def run_main(self, *, previous=True, adopt_existing=False):
        arguments = [
            str(SCRIPT),
            "--checkout",
            str(self.checkout),
            "--archive",
            str(self.archive),
        ]
        if previous:
            arguments += ["--previous-patch", str(self.previous_patch)]
        if adopt_existing:
            arguments.append("--adopt-existing")
        with patch.object(sys, "argv", arguments), redirect_stdout(io.StringIO()):
            self.module.main()

    def checkout_contents(self):
        return {
            str(path.relative_to(self.checkout)): (
                ("symlink", os.readlink(path))
                if path.is_symlink()
                else ("file", path.read_bytes())
            )
            for path in self.checkout.rglob("*")
            if ".git" not in path.relative_to(self.checkout).parts
            and (path.is_file() or path.is_symlink())
        }

    def assert_rejected_without_changes(self, *, previous=True):
        before = self.checkout_contents()
        with self.assertRaises(RuntimeError):
            self.run_main(previous=previous)
        self.assertEqual(self.checkout_contents(), before)

    def test_fresh_checkout_and_reuse(self):
        self.fixture(existing=False)
        self.run_main(previous=False)
        self.assertTrue((self.checkout / ".git").is_dir())
        self.assertEqual(
            (self.checkout / "integration.txt").read_text(), "first\nscene\nlast\n"
        )
        self.assertEqual(
            (self.checkout / OVERLAY / "noir_scene.rs").read_bytes(),
            (self.root / "src/noir_scene.rs").read_bytes(),
        )
        before = self.checkout_contents()
        self.run_main(previous=False)
        self.assertEqual(self.checkout_contents(), before)

    def test_rename_migration_and_idempotence_preserve_unchanged_mtimes(self):
        self.fixture()
        unchanged = self.checkout / OVERLAY / "noir_photo.rs"
        os.utime(unchanged, ns=(946684800000000000, 946684800000000000))
        previous_mtime = unchanged.stat().st_mtime_ns
        self.run_main()
        self.assertEqual(unchanged.stat().st_mtime_ns, previous_mtime)
        for relative in self.stale:
            self.assertFalse((self.checkout / relative).exists())
        state = json.loads(self.stamp.read_text())
        self.assertEqual(
            state["integration_sha256"], self.module.PIN["integration_sha256"]
        )
        self.assertIn(str(OVERLAY / "noir_scene.rs"), state["files"])
        self.assertTrue(all(str(path) not in state["files"] for path in self.stale))
        before = self.checkout_contents()
        mtimes = {
            relative: (self.checkout / relative).stat().st_mtime_ns
            for relative in state["files"]
        }
        self.run_main(previous=False)
        self.assertEqual(self.checkout_contents(), before)
        self.assertEqual(
            {path: (self.checkout / path).stat().st_mtime_ns for path in mtimes},
            mtimes,
        )

    def test_legacy_stamp_and_missing_stale_files_can_migrate(self):
        self.fixture(stamp_digest=False)
        (self.checkout / self.stale[0]).unlink()
        self.run_main()
        self.assertTrue((self.checkout / OVERLAY / "noir_scene.rs").is_file())
        self.assertFalse((self.checkout / self.stale[1]).exists())

    def test_unstamped_checkout_requires_adoption_and_keeps_unmanaged_files(self):
        self.fixture()
        self.stamp.unlink()
        self.assert_rejected_without_changes()
        self.run_main(adopt_existing=True)
        self.assertTrue((self.checkout / OVERLAY / "noir_scene.rs").is_file())
        for relative in self.stale:
            self.assertTrue((self.checkout / relative).is_file())

    def test_source_stamp_symlink_is_preserved(self):
        self.fixture()
        outside = write(self.checkout.parent / "state.json", self.stamp.read_text())
        before = outside.read_bytes()
        self.stamp.unlink()
        self.stamp.symlink_to(outside)
        self.assert_rejected_without_changes()
        self.assertEqual(outside.read_bytes(), before)

    def test_other_upstream_version_is_preserved(self):
        self.fixture()
        state = json.loads(self.stamp.read_text())
        state["tag"] = "rust-v0.0.0"
        self.stamp.write_text(json.dumps(state))
        self.assert_rejected_without_changes()

    def test_archive_checksum_failure_does_not_create_checkout(self):
        self.fixture(existing=False)
        self.archive.write_bytes(b"unexpected source archive")
        with self.assertRaisesRegex(RuntimeError, "Source archive checksum"):
            self.run_main(previous=False)
        self.assertFalse(self.checkout.exists())

    def test_integration_checksum_failure_preserves_checkout(self):
        self.fixture()
        self.current_patch.write_text(integration_patch("original", "unexpected"))
        self.assert_rejected_without_changes()

    def test_edited_stale_files_are_preserved_before_integration_changes(self):
        for index in range(3):
            with self.subTest(extension=(".rs", ".snap", ".nrf")[index]):
                self.fixture()
                write(self.checkout / self.stale[index], "intentional local edit\n")
                self.assert_rejected_without_changes()

    def test_new_destination_conflict_is_preserved(self):
        self.fixture()
        write(self.checkout / OVERLAY / "noir_scene.rs", "local new module\n")
        self.assert_rejected_without_changes()

    def test_edited_retained_file_is_preserved(self):
        self.fixture()
        write(self.checkout / OVERLAY / "noir_photo.rs", "local photo changes\n")
        self.assert_rejected_without_changes()

    def test_canonical_changes_update_previously_synced_files(self):
        self.fixture()
        source = write(self.root / "src/noir_photo.rs", "pub struct UpdatedPhoto;\n")
        self.run_main()
        destination = self.checkout / OVERLAY / "noir_photo.rs"
        self.assertEqual(destination.read_bytes(), source.read_bytes())
        state = json.loads(self.stamp.read_text())
        self.assertEqual(state["files"][str(OVERLAY / "noir_photo.rs")], sha256(source))

    def test_migration_requires_previous_patch(self):
        self.fixture()
        self.assert_rejected_without_changes(previous=False)

    def test_previous_patch_checksum_must_match_stamp(self):
        self.fixture()
        self.previous_patch.write_text(integration_patch("original", "different"))
        self.assert_rejected_without_changes()

    def test_previous_patch_must_match_legacy_checkout_integration(self):
        self.fixture(stamp_digest=False)
        self.previous_patch.write_text(integration_patch("original", "different"))
        self.assert_rejected_without_changes()

    def test_incompatible_new_integration_rolls_back(self):
        self.fixture()
        self.current_patch.write_text(integration_patch("unexpected", "scene"))
        self.module.PIN["integration_sha256"] = sha256(self.current_patch)
        self.assert_rejected_without_changes()

    def test_new_integration_apply_failure_rolls_back(self):
        self.fixture()
        real_run = subprocess.run
        failed_calls = []

        def fail_new_apply(command, *args, **kwargs):
            if (
                command[:2] == ["git", "apply"]
                and command[-1] == str(self.current_patch)
                and "--check" not in command
                and "--reverse" not in command
            ):
                failed_calls.append(command)
                raise subprocess.CalledProcessError(1, command)
            return real_run(command, *args, **kwargs)

        with patch.object(self.module.subprocess, "run", side_effect=fail_new_apply):
            self.assert_rejected_without_changes()
        self.assertEqual(len(failed_calls), 1)

    def test_stamp_cannot_remove_files_outside_managed_paths(self):
        for kind in ("absolute", "parent", "unmanaged", "extension"):
            with self.subTest(kind=kind):
                self.fixture()
                outside = write(self.checkout.parent / "keep.rs", "keep this file\n")
                relative = {
                    "absolute": str(outside),
                    "parent": "../keep.rs",
                    "unmanaged": "keep.rs",
                    "extension": str(OVERLAY / "keep.txt"),
                }[kind]
                target = (
                    outside
                    if kind in ("absolute", "parent")
                    else write(self.checkout / relative, "keep this file\n")
                )
                state = json.loads(self.stamp.read_text())
                state["files"][relative] = sha256(target)
                self.stamp.write_text(json.dumps(state))
                self.assert_rejected_without_changes()
                self.assertEqual(outside.read_text(), "keep this file\n")

    def test_symlink_targets_and_parent_directories_are_preserved(self):
        for kind in ("stale_file", "incoming_file", "incoming_parent"):
            with self.subTest(kind=kind):
                self.fixture()
                outside = write(self.checkout.parent / "outside/keep.rs", "keep\n")
                if kind == "stale_file":
                    destination = self.checkout / self.stale[0]
                    destination.unlink()
                    destination.symlink_to(outside)
                elif kind == "incoming_file":
                    (self.checkout / OVERLAY / "noir_scene.rs").symlink_to(outside)
                else:
                    write(self.root / "src/nested/keep.rs", "new content\n")
                    (self.checkout / OVERLAY / "nested").symlink_to(
                        outside.parent, target_is_directory=True
                    )
                self.assert_rejected_without_changes()
                self.assertEqual(outside.read_text(), "keep\n")


if __name__ == "__main__":
    unittest.main()
