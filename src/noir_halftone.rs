//! Glyph screens for the Noir scene.
//!
//! [`Shot`] maps panel cells onto the cropped photograph (or the Moiré field),
//! applying contrast, the light-background inversion, the slow pan of stills,
//! and the brief scan slip. [`paint_braille`] is the default halftone: each
//! cell is a 2×4 Braille block thresholded through a vertical line screen, so
//! midtones become dense lavender stripes, shadows thin to single dots, and
//! bright rows carry pale acid-yellow bands. [`paint_ascii`] keeps the original
//! density ramp. [`Palette`] quantizes every color once per frame so 256-color
//! terminals never search the palette per dot.

use std::f32::consts::TAU;
use std::time::Duration;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Color;

use super::Appearance;
use super::Motion;
use super::Source;
use crate::color::blend;
use crate::color::is_light;
use crate::terminal_palette::best_color_for_level;

/// Vertical squeeze accepted so the wide panel keeps most of a 16:9 frame instead of a thin
/// letterbox strip.
const SQUEEZE: f32 = 1.25;
const SLIP_CYCLE: Duration = Duration::from_millis(4300);
const SLIP_HOLD: Duration = Duration::from_millis(250);
const RAMP: &[u8] = b" .:-=+*#%@";
const GRAIN: f32 = 0.09;
const BAYER: [[u8; 4]; 4] = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];
/// Threshold per Braille dot, indexed `[column][row]`. The left column fills first, so midtones
/// read as vertical stripes while every eighth of ink still adds exactly one dot.
const SCREEN: [[f32; 4]; 2] = [
    [0.0625, 0.3125, 0.1875, 0.4375],
    [0.8125, 0.5625, 0.9375, 0.6875],
];
const BRAILLE_BITS: [[u8; 4]; 2] = [[0x01, 0x02, 0x04, 0x40], [0x08, 0x10, 0x20, 0x80]];
/// A slow threshold wave rolling across the dots keeps a still breathing without ever turning a
/// black dot on or a white dot off.
const WAVE: f32 = 0.05;
const BAND_INK: f32 = 0.7;

/// Colors for one frame, quantized once for the terminal's color level.
pub(super) struct Palette {
    pub(super) light: bool,
    pub(super) tones: [Color; 16],
    pub(super) panel_bg: Color,
    pub(super) band_bg: Color,
    pub(super) band_fg: Color,
    pub(super) label: Color,
    pub(super) credit: Color,
}

impl Palette {
    /// Lavender rises to pale lavender with ink and tips into acid yellow at the top; on light
    /// backgrounds violet ink deepens instead and the accent turns olive so it still prints.
    pub(super) fn new(appearance: Appearance, presence: f32) -> Self {
        let bg = appearance.background;
        let light = is_light(bg);
        let (low, high, acid) = if light {
            ((78, 62, 128), (44, 34, 80), (108, 112, 40))
        } else {
            ((184, 170, 230), (222, 216, 244), (228, 236, 150))
        };
        let quantize = |rgb| best_color_for_level(rgb, appearance.color_level);
        let tones = std::array::from_fn::<_, 16, _>(|index| {
            let ink = index as f32 / 15.0;
            let hue = blend(high, low, ((ink - 0.45) / 0.55).clamp(0.0, 1.0));
            let hue = blend(acid, hue, ((ink - 0.8) / 0.2).clamp(0.0, 1.0) * 0.7);
            quantize(blend(hue, bg, (0.26 + 0.74 * ink) * presence))
        });
        Self {
            light,
            tones,
            panel_bg: quantize(blend(low, bg, 0.07 * presence)),
            band_bg: quantize(blend(acid, bg, 0.22 * presence)),
            band_fg: quantize(blend(acid, bg, 0.92 * presence)),
            label: quantize(blend(high, bg, 0.55 * presence)),
            credit: quantize(blend(low, bg, 0.32 * presence)),
        }
    }
}

