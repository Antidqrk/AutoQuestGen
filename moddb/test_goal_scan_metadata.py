"""Natural action goals need original labels and criteria, not inventory icons."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A


class GoalScanMetadataTests(unittest.TestCase):
    def test_displayed_action_criteria_are_preserved_separately_from_item_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mods = root / 'mods'
            mods.mkdir()
            (root / 'minecraftinstance.json').write_text('{"minecraftVersion":"1.20.1"}')
            criteria = {'guardian': {'trigger': 'minecraft:player_killed_entity', 'conditions': {
                'entity': [{'condition': 'minecraft:entity_properties', 'entity': 'this',
                            'predicate': {'type': 'demo:doppleganger', 'nbt': '{hardMode:1b}'}}]}}}
            advancement = {'parent': 'demo:main/root', 'display': {
                'title': {'translate': 'advancement.demo.guardian'},
                'description': {'translate': 'advancement.demo.guardian.desc'},
                'icon': {'item': 'demo:trophy'}}, 'criteria': criteria,
                'requirements': [['guardian']]}
            language = {'entity.demo.doppleganger': 'Guardian of the Garden',
                        'entity.demo.ghost': 'Dormant Guardian',
                        'entity.demo.template': 'Variant %s',
                        'dimension.demo.garden': 'The Secret Garden',
                        'dimension.demo.unregistered': 'Unregistered Garden',
                        'structure.demo.surface.arena': 'Garden Arena',
                        'structure.demo.unregistered': 'Imaginary Arena',
                        'item.demo.trophy': 'Trophy',
                        'advancement.demo.guardian': 'The Greater Trial',
                        'advancement.demo.guardian.desc': 'Defeat the empowered Guardian.'}
            png = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00\x00\x00\x10' * 2
            with zipfile.ZipFile(mods / 'demo.jar', 'w') as z:
                z.writestr('META-INF/mods.toml', '[[mods]]\nmodId="demo"\ndisplayName="Demo"\n')
                z.writestr('assets/demo/lang/en_us.json', json.dumps(language))
                z.writestr('assets/demo/models/item/trophy.json', json.dumps({
                    'parent': 'minecraft:item/generated', 'textures': {'layer0': 'demo:item/trophy'}}))
                z.writestr('assets/demo/textures/item/trophy.png', png)
                z.writestr('data/demo/advancements/main/guardian.json', json.dumps(advancement))
                z.writestr('data/demo/advancements/main/hidden.json', json.dumps({'criteria': {'code': {'trigger': 'minecraft:impossible'}}}))
                z.writestr('data/demo/dimension/garden.json', '{}')
                z.writestr('data/demo/worldgen/structure/surface/arena.json', '{}')
            profile = {'matched': True, 'message': 'fixture verified profile',
                       'absent_ids': [], 'excluded_entities': ['demo:ghost']}
            logs = []
            with patch.object(A, 'DATA_DIR', root), \
                    patch.object(A, '_vanilla_items_from_client', return_value={'minecraft:book'}), \
                    patch('quest_registry_profile.match_profile', return_value=profile):
                scan = A.scan_mods(mods, 100, logs.append)
                cached = A.scan_mods(mods, 100, logs.append)
            self.assertTrue(any('cache hit' in line for line in logs))
            self.assertEqual({'demo:doppleganger': 'Guardian of the Garden'}, scan['entity_names'])
            self.assertNotIn('demo:ghost', scan['entities']['demo'])
            self.assertEqual({'demo:garden': 'The Secret Garden'}, scan['dimension_names'])
            self.assertEqual({'demo:surface/arena': 'Garden Arena'}, scan['structure_names'])
            self.assertEqual({'demo:main/guardian', 'demo:main/hidden'}, scan['advancements']['demo'])
            info = scan['advancement_info']['demo:main/guardian']
            self.assertEqual(criteria, info['criteria'])
            self.assertEqual([['guardian']], info['requirements'])
            self.assertEqual('Defeat the empowered Guardian.', info['description'])
            self.assertEqual('demo:trophy', info['icon'])
            self.assertEqual('', scan['progression']['demo'][0]['desc'])
            for key in ('entity_names', 'dimension_names', 'structure_names', 'advancements', 'advancement_info'):
                self.assertEqual(scan[key], cached[key])

    def test_singular_advancement_records_and_unknown_translations_stay_literal(self):
        rows = {'demo:trial': {'display': {'title': {'text': 'Literal Trial'},
                                         'description': {'translate': 'missing.key'},
                                         'icon': {'id': 'demo:medal'}},
                                'criteria': {'code': {'trigger': 'demo:custom_action'}}}}
        catalog, info = A._goal_advancement_metadata(rows, {})
        self.assertEqual({'demo:trial'}, catalog['demo'])
        self.assertEqual('Literal Trial', info['demo:trial']['title'])
        self.assertEqual('', info['demo:trial']['description'])
        self.assertEqual('demo:medal', info['demo:trial']['icon'])
        self.assertEqual('demo:custom_action', info['demo:trial']['criteria']['code']['trigger'])


if __name__ == '__main__':
    unittest.main()
