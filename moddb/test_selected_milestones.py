"""Selected-pack completion joins substantial achievements, not deep filler."""
import copy
import unittest

from quest_campaign import _campaign_milestones, apply_campaign
from moddb.test_campaign import book_for, quests
from moddb.test_natural_goal_campaign import botania_scan
from moddb.test_offline_story import scan_for


class SelectedMilestoneTests(unittest.TestCase):
    def select(self, doc, scan):
        return _campaign_milestones([(q, c) for c in doc['chapters'] for q in c['quests']],
                                    scan, set(scan['items']))

    def test_deep_high_tier_cosmetics_and_raw_components_never_become_milestones(self):
        bad = ['dyenamics:spring_green_dye', 'mcwbiomesoplenty:willow_bridge_pier',
               'extradisks:raw_withering_processor', 'create:spring_green_seat',
               'rftoolsutility:crafter2', 'demo:reactor_casing',
               'demo:storage_upgrade', 'demo:unknown_deep_item',
               'travelersbackpack:backpack_tank', 'ars_nouveau:novice_spell_book',
               'constructionwand:stone_wand', 'additional_lights:soul_wand',
               'crystalix:crystalix_wand', 'angelring:diamond_ring']
        scan = scan_for(bad)
        scan['tier'] = dict.fromkeys(bad, 99)
        doc = book_for(bad)
        for i, q in enumerate(quests(doc)):
            q['dependencies'] = ['q' + str(i - 1)] if i else []
        doc['chapters'][4]['quests'][0]['title'] = 'More recipes in the same station'
        before = copy.deepcopy(doc)
        self.assertEqual(self.select(doc, scan), [])
        self.assertEqual(doc, before)

    def test_confirmed_capstones_outweigh_deeper_minor_items_from_the_same_mod(self):
        good = ['create:train_controls', 'mekanism:mekasuit_bodyarmor',
                'ae2:crafting_storage_64k', 'rftoolsutility:crafter3']
        bad = ['create:spring_green_seat', 'mekanism:reactor_casing',
               'ae2:engineering_processor', 'rftoolsutility:crafter2']
        scan = scan_for(good + bad)
        scan['tier'] = dict.fromkeys(bad, 99)
        doc = book_for(good + bad)
        for i, q in enumerate(quests(doc)[len(good):], len(good)):
            q['dependencies'] = ['q' + str(i - 1)]
        self.assertEqual({row[4]['item'] for row in self.select(doc, scan)}, set(good))

    def test_bounded_equipment_names_keep_real_equipment_without_substring_matches(self):
        good = ['demo:advanced_controller', 'demo:reactor', 'demo:archmage_spell_book',
                'demo:flight_amulet', 'demo:diamond_backpack']
        bad = ['demo:spring_green_dye', 'demo:spring_green_seat', 'demo:wand_core',
               'demo:controller_frame']
        # Each mod contributes one achievement, so evaluate the candidates
        # independently rather than mistaking ranking for eligibility.
        for item in good + bad:
            with self.subTest(item=item):
                chosen = self.select(book_for([item]), scan_for([item]))
                self.assertEqual(bool(chosen), item in good)

    def test_substantive_authored_project_replaces_generic_manual_orientation(self):
        scan = scan_for(['minecolonies:blockhutbuilder'])
        qs = [{'id': 'read', 'title': 'Read the guide', 'dependencies': [],
               'tasks': [{'type': 'checkmark', '_guide_mod': 'minecolonies',
                          '_guide_ref': 'check:minecolonies:read_the_guide'}]},
              {'id': 'build', 'title': 'A Colony That Supports Its Builders', 'dependencies': ['read'],
               'tasks': [{'type': 'checkmark', '_guide_mod': 'minecolonies',
                          '_guide_ref': 'check:minecolonies:self_sufficient_colony'}]},
              {'id': 'tips', 'title': 'More recipes in the same station', 'dependencies': ['build'],
               'tasks': [{'type': 'checkmark', '_guide_mod': 'minecolonies',
                          '_guide_ref': 'check:minecolonies:more_recipes'}]}]
        doc = {'chapters': [{'id': 'colony', 'quests': qs}]}
        self.assertEqual([row[1] for row in self.select(doc, scan)], ['build'])

    def test_major_encounters_and_expeditions_do_not_select_ordinary_mobs_or_small_houses(self):
        tasks = [({'type': 'kill', 'entity': 'twilightforest:lich'}, True),
                 ({'type': 'kill', 'entity': 'creeperoverhaul:hills_creeper'}, False),
                 ({'type': 'dimension', 'dimension': 'twilightforest:twilight_forest'}, True),
                 ({'type': 'structure', 'structure': 'cataclysm:acropolis'}, True),
                 ({'type': 'structure', 'structure': 'dungeons_arise:small_prairie_house'}, False),
                 ({'type': 'structure', 'structure': 'railcraft:geode'}, False),
                 ({'type': 'advancement', 'advancement': 'demo:root'}, False)]
        for task, eligible in tasks:
            with self.subTest(task=task):
                target = next(value for key, value in task.items() if key != 'type')
                scan = scan_for([target.split(':')[0] + ':placeholder'])
                doc = {'chapters': [{'quests': [{'id': 'encounter', 'title': 'Explore',
                                               'tasks': [task], 'dependencies': []}]}]}
                self.assertEqual(bool(self.select(doc, scan)), eligible)

    def test_selected_summary_skips_minor_mod_but_retains_all_its_quests(self):
        scan = botania_scan()
        extra = scan_for(['dyenamics:spring_green_dye', 'create:train_controls'])
        scan['mods'].extend(extra['mods'])
        scan['items'].update(extra['items'])
        scan['craftable'].update(extra['craftable'])
        scan['tier'] = {'dyenamics:spring_green_dye': 99}
        doc = book_for(['dyenamics:spring_green_dye', 'create:train_controls'])
        before = copy.deepcopy(doc)
        result = apply_campaign(doc, scan, [], 'kill Gaia Guardian', scope='selected')
        self.assertEqual(result['campaign']['branch_milestone_ids'], ['q1'])
        byid = {q['id']: q for q in quests(result)}
        self.assertEqual(byid['q0'], before['chapters'][0]['quests'][0])
        self.assertEqual(byid['q1'], before['chapters'][1]['quests'][0])


if __name__ == '__main__':
    unittest.main()
