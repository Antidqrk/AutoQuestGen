"""Client item tags must survive mod additions, replacements and cache reloads."""
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


class VanillaTagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mods = self.root / 'Instances' / 'pack' / 'mods'
        self.mods.mkdir(parents=True)
        (self.mods.parent / 'minecraftinstance.json').write_text('{"minecraftVersion":"1.20.1"}')

    def client(self, version='1.20.1', directory='items'):
        path = self.root / 'Install' / 'versions' / version / (version + '.jar')
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as zf:
            for item in ('diamond', 'oak_sapling', 'birch_sapling'):
                zf.writestr('assets/minecraft/models/item/' + item + '.json', '{}')
            zf.writestr('assets/minecraft/lang/en_us.json', json.dumps({
                'item.minecraft.' + item: item for item in ('diamond', 'oak_sapling', 'birch_sapling')}))
            zf.writestr('data/minecraft/tags/' + directory + '/saplings.json', json.dumps({
                'values': ['minecraft:oak_sapling', 'minecraft:birch_sapling']}))
            zf.writestr('data/minecraft/tags/' + directory + '/nested.json', json.dumps({
                'values': ['#minecraft:saplings']}))
            zf.writestr('data/minecraft/tags/blocks/saplings.json', json.dumps({
                'values': ['minecraft:wall_torch']}))
        return path

    def mod(self, replace=False):
        with zipfile.ZipFile(self.mods / 'demo.jar', 'w') as zf:
            zf.writestr('META-INF/mods.toml', '[[mods]]\nmodId="demo"\ndisplayName="Demo"\n')
            zf.writestr('assets/demo/lang/en_us.json', '{"item.demo.sapling":"Sapling"}')
            zf.writestr('assets/demo/models/item/sapling.json', json.dumps({
                'parent': 'minecraft:item/generated', 'textures': {'layer0': 'demo:item/sapling'}}))
            zf.writestr('assets/demo/textures/item/sapling.png', b'png')
            zf.writestr('data/minecraft/tags/items/saplings.json', json.dumps({
                'replace': replace, 'values': ['demo:sapling']}))

    def test_matching_client_tags_merge_with_mods_and_survive_cached_scan(self):
        self.client(); self.mod()
        with patch.object(A, 'DATA_DIR', self.root), patch.object(A, '_COLOUR_SIBLINGS', {}):
            scan = A.scan_mods(self.mods, 100, lambda _: None)
            wanted = {'minecraft:oak_sapling', 'minecraft:birch_sapling', 'demo:sapling'}
            self.assertEqual(wanted, scan['tags']['minecraft:saplings'])
            self.assertEqual(wanted, scan['tags']['minecraft:nested'])
            replay = A.scan_mods(self.mods, 100, lambda _: None)
            self.assertEqual(scan['tags'], replay['tags'])
            # Recreate the previous schema's incomplete cache; a version9
            # scanner must rebuild instead of replaying its mod-only members.
            path = A._scan_cache_path(self.mods)
            with patch.object(A, '_SCAN_CACHE_VERSION', 8):
                previous_key = A._scan_cache_key(self.mods, 100)
            scan['tags']['minecraft:saplings'] = {'demo:sapling'}
            path.write_bytes(pickle.dumps({'key': previous_key, 'scan': scan}))
            restored = A.scan_mods(self.mods, 100, lambda _: None)
            self.assertEqual(wanted, restored['tags']['minecraft:saplings'])

    def test_mod_replace_removes_vanilla_members_before_nested_resolution(self):
        self.client(); self.mod(replace=True)
        with patch.object(A, 'DATA_DIR', self.root), patch.object(A, '_COLOUR_SIBLINGS', {}):
            scan = A.scan_mods(self.mods, 100, lambda _: None)
        self.assertEqual({'demo:sapling'}, scan['tags']['minecraft:saplings'])
        self.assertEqual({'demo:sapling'}, scan['tags']['minecraft:nested'])

    def test_wrong_client_cannot_supply_tags_and_new_matching_client_invalidates_cache(self):
        self.client('1.21.1', 'item'); self.mod()
        with patch.object(A, 'DATA_DIR', self.root), patch.object(A, '_COLOUR_SIBLINGS', {}):
            before = A._scan_cache_key(self.mods, 100)
            scan = A.scan_mods(self.mods, 100, lambda _: None)
            self.assertEqual({'demo:sapling'}, scan['tags']['minecraft:saplings'])
            self.assertNotIn('minecraft:nested', scan['tags'])
            exact = self.client()
            self.assertNotEqual(before, A._scan_cache_key(self.mods, 100))
            scan = A.scan_mods(self.mods, 100, lambda _: None)
            self.assertIn('minecraft:oak_sapling', scan['tags']['minecraft:saplings'])
            key = A._scan_cache_key(self.mods, 100)
            with zipfile.ZipFile(exact, 'a') as zf:
                zf.writestr('data/minecraft/tags/items/extra.json', '{"values":["minecraft:diamond"]}')
            self.assertNotEqual(key, A._scan_cache_key(self.mods, 100))

    def test_singular_item_tags_and_missing_nested_tag_semantics(self):
        self.client('1.21.1', 'item')
        (self.mods.parent / 'minecraftinstance.json').write_text('{"minecraftVersion":"1.21.1"}')
        docs = []
        A._vanilla_items_from_client(self.mods, item_tags=docs)
        docs += [('demo:optional', {'values': ['minecraft:diamond', {'id': '#missing:tag', 'required': False}]}),
                 ('demo:required', {'values': ['minecraft:diamond', '#missing:tag']}),
                 ('demo:cycle', {'values': ['#demo:cycle']})]
        tags = A._resolve_item_tag_documents(docs)
        self.assertEqual({'minecraft:oak_sapling', 'minecraft:birch_sapling'}, tags['minecraft:nested'])
        self.assertEqual({'minecraft:diamond'}, tags['demo:optional'])
        self.assertFalse(tags['demo:required'])
        self.assertFalse(tags['demo:cycle'])


if __name__ == '__main__':
    unittest.main()
