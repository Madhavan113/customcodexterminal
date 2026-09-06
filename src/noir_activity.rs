//! A working-only accent in the unused row above the draft. No draft cells,
//! terminal selections, footer content, or popup content are modified.

use std::cell::Cell;
use std::time::Duration;
use std::time::Instant;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Modifier;

use super::ChatComposer;
use super::EffortTier;
use super::popup_state::ActivePopup;
use crate::color::blend;
use crate::color::is_light;
use crate::terminal_palette::StdoutColorLevel;
use crate::terminal_palette::best_color_for_level;
use crate::terminal_palette::default_bg;
use crate::terminal_palette::effective_stdout_color_level;

const FRAME_TICK: Duration = Duration::from_millis(50);

#[derive(Default)]
pub(super) struct NoirActivity {
    pub(super) enabled: bool,
    working: bool,
    started_at: Cell<Option<Instant>>,
}

impl NoirActivity {
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
            self.effort_tier,
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
    tier: Option<EffortTier>,
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
    // Max and Ultra reuse the prompt accent so the rail, prompt, and grayscale scene share hues.
    let light = is_light(background);
    let cyan = if light {
        (0, 105, 122)
    } else {
        (138, 246, 255)
    };
    let seconds = elapsed.as_secs_f64();
    let label = match tier {
        None => " WORKING ",
        Some(EffortTier::Max) => " MAX ",
        Some(EffortTier::Ultra) => " ULTRA ",
    };
    let label_start = rail.width.saturating_sub(label.len() as u16) / 2;
    for column in 0..rail.width {
        let position = f64::from(column) / f64::from(rail.width.max(2) - 1);
        let (hue, strength) = match tier {
            None => {
                let center = (seconds / 2.4).fract();
                let distance = (position - center).abs();
                (cyan, 0.20 + 0.80 * (-70.0 * distance * distance).exp())
            }
            Some(tier @ EffortTier::Max) => {
                let pulse = (std::f64::consts::TAU * (seconds / 2.8 - position * 0.22)).cos();
                (tier.accent_rgb(light), 0.28 + 0.72 * (0.5 + 0.5 * pulse))
            }
            Some(tier @ EffortTier::Ultra) => {
                let wave = (std::f64::consts::TAU * (position * 1.3 - seconds / 4.8)).sin();
                let glow = (std::f64::consts::TAU * (position * 0.7 + seconds / 3.2)).cos();
                (
                    blend(cyan, tier.accent_rgb(light), (0.5 + 0.5 * wave) as f32),
                    0.55 + 0.45 * (0.5 + 0.5 * glow),
                )
            }
        };
        let cell = &mut buf[(rail.x + column, rail.y)];
        if cell.symbol() != " " {
            continue;
        }
        let label_column = column.checked_sub(label_start);
        let letter = label_column
            .filter(|_| rail.width >= label.len() as u16 + 4)
            .and_then(|index| label.as_bytes().get(usize::from(index)));
        let symbol =
            letter.map_or_else(|| "─".to_string(), |letter| char::from(*letter).to_string());
        let strength = if letter.is_some() {
            strength.max(0.85)
        } else {
            strength
        };
        cell.set_symbol(&symbol).set_fg(best_color_for_level(
            blend(hue, background, strength as f32),
            color_level,
        ));
        if letter.is_some() {
            cell.modifier.insert(Modifier::BOLD);
        }
    }
}

#[cfg(test)]
#[path = "noir_activity_tests.rs"]
mod tests;
