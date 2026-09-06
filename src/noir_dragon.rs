//! A photographic ASCII study in its own reserved rows above the composer.
//!
//! Real luminance data drives every glyph: Flight loops frames of birds taking
//! off from the user's homepage footage, and Transit drifts across Mario
//! Calvo's long-exposure subway still. The picture sits to the right of a quiet
//! label so the negative space reads as deliberate. It only moves while a task
//! is running; idle, it holds a dimmer first frame and schedules nothing.
//!
//! Typed input keeps priority. The scene shrinks or vanishes when the terminal
//! is narrow or short, for popups, disabled input, `tui.animations=false`, and
//! terminals without 256-color support. `CODEX_NOIR_SCENE=flight|transit|off`
//! selects the study; the legacy `CODEX_NOIR_DRAGON=0` still disables it.

use std::cell::Cell;
use std::f32::consts::TAU;
use std::time::Duration;
use std::time::Instant;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

use super::ChatComposer;
use super::popup_state::ActivePopup;
use crate::color::blend;
use crate::color::is_light;
use crate::render::renderable::Renderable;
use crate::render::renderable::RenderableItem;
use crate::terminal_palette::StdoutColorLevel;
use crate::terminal_palette::best_color_for_level;
use crate::terminal_palette::default_bg;
use crate::terminal_palette::effective_stdout_color_level;

#[path = "noir_photo.rs"]
mod noir_photo;

use noir_photo::Study;

const MIN_WIDTH: u16 = 44;
const MIN_ROWS: u16 = 5;
/// Cells are about twice as tall as they are wide, so a 16:9 frame at true aspect spans 3.6
/// columns per row. Widening to 5.2 columns with a mild vertical squeeze and a centered crop
/// keeps the picture recognizable while leaving the left of the scene to the label.
const COLUMNS_PER_ROW: f32 = 5.2;
const SQUEEZE: f32 = 1.25;
const STILL_TICK: Duration = Duration::from_millis(100);
const RAMP: &[u8] = b" .:-=+*#%@";
const GRAIN: f32 = 0.09;
const BAYER: [[u8; 4]; 4] = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];
const SLIP_CYCLE: Duration = Duration::from_millis(4300);
const SLIP_HOLD: Duration = Duration::from_millis(250);

/// Which photographic study occupies the scene.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Scene {
    Flight,
    Transit,
}

impl Scene {
    /// `CODEX_NOIR_SCENE` picks the study and unknown values keep Flight. Either it or the
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
            Some("transit") => Self::Transit,
            _ => Self::Flight,
        })
    }

    fn study(self) -> Option<&'static Study<'static>> {
        match self {
            Self::Flight => noir_photo::FLIGHT.as_ref(),
            Self::Transit => noir_photo::TRANSIT.as_ref(),
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Flight => "F L I G H T",
            Self::Transit => "T R A N S I T",
        }
    }

    fn credit(self) -> &'static str {
        match self {
            Self::Flight => "madhavanprasanna.com",
            Self::Transit => "photo by mario calvo",
        }
    }
}

/// Whether the scene is playing, and for how long the current task has run.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Motion {
    Idle,
    Working(Duration),
}

pub(super) struct NoirDragon {
    scene: Option<Scene>,
    started_at: Cell<Option<Instant>>,
}

impl Default for NoirDragon {
    fn default() -> Self {
        Self::from_preferences(
            std::env::var("CODEX_NOIR_SCENE").ok().as_deref(),
            std::env::var("CODEX_NOIR_DRAGON").ok().as_deref(),
        )
    }
}

