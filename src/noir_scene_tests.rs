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

/// The existing scene tests paint at cruise; drive-specific tests call `super::paint`.
fn paint(
    scene: Scene,
    style: Style,
    motion: Motion,
    area: Rect,
    buf: &mut Buffer,
    appearance: Appearance,
) {
    super::paint(scene, style, motion, Drive::Cruise, area, buf, appearance);
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
        (Some("dither"), None),
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
            Some(Scene::Dither),
            Some(Scene::Coast),
            Some(Scene::Flight),
            Some(Scene::Transit),
            Some(Scene::Transit),
            Some(Scene::Moire),
            Some(Scene::Dither),
            Some(Scene::Dither),
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
            Some("dither"),
            Some(" Bayer "),
        ]
        .map(Style::from_preference),
        [
            None,
            Some(Style::Halftone),
            Some(Style::Ascii),
            Some(Style::Ascii),
            None,
            Some(Style::Dither),
            Some(Style::Dither),
        ]
    );
    // Photographs default to the halftone and the Dither scene to the ordered dither, unless a
    // style is set explicitly.
    let unset = NoirScene::from_preferences(
        /*scene*/ None, /*legacy*/ None, /*style*/ None,
    );
    let ascii = NoirScene::from_preferences(Some("dither"), /*legacy*/ None, Some("ascii"));
    assert_eq!(
        [
            unset.style_for(Scene::Coast),
            unset.style_for(Scene::Moire),
            unset.style_for(Scene::Dither),
            ascii.style_for(Scene::Coast),
            ascii.style_for(Scene::Dither),
        ],
        [
            Style::Halftone,
            Style::Halftone,
            Style::Dither,
            Style::Ascii,
            Style::Ascii,
        ]
    );
    // Effort tiers pick the drive unless CODEX_NOIR_DRIVE pins one.
    assert_eq!(
        [None, Some(EffortTier::Max), Some(EffortTier::Ultra)].map(Drive::from_tier),
        [Drive::Cruise, Drive::Overdrive, Drive::Warp]
    );
    assert_eq!(
        [
            None,
            Some("cruise"),
            Some("off"),
            Some(" Overdrive "),
            Some("max"),
            Some("warp"),
            Some("ultra"),
            Some("ludicrous"),
        ]
        .map(Drive::from_preference),
        [
            None,
            Some(Drive::Cruise),
            Some(Drive::Cruise),
            Some(Drive::Overdrive),
            Some(Drive::Overdrive),
            Some(Drive::Warp),
            Some(Drive::Warp),
            None,
        ]
    );
    let mut pinned = NoirScene::from_preferences(
        /*scene*/ None, /*legacy*/ None, /*style*/ None,
    );
    assert_eq!(pinned.drive_for(Some(EffortTier::Ultra)), Drive::Warp);
    pinned.drive = Some(Drive::Cruise);
    assert_eq!(pinned.drive_for(Some(EffortTier::Ultra)), Drive::Cruise);
}

