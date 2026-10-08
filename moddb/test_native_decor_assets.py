"""Quality checks for the bundled original native map artwork."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image

from tools import render_decor_assets as renderer


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'decor_assets'


class NativeDecorAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ASSETS / 'native_manifest.json').read_text(encoding='utf-8'))
        cls.entries = cls.manifest['assets']

    def path_for(self, entry):
        namespace, texture = entry['texture'].split(':', 1)
        self.assertEqual(namespace, 'autoquestgen')
        self.assertNotIn('..', texture.split('/'))
        self.assertEqual(texture, f"textures/{entry['theme']}/{entry['id']}.png")
        path = (ASSETS / 'assets' / namespace / texture).resolve()
        self.assertTrue(path.is_relative_to(ASSETS.resolve()))
        return path

    def test_complete_distinct_theme_and_kind_catalog(self):
        self.assertEqual(self.manifest['schema'], 1)
        self.assertEqual(len(self.entries), 60)
        self.assertEqual(len({e['id'] for e in self.entries}), 60)
        self.assertEqual(len({e['title'] for e in self.entries}), 60)
        self.assertEqual({(e['theme'], e['kind']) for e in self.entries},
                         {(theme, kind) for theme in renderer.THEMES for kind in renderer.KINDS})

    def test_png_integrity_and_catalog_hashes(self):
        for entry in self.entries:
            with self.subTest(asset=entry['id']):
                path = self.path_for(entry)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
                with Image.open(path) as art:
                    self.assertEqual(art.format, 'PNG')
                    self.assertEqual(art.mode, 'RGBA')
                    self.assertEqual(art.size, (entry['width'], entry['height']))
                    self.assertEqual(art.size, renderer.SIZES[entry['kind']])
                    self.assertFalse(art.info.get('exif'))

    def test_transparent_edges_and_open_quest_space(self):
        for entry in self.entries:
            with self.subTest(asset=entry['id']), Image.open(self.path_for(entry)) as art:
                alpha = art.getchannel('A')
                bounds = alpha.getbbox()
                self.assertIsNotNone(bounds)
                self.assertGreater(bounds[0], 0)
                self.assertGreater(bounds[1], 0)
                self.assertLess(bounds[2], art.width)
                self.assertLess(bounds[3], art.height)
                # Sparse overlays stay out of the way of nodes and labels.
                opaque_fraction = 1 - alpha.histogram()[0] / (art.width * art.height)
                self.assertGreater(opaque_fraction, .015)
                self.assertLess(opaque_fraction, .25)
                if entry['kind'] in {'frame', 'panel'}:
                    self.assertIsNone(alpha.crop((136, 136, 376, 376)).getbbox())

    def test_geometry_is_distinct_without_using_color(self):
        silhouettes = {}
        for entry in self.entries:
            with Image.open(self.path_for(entry)) as art:
                silhouette = art.getchannel('A').point(lambda value: 255 if value > 40 else 0)
                digest = hashlib.sha256(silhouette.tobytes()).hexdigest()
                self.assertNotIn(digest, silhouettes, (entry['id'], silhouettes.get(digest)))
                silhouettes[digest] = entry['id']
        self.assertEqual(len(silhouettes), 60)

    def test_editable_source_recreates_exported_pixels(self):
        # Cover every theme and design, rather than merely checking file existence.
        for entry in self.entries:
            with self.subTest(asset=entry['id']), Image.open(self.path_for(entry)) as art:
                fresh = renderer.render(entry['theme'], entry['kind'])
                self.assertEqual(fresh.tobytes(), art.tobytes())

    def test_review_contact_sheet_exists_and_covers_all_rows(self):
        with Image.open(ASSETS / 'native_contact_sheet.png') as sheet:
            self.assertEqual(sheet.size, (1070, 2550))
            self.assertEqual(sheet.mode, 'RGB')


if __name__ == '__main__':
    unittest.main()
