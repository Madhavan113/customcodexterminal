//! A photographic study in its own reserved rows above the composer.
//!
//! Real luminance data drives every glyph. Dither, the default, is the
//! background study: a still of a rocky shoreline fills the whole band edge to
//! edge, ordered-dithered into foreground dots with no panel tint, while three
//! soft lights drift across it so the shore surfaces and sinks. Coast shows the
//! same photograph as a right-aligned halftone panel beside a quiet label;
//! Flight loops frames of birds taking off from the user's homepage footage;
//! Transit is Mario Calvo's long-exposure subway still. Moiré is the one
//! exception: a deterministic interference field rather than a photograph.
//! Every scene only moves while a task is running; idle, it holds a dimmer
//! first frame and schedules nothing.
//!
//! `CODEX_NOIR_STYLE=halftone|ascii|dither` chooses between the Braille
//! line-screen halftone, the original ASCII density ramp, and the Bayer ordered
//! dither in `noir_dither.rs`; unset, photographs use the halftone and Dither
//! uses the dither. Typed input keeps priority: the scene shrinks or vanishes
//! when the terminal is narrow or short, for popups, disabled input,
//! `tui.animations=false`, and terminals without 256-color support.
//! `CODEX_NOIR_SCENE=coast|flight|transit|moire|dither|off` selects the study
//! and the legacy `CODEX_NOIR_DRAGON=0` still disables it.
//!
//! The reasoning effort sets the [`Drive`]: Cruise at normal effort, Overdrive
//! at Max (hot magenta-to-amber colors, a fast sweeping beam, motion-blur
//! streaks), and Warp at Ultra (the picture smears away and `noir_warp.rs`
//! streams stars from a vanishing point in cyan, white, and acid). Idle frames
//! ignore the drive so a resting terminal always looks the same.

use std::cell::Cell;
use std::time::Duration;
use std::time::Instant;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

use super::ChatComposer;
use super::EffortTier;
use super::popup_state::ActivePopup;
use crate::render::renderable::Renderable;
use crate::render::renderable::RenderableItem;
use crate::terminal_palette::StdoutColorLevel;
use crate::terminal_palette::default_bg;
use crate::terminal_palette::effective_stdout_color_level;

#[path = "noir_dither.rs"]
mod noir_dither;
#[path = "noir_frame.rs"]
mod noir_frame;
#[path = "noir_halftone.rs"]
mod noir_halftone;
#[path = "noir_photo.rs"]
mod noir_photo;
#[path = "noir_warp.rs"]
mod noir_warp;

use noir_frame::FrameCache;
use noir_frame::FrameSpec;
use noir_halftone::Palette;
use noir_halftone::Shot;
use noir_photo::Study;

const MIN_WIDTH: u16 = 44;
const MIN_ROWS: u16 = 5;
/// Cells are about twice as tall as they are wide, so a 16:9 frame at true aspect spans 3.6
/// columns per row. Widening to 5.2 columns with a mild vertical squeeze and an anchored crop
/// keeps the picture recognizable while leaving the left of the scene to the label.
const COLUMNS_PER_ROW: f32 = 5.2;
const STILL_TICK: Duration = Duration::from_millis(100);

/// Which study occupies the scene.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Scene {
    Coast,
    Flight,
    Transit,
    Moire,
    Dither,
}

/// Where a scene's ink comes from: embedded luminance, the same luminance under drifting lights,
/// or the mathematical Moiré field.
#[derive(Clone, Copy)]
enum Source<'a> {
    Photo(&'a Study<'a>),
    Lit(&'a Study<'a>),
    Field,
}

impl Scene {
    /// `CODEX_NOIR_SCENE` picks the study and unknown values keep Dither. Either it or the
    /// legacy `CODEX_NOIR_DRAGON` switch turns the scene off with `0`, `false`, or `off`.
    fn from_preferences(scene: Option<&str>, legacy: Option<&str>) -> Option<Self> {
        let normalized = |value: Option<&str>| value.map(|value| value.trim().to_ascii_lowercase());
        let disabled =
            |value: &Option<String>| matches!(value.as_deref(), Some("0" | "false" | "off"));
        let scene = normalized(scene);
        if disabled(&scene) || disabled(&normalized(legacy)) {
            return None;
        }
        Some(match scene.as_deref() {
            Some("coast") => Self::Coast,
            Some("flight") => Self::Flight,
            Some("transit") => Self::Transit,
            Some("moire" | "moiré") => Self::Moire,
            _ => Self::Dither,
        })
    }

