# Codex Noir

A terminal-only setup with photographic ASCII, quiet motion, a charcoal palette, and a clean zsh prompt. Run it inside macOS Terminal. The custom rendering lives in the real Codex Rust TUI.

The visual reference is [Madhavan's website](https://madhavanprasanna.com/). Flight samples its birds-in-flight footage; Transit uses a real long-exposure subway photograph. Both are bundled as grayscale pixels and rendered into terminal glyphs. [Source credits](assets/SOURCES.md).

## Use

```sh
codex-noir
codex-noir resume --last
CODEX_NOIR_SCENE=transit codex-noir
CODEX_NOIR_SCENE=off codex-noir
codex-noir -c tui.animations=false
```

Flight is the default. Images move while Codex is working and become still while idle. The scene yields to input, popups, and small terminal regions. `CODEX_NOIR_SCENE=off` hides only the scene; disabling `tui.animations` also removes the activity rail. The legacy `CODEX_NOIR_DRAGON=0` switch remains supported.

The launcher forwards every ordinary Codex command and argument. It preserves your authentication, history, tools, permissions, model, and reasoning effort. It selects the local `noir-velocity` syntax theme when available and disables updater checks for this separately maintained build. Later `-c` arguments override launcher defaults. The stock `codex` command remains available.

The shell prompt shows the working directory, Git branch, and a nonzero exit status. It uses no continuously running animation or background prompt process. The Terminal profile retains the existing SF Mono font and uses restrained colors for normal text, syntax, selection, and the cursor.

## Source map

| Path | What to change |
| --- | --- |
| `src/noir_dragon.rs` | Scene layout, lifecycle, and composer wrapper; private names retained for compatibility |
| `src/noir_photo.rs` | Bounded frame parsing, timing, and luminance sampling |
| `src/noir_*tests.rs`, `src/snapshots/` | Renderer behavior and visual regression tests |
| `src/noir_activity.rs` | The activity rail, using actual Codex effort settings |
| `assets/` | Grayscale footage, photographs, metadata, and credits |
| `terminal/noir.zsh` | Shell prompt |
| `terminal/Noir Velocity.terminal` | macOS Terminal profile |
| `terminal/noir-velocity.tmTheme` | Code highlighting inside Codex |
| `patches/codex-integration.patch` | Pinned upstream integration, release lockfile alignment, and existing terminal test fixture fixes |
| `scripts/` | Source preparation, build, installation, media conversion, previews, and Claude handoffs |
| `upstream.json` | Exact upstream version and source checksum |

This is a personal modification of OpenAI Codex **0.153.4**, using Rust **1.95.0**. The repository stores the custom modules directly; the build script reconstructs the full upstream tree and copies these modules into it. Image data is embedded at compile time, with no runtime image downloads or video decoder.

## Build and install

Python 3.12+, the pinned Rust toolchain, and the upstream macOS build prerequisites are required. Use the [pinned upstream source](https://github.com/openai/codex/tree/rust-v0.153.4) for its full development prerequisites. The installer targets Apple Silicon and reuses companion executables from an existing, exact-version Codex package.

```sh
./scripts/build.sh
cd build/codex-rust-v0.153.4/codex-rs
env -u NO_COLOR TERM_PROGRAM=Apple_Terminal just test -p codex-tui --cargo-profile dev-small
just fix -p codex-tui --profile dev-small --allow-staged
just fmt
cd ../../..
python3 scripts/install-terminal.py --apply --activate-profile
python3 scripts/install.py --replace --apply
```

Both installers preview by default when `--apply` is omitted. Existing custom packages, launchers, and shell configuration are backed up before replacement. The Terminal installer imports a new profile, selects it for new windows, and applies it to tabs already using Overdrive Noir or Noir Velocity. Open a new shell or run `source ~/.config/zsh/noir.zsh` to load the new prompt in an existing tab.

This machine also has a prepared source/toolchain cache. To reuse it after editing `src/`:

```sh
. /private/tmp/codex-noir-build/env.sh
./scripts/build.sh \
  --checkout /private/tmp/codex-noir-build/codex-rust-v0.153.4 \
  --archive /private/tmp/codex-noir-build/codex-rust-v0.153.4.tar.gz \
  --adopt-existing
python3 scripts/install.py --replace --apply \
  --binary /private/tmp/codex-noir-build/target/dev-small/codex \
  --source-root /private/tmp/codex-noir-build/codex-rust-v0.153.4 \
  --source-archive /private/tmp/codex-noir-build/codex-rust-v0.153.4.tar.gz
```

Review and test source edits before reinstalling. The preparer checks the archive checksum and integration patch, and refuses to overwrite diverging edits made directly in an existing checkout. Copy intentional checkout edits back into `src/` first.

The installed package is `~/.local/share/codex-noir/0.153.4`; the launcher is `~/.local/bin/codex-noir`. Each package includes its editable source bundle and provenance hashes. To roll back, move aside the current package and launcher and restore their matching timestamped `.backup-*` siblings. Shell/profile backup mappings are under `~/.config/terminal/backups/noir-velocity-*/files.json`.

## Working with Claude

[CLAUDE.md](CLAUDE.md) teaches Claude the codebase, design direction, and input-safety rules. Start `claude` in this directory for an interactive session, or send a task through the peer script:

```sh
python3 scripts/claude-peer.py 'Review the scene design and suggest one improvement.'
python3 scripts/claude-peer.py --edit 'Refine the renderer in src/; keep input and motion controls intact.'
```

The script uses your installed, logged-in Claude Code and resumes a dedicated conversation. Its default tools allow repository reading; `--edit` additionally allows file edits. Shell execution and external MCP tools are not enabled by the bridge. Claude's response is printed for Codex or you to review. Prompts passed over stdin are also supported. Session state and results stay in ignored `.agents-local/`; use `--new` for a fresh conversation.

One agent should own a given file at a time. Codex coordinates assets, builds, installation, and final checks; Claude's assigned design work stays within the agreed renderer files. The initial redesign was carried out through the actual Claude CLI using a resumable session.

## Bring your own imagery

Use a local photo or a short local video. Conversion requires Pillow; video also requires ffmpeg. These are build tools only.

```sh
python3 -m venv .venv
.venv/bin/pip install -r scripts/requirements-media.txt
.venv/bin/python scripts/prepare-media.py /path/to/photo.jpg \
  --name transit --title Transit \
  --source-url https://example.com/original \
  --source-credit 'Photographer / source'
```

For a clip, use `--name flight --title Flight --video --start 0 --duration 4 --fps 12`. The converter generates a JPEG preview, metadata, and a bounded `.nrf` grayscale sequence. It uses one contrast curve across a clip and blends the loop transition. Rebuild after changing assets. The bundled scene selectors are `flight` and `transit`; additional names need a renderer entry.

## Verification and history

The preview harness runs the actual CLI in a pseudo-terminal against an inert local streaming fixture. It does not use your authentication or call a model API. Install `scripts/requirements-preview.txt`, then run:

```sh
python3 scripts/preview-terminal.py ~/.local/share/codex-noir/0.153.4/bin/codex \
  --scenes flight transit --ansi256
```

Use the packaged executable so its companion tools are present. Captures and machine-readable checks are written under ignored `output/terminal-preview/`. The 13 focused Noir renderer tests pass, including layout/cursor preservation, clipping, scene selection, animation lifecycle, parser limits, and the reviewed visual snapshots. The final scoped Clippy fix, formatting, and CLI build also completed successfully.

Eight actual CLI capture scenarios pass: Flight and Transit at 112×30 in 256 colors; Flight at 80×24 in truecolor; a 40×18 terminal that hides the image; scene-off and motion-off settings; and the actual Max and Ultra effort rails. Each preserves the draft and stops decorative movement while idle. The captures use an isolated local fixture, with no model request or change to the user's settings.

The complete TUI suite in a macOS Terminal environment passed 4,073 tests and skipped six. Its 31 remaining snapshot failures are unrelated to the photographic renderer: 28 expect the development version `0.0.0` instead of the pinned release `0.153.4`, and three expect an Option–Up hint where upstream selects Shift–Left for Apple Terminal. The real-binary reconnect and immediate-input checks pass. These unrelated snapshots are left unchanged. An initial run with the coding host's `NO_COLOR=1` also suppressed ANSI sequences expected by four cursor tests; those passed with colors enabled for the target terminal.

The earlier desktop app prototype is preserved at the [`app-prototype`](https://github.com/Madhavan113/customcodexterminal/tree/app-prototype) tag. Main now develops the ordinary terminal setup.
