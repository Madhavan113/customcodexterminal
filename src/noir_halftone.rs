//! Glyph screens for the Noir scene.
//!
//! [`Shot`] maps panel cells onto the cropped photograph (or the Moiré field),
//! applying contrast, the light-background inversion, the slow pan of stills,
//! and the brief scan slip. A lit photograph instead takes its exposure from
//! [`glow`], three soft lights drifting across the band, so the picture
//! surfaces and sinks as they pass. [`paint_braille`] is the default halftone:
//! each cell is a 2×4 Braille block thresholded through a vertical line screen,
//! so midtones become dense lavender stripes, shadows thin to single dots, and
//! bright rows carry pale acid-yellow bands. [`paint_ascii`] keeps the original
//! density ramp; the ordered dither lives in `noir_dither.rs`. [`Palette`]
//! quantizes every color once per frame so 256-color terminals never search
//! the palette per dot.

use std::f32::consts::TAU;
use std::time::Duration;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Color;

use super::Appearance;
use super::Drive;
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
pub(super) const BRAILLE_BITS: [[u8; 4]; 2] = [[0x01, 0x02, 0x04, 0x40], [0x08, 0x10, 0x20, 0x80]];
/// A slow threshold wave rolling across the dots keeps a still breathing without ever turning a
/// black dot on or a white dot off.
const WAVE: f32 = 0.05;
const BAND_INK: f32 = 0.7;
/// Exposure of a lit photograph: the floor keeps a sparse dithered sky where no light falls, and
/// each light adds up to the gain on top, so lit sky nearly fills and lit water shows its texture.
const GLOW_FLOOR: f32 = 0.30;
const GLOW_GAIN: f32 = 0.65;
/// Ink below the toe stays blank, so rocks and unlit water are negative space rather than noise.
const TOE: f32 = 0.12;
/// Midtone lift for the lit photograph, so the sea shows its texture under a light while the
/// rocks stay dark.
const LIFT: f32 = 0.6;
/// A faint wave travelling along the band gives the lit picture a direction of motion.
const RIPPLE: f32 = 0.04;
/// Stops across the dither band's diagonal color gradient.
pub(super) const GRADIENT_STOPS: usize = 8;
/// Seconds the warp jump takes: the picture smears away while the star stream comes up to speed.
pub(super) const WARP_JUMP: f32 = 2.5;

/// Colors for one frame, quantized once for the terminal's color level.
pub(super) struct Palette {
    pub(super) light: bool,
    pub(super) tones: [Color; 16],
    /// Ink tones per gradient stop, lavender at the first stop and acid yellow at the last.
    pub(super) gradient: [[Color; 16]; GRADIENT_STOPS],
    pub(super) panel_bg: Color,
    pub(super) band_bg: Color,
    pub(super) band_fg: Color,
    pub(super) label: Color,
    pub(super) credit: Color,
}

