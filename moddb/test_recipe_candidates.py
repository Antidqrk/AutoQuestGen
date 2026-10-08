"""Generic recipe tags should not impose exotic materials when basics are valid."""
import copy
import unittest

from moddb.test_offline_story import scan_for
from quest_campaign import _objective_route, _recipe_ingredient_choices, _known


class RecipeCandidateTests(unittest.TestCase):
    def lexica_route(self, ingredient, members):
        items = ['botania:lexicon', 'minecraft:book', 'minecraft:oak_sapling',
                 'minecraft:acacia_sapling', 'aether:golden_oak_sapling']
        scan = scan_for(items, {'botania:lexicon': [('minecraft:crafting_shapeless',
                                                   [ingredient, 'minecraft:book'])]})
        scan['tags'] = {'minecraft:saplings': set(members), 'aether:saplings': set(members)}
        before = copy.deepcopy(scan)
        target = dict(kind='item', target_id='botania:lexicon', mod='botania', label='Lexica Botania')
        root, route = _objective_route(target, scan, include_recipes=True)
        self.assertEqual(scan, before)
        return route[root]['parents'], route

    def test_confirmed_common_vanilla_sapling_precedes_exotic_or_variant_choices(self):
        parents, route = self.lexica_route('#minecraft:saplings', [
            'aether:golden_oak_sapling', 'minecraft:acacia_sapling', 'minecraft:oak_sapling'])
        self.assertEqual(set(parents), {'minecraft:book', 'minecraft:oak_sapling'})
        self.assertNotIn('aether:golden_oak_sapling', route)

    def test_mod_exclusive_tag_never_gains_a_vanilla_substitute(self):
        for tag in ('#aether:saplings', '#minecraft:saplings'):
            with self.subTest(tag=tag):
                parents, route = self.lexica_route(tag, ['aether:golden_oak_sapling'])
                self.assertEqual(set(parents), {'minecraft:book', 'aether:golden_oak_sapling'})
                self.assertNotIn('minecraft:oak_sapling', route)

    def test_exact_mod_ingredient_is_unchanged_by_available_common_options(self):
        parents, route = self.lexica_route('aether:golden_oak_sapling', ['minecraft:oak_sapling'])
        self.assertEqual(set(parents), {'minecraft:book', 'aether:golden_oak_sapling'})
        self.assertNotIn('minecraft:oak_sapling', route)

    def test_unverified_or_unobtainable_tag_member_is_not_a_candidate(self):
        scan = scan_for(['aether:golden_oak_sapling', 'minecraft:oak_sapling'])
        scan['tags'] = {'minecraft:saplings': {'aether:golden_oak_sapling', 'minecraft:oak_sapling',
                                              'minecraft:birch_sapling'}}
        scan['unobtainable'] = {'minecraft:oak_sapling'}
        self.assertEqual(_recipe_ingredient_choices('#minecraft:saplings', scan, _known(scan)),
                         ['aether:golden_oak_sapling'])


if __name__ == '__main__':
    unittest.main()
