# Dragon Terminal

A local macOS terminal with a floating dragon, animated backgrounds, and a translucent terminal surface. It opens your existing Codex Noir or a login zsh session.

## Launch

```sh
dragon-terminal
dragon-terminal --shell
dragon-terminal --cwd /path/to/project
dragon-terminal --resume
dragon-terminal --resume SESSION_ID
```

The launcher lives at `~/.local/bin/dragon-terminal`; the app lives at `~/Applications/Dragon Terminal.app`. Bare `--resume` resumes the most recent Codex session. Launching from a terminal preserves its current directory unless `--cwd` is supplied.

`codex` remains the original CLI. `codex-noir` remains the separately installed customized CLI. The app uses the existing Codex authentication, settings, models, tools, and session history.

## Controls

Choose Dragon, Aurora, Embers, or Void; then select Noir, Violet, Ember, or Ice colors. Glow adjusts the animation strength, Terminal shade adjusts readability, and Motion pauses animation. Appearance is saved in native macOS preferences under `local.codex-noir.dragon-terminal`.

“Follow Codex” infers activity from the terminal's visible status. Manual Ambient, Working, Max, and Ultra modes control appearance. Use `/model` inside Codex to change actual model or reasoning settings.

| Shortcut | Action |
| --- | --- |
| ⌘N | New Codex window |
| ⇧⌘N | New shell window |
| ⌘C / ⌘V / ⌘A | Copy selection / paste / select all |
| ⌘= / ⌘− | Larger / smaller terminal text |
| ⇧⌘F | Toggle focus mode |
| ⌘W | Close this window |

New windows preserve existing sessions. Closing an active window or quitting asks before stopping its owned processes.

## Architecture and dependencies

The Objective-C host in `native/DragonTerminal.m` uses system AppKit and WKWebView. A separate C `forkpty` worker owns each terminal session. Bundled web assets load through `dragon://app`; there is no local HTTP server, WebSocket listener, Electron, or Node runtime. External web navigation is rejected, and native bridge messages require the bundled main-frame origin.

Terminal input and output use bounded queues. Each output delivery is acknowledged after xterm finishes writing it. Resize events are coalesced while input is blocked. Background animation uses canvas behind the terminal and never writes decorative bytes into the terminal stream.

| Dependency | Version / source | License |
| --- | --- | --- |
| `@xterm/xterm` | 6.0.0, vendored JS and CSS | [MIT](web/vendor/LICENSE-xterm) |
| `@xterm/addon-fit` | 0.11.0, vendored JS | [MIT](web/vendor/LICENSE-addon-fit) |
| AppKit, WebKit, Objective-C runtime, libc | Supplied by macOS | Apple system components |
| Codex Noir | Existing separate installation, based on Codex 0.153.4 | Upstream Apache-2.0 |
| clang / macOS SDK | Xcode Command Line Tools, build only | Apple developer tools |
| Python 3 standard library | Installer and tests only | Python Software Foundation license |

Vendor archive SHA-256 hashes are recorded in [web/vendor/provenance.json](web/vendor/provenance.json). The app targets Apple Silicon. Both native executables declare macOS 12.0 as their minimum; this personal build was checked on this machine's current macOS.

## Build and install

From this directory, with Command Line Tools and Python 3 available:

```sh
./build-native.sh
python3 native/smoke-pty.py
python3 install-dragon.py
```

The build creates and ad-hoc signs `build/Dragon Terminal.app`. The installer command above only validates and previews. After UI QA, install with `python3 install-dragon.py --apply`. When replacing an earlier installation, add `--replace`; existing app and launcher paths move to timestamped sibling backups, with automatic rollback on installation failure. Quit running copies before rebuilding or replacing them.

To restore a backup, move aside the current app and launcher and rename their corresponding `.backup-TIMESTAMP` paths to their original names. Codex configuration and authentication are outside this installer.

The worker smoke checks exercise UTF-8, resizing, Ctrl-C, output backpressure, exit status, malformed input, and independent-session shutdown. `native/smoke-host.m` additionally checks the host's input cap and resize coalescing. See [native/README.md](native/README.md) for the bridge and worker protocol.

## Diagnostic snapshots

Explicit QA arguments can execute a local JavaScript file once after the page is ready and export a snapshot:

```sh
"build/Dragon Terminal.app/Contents/MacOS/DragonTerminal" \
  --shell --cwd /private/tmp \
  --qa-script /private/tmp/dragon-qa.js --qa-delay 1 \
  --snapshot /private/tmp/dragon-qa.png --snapshot-delay 4
```

This writes the PNG plus `/private/tmp/dragon-qa.png.json`, containing visual preferences, terminal dimensions, visible terminal text, activity, and renderer statistics. These actions only run when explicitly supplied on the native command line; the web bridge cannot request arbitrary script or snapshot paths.
