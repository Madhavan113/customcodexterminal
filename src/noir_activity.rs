//! A working-only accent in the unused row above the draft. No draft cells,
//! terminal selections, footer content, or popup content are modified.

use std::cell::Cell;
use std::time::Duration;
use std::time::Instant;

use codex_protocol::openai_models::ReasoningEffort;
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Color;
use ratatui::style::Modifier;

use super::ChatComposer;
use super::popup_state::ActivePopup;
use crate::color::blend;
use crate::color::is_light;
use crate::terminal_palette::StdoutColorLevel;
use crate::terminal_palette::best_color_for_level;
use crate::terminal_palette::default_bg;
use crate::terminal_palette::effective_stdout_color_level;

const FRAME_TICK: Duration = Duration::from_millis(50);

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
enum ActivityMode {
    #[default]
    Standard,
    Extra,
    Ultra,
}

#[derive(Default)]
pub(super) struct NoirActivity {
    pub(super) enabled: bool,
    working: bool,
    mode: ActivityMode,
    started_at: Cell<Option<Instant>>,
}

impl NoirActivity {
    pub(super) fn set_effort(&mut self, effort: Option<&ReasoningEffort>) {
        let mode = match effort {
            Some(ReasoningEffort::Ultra) => ActivityMode::Ultra,
            Some(ReasoningEffort::XHigh | ReasoningEffort::Max | ReasoningEffort::Persistent) => {
                ActivityMode::Extra
            }
            Some(
                ReasoningEffort::None
                | ReasoningEffort::Minimal
                | ReasoningEffort::Low
                | ReasoningEffort::Medium
                | ReasoningEffort::High
                | ReasoningEffort::Custom(_),
            )
            | None => ActivityMode::Standard,
        };
        if self.mode != mode {
            self.started_at.set(None);
            self.mode = mode;
        }
    }

    fn set_enabled(&mut self, enabled: bool) {
        if self.enabled != enabled {
            self.started_at.set(None);
        }
        self.enabled = enabled;
    }

    pub(super) fn set_working(&mut self, working: bool) {
        if self.working != working {
            self.started_at.set(None);
        }
        self.working = working;
    }

    fn elapsed_at(&self, now: Instant) -> Option<Duration> {
        if !self.enabled || !self.working {
            return None;
        }
        let started_at = self.started_at.get().unwrap_or_else(|| {
            self.started_at.set(Some(now));
            now
        });
        Some(now.saturating_duration_since(started_at))
    }
}

impl ChatComposer {
    pub(crate) fn set_noir_animations_enabled(&mut self, enabled: bool) {
        self.noir_activity.set_enabled(enabled);
    }

    pub(super) fn render_noir_activity(&self, composer: Rect, buf: &mut Buffer) {
        if !matches!(self.popups.active, ActivePopup::None)
            || !self.draft.input_enabled
            || composer.height < 3
            || composer.width < 8
            || self
                .effort_ignition
                .as_ref()
                .is_some_and(|ignition| !ignition.is_finished())
        {
            return;
        }
        let color_level = effective_stdout_color_level();
        if !matches!(
            color_level,
            StdoutColorLevel::TrueColor | StdoutColorLevel::Ansi256
        ) {
            return;
        }
        let Some(background) = default_bg() else {
            return;
        };
        let Some(elapsed) = self.noir_activity.elapsed_at(Instant::now()) else {
            return;
        };
        let rail = Rect::new(
            composer.x.saturating_add(2),
            composer.y,
            composer.width.saturating_sub(4),
            /*height*/ 1,
        );
        paint(
            self.noir_activity.mode,
            elapsed,
            rail,
            buf,
            background,
            color_level,
        );
        if let Some(frame_requester) = &self.frame_requester {
            frame_requester.schedule_frame_in(FRAME_TICK);
        }
    }
}

fn paint(
    mode: ActivityMode,
    elapsed: Duration,
    rail: Rect,
    buf: &mut Buffer,
    background: (u8, u8, u8),
    color_level: StdoutColorLevel,
) {
    let rail = rail.intersection(buf.area);
    if rail.is_empty() {
        return;
    }
    let light = is_light(background);
    let cyan = if light {
        (0, 105, 122)
    } else {
        (138, 246, 255)
    };
    // Keyboard redraws within a tick reuse the same animation phase. Quantize the small
    // palette once per paint, rather than searching all 256 terminal colors for every cell.
    let seconds = (elapsed.as_millis() / FRAME_TICK.as_millis()) as f64 * 0.05;
    let label = match mode {
        ActivityMode::Standard => " WORKING ",
        ActivityMode::Extra if rail.width >= 20 => " EXTRA THINKING ",
        ActivityMode::Extra => " THINKING ",
        ActivityMode::Ultra => " ULTRA ",
    };
    let period = match mode {
        ActivityMode::Standard | ActivityMode::Extra => 2.4,
        ActivityMode::Ultra => 1.6,
    };
    let sway = 0.5 + 0.5 * (std::f64::consts::TAU * seconds / period).sin();
    let label_start = if mode == ActivityMode::Standard {
        rail.width.saturating_sub(label.len() as u16) / 2
    } else {
        let travel = rail.width.saturating_sub(label.len() as u16 + 4);
        2 + (f64::from(travel) * sway).round() as u16
    };
    let violet = if light {
        (124, 58, 217)
    } else {
        (170, 106, 255)
    };
    let warp_palette: Option<[Color; 16]> = (mode != ActivityMode::Standard).then(|| {
        std::array::from_fn(|index| {
            best_color_for_level(
                blend(violet, background, 0.24 + 0.44 * index as f32 / 15.0),
                color_level,
            )
        })
    });
    let ink = best_color_for_level(violet, color_level);
    let text = best_color_for_level(
        if light {
            (60, 24, 110)
        } else {
            (248, 235, 255)
        },
        color_level,
    );
    for column in 0..rail.width {
        let position = f64::from(column) / f64::from(rail.width.max(2) - 1);
        let cell = &mut buf[(rail.x + column, rail.y)];
        if cell.symbol() != " " {
            continue;
        }
        let label_column = column.checked_sub(label_start);
        let letter = label_column
            .filter(|_| rail.width >= label.len() as u16 + 4)
            .and_then(|index| label.as_bytes().get(usize::from(index)));
        if let Some(palette) = &warp_palette {
            let beam = (-80.0 * (position - sway).powi(2)).exp();
            let ripple = (std::f64::consts::TAU * (position * 2.0 - seconds)).sin();
            let strength = 0.32 + 0.48 * beam + 0.20 * (0.5 + 0.5 * ripple);
            let shade = (strength * 15.0).round() as usize;
            cell.set_char(letter.map_or('━', |letter| char::from(*letter)))
                .set_bg(palette[shade.min(15)])
                .set_fg(if letter.is_some() { text } else { ink });
        } else {
            let distance = (position - (seconds / 2.4).fract()).abs();
            let strength = 0.20 + 0.80 * (-70.0 * distance * distance).exp();
            let strength = if letter.is_some() {
                strength.max(0.85)
            } else {
                strength
            };
            cell.set_char(letter.map_or('─', |letter| char::from(*letter)))
                .set_fg(best_color_for_level(
                    blend(cyan, background, strength as f32),
                    color_level,
                ));
        }
        if letter.is_some() {
            cell.modifier.insert(Modifier::BOLD);
        }
    }
}

#[cfg(test)]
#[path = "noir_activity_tests.rs"]
mod tests;
