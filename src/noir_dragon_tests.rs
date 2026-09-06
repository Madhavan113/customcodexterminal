use super::*;
use pretty_assertions::assert_eq;
use ratatui::style::Color;
use ratatui::style::Style as CellStyle;
use ratatui::text::Line;
use ratatui::widgets::Widget;
use unicode_width::UnicodeWidthStr;

fn appearance() -> Appearance {
    Appearance {
        background: (16, 15, 24),
        color_level: StdoutColorLevel::TrueColor,
    }
}

fn light_appearance() -> Appearance {
    Appearance {
        background: (247, 243, 236),
        color_level: StdoutColorLevel::Ansi256,
    }
}

fn rows(buf: &Buffer) -> Vec<String> {
    let area = buf.area;
    (area.top()..area.bottom())
        .map(|y| {
            (area.left()..area.right())
                .map(|x| buf[(x, y)].symbol())
                .collect::<String>()
                .trim_end()
                .to_string()
        })
        .collect()
}

#[test]
fn noir_scene_preferences_select_scene_and_style_and_honor_legacy_switch() {
    let cases = [
        (None, None),
        (Some("coast"), None),
        (Some("flight"), None),
        (Some("transit"), None),
        (Some(" Transit "), None),
        (Some("moire"), None),
        (Some("dragon"), None),
        (Some("off"), None),
        (Some("0"), None),
        (None, Some("false")),
        (Some("transit"), Some("OFF")),
        (Some("transit"), Some("1")),
        (Some("FALSE"), Some("1")),
    ];
    assert_eq!(
        cases.map(|(scene, legacy)| Scene::from_preferences(scene, legacy)),
        [
            Some(Scene::Coast),
            Some(Scene::Coast),
            Some(Scene::Flight),
            Some(Scene::Transit),
            Some(Scene::Transit),
            Some(Scene::Moire),
            Some(Scene::Coast),
            None,
            None,
            None,
            None,
            Some(Scene::Transit),
            None,
        ]
    );
    assert_eq!(
        [
            None,
            Some("halftone"),
            Some("ascii"),
            Some(" ASCII "),
            Some("braille"),
        ]
        .map(Style::from_preference),
        [
            Style::Halftone,
            Style::Halftone,
            Style::Ascii,
            Style::Ascii,
            Style::Halftone,
        ]
    );
}

#[test]
fn noir_scene_clock_runs_only_while_working_and_restarts_after_rest() {
    let dragon = NoirDragon::from_preferences(
        /*scene*/ None, /*legacy*/ None, /*style*/ None,
    );
    let now = Instant::now();
    assert_eq!(
        [
            dragon.motion_at(now, /*working*/ false),
            dragon.motion_at(now, /*working*/ true),
            dragon.motion_at(now + Duration::from_secs(60), /*working*/ true),
            dragon.motion_at(now + Duration::from_secs(61), /*working*/ false),
            dragon.motion_at(now + Duration::from_secs(62), /*working*/ true),
        ],
        [
            Motion::Idle,
            Motion::Working(Duration::ZERO),
            Motion::Working(Duration::from_secs(60)),
            Motion::Idle,
            Motion::Working(Duration::ZERO),
        ],
    );
}

struct Draft;

impl Renderable for Draft {
    fn desired_height(&self, _width: u16) -> u16 {
        3
    }
    fn render(&self, area: Rect, buf: &mut Buffer) {
        Line::from("MY DRAFT MUST STAY READABLE").render(area, buf);
    }
    fn cursor_pos(&self, area: Rect) -> Option<(u16, u16)> {
        (!area.is_empty()).then_some((area.x, area.y))
    }
}

