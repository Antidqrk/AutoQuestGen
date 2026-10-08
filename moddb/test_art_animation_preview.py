"""Real PNG frame cropping and portable gallery/quest animation contract."""
import base64
import json
from pathlib import Path
import re
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

import decor_browser as B
from quest_preview import write_preview


class FixtureCatalog:
    def __init__(self, root):
        self.root = root
        self.asset = SimpleNamespace(id='qa_animation', title='QA Animation', theme='space',
            kind='animation', texture='autoquestgen:block/space/qa_animation', path='sheet.png',
            width=16, height=16, animation={'frame_width': 8, 'frame_height': 8,
                'frames': 4, 'frametime': 1, 'interpolate': False})
        self.assets = (self.asset,)

    def get(self, value):
        return self.asset if value in (self.asset.id, self.asset.texture) else None

    def read(self, asset):
        return (self.root / 'sheet.png').read_bytes()

    def validate_references(self, values):
        assert set(values) <= {self.asset.texture}


class ArtAnimationPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        sheet = Image.new('RGBA', (16, 16))
        for (x, y), color in zip(((0, 0), (8, 0), (0, 8), (8, 8)),
                                 ('red', 'lime', 'blue', 'yellow')):
            sheet.paste(color, (x, y, x+8, y+8))
        sheet.save(self.path / 'sheet.png')
        self.catalog = FixtureCatalog(self.path)

    def test_portable_gallery_counts_and_animation_are_catalog_driven(self):
        text = B.write_gallery(self.path / 'gallery.html', self.catalog).read_text(encoding='utf-8')
        self.assertIn('1 original pieces', text)
        self.assertIn('1 animated accents', text)
        self.assertNotIn('72 original', text)
        metadata = json.loads(re.search(r'id="art-data" type="application/json">(.*?)</script>', text, re.S).group(1))
        self.assertEqual(metadata[0]['animation']['frames'], 4)
        self.assertEqual(metadata[0]['animation']['frametime'], 1)
        self.assertIn('viewBox="0 0 8 8"', text, 'Thumbnail must crop to one frame, not shrink the whole sheet')
        self.assertIn('requestAnimationFrame', text)
        self.assertIn('prefers-reduced-motion: reduce', text)
        self.assertIn('Play animation', text)
        self.assertNotIn('fetch(', text)

    def test_quest_preview_embeds_sheet_and_timing_without_changing_export_fields(self):
        chapter = {'title': 'Orbit', 'quests': [], 'images': [{'image': self.catalog.asset.texture,
                    'x': 2, 'y': 3, 'width': 5, 'height': 5, 'alpha': 180, 'rotation': 30}]}
        with patch('decor_library.load_catalog', return_value=self.catalog):
            text = write_preview([('orbit', chapter)], self.path / 'preview.html').read_text(encoding='utf-8')
        payload = json.loads(re.search(r'id="book-data" type="application/json">(.*?)</script>', text, re.S).group(1))
        self.assertEqual(payload['chapters'][0], chapter)
        self.assertEqual(payload['texture_animations'][self.catalog.asset.texture],
                         {'width': 16, 'height': 16, 'frame_width': 8, 'frame_height': 8, 'frames': 4, 'frametime': 1})
        self.assertEqual(base64.b64decode(payload['textures'][self.catalog.asset.texture].split(',')[1]), self.catalog.read(None))
        self.assertIn("overflow:'hidden'", text)
        self.assertIn('Pause artwork', text)
        self.assertIn('requestAnimationFrame', text)
        self.assertNotIn('fetch(', text)

    def test_tk_frame_crops_follow_row_major_sheet_order(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        source = tk.PhotoImage(master=root, file=str(self.path / 'sheet.png'))
        expected = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for frame, color in enumerate(expected):
            photo = B._frame_photo(root, source, self.catalog.asset, 8, 8, frame)
            self.assertEqual((photo.width(), photo.height()), (8, 8))
            self.assertEqual(photo.get(2, 2), color)

    def test_tk_inspector_animates_one_selected_image_and_pause_stops_frames(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with patch.object(B, 'load_catalog', return_value=self.catalog):
            top = B.show_art_library(root, lambda *_: None)
        top.withdraw()
        self.assertEqual(len(top._art_photos), 1)
        self.assertEqual(top._art_selected_id, self.catalog.asset.id)
        frames = set()
        for _ in range(7):
            root.update()
            frames.add(top._art_preview_frame)
            time.sleep(.06)
        self.assertGreater(len(frames), 1)
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)
        play = next(w for w in children(top) if isinstance(w, ttk.Button) and w.cget('text') == 'Pause animation')
        play.invoke()
        frame = top._art_preview_frame
        for _ in range(3):
            time.sleep(.06)
            root.update()
        self.assertEqual(top._art_preview_frame, frame)
        self.assertEqual(play.cget('text'), 'Play animation')
        top.destroy()
        root.update()


if __name__ == '__main__':
    unittest.main()