impl Palette {
    /// Cruise: lavender rises to pale lavender with ink and tips into acid yellow at the top.
    /// Overdrive runs hot, magenta into amber and orange; Warp is cyan into white and acid
    /// green. On light backgrounds every set deepens so it still prints.
    pub(super) fn new(appearance: Appearance, presence: f32, drive: Drive) -> Self {
        let bg = appearance.background;
        let light = is_light(bg);
        let (low, high, acid) = match (drive, light) {
            (Drive::Cruise, false) => ((184, 170, 230), (222, 216, 244), (228, 236, 150)),
            (Drive::Cruise, true) => ((78, 62, 128), (44, 34, 80), (108, 112, 40)),
            (Drive::Overdrive, false) => ((214, 92, 236), (255, 178, 110), (255, 118, 72)),
            (Drive::Overdrive, true) => ((122, 36, 142), (150, 82, 20), (168, 58, 22)),
            (Drive::Warp, false) => ((96, 224, 255), (244, 250, 255), (200, 255, 140)),
            (Drive::Warp, true) => ((0, 108, 150), (28, 48, 112), (70, 118, 20)),
        };
        let quantize = |rgb| best_color_for_level(rgb, appearance.color_level);
        let tones = std::array::from_fn::<_, 16, _>(|index| {
            let ink = index as f32 / 15.0;
            let hue = blend(high, low, ((ink - 0.45) / 0.55).clamp(0.0, 1.0));
            let hue = blend(acid, hue, ((ink - 0.8) / 0.2).clamp(0.0, 1.0) * 0.7);
            quantize(blend(hue, bg, (0.26 + 0.74 * ink) * presence))
        });
        // The gradient slides from lavender through pale lavender into acid yellow; ink still
        // lifts each stop toward the background-appropriate highlight.
        let gradient = std::array::from_fn::<_, GRADIENT_STOPS, _>(|stop| {
            let along = stop as f32 / (GRADIENT_STOPS - 1) as f32;
            let base = blend(acid, low, ((along - 0.45) / 0.55).clamp(0.0, 1.0));
            std::array::from_fn::<_, 16, _>(|index| {
                let ink = index as f32 / 15.0;
                let hue = blend(high, base, ((ink - 0.55) / 0.45).clamp(0.0, 1.0) * 0.6);
                quantize(blend(hue, bg, (0.3 + 0.7 * ink) * presence))
            })
        });
        Self {
            light,
            tones,
            gradient,
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
    extent: (f32, f32),
    /// Horizontal motion blur in cells: highlights smear into dashes as the drive climbs.
    streak: f32,
    /// Multiplier on the lit picture; it fades to nothing during the warp jump.
    fade: f32,
    pub(super) drive: Drive,
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
        drive: Drive,
    ) -> Self {
        let elapsed = match motion {
            Motion::Idle => None,
            Motion::Working(elapsed) => Some(elapsed),
        };
        let seconds = elapsed.as_ref().map_or(0.0, Duration::as_secs_f32);
        let jump = (seconds / WARP_JUMP).clamp(0.0, 1.0);
        let (pan_period, streak, fade) = match drive {
            Drive::Cruise => (16.0, 0.0, 1.0),
            Drive::Overdrive => (5.0, 2.5, 1.0),
            // The picture smears into short dashes and drops away fast so the stars own the band.
            Drive::Warp => (3.0, 1.0 + 2.0 * jump, (1.0 - jump) * (1.0 - jump)),
        };
        let (frame, source_width, source_height, still) = match source {
            Source::Photo(study) | Source::Lit(study) => (
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
            pan_margin * (seconds * TAU / pan_period).sin()
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
            extent: (f32::from(panel.width), f32::from(panel.height)),
            streak,
            fade,
            drive,
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
            Source::Photo(study) | Source::Lit(study) => {
                // The sample box stretches backwards by the streak, so bright detail trails
                // into dashes like a long exposure of something moving.
                let x0 = self.x_origin + (x + slip - self.streak) * self.column_px;
                let y0 = self.y_origin + y * self.row_px;
                study.mean_luminance(
                    self.frame,
                    x0.floor() as i32,
                    y0.floor() as i32,
                    (x0 + (w + self.streak) * self.column_px).ceil() as i32,
                    (y0 + h * self.row_px).ceil() as i32,
                )
            }
            Source::Field => field(
                (x + slip + w / 2.0) * 2.0,
                (y + h / 2.0) * 4.0,
                self.seconds,
            ),
        };
        match self.source {
            Source::Photo(_) | Source::Field => {
                let contrast = ((luminance - 0.5) * 1.25 + 0.5).clamp(0.0, 1.0);
                if self.light { 1.0 - contrast } else { contrast }
            }
            Source::Lit(_) => {
                // Light backgrounds print the shore's darkness instead, so the lights still
                // reveal it while the sky stays blank.
                let tone = if self.light {
                    1.0 - luminance
                } else {
                    luminance
                };
                let (cx, cy) = (x + w / 2.0, y + h / 2.0);
                let exposure =
                    GLOW_FLOOR + GLOW_GAIN * glow(cx, cy, self.extent, self.seconds, self.drive);
                let ripple = RIPPLE
                    * (TAU * (cx / 18.0 - self.seconds / 2.8)
                        + 1.7 * (TAU * (cy / 9.0 + self.seconds / 15.0)).sin())
                    .sin();
                (((tone.powf(LIFT) * exposure + ripple - TOE) / (1.0 - TOE)) * self.fade)
                    .clamp(0.0, 1.0)
            }
        }
    }
}

/// Lights drifting across a panel of `extent` cells, summed and clamped to `0.0..=1.0`. `x` and
/// `y` are cell coordinates; distances are measured in cell heights so the lights stay round.
/// Cruise has three soft lights on slow Lissajous paths, each about a ninth of the band wide.
/// Overdrive trades them for one tight beam sweeping the band every few seconds plus a dim
/// follower. Time only moves the lights, so a still shot is deterministic.
pub(super) fn glow(x: f32, y: f32, extent: (f32, f32), seconds: f32, drive: Drive) -> f32 {
    let (width, height) = (extent.0 * 0.5, extent.1);
    let lights: &[(f32, f32, f32, f32, f32, f32)] = match drive {
        Drive::Cruise | Drive::Warp => &[
            (19.0, 11.0, 0.0, 1.9, 1.0, 0.11),
            (27.0, 13.0, 2.1, 0.4, 0.8, 0.11),
            (23.0, 17.0, 4.2, 3.3, 0.7, 0.11),
        ],
        Drive::Overdrive => &[
            (4.5, 7.0, 0.0, 1.2, 1.4, 0.06),
            (11.0, 5.0, 2.6, 0.0, 0.5, 0.14),
        ],
    };
    let mut total = 0.0;
    for &(period_x, period_y, phase_x, phase_y, strength, spread) in lights {
        let radius = (width * spread).max(1.0);
        let cx = width * (0.5 + 0.42 * (TAU * seconds / period_x + phase_x).sin());
        let cy = height * (0.5 + 0.34 * (TAU * seconds / period_y + phase_y).sin());
        let (dx, dy) = (x * 0.5 - cx, y - cy);
        total += strength * (-(dx * dx + dy * dy) / (2.0 * radius * radius)).exp();
    }
    total.clamp(0.0, 1.0)
}

/// The Braille block lighting exactly the dots in `bits`, using the `BRAILLE_BITS` layout.
pub(super) fn braille(bits: u8) -> char {
    char::from_u32(0x2800 | u32::from(bits)).unwrap_or(' ')
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
                let fg = if band {
                    palette.band_fg
                } else {
                    palette.tones[(mean * 15.0).round() as usize]
                };
                cell.set_char(braille(bits)).set_fg(fg);
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
