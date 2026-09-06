# Native host

`../build-native.sh` builds and signs a staged `../build/Dragon Terminal.app`. It does not install or launch the app. Run `python3 native/smoke-pty.py` from the source root to exercise the worker without starting Codex. After UI QA, `python3 install-dragon.py --apply` installs the app and launcher; existing destinations require `--replace` and receive timestamped backups.

The host uses AppKit and system WebKit. Bundled web assets are served only through `dragon://app`, without a network listener. Navigation and new windows in the web view are restricted, and native messages require the expected main-frame origin. The bridge exposes terminal input/resize, creating a separate session window, clipboard copy, and validated visual preferences. All shell processes belong to a window's private PTY worker.

Launch arguments are `--shell`, `--cwd DIR`, and `--resume [SESSION_ID]`. Bare `--resume` resumes the last Codex session. The installed launcher preserves the caller's working directory. New Codex and New Shell create windows; they never replace existing work.

The web page sends `ready`, `input`, `inputBytes`, `resize`, `newSession`, `copy`, `preferences`, and `ack` messages. It must acknowledge each native `data` event after xterm's write callback completes. Native emits `preferences`, `session`, base64 `data`, and `exit` events through `DragonNative.receive`. Native Copy/Paste/Select All and View menu items call the corresponding `DragonNative` methods.

The worker accepts framed stdin: one type byte plus a big-endian 32-bit payload length. Type 1 carries input bytes (maximum 64 KB); type 2 carries big-endian uint16 columns and rows; type 3 has an empty payload and closes the owned session. Stdout contains raw PTY bytes. EOF on worker stdin also shuts down that session. Worker input/output queues are each bounded to 512 KB; the host output queue is bounded to 512 KB with one 32 KB delivery awaiting acknowledgement. The host input queue is capped at 1 MB, with one four-byte slot that coalesces pending resize requests to the newest dimensions.

Explicit local QA arguments are `--qa-script PATH --qa-delay SECONDS` and `--snapshot PATH --snapshot-delay SECONDS`. The script runs once after the web page is ready. Snapshot capture writes the PNG at PATH and the JSON result of `DragonNative.snapshot()` at PATH.json. These developer arguments are not exposed through the web bridge, and normal launches do not execute QA scripts or export snapshots.

The host is built from `DragonTerminal.m` with clang and system AppKit/WebKit frameworks. This avoids a Swift SDK/module mismatch in the installed Command Line Tools. No system toolchain files are modified.
