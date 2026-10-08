"""Creative-tab artwork must not displace actual tablets or work tables."""
import json
from pathlib import Path
import pickle
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00\x00\x00\x10' * 2


class TabMarkerTests(unittest.TestCase):
    def test_real_tablets_and_tables_are_not_creative_tab_icons(self):
        for item in ('botania:mana_tablet', 'minecraft:crafting_table', 'demo:alchemy_table'):
            with self.subTest(item=item):
                self.assertLess(A._junk_score(item), 3)
        for item in ('demo:creative_tab', 'demo:tools_tab', 'demo:tab_icon', 'demo:mod_icon'):
            with self.subTest(item=item):
                self.assertEqual(3, A._junk_score(item))

    def test_capped_real_scan_keeps_tablet_and_rejects_ui_picture_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'mods').mkdir()
            (root / 'minecraftinstance.json').write_text('{"minecraftVersion":"1.20.1"}')
            ids = ('mana_tablet', 'alchemy_table', 'ornament', 'tools_tab', 'tab_icon')
            with zipfile.ZipFile(root / 'mods/demo.jar', 'w') as jar:
                jar.writestr('META-INF/mods.toml', '[[mods]]\nmodId="demo"\ndisplayName="Demo"\n')
                jar.writestr('assets/demo/lang/en_us.json', json.dumps({'item.demo.' + i: i for i in ids}))
                for item in ids:
                    jar.writestr('assets/demo/models/item/' + item + '.json', json.dumps({
                        'parent': 'minecraft:item/generated', 'textures': {'layer0': 'demo:item/' + item}}))
                    jar.writestr('assets/demo/textures/item/' + item + '.png', PNG)
                    jar.writestr('data/demo/recipes/' + item + '.json', json.dumps({
                        'type': 'minecraft:crafting_shapeless', 'ingredients': [{'item': 'minecraft:iron_ingot'}],
                        'result': {'item': 'demo:' + item}}))
            with patch.object(A, 'DATA_DIR', root), patch.object(A, '_COLOUR_SIBLINGS', {}), \
                    patch.object(A, '_vanilla_items_from_client', return_value={'minecraft:iron_ingot'}):
                scan = A.scan_mods(root / 'mods', 2, lambda _: None)
                self.assertEqual({'demo:mana_tablet', 'demo:alchemy_table'}, scan['items']['demo'])
                self.assertEqual(scan['items']['demo'], scan['craftable']['demo'])
                cached = A.scan_mods(root / 'mods', 2, lambda _: None)
                self.assertEqual(scan['items'], cached['items'])
                # Simulate an older capped inventory that lost the tablet.
                # The schema bump must force a rebuild even with unchanged JARs.
                path = A._scan_cache_path(root / 'mods')
                old = pickle.loads(path.read_bytes())
                with patch.object(A, '_SCAN_CACHE_VERSION', A._SCAN_CACHE_VERSION - 1):
                    old['key'] = A._scan_cache_key(root / 'mods', 2)
                old['scan']['items']['demo'] = {'demo:ornament', 'demo:alchemy_table'}
                path.write_bytes(pickle.dumps(old))
                rebuilt = A.scan_mods(root / 'mods', 2, lambda _: None)
                self.assertIn('demo:mana_tablet', rebuilt['items']['demo'])


if __name__ == '__main__':
    unittest.main()