impl NoirDragon {
    fn from_preferences(scene: Option<&str>, legacy: Option<&str>) -> Self {
        Self {
            scene: Scene::from_preferences(scene, legacy),
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
struct Appearance {
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
    fn scene(&self) -> Option<(Scene, &'static Study<'static>)> {
        let scene = self.composer.noir_dragon.scene?;
        Some((scene, scene.study()?))
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
            && let Some((scene, study)) = self.scene()
        {
            paint(scene, motion, picture, buf, appearance);
            if working && let Some(requester) = &self.composer.frame_requester {
                requester.schedule_frame_in(study.frame_interval().unwrap_or(STILL_TICK));
            }
        }
        self.inner.render(content, buf);
    }
}

/// Paints the label into the negative space and the photograph into a right-aligned panel.
/// Only blank cells inside `area` change, so drafts, selections, and popups are never touched.
fn paint(scene: Scene, motion: Motion, area: Rect, buf: &mut Buffer, appearance: Appearance) {
    let area = area.intersection(buf.area);
    let Some(study) = scene.study() else {
        return;
    };
    if area.width < MIN_WIDTH || area.height < MIN_ROWS {
        return;
    }
    let light = is_light(appearance.background);
    let (base, accent) = if light {
        ((56, 48, 42), (0, 105, 122))
    } else {
        ((236, 228, 214), (138, 246, 255))
    };
    let presence = match motion {
        Motion::Idle => 0.6,
        Motion::Working(_) => 1.0,
    };
    let panel_width =
        ((f32::from(area.height) * COLUMNS_PER_ROW).round() as u16).min(area.width - 4);
    let panel = Rect::new(
        area.right() - 2 - panel_width,
        area.y,
        panel_width,
        area.height,
    );

    let free = usize::from(panel.x.saturating_sub(area.x).saturating_sub(3));
    for (row, (text, strength)) in [(scene.label(), 0.55), (scene.credit(), 0.30)]
        .into_iter()
        .enumerate()
    {
        if text.len() > free || (row > 0 && area.height < 8) {
            break;
        }
        let color = best_color_for_level(
            blend(base, appearance.background, strength * presence),
            appearance.color_level,
        );
        for (column, glyph) in text.chars().enumerate() {
            let cell = &mut buf[(area.x + 2 + column as u16, area.y + row as u16)];
            if glyph != ' ' && cell.symbol() == " " {
                cell.set_char(glyph).set_fg(color);
            }
        }
    }

    let elapsed = match motion {
        Motion::Idle => None,
        Motion::Working(elapsed) => Some(elapsed),
    };
    let seconds = elapsed.as_ref().map_or(0.0, Duration::as_secs_f32);
    let frame = elapsed.map_or(0, |elapsed| study.frame_at(elapsed));
    let still = study.frame_interval().is_none();
    let source_width = study.width as f32;
    let source_height = study.height as f32;
    // A still glides sideways across a slightly narrower crop; footage carries its own motion.
    let pan_margin = if still {
        (source_width * 0.045).ceil()
    } else {
        0.0
    };
    let pan = if elapsed.is_some() {
        pan_margin * (seconds * TAU / 16.0).sin()
    } else {
        0.0
    };
    // Grain drifts in the direction of travel, faster over the still than over footage.
    let grain_phase = (seconds * if still { 6.0 } else { 2.5 }) as usize;
    // A brief scan slip: two rows shift sideways for a quarter second every few seconds.
    let slip = elapsed.and_then(|elapsed| {
        let cycle = elapsed.as_millis() / SLIP_CYCLE.as_millis();
        let holding = elapsed.as_millis() % SLIP_CYCLE.as_millis() < SLIP_HOLD.as_millis();
        (cycle > 0 && holding).then(|| {
            let start = (cycle as usize * 3) % usize::from(area.height - 1);
            let shift = if cycle.is_multiple_of(2) { 1.5 } else { -1.5 };
            (start as u16, shift)
        })
    });

    let column_px = (source_width - 2.0 * pan_margin) / f32::from(panel.width);
    let crop_height = (2.0 * column_px * f32::from(panel.height) * SQUEEZE).min(source_height);
    let row_px = crop_height / f32::from(panel.height);
    let x_origin = pan_margin + pan;
    let y_origin = (source_height - crop_height) / 2.0;
    let steps = (RAMP.len() - 1) as f32;
    // Quantize color separately from glyph density. A small palette avoids
    // searching all 256 terminal colors for every image cell on every frame.
    let tones = std::array::from_fn::<_, 16, _>(|index| {
        let ink = index as f32 / 15.0;
        let highlight = ((ink - 0.85) / 0.15).clamp(0.0, 1.0) * 0.4;
        let tone = blend(accent, base, highlight);
        best_color_for_level(
            blend(tone, appearance.background, (0.28 + 0.72 * ink) * presence),
            appearance.color_level,
        )
    });
    for row in 0..panel.height {
        let slip_px = match slip {
            Some((start, shift)) if (start..=start + 1).contains(&row) => shift * column_px,
            _ => 0.0,
        };
        let y0 = y_origin + f32::from(row) * row_px;
        let y1 = y0 + row_px;
        for column in 0..panel.width {
            let cell = &mut buf[(panel.x + column, panel.y + row)];
            if cell.symbol() != " " {
                continue;
            }
            let x0 = x_origin + f32::from(column) * column_px + slip_px;
            let x1 = x0 + column_px;
            let luminance = study.mean_luminance(
                frame,
                x0.floor() as i32,
                y0.floor() as i32,
                x1.ceil() as i32,
                y1.ceil() as i32,
            );
            let contrast = ((luminance - 0.5) * 1.25 + 0.5).clamp(0.0, 1.0);
            let ink = if light { 1.0 - contrast } else { contrast };
            let grain = BAYER[usize::from(row) % 4][(usize::from(column) + grain_phase) % 4];
            let dither = (f32::from(grain) - 7.5) / 16.0 * GRAIN;
            let level = ((ink + dither) * steps).round().clamp(0.0, steps) as usize;
            if level == 0 {
                continue;
            }
            cell.set_char(RAMP[level] as char)
                .set_fg(tones[(ink * 15.0).round() as usize]);
        }
    }
}

#[cfg(test)]
#[path = "noir_dragon_tests.rs"]
mod tests;
