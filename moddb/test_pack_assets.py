"""Local pack scripts prove registration; assets prove the item can render."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x10" * 2


class LiteralRegistrationTests(unittest.TestCase):
    def parse(self, text, present=()):
        return A._literal_kubejs_items(text, set(present))

    def test_literal_builders_and_names_only(self):
        rows = self.parse("""
        StartupEvents.registry('item', e => {
          e.create('star').displayName('Pack Star');
          e.create('demo:part').texture('demo:item/shared')
          e.create(variable).texture('demo:item/shared');
          e.create('custom', 'custom_builder');
        })
        """)
        self.assertEqual({'kubejs:star', 'demo:part'}, set(rows))
        self.assertEqual('Pack Star', rows['kubejs:star']['displayName'])
        self.assertEqual('demo:item/shared', rows['demo:part']['texture'])

    def test_comments_strings_and_non_item_registries_do_not_register(self):
        self.assertEqual({}, self.parse("""
        // StartupEvents.registry('item', e => { e.create('comment'); })
        const text = "StartupEvents.registry('item', e => { e.create('string'); })";
        StartupEvents.registry('block', e => { e.create('block'); });
        StartupEvents.registry('item', e => {
          /* e.create('comment2'); */
          const text2 = "e.create('string2')";
        });
        """))

    def test_known_mod_guards_and_unknown_branches(self):
        rows = self.parse("""
        StartupEvents.registry('item', e => {
          if (Platform.isLoaded('base')) { e.create('present'); }
          if (!Platform.isLoaded('base')) { e.create('absent'); }
          if (Platform.isLoaded('missing')) { e.create('no'); } else { e.create('yes'); }
          if (someConfig) { e.create('unknown'); }
          if (someConfig) e.create('unbraced');
          for (let i = 0; i < 3; i++) { e.create('loop'); }
          e.create('always');
        });
        """, {'base'})
        self.assertEqual({'kubejs:present', 'kubejs:yes', 'kubejs:always'}, set(rows))

    def test_registry_inside_unexecuted_scope_is_omitted(self):
        self.assertEqual({}, self.parse("""
        if (false) StartupEvents.registry('item', e => { e.create('hidden'); });
        function later() { StartupEvents.registry('item', e => { e.create('later'); }); }
        """))

    def test_parenthesized_callback_nested_callbacks_and_newlines(self):
        rows = self.parse("""
        StartupEvents.registry("item", (event) => {
          event.create("eye").use((level) => { return false; }).texture("demo:item/eye")
          event.create("star").displayName("Star")
        });
        """)
        self.assertEqual({'kubejs:eye', 'kubejs:star'}, set(rows))


class PackAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'mods').mkdir()

    def write(self, name, data):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(data, bytes):
            data = data.encode()
        p.write_bytes(data)
        return p

    def index(self, mods=('kubejs',), models=None, textures=()):
        model_map = models if models is not None else {}
        result = A._index_pack_assets(self.root, model_map, {}, set(textures), set(mods))
        return result, model_map

    def registration(self, text):
        self.write('kubejs/startup_scripts/items.js', "StartupEvents.registry('item', e => {" + text + "});")

    def test_atm_style_generated_item_requires_real_registered_id_and_png(self):
        self.registration("e.create('atm_star_shard_1').displayName('ATM Star Fragment'); e.create('missing');")
        self.write('kubejs/assets/kubejs/textures/item/atm_star_shard_1.png', PNG)
        self.write('kubejs/assets/kubejs/textures/item/unregistered.png', PNG)
        (items, _lang, notes), models = self.index()
        self.assertEqual({'kubejs:atm_star_shard_1': 'ATM Star Fragment'}, items)
        self.assertIn('kubejs:item/atm_star_shard_1', models)
        self.assertTrue(notes)
        self.assertEqual({}, self.index(mods=())[0][0])

    def test_explicit_texture_works_and_missing_texture_is_not_rescued(self):
        self.registration("e.create('good').texture('demo:item/shared'); e.create('bad').texture('demo:item/missing');")
        self.write('kubejs/assets/kubejs/textures/item/bad.png', PNG)
        (items, _, _), _models = self.index(textures={'demo:item/shared'})
        self.assertEqual({'kubejs:good'}, set(items))

    def test_real_model_is_validated_instead_of_replaced_with_default(self):
        self.registration("e.create('broken');")
        self.write('kubejs/assets/kubejs/textures/item/broken.png', PNG)
        self.write('kubejs/assets/kubejs/models/item/broken.json', json.dumps({
            'parent': 'minecraft:item/generated', 'textures': {'layer0': 'kubejs:item/not_here'}}))
        self.assertEqual({}, self.index()[0][0])

    def test_computed_texture_is_not_approximated_by_default_or_model(self):
        self.registration("e.create('star').texture(whichTexture);")
        self.write('kubejs/assets/kubejs/textures/item/star.png', PNG)
        self.write('kubejs/assets/kubejs/models/item/star.json', json.dumps({
            'parent': 'minecraft:item/generated', 'textures': {'layer0': 'kubejs:item/star'}}))
        self.assertEqual({}, self.index()[0][0])

    def test_enabled_resource_pack_supplies_texture_but_disabled_pack_does_not(self):
        self.registration("e.create('star');")
        self.write('resourcepacks/art/assets/kubejs/textures/item/star.png', PNG)
        self.assertEqual({}, self.index()[0][0])
        self.write('options.txt', 'resourcePacks:["vanilla","file/art"]\n')
        self.assertEqual({'kubejs:star'}, set(self.index()[0][0]))

    def test_enabled_zip_override_can_make_existing_model_invalid(self):
        self.registration("e.create('star');")
        self.write('kubejs/assets/kubejs/textures/item/star.png', PNG)
        self.write('resourcepacks/art.zip', b'')
        with zipfile.ZipFile(self.root / 'resourcepacks/art.zip', 'w') as zf:
            zf.writestr('assets/kubejs/models/item/star.json', json.dumps({
                'parent': 'minecraft:item/generated', 'textures': {'layer0': 'kubejs:item/missing'}}))
        self.write('options.txt', 'resourcePacks:["file/art.zip"]\n')
        self.assertEqual({}, self.index()[0][0])

    def test_resourcepack_parent_paths_are_rejected(self):
        self.write('options.txt', 'resourcePacks:["file/../escape.zip","file/C:\\\\escape.zip"]\n')
        self.assertEqual([], A._pack_asset_inputs(self.root)[1])

    def test_cache_changes_when_startup_asset_or_server_recipe_changes(self):
        self.registration("e.create('star');")
        first = A._scan_cache_key(self.root / 'mods', 30)
        self.write('kubejs/assets/kubejs/textures/item/star.png', PNG)
        second = A._scan_cache_key(self.root / 'mods', 30)
        self.write('kubejs/server_scripts/recipes.js', "ServerEvents.recipes(e => {});")
        third = A._scan_cache_key(self.root / 'mods', 30)
        self.assertTrue(first)
        self.assertEqual(3, len({first, second, third}))

    def test_full_scan_inserts_real_registration_and_name_without_inventing_recipe(self):
        with zipfile.ZipFile(self.root / 'mods/kubejs.jar', 'w') as zf:
            zf.writestr('META-INF/mods.toml', '[[mods]]\nmodId="kubejs"\ndisplayName="KubeJS"\n')
        self.registration("e.create('star').displayName('Pack Star');")
        self.write('kubejs/assets/kubejs/textures/item/star.png', PNG)
        self.write('minecraftinstance.json', '{"minecraftVersion":"1.20.1"}')
        with patch.object(A, 'DATA_DIR', self.root), patch.object(A, '_vanilla_items_from_client', return_value=set()):
            scan = A.scan_mods(self.root / 'mods', 30, lambda _: None)
        self.assertIn('kubejs:star', scan['items']['kubejs'])
        self.assertEqual('Pack Star', scan['names']['kubejs:star'])
        self.assertNotIn('kubejs:star', scan['craftable']['kubejs'])
        self.assertIn('kubejs:star', scan['unobtainable'])
        self.assertEqual({'kubejs:star'}, set(scan['pack_items']))

    def test_only_model_valid_recipe_backed_creative_items_are_restored(self):
        ids = ('creative_essence', 'creative_hidden', 'creative_missing_texture', 'debug_creative_tool')
        with zipfile.ZipFile(self.root / 'mods/demo.jar', 'w') as zf:
            zf.writestr('META-INF/mods.toml', '[[mods]]\nmodId="demo"\ndisplayName="Demo"\n')
            zf.writestr('assets/demo/lang/en_us.json', json.dumps({'item.demo.' + i: i.replace('_', ' ') for i in ids}))
            for iid in ids:
                zf.writestr('assets/demo/models/item/' + iid + '.json', json.dumps({
                    'parent': 'minecraft:item/generated', 'textures': {'layer0': 'demo:item/' + iid}}))
                if iid != 'creative_missing_texture':
                    zf.writestr('assets/demo/textures/item/' + iid + '.png', PNG)
        report = {'recipes': [{'output': {'id': 'demo:' + i, 'components': ''},
                               'ingredients': [{'id': 'minecraft:diamond'}], 'kind': 'minecraft:crafting_shaped'}
                              for i in ids if i != 'creative_hidden'], 'skipped': []}
        self.write('minecraftinstance.json', '{"minecraftVersion":"1.20.1"}')
        with patch.object(A, 'DATA_DIR', self.root), patch.object(A, '_vanilla_items_from_client', return_value={'minecraft:diamond'}), \
                patch('quest_packdata.load_pack_recipes', return_value=report):
            scan = A.scan_mods(self.root / 'mods', 30, lambda _: None)
        self.assertEqual({'demo:creative_essence'}, scan['items']['demo'])
        self.assertIn('demo:creative_essence', scan['craftable']['demo'])
        self.assertNotIn('demo:creative_essence', scan['unobtainable'])

    def test_cap_preserves_valid_pack_recipe_items_without_inventing_missing_ids(self):
        ids = ('a_filler', 'z_reagent', 'z_result')
        with zipfile.ZipFile(self.root / 'mods/demo.jar', 'w') as zf:
            zf.writestr('META-INF/mods.toml', '[[mods]]\nmodId="demo"\ndisplayName="Demo"\n')
            zf.writestr('assets/demo/lang/en_us.json', json.dumps({'item.demo.' + i: i for i in ids}))
            for iid in ids:
                zf.writestr('assets/demo/models/item/' + iid + '.json', json.dumps({
                    'parent': 'minecraft:item/generated', 'textures': {'layer0': 'demo:item/' + iid}}))
                zf.writestr('assets/demo/textures/item/' + iid + '.png', PNG)
        report = {'recipes': [{'output': {'id': 'demo:z_result', 'components': ''},
                               'ingredients': [{'id': 'demo:z_reagent'}, {'id': 'absent:not_registered'}],
                               'kind': 'minecraft:crafting_shaped'}], 'skipped': []}
        self.write('minecraftinstance.json', '{"minecraftVersion":"1.20.1"}')
        with patch.object(A, 'DATA_DIR', self.root), patch.object(A, '_vanilla_items_from_client', return_value={'minecraft:diamond'}), \
                patch('quest_packdata.load_pack_recipes', return_value=report):
            scan = A.scan_mods(self.root / 'mods', 1, lambda _: None)
        self.assertEqual({'demo:z_result', 'demo:z_reagent'}, scan['items']['demo'])
        self.assertFalse(any('absent:not_registered' in v for v in scan['items'].values()))
        self.assertNotIn('demo:z_result', scan['craftable']['demo'])

    def test_roots_crafting_result_and_incenses_are_read_from_known_effect_only(self):
        recipe = {'type': 'rootsclassic:ritual', 'effect': 'rootsclassic:crafting',
                  'effectConfig': {'result': {'id': 'rootsclassic:crystal_staff', 'count': 1}},
                  'ingredients': [{'item': 'minecraft:diamond_block'}],
                  'incenses': [{'item': 'rootsclassic:acacia_bark'}]}
        self.assertEqual({'rootsclassic:crystal_staff'}, A._recipe_result_ids(recipe))
        self.assertEqual({'minecraft:diamond_block', 'rootsclassic:acacia_bark'}, A._recipe_ingredient_ids(recipe))
        recipe['effect'] = 'rootsclassic:not_crafting'
        self.assertEqual(set(), A._recipe_result_ids(recipe))

    def test_registered_known_loot_modifier_proves_drop_without_recipe(self):
        ids = ('heart', 'unregistered', 'unknown_condition', 'unknown_type')
        with zipfile.ZipFile(self.root / 'mods/deeperdarker.jar', 'w') as zf:
            zf.writestr('META-INF/neoforge.mods.toml', '[[mods]]\nmodId="deeperdarker"\ndisplayName="Deeper Darker"\n')
            zf.writestr('assets/deeperdarker/lang/en_us.json', json.dumps({'item.deeperdarker.' + i: i.replace('_', ' ') for i in ids}))
            zf.writestr('data/neoforge/loot_modifiers/global_loot_modifiers.json', json.dumps({
                'entries': ['deeperdarker:' + i for i in ids if i != 'unregistered']}))
            for iid in ids:
                zf.writestr('assets/deeperdarker/models/item/' + iid + '.json', json.dumps({
                    'parent': 'minecraft:item/generated', 'textures': {'layer0': 'deeperdarker:item/' + iid}}))
                zf.writestr('assets/deeperdarker/textures/item/' + iid + '.png', PNG)
                zf.writestr('data/deeperdarker/loot_modifiers/' + iid + '.json', json.dumps({
                    'type': 'unknown:add' if iid == 'unknown_type' else 'deeperdarker:add',
                    'conditions': [{'condition': 'unknown:enabled' if iid == 'unknown_condition' else 'neoforge:loot_table_id',
                                    'loot_table_id': 'minecraft:entities/warden'}],
                    'item': 'deeperdarker:' + iid, 'min': 1, 'max': 1}))
        self.write('minecraftinstance.json', '{"minecraftVersion":"1.21.1"}')
        with patch.object(A, 'DATA_DIR', self.root), patch.object(A, '_vanilla_items_from_client', return_value={'minecraft:diamond'}):
            scan = A.scan_mods(self.root / 'mods', 30, lambda _: None)
        self.assertNotIn('deeperdarker:heart', scan['unobtainable'])
        self.assertNotIn('deeperdarker:heart', scan['craftable']['deeperdarker'])
        self.assertEqual({'minecraft:warden'}, scan['drops']['deeperdarker:heart']['entities'])
        self.assertTrue({'deeperdarker:' + i for i in ids[1:]} <= scan['unobtainable'])

    def test_vanilla_sources_support_both_120_plural_and_121_singular_directories(self):
        for recipe_dir, loot_dir in (('recipes', 'loot_tables'), ('recipe', 'loot_table')):
            with self.subTest(recipe_dir=recipe_dir):
                jar = self.root / (recipe_dir + '.jar')
                with zipfile.ZipFile(jar, 'w') as zf:
                    zf.writestr('data/minecraft/' + recipe_dir + '/iron_ingot.json', json.dumps({
                        'type': 'minecraft:smelting', 'ingredient': {'item': 'minecraft:raw_iron'},
                        'result': {'id': 'minecraft:iron_ingot', 'count': 1}}))
                    zf.writestr('data/minecraft/' + loot_dir + '/chests/end_city_treasure.json', json.dumps({
                        'pools': [{'entries': [{'type': 'minecraft:item', 'name': 'minecraft:diamond'}]}]}))
                with patch.object(A, '_VANILLA_CRAFTABLE', set()):
                    sources = A._load_vanilla_sources(jar)
                    self.assertIn('minecraft:iron_ingot', A._VANILLA_CRAFTABLE)
                    self.assertIn('smelted from raw iron', sources['minecraft:iron_ingot'])
                    self.assertIn('End Cit', sources['minecraft:diamond'])


if __name__ == '__main__':
    unittest.main()
