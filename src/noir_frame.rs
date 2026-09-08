//! One cached decorative frame, independent of the composer's keyboard redraws.
//!
//! Status updates and typing can redraw the TUI faster than a scene's cadence. Sampling the
//! picture only at that cadence keeps those redraws cheap and avoids sending new colors and
//! glyphs to the terminal for every keystroke. Geometry and appearance belong to the cache key;
//! draft cells never enter the cache and are still protected when the picture is composited.

use std::cell::RefCell;
use std::time::Duration;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Color;

use super::Appearance;
use super::Drive;
use super::Motion;
use super::Scene;
use super::Style;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) struct FrameSpec {
    pub(super) scene: Scene,
    pub(super) style: Style,
    pub(super) motion: Motion,
    pub(super) drive: Drive,
    pub(super) appearance: Appearance,
}

struct CachedFrame {
    spec: FrameSpec,
    cells: Buffer,
}

#[derive(Default)]
pub(super) struct FrameCache {
    frame: RefCell<Option<CachedFrame>>,
}

impl FrameCache {
    pub(super) fn render(
        &self,
        mut spec: FrameSpec,
        interval: Duration,
        area: Rect,
        buf: &mut Buffer,
    ) {
        let area = area.intersection(buf.area);
        if area.is_empty() {
            return;
        }
        if let Motion::Working(elapsed) = spec.motion {
            // Scene intervals are bounded by the drive's subsecond tick. Keep all redraws
            // within one interval on the same picture, even when a key triggers a redraw.
            let remainder = Duration::from_nanos((elapsed.as_nanos() % interval.as_nanos()) as u64);
            spec.motion = Motion::Working(elapsed - remainder);
        }
        let mut cached = self.frame.borrow_mut();
        let frame = match cached.as_mut() {
            Some(frame) if frame.spec == spec && frame.cells.area == area => frame,
            _ => {
                let mut cells = Buffer::empty(area);
                super::paint(
                    spec.scene,
                    spec.style,
                    spec.motion,
                    spec.drive,
                    area,
                    &mut cells,
                    spec.appearance,
                );
                cached.insert(CachedFrame { spec, cells })
            }
        };
        for y in area.top()..area.bottom() {
            for x in area.left()..area.right() {
                let cell = &mut buf[(x, y)];
                if cell.symbol() != " " {
                    continue;
                }
                let picture = &frame.cells[(x, y)];
                // Only apply fields the painter touched. Foreground-only scenes must retain
                // the existing background, and untouched whitespace must keep its styling.
                if picture.symbol() != " " {
                    cell.set_symbol(picture.symbol());
                }
                if picture.fg != Color::Reset {
                    cell.set_fg(picture.fg);
                }
                if picture.bg != Color::Reset {
                    cell.set_bg(picture.bg);
                }
            }
        }
    }
}

#[cfg(test)]
#[path = "noir_frame_tests.rs"]
mod tests;
