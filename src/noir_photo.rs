//! Embedded photographic luminance for the Codex Noir scene.
//!
//! An `.nrf` file is grayscale source data rather than prebuilt glyphs: a
//! 16-byte little-endian header (`NOIR`, u16 version, u16 width, u16 height,
//! u16 FPS with zero meaning a still, u32 frame count) followed by row-major u8
//! pixels for every frame. The scene renderer alone decides how luminance
//! becomes terminal characters, and a malformed file simply yields no study.

use std::sync::LazyLock;
use std::time::Duration;

const MAGIC: &[u8; 4] = b"NOIR";
const VERSION: u16 = 1;
const HEADER_LEN: usize = 16;

/// Birds taking flight, sampled from the user's homepage footage.
pub(super) static FLIGHT: LazyLock<Option<Study<'static>>> =
    LazyLock::new(|| Study::parse(include_bytes!("../../../assets/noir/flight.nrf")));

/// Mario Calvo's long-exposure photograph of a moving subway train.
pub(super) static TRANSIT: LazyLock<Option<Study<'static>>> =
    LazyLock::new(|| Study::parse(include_bytes!("../../../assets/noir/transit.nrf")));

/// A parsed luminance clip. Stills have a single frame and no frame rate.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct Study<'a> {
    pub(super) width: usize,
    pub(super) height: usize,
    pub(super) fps: u16,
    pub(super) frame_count: usize,
    pixels: &'a [u8],
}

impl<'a> Study<'a> {
    pub(super) fn parse(bytes: &'a [u8]) -> Option<Self> {
        let header = bytes.get(..HEADER_LEN)?;
        let field = |offset: usize| u16::from_le_bytes([header[offset], header[offset + 1]]);
        if &header[..MAGIC.len()] != MAGIC || field(/*offset*/ 4) != VERSION {
            return None;
        }
        let width = usize::from(field(/*offset*/ 6));
        let height = usize::from(field(/*offset*/ 8));
        let fps = field(/*offset*/ 10);
        let frame_count = usize::try_from(u32::from_le_bytes([
            header[12], header[13], header[14], header[15],
        ]))
        .ok()?;
        // Match the offline converter's size/rate budget. This also keeps
        // block sums bounded and prevents zero-duration redraw requests.
        if !(1..=256).contains(&width)
            || !(1..=144).contains(&height)
            || !(1..=256).contains(&frame_count)
            || fps > 24
            || (fps == 0 && frame_count != 1)
        {
            return None;
        }
        let expected = width
            .checked_mul(height)?
            .checked_mul(frame_count)?
            .checked_add(HEADER_LEN)?;
        if bytes.len() != expected {
            return None;
        }
        Some(Self {
            width,
            height,
            fps,
            frame_count,
            pixels: &bytes[HEADER_LEN..],
        })
    }

    /// Time between frames while the clip plays; stills return `None`.
    pub(super) fn frame_interval(&self) -> Option<Duration> {
        if self.fps == 0 || self.frame_count < 2 {
            return None;
        }
        Some(Duration::from_millis(1000 / u64::from(self.fps)))
    }

    /// Frame reached after `elapsed` of continuous playback, looping at the clip's native rate.
    pub(super) fn frame_at(&self, elapsed: Duration) -> usize {
        if self.frame_interval().is_none() {
            return 0;
        }
        let ticks = elapsed.as_millis() * u128::from(self.fps) / 1000;
        (ticks % self.frame_count as u128) as usize
    }

    /// Mean luminance in `0.0..=1.0` of the half-open source block `[x0, x1) × [y0, y1)` of a
    /// frame. Coordinates are clamped inside the image so callers may pan or shift freely, and
    /// the frame index wraps around the clip.
    pub(super) fn mean_luminance(&self, frame: usize, x0: i32, y0: i32, x1: i32, y1: i32) -> f32 {
        let frame_len = self.width * self.height;
        let frame = &self.pixels[(frame % self.frame_count) * frame_len..][..frame_len];
        let clamp = |value: i32, limit: usize| value.clamp(0, limit as i32 - 1) as usize;
        let (xa, ya) = (clamp(x0, self.width), clamp(y0, self.height));
        let xb = clamp(x1.saturating_sub(1), self.width).max(xa);
        let yb = clamp(y1.saturating_sub(1), self.height).max(ya);
        let mut sum = 0u32;
        for y in ya..=yb {
            let row = &frame[y * self.width..][..self.width];
            sum += row[xa..=xb]
                .iter()
                .map(|&pixel| u32::from(pixel))
                .sum::<u32>();
        }
        let count = ((xb - xa + 1) * (yb - ya + 1)) as f32;
        sum as f32 / count / 255.0
    }
}

#[cfg(test)]
#[path = "noir_photo_tests.rs"]
mod tests;