/// One frame's view of a source: crop geometry, playback frame, and the motion-derived phases.
pub(super) struct Shot<'a> {
    source: Source<'a>,
    frame: usize,
    x_origin: f32,
    column_px: f32,
    y_origin: f32,
    row_px: f32,
    slip: Option<(u16, f32)>,
    light: bool,
    pub(super) seconds: f32,
    pub(super) grain_phase: usize,
    pub(super) band_phase: usize,
}

impl<'a> Shot<'a> {
    /// `focus` anchors the vertical crop: 0.5 centers it, larger values keep more of the bottom.
    pub(super) fn new(
        source: Source<'a>,
        focus: f32,
        panel: Rect,
        motion: Motion,
        light: bool,
    ) -> Self {
        let elapsed = match motion {
            Motion::Idle => None,
            Motion::Working(elapsed) => Some(elapsed),
        };
        let seconds = elapsed.as_ref().map_or(0.0, Duration::as_secs_f32);
        let (frame, source_width, source_height, still) = match source {
            Source::Photo(study) => (
                elapsed.map_or(0, |elapsed| study.frame_at(elapsed)),
                study.width as f32,
                study.height as f32,
                study.frame_interval().is_none(),
            ),
            Source::Field => (0, f32::from(panel.width), f32::from(panel.height), true),
        };
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
        // A brief scan slip: two rows shift sideways for a quarter second every few seconds.
        let slip = elapsed.and_then(|elapsed| {
            let cycle = elapsed.as_millis() / SLIP_CYCLE.as_millis();
            let holding = elapsed.as_millis() % SLIP_CYCLE.as_millis() < SLIP_HOLD.as_millis();
            (cycle > 0 && holding).then(|| {
                let start = (cycle as usize * 3) % usize::from(panel.height.max(2) - 1);
                let shift = if cycle.is_multiple_of(2) { 1.5 } else { -1.5 };
                (start as u16, shift)
            })
        });
        let column_px = (source_width - 2.0 * pan_margin) / f32::from(panel.width.max(1));
        let crop_height = (2.0 * column_px * f32::from(panel.height) * SQUEEZE).min(source_height);
        Self {
            source,
            frame,
            x_origin: pan_margin + pan,
            column_px,
            y_origin: (source_height - crop_height) * focus,
            row_px: crop_height / f32::from(panel.height.max(1)),
            slip,
            light,
            seconds,
            grain_phase: (seconds * if still { 6.0 } else { 2.5 }) as usize,
            band_phase: if elapsed.is_some() {
                (seconds / 2.5) as usize % 4
            } else {
                1
            },
        }
    }

    /// Ink in `0.0..=1.0` for the block `[x, x + w) × [y, y + h)` measured in panel cells.
    /// On dark backgrounds ink follows brightness; on light backgrounds it follows darkness.
    pub(super) fn ink(&self, x: f32, y: f32, w: f32, h: f32) -> f32 {
        let slip = match self.slip {
            Some((start, shift)) if (f32::from(start)..f32::from(start) + 2.0).contains(&y) => {
                shift
            }
            _ => 0.0,
        };
        let luminance = match self.source {
            Source::Photo(study) => {
                let x0 = self.x_origin + (x + slip) * self.column_px;
                let y0 = self.y_origin + y * self.row_px;
                study.mean_luminance(
                    self.frame,
                    x0.floor() as i32,
                    y0.floor() as i32,
                    (x0 + w * self.column_px).ceil() as i32,
                    (y0 + h * self.row_px).ceil() as i32,
                )
            }
            Source::Field => field(
                (x + slip + w / 2.0) * 2.0,
                (y + h / 2.0) * 4.0,
                self.seconds,
            ),
        };
        let contrast = ((luminance - 0.5) * 1.25 + 0.5).clamp(0.0, 1.0);
        if self.light { 1.0 - contrast } else { contrast }
    }
}

