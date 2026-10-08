"""Registry absence is pack-specific evidence, never a global mod blacklist."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A
import quest_registry_profile as R

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00\x00\x00\x10' * 2


class RuntimeProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'mods').mkdir()
        self.metadata = {'baseModLoader': {'minecraftVersion': '1.21.1', 'name': 'neoforge-21.1.1'}}
        self.write('minecraftinstance.json', json.dumps(self.metadata))
        self.jar()
        self.write('config/mod.toml', 'register_ghost=false')
        self.write('kubejs/startup_scripts/items.js', '// fixture')
        self.write('kubejs/server_scripts/recipes.js', '// fixture')
        self.profile_file = self.root / R.PROFILE_FILE
        self.save_profile()

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8')
        return target

    def jar(self, marker='original'):
        with zipfile.ZipFile(self.root / 'mods/demo.jar', 'w') as z:
            z.writestr('META-INF/neoforge.mods.toml', '[[mods]]\nmodId="demo"\ndisplayName="Demo"\nversion="1"\n')
            z.writestr('marker.txt', marker)
            z.writestr('assets/demo/lang/en_us.json', json.dumps({
                'item.demo.live_crystal': 'Live Crystal', 'item.demo.ghost_crystal': 'Ghost Crystal',
                'entity.demo.live_mob': 'Live Mob', 'entity.demo.ghost_mob': 'Ghost Mob'}))
            for name in ('live_crystal', 'ghost_crystal'):
                z.writestr('assets/demo/models/item/' + name + '.json', json.dumps({
                    'parent': 'minecraft:item/generated', 'textures': {'layer0': 'demo:item/' + name}}))
                z.writestr('assets/demo/textures/item/' + name + '.png', PNG)
                z.writestr('data/demo/recipe/' + name + '.json', json.dumps({
                    'type': 'minecraft:crafting_shapeless', 'ingredients': [{'item': 'minecraft:diamond'}],
                    'result': {'id': 'demo:' + name}}))

    def save_profile(self):
        row = dict(id='fixture', **R.pack_signature(self.root, '1.21.1'),
                   absent_ids=['demo:ghost_crystal'], excluded_entities=['demo:ghost_mob'])
        self.profile_file.write_text(json.dumps({'schema': 1, 'profiles': [row]}), encoding='utf-8')

    def match(self):
        return R.match_profile(self.root, '1.21.1', self.profile_file)

    def test_exact_match_removes_only_absent_items_entities_and_rewards(self):
        profile = self.match()
        self.assertTrue(profile['matched'])
        scan = {'items': {'demo': {'demo:live_crystal', 'demo:ghost_crystal'}},
                'craftable': {'demo': {'demo:live_crystal', 'demo:ghost_crystal'}},
                'entities': {'demo': {'demo:live_mob', 'demo:ghost_mob'}},
                'vanilla': {'minecraft:diamond'}, 'unobtainable': []}
        self.assertEqual(1, R.apply_profile(scan, profile))
        self.assertEqual({'demo:live_crystal'}, scan['items']['demo'])
        self.assertEqual({'demo:live_mob'}, scan['entities']['demo'])
        self.assertIn('demo:live_crystal', A.reward_pool(scan))
        self.assertNotIn('demo:ghost_crystal', A.reward_pool(scan))
        self.assertNotIn('demo:ghost_crystal', A.payout_lanes(scan).get('demo', []))
        doc = {'chapters': [{'id': 'ch', 'quests': [
            {'id': 'live', 'icon': 'demo:ghost_crystal', 'tasks': [{'type': 'item', 'item': 'demo:live_crystal'}],
             'rewards': [{'type': 'item', 'item': 'demo:ghost_crystal'}]},
            {'id': 'ghost', 'tasks': [{'type': 'item', 'item': 'demo:ghost_crystal'}]},
            {'id': 'fake_mob', 'tasks': [{'type': 'kill', 'entity': 'demo:ghost_mob'}]},
            {'id': 'real_mob', 'tasks': [{'type': 'kill', 'entity': 'demo:live_mob'}]}]}]}
        clean = A.clean_doc(doc, scan, lambda _: None)
        quests = {q['id']: q for ch in clean['chapters'] for q in ch['quests']}
        self.assertEqual({'live', 'real_mob'}, set(quests))
        self.assertNotIn('icon', quests['live'])
        self.assertEqual([], quests['live']['rewards'])

    def test_changed_config_and_each_script_disable_all_exclusions(self):
        for path in ('config/mod.toml', 'kubejs/startup_scripts/items.js', 'kubejs/server_scripts/recipes.js'):
            with self.subTest(path=path):
                target = self.root / path
                original = target.read_text()
                target.write_text(original + '\nchanged')
                self.assertFalse(self.match()['matched'])
                target.write_text(original)
        self.assertTrue(self.match()['matched'])

    def test_changed_jar_bytes_same_name_and_version_disable_profile(self):
        self.jar('modified')
        self.assertFalse(self.match()['matched'])

    def test_added_mod_disables_profile_before_expensive_signature(self):
        (self.root / 'mods/companion.jar').write_bytes(b'not needed for early rejection')
        with patch.object(R, 'pack_signature', side_effect=AssertionError('must reject by mod set')):
            self.assertFalse(self.match()['matched'])

    def test_changed_minecraft_or_loader_disables_profile(self):
        self.assertFalse(R.match_profile(self.root, '1.20.1', self.profile_file)['matched'])
        self.metadata['baseModLoader']['name'] = 'neoforge-different'
        self.write('minecraftinstance.json', json.dumps(self.metadata))
        self.assertFalse(self.match()['matched'])

    def test_generated_quests_and_world_progress_do_not_change_source_signature(self):
        self.write('config/ftbquests/quests/chapters/generated.snbt', '{}')
        self.write('world/playerdata/progress.dat', 'progress')
        self.write('logs/latest.log', 'log')
        self.assertTrue(self.match()['matched'])

    def test_mismatch_keeps_same_item_and_entity_available_in_other_pack(self):
        self.write('config/mod.toml', 'register_ghost=true')
        scan = {'items': {'demo': {'demo:ghost_crystal'}}, 'entities': {'demo': {'demo:ghost_mob'}}}
        R.apply_profile(scan, self.match())
        self.assertEqual({'demo:ghost_crystal'}, scan['items']['demo'])
        self.assertEqual({'demo:ghost_mob'}, scan['entities']['demo'])

    def test_removed_config_needs_its_own_verified_profile_and_never_unions_exclusions(self):
        old = json.loads(self.profile_file.read_text())['profiles'][0]
        (self.root / 'config/mod.toml').unlink()
        self.assertFalse(self.match()['matched'])
        # A second runtime capture can have different registrations, even with
        # identical mod JARs. Each exact signature owns only its own evidence.
        new = dict(id='fixture-default-config', **R.pack_signature(self.root, '1.21.1'),
                   absent_ids=['demo:live_crystal'], excluded_entities=['demo:live_mob'])
        self.profile_file.write_text(json.dumps({'schema': 1, 'profiles': [old, new]}))
        profile = self.match()
        self.assertEqual('fixture-default-config', profile['profile_id'])
        scan = {'items': {'demo': {'demo:live_crystal', 'demo:ghost_crystal'}},
                'entities': {'demo': {'demo:live_mob', 'demo:ghost_mob'}}}
        R.apply_profile(scan, profile)
        self.assertEqual({'demo:ghost_crystal'}, scan['items']['demo'])
        self.assertEqual({'demo:ghost_mob'}, scan['entities']['demo'])
        self.assertNotIn('demo:live_crystal', A.reward_pool(scan))
        self.write('config/mod.toml', 'register_ghost=false')
        profile = self.match()
        self.assertEqual('fixture', profile['profile_id'])
        self.assertEqual(['demo:ghost_crystal'], profile['absent_ids'])
        self.write('config/mod.toml', 'unverified-third-state')
        self.assertFalse(self.match()['matched'])

    def test_profile_resource_resolves_from_source_and_frozen_data_root(self):
        with patch.object(R.sys, '_MEIPASS', str(self.root), create=True):
            self.assertEqual(self.profile_file, R.profile_path())
            self.assertTrue(R.match_profile(self.root, '1.21.1')['matched'])
        expected = Path(R.__file__).resolve().parent / R.PROFILE_FILE
        self.assertEqual(expected, R.profile_path())

    def test_scanner_filters_before_cache_and_invalidates_on_config_or_profile_changes(self):
        logs = []
        with patch.object(R, 'profile_path', return_value=self.profile_file), \
                patch.object(A, 'DATA_DIR', self.root), \
                patch.object(A, '_vanilla_items_from_client', return_value={'minecraft:diamond'}):
            scan = A.scan_mods(self.root / 'mods', 100, logs.append)
            self.assertTrue(scan['runtime_item_profile']['matched'])
            self.assertEqual({'demo:live_crystal'}, scan['items']['demo'])
            self.assertEqual({'demo:live_mob'}, scan['entities']['demo'])
            cached = A.scan_mods(self.root / 'mods', 100, logs.append)
            self.assertEqual(scan['items'], cached['items'])
            self.assertTrue(any('cache hit' in line for line in logs))
            key = A._scan_cache_key(self.root / 'mods', 100)
            self.write('config/mod.toml', 'register_ghost=true')
            self.assertNotEqual(key, A._scan_cache_key(self.root / 'mods', 100))
            changed = A.scan_mods(self.root / 'mods', 100, logs.append)
            self.assertFalse(changed['runtime_item_profile']['matched'])
            self.assertIn('demo:ghost_crystal', changed['items']['demo'])
            self.assertIn('demo:ghost_mob', changed['entities']['demo'])
            key = A._scan_cache_key(self.root / 'mods', 100)
            self.save_profile()
            self.assertNotEqual(key, A._scan_cache_key(self.root / 'mods', 100))

    def test_malformed_profile_is_a_safe_no_match(self):
        for value in ('[]', '{"schema":1,"profiles":null}', '{not json'):
            self.profile_file.write_text(value)
            self.assertFalse(self.match()['matched'])

    def test_release_stages_helper_and_bundles_profile_data(self):
        import build_release
        self.assertIn('quest_registry_profile.py', build_release.ROOT_SHIP)
        self.assertIn(R.PROFILE_FILE, build_release.ROOT_SHIP)
        self.assertIn("('runtime_item_profiles.json', '.')", build_release.SPEC)

    def test_bundled_diagnostic_manifests_reproduce_their_exact_signatures(self):
        document = json.loads((Path(R.__file__).parent / R.PROFILE_FILE).read_text(encoding='utf-8'))
        signatures = [row['signature'] for row in document['profiles']]
        self.assertEqual(len(signatures), len(set(signatures)))
        for row in document['profiles']:
            if 'signature_inputs' not in row:
                continue  # Historical profiles predate per-file diagnostics.
            with self.subTest(profile=row['id']):
                facts = row['signature_inputs']
                self.assertEqual(row['signature'], R._hash_bytes(R._canonical(facts)))
                self.assertEqual(row['jar_count'], len(facts['jars']))
                self.assertEqual(row['source_file_count'], len(facts['sources']))
                self.assertTrue(all(not Path(name).is_absolute() and '..' not in Path(name).parts
                                    for name, _digest in facts['sources']))


if __name__ == '__main__':
    unittest.main()
