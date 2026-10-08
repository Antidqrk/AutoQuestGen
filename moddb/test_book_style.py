"""Sidebar reorganization is presentation, never a quest-graph rewrite."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A
from quest_book_style import (GROUP_ORDER, chapter_presentation, group_art_theme,
                              group_for_chapter, group_presentation, prepare_book)


def chapter(key, title, item, group='Expansion', count=1, **extra):
    return dict(id=key, title=title, group=group, quests=[
        {'id': key+'_q', 'title': title+' objective', 'x': -4.5, 'y': 3.0, 'size': 1.8,
         'tasks': [{'id': key+'_task', 'type': 'item', 'item': item, 'count': count}],
         'dependencies': [], 'rewards': [{'id': key+'_reward', 'type': 'xp', 'xp': 70}]}], **extra)


class BookStyleTests(unittest.TestCase):
    def test_classic_is_an_independent_unchanged_copy(self):
        doc = {'chapters': [chapter('b', 'New Horizons', 'botania:mana_pool')]}
        result = prepare_book(doc)
        self.assertEqual(doc, result)
        result['chapters'][0]['quests'][0]['tasks'][0]['count'] = 99
        self.assertEqual(doc['chapters'][0]['quests'][0]['tasks'][0]['count'], 1)

    def test_popular_mod_chapters_land_in_meaningful_compact_categories(self):
        cases = [('Botania', 'botania:mana_pool', 'Magic'), ('AE2', 'ae2:controller', 'Storage'),
                 ('Pipes', 'pipez:item_pipe', 'Logistics'), ('Create', 'create:mechanical_press', 'Tech'),
                 ('Powah', 'powah:reactor_basic', 'Power'), ('Silent Gear', 'silentgear:blueprint', 'Tools and Gear'),
                 ('Mystical Agriculture', 'mysticalagriculture:infusion_altar', 'Resources'),
                 ('Croptopia', 'croptopia:tomato', 'Nature and Food'),
                 ('The Twilight Forest', 'twilightforest:magic_map', 'Exploration'),
                 ('Chipped', 'chipped:carpenters_table', 'Building and Utilities')]
        doc = {'chapters': [chapter(str(i), title, item) for i, (title, item, _) in enumerate(cases)]}
        before = copy.deepcopy(doc)
        result = prepare_book(doc, 'immersive')
        by_id = {c['id']: c for c in result['chapters']}
        for i, (_, _, expected) in enumerate(cases):
            self.assertEqual(by_id[str(i)]['group'], expected)
            self.assertEqual(by_id[str(i)]['quests'], before['chapters'][i]['quests'])
        self.assertEqual(doc, before)
        order = [GROUP_ORDER.index(c['group']) for c in result['chapters']]
        self.assertEqual(order, sorted(order))
        self.assertNotIn('Expansion', {c['group'] for c in result['chapters']})

    def test_specific_subchapter_beats_broad_mod_category(self):
        for title, item, group in [('Mekanism Reactors', 'mekanism:ultimate_control_circuit', 'Power'),
                                   ('Apotheosis Gear', 'apotheosis:gem', 'Tools and Gear'),
                                   ('Basic Logistics', 'minecraft:hopper', 'Logistics')]:
            self.assertEqual(group_for_chapter(chapter('x', title, item)), group)
        self.assertEqual(group_for_chapter(chapter('shop', 'Supply Shop', 'minecraft:emerald', 'Shop',
                                                  _shop='autoquestgen')), 'Shop')

    def test_scanned_categories_classify_new_mods_without_hardcoded_names(self):
        scan = {'mods': [{'mod_id': 'newmagic', 'name': 'New Magic', 'category': 'magic'},
                         {'mod_id': 'newworld', 'name': 'New World', 'category': 'world'}]}
        self.assertEqual(group_for_chapter(chapter('a', 'New Horizons', 'newmagic:altar'), scan), 'Magic')
        self.assertEqual(group_for_chapter(chapter('b', 'The Frontier', 'newworld:portal'), scan), 'Exploration')

    def test_vague_generated_titles_are_replaced_by_actual_contents(self):
        scan = {'mods': [{'mod_id': 'botania', 'name': 'Botania', 'category': 'magic'},
                         {'mod_id': 'occultism', 'name': 'Occultism', 'category': 'magic'}]}
        c = chapter('a', '&5&lNew Horizons II', 'botania:mana_pool')
        c['quests'].append({'id': 'b', 'tasks': [{'type': 'item', 'item': 'occultism:chalk_white'}],
                            'dependencies': ['a_q']})
        result = prepare_book({'chapters': [c]}, 'immersive', scan)['chapters'][0]
        self.assertEqual(result['title'], 'Botania & Occultism')
        self.assertEqual(result['id'], c['id'])
        self.assertEqual(result['quests'], c['quests'])

    def test_goal_is_first_in_main_questline_and_sidebar_pointers_stay_valid(self):
        goal = chapter('goal', 'Guardian of Gaia', 'botania:gaia_ingot', 'Main Goal', _campaign=True, _campaign_role='goal')
        journey = chapter('journey', 'Your Modpack Journey', 'minecraft:book', 'Main Goal', _campaign_summary=True, _campaign_role='journey')
        intro = chapter('start', 'Overworld Beginnings', 'minecraft:oak_log', 'Vanilla', _vanilla=True)
        tech = chapter('tech', 'Create', 'create:andesite_alloy', 'Technology')
        goal['quests'][0]['dependencies'] = ['start_q']
        journey['quests'][0]['dependencies'] = ['goal_q', 'tech_q']
        doc = {'chapters': [tech, intro, journey, goal], 'campaign': {
            'goal_quest_ids': ['goal_q'], 'finale_quest_ids': ['journey_q'],
            'sidebar': {'group': 'Main Goal', 'after_group': 'Getting Started', 'chapter_ids': ['goal', 'journey']}}}
        result = prepare_book(doc, 'immersive')
        self.assertEqual(result['chapters'][0]['id'], 'goal')
        sidebar = result['campaign']['sidebar']
        self.assertEqual(sidebar['group'], 'Main Questline')
        self.assertEqual(sidebar['chapter_ids'][0], 'goal')
        self.assertEqual(set(sidebar['chapter_ids']), {'goal', 'journey', 'start'})
        self.assertEqual(result['campaign']['goal_quest_ids'], ['goal_q'])
        self.assertEqual(result['campaign']['finale_quest_ids'], ['journey_q'])
        before = {c['id']: c['quests'] for c in doc['chapters']}
        self.assertEqual({c['id']: c['quests'] for c in result['chapters']}, before)
        self.assertEqual(A._campaign_navigation(result), result['chapters'])

    def test_scenario_story_group_order_and_titles_are_preserved(self):
        doc = {'scenario': {'title': 'Return the Rain'}, 'campaign': {'kind': 'scenario',
               'sidebar': {'group': 'Return the Rain', 'chapter_ids': ['first', 'next']}},
               'chapters': [chapter('first', '01 · Bring Back Water', 'create:mechanical_pump', 'Return the Rain'),
                            chapter('next', '02 · Let the Garden Grow', 'botania:grass_seeds', 'Return the Rain')]}
        result = prepare_book(doc, 'immersive')
        self.assertEqual(result['campaign'], doc['campaign'])
        for original, styled in zip(doc['chapters'], result['chapters']):
            for key in ('id', 'title', 'group', 'quests'): self.assertEqual(styled[key], original[key])
            self.assertEqual(chapter_presentation(styled, styled['title'], 'd', 5), original['title'])

    def test_native_and_template_maps_keep_authored_titles_and_graphs(self):
        for flag in ('_native_reference', '_template'):
            with self.subTest(flag=flag):
                c = chapter('native', '&bAn Authored Chapter', 'botania:mana_pool', 'Magic', **{flag: True})
                c['images'] = [{'image': 'original:textures/header.png', 'x': 2.5, 'width': 9}]
                result = prepare_book({'chapters': [c]}, 'immersive')['chapters'][0]
                self.assertEqual(result['title'], c['title'])
                self.assertEqual(result['quests'], c['quests'])
                self.assertEqual(result['images'], c['images'])
                self.assertEqual(chapter_presentation(result, 'Altered', '5', 0), c['title'])

    def test_idless_retitling_preserves_converter_id_seed(self):
        c = chapter('unused', 'New Horizons', 'botania:mana_pool')
        del c['id']
        result = prepare_book({'chapters': [c]}, 'immersive')['chapters'][0]
        self.assertEqual(result['id'], 'New Horizons')
        self.assertEqual(result['title'], 'Botania')
        options = {'auto_chain': False, 'decor_art': False, 'layout': 'lanes'}
        old = A.build_chapters({'chapters': [copy.deepcopy(c)]}, lambda *_: None, options)[0][0][1]
        new = A.build_chapters({'chapters': [result]}, lambda *_: None, options)[0][0][1]
        self.assertEqual(old['id'], new['id'])
        self.assertEqual([q['id'] for q in old['quests']], [q['id'] for q in new['quests']])

    def test_titles_are_neutral_except_numbered_main_chapters(self):
        self.assertEqual(chapter_presentation({'group': 'Magic'}, '&d&lBotania', 'd', 7), '&7Botania')
        self.assertEqual(chapter_presentation({'group': 'Main Questline', '_book_style_main_number': 1},
                                            'Guardian of Gaia', 'd', 8), '&aChapter 1&r: &eGuardian of Gaia')
        self.assertEqual(group_presentation('Magic', '0', 50, 'bold'), '&dMagic')
        self.assertNotIn('&l', group_presentation('Storage', '7', 2))
        self.assertEqual(group_art_theme('Power'), 'power')

    def test_heading_treatments_are_distinct_readable_and_keep_theme_color(self):
        headings = {style: group_presentation('Magic', '0', 2, style)
                    for style in ('bold', 'bracket', 'stage', 'rule')}
        self.assertEqual(len(set(headings.values())), 4)
        self.assertTrue(all(value.startswith('&d') and 'Magic' in value for value in headings.values()))
        self.assertIn('[Magic]', headings['bracket'])
        self.assertIn('03 · Magic', headings['stage'])
        self.assertIn('— Magic —', headings['rule'])

    def test_bad_style_fails_clearly_and_original_order_metadata_is_stable(self):
        with self.assertRaisesRegex(ValueError, 'Unknown quest book style'):
            prepare_book({'chapters': []}, 'unknown')
        doc = {'chapters': [chapter('m', 'Botania', 'botania:mana_pool'), chapter('s', 'AE2', 'ae2:controller')]}
        result = prepare_book(doc, 'immersive')
        self.assertEqual({c['id']: c['_book_style_original_index'] for c in result['chapters']}, {'m': 0, 's': 1})
        again = prepare_book(result, 'immersive')
        self.assertEqual(result, again)

    def test_export_regrouping_does_not_change_amounts_or_synthetic_rewards(self):
        doc = {'chapters': [chapter('m', 'Botania', 'botania:mana_pool'),
                            chapter('s', 'AE2', 'ae2:certus_quartz_crystal', count=16)]}
        for c in doc['chapters']: c['quests'][0].pop('rewards')
        options = {'auto_chain': False, 'decor_art': False, 'layout': 'lanes', 'xp_rewards': True}
        def mechanics(style):
            exported = A.build_chapters(prepare_book(doc, style), lambda *_: None, options)[0]
            return {q['id']: (q['tasks'], q['rewards'], q.get('dependencies', [])) for _, c in exported for q in c['quests']}
        self.assertEqual(mechanics('classic'), mechanics('immersive'))


if __name__ == '__main__':
    unittest.main()
