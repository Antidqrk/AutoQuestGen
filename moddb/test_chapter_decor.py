"""Portable artwork-control regressions; no modpack reads or writes."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A
from decor_library import THEMES, load_catalog
from quest_decor import chapter_images


AUTHORED_ART = {
    'id': '5000000000000001', 'image': 'example:textures/original_map.png',
    'x': -2.25, 'y': 3.5, 'width': 7.5, 'height': 4.0,
    'rotation': 15.0, 'alpha': 173, 'order': -4, 'color': -1,
}


def book(mode='generated', authored=False):
    quests = [
        {'id': '1000000000000001', 'title': 'First pool', 'x': -6.0, 'y': -2.0,
         'size': 1.0, 'description': ['Build a mana pool.'],
         'tasks': [{'id': '2000000000000001', 'type': 'item', 'item': 'botania:mana_pool', 'count': 1}]},
        {'id': '1000000000000002', 'title': 'A growing supply', 'x': 0.0, 'y': 2.0,
         'size': 1.5, 'dependencies': ['1000000000000001'],
         'tasks': [{'id': '2000000000000002', 'type': 'item', 'item': 'botania:terrasteel_ingot', 'count': 3}],
         'rewards': [{'id': '3000000000000001', 'type': 'xp', 'xp': 25}]},
        {'id': '1000000000000003', 'title': 'The garden', 'x': 5.0, 'y': -2.0,
         'size': 2.0, 'dependencies': ['1000000000000002'],
         'tasks': [{'id': '2000000000000003', 'type': 'checkmark'}]},
    ]
    chapter = {'id': '4000000000000001', 'title': 'Living garden', 'group': 'Botanical',
               'filename': 'garden', 'quests': quests, 'images': [copy.deepcopy(AUTHORED_ART)] if authored else []}
    if mode == 'native':
        chapter['_native_reference'] = True
    elif mode == 'reference':
        chapter['_template'] = True
    elif mode == 'fallback':
        chapter['_template'] = True
        for quest in quests:
            quest.pop('x'); quest.pop('y')
    return {'title': 'Portable artwork book', '_offline_generated': True, 'chapters': [chapter]}


class ChapterDecorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Validate the real bundled files once. Output references must resolve
        # against this catalog, not invented test-only texture names.
        cls.catalog = load_catalog()

    def build(self, mode='generated', authored=False, **overrides):
        options = dict(seed=1234, decor_art=True, decor_source='library', decor_theme='botanical',
                       aesthetic='lavish', layout='reference', quest_size_style='preserve',
                       style_chapters=True, groups=True, progression='linear', auto_chain=False)
        options.update(overrides)
        warnings = []
        with patch('decor_library.load_catalog', return_value=self.catalog):
            chapters, groups, extra = A.build_chapters(book(mode, authored), warnings.append, options)
        return chapters, groups, extra, warnings

    def kinds(self, chapter):
        return {self.catalog.get(image['image']).kind for image in chapter['images']
                if image['image'].startswith('autoquestgen:')}

    def mechanics(self, chapter):
        return [{key: quest.get(key) for key in ('id', 'tasks', 'rewards', 'dependencies', 'x', 'y', 'size')}
                for quest in chapter['quests']]

    def test_each_theme_resolves_real_art_and_preserves_image_proportions(self):
        quests = book()['chapters'][0]['quests']
        original = copy.deepcopy(quests)
        referenced = set()
        for theme in THEMES:
            with self.subTest(theme=theme):
                images = chapter_images('garden', quests, A.AESTHETIC_LEVELS['lavish'],
                    {'_decor_catalog': self.catalog, 'decor_theme': theme, 'decor_source': 'library'}, '', A.ftb_id)
                self.assertEqual(len({image['id'] for image in images}), len(images))
                assets = self.catalog.validate_references(image['image'] for image in images)
                self.assertEqual({asset.kind for asset in assets},
                                 {'background', 'frame', 'panel', 'divider', 'emblem', 'ornament'})
                self.assertEqual({asset.theme for asset in assets}, {theme})
                for image in images:
                    asset = self.catalog.get(image['image'])
                    referenced.add(asset.texture)
                    self.assertAlmostEqual(image['height'], image['width'] * asset.height / asset.width, delta=.002)
                    self.assertTrue(all(math.isfinite(image[key]) for key in ('x', 'y', 'width', 'height')))
                    self.assertGreater(image['width'], 0)
                    self.assertGreater(image['height'], 0)
        self.assertEqual(referenced, {asset.texture for asset in self.catalog.assets if 'epic' not in asset.keywords})
        self.assertEqual(quests, original)

    def test_auto_theme_uses_actual_objectives_and_explicit_override_wins(self):
        for item, expected in [('botania:mana_pool', 'botanical'), ('create:mechanical_press', 'machinery'),
                               ('ad_astra:rocket', 'space')]:
            quests = [{'id': 'q', 'tasks': [{'item': {'id': item}}]}]
            for theme, chosen in [('auto', expected), ('ocean', 'ocean')]:
                with self.subTest(item=item, theme=theme):
                    images = chapter_images('q', quests, A.AESTHETIC_LEVELS['balanced'],
                        {'_decor_catalog': self.catalog, 'decor_theme': theme, 'decor_source': 'library'}, '', A.ftb_id)
                    self.assertEqual({self.catalog.get(image['image']).theme for image in images}, {chosen})

    def test_theme_changes_only_images_in_all_converter_paths(self):
        for mode in ('generated', 'reference', 'fallback', 'native'):
            for size_style in ('uniform', 'dramatic', 'preserve'):
                with self.subTest(mode=mode, sizes=size_style):
                    first = self.build(mode, quest_size_style=size_style, decor_theme='botanical')
                    second = self.build(mode, quest_size_style=size_style, decor_theme='nether')
                    first_chapter, second_chapter = first[0][0][1], second[0][0][1]
                    self.assertNotEqual(first_chapter['images'], second_chapter['images'])
                    self.assertEqual({k: v for k, v in first_chapter.items() if k != 'images'},
                                     {k: v for k, v in second_chapter.items() if k != 'images'})
                    self.assertEqual(first[1:3], second[1:3])
                    self.assertEqual(len(first_chapter['quests']), 3)
                    self.assertEqual(sum(len(q['tasks']) for q in first_chapter['quests']), 3)
                    self.assertEqual({self.catalog.get(i['image']).theme for i in second_chapter['images']}, {'nether'})

    def test_artwork_source_selection_is_effective_without_changing_quests(self):
        for mode in ('generated', 'reference', 'fallback', 'native'):
            with self.subTest(mode=mode):
                library = self.build(mode, decor_source='library')[0][0][1]
                accents = self.build(mode, decor_source='accents')[0][0][1]
                stock = self.build(mode, decor_source='stock')[0][0][1]
                self.assertIn('background', self.kinds(library))
                self.assertNotIn('background', self.kinds(accents))
                self.assertEqual(self.kinds(accents), {'frame', 'panel', 'divider', 'emblem', 'ornament'})
                self.assertTrue(stock['images'])
                self.assertTrue(all(i['image'].startswith('ftbquests:textures/shapes/') for i in stock['images']))
                self.assertEqual(library['quests'], accents['quests'])
                self.assertEqual(library['quests'], stock['quests'])

    def test_art_off_and_minimal_remove_every_image_without_loading_catalog(self):
        for mode in ('generated', 'reference', 'fallback', 'native'):
            for source in ('library', 'accents', 'stock'):
                for override in ({'decor_art': False}, {'aesthetic': 'minimal'}):
                    with self.subTest(mode=mode, source=source, override=override):
                        options = dict(decor_art=True, decor_source=source, aesthetic='lavish',
                                       layout='reference', quest_size_style='preserve')
                        with patch('decor_library.load_catalog', return_value=self.catalog):
                            baseline, _, _ = A.build_chapters(book(mode, authored=True), lambda *_: None, options)
                        options.update(override)
                        with patch('decor_library.load_catalog', side_effect=AssertionError('disabled artwork read disk')):
                            chapters, _, _ = A.build_chapters(book(mode, authored=True), lambda *_: None, options)
                        self.assertEqual(chapters[0][1]['images'], [])
                        self.assertEqual(len(chapters[0][1]['quests']), 3)
                        self.assertEqual(self.mechanics(chapters[0][1]), self.mechanics(baseline[0][1]))

    def test_levels_add_meaningful_artwork_and_preserve_objectives(self):
        for mode in ('generated', 'fallback', 'native'):
            for source in ('library', 'accents'):
                with self.subTest(mode=mode, source=source):
                    chapters = [self.build(mode, decor_source=source, aesthetic=level)[0][0][1]
                                for level in ('balanced', 'decorated', 'lavish')]
                    self.assertLess(len(chapters[0]['images']), len(chapters[1]['images']))
                    self.assertLess(len(chapters[1]['images']), len(chapters[2]['images']))
                    self.assertIn('divider', self.kinds(chapters[1]))
                    self.assertNotIn('ornament', self.kinds(chapters[1]))
                    self.assertIn('ornament', self.kinds(chapters[2]))
                    self.assertEqual(chapters[0]['quests'], chapters[1]['quests'])
                    self.assertEqual(chapters[1]['quests'], chapters[2]['quests'])

    def test_stock_levels_are_effective_and_ignore_inapplicable_library_theme(self):
        for mode in ('generated', 'fallback', 'native'):
            with self.subTest(mode=mode):
                chapters = [self.build(mode, decor_source='stock', aesthetic=level)[0][0][1]
                            for level in ('balanced', 'decorated', 'lavish')]
                self.assertLess(len(chapters[0]['images']), len(chapters[1]['images']))
                # Small maps cap the number of stock shapes; the visual field
                # still changes scale, spacing, and opacity at the next level.
                self.assertNotEqual(chapters[1]['images'], chapters[2]['images'])
                self.assertEqual(self.mechanics(chapters[0]), self.mechanics(chapters[2]))
                ocean = self.build(mode, decor_source='stock', decor_theme='ocean')
                nether = self.build(mode, decor_source='stock', decor_theme='nether')
                self.assertEqual(ocean, nether)

    def test_authored_art_is_retained_when_library_theme_changes(self):
        for mode in ('generated', 'reference', 'fallback', 'native'):
            for source in ('library', 'accents', 'stock'):
                with self.subTest(mode=mode, source=source):
                    for theme in ('ocean', 'arcane'):
                        chapter = self.build(mode, authored=True, decor_source=source, decor_theme=theme)[0][0][1]
                        authored = [image for image in chapter['images'] if image['id'] == AUTHORED_ART['id']]
                        self.assertEqual(authored, [AUTHORED_ART])
                        if source != 'stock':
                            custom = [image for image in chapter['images'] if image['image'].startswith('autoquestgen:')]
                            self.assertTrue(custom)
                            self.assertEqual({self.catalog.get(i['image']).theme for i in custom}, {theme})

    def test_repeat_export_has_stable_images_and_numeric_snbt_fields(self):
        for mode in ('generated', 'fallback', 'native'):
            with self.subTest(mode=mode):
                first = self.build(mode)
                second = self.build(mode)
                self.assertEqual(first, second)
                chapter = first[0][0][1]
                serialized = A.snbt_dumps(chapter)
                roundtrip = A.snbt_loads(serialized)
                self.assertEqual(roundtrip['images'], chapter['images'])
                self.assertEqual(roundtrip['quests'], chapter['quests'])
                self.assertEqual(len({i['id'] for i in chapter['images']}), len(chapter['images']))


if __name__ == '__main__':
    unittest.main()
