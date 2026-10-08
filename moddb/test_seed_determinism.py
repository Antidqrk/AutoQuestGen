"""A numbered seed must survive a new Python process and hash-table order."""
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import autoquestgen as A
from moddb.test_offline_story import scan_for


def probe():
    materials = ('copper', 'tin', 'iron', 'silver', 'gold', 'lead')
    families = ('crystal', 'gear', 'motor', 'coil', 'pump', 'processor')
    items = ['factory:' + material + '_' + family for material in materials for family in families]
    items += ['factory:unique_device_' + str(i) for i in range(45)]
    items += ['utility:' + material + '_crystal' for material in materials]
    recipes = {item: [('minecraft:crafting_shaped', frozenset({'minecraft:iron_ingot'}))] for item in items}
    recipes[items[0]] = {('minecraft:crafting_shaped', frozenset({'factory:tin_gear'})),
                        ('minecraft:crafting_shaped', frozenset({'factory:iron_gear'}))}
    scan = scan_for(items, recipes)
    scan['names'] = {item: A._pretty_name(item) for item in items}
    scan['vanilla'].update({'minecraft:diamond', 'minecraft:chest'})
    scan['structures'] = {'factory': {'factory:workshop'}}
    scan['struct_loot'] = {'factory:workshop': set(items)}
    scan['unobtainable'] = set()
    opts = dict(density='large', target=150, seed=47, reward='standard', progression='linear',
                creativity=.55, groups=True, aesthetic='lavish', mimic=False, layout='reference',
                vanilla_chapters=False, include_decor=False, collections=True, campaign=True,
                campaign_goal='', auto_chain=True, style_chapters=True, decor_art=True,
                quest_shape='auto', _scan=scan)
    doc = A.local_quest_doc(scan, ['factory', 'utility'], opts)
    raw = json.dumps(doc, sort_keys=True)
    cleaned = A.clean_doc(doc, scan, lambda _: None)
    chapters, groups, tables = A.build_chapters(cleaned, lambda _: None, opts)
    # Compare actual ordered SNBT text, not merely sets of quest IDs. This
    # includes goals, counts, prose, prerequisites, rewards, icons and maps.
    result = {'raw_doc': raw,
              'export': [(name, A.snbt_dumps(ch)) for name, ch in chapters],
              'groups': A.snbt_dumps(groups), 'tables': A.snbt_dumps(tables),
              'rank': A.rank_mod_items('factory', scan),
              'wheels': A._collection_wheels('factory', scan, set(), random.Random(47)),
              'reward_pool': A.reward_pool(scan),
              'recipe': A._one_recipe(items[0], scan),
              'structure_loot': A.structure_reward('factory:workshop', scan)}
    print(json.dumps(result, sort_keys=True))


class SeedDeterminismTests(unittest.TestCase):
    def test_full_book_and_export_are_identical_across_process_hash_seeds(self):
        outputs = []
        for hash_seed in ('1', '2'):
            env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONIOENCODING='utf-8')
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--probe'],
                                    cwd=ROOT, env=env, text=True, encoding='utf-8',
                                    capture_output=True, timeout=60, check=True)
            outputs.append(json.loads(result.stdout))
        self.assertTrue(outputs[0]['export'])
        self.assertEqual(outputs[0], outputs[1])

    def test_fresh_cached_and_switched_pack_restore_their_own_colour_facts(self):
        png = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00\x00\x00\x10' * 2
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, ids in (('colours', ['red_crystal', 'blue_crystal', 'green_crystal', 'dark_wand', 'machine']),
                              ('plain', ['dark_wand', 'machine'])):
                instance = root / name
                (instance / 'mods').mkdir(parents=True)
                (instance / 'minecraftinstance.json').write_text('{"minecraftVersion":"1.20.1"}')
                with zipfile.ZipFile(instance / 'mods/demo.jar', 'w') as z:
                    z.writestr('META-INF/mods.toml', '[[mods]]\nmodId="demo"\ndisplayName="Demo"\n')
                    z.writestr('assets/demo/lang/en_us.json', json.dumps({'item.demo.' + i: i for i in ids}))
                    for item in ids:
                        z.writestr('assets/demo/models/item/' + item + '.json', json.dumps({
                            'parent': 'minecraft:item/generated', 'textures': {'layer0': 'demo:item/' + item}}))
                        z.writestr('assets/demo/textures/item/' + item + '.png', png)
                        z.writestr('data/demo/recipes/' + item + '.json', json.dumps({
                            'type': 'minecraft:crafting_shapeless', 'ingredients': [{'item': 'minecraft:iron_ingot'}],
                            'result': {'item': 'demo:' + item}}))
            options = {'seed': 47, 'mimic': False, 'density': 'small', 'vanilla_chapters': False}
            logs = []
            with patch.object(A, 'DATA_DIR', root), patch.object(A, '_COLOUR_SIBLINGS', {}), \
                    patch.object(A, '_vanilla_items_from_client', return_value={'minecraft:iron_ingot', 'minecraft:book'}):
                first = A.scan_mods(root / 'colours/mods', 100, logs.append)
                first_doc = A.local_quest_doc(first, ['demo'], options)
                self.assertTrue(A._colour_variant('demo:red_crystal'))
                self.assertFalse(A._colour_variant('demo:dark_wand'))
                self.assertEqual(3, first['colour_siblings']['demo']['crystal'])
                second = A.scan_mods(root / 'plain/mods', 100, logs.append)
                self.assertFalse(A._colour_variant('demo:red_crystal'))
                # Generate from an older selected scan after another pack was scanned.
                self.assertEqual(first_doc, A.local_quest_doc(first, ['demo'], options))
                self.assertTrue(A._colour_variant('demo:red_crystal'))
                # New-process equivalent: discard globals before the cache hit.
                A._COLOUR_SIBLINGS.clear()
                cached = A.scan_mods(root / 'colours/mods', 100, logs.append)
                self.assertTrue(any('cache hit' in line for line in logs))
                self.assertTrue(A._colour_variant('demo:red_crystal'))
                self.assertEqual(first_doc, A.local_quest_doc(cached, ['demo'], options))
                A.local_quest_doc(second, ['demo'], options)
                self.assertFalse(A._colour_variant('demo:red_crystal'))


if __name__ == '__main__':
    if '--probe' in sys.argv:
        probe()
    else:
        unittest.main()
