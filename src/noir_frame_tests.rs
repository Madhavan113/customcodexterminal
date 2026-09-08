use super::*;
use pretty_assertions::assert_eq;
use ratatui::style::Modifier;
use ratatui::style::Style as CellStyle;

use crate::terminal_palette::StdoutColorLevel;

fn spec(drive: Drive) -> FrameSpec {
    FrameSpec {
        scene: Scene::Dither,
        style: Style::Dither,
        motion: Motion::Working(Duration::ZERO),
        drive,
        appearance: Appearance {
            background: (16, 15, 24),
            color_level: StdoutColorLevel::TrueColor,
        },
    }
}

#[test]
fn noir_cached_scene_holds_between_ticks_without_delaying_input() {
    let area = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 112, /*height*/ 10,
    );
    for drive in [Drive::Cruise, Drive::Overdrive, Drive::Warp] {
        let cache = FrameCache::default();
        let mut spec = spec(drive);
        let mut first = Buffer::empty(area);
        cache.render(spec, drive.tick(), area, &mut first);

        // A keyboard redraw just before the next tick keeps the previous image and the
        // newly rendered input. No wait, sleep or global frame limiter gates that input.
        let elapsed = drive.tick() - Duration::from_nanos(1);
        spec.motion = Motion::Working(elapsed);
        let mut keyboard = Buffer::empty(area);
        keyboard.set_string(
            /*x*/ 40,
            /*y*/ 5,
            "NEW_INPUT",
            CellStyle::default(),
        );
        let input = keyboard.clone();
        cache.render(spec, drive.tick(), area, &mut keyboard);
        let mut expected = first.clone();
        // Existing input keeps its own styling as well as its text.
        for x in 40..49 {
            expected[(x, 5)] = input[(x, 5)].clone();
        }
        assert_eq!(keyboard, expected);

        // Input from that redraw must never get baked into the cached picture.
        let mut clean = Buffer::empty(area);
        cache.render(spec, drive.tick(), area, &mut clean);
        assert_eq!(clean, first);

        spec.motion = Motion::Working(drive.tick());
        let mut next = Buffer::empty(area);
        cache.render(spec, drive.tick(), area, &mut next);
        assert_ne!(next, first, "{drive:?} must advance at its own cadence");
    }
}

#[test]
fn noir_cached_frames_match_direct_paint_after_layout_and_appearance_changes() {
    let cache = FrameCache::default();
    let requested = Rect::new(
        /*x*/ 3, /*y*/ 5, /*width*/ 90, /*height*/ 8,
    );
    let styled = CellStyle::default()
        .fg(Color::Yellow)
        .bg(Color::Blue)
        .add_modifier(Modifier::UNDERLINED);
    // Reuse one cache while changing every input that can alter the picture. Clipping,
    // occupied cells and styled whitespace must behave exactly like the original painter.
    for (area, appearance) in [
        (requested, spec(Drive::Cruise).appearance),
        (
            Rect::new(
                /*x*/ 3, /*y*/ 5, /*width*/ 70, /*height*/ 6,
            ),
            Appearance {
                background: (247, 243, 236),
                color_level: StdoutColorLevel::Ansi256,
            },
        ),
    ] {
        for scene in [
            Scene::Coast,
            Scene::Flight,
            Scene::Transit,
            Scene::Moire,
            Scene::Dither,
        ] {
            for style in [Style::Halftone, Style::Ascii, Style::Dither] {
                for drive in [Drive::Cruise, Drive::Overdrive, Drive::Warp] {
                    for motion in [Motion::Idle, Motion::Working(Duration::ZERO)] {
                        let spec = FrameSpec {
                            scene,
                            style,
                            motion,
                            drive,
                            appearance,
                        };
                        let mut actual = Buffer::empty(area);
                        actual.set_style(area, styled);
                        actual.set_string(/*x*/ 45, /*y*/ 7, "KEEP_ME", styled);
                        let mut expected = actual.clone();
                        super::super::paint(
                            scene,
                            style,
                            motion,
                            drive,
                            requested,
                            &mut expected,
                            appearance,
                        );
                        cache.render(spec, drive.tick(), requested, &mut actual);
                        assert_eq!(actual, expected, "{scene:?} {style:?} {drive:?} {motion:?}");
                    }
                }
            }
        }
    }
}