#[test]
fn noir_scene_reserves_rows_only_when_input_still_fits() {
    let (mut composer, _rx) = super::super::tests::new_test_composer();
    composer.set_noir_animations_enabled(/*enabled*/ true);
    composer.noir_dragon =
        NoirDragon::from_preferences(Some("transit"), /*legacy*/ None, /*style*/ None);
    for width in [12, 43, 44, 79, 80, 111, 112, 160] {
        for height in [0, 3, 7, 8, 9, 11, 13, 20] {
            let wrapper = DragonComposer {
                composer: &composer,
                inner: RenderableItem::Borrowed(&Draft),
                appearance: Some(appearance()),
            };
            let area = Rect::new(/*x*/ 0, /*y*/ 0, width, height);
            let (picture, content) = wrapper.regions(area);
            let wanted = match width {
                112.. => 10,
                80.. => 8,
                44.. => 6,
                _ => 0,
            };
            let spare = height.saturating_sub(3);
            let expected = if wanted == 0 || spare < 5 {
                0
            } else {
                wanted.min(spare)
            };
            assert_eq!(picture.height, expected, "{width}x{height}");
            assert_eq!(
                wrapper.desired_height(width),
                3 + wanted,
                "{width}x{height}"
            );
            assert_eq!(wrapper.cursor_pos(area), Draft.cursor_pos(content));
            let mut actual = Buffer::empty(area);
            wrapper.render(area, &mut actual);
            let mut plain = Buffer::empty(area);
            Draft.render(content, &mut plain);
            let start = usize::from(picture.height) * usize::from(width);
            assert_eq!(&actual.content[start..], &plain.content[start..]);
            assert_eq!(
                actual.content[..start]
                    .iter()
                    .any(|cell| cell.symbol() != " "),
                expected > 0,
                "{width}x{height}"
            );
        }
    }
    for (scene, legacy) in [
        (Some("off"), None),
        (None, Some("0")),
        (Some("transit"), Some("false")),
    ] {
        composer.noir_dragon = NoirDragon::from_preferences(scene, legacy, /*style*/ None);
        let wrapper = DragonComposer {
            composer: &composer,
            inner: RenderableItem::Borrowed(&Draft),
            appearance: Some(appearance()),
        };
        assert_eq!(
            wrapper.desired_height(/*width*/ 112),
            3,
            "{scene:?} {legacy:?}"
        );
    }
    composer.noir_dragon = NoirDragon::from_preferences(
        /*scene*/ None, /*legacy*/ None, /*style*/ None,
    );
    {
        let wrapper = DragonComposer {
            composer: &composer,
            inner: RenderableItem::Borrowed(&Draft),
            appearance: None,
        };
        assert_eq!(wrapper.desired_height(/*width*/ 112), 3);
    }
    composer.set_text_content("/".to_string(), Vec::new(), Vec::new());
    {
        let wrapper = DragonComposer {
            composer: &composer,
            inner: RenderableItem::Borrowed(&Draft),
            appearance: Some(appearance()),
        };
        assert_eq!(wrapper.desired_height(/*width*/ 112), 3);
    }
    composer.set_text_content(String::new(), Vec::new(), Vec::new());
    composer.set_input_enabled(/*enabled*/ false, /*placeholder*/ None);
    {
        let wrapper = DragonComposer {
            composer: &composer,
            inner: RenderableItem::Borrowed(&Draft),
            appearance: Some(appearance()),
        };
        assert_eq!(wrapper.desired_height(/*width*/ 112), 3);
    }
    composer.set_input_enabled(/*enabled*/ true, /*placeholder*/ None);
    composer.set_noir_animations_enabled(/*enabled*/ false);
    let wrapper = DragonComposer {
        composer: &composer,
        inner: RenderableItem::Borrowed(&Draft),
        appearance: Some(appearance()),
    };
    assert_eq!(wrapper.desired_height(/*width*/ 112), 3);
}

#[test]
fn noir_scene_resets_playback_when_hidden_by_a_popup_or_disabled_motion() {
    let (mut composer, _rx) = super::super::tests::new_test_composer();
    composer.set_noir_animations_enabled(/*enabled*/ true);
    composer.noir_dragon = NoirDragon::from_preferences(
        /*scene*/ None, /*legacy*/ None, /*style*/ None,
    );
    let render = |composer: &ChatComposer| {
        let area = Rect::new(
            /*x*/ 0, /*y*/ 0, /*width*/ 112, /*height*/ 20,
        );
        DragonComposer {
            composer,
            inner: RenderableItem::Borrowed(&Draft),
            appearance: Some(appearance()),
        }
        .render(area, &mut Buffer::empty(area));
    };
    composer.set_task_running(/*running*/ true);
    render(&composer);
    assert!(composer.noir_dragon.started_at.get().is_some());

    composer.set_text_content("/".to_string(), Vec::new(), Vec::new());
    composer.set_task_running(/*running*/ false);
    render(&composer);
    assert_eq!(composer.noir_dragon.started_at.get(), None);

    composer.set_text_content(String::new(), Vec::new(), Vec::new());
    composer.set_task_running(/*running*/ true);
    let resumed = Instant::now();
    render(&composer);
    assert!(
        composer
            .noir_dragon
            .started_at
            .get()
            .is_some_and(|start| start >= resumed)
    );

    composer.set_noir_animations_enabled(/*enabled*/ false);
    render(&composer);
    assert_eq!(composer.noir_dragon.started_at.get(), None);
}