    fn source(self) -> Option<Source<'static>> {
        match self {
            Self::Coast => noir_photo::COAST.as_ref().map(Source::Photo),
            Self::Flight => noir_photo::FLIGHT.as_ref().map(Source::Photo),
            Self::Transit => noir_photo::TRANSIT.as_ref().map(Source::Photo),
            Self::Moire => Some(Source::Field),
            Self::Dither => noir_photo::COAST.as_ref().map(Source::Lit),
        }
    }

    /// Vertical anchor of the crop. The coastline keeps its rocks and horizon by leaning low; the
    /// wide Dither strip leans lower still so the horizon sits in its upper third.
    fn focus(self) -> f32 {
        match self {
            Self::Coast => 0.6,
            Self::Dither => 0.66,
            Self::Flight | Self::Transit | Self::Moire => 0.5,
        }
    }

    /// Whether the study fills the whole band edge to edge instead of a right-aligned panel.
    fn full_bleed(self) -> bool {
        match self {
            Self::Dither => true,
            Self::Coast | Self::Flight | Self::Transit | Self::Moire => false,
        }
    }

    /// The treatment used when `CODEX_NOIR_STYLE` is unset or unknown.
    fn default_style(self) -> Style {
        match self {
            Self::Dither => Style::Dither,
            Self::Coast | Self::Flight | Self::Transit | Self::Moire => Style::Halftone,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Coast => "C O A S T",
            Self::Flight => "F L I G H T",
            Self::Transit => "T R A N S I T",
            Self::Moire => "M O I R E",
            Self::Dither => "D I T H E R",
        }
    }

    fn credit(self) -> &'static str {
        match self {
            Self::Coast | Self::Dither => "photo by focal insight",
            Self::Flight => "madhavanprasanna.com",
            Self::Transit => "photo by mario calvo",
            Self::Moire => "interference field",
        }
    }
}

/// How ink becomes glyphs.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Style {
    Halftone,
    Ascii,
    Dither,
}

impl Style {
    /// `CODEX_NOIR_STYLE=ascii` restores the density ramp, `dither` selects the ordered dither,
    /// and `halftone` the line screen; anything else defers to the scene's default.
    fn from_preference(style: Option<&str>) -> Option<Self> {
        match style
            .map(|value| value.trim().to_ascii_lowercase())
            .as_deref()
        {
            Some("halftone") => Some(Self::Halftone),
            Some("ascii") => Some(Self::Ascii),
            Some("dither" | "bayer") => Some(Self::Dither),
            _ => None,
        }
    }
}

/// Whether the scene is playing, and for how long the current task has run.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Motion {
    Idle,
    Working(Duration),
}

/// How hard the scene pushes while working. Normal effort cruises, Max goes into overdrive,
/// and Ultra jumps to warp. `CODEX_NOIR_DRIVE=cruise|overdrive|warp` pins one level.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Drive {
    Cruise,
    Overdrive,
    Warp,
}

impl Drive {
    fn from_tier(tier: Option<EffortTier>) -> Self {
        match tier {
            None => Self::Cruise,
            Some(EffortTier::Max) => Self::Overdrive,
            Some(EffortTier::Ultra) => Self::Warp,
        }
    }

    fn from_preference(value: Option<&str>) -> Option<Self> {
        match value
            .map(|value| value.trim().to_ascii_lowercase())
            .as_deref()
        {
            Some("cruise" | "off") => Some(Self::Cruise),
            Some("overdrive" | "max") => Some(Self::Overdrive),
            Some("warp" | "warpspeed" | "ultra") => Some(Self::Warp),
            _ => None,
        }
    }

    /// Redraw interval while working; faster drives redraw faster.
    pub(super) fn tick(self) -> Duration {
        match self {
            Self::Cruise => Duration::from_millis(100),
            Self::Overdrive => Duration::from_millis(50),
            Self::Warp => Duration::from_millis(33),
        }
    }

    /// Seconds for the dither gradient to slide once along the band.
    pub(super) fn gradient_period(self) -> f32 {
        match self {
            Self::Cruise => 40.0,
            Self::Overdrive => 12.0,
            Self::Warp => 6.0,
        }
    }
}

/// The fastest redraw the host terminal is asked to keep up with while working.
///
/// `CODEX_NOIR_FPS=<1..=60>` caps how often the scene and the activity rail schedule
/// redraws. Drives and clips that already redraw more slowly keep their own cadence, and
/// nothing is capped while the variable is unset. `bin/codex-noir` sets a low cap in Electron
/// terminals, which repaint slowly and often without GPU acceleration.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(super) struct Cadence {
    floor: Option<Duration>,
}

