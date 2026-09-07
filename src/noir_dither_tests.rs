use super::super::Appearance;
use super::super::Drive;
use super::super::Motion;
use super::super::Source;
use super::super::noir_photo::Study;
use super::*;
use crate::terminal_palette::StdoutColorLevel;
use pretty_assertions::assert_eq;
use ratatui::style::Color;

/// A 16×8 still of one uniform gray level.
fn flat_study_bytes(level: u8) -> Vec<u8> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(b"NOIR");
    bytes.extend_from_slice(&1u16.to_le_bytes());
    bytes.extend_from_slice(&16u16.to_le_bytes());
    bytes.extend_from_slice(&8u16.to_le_bytes());
    bytes.extend_from_slice(&0u16.to_le_bytes());
    bytes.extend_from_slice(&1u32.to_le_bytes());
    bytes.extend(std::iter::repeat_n(level, 16 * 8));
    bytes
}

fn painted(level: u8) -> (Vec<(String, Color, Color)>, Palette) {
    let bytes = flat_study_bytes(level);
    let study = Study::parse(&bytes).expect("flat still parses");
    let panel = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 4, /*height*/ 2,
    );
    let palette = Palette::new(
        Appearance {
            background: (16, 15, 24),
            color_level: StdoutColorLevel::TrueColor,
        },
        /*presence*/ 1.0,
        Drive::Cruise,
    );
    let shot = Shot::new(
        Source::Photo(&study),
        /*focus*/ 0.5,
        panel,
        Motion::Idle,
        palette.light,
        Drive::Cruise,
    );
    let mut buf = Buffer::empty(panel);
    paint_bayer(&shot, panel, &mut buf, &palette);
    (
        buf.content
            .iter()
            .map(|cell| (cell.symbol().to_string(), cell.fg, cell.bg))
            .collect(),
        palette,
    )
}

#[test]
fn noir_dither_screens_flat_tones_through_the_bayer_matrix_without_background() {
    // Mid gray clears exactly half of the 64 thresholds, so every cell lights four dots in a
    // checker. White clears all of them; black clears none and leaves the cell untouched.
    // Colors follow the diagonal gradient, so each cell compares against its own stop.
    let stops = |ink: usize, palette: &Palette| {
        (0..2)
            .flat_map(|row| (0..4).map(move |column| (row, column)))
            .map(|(row, column)| {
                let along = (0.7 * column as f32 / 3.0 + 0.3 * row as f32) * 2.0 - 1.0;
                palette.gradient[(along.abs() * 7.0).round() as usize][ink]
            })
            .collect::<Vec<_>>()
    };
    let (gray, palette) = painted(128);
    assert_eq!(
        gray,
        stops(8, &palette)
            .into_iter()
            .map(|fg| ("\u{2895}".to_string(), fg, Color::Reset))
            .collect::<Vec<_>>()
    );
    let (white, palette) = painted(255);
    assert_eq!(
        white,
        stops(15, &palette)
            .into_iter()
            .map(|fg| ("\u{28ff}".to_string(), fg, Color::Reset))
            .collect::<Vec<_>>()
    );
    let (black, _) = painted(0);
    assert_eq!(
        black,
        vec![(" ".to_string(), Color::Reset, Color::Reset); 8]
    );
}
