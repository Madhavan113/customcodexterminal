"""The WezTerm installer's GIF pacing must change only the delay bytes of untimed frames."""

import importlib.util
import io
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ASSET = SCRIPTS.parent / "assets/wallpaper-source.gif"

try:
    from PIL import Image, ImageSequence
except ImportError:  # pragma: no cover - PIL is optional for the suite
    Image = ImageSequence = None

spec = importlib.util.spec_from_file_location(
    "install_wezterm", SCRIPTS / "install-wezterm.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def durations(data):
    with Image.open(io.BytesIO(data)) as image:
        return [
            frame.info.get("duration", 0) for frame in ImageSequence.Iterator(image)
        ]


def timed_gif(colors, duration):
    frames = [Image.new("RGB", (4, 3), color) for color in colors]
    buffer = io.BytesIO()
    frames[0].save(
        buffer, format="GIF", save_all=True, append_images=frames[1:], duration=duration
    )
    return buffer.getvalue()


def pixels(data):
    with Image.open(io.BytesIO(data)) as image:
        return [
            frame.convert("RGBA").tobytes() for frame in ImageSequence.Iterator(image)
        ]


@unittest.skipUnless(Image, "Pillow is not installed")
class GifPacingTests(unittest.TestCase):
    def test_untimed_frames_take_the_pace_and_pixels_stay_identical(self):
        # Pillow omits the timing block for zero delays, so write timed frames and zero the
        # delay bytes of each Graphic Control Extension (0x21 0xF9 0x04 flags lo hi) directly.
        original = bytearray(timed_gif([(255, 0, 0), (0, 255, 0), (0, 0, 255)], 40))
        position = 0
        while (position := original.find(b"\x21\xf9\x04", position)) != -1:
            original[position + 4 : position + 6] = b"\x00\x00"
            position += 8
        original = bytes(original)
        self.assertEqual(durations(original), [0, 0, 0])
        paced = installer.gif_with_frame_delay(original, 100)
        self.assertEqual(len(paced), len(original))
        self.assertEqual(durations(paced), [100, 100, 100])
        self.assertEqual(pixels(paced), pixels(original))
        # Exactly two bytes per frame differ: the delay field of each Graphic Control Extension.
        changed = sum(a != b for a, b in zip(original, paced))
        self.assertEqual(changed, 3)

    def test_frames_with_their_own_timing_are_left_alone(self):
        timed = timed_gif([(255, 0, 0), (0, 0, 255)], 40)
        self.assertEqual(durations(timed), [40, 40])
        self.assertEqual(installer.gif_with_frame_delay(timed, 100), timed)

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            installer.gif_with_frame_delay(b"\x89PNG\r\n\x1a\n", 100)

    @unittest.skipUnless(ASSET.is_file(), "wallpaper asset is not checked out")
    def test_the_shipped_wallpaper_is_paced_losslessly(self):
        original = ASSET.read_bytes()
        paced = installer.gif_with_frame_delay(original, 100)
        self.assertTrue(all(delay == 0 for delay in durations(original)))
        self.assertTrue(all(delay == 100 for delay in durations(paced)))
        self.assertEqual(pixels(paced), pixels(original))


if __name__ == "__main__":
    unittest.main()