impl Cadence {
    pub(super) fn from_env() -> Self {
        Self::from_preference(std::env::var("CODEX_NOIR_FPS").ok().as_deref())
    }

    pub(super) fn from_preference(fps: Option<&str>) -> Self {
        let floor = fps
            .and_then(|value| value.trim().parse::<u64>().ok())
            .filter(|fps| (1..=60).contains(fps))
            .map(|fps| Duration::from_millis(1000 / fps));
        Self { floor }
    }

    /// `tick`, unless it would redraw faster than the cap allows.
    pub(super) fn clamp(self, tick: Duration) -> Duration {
        match self.floor {
            Some(floor) => tick.max(floor),
            None => tick,
        }
    }
}

pub(super) struct NoirScene {
    scene: Option<Scene>,
    style: Option<Style>,
    drive: Option<Drive>,
    cadence: Cadence,
    started_at: Cell<Option<Instant>>,
    frames: FrameCache,
}

impl Default for NoirScene {
    fn default() -> Self {
        let mut scene = Self::from_preferences(
            std::env::var("CODEX_NOIR_SCENE").ok().as_deref(),
            std::env::var("CODEX_NOIR_DRAGON").ok().as_deref(),
            std::env::var("CODEX_NOIR_STYLE").ok().as_deref(),
        );
        scene.drive = Drive::from_preference(std::env::var("CODEX_NOIR_DRIVE").ok().as_deref());
        scene.cadence = Cadence::from_env();
        scene
    }
}

impl NoirScene {
    fn from_preferences(scene: Option<&str>, legacy: Option<&str>, style: Option<&str>) -> Self {
        Self {
            scene: Scene::from_preferences(scene, legacy),
            style: Style::from_preference(style),
            drive: None,
            cadence: Cadence::default(),
            started_at: Cell::new(None),
            frames: FrameCache::default(),
        }
    }

    /// The pinned drive, or the one the reasoning effort implies.
    fn drive_for(&self, tier: Option<EffortTier>) -> Drive {
        self.drive.unwrap_or_else(|| Drive::from_tier(tier))
    }

    /// The explicit style preference, or the scene's own default.
    fn style_for(&self, scene: Scene) -> Style {
        self.style.unwrap_or_else(|| scene.default_style())
    }

    /// The playback clock starts with visible work and resets while idle or
    /// hidden, so a later task never inherits a previous task's playback time.
    fn motion_at(&self, now: Instant, working: bool) -> Motion {
        if !working {
            self.started_at.set(None);
            return Motion::Idle;
        }
        let started_at = self.started_at.get().unwrap_or_else(|| {
            self.started_at.set(Some(now));
            now
        });
        Motion::Working(now.saturating_duration_since(started_at))
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) struct Appearance {
    background: (u8, u8, u8),
    color_level: StdoutColorLevel,
}

struct SceneComposer<'a> {
    composer: &'a ChatComposer,
    inner: RenderableItem<'a>,
    appearance: Option<Appearance>,
}

impl ChatComposer {
    pub(crate) fn with_noir_scene<'a>(&'a self, inner: RenderableItem<'a>) -> RenderableItem<'a> {
        let color_level = effective_stdout_color_level();
        let appearance = default_bg()
            .filter(|_| {
                matches!(
                    color_level,
                    StdoutColorLevel::TrueColor | StdoutColorLevel::Ansi256
                )
            })
            .map(|background| Appearance {
                background,
                color_level,
            });
        RenderableItem::Owned(Box::new(SceneComposer {
            composer: self,
            inner,
            appearance,
        }))
    }
}

fn rows_for_width(width: u16) -> u16 {
    if width >= 112 {
        10
    } else if width >= 80 {
        8
    } else {
        6
    }
}

impl SceneComposer<'_> {
    fn scene(&self) -> Option<(Scene, Source<'static>)> {
        let scene = self.composer.noir_scene.scene?;
        Some((scene, scene.source()?))
    }

    fn scene_height(&self, width: u16) -> u16 {
        if width >= MIN_WIDTH
            && self.appearance.is_some()
            && self.scene().is_some()
            && self.composer.noir_activity.enabled
            && self.composer.draft.input_enabled
            && matches!(self.composer.popups.active, ActivePopup::None)
        {
            rows_for_width(width)
        } else {
            0
        }
    }

    /// Splits the area into picture rows and the composer. Typed input wins: the picture only
    /// takes rows the composer does not need and disappears below `MIN_ROWS`.
    fn regions(&self, area: Rect) -> (Rect, Rect) {
        let wanted = self.scene_height(area.width);
        let spare = area
            .height
            .saturating_sub(self.inner.desired_height(area.width));
        let height = if wanted == 0 || spare < MIN_ROWS {
            0
        } else {
            wanted.min(spare)
        };
        (
            Rect::new(area.x, area.y, area.width, height),
            Rect::new(area.x, area.y + height, area.width, area.height - height),
        )
    }
}

