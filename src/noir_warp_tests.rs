use std::time::Duration;

use super::super::Appearance;
use super::super::Drive;
use super::super::Motion;
use super::super::Source;
use super::*;
use crate::terminal_palette::StdoutColorLevel;
use pretty_assertions::assert_eq;
use ratatui::style::Color;

fn painted(millis: u64, color_level: StdoutColorLevel, background: (u8, u8, u8)) -> Buffer {
    let panel = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 112, /*height*/ 10,
    );
    let palette = Palette::new(
        Appearance {
            background,
            color_level,
        },
        /*presence*/ 1.0,
        Drive::Warp,
    );
    let shot = Shot::new(
        Source::Field,
        /*focus*/ 0.5,
        panel,
        Motion::Working(Duration::from_millis(millis)),
        palette.light,
        Drive::Warp,
    );
    let mut buf = Buffer::empty(panel);
    paint(&shot, panel, &mut buf, &palette);
    buf
}

#[test]
fn noir_warp_streams_braille_stars_without_background_and_keeps_moving() {
    let early = painted(300, StdoutColorLevel::TrueColor, (16, 15, 24));
    let cruising = painted(6000, StdoutColorLevel::Ansi256, (247, 243, 236));
    let later = painted(6100, StdoutColorLevel::Ansi256, (247, 243, 236));
    assert_ne!(cruising, later, "the stream must move between frames");
    assert_eq!(
        painted(6000, StdoutColorLevel::Ansi256, (247, 243, 236)),
        cruising,
        "frames are deterministic in time"
    );
    let mut lit = [0usize; 2];
    for (index, buf) in [&early, &cruising].into_iter().enumerate() {
        for cell in &buf.content {
            let symbol = cell.symbol();
            assert_eq!(
                cell.bg,
                Color::Reset,
                "no background so the wallpaper shows through"
            );
            assert!(
                symbol == " "
                    || symbol
                        .chars()
                        .all(|glyph| ('\u{2800}'..='\u{28ff}').contains(&glyph)),
                "{symbol:?} is not Braille"
            );
            if symbol != " " {
                lit[index] += 1;
                if index == 1 {
                    assert!(
                        matches!(cell.fg, Color::Indexed(_)),
                        "quantized on 256 colors"
                    );
                }
            }
        }
    }
    // Speed and streak length grow through the jump, so far more cells light up at cruise than
    // in the first moments, and even then the band stays mostly open.
    assert!(lit[0] < lit[1], "{lit:?}");
    assert!(
        lit[1] > 112 * 10 / 10 && lit[1] < 112 * 10 * 3 / 4,
        "{lit:?}"
    );
}
