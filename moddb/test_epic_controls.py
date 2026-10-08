"""Real exported choices for epic art, organization, and the optional shop."""
import copy
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import autoquestgen as A
from decor_library import load_catalog, THEMES
from quest_decor import chapter_images
from moddb import test_offline_ui as _ui

def quests():
    return [{'id':'q1','x':-3.,'y':0.,'size':1.,'tasks':[{'type':'item','item':'botania:mana_pool'}]},
            {'id':'q2','x':3.,'y':2.,'size':2.2,'dependencies':['q1'],'tasks':[{'type':'checkmark'}]}]

class EpicArtControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=load_catalog()

    def images(self, **overrides):
        opts={'_decor_catalog':self.catalog,'decor_theme':'botanical','decor_source':'library',
              'decor_collection':'epic','decor_motion':True,'decor_size':'mixed','seed':42}
        opts.update(overrides)
        return chapter_images('test',quests(),A.AESTHETIC_LEVELS['lavish'],opts,'',A.ftb_id)

    def test_all_themes_have_large_small_and_native_moving_art(self):
        for theme in THEMES:
            with self.subTest(theme=theme):
                images=self.images(decor_theme=theme)
                assets=self.catalog.validate_references(i['image'] for i in images)
                self.assertTrue({'background','landmark','animation'} <= {a.kind for a in assets})
                for image in images:
                    asset=self.catalog.get(image['image'])
                    self.assertAlmostEqual(image['width']/image['height'],
                        asset.display_width/asset.display_height,delta=.003)
                    if asset.animated:
                        self.assertFalse(image['image'].endswith('.png'))
                        self.assertEqual(asset.animation['frames'],32)

    def test_collection_motion_and_size_choices_really_change_map(self):
        epic=self.images()
        classic=self.images(decor_collection='classic',decor_motion=False)
        self.assertNotEqual(epic,classic)
        self.assertFalse(any(self.catalog.get(i['image']).kind in {'landmark','animation'} for i in classic))
        still=self.images(decor_motion=False)
        self.assertFalse(any(self.catalog.get(i['image']).animated for i in still))
        small=self.images(decor_size='small')
        large=self.images(decor_size='large')
        pick=lambda images:{self.catalog.get(i['image']).kind:i for i in images}
        for kind in ('landmark','animation'):
            self.assertGreater(pick(large)[kind]['height'],pick(small)[kind]['height'])
        self.assertEqual(self.images(),epic)

    def test_space_variants_vary_deterministically_without_invented_references(self):
        selected={next(self.catalog.get(i['image']).id for i in self.images(decor_theme='space',seed=seed)
                       if self.catalog.get(i['image']).kind=='background') for seed in range(12)}
        self.assertEqual(len(selected),3)

class EpicUiControls(unittest.TestCase):
    setUp=_ui.OfflineUiTests.setUp
    def test_all_new_settings_persist_and_disabled_shop_is_explicit(self):
        self.assertEqual(self.app.book_style.get(),'immersive')
        self.assertIn('disabled',self.app._shop_prices_row.combo.state())
        self.app.shop_enabled.set(True)
        self.assertNotIn('disabled',self.app._shop_prices_row.combo.state())
        self.app.shop_style.set('builder');self.app.shop_prices.set('high')
        self.app.decor_collection.set('classic');self.app.decor_motion.set(False)
        self.app.decor_size.set('small');self.app.book_style.set('classic')
        self.app._save_cfg()
        expected={'shop_enabled':True,'shop_style':'builder','shop_prices':'high',
                  'decor_collection':'classic','decor_motion':False,'decor_size':'small','book_style':'classic'}
        saved=json.loads(A.CONFIG_PATH.read_text())
        opts=self.app._opts()
        for key,value in expected.items():
            self.assertEqual(saved[key],value)
            self.assertEqual(opts[key],value)
        self.assertIn('disabled',self.app._art_size_row.combo.state())
        self.app.decor_motion.set(True)
        self.assertNotIn('disabled',self.app._art_size_row.combo.state())
        self.app.decor_source.set('stock')
        self.assertIn('disabled',self.app._motion_toggle.state())

    def test_epic_preset_selects_new_art_and_organization_without_enabling_shop(self):
        self.app._apply_book_preset('epic')
        opts=self.app._opts()
        self.assertEqual(opts['decor_collection'],'epic')
        self.assertTrue(opts['decor_motion'])
        self.assertEqual(opts['book_style'],'immersive')
        self.assertFalse(opts['shop_enabled'])

    def test_cli_carries_every_new_choice(self):
        argv=['AutoQuestGen','--build','mods','out','--book-style','classic','--art-collection','classic',
              '--no-animations','--decor-size','large','--shop','--shop-style','adventurer','--shop-prices','low']
        with patch.object(sys,'argv',argv),patch.object(A,'run_cli_build',return_value=0) as build:
            with self.assertRaises(SystemExit):A.main()
        self.assertEqual({k:build.call_args.kwargs[k] for k in
                          ('book_style','decor_collection','decor_motion','decor_size','shop_enabled','shop_style','shop_prices')},
                         {'book_style':'classic','decor_collection':'classic','decor_motion':False,'decor_size':'large',
                          'shop_enabled':True,'shop_style':'adventurer','shop_prices':'low'})

if __name__=='__main__':unittest.main()