/// A deterministic interference field in Braille-dot units: a warped soft checker beats against
/// a second lattice at a slightly different pitch and rotation, so the two read as moiré with
/// rounded-square islands. Time only slides the lattices; nothing here is random.
pub(super) fn field(u: f32, v: f32, seconds: f32) -> f32 {
    let soft = |value: f32| ((value + 0.25) / 0.5).clamp(0.0, 1.0);
    let warp_u = u + 2.5 * (v * 0.16 + seconds * 0.2).sin();
    let warp_v = v + 2.5 * (u * 0.16 - seconds * 0.15).sin();
    let checker = soft((warp_u * 0.52).sin() * (warp_v * 0.52).sin());
    let (sin_t, cos_t) = 0.12f32.sin_cos();
    let ru = u * cos_t - v * sin_t + seconds * 0.9;
    let rv = u * sin_t + v * cos_t;
    let lattice = soft((ru * 0.56).sin() * (rv * 0.56).sin());
    0.15 + 0.7 * (0.5 * checker + 0.5 * lattice)
}

/// The Braille line-screen halftone. Every panel cell gets the faint panel background; cells
/// with ink get a Braille block, and bright cells on band rows print in acid yellow. Cells that
/// already hold a symbol are skipped entirely.
pub(super) fn paint_braille(shot: &Shot<'_>, panel: Rect, buf: &mut Buffer, palette: &Palette) {
    for row in 0..panel.height {
        let band_row = (usize::from(row) + shot.band_phase) % 4 == 1;
        for column in 0..panel.width {
            let cell = &mut buf[(panel.x + column, panel.y + row)];
            if cell.symbol() != " " {
                continue;
            }
            let mut bits = 0u8;
            let mut total = 0.0;
            for (sub_x, thresholds) in SCREEN.iter().enumerate() {
                let dot = usize::from(column) * 2 + sub_x;
                let wave = WAVE * (TAU * (dot as f32 / 28.0 - shot.seconds / 7.0)).sin();
                for (sub_y, threshold) in thresholds.iter().enumerate() {
                    let ink = shot.ink(
                        f32::from(column) + sub_x as f32 * 0.5,
                        f32::from(row) + sub_y as f32 * 0.25,
                        0.5,
                        0.25,
                    );
                    total += ink;
                    if ink > threshold + wave {
                        bits |= BRAILLE_BITS[sub_x][sub_y];
                    }
                }
            }
            let mean = total / 8.0;
            let band = band_row && mean >= BAND_INK;
            cell.set_bg(if band {
                palette.band_bg
            } else {
                palette.panel_bg
            });
            if bits != 0 {
                let glyph = char::from_u32(0x2800 | u32::from(bits)).unwrap_or(' ');
                let fg = if band {
                    palette.band_fg
                } else {
                    palette.tones[(mean * 15.0).round() as usize]
                };
                cell.set_char(glyph).set_fg(fg);
            }
        }
    }
}

/// The original ASCII density ramp with drifting Bayer grain. Foreground only, blank cells stay
/// blank, and occupied cells are skipped.
pub(super) fn paint_ascii(shot: &Shot<'_>, panel: Rect, buf: &mut Buffer, palette: &Palette) {
    let steps = (RAMP.len() - 1) as f32;
    for row in 0..panel.height {
        for column in 0..panel.width {
            let cell = &mut buf[(panel.x + column, panel.y + row)];
            if cell.symbol() != " " {
                continue;
            }
            let ink = shot.ink(f32::from(column), f32::from(row), 1.0, 1.0);
            let grain = BAYER[usize::from(row) % 4][(usize::from(column) + shot.grain_phase) % 4];
            let dither = (f32::from(grain) - 7.5) / 16.0 * GRAIN;
            let level = ((ink + dither) * steps).round().clamp(0.0, steps) as usize;
            if level == 0 {
                continue;
            }
            cell.set_char(RAMP[level] as char)
                .set_fg(palette.tones[(ink * 15.0).round() as usize]);
        }
    }
}

#[cfg(test)]
#[path = "noir_halftone_tests.rs"]
mod tests;
