use super::*;
use pretty_assertions::assert_eq;
use ratatui::style::Color;
use ratatui::style::Style;

#[test]
fn noir_activity_tracks_work_and_reduced_motion_without_a_timeout() {
    let mut activity = NoirActivity::default();
    let now = Instant::now();
    let mut frames = vec![activity.elapsed_at(now)];
    activity.set_enabled(/*enabled*/ true);
    frames.push(activity.elapsed_at(now));
    activity.set_working(/*working*/ true);
    frames.push(activity.elapsed_at(now));
    frames.push(activity.elapsed_at(now + Duration::from_secs(600)));
    activity.set_working(/*working*/ false);
    frames.push(activity.elapsed_at(now + Duration::from_secs(601)));
    activity.set_working(/*working*/ true);
    frames.push(activity.elapsed_at(now + Duration::from_secs(602)));
    activity.set_enabled(/*enabled*/ false);
    frames.push(activity.elapsed_at(now + Duration::from_secs(603)));
    activity.set_enabled(/*enabled*/ true);
    frames.push(activity.elapsed_at(now + Duration::from_secs(604)));
    assert_eq!(
        frames,
        vec![
            None,
            None,
            Some(Duration::ZERO),
            Some(Duration::from_secs(600)),
            None,
            Some(Duration::ZERO),
            None,
            Some(Duration::ZERO)
        ]
    );
}

#[test]
fn noir_activity_modes_animate_without_touching_draft_cells() {
    let area = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 32, /*height*/ 3,
    );
    let rail = Rect::new(
        /*x*/ 2, /*y*/ 0, /*width*/ 28, /*height*/ 1,
    );
    let mut original = Buffer::empty(area);
    original.set_string(
        /*x*/ 1,
        /*y*/ 1,
        "› my draft stays exactly here",
        Style::default(),
    );
    original[(5, 1)].set_bg(Color::Magenta);
    let mut gallery = Vec::new();
    for tier in [None, Some(EffortTier::Max), Some(EffortTier::Ultra)] {
        let mut first = None;
        for millis in [0, 700, 1700] {
            let mut buf = original.clone();
            paint(
                tier,
                Duration::from_millis(millis),
                rail,
                &mut buf,
                (9, 10, 16),
                StdoutColorLevel::TrueColor,
            );
            assert_eq!(&buf.content[32..], &original.content[32..]);
            if let Some(first) = &first {
                assert_ne!(&buf, first, "working animation must keep changing");
            } else {
                first = Some(buf.clone());
            }
            let symbols = (0..32).map(|x| buf[(x, 0)].symbol()).collect::<String>();
            let symbols = symbols.trim_end();
            let colors = (2..30)
                .map(|x| match buf[(x, 0)].fg {
                    Color::Rgb(r, g, b) => format!("{r:02x}{g:02x}{b:02x}"),
                    color => format!("{color:?}"),
                })
                .collect::<Vec<_>>()
                .join(" ");
            gallery.push(format!("{tier:?} {millis}ms\n{symbols}\n{colors}"));
        }
    }
    insta::assert_snapshot!("noir_activity_mode_gallery", gallery.join("\n\n"));
}

#[test]
fn noir_activity_clips_small_offset_rails_and_preserves_occupied_cells() {
    for width in 0..=12 {
        let area = Rect::new(/*x*/ 3, /*y*/ 5, width, /*height*/ 2);
        let mut buf = Buffer::empty(area);
        if width > 0 {
            buf[(3, 5)].set_symbol("x").set_bg(Color::Magenta);
        }
        let original = buf.clone();
        let rail = Rect::new(
            /*x*/ 0,
            /*y*/ 5,
            width.saturating_add(8),
            /*height*/ 1,
        );
        paint(
            Some(EffortTier::Ultra),
            Duration::from_millis(700),
            rail,
            &mut buf,
            (9, 10, 16),
            StdoutColorLevel::Ansi256,
        );
        if width > 0 {
            assert_eq!(buf[(3, 5)], original[(3, 5)]);
        }
        assert_eq!(
            &buf.content[usize::from(width)..],
            &original.content[usize::from(width)..]
        );
    }
}

#[test]
fn noir_activity_is_dormant_behind_a_popup_or_when_motion_is_disabled() {
    let (mut composer, _rx) = super::super::tests::new_test_composer();
    composer.set_noir_animations_enabled(/*enabled*/ true);
    composer.set_task_running(/*running*/ true);
    composer.set_text_content("/".to_string(), Vec::new(), Vec::new());
    let area = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 40, /*height*/ 8,
    );
    let mut buf = Buffer::empty(area);
    let original = buf.clone();
    assert!(!matches!(composer.popups.active, ActivePopup::None));
    composer.render_noir_activity(area, &mut buf);
    assert_eq!(buf, original);
    assert_eq!(composer.noir_activity.started_at.get(), None);

    composer.set_text_content(String::new(), Vec::new(), Vec::new());
    composer.set_noir_animations_enabled(/*enabled*/ false);
    assert_eq!(composer.noir_activity.elapsed_at(Instant::now()), None);
}
