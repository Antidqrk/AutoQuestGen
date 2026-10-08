"""Exercise art selection and offline preview using the real bundled assets."""
import base64
import json
from pathlib import Path
import re
import tempfile
from tkinter import ttk
import unittest

import autoquestgen as A
from decor_browser import THEME_LABELS
from decor_library import load_catalog
from quest_preview import write_preview
from moddb import test_offline_ui as _offline_ui


class ArtLibraryUiTests(unittest.TestCase):
    setUp = _offline_ui.OfflineUiTests.setUp

    def test_defaults_and_saved_options_use_local_library(self):
        self.assertEqual(self.app._opts()['decor_source'], 'library')
        self.app.decor_source.set('accents')
        self.app.decor_theme.set('ocean')
        self.app._save_cfg()
        saved = json.loads(A.CONFIG_PATH.read_text())
        self.assertEqual((saved['decor_source'], saved['decor_theme']), ('accents', 'ocean'))
        self.assertEqual(self.app._opts()['decor_theme'], 'ocean')

    def test_unavailable_art_controls_and_preset_are_explicit(self):
        self.app.aesthetic.set('lavish')
        self.app.opt_decor.set(True)
        self.app.decor_source.set('stock')
        self.assertIn('disabled', self.app._art_theme_row.combo.state())
        self.app._apply_book_preset('epic')
        self.assertEqual(self.app.decor_source.get(), 'library')
        self.assertEqual(self.app.decor_theme.get(), 'auto')
        self.assertNotIn('disabled', self.app._art_theme_row.combo.state())
        self.app.opt_decor.set(False)
        self.assertIn('disabled', self.app._art_source_row.combo.state())
        self.app.opt_decor.set(True)
        self.app.aesthetic.set('minimal')
        self.assertIn('disabled', self.app._art_source_row.combo.state())

    def test_gallery_displays_every_asset_and_selecting_theme_changes_build_settings(self):
        top = self.app._show_art_library()
        self.addCleanup(lambda: top.destroy() if top.winfo_exists() else None)
        self.assertEqual(len(top._art_photos), len(load_catalog().assets))
        def children(widget):
            direct = list(widget.winfo_children())
            return direct + [sub for child in direct for sub in children(child)]
        widgets = children(top)
        themes = next(w for w in widgets if isinstance(w, ttk.Combobox) and THEME_LABELS['farming'] in w.cget('values'))
        themes.set(THEME_LABELS['farming'])
        themes.event_generate('<<ComboboxSelected>>')
        self.root.update_idletasks()
        self.assertEqual(len(top._art_photos), len(load_catalog().by_theme('farming')))
        self.app.aesthetic.set('minimal')
        self.app.opt_decor.set(False)
        next(w for w in children(top) if isinstance(w, ttk.Button) and w.cget('text') == 'Use this theme').invoke()
        self.assertEqual(self.app._opts()['decor_theme'], 'farming')
        self.assertEqual(self.app._opts()['decor_source'], 'library')
        self.assertTrue(self.app.opt_decor.get())
        self.assertEqual(self.app.aesthetic.get(), 'decorated')

    def test_portable_preview_embeds_exact_png_and_export_image_geometry(self):
        catalog = load_catalog()
        asset = catalog.by_theme('botanical', 'background')[0]
        chapter = {'title':'Garden', 'quests':[], 'images':[{'image':asset.texture,
            'x':1.25, 'y':-2, 'width':15, 'height':10, 'alpha':160, 'order':-40}]}
        with tempfile.TemporaryDirectory() as tmp:
            text = write_preview([('garden', chapter)], Path(tmp)/'preview.html').read_text()
        data = json.loads(re.search(r'type="application/json">(.*?)</script>', text, re.S).group(1))
        self.assertEqual(data['chapters'][0]['images'], chapter['images'])
        uri = data['textures'][asset.texture]
        self.assertEqual(base64.b64decode(uri.split(',', 1)[1]), catalog.read(asset))
        self.assertNotIn('fetch(', text)
        self.assertIn("'pointer-events':'none'", text)


if __name__ == '__main__':
    unittest.main()
