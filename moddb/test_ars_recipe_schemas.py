"""Ars Nouveau serializer fields, verified against installed 1.20.1 4.12.7."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A

BOOK = {'type': 'ars_nouveau:book_upgrade', 'pattern': [' y '],
        'key': {'y': {'item': 'ars_nouveau:spell_book'}},
        'ingredients': [{'item': 'ars_nouveau:novice_spell_book'}, {'item': 'minecraft:diamond'}],
        'result': {'item': 'ars_nouveau:apprentice_spell_book'}}
GLYPH = {'type': 'ars_nouveau:glyph', 'count': 1, 'exp': 55,
         'inputItems': [{'item': {'item': 'ars_nouveau:water_essence'}},
                        {'item': {'item': 'minecraft:water_bucket'}}],
         'output': 'ars_nouveau:glyph_conjure_water'}
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00\x00\x00\x10' * 2


class ArsRecipeSchemaTests(unittest.TestCase):
    def test_book_upgrade_ignores_obsolete_shaped_key(self):
        self.assertEqual({'ars_nouveau:novice_spell_book', 'minecraft:diamond'}, A._recipe_ingredient_ids(BOOK))
        self.assertEqual({'ars_nouveau:apprentice_spell_book'}, A._recipe_result_ids(BOOK))
        shaped = {'type': 'minecraft:crafting_shaped', 'pattern': ['A'],
                  'key': {'A': {'item': 'minecraft:diamond'}}}
        self.assertEqual({'minecraft:diamond'}, A._recipe_ingredient_ids(shaped))

    def test_glyph_reads_wrapped_ingredients_without_traversing_other_metadata(self):
        self.assertEqual({'ars_nouveau:water_essence', 'minecraft:water_bucket'}, A._recipe_ingredient_ids(GLYPH))
        self.assertEqual({'ars_nouveau:glyph_conjure_water'}, A._recipe_result_ids(GLYPH))
        tagged = copy.deepcopy(GLYPH)
        tagged['inputItems'] = [{'item': {'tag': 'forge:gems/diamond'}}]
        self.assertEqual({'#forge:gems/diamond'}, A._recipe_ingredient_ids(tagged))
        tagged['type'] = 'unrelated:other_serializer'
        self.assertEqual(set(), A._recipe_ingredient_ids(tagged))

    def test_real_jar_scan_records_valid_book_and_glyph_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'mods').mkdir()
            (root / 'minecraftinstance.json').write_text('{"minecraftVersion":"1.20.1"}')
            ids = ('novice_spell_book', 'apprentice_spell_book', 'water_essence', 'glyph_conjure_water')
            with zipfile.ZipFile(root / 'mods/ars.jar', 'w') as jar:
                jar.writestr('META-INF/mods.toml', '[[mods]]\nmodId="ars_nouveau"\ndisplayName="Ars Nouveau"\n')
                jar.writestr('assets/ars_nouveau/lang/en_us.json', json.dumps({'item.ars_nouveau.' + i: i for i in ids}))
                for item in ids:
                    jar.writestr('assets/ars_nouveau/models/item/' + item + '.json', json.dumps({
                        'parent': 'minecraft:item/generated', 'textures': {'layer0': 'ars_nouveau:item/' + item}}))
                    jar.writestr('assets/ars_nouveau/textures/item/' + item + '.png', PNG)
                jar.writestr('data/ars_nouveau/recipes/apprentice_spell_book_upgrade.json', json.dumps(BOOK))
                jar.writestr('data/ars_nouveau/recipes/glyph_conjure_water.json', json.dumps(GLYPH))
            with patch.object(A, 'DATA_DIR', root), patch.object(A, '_COLOUR_SIBLINGS', {}), \
                    patch.object(A, '_vanilla_items_from_client', return_value={'minecraft:diamond', 'minecraft:water_bucket'}):
                scan = A.scan_mods(root / 'mods', 100, lambda _: None)
                book = scan['craft_recipes']['ars_nouveau:apprentice_spell_book']
                glyph = scan['craft_recipes']['ars_nouveau:glyph_conjure_water']
                self.assertEqual([('ars_nouveau:book_upgrade', frozenset({'ars_nouveau:novice_spell_book', 'minecraft:diamond'}))], book)
                self.assertEqual([('ars_nouveau:glyph', frozenset({'ars_nouveau:water_essence', 'minecraft:water_bucket'}))], glyph)
                self.assertNotIn('ars_nouveau:spell_book', scan['edges']['ars_nouveau:apprentice_spell_book'])
                self.assertIn('ars_nouveau:glyph_conjure_water', scan['craftable']['ars_nouveau'])


if __name__ == '__main__':
    unittest.main()
