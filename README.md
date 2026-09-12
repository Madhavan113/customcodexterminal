# Codex Noir

A customized Codex terminal interface with photographic glyph art, activity-aware motion, a violet palette, and a colored zsh prompt. Run `codex-noir` inside your terminal to use the optimized Rust TUI.

The [`noir` agent workspace](https://github.com/Madhavan113/noir) is now a standalone project at `~/noir`, with its own Python package, dependencies, tests and installer. Develop the workspace there. This repository owns `codex-noir`, `claude-noir`, renderer assets, terminal profiles and shell customization.

The visual references are [Madhavan's website](https://madhavanprasanna.com/), the supplied lavender/yellow halftones, and [Cosmos](https://www.cosmos.so/e/885336733). Coast uses a real rocky-coast photograph; Flight samples the website's birds-in-flight footage; Transit uses a long-exposure subway photograph. Moire is a moving mathematical checker/dot field. [Source credits](assets/SOURCES.md).

## Performance

Interactive builds use Rust's optimized `release` profile. The renderer caches one decorative frame at the scene's animation cadence, so keyboard and status redraws can reuse it. Input stays responsive while the scene animates, and motion stops when the turn becomes idle.

In the 2026-09-07 comparison against the previous `dev-small` build, a 500-line local response followed by streaming updates produced these results at 120×36 in 256 colors:

| Mode | Median keypress echo before → after | Process CPU time before → after |
| --- | --- | --- |
| Cruise | 50.7 → 29.2 ms | 6.72 → 2.26 s |
| Warp | 51.5 → 31.0 ms | 7.35 → 2.88 s |

All 14 live CLI scenarios preserved draft text, cursor position, and idle behavior across color modes, drive settings, and motion controls. These measurements include the CLI and an emulated terminal; they do not measure a native terminal window's display latency. CPU time includes startup and shutdown. The [benchmark commands](#verification-and-history) reproduce the workload without calling a model API.

With the violet warp bar enabled, the optimized Ultra build measured **30.1 ms** median keypress echo in the same 120×36, 256-color streaming workload. All six release verification scenarios preserved the draft, cursor, and idle behavior. Local captures and results are under ignored `output/warp-mode/`.

## Codex TUI: `codex-noir`

```sh
codex-noir
codex-noir resume --last
codex-noir -c 'model_reasoning_effort="ultra"'
CODEX_NOIR_SCENE=coast codex-noir
CODEX_NOIR_SCENE=moire codex-noir
CODEX_NOIR_SCENE=flight codex-noir
CODEX_NOIR_SCENE=transit codex-noir
CODEX_NOIR_SCENE=dither codex-noir
CODEX_NOIR_STYLE=ascii codex-noir
CODEX_NOIR_STYLE=dither codex-noir
CODEX_NOIR_SCENE=off codex-noir
codex-noir -c tui.animations=false
```

Dither is the default scene. Coast and the other photographs default to the `halftone` style, which uses Unicode Braille dots for finer photographic detail, with lavender ink and pale acid-yellow bands. `CODEX_NOIR_STYLE=ascii` selects the original character-density treatment. `CODEX_NOIR_STYLE=dither` renders any scene through a screen-fixed 8×8 Bayer ordered dither as foreground dots only, so the Terminal wallpaper shows through. Unknown scene values fall back to Dither; unknown style values fall back to the scene's default.

`CODEX_NOIR_SCENE=dither`, the default, is the background scene: the Coast photograph fills the whole band edge to edge with no caption, dithered by default, while three soft lights drift across it and the dithered shore surfaces and sinks under them.

The reasoning effort drives the band. Normal effort cruises in lavender and acid yellow. **Max** shifts into overdrive: magenta-to-amber colors, a fast sweeping beam, motion-blur streaks, a faster gradient, 20 redraws a second. **Ultra** jumps to warp: over two and a half seconds the picture smears away and a star stream pours out of a vanishing point in cyan, white, and acid green at 30 redraws a second. Idle frames ignore the drive, so a resting terminal always looks the same. `CODEX_NOIR_DRIVE=cruise|overdrive|warp` pins a level for any tier.

The **warp bar** appears automatically while working at the highest reasoning settings. At `xhigh` or `max`, a filled violet strip above the draft carries an oscillating **EXTRA THINKING** label; at `ultra`, **ULTRA** sweeps back and forth faster. It follows effort changes and resumed sessions, stays inside the spare row above the draft, and yields to popups. Its animation advances at most 20 times a second and stops when the turn ends. Select the effort in Codex or pass the configuration argument above; the launcher keeps your existing effort unless you change it.

Images move while Codex is working and become still while idle. Coast and Transit drift gently through still photographs; Flight plays sampled footage; Moire moves its interference pattern. The scene yields to input, popups, and small terminal regions. `CODEX_NOIR_SCENE=off` hides only the scene; disabling `tui.animations` also removes the activity rail. The legacy `CODEX_NOIR_DRAGON=0` switch remains supported.

The launcher forwards every ordinary Codex command and argument. It preserves your authentication, history, tools, permissions, model, and reasoning effort. It selects the local `noir-velocity` syntax theme when available and disables updater checks for this separately maintained build. Later `-c` arguments override launcher defaults. The stock `codex` command remains available.

The shell prompt shows the working directory, Git branch, and a nonzero exit status in color. It uses no continuously running animation or background prompt process. The Terminal profile retains the existing SF Mono font and uses restrained colors for normal text, syntax, selection, and the cursor.

The Terminal window itself uses a photograph as its wallpaper: fog-bound tree silhouettes found on Cosmos, shown in their original color. It stays still behind ordinary shell commands as well as Codex. Swap the picture with `python3 scripts/prepare-background.py --photo /path/to/image.jpg` followed by `python3 scripts/install-terminal.py --apply`.

## WezTerm: the animated wallpaper

macOS Terminal cannot animate a background image, so the moving wallpaper lives in [WezTerm](https://wezterm.org/), which plays animated GIF, WebP, and PNG backgrounds natively. `terminal/wezterm.lua` carries the Noir Velocity palette, SF Mono, and the wallpaper; the colored prompt comes from your login shell as usual, and `codex-noir` and `claude-noir` run unchanged inside it.

```sh
brew install --cask wezterm
python3 scripts/install-wezterm.py --apply
python3 scripts/install-wezterm.py --apply --wallpaper /path/to/other.gif
open -a WezTerm
```

The config is copied to `~/.config/wezterm/wezterm.lua` and the GIF to `~/.config/wezterm/noir-velocity.gif`. Edit the two variables at the top of the config to swap the file or change `dim`, the translucent wash that keeps text readable over the picture (0 shows it exactly as shot). Terminal.app keeps a still frame of the same GIF as its wallpaper.

The installed profile uses Mac-style editing shortcuts: `Command+C` copies, `Command+V` pastes, and `Command+Z` sends the terminal undo sequence used by zsh and the Codex composer. `Ctrl+C` and `Ctrl+Z` retain their normal Unix interrupt and process-suspension behavior. Codex leaves the mouse to the terminal by default, so the wheel scrolls the transcript through your terminal's own scrollback. Set `CODEX_NOIR_MOUSE=1` to let a click inside the composer place its insertion cursor instead; Codex then owns the mouse for that session, the wheel no longer scrolls, and you hold Shift while dragging to select terminal text before pressing `Command+C`.

## Claude Code: `claude-noir`

`claude-noir` gives Claude Code the same setup. Claude Code is closed source, so instead of patching it the launcher opens a dedicated tmux layout: a small top pane where Claude's mascot dances across the width (bouncing, hopping, flipping, leaving a lavender dither trail) with a shiba bounding beside it, and the main pane running the real `claude` with all your arguments, authentication, and settings. Closing Claude closes the layout.

The bottom row of the top pane is an effort rail, the counterpart of the Codex band. While Claude works at `xhigh`, or on a turn containing `ultrathink`, it becomes a filled violet **EXTRA THINKING** bar. At `max` or with `ultracode`, the **ULTRA** label sweeps back and forth faster. The bar keeps the active tool name visible when there is room and colors the tmux divider violet. Other effort levels retain a cyan beam for `high` and slate for `low` and `medium`; permission prompts show amber `WAITING FOR YOU`. Warp stops between turns and while waiting for you. The companion keeps its existing 12-frame-per-second cadence.

To know this, the launcher passes `claude` an inline `--settings` that registers `scripts/claude-pulse.py` as a background hook (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Notification`, `Stop`, and a few more) for that session only. Hooks passed this way merge with your own settings. The rail prefers [Claude's reported active effort](https://code.claude.com/docs/en/hooks), with environment overrides, the launch flag, and saved settings as fallbacks. The pulse writes a small JSON file under `~/.local/state/claude-noir/`, which the launcher removes on exit, and it does nothing outside `claude-noir`. If you pass your own `--settings`, the launcher leaves it alone and the rail shows only `CLAUDE`.

```sh
brew install tmux
python3 scripts/install-claude.py --apply
claude-noir
claude-noir --effort xhigh         # violet EXTRA THINKING bar while working
claude-noir --effort max           # violet ULTRA bar while working
CLAUDE_NOIR_ROWS=10 claude-noir     # taller mascot pane (default 8)
CLAUDE_NOIR_DANCER=off claude-noir  # plain claude
```

The launcher uses its own tmux server socket and config (`terminal/claude-noir.tmux.conf`), so an existing tmux setup is untouched. Inside an existing tmux session, or without tmux, it runs plain `claude`. The animation is `scripts/claude-dancer.py` and the hook is `scripts/claude-pulse.py`, both installed to `~/.local/share/claude-noir/`.

## Palette and Ghostty

`terminal/palette.json` is the single source of the terminal colors: a vivid set (coral red, mint, gold, periwinkle, orchid, sky cyan) on the dark violet background. `python3 scripts/apply-palette.py` writes it into the macOS Terminal profile, `terminal/wezterm.lua`, `terminal/ghostty.conf`, and Cursor's integrated-terminal colors; re-run the installers afterwards. Every profile uses a blinking bar cursor.

Ghostty gets the same profile through `terminal/ghostty.conf`, installed with `python3 scripts/install-ghostty.py --apply` to `~/.config/ghostty/config`. Stock Ghostty shows only PNG/JPEG backgrounds, so it displays the wallpaper's first frame.

`patches/ghostty-animated-background.patch` teaches Ghostty 1.3.1 to play animated GIF backgrounds: it enables wuffs's GIF decoder, adds `pkg/wuffs/src/gif.zig` (all frames composited with disposal handling, unit-tested), and makes the renderer swap frames on the existing animation draw timer, so `background-image = something.gif` simply animates. The patch also stops the xcframework step from building iOS libraries for native-only builds. With only the Command Line Tools, the whole Zig side compiles and the renderer tests pass (`zig build test -Dxcframework-target=native -Demit-xcframework=false -Demit-macos-app=false -Dtest-filter=renderer`, with `metal`/`metallib` stubbed); packaging `Ghostty.app` still needs full Xcode for `xcodebuild` and the Metal shader compiler. `scripts/build-ghostty.sh` clones v1.3.1 into `build/ghostty`, applies the patch, fetches Zig 0.15.2, and builds the app once Xcode is selected.

## Shell prompt

The prompt prints the path in cyan, the Git branch in magenta, the `››` marker in yellow, and a nonzero exit status in red. `NO_COLOR` prints it plain. It runs no background process.

## Cursor terminal wallpaper

Cursor's built-in terminal has no wallpaper setting, so the same photograph is layered over its panel with the [vscode-background](https://github.com/shalldie/vscode-background) extension (`shalldie.background`), which patches Cursor's workbench files. Install it with `cursor --install-extension shalldie.background`, then add to Cursor's `settings.json`:

```json
"background.enabled": true,
"background.editor": { "images": [], "useFront": false },
"background.panel": {
  "images": ["file:///Users/you/.config/terminal/backgrounds/noir-velocity.png"],
  "opacity": 0.3,
  "size": "cover"
}
```

Restart Cursor; the extension patches itself in on first launch and asks for one more restart. `opacity` is the image layer's opacity over the panel (0.1–0.3 keeps text readable). Cursor will warn that its installation "appears corrupt" because a file changed; that is expected, and every Cursor update needs the extension to re-apply its patch.

## Source map

| Path | What to change |
| --- | --- |
| `src/noir_scene.rs` | Scene layout, lifecycle, and composer wrapper |
| `src/noir_frame.rs` | Cached decorative frames at each scene's cadence, independent of keyboard redraws |
| `src/noir_photo.rs` | Bounded frame parsing, timing, and luminance sampling |
| `src/noir_halftone.rs` | Braille/ASCII painting, color adaptation, photographic crops, the Moire field, and the drifting glow |
| `src/noir_dither.rs` | Bayer ordered-dither painting used by the `dither` style and scene |
| `src/noir_warp.rs` | The Ultra-effort star stream |
| `src/noir_*tests.rs`, `src/snapshots/` | Renderer behavior and visual regression tests |
| `src/noir_activity.rs` | The activity rail and violet warp bar, using actual Codex effort settings |
| `assets/` | Grayscale footage, photographs, metadata, and credits |
| `licenses/codex.txt` | OpenAI Codex license and attribution |
| `terminal/noir.zsh` | Colored shell prompt |
| `terminal/palette.json`, `scripts/apply-palette.py` | Shared terminal colors for Terminal, WezTerm, Ghostty, and Cursor |
| `terminal/ghostty.conf`, `scripts/install-ghostty.py` | Ghostty profile |
| `bin/claude-noir`, `scripts/claude-dancer.py`, `scripts/claude-pulse.py`, `terminal/claude-noir.tmux.conf`, `scripts/install-claude.py` | Claude Code layout with the dancing mascot, the shiba, and the effort rail |
| `terminal/Noir Velocity.terminal` | macOS Terminal profile |
| `terminal/wezterm.lua`, `scripts/install-wezterm.py` | WezTerm profile with the animated GIF wallpaper |
| `terminal/backgrounds/noir-velocity.png` | Native wallpaper, regenerated by `scripts/prepare-background.py` |
| `terminal/noir-velocity.tmTheme` | Code highlighting inside Codex |
| `patches/codex-integration.patch` | Pinned upstream integration, release lockfile alignment, and existing terminal test fixture fixes |
| `scripts/` | Source preparation, build, installation, media conversion, previews, and Claude handoffs |
| `scripts/noir_project.py` | Shared pinned metadata and file checksums for build and packaging |
| `upstream.json` | Exact upstream version and source checksum |

This is a personal modification of OpenAI Codex **0.153.4**, using Rust **1.95.0**. The repository stores the custom modules directly; the build script reconstructs the full upstream tree and copies these modules into it. Image data is embedded at compile time, with no runtime image downloads or video decoder.

## Build and install

Python 3.12+, Git, `just`, the pinned Rust toolchain, and the upstream macOS build prerequisites are required. Use the [pinned upstream source](https://github.com/openai/codex/tree/rust-v0.153.4) for its full development prerequisites. The installer targets Apple Silicon and reuses companion executables from an existing, exact-version Codex package.

```sh
python3 scripts/prepare-source.py
cd build/codex-rust-v0.153.4/codex-rs
env -u NO_COLOR TERM_PROGRAM=Apple_Terminal just test -p codex-tui --cargo-profile dev-small
just fix -p codex-tui --profile dev-small --allow-staged
just fmt
cd ../../..
# Preserve any formatter/fix edits in src/ before syncing again.
./scripts/build.sh
python3 scripts/install-terminal.py --apply --activate-profile
python3 scripts/install.py --replace --apply
```

Interactive builds use the optimized `release` profile. The installer selects that same artifact and respects `CARGO_TARGET_DIR`. For development only, set `CODEX_NOIR_BUILD_PROFILE=dev-small` for both build and install; that profile disables compiler optimization and is slower during animation. The scene caches its current frame between animation ticks, so typing and status redraws do not resample the photograph or advance its colors early.

Both installers preview by default when `--apply` is omitted. Existing custom packages, launchers, and shell configuration are backed up before replacement. The Terminal installer imports a new profile, selects it for new windows, and applies it to tabs already using Overdrive Noir or Noir Velocity. Open a new shell or run `source ~/.config/zsh/noir.zsh` to load the new prompt in an existing tab.

The Terminal installer copies the wallpaper to `~/.config/terminal/backgrounds/` and creates its native file bookmark using macOS Foundation. The portable profile in this repository contains no machine-specific image bookmark; install it through the script to include the wallpaper.

To keep the source and Cargo caches elsewhere, substitute your cache directory below. Load any local toolchain environment before running these commands:

```sh
noir_cache=/path/to/codex-noir-cache
export CARGO_TARGET_DIR="$noir_cache/target"
./scripts/build.sh \
  --checkout "$noir_cache/codex-rust-v0.153.4" \
  --archive "$noir_cache/codex-rust-v0.153.4.tar.gz" \
  --adopt-existing
python3 scripts/install.py --replace --apply \
  --binary "$CARGO_TARGET_DIR/release/codex" \
  --source-root "$noir_cache/codex-rust-v0.153.4" \
  --source-archive "$noir_cache/codex-rust-v0.153.4.tar.gz"
```

Review and test source edits before reinstalling. The preparer checks the archive checksum and integration patch, and refuses to overwrite diverging edits made directly in an existing checkout. Copy intentional checkout edits back into `src/` or `assets/` first. It removes obsolete modules, snapshots, and assets only when they still match the previous sync, and leaves unchanged files untouched.

When updating the integration patch, keep a copy of the patch that prepared your checkout. Pass `--previous-patch /path/to/previous-integration.patch` to `scripts/prepare-source.py` or `scripts/build.sh` to migrate that checkout. The preparer validates local edits before migration and restores the previous integration if the replacement patch cannot apply. Without the previous patch, choose a new `--checkout` directory; existing checkouts are never reset automatically.

The installed package is `~/.local/share/codex-noir/0.153.4`; the launcher is `~/.local/bin/codex-noir`. Each package includes its editable source bundle and provenance hashes. To roll back, move aside the current package and launcher and restore their matching timestamped `.backup-*` siblings. Shell/profile backup mappings are under `~/.config/terminal/backups/noir-velocity-*/files.json`.

## Development

Edit the canonical renderer modules in `src/`; source preparation syncs them into the pinned upstream checkout. Keep artwork inside its decorative region and preserve draft text, cursor position, popups, terminal color capabilities, and motion-off behavior. Follow the upstream contribution instructions for Rust changes and use the test, lint, and format steps above.

Local `AGENTS.md` and `CLAUDE.md` files are optional, ignored contributor instructions. They are not required to build or package the project. The optional Claude peer reads this README and any local Claude instructions:

```sh
python3 scripts/claude-peer.py 'Review the scene design and suggest one improvement.'
python3 scripts/claude-peer.py --edit 'Refine the renderer in src/; keep input and motion controls intact.'
```

The script uses your installed, logged-in Claude Code and resumes a dedicated conversation. Its default tools allow repository reading; `--edit` additionally allows file edits. Shell execution and external MCP tools are not enabled by the bridge. Claude's response is printed for Codex or you to review. Prompts passed over stdin are also supported. Session state and results stay in ignored `.agents-local/`; use `--new` for a fresh conversation.

Coordinate file ownership when multiple contributors work at once, especially around renderer sources and the prepared upstream checkout.

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

For a clip, use `--name flight --title Flight --video --start 0 --duration 4 --fps 12`. The converter generates a JPEG preview, metadata, and a bounded `.nrf` grayscale sequence. It uses one contrast curve across a clip and blends the loop transition. Rebuild after changing assets. The bundled photo selectors are `coast`, `flight`, and `transit`; additional names need a renderer entry. `moire` needs no source image, and `dither` reuses the Coast photograph.

## Verification and history

Run the regression tests with `python3 -m unittest discover -s scripts -p 'test_*.py'`. Source preparation checks cover checksum validation, checkout creation and adoption, rename migration, local-edit protection, bounded cleanup, and integration rollback. Packaging checks cover builds without local contributor documents and preserve the bundled license and attribution. These checks use temporary fixtures. Workspace tests and native provider verification live in the standalone Noir project.

For Python lint and formatting checks:

```sh
python3 -m venv .venv
.venv/bin/pip install -r scripts/requirements-dev.txt
.venv/bin/ruff check scripts
.venv/bin/ruff format --check scripts
```

The preview harness runs the actual CLI in a pseudo-terminal against an inert local streaming fixture. It does not use your authentication or call a model API. Install `scripts/requirements-preview.txt`, then run:

```sh
python3 scripts/preview-terminal.py ~/.local/share/codex-noir/0.153.4/bin/codex \
  --scenes coast moire flight transit --ansi256
```

Use the packaged executable so its companion tools are present. Captures and machine-readable checks are written under ignored `output/terminal-preview/`. The preview renders captured ANSI cells with local fonts and composites the wallpaper underneath.

Measure keyboard echo, terminal output volume, and process CPU time against the same local fixture with:

```sh
.venv/bin/python scripts/benchmark-terminal.py ~/.local/share/codex-noir/0.153.4/bin/codex
.venv/bin/python scripts/benchmark-terminal.py ~/.local/share/codex-noir/0.153.4/bin/codex \
  --ansi256 --output output/terminal-benchmark-ansi256
.venv/bin/python scripts/benchmark-terminal.py ~/.local/share/codex-noir/0.153.4/bin/codex \
  --stream --ansi256 --cases cruise warp --output output/terminal-benchmark-streaming
.venv/bin/python scripts/benchmark-terminal.py ~/.local/share/codex-noir/0.153.4/bin/codex \
  --stream --effort ultra --cases warp motion-off --output output/terminal-benchmark-ultra
```

The benchmark covers Cruise, Overdrive, Warp, scene-off and motion-off. `--stream` adds a 500-line response followed by continuing updates. `--effort` with `xhigh`, `max`, or `ultra` also verifies the moving violet label and its removal when work ends; `--light` checks a light terminal background. It verifies draft and cursor preservation while working and a still picture after completion (streaming) or interruption (held response). Its latency measurements cover the CLI and an emulated terminal, not a native terminal application's display time; CPU time includes startup and shutdown. Run comparisons without a competing build.

The recorded comparison is summarized under [Performance](#performance). Raw captures and measurements are kept locally under ignored `output/lag-fix/`.

The 2026-09-07 performance change passed all 26 Noir tests, including cached-versus-direct painting across scene, style, drive, geometry and color changes; draft/cursor preservation; animation lifecycle; parser limits; and the reviewed visual snapshots. The scoped Clippy fix, formatting, and optimized release CLI build completed successfully. All 34 Python project tests also passed, including build/installer profile and target-directory agreement.

Earlier renderer validation passed eight actual CLI capture scenarios: Coast, Moire, and Flight at 112×30 in 256 colors; Coast with the ASCII style; Coast at 80×24 in truecolor; a 40×18 terminal that hides the image; and scene-off and motion-off settings. Each preserved the draft and stopped decorative movement while idle. The captures used an isolated local fixture, with no model request or change to the user's settings. Max and Ultra rail checks also passed.

The full TUI suite in a macOS Terminal environment recorded 4,086 passed (one passed on retry), six skipped, and 31 snapshot failures. The same 31 failed before the performance change: 28 expect the development version `0.0.0` instead of the pinned release `0.153.4`, and three expect an Option–Up hint where upstream selects Shift–Left for Apple Terminal. The real-binary reconnect and immediate-input checks passed. These unrelated snapshots are left unchanged. Run with `NO_COLOR` unset as shown above; it otherwise suppresses ANSI sequences expected by four cursor tests. Re-run the checks for new Rust changes instead of treating these historical results as current validation.

The warp-bar update passed all 28 Noir tests and 14 Claude companion tests, with the new violet activity snapshot visually reviewed. Live Codex fixture checks covered `xhigh`, `max`, `ultra`, light and dark backgrounds, 256 colors, and motion-off; a private tmux check verified the Claude bar, permission pauses, and unchanged draft/cursor state. Its full TUI run reported the same 31 baseline snapshot failures plus the updated activity snapshot and one paste-timing check. Both additional checks passed in the focused follow-up; scoped Clippy and formatting completed successfully.

The earlier desktop app prototype is preserved at the [`app-prototype`](https://github.com/Madhavan113/customcodexterminal/tree/app-prototype) tag. Main now develops the ordinary terminal setup.

## Upstream and asset credits

Codex Noir is a personal modification of OpenAI Codex 0.153.4. The full Apache-2.0 license and attribution are retained in [licenses/codex.txt](licenses/codex.txt) and included in the installed source bundle. Photographs and footage have separate source terms recorded in [assets/SOURCES.md](assets/SOURCES.md).