#[test]
fn noir_scene_clock_runs_only_while_working_and_restarts_after_rest() {
    let scene = NoirScene::from_preferences(
        /*scene*/ None, /*legacy*/ None, /*style*/ None,
    );
    let now = Instant::now();
    assert_eq!(
        [
            scene.motion_at(now, /*working*/ false),
            scene.motion_at(now, /*working*/ true),
            scene.motion_at(now + Duration::from_secs(60), /*working*/ true),
            scene.motion_at(now + Duration::from_secs(61), /*working*/ false),
            scene.motion_at(now + Duration::from_secs(62), /*working*/ true),
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
    composer.noir_scene =
        NoirScene::from_preferences(Some("transit"), /*legacy*/ None, /*style*/ None);
    for width in [12, 43, 44, 79, 80, 111, 112, 160] {
        for height in [0, 3, 7, 8, 9, 11, 13, 20] {
            let wrapper = SceneComposer {
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
        composer.noir_scene = NoirScene::from_preferences(scene, legacy, /*style*/ None);
        let wrapper = SceneComposer {
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
    composer.noir_scene = NoirScene::from_preferences(
        /*scene*/ None, /*legacy*/ None, /*style*/ None,
    );
    {
        let wrapper = SceneComposer {
            composer: &composer,
            inner: RenderableItem::Borrowed(&Draft),
            appearance: None,
        };
        assert_eq!(wrapper.desired_height(/*width*/ 112), 3);
    }
    composer.set_text_content("/".to_string(), Vec::new(), Vec::new());
    {
        let wrapper = SceneComposer {
            composer: &composer,
            inner: RenderableItem::Borrowed(&Draft),
            appearance: Some(appearance()),
        };
        assert_eq!(wrapper.desired_height(/*width*/ 112), 3);
    }
    composer.set_text_content(String::new(), Vec::new(), Vec::new());
    composer.set_input_enabled(/*enabled*/ false, /*placeholder*/ None);
    {
        let wrapper = SceneComposer {
            composer: &composer,
            inner: RenderableItem::Borrowed(&Draft),
            appearance: Some(appearance()),
        };
        assert_eq!(wrapper.desired_height(/*width*/ 112), 3);
    }
    composer.set_input_enabled(/*enabled*/ true, /*placeholder*/ None);
    composer.set_noir_animations_enabled(/*enabled*/ false);
    let wrapper = SceneComposer {
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
    composer.noir_scene = NoirScene::from_preferences(
        /*scene*/ None, /*legacy*/ None, /*style*/ None,
    );
    let render = |composer: &ChatComposer| {
        let area = Rect::new(
            /*x*/ 0, /*y*/ 0, /*width*/ 112, /*height*/ 20,
        );
        SceneComposer {
            composer,
            inner: RenderableItem::Borrowed(&Draft),
            appearance: Some(appearance()),
        }
        .render(area, &mut Buffer::empty(area));
    };
    composer.set_task_running(/*running*/ true);
    render(&composer);
    assert!(composer.noir_scene.started_at.get().is_some());

    composer.set_text_content("/".to_string(), Vec::new(), Vec::new());
    composer.set_task_running(/*running*/ false);
    render(&composer);
    assert_eq!(composer.noir_scene.started_at.get(), None);

    composer.set_text_content(String::new(), Vec::new(), Vec::new());
    composer.set_task_running(/*running*/ true);
    let resumed = Instant::now();
    render(&composer);
    assert!(
        composer
            .noir_scene
            .started_at
            .get()
            .is_some_and(|start| start >= resumed)
    );

    composer.set_noir_animations_enabled(/*enabled*/ false);
    render(&composer);
    assert_eq!(composer.noir_scene.started_at.get(), None);
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
        (Scene::Coast, Style::Dither),
        (Scene::Dither, Style::Dither),
        (Scene::Dither, Style::Halftone),
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
        // The right-aligned panel leaves the band's corners alone; a full-bleed study fills them.
        if !scene.full_bleed() {
            untouched(3, 5);
            untouched(4, 10);
        }
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
    for scene in [
        Scene::Coast,
        Scene::Flight,
        Scene::Transit,
        Scene::Moire,
        Scene::Dither,
    ] {
        for style in [Style::Halftone, Style::Ascii, Style::Dither] {
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
fn noir_dither_band_keeps_the_wallpaper_and_carries_no_caption() {
    let area = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 112, /*height*/ 10,
    );
    let is_braille = |symbol: &str| {
        symbol
            .chars()
            .all(|glyph| ('\u{2800}'..='\u{28ff}').contains(&glyph))
    };
    // The dark terminal is the primary case; the light one must still quantize and stay open.
    for (look, quantized) in [(appearance(), false), (light_appearance(), true)] {
        let mut buf = Buffer::empty(area);
        paint(
            Scene::Dither,
            Style::Dither,
            Motion::Working(Duration::from_millis(3200)),
            area,
            &mut buf,
            look,
        );
        let mut dots = 0usize;
        let mut braille_cells = 0usize;
        for y in 0..10 {
            for x in 0..112 {
                let cell = &buf[(x, y)];
                let symbol = cell.symbol();
                assert_eq!(symbol.width(), 1, "{x},{y} {symbol:?}");
                assert_eq!(
                    cell.bg,
                    Color::Reset,
                    "{x},{y} must let the wallpaper through"
                );
                assert!(
                    symbol == " " || is_braille(symbol),
                    "{x},{y} {symbol:?} is not Braille"
                );
                if symbol != " " {
                    braille_cells += 1;
                    dots +=
                        (symbol.chars().next().unwrap_or(' ') as u32 & 0xff).count_ones() as usize;
                    assert_eq!(
                        matches!(cell.fg, Color::Indexed(_)),
                        quantized,
                        "{x},{y} foreground quantization"
                    );
                }
            }
        }
        // No caption anywhere in the band: every non-blank cell is picture.
        assert!(
            buf.content
                .iter()
                .all(|cell| cell.symbol() == " " || is_braille(cell.symbol())),
            "the band must carry no text"
        );
        // A background reads as texture, not as a wall: well under half of all dots are lit, and
        // on the dark terminal between a fifth and nine tenths of the cells carry any dot at all.
        // The light terminal prints the shore's darkness, so almost every cell holds a sparse dot.
        let cells = 112 * 10;
        assert!(
            (cells / 20..cells * 4).contains(&dots),
            "{dots} dots in {cells} cells, quantized {quantized}"
        );
        assert!(
            quantized || (cells / 5..cells * 9 / 10).contains(&braille_cells),
            "{braille_cells} of {cells} cells"
        );
    }

    // An explicit dither style keeps photographs in their right-aligned panel.
    let mut coast = Buffer::empty(area);
    paint(
        Scene::Coast,
        Style::Dither,
        Motion::Idle,
        area,
        &mut coast,
        appearance(),
    );
    for y in 2..10 {
        for x in 0..58 {
            assert_eq!(coast[(x, y)].symbol(), " ", "{x},{y}");
        }
    }
    assert!(
        (58..110).any(|x| is_braille(coast[(x, 5)].symbol())),
        "the coast must print through the dither"
    );
}

#[test]
fn noir_dither_gallery_shows_the_lit_coast_band_and_dithered_studies() {
    let mut gallery = Vec::new();
    for (scene, width, height, motion, look) in [
        (
            Scene::Dither,
            112,
            10,
            Motion::Working(Duration::ZERO),
            appearance(),
        ),
        (
            Scene::Dither,
            112,
            10,
            Motion::Working(Duration::from_millis(3200)),
            appearance(),
        ),
        (
            Scene::Dither,
            112,
            10,
            Motion::Working(Duration::from_millis(9000)),
            appearance(),
        ),
        (Scene::Dither, 80, 8, Motion::Idle, appearance()),
        (
            Scene::Dither,
            80,
            8,
            Motion::Working(Duration::from_millis(1200)),
            light_appearance(),
        ),
        (
            Scene::Dither,
            44,
            6,
            Motion::Working(Duration::ZERO),
            appearance(),
        ),
        (
            Scene::Coast,
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
        (
            Scene::Moire,
            80,
            8,
            Motion::Working(Duration::from_millis(2200)),
            appearance(),
        ),
    ] {
        let area = Rect::new(/*x*/ 0, /*y*/ 0, width, height);
        let mut buf = Buffer::empty(area);
        paint(scene, Style::Dither, motion, area, &mut buf, look);
        assert!(
            buf.content.iter().any(|cell| cell.symbol() != " "),
            "{scene:?} must be visible at {width}x{height}"
        );
        gallery.push(format!("{scene:?} {width}x{height} {motion:?}"));
        gallery.extend(rows(&buf));
    }
    insta::assert_snapshot!("noir_dither_gallery", gallery.join("\n"));
}

#[test]
fn noir_drives_change_the_working_picture_but_never_the_idle_one() {
    let area = Rect::new(
        /*x*/ 0, /*y*/ 0, /*width*/ 112, /*height*/ 10,
    );
    let render = |drive: Drive, motion: Motion, look: Appearance| {
        let mut buf = Buffer::empty(area);
        super::paint(
            Scene::Dither,
            Style::Dither,
            motion,
            drive,
            area,
            &mut buf,
            look,
        );
        buf
    };
    let working = Motion::Working(Duration::from_millis(3200));
    let cruise = render(Drive::Cruise, working, appearance());
    let overdrive = render(Drive::Overdrive, working, appearance());
    let warp = render(Drive::Warp, working, appearance());
    assert_ne!(
        cruise, overdrive,
        "overdrive must recolor and streak the picture"
    );
    assert_ne!(overdrive, warp, "warp must replace the picture");
    for (drive, buf) in [
        (Drive::Cruise, &cruise),
        (Drive::Overdrive, &overdrive),
        (Drive::Warp, &warp),
    ] {
        for cell in &buf.content {
            assert_eq!(cell.bg, Color::Reset, "{drive:?} keeps the wallpaper");
            assert!(
                cell.symbol() == " "
                    || cell
                        .symbol()
                        .chars()
                        .all(|glyph| ('\u{2800}'..='\u{28ff}').contains(&glyph)),
                "{drive:?} {:?} is not Braille",
                cell.symbol()
            );
        }
    }
    // The warp jump keeps a fading picture under the stars; after it only stars remain.
    let jump = render(
        Drive::Warp,
        Motion::Working(Duration::from_millis(800)),
        appearance(),
    );
    assert_ne!(jump, warp);
    for drive in [Drive::Overdrive, Drive::Warp] {
        assert_eq!(
            render(drive, Motion::Idle, appearance()),
            render(Drive::Cruise, Motion::Idle, appearance()),
            "{drive:?} must not change the idle frame"
        );
        assert_ne!(
            render(drive, working, appearance()),
            render(
                drive,
                Motion::Working(Duration::from_millis(3300)),
                appearance()
            ),
            "{drive:?} must keep moving"
        );
        let light = render(drive, working, light_appearance());
        assert!(
            light.content.iter().any(|cell| cell.symbol() != " "),
            "{drive:?} shows on light backgrounds"
        );
    }
}

#[test]
fn noir_drive_gallery_shows_overdrive_and_warp() {
    let mut gallery = Vec::new();
    for (scene, drive, width, height, millis) in [
        (Scene::Dither, Drive::Overdrive, 112, 10, 3200),
        (Scene::Dither, Drive::Warp, 112, 10, 900),
        (Scene::Dither, Drive::Warp, 112, 10, 6000),
        (Scene::Dither, Drive::Warp, 80, 8, 12000),
        (Scene::Coast, Drive::Overdrive, 112, 10, 2000),
    ] {
        let area = Rect::new(/*x*/ 0, /*y*/ 0, width, height);
        let mut buf = Buffer::empty(area);
        super::paint(
            scene,
            Style::Dither,
            Motion::Working(Duration::from_millis(millis)),
            drive,
            area,
            &mut buf,
            appearance(),
        );
        assert!(
            buf.content.iter().any(|cell| cell.symbol() != " "),
            "{scene:?} {drive:?} must be visible at {width}x{height}"
        );
        gallery.push(format!("{scene:?} {drive:?} {width}x{height} {millis}ms"));
        gallery.extend(rows(&buf));
    }
    insta::assert_snapshot!("noir_drive_gallery", gallery.join("\n"));
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

#[test]
fn noir_cadence_caps_only_ticks_faster_than_the_requested_rate() {
    let eight = Cadence::from_preference(Some("8"));
    assert_eq!(
        [33, 100, 125, 200].map(|millis| eight.clamp(Duration::from_millis(millis))),
        [125, 125, 125, 200].map(Duration::from_millis)
    );
    assert_eq!(
        Cadence::from_preference(Some(" 30 ")).clamp(Duration::from_millis(16)),
        Duration::from_millis(33)
    );
    for unset in [None, Some(""), Some("0"), Some("61"), Some("fast")] {
        assert_eq!(
            Cadence::from_preference(unset).clamp(Duration::from_millis(33)),
            Duration::from_millis(33),
            "{unset:?} must leave the drive's cadence alone"
        );
    }
    let scene = NoirScene::from_preferences(
        /*scene*/ None, /*legacy*/ None, /*style*/ None,
    );
    assert_eq!(scene.cadence, Cadence::default());
}
