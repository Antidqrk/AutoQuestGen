"""Native animation assets contain moving geometry and close their loops."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import statistics
import unittest

from PIL import Image, ImageChops, ImageStat
from tools import render_animated_decor as renderer


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / 'decor_assets'


class AnimatedDecorAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = json.loads((ART / 'animated_manifest.json').read_text(encoding='utf-8'))['assets']

    def path_for(self, entry):
        expected = f"assets/autoquestgen/textures/block/{entry['theme']}/{entry['id']}.png"
        self.assertEqual(entry['path'], expected)
        self.assertEqual(entry['texture'], f"autoquestgen:block/{entry['theme']}/{entry['id']}")
        path = (ART / entry['path']).resolve()
        self.assertTrue(path.is_relative_to(ART.resolve()))
        return path

    def frames_for(self, entry):
        width = entry['animation']['frame_width']
        height = entry['animation']['frame_height']
        with Image.open(self.path_for(entry)) as sheet:
            columns = sheet.width // width
            return [sheet.crop(((i % columns) * width, (i // columns) * height,
                                (i % columns + 1) * width, (i // columns + 1) * height))
                    for i in range(entry['animation']['frames'])]

    def test_twelve_native_atlases_have_safe_verified_metadata(self):
        self.assertEqual(len(self.entries), 12)
        self.assertEqual({e['theme'] for e in self.entries}, set(renderer.NAMES))
        self.assertEqual(len({e['id'] for e in self.entries}), 12)
        for entry in self.entries:
            with self.subTest(theme=entry['theme']):
                self.assertEqual(entry['kind'], 'animation')
                self.assertEqual(entry['animation'], dict(frame_width=256, frame_height=256,
                    frames=32, frametime=2, interpolate=False))
                path = self.path_for(entry)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
                with Image.open(path) as sheet:
                    self.assertEqual(sheet.mode, 'RGBA')
                    self.assertEqual(sheet.size, (1024, 2048))
                    self.assertEqual(sheet.size, (entry['width'], entry['height']))

    def test_frames_have_real_geometry_motion_not_just_a_blink_or_recolor(self):
        for entry in self.entries:
            with self.subTest(theme=entry['theme']):
                frames = self.frames_for(entry)
                pixel_hashes = {hashlib.sha256(f.tobytes()).hexdigest() for f in frames}
                shape_hashes = {hashlib.sha256(f.getchannel('A').point(lambda v: 255 if v >= 90 else 0).tobytes()).hexdigest()
                                for f in frames}
                self.assertGreaterEqual(len(pixel_hashes), 24)
                self.assertGreaterEqual(len(shape_hashes), 16)

    def test_transparent_margins_and_loop_boundary_are_clean(self):
        for entry in self.entries:
            with self.subTest(theme=entry['theme']):
                frames = self.frames_for(entry)
                visible = []
                for frame in frames:
                    alpha = frame.getchannel('A')
                    left, top, right, bottom = alpha.getbbox()
                    self.assertGreater(left, 0)
                    self.assertGreater(top, 0)
                    self.assertLess(right, frame.width)
                    self.assertLess(bottom, frame.height)
                    self.assertGreater(alpha.histogram()[0] / (frame.width * frame.height), .1)
                    background = Image.new('RGBA', frame.size, '#101724')
                    background.alpha_composite(frame)
                    visible.append(background.convert('RGB'))
                changes = [sum(ImageStat.Stat(ImageChops.difference(visible[i], visible[(i+1) % len(visible)])).mean)
                           for i in range(len(visible))]
                # The final-to-first transition should behave like another
                # frame, not a jump caused by restarting an unrelated sequence.
                self.assertGreater(min(changes), .05)
                self.assertLessEqual(changes[-1], statistics.median(changes[:-1]) * 1.8)

    def test_native_source_recreates_saved_frames_in_row_major_order(self):
        for entry in self.entries:
            frames = self.frames_for(entry)
            for index in (0, 11, 31):
                with self.subTest(theme=entry['theme'], frame=index):
                    self.assertEqual(renderer.render_frame(entry['theme'], index).tobytes(), frames[index].tobytes())

    def test_gif_previews_match_native_frame_count_and_timing(self):
        for entry in self.entries:
            with self.subTest(theme=entry['theme']), Image.open(ART / entry['preview']) as preview:
                self.assertEqual(preview.n_frames, 32)
                self.assertEqual(preview.info.get('loop'), 0)
                duration = 0
                for index in range(preview.n_frames):
                    preview.seek(index)
                    duration += preview.info['duration']
                self.assertEqual(duration, 3200)


if __name__ == '__main__':
    unittest.main()
