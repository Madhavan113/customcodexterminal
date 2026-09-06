use super::*;
use pretty_assertions::assert_eq;

fn encoded(width: u16, height: u16, fps: u16, frames: &[&[u8]]) -> Vec<u8> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(MAGIC);
    bytes.extend_from_slice(&VERSION.to_le_bytes());
    bytes.extend_from_slice(&width.to_le_bytes());
    bytes.extend_from_slice(&height.to_le_bytes());
    bytes.extend_from_slice(&fps.to_le_bytes());
    bytes.extend_from_slice(&(frames.len() as u32).to_le_bytes());
    for frame in frames {
        bytes.extend_from_slice(frame);
    }
    bytes
}

const FIRST: &[u8] = &[0, 51, 102, 153, 204, 255];
const SECOND: &[u8] = &[255; 6];
const STILL: &[u8] = &[10, 20];

#[test]
fn noir_photo_parses_clips_and_rejects_malformed_files() {
    let clip = encoded(
        /*width*/ 3,
        /*height*/ 2,
        /*fps*/ 12,
        &[FIRST, SECOND],
    );
    let study = Study::parse(&clip).expect("clip parses");
    assert_eq!(
        (study.width, study.height, study.fps, study.frame_count),
        (3, 2, 12, 2)
    );
    assert_eq!(study.frame_interval(), Some(Duration::from_millis(83)));
    let still = encoded(
        /*width*/ 2,
        /*height*/ 1,
        /*fps*/ 0,
        &[STILL],
    );
    let still = Study::parse(&still).expect("still parses");
    assert_eq!(still.frame_interval(), None);

    let mut bad_magic = clip.clone();
    bad_magic[0] = b'X';
    let mut bad_version = clip.clone();
    bad_version[4] = 2;
    let mut zero_width = clip.clone();
    zero_width[6] = 0;
    zero_width[7] = 0;
    let mut zero_frames = clip.clone();
    zero_frames[12..16].fill(0);
    let truncated = clip[..clip.len() - 1].to_vec();
    let mut padded = clip.clone();
    padded.push(0);
    for bytes in [
        bad_magic,
        bad_version,
        zero_width,
        zero_frames,
        truncated,
        padded,
        clip[..8].to_vec(),
        Vec::new(),
        encoded(
            /*width*/ 257,
            /*height*/ 1,
            /*fps*/ 0,
            &[&[0; 257]],
        ),
        encoded(
            /*width*/ 1,
            /*height*/ 145,
            /*fps*/ 0,
            &[&[0; 145]],
        ),
        encoded(
            /*width*/ 3,
            /*height*/ 2,
            /*fps*/ 2000,
            &[FIRST, SECOND],
        ),
        encoded(
            /*width*/ 3,
            /*height*/ 2,
            /*fps*/ 0,
            &[FIRST, SECOND],
        ),
    ] {
        assert_eq!(Study::parse(&bytes), None, "{bytes:?}");
    }
}

#[test]
fn noir_photo_loops_frames_at_native_rate_and_averages_clamped_blocks() {
    let clip = encoded(
        /*width*/ 3,
        /*height*/ 2,
        /*fps*/ 12,
        &[FIRST, SECOND],
    );
    let study = Study::parse(&clip).expect("clip parses");
    assert_eq!(
        [0, 82, 84, 999, 1000, 3416, 3417].map(|ms| study.frame_at(Duration::from_millis(ms))),
        [0, 0, 1, 1, 0, 0, 1]
    );
    let still = encoded(
        /*width*/ 2,
        /*height*/ 1,
        /*fps*/ 0,
        &[STILL],
    );
    let still = Study::parse(&still).expect("still parses");
    assert_eq!(still.frame_at(Duration::from_secs(60)), 0);

    let thousandths = |value: f32| (value * 1000.0).round() as i32;
    assert_eq!(
        [
            study.mean_luminance(
                /*frame*/ 0, /*x0*/ 0, /*y0*/ 0, /*x1*/ 3, /*y1*/ 2
            ),
            study.mean_luminance(
                /*frame*/ 0, /*x0*/ 1, /*y0*/ 0, /*x1*/ 3, /*y1*/ 1
            ),
            study.mean_luminance(
                /*frame*/ 0, /*x0*/ -5, /*y0*/ -5, /*x1*/ 1, /*y1*/ 1
            ),
            study.mean_luminance(
                /*frame*/ 0, /*x0*/ 2, /*y0*/ 1, /*x1*/ 40, /*y1*/ 40
            ),
            study.mean_luminance(
                /*frame*/ 2, /*x0*/ 0, /*y0*/ 0, /*x1*/ 3, /*y1*/ 2
            ),
            study.mean_luminance(
                /*frame*/ 1, /*x0*/ 0, /*y0*/ 0, /*x1*/ 3, /*y1*/ 2
            ),
            study.mean_luminance(/*frame*/ 0, i32::MIN, i32::MIN, i32::MIN, i32::MIN),
            study.mean_luminance(/*frame*/ 0, i32::MAX, i32::MAX, i32::MAX, i32::MAX),
        ]
        .map(thousandths),
        [500, 300, 0, 1000, 500, 1000, 0, 1000]
    );
}
