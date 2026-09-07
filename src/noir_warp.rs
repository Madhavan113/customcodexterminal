//! Warpspeed for the Ultra effort tier.
//!
//! [`paint`] streams stars out of a vanishing point a little right of the
//! band's center. Each star is a streak whose length grows with distance and
//! with speed; speed eases up over the warp jump, then holds. A soft core of
//! ordered-dither dots glows at the origin. Color follows radius through the
//! warp palette: cyan near the
//! core, white at streak heads, acid green far out. Everything is a
//! deterministic function of time from a fixed star table, so frames are
//! reproducible and there is no randomness at runtime. Only foreground Braille
//! is painted; occupied cells are skipped and no background is set.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

use super::noir_dither::BAYER;
use super::noir_halftone::BRAILLE_BITS;
use super::noir_halftone::GRADIENT_STOPS;
use super::noir_halftone::Palette;
use super::noir_halftone::Shot;
use super::noir_halftone::WARP_JUMP;
use super::noir_halftone::braille;

const STARS: usize = 180;
/// The golden angle spreads star directions evenly without visible spokes.
const GOLDEN_ANGLE: f32 = 2.399_963;
/// Dots per second of travel at full speed.
const TRAVEL_RATE: f32 = 30.0;
/// Streak intensity a dot needs to print regardless of the dither threshold.
const STAR_INK: f32 = 0.3;

/// Paints the star stream into blank cells of `panel`.
pub(super) fn paint(shot: &Shot<'_>, panel: Rect, buf: &mut Buffer, palette: &Palette) {
    let width = usize::from(panel.width) * 2;
    let height = usize::from(panel.height) * 4;
    if width == 0 || height == 0 {
        return;
    }
    let seconds = shot.seconds;
    let ramp = (seconds / WARP_JUMP).clamp(0.0, 1.0);
    let speed = ramp * ramp * (3.0 - 2.0 * ramp);
    let travel = TRAVEL_RATE * seconds * (0.2 + 0.8 * speed);
    let (vx, vy) = (width as f32 * 0.62, height as f32 * 0.5);
    let reach =
        (vx.max(width as f32 - vx).powi(2) + vy.max(height as f32 - vy).powi(2)).sqrt() + 4.0;

    // Rasterize every streak into a dot canvas, keeping the brightest hit per dot.
    let mut dots = vec![0.0f32; width * height];
    for star in 0..STARS {
        let angle = star as f32 * GOLDEN_ANGLE;
        let phase = (star as f32 * 0.618_034).fract() * reach;
        let rate = 0.55 + 0.45 * (star as f32 * 0.37).fract();
        let head = (phase + travel * rate) % reach;
        let length = 1.0 + (0.04 + 0.28 * speed) * head;
        let (dx, dy) = (angle.cos(), angle.sin());
        let tail = (head - length).max(0.0);
        let mut r = tail;
        while r <= head {
            let (x, y) = (vx + r * dx, vy + r * dy);
            if x >= 0.0 && y >= 0.0 && (x as usize) < width && (y as usize) < height {
                let along = if head > tail {
                    (r - tail) / (head - tail)
                } else {
                    1.0
                };
                let intensity = 0.3 + 0.7 * along;
                let dot = &mut dots[y as usize * width + x as usize];
                if intensity > *dot {
                    *dot = intensity;
                }
            }
            r += 0.5;
        }
    }

    for row in 0..panel.height {
        for column in 0..panel.width {
            let cell = &mut buf[(panel.x + column, panel.y + row)];
            if cell.symbol() != " " {
                continue;
            }
            let mut bits = 0u8;
            let mut peak = 0.0f32;
            let mut radius_total = 0.0f32;
            for (sub_x, column_bits) in BRAILLE_BITS.iter().enumerate() {
                let dot_x = usize::from(column) * 2 + sub_x;
                for (sub_y, bit) in column_bits.iter().enumerate() {
                    let dot_y = usize::from(row) * 4 + sub_y;
                    let (rx, ry) = (dot_x as f32 + 0.5 - vx, dot_y as f32 + 0.5 - vy);
                    let radius = (rx * rx + ry * ry).sqrt();
                    radius_total += radius;
                    let star = dots[dot_y * width + dot_x];
                    // The core glows softly and breathes with speed.
                    let core = 0.4 * speed * (-(radius * radius) / 90.0).exp();
                    let ink = star.max(core);
                    let threshold = (f32::from(BAYER[dot_y % 8][dot_x % 8]) + 0.5) / 64.0;
                    if star >= STAR_INK || ink > threshold {
                        bits |= bit;
                        peak = peak.max(ink);
                    }
                }
            }
            if bits == 0 {
                continue;
            }
            let stop = ((radius_total / 8.0 / reach).clamp(0.0, 1.0) * (GRADIENT_STOPS - 1) as f32)
                .round() as usize;
            let level = (peak * 15.0).round().clamp(0.0, 15.0) as usize;
            cell.set_char(braille(bits))
                .set_fg(palette.gradient[stop][level]);
        }
    }
}

#[cfg(test)]
#[path = "noir_warp_tests.rs"]
mod tests;