#[test]
fn noir_scene_paints_only_blank_cells_inside_clipped_offset_regions() {
    let requested = Rect::new(
        /*x*/ 3, /*y*/ 5, /*width*/ 90, /*height*/ 8,
    );
    for (scene, style) in [
        (Scene::Coast, Style::Halftone),
        (Scene::Flight, Style::Halftone),
        (Scene::Moire, Style::Halftone),
        (Scene::Transit, Style::Ascii),
        (Scene::Moire, Style::Ascii),
    ] {
        let mut buf = Buffer::empty(Rect::new(
            /*x*/ 3, /*y*/ 5, /*width*/ 70, /*height*/ 6,
        ));
        buf.set_string(/*x*/ 45, /*y*/ 7, "KEEP_ME", CellStyle::default());
        buf[(45, 7)].set_bg(Color::Magenta);
        buf.set_string(/*x*/ 5, /*y*/ 5, "F", CellStyle::default());
        let original = buf.clone();
        paint(
            scene,
            style,
            Motion::Working(Duration::from_millis(4400)),
            requested,
            &mut buf,
            appearance(),
        );
        let untouched = |x: u16, y: u16| {
            assert_eq!(buf[(x, y)], original[(x, y)], "{scene:?} {style:?} {x},{y}");
        };
        for x in 45..52 {
            untouched(x, 7);
        }
        untouched(5, 5);
        untouched(3, 5);
        untouched(4, 10);
        assert!(
            buf.content
                .iter()
                .filter(|cell| cell.symbol() != " ")
                .count()
                > original.content.len() / 8,
            "{scene:?} {style:?} must fill the clipped panel"
        );
        let mut empty = Buffer::empty(Rect::new(
            /*x*/ 0, /*y*/ 0, /*width*/ 40, /*height*/ 4,
        ));
        paint(
            scene,
            style,
            Motion::Idle,
            requested,
            &mut empty,
            appearance(),
        );
        assert_eq!(empty, Buffer::empty(empty.area));
    }
}

#[test]
fn noir_scene_moves_while_working_and_holds_still_when_idle() {
    let area = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 112, /*height*/ 10,
    );
    for scene in [Scene::Coast, Scene::Flight, Scene::Transit, Scene::Moire] {
        for style in [Style::Halftone, Style::Ascii] {
            let frames = [0, 400, 800].map(|millis| {
                let mut buf = Buffer::empty(area);
                paint(
                    scene,
                    style,
                    Motion::Working(Duration::from_millis(millis)),
                    area,
                    &mut buf,
                    appearance(),
                );
                buf
            });
            assert_ne!(
                frames[0], frames[1],
                "{scene:?} {style:?} must move while working"
            );
            assert_ne!(frames[1], frames[2], "{scene:?} {style:?} must keep moving");
            let mut idle = Buffer::empty(area);
            paint(scene, style, Motion::Idle, area, &mut idle, appearance());
            let mut idle_again = Buffer::empty(area);
            paint(
                scene,
                style,
                Motion::Idle,
                area,
                &mut idle_again,
                appearance(),
            );
            assert_eq!(
                idle, idle_again,
                "{scene:?} {style:?} idle is deterministic"
            );
            assert_ne!(
                idle, frames[0],
                "{scene:?} {style:?} must brighten while working"
            );
            assert!(
                idle.content.iter().any(|cell| cell.symbol() != " "),
                "{scene:?} {style:?} stays visible while idle"
            );
        }
    }
}

