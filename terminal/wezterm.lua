-- Noir Velocity for WezTerm: the same palette as the macOS Terminal profile, the rainbow zsh
-- prompt (loaded by your login shell), and an animated GIF wallpaper, which Terminal.app cannot do.
-- Installed by scripts/install-wezterm.py to ~/.config/wezterm/wezterm.lua.
local wezterm = require("wezterm")
local config = wezterm.config_builder()
local act = wezterm.action

-- Tune these two lines freely.
local wallpaper = wezterm.home_dir .. "/.config/wezterm/noir-velocity.gif"
local dim = 0.25 -- 0 shows the wallpaper exactly as shot; higher darkens it under the text.

-- SF Mono ships inside Terminal.app rather than as a system font; point WezTerm at that folder.
config.font_dirs = { "/System/Applications/Utilities/Terminal.app/Contents/Resources/Fonts" }
config.font = wezterm.font_with_fallback({ "SF Mono", "Menlo" })
config.font_size = 12.0
config.line_height = 1.05

config.colors = {
	foreground = "#ECEAF5",
	background = "#141320",
	cursor_bg = "#FFE68F",
	cursor_fg = "#141320",
	cursor_border = "#FFE68F",
	selection_bg = "#4A3D78",
	selection_fg = "#FFFFFF",
	ansi = { "#2B2840", "#FF5C8A", "#5CE0A5", "#FFD75C", "#6E8DFF", "#C46BFF", "#4FDDFF", "#ECEAF5" },
	brights = { "#7E7A9C", "#FF8FB1", "#8DF1C6", "#FFE68F", "#96AEFF", "#D89DFF", "#8DECFF", "#FFFFFF" },
}
config.bold_brightens_ansi_colors = true
config.default_cursor_style = "BlinkingBar"

-- The wallpaper plays as a background layer; a translucent wash above it keeps text readable.
config.background = {
	{
		source = { File = wallpaper },
		width = "Cover",
		height = "Cover",
		horizontal_align = "Center",
		vertical_align = "Middle",
		repeat_x = "NoRepeat",
		repeat_y = "NoRepeat",
	},
	{
		source = { Color = "#141320" },
		width = "100%",
		height = "100%",
		opacity = dim,
	},
}
config.animation_fps = 24
config.max_fps = 60

config.hide_tab_bar_if_only_one_tab = true
config.use_fancy_tab_bar = false
config.window_padding = { left = 10, right = 10, top = 8, bottom = 6 }
config.initial_cols = 120
config.initial_rows = 36
config.window_close_confirmation = "NeverPrompt"
config.audible_bell = "Disabled"
config.native_macos_fullscreen_mode = true
config.check_for_updates = false

-- Mac-style clipboard and undo shortcuts. Control keys retain their Unix meanings.
config.keys = {
	{ key = "c", mods = "SUPER", action = act.CopyTo("Clipboard") },
	{ key = "v", mods = "SUPER", action = act.PasteFrom("Clipboard") },
	-- Ctrl-_ is the standard terminal undo sequence understood by zsh and Codex.
	{ key = "z", mods = "SUPER", action = act.SendString("\x1f") },
}

return config
