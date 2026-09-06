//! A photographic study in its own reserved rows above the composer.
//!
//! Real luminance data drives every glyph. Coast, the default, is a still of a
//! rocky shoreline; Flight loops frames of birds taking off from the user's
//! homepage footage; Transit is Mario Calvo's long-exposure subway still. Moiré
//! is the one exception: a deterministic interference field rather than a
//! photograph. The picture sits to the right of a quiet label so the negative
//! space reads as deliberate. It only moves while a task is running; idle, it
//! holds a dimmer first frame and schedules nothing.
//!
//! `CODEX_NOIR_STYLE=halftone|ascii` chooses between the Braille line-screen
//! halftone (default) and the original ASCII density ramp; both live in
//! `noir_halftone.rs`. Typed input keeps priority: the scene shrinks or
//! vanishes when the terminal is narrow or short, for popups, disabled input,
//! `tui.animations=false`, and terminals without 256-color support.
//! `CODEX_NOIR_SCENE=coast|flight|transit|moire|off` selects the study and the
//! legacy `CODEX_NOIR_DRAGON=0` still disables it.

use std::cell::Cell;
use std::time::Duration;
use std::time::Instant;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

use super::ChatComposer;
use super::popup_state::ActivePopup;
use crate::render::renderable::Renderable;
use crate::render::renderable::RenderableItem;
use crate::terminal_palette::StdoutColorLevel;
use crate::terminal_palette::default_bg;
use crate::terminal_palette::effective_stdout_color_level;

#[path = "noir_halftone.rs"]
mod noir_halftone;
#[path = "noir_photo.rs"]
mod noir_photo;

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
}

/// Where a scene's ink comes from: embedded luminance, or the mathematical Moiré field.
#[derive(Clone, Copy)]
enum Source<'a> {
    Photo(&'a Study<'a>),
    Field,
}

impl Scene {
    /// `CODEX_NOIR_SCENE` picks the study and unknown values keep Coast. Either it or the
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
            Some("flight") => Self::Flight,
            Some("transit") => Self::Transit,
            Some("moire" | "moiré") => Self::Moire,
            _ => Self::Coast,
        })
    }

    fn source(self) -> Option<Source<'static>> {
        match self {
            Self::Coast => noir_photo::COAST.as_ref().map(Source::Photo),
            Self::Flight => noir_photo::FLIGHT.as_ref().map(Source::Photo),
            Self::Transit => noir_photo::TRANSIT.as_ref().map(Source::Photo),
            Self::Moire => Some(Source::Field),
        }
    }

    /// Vertical anchor of the crop. The coastline keeps its rocks and horizon by leaning low.
    fn focus(self) -> f32 {
        match self {
            Self::Coast => 0.6,
            Self::Flight | Self::Transit | Self::Moire => 0.5,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Coast => "C O A S T",
            Self::Flight => "F L I G H T",
            Self::Transit => "T R A N S I T",
            Self::Moire => "M O I R E",
        }
    }

    fn credit(self) -> &'static str {
        match self {
            Self::Coast => "photo by focal insight",
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
}

impl Style {
    /// `CODEX_NOIR_STYLE=ascii` restores the density ramp; anything else is the halftone.
    fn from_preference(style: Option<&str>) -> Self {
        match style
            .map(|value| value.trim().to_ascii_lowercase())
            .as_deref()
        {
            Some("ascii") => Self::Ascii,
            _ => Self::Halftone,
        }
    }
}

/// Whether the scene is playing, and for how long the current task has run.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Motion {
    Idle,
    Working(Duration),
}

pub(super) struct NoirDragon {
    scene: Option<Scene>,
    style: Style,
    started_at: Cell<Option<Instant>>,
}

impl Default for NoirDragon {
    fn default() -> Self {
        Self::from_preferences(
            std::env::var("CODEX_NOIR_SCENE").ok().as_deref(),
            std::env::var("CODEX_NOIR_DRAGON").ok().as_deref(),
            std::env::var("CODEX_NOIR_STYLE").ok().as_deref(),
        )
    }
}

impl NoirDragon {
    fn from_preferences(scene: Option<&str>, legacy: Option<&str>, style: Option<&str>) -> Self {
        Self {
            scene: Scene::from_preferences(scene, legacy),
            style: Style::from_preference(style),
            started_at: Cell::new(None),
        }
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

#[derive(Clone, Copy)]
pub(super) struct Appearance {
    background: (u8, u8, u8),
    color_level: StdoutColorLevel,
}

struct DragonComposer<'a> {
    composer: &'a ChatComposer,
    inner: RenderableItem<'a>,
    appearance: Option<Appearance>,
}

impl ChatComposer {
    pub(crate) fn with_noir_dragon<'a>(&'a self, inner: RenderableItem<'a>) -> RenderableItem<'a> {
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
        RenderableItem::Owned(Box::new(DragonComposer {
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

impl DragonComposer<'_> {
    fn scene(&self) -> Option<(Scene, Source<'static>)> {
        let scene = self.composer.noir_dragon.scene?;
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

impl Renderable for DragonComposer<'_> {
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
        let motion = self.composer.noir_dragon.motion_at(Instant::now(), working);
        if !picture.is_empty()
            && let Some(appearance) = self.appearance
            && let Some((scene, source)) = self.scene()
        {
            let style = self.composer.noir_dragon.style;
            paint(scene, style, motion, picture, buf, appearance);
            if working && let Some(requester) = &self.composer.frame_requester {
                let tick = match source {
                    Source::Photo(study) => study.frame_interval().unwrap_or(STILL_TICK),
                    Source::Field => STILL_TICK,
                };
                requester.schedule_frame_in(tick);
            }
        }
        self.inner.render(content, buf);
    }
}

/// Paints the label into the negative space and the study into a right-aligned panel.
/// Only blank cells inside `area` change, so drafts, selections, and popups are never touched.
fn paint(
    scene: Scene,
    style: Style,
    motion: Motion,
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
    let presence = match motion {
        Motion::Idle => 0.6,
        Motion::Working(_) => 1.0,
    };
    let palette = Palette::new(appearance, presence);
    let panel_width =
        ((f32::from(area.height) * COLUMNS_PER_ROW).round() as u16).min(area.width - 4);
    let panel = Rect::new(
        area.right() - 2 - panel_width,
        area.y,
        panel_width,
        area.height,
    );

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

    let shot = Shot::new(source, scene.focus(), panel, motion, palette.light);
    match style {
        Style::Halftone => noir_halftone::paint_braille(&shot, panel, buf, &palette),
        Style::Ascii => noir_halftone::paint_ascii(&shot, panel, buf, &palette),
    }
}

#[cfg(test)]
#[path = "noir_dragon_tests.rs"]
mod tests;
