# Codex Noir: terminal-only working context

The user's latest direction is authoritative: customize the real terminal. Build no desktop application, web wrapper, canvas overlay, AppKit window, or xterm frontend. The earlier Dragon Terminal prototype is preserved at the `app-prototype` Git tag and locally under ignored `output/app-prototype/`.

## What this project is

A personal terminal setup: a macOS Terminal profile, a zsh prompt, and a patched version of the real Codex Rust TUI. Start it with `codex-noir` inside an ordinary terminal. Keep the stock `codex` command, authentication, settings, model selection, permissions, sessions, input handling, and tool execution intact.

The project is based on OpenAI Codex 0.153.4. **Edit the canonical renderer files in `src/` in this repository.** `scripts/prepare-source.py` copies them into the pinned upstream checkout for building. The existing warm checkout/cache are `/private/tmp/codex-noir-build/codex-rust-v0.153.4/` and `/private/tmp/codex-noir-build/target/`; a fresh checkout can also be generated under `build/`. Read the upstream `AGENTS.md`, `codex-rs/tui/styles.md`, and `codex-rs/tui/src/bottom_pane/AGENTS.md` before Rust work. Avoid editing generated checkouts directly unless the current task explicitly assigns that path.

## Terminal renderer

Within the upstream checkout:

- `codex-rs/tui/src/bottom_pane/chat_composer/noir_dragon.rs` wraps the existing composer in a separately reserved decorative region. The old private names remain for integration compatibility; the new content is photographic ASCII.
- `noir_dragon_tests.rs` beside it covers layout, cursor/draft safety, animation lifecycle, and snapshots.
- `noir_photo.rs` parses and samples bounded grayscale frames; its tests are in `noir_photo_tests.rs`. All `noir_*.rs` modules have matching editable files under this repo's `src/`.
- `noir_activity.rs` and its tests paint the existing activity rail in an unused composer border. Effort labels reflect actual settings.
- `chat_composer.rs` and `bottom_pane/mod.rs` contain the existing integration. Keep edits out of their input/paste state machines.
- `chatwidget/rendering.rs` composes the active transcript with the bottom pane at zero flex; its existing wrapper geometry gives input priority when the scene cannot fit. The render implementation is in this submodule, not the large `chatwidget.rs` file.
- `codex-rs/tui/assets/noir/` holds embedded luminance frames. The existing `assets/**` Bazel compile-data glob covers them.

The scene must yield space to typed input, vanish for popups or insufficient geometry, keep buffer accesses bounded, and never overwrite nonblank content. Animation advances only during visible work; its clock resets while idle or hidden. `-c tui.animations=false`, `CODEX_NOIR_SCENE=off`, and the legacy `CODEX_NOIR_DRAGON=0` must disable it. Respect terminal color capabilities and both dark and light backgrounds. The renderer precomputes 16 tones per frame to avoid a full 256-color search for every image cell.

## Visual direction

Reference: https://madhavanprasanna.com/. Its real birds-in-flight MP4 becomes a fine textured image grid. Bring that sense of velocity into terminal glyphs: continuous motion, photographic forms, controlled grain, negative space, restrained color. Keep normal code and conversation highly legible.

Use actual photographs or sampled footage as glyph source data. No generated AI imagery, cartoon dragon, fake telemetry, arbitrary glitch spam, or ornamental rainbow effects. Flight uses frames from the user's website; Transit uses Mario Calvo's long-exposure subway photograph. Sources are recorded in `assets/SOURCES.md`.

The `.nrf` format is grayscale source data, not prebuilt terminal escape sequences. A 16-byte little-endian header contains magic `NOIR`, version u16, width u16, height u16, FPS u16 (zero for stills), and frame count u32, followed by row-major u8 pixels for every frame. The renderer alone emits safe display glyphs. Current inputs are 160×90; Flight has 41 frames at 12 FPS and Transit has one frame.

Parser limits are 256×144, 256 frames, and 24 FPS. A zero-FPS still has exactly one frame. Keep parser and converter limits consistent when changing the asset pipeline.

## Collaboration

Codex coordinates assets, shell/profile changes, durable packaging, builds, QA, Git, and installation. Claude handles the bounded renderer/design task assigned in its current prompt. Do not change the other agent's files, run concurrent Cargo commands, commit, push, install, or launch additional agents unless assigned explicitly.

Codex runs upstream-required `just test -p codex-tui`, reviews relevant snapshot changes, runs `just fix -p codex-tui` for substantial Rust changes, and finishes with `just fmt`. Use `just test`, not direct `cargo test`. Source the existing build environment at `/private/tmp/codex-noir-build/env.sh`; the reusable Cargo profile is `dev-small`.

Prompts, session IDs, transcripts, and local captures stay in ignored `output/` or `.agents-local/`. Put reusable codebase knowledge here. Report files changed, design decisions, and any unresolved concerns honestly.
