//! Ordered dithering for the Noir scene.
//!
//! [`paint_bayer`] is the treatment behind `CODEX_NOIR_STYLE=dither` and the
//! default for the Dither scene. Every Braille dot is thresholded against a
//! screen-fixed 8×8 Bayer matrix, so smooth tones become the crosshatch-to-dot
//! progression of a 1-bit dither. The matrix stays put while the picture moves
//! underneath it, so a tone crossing a threshold flips a whole lattice of dots
//! at once; that lattice shimmer is what makes the dither read as animated.
//! Only foreground glyphs are painted: the terminal's own wallpaper shows
//! through between the dots, so the treatment behaves like a background. Color
//! follows a diagonal gradient across the band that slides while working:
//! lavender into acid yellow at cruise, magenta into amber in overdrive, and
//! cyan into acid green at warp, faster with each drive.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

use super::noir_halftone::BRAILLE_BITS;
use super::noir_halftone::GRADIENT_STOPS;
use super::noir_halftone::Palette;
use super::noir_halftone::Shot;
use super::noir_halftone::braille;

/// Threshold matrix per Braille dot, indexed `[row][column]`; `(value + 0.5) / 64` is the ink a
/// dot needs before it prints. Four cells across and two cells down repeat the whole matrix.
pub(super) const BAYER: [[u8; 8]; 8] = [
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
];

/// The ordered dither. Cells that already hold a symbol are skipped, cells whose dots all stay
/// below threshold stay blank, and no background is set so the wallpaper shows through.
pub(super) fn paint_bayer(shot: &Shot<'_>, panel: Rect, buf: &mut Buffer, palette: &Palette) {
    let drift = (shot.seconds / shot.drive.gradient_period()).fract();
    for row in 0..panel.height {
        let down = f32::from(row) / f32::from(panel.height.max(2) - 1);
        for column in 0..panel.width {
            let across = f32::from(column) / f32::from(panel.width.max(2) - 1);
            let along = ((0.7 * across + 0.3 * down + drift) % 1.0 * 2.0 - 1.0).abs();
            let stop = (along * (GRADIENT_STOPS - 1) as f32).round() as usize;
            let cell = &mut buf[(panel.x + column, panel.y + row)];
            if cell.symbol() != " " {
                continue;
            }
            let mut bits = 0u8;
            let mut total = 0.0;
            for (sub_x, column_bits) in BRAILLE_BITS.iter().enumerate() {
                let dot_x = (usize::from(column) * 2 + sub_x) % 8;
                for (sub_y, bit) in column_bits.iter().enumerate() {
                    let ink = shot.ink(
                        f32::from(column) + sub_x as f32 * 0.5,
                        f32::from(row) + sub_y as f32 * 0.25,
                        0.5,
                        0.25,
                    );
                    total += ink;
                    let threshold = BAYER[(usize::from(row) * 4 + sub_y) % 8][dot_x];
                    if ink > (f32::from(threshold) + 0.5) / 64.0 {
                        bits |= bit;
                    }
                }
            }
            if bits != 0 {
                cell.set_char(braille(bits))
                    .set_fg(palette.gradient[stop][(total / 8.0 * 15.0).round() as usize]);
            }
        }
    }
}

#[cfg(test)]
#[path = "noir_dither_tests.rs"]
mod tests;
