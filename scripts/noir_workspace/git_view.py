"""Read-only Git inspection with a working-directory baseline, not a HEAD baseline."""

import difflib
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from .store import clean, private_directory

FILE_LIMIT = 1024 * 1024
TOTAL_LIMIT = 32 * FILE_LIMIT
FILE_COUNT_LIMIT = 30_000


def git(root, *args, check=True):
    result = subprocess.run(
        ["git", "--no-optional-locks", "--literal-pathspecs", "-C", str(root), *args],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if check and result.returncode:
        raise ValueError(
            clean(
                result.stderr.decode(errors="replace").strip() or "Git command failed",
                1000,
            )
        )
    return result


def project_root(path):
    path = Path(path).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Project directory does not exist: {path}")
    result = git(path, "rev-parse", "--show-toplevel", check=False)
    return Path(os.fsdecode(result.stdout).strip()) if result.returncode == 0 else path


def branch(root):
    result = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if result.returncode:
        result = git(root, "rev-parse", "--short", "HEAD", check=False)
    return clean(os.fsdecode(result.stdout).strip(), 100) or "No commits"


class GitView:
    def __init__(self, directory):
        self.directory = private_directory(Path(directory) / "baselines")
        self.cache = {}

    def paths(self, root):
        result = git(
            root,
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            check=False,
        )
        if result.returncode:
            return None
        paths = sorted({os.fsdecode(p) for p in result.stdout.split(b"\0") if p})
        if len(paths) > FILE_COUNT_LIMIT:
            raise ValueError(
                f"Project has over {FILE_COUNT_LIMIT:,} files; choose a smaller project for changes inspection"
            )
        return paths

    def read(self, root, relative):
        path = Path(root) / relative
        # A tracked directory may have been replaced by an outward-pointing symlink.
        if not path.parent.resolve().is_relative_to(Path(root).resolve()):
            return {"kind": "unavailable", "reason": "Path leaves project"}
        try:
            meta = path.lstat()
        except FileNotFoundError:
            return None
        signature = (
            meta.st_ino,
            meta.st_mtime_ns,
            meta.st_ctime_ns,
            meta.st_size,
            meta.st_mode,
        )
        cache_key = str(path)
        if cache_key in self.cache and self.cache[cache_key][0] == signature:
            return self.cache[cache_key][1]
        if stat.S_ISLNK(meta.st_mode):
            data = os.fsencode(os.readlink(path))
            kind = "symlink"
        elif stat.S_ISREG(meta.st_mode):
            if meta.st_size > FILE_LIMIT:
                return {
                    "kind": "large",
                    "size": meta.st_size,
                    "modified": meta.st_mtime_ns,
                    "mode": stat.S_IMODE(meta.st_mode),
                }
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read(FILE_LIMIT + 1)
            if len(data) > FILE_LIMIT:
                return {
                    "kind": "large",
                    "size": len(data),
                    "modified": meta.st_mtime_ns,
                }
            kind = "file"
        else:
            return {
                "kind": "directory" if path.is_dir() else "special",
                "mode": stat.S_IMODE(meta.st_mode),
            }
        try:
            content = data.decode("utf-8") if b"\0" not in data else None
        except UnicodeDecodeError:
            content = None
        value = {
            "kind": kind,
            "hash": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mode": stat.S_IMODE(meta.st_mode),
            "content": content,
        }
        self.cache[cache_key] = (signature, value)
        return value

    def snapshot(self, root):
        paths = self.paths(root)
        if paths is None:
            return None
        files, total = {}, 0
        for path in paths:
            try:
                value = self.read(root, path)
            except OSError as error:
                value = {"kind": "unavailable", "reason": str(error)}
            if value is None:
                continue
            total += len((value.get("content") or "").encode())
            if total > TOTAL_LIMIT:
                raise ValueError(
                    "Text baseline exceeds 32 MiB; choose a smaller project"
                )
            files[path] = value
        return files

    def capture(self, root, agent_id):
        files = self.snapshot(root)
        path = self.directory / f"{agent_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"root": str(root), "files": files}, ensure_ascii=True)
        )
        temporary.chmod(0o600)
        temporary.replace(path)

    def inspect(self, root, agent_id, scope="task", selected=None):
        if scope == "git":
            return self.git_changes(root, selected)
        path = self.directory / f"{agent_id}.json"
        if not path.is_file():
            return {
                "files": [],
                "diff": "Baseline is being captured.",
                "branch": branch(root),
            }
        before = json.loads(path.read_text())["files"]
        after = self.snapshot(root)
        if before is None or after is None:
            return {
                "files": [],
                "diff": "Changes inspection requires a Git repository.",
                "branch": "No Git repository",
            }
        files, diffs = [], {}
        for name in sorted(before.keys() | after.keys()):
            old, new = before.get(name), after.get(name)
            if old == new:
                continue
            status = "A" if old is None else "D" if new is None else "M"
            old_text = old.get("content") if old else ""
            new_text = new.get("content") if new else ""
            if old_text is not None and new_text is not None:
                lines = list(
                    difflib.unified_diff(
                        old_text.splitlines(keepends=True),
                        new_text.splitlines(keepends=True),
                        fromfile=f"a/{name}",
                        tofile=f"b/{name}",
                    )
                )
                additions = sum(
                    line.startswith("+") and not line.startswith("+++")
                    for line in lines
                )
                deletions = sum(
                    line.startswith("-") and not line.startswith("---")
                    for line in lines
                )
                diff = "".join(
                    line
                    if line.endswith("\n")
                    else line + "\n\\ No newline at end of file\n"
                    for line in lines
                )
                if (
                    old
                    and new
                    and (
                        old.get("mode") != new.get("mode")
                        or old.get("kind") != new.get("kind")
                    )
                ):
                    diff = (
                        f"Mode/type changed: {old.get('kind')} {old.get('mode', 0):o} → {new.get('kind')} {new.get('mode', 0):o}\n"
                        + diff
                    )
                if not diff:
                    diff = f"{status} {name} (empty file)"
            else:
                additions = deletions = None
                reason = (
                    "binary file"
                    if (old or new).get("hash")
                    else "large or unavailable file"
                )
                diff = f"{status} {name}\nContent preview unavailable: {reason}.\n"
            files.append(
                {
                    "path": name,
                    "status": status,
                    "added": additions,
                    "removed": deletions,
                }
            )
            diffs[name] = clean(diff)
        selected = selected if selected in diffs else next(iter(diffs), None)
        return {
            "files": files,
            "selected": selected,
            "diff": diffs.get(selected, "No changes since this agent started."),
            "branch": branch(root),
        }

    def git_changes(self, root, selected):
        result = git(
            root, "status", "--porcelain=v1", "-z", "--untracked-files=all", check=False
        )
        if result.returncode:
            return {
                "files": [],
                "diff": "Changes inspection requires a Git repository.",
                "branch": "No Git repository",
            }
        entries = result.stdout.split(b"\0")
        files, index = [], 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            status, path = os.fsdecode(entry[:2]), os.fsdecode(entry[3:])
            original = None
            if "R" in status or "C" in status:
                original = os.fsdecode(entries[index])
                index += 1
            files.append(
                {
                    "path": path,
                    "status": status,
                    "original": original,
                    "added": None,
                    "removed": None,
                }
            )
        chosen = next(
            (item for item in files if item["path"] == selected),
            files[0] if files else None,
        )
        diff = "Working tree is clean."
        if chosen:
            selected = chosen["path"]
            if chosen["status"] == "??":
                value = self.read(root, selected)
                content = (value or {}).get("content")
                diff = (
                    "".join(
                        difflib.unified_diff(
                            [],
                            (content or "").splitlines(keepends=True),
                            fromfile="/dev/null",
                            tofile=f"b/{selected}",
                        )
                    )
                    if content is not None
                    else f"Untracked binary or large file: {selected}"
                )
                diff = diff or f"Untracked empty file: {selected}"
            else:
                paths = [selected] + (
                    [chosen["original"]] if chosen["original"] else []
                )
                args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
                staged = os.fsdecode(git(root, *args, "--cached", "--", *paths).stdout)
                unstaged = os.fsdecode(git(root, *args, "--", *paths).stdout)
                diff = (f"Staged changes\n{staged}\n" if staged else "") + (
                    f"Unstaged changes\n{unstaged}" if unstaged else ""
                )
                diff = diff or f"{chosen['status']} {selected}"
        return {
            "files": files,
            "selected": selected,
            "diff": clean(diff),
            "branch": branch(root),
        }
