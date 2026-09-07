use super::super::Drive;
use super::super::noir_photo::Study;
use super::*;
use crate::terminal_palette::StdoutColorLevel;
use pretty_assertions::assert_eq;

/// An 8×8 still whose left half is white and right half is black.
fn split_study_bytes() -> Vec<u8> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(b"NOIR");
    bytes.extend_from_slice(&1u16.to_le_bytes());
    bytes.extend_from_slice(&8u16.to_le_bytes());
    bytes.extend_from_slice(&8u16.to_le_bytes());
    bytes.extend_from_slice(&0u16.to_le_bytes());
    bytes.extend_from_slice(&1u32.to_le_bytes());
    for _row in 0..8 {
        bytes.extend_from_slice(&[255, 255, 255, 255, 0, 0, 0, 0]);
    }
    bytes
}

#[test]
fn noir_halftone_screens_a_split_photograph_into_full_and_empty_braille_cells() {
    let bytes = split_study_bytes();
    let study = Study::parse(&bytes).expect("split still parses");
    let panel = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 2, /*height*/ 1,
    );
    for (background, full, empty) in [((16, 15, 24), 0, 1), ((247, 243, 236), 1, 0)] {
        let palette = Palette::new(
            Appearance {
                background,
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
        paint_braille(&shot, panel, &mut buf, &palette);
        assert_eq!(
            (
                buf[(full, 0)].symbol(),
                buf[(full, 0)].fg,
                buf[(full, 0)].bg,
                buf[(empty, 0)].symbol(),
                buf[(empty, 0)].bg,
            ),
            (
                "\u{28ff}",
                palette.band_fg,
                palette.band_bg,
                " ",
                palette.panel_bg,
            ),
            "background {background:?}"
        );
    }
}

#[test]
fn noir_halftone_field_stays_bounded_with_contrast_and_only_time_moves_it() {
    let mut lowest = f32::MAX;
    let mut highest = f32::MIN;
    let mut moved = false;
    for v in 0..40 {
        for u in 0..104 {
            let (u, v) = (u as f32, v as f32);
            let idle = field(u, v, /*seconds*/ 0.0);
            lowest = lowest.min(idle);
            highest = highest.max(idle);
            moved |= idle != field(u, v, /*seconds*/ 2.0);
        }
    }
    assert!(
        (0.0..=1.0).contains(&lowest) && (0.0..=1.0).contains(&highest),
        "{lowest} {highest}"
    );
    assert!(highest - lowest > 0.5, "{lowest} {highest}");
    assert!(moved, "the field must drift while working");
}

#[test]
fn noir_halftone_glow_stays_bounded_and_drifts_while_working() {
    let extent = (112.0, 10.0);
    let mut peak = 0.0f32;
    let mut trough = 1.0f32;
    let mut moved = false;
    for y in 0..10 {
        for x in 0..112 {
            let (x, y) = (x as f32 + 0.5, y as f32 + 0.5);
            let idle = glow(x, y, extent, /*seconds*/ 0.0, Drive::Cruise);
            peak = peak.max(idle);
            trough = trough.min(idle);
            moved |= idle != glow(x, y, extent, /*seconds*/ 3.0, Drive::Cruise);
            let beam = glow(x, y, extent, /*seconds*/ 3.0, Drive::Overdrive);
            assert!((0.0..=1.0).contains(&beam), "{beam}");
        }
    }
    assert!(
        (0.0..=1.0).contains(&trough) && (0.0..=1.0).contains(&peak),
        "{trough} {peak}"
    );
    assert!(peak - trough > 0.5, "{trough} {peak}");
    assert!(moved, "the lights must drift while working");
}
