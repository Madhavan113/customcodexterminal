# Working on Codex Noir

Read `CLAUDE.md` and `README.md` for shared project context. The user's latest instructions take precedence. This project customizes the ordinary terminal and the actual Codex TUI.

- Edit renderer modules in `src/`; `scripts/prepare-source.py` syncs them into the pinned upstream checkout. Preserve deliberate checkout edits before syncing.
- Follow the upstream checkout's `AGENTS.md` for Rust changes. Run its project tests with `just test -p codex-tui`, review the relevant visual snapshots, and finish with the required lint/format steps.
- Keep image work inside the decorative region; preserve draft text, cursor position, popups, terminal capabilities, and motion-off behavior.
- Use sourced photos or footage. Record credits and conversion settings in `assets/`.
- Keep agent conversations, local credentials, build caches, and UI captures out of Git. Use `.agents-local/` or `output/`.
- Coordinate ownership before concurrent edits. Claude's design task and Codex's integration task should operate on different files.