impl Renderable for SceneComposer<'_> {
    fn desired_height(&self, width: u16) -> u16 {
        self.inner
            .desired_height(width)
            .saturating_add(self.scene_height(width))
    }

    fn cursor_pos(&self, area: Rect) -> Option<(u16, u16)> {
        self.inner.cursor_pos(self.regions(area).1)
    }

    fn cursor_style(&self, area: Rect) -> crossterm::cursor::SetCursorStyle {
        self.inner.cursor_style(self.regions(area).1)
    }

    fn render(&self, area: Rect, buf: &mut Buffer) {
        let (picture, content) = self.regions(area);
        let working = self.composer.is_task_running && !picture.is_empty();
        let motion = self.composer.noir_scene.motion_at(Instant::now(), working);
        if !picture.is_empty()
            && let Some(appearance) = self.appearance
            && let Some((scene, source)) = self.scene()
        {
            let style = self.composer.noir_scene.style_for(scene);
            let drive = self
                .composer
                .noir_scene
                .drive_for(self.composer.effort_tier);
            let tick = self.composer.noir_scene.cadence.clamp(
                match source {
                    Source::Photo(study) | Source::Lit(study) => {
                        study.frame_interval().unwrap_or(STILL_TICK)
                    }
                    Source::Field => STILL_TICK,
                }
                .min(drive.tick()),
            );
            self.composer.noir_scene.frames.render(
                FrameSpec {
                    scene,
                    style,
                    motion,
                    drive,
                    appearance,
                },
                tick,
                picture,
                buf,
            );
            if working && let Some(requester) = &self.composer.frame_requester {
                requester.schedule_frame_in(tick);
            }
        }
        self.inner.render(content, buf);
    }
}

/// Paints the label into the negative space and the study into a right-aligned panel, or, for a
/// full-bleed scene, the picture alone across the whole band. Only blank cells inside `area`
/// change, so drafts, selections, and popups are never touched.
fn paint(
    scene: Scene,
    style: Style,
    motion: Motion,
    drive: Drive,
    area: Rect,
    buf: &mut Buffer,
    appearance: Appearance,
) {
    let area = area.intersection(buf.area);
    let Some(source) = scene.source() else {
        return;
    };
    if area.width < MIN_WIDTH || area.height < MIN_ROWS {
        return;
    }
    // A resting terminal looks the same at every effort level.
    let (presence, drive) = match motion {
        Motion::Idle => (0.6, Drive::Cruise),
        Motion::Working(_) => (1.0, drive),
    };
    let palette = Palette::new(appearance, presence, drive);
    let panel = if scene.full_bleed() {
        area
    } else {
        let panel_width =
            ((f32::from(area.height) * COLUMNS_PER_ROW).round() as u16).min(area.width - 4);
        Rect::new(
            area.right() - 2 - panel_width,
            area.y,
            panel_width,
            area.height,
        )
    };

    // Full-bleed scenes carry no caption: the picture is the whole point.
    if !scene.full_bleed() {
        let free = usize::from(panel.x.saturating_sub(area.x).saturating_sub(3));
        for (row, (text, color)) in [
            (scene.label(), palette.label),
            (scene.credit(), palette.credit),
        ]
        .into_iter()
        .enumerate()
        {
            if text.len() > free || (row > 0 && area.height < 8) {
                break;
            }
            for (column, glyph) in text.chars().enumerate() {
                let cell = &mut buf[(area.x + 2 + column as u16, area.y + row as u16)];
                if glyph != ' ' && cell.symbol() == " " {
                    cell.set_char(glyph).set_fg(color);
                }
            }
        }
    }

    let shot = Shot::new(source, scene.focus(), panel, motion, palette.light, drive);
    // At warp the star stream owns the band; the picture only shows while it smears away.
    if drive == Drive::Warp && scene.full_bleed() {
        noir_warp::paint(&shot, panel, buf, &palette);
        if shot.seconds >= noir_halftone::WARP_JUMP {
            return;
        }
    }
    match style {
        Style::Halftone => noir_halftone::paint_braille(&shot, panel, buf, &palette),
        Style::Ascii => noir_halftone::paint_ascii(&shot, panel, buf, &palette),
        Style::Dither => noir_dither::paint_bayer(&shot, panel, buf, &palette),
    }
}

#[cfg(test)]
#[path = "noir_scene_tests.rs"]
mod tests;