#[test]
fn noir_halftone_keeps_single_width_glyphs_and_quantized_colors_inside_the_panel() {
    let area = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 112, /*height*/ 10,
    );
    // 10 rows give a 52-column panel that ends two columns before the right edge.
    let panel_left = 112 - 2 - 52;
    for scene in [Scene::Coast, Scene::Flight, Scene::Transit, Scene::Moire] {
        let mut buf = Buffer::empty(area);
        paint(
            scene,
            Style::Halftone,
            Motion::Working(Duration::from_millis(3200)),
            area,
            &mut buf,
            light_appearance(),
        );
        let mut braille_cells = 0;
        for y in 0..10 {
            for x in 0..112 {
                let cell = &buf[(x, y)];
                let symbol = cell.symbol();
                assert_eq!(symbol.width(), 1, "{scene:?} {x},{y} {symbol:?}");
                if (panel_left..110).contains(&x) {
                    assert!(
                        symbol == " "
                            || symbol
                                .chars()
                                .all(|glyph| ('\u{2800}'..='\u{28ff}').contains(&glyph)),
                        "{scene:?} {x},{y} {symbol:?} is not Braille"
                    );
                    assert!(
                        matches!(cell.bg, Color::Indexed(_)),
                        "{scene:?} {x},{y} panel background must be quantized"
                    );
                    if symbol != " " {
                        braille_cells += 1;
                        assert!(
                            matches!(cell.fg, Color::Indexed(_)),
                            "{scene:?} {x},{y} foreground must be quantized"
                        );
                    }
                } else {
                    assert_eq!(cell.bg, Color::Reset, "{scene:?} {x},{y}");
                    assert!(symbol.is_ascii(), "{scene:?} {x},{y} {symbol:?}");
                }
            }
        }
        assert!(braille_cells > 52 * 10 / 4, "{scene:?} {braille_cells}");
    }
}

#[test]
fn noir_scene_gallery_shows_both_photographic_studies() {
    let mut gallery = Vec::new();
    for (scene, width, height, motion, look) in [
        (
            Scene::Flight,
            112,
            10,
            Motion::Working(Duration::ZERO),
            appearance(),
        ),
        (
            Scene::Flight,
            112,
            10,
            Motion::Working(Duration::from_millis(1500)),
            appearance(),
        ),
        (Scene::Flight, 80, 8, Motion::Idle, appearance()),
        (
            Scene::Transit,
            112,
            10,
            Motion::Working(Duration::from_millis(700)),
            appearance(),
        ),
        (
            Scene::Transit,
            80,
            8,
            Motion::Working(Duration::ZERO),
            light_appearance(),
        ),
        (Scene::Transit, 44, 6, Motion::Idle, appearance()),
    ] {
        let area = Rect::new(/*x*/ 0, /*y*/ 0, width, height);
        let mut buf = Buffer::empty(area);
        paint(scene, Style::Ascii, motion, area, &mut buf, look);
        assert!(
            buf.content.iter().any(|cell| cell.symbol() != " "),
            "{scene:?} must be visible at {width}x{height}"
        );
        gallery.push(format!("{scene:?} {width}x{height} {motion:?}"));
        gallery.extend(rows(&buf));
    }
    insta::assert_snapshot!("noir_scene_gallery", gallery.join("\n"));
}

#[test]
fn noir_halftone_gallery_shows_coast_footage_and_moire() {
    let mut gallery = Vec::new();
    for (scene, width, height, motion, look) in [
        (
            Scene::Coast,
            112,
            10,
            Motion::Working(Duration::ZERO),
            appearance(),
        ),
        (
            Scene::Coast,
            112,
            10,
            Motion::Working(Duration::from_millis(6000)),
            appearance(),
        ),
        (Scene::Coast, 80, 8, Motion::Idle, appearance()),
        (
            Scene::Flight,
            112,
            10,
            Motion::Working(Duration::from_millis(1500)),
            appearance(),
        ),
        (
            Scene::Transit,
            80,
            8,
            Motion::Working(Duration::ZERO),
            light_appearance(),
        ),
        (
            Scene::Moire,
            112,
            10,
            Motion::Working(Duration::ZERO),
            appearance(),
        ),
        (
            Scene::Moire,
            112,
            10,
            Motion::Working(Duration::from_millis(2200)),
            appearance(),
        ),
        (Scene::Moire, 44, 6, Motion::Idle, appearance()),
    ] {
        let area = Rect::new(/*x*/ 0, /*y*/ 0, width, height);
        let mut buf = Buffer::empty(area);
        paint(scene, Style::Halftone, motion, area, &mut buf, look);
        assert!(
            buf.content.iter().any(|cell| cell.symbol() != " "),
            "{scene:?} must be visible at {width}x{height}"
        );
        gallery.push(format!("{scene:?} {width}x{height} {motion:?}"));
        gallery.extend(rows(&buf));
    }
    insta::assert_snapshot!("noir_halftone_gallery", gallery.join("\n"));
}
