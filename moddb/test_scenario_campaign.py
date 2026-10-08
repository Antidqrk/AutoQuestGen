"""Scenario stages must keep real preparation, action detection and story gates."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import autoquestgen as A
from quest_campaign import apply_scenario, _assert_dag, _reduce_scenario_dependencies
from moddb.test_offline_story import scan_for
from moddb.test_campaign import book_for, quests
from moddb.test_natural_goal_campaign import botania_scan, ancestors


def stage(key, goal, parents=(), manual=False, **extra):
    return dict(id=key, title=key.replace('_', ' ').title(), goal=goal,
                depends_on=list(parents), completion='manual' if manual else 'objective',
                description='Place water in the chosen restoration area, then confirm after the pool exists.' if manual else '',
                **extra)


def outline(stages, **extra):
    return dict(title='A World Worth Growing', premise='Restore a barren world.', stages=stages, **extra)


class ScenarioCampaignTests(unittest.TestCase):
    def test_scenario_sidebar_keeps_story_name_and_author_order_with_branch_finale(self):
        scan=scan_for(['demo:water_rod','demo:soil_rod'])
        plan=outline([stage('water','craft demo:water_rod'),stage('soil','craft demo:soil_rod')])
        plan['title']='A Living World'
        doc=apply_scenario({'chapters':[]},scan,[],plan)
        sidebar=doc['campaign']['sidebar']
        self.assertEqual(sidebar,{'group':'A Living World','after_group':'Getting Started',
                                 'chapter_ids':['scenario_stage_water','scenario_stage_soil','scenario_final']})
        self.assertEqual([c['_campaign_role'] for c in doc['chapters']],['stage','stage','finale'])
        self.assertTrue(all(c['group']=='A Living World' for c in doc['chapters']))
        self.assertEqual(list(doc['scenario']['stage_quest_ids']),['water','soil'])
        _assert_dag(doc['chapters'])

    def test_shared_recipe_preparation_and_manual_restoration_gate_actual_boss(self):
        scan=botania_scan()
        for item in ('botania:water_rod','botania:mana_tablet'):
            scan['items']['botania'].add(item)
            scan['craftable']['botania'].add(item)
        scan['craft_recipes']['botania:water_rod']=[('crafting',['botania:manasteel_ingot','botania:livingwood_log'])]
        plan=outline([stage('mana','craft botania:mana_pool'),
                      stage('water','craft botania:water_rod',['mana'],True,requirements=['craft botania:mana_tablet']),
                      stage('guardian','kill the gaia garden from botania',['water'])])
        original=book_for(['botania:mana_pool','botania:livingwood_log'])
        before=copy.deepcopy(original)
        doc=apply_scenario(original,scan,['botania'],plan)
        self.assertEqual(original,before)
        rows={q['id']:q for q in quests(doc)}
        ids=doc['scenario']['stage_quest_ids']
        water=rows[ids['water']]
        self.assertEqual(water['tasks'][0]['type'],'checkmark')
        self.assertIn(ids['mana'],water['dependencies'])
        self.assertIn('manual checkpoint',' '.join(water['description']).lower())
        boss=rows[ids['guardian']]
        self.assertEqual(boss['tasks'][0]['entity'],'botania:doppleganger')
        self.assertIn(ids['water'],boss['dependencies'])
        self.assertEqual(doc['campaign']['finale_quest_ids'],[boss['id']])
        item_counts={i:sum(t.get('item')==i for q in quests(doc) for t in q['tasks']) for i in ('botania:mana_pool','botania:mana_tablet')}
        self.assertEqual(item_counts,{'botania:mana_pool':1,'botania:mana_tablet':1})
        self.assertTrue(any(t.get('item')=='botania:mana_tablet' for q in ancestors(doc,water['id']) for t in q['tasks']))
        _assert_dag(doc['chapters'])

    def test_preparation_is_not_truncated_by_tiny_campaign_support_limits(self):
        items=['demo:part_%02d'%i for i in range(22)]
        recipes={items[i]:[('crafting',[items[i-1]])] for i in range(1,len(items))}
        doc=apply_scenario({'chapters':[]},scan_for(items,recipes),['demo'],outline([stage('machine','craft '+items[-1])]))
        self.assertEqual({t.get('item') for q in quests(doc) for t in q['tasks']},set(items))
        self.assertEqual(len(ancestors(doc,doc['campaign']['finale_quest_ids'][0])),21)
        _assert_dag(doc['chapters'])

    def test_real_recipe_alternatives_and_tags_choose_one_available_route(self):
        scan=scan_for(['demo:tool','demo:log','demo:wood'],{'demo:tool':[('crafting',['demo:absent']),('crafting',['#demo:timber'])]})
        scan['tags']={'demo:timber':{'demo:log','demo:wood'}}
        doc=apply_scenario({'chapters':[]},scan,['demo'],outline([stage('tool','craft demo:tool')]))
        items={t.get('item') for q in quests(doc) for t in q['tasks']}
        self.assertEqual(items,{'demo:tool','demo:log'})

    def test_equivalent_ingredient_order_has_identical_scenario_output(self):
        items=['demo:tool','demo:wood','demo:stone']
        scans=[scan_for(items,{'demo:tool':[('crafting',ingredients)]})
               for ingredients in (['demo:wood','demo:stone'],['demo:stone','demo:wood'])]
        docs=[apply_scenario({'chapters':[]},scan,['demo'],outline([stage('tool','craft demo:tool')]),seed=14) for scan in scans]
        self.assertEqual(docs[0],docs[1])

    def test_world_acquisition_is_not_gated_by_optional_conversion_recipes(self):
        scan=scan_for(['demo:stew','demo:wall_mushroom','minecraft:brown_mushroom'],
                      {'demo:stew':[('crafting',['minecraft:brown_mushroom'])],
                       'demo:wall_mushroom':[('crafting',['minecraft:brown_mushroom'])],
                       'minecraft:brown_mushroom':[('crafting',['demo:wall_mushroom'])]})
        scan['vanilla_sources']={'minecraft:brown_mushroom':'Harvest naturally growing mushrooms.'}
        doc=apply_scenario({'chapters':[]},scan,['demo'],outline([
            stage('stew','craft demo:stew'),stage('decoration','craft demo:wall_mushroom',['stew'])]))
        mushroom=next(q for q in quests(doc) if q['tasks'][0].get('item')=='minecraft:brown_mushroom')
        self.assertFalse(mushroom['dependencies'])
        _assert_dag(doc['chapters'])

    def test_missing_real_recipe_input_blocks_instead_of_omitting_preparation(self):
        scan=scan_for(['demo:tool'],{'demo:tool':[('crafting',['demo:missing'])]})
        with self.assertRaisesRegex(ValueError,'ingredient|unavailable'):
            apply_scenario({'chapters':[]},scan,['demo'],outline([stage('tool','craft demo:tool')]))

    def test_unresolved_recipe_tag_is_an_explicit_manual_requirement(self):
        scan=scan_for(['demo:guide'],{'demo:guide':[('crafting',['#minecraft:saplings'])]})
        doc=apply_scenario({'chapters':[]},scan,['demo'],outline([stage('guide','craft demo:guide')]))
        check=next(q for q in quests(doc) if q['tasks'][0]['type']=='checkmark')
        self.assertIn('#minecraft:saplings',' '.join(check['description']))
        self.assertIn('manual confirmation',' '.join(check['description']))
        final=next(q for q in quests(doc) if q['id'] in doc['campaign']['finale_quest_ids'])
        self.assertIn(check['id'],final['dependencies'])

    def test_new_stage_preparation_roots_wait_for_prior_stage_without_gating_shared_items(self):
        scan=scan_for(['demo:water','other:soil','other:clay','demo:shared'],
                      {'demo:water':[('crafting',['demo:shared'])],
                       'other:soil':[('crafting',['demo:shared','other:clay'])]})
        plan=outline([stage('water','craft demo:water',manual=True),
                      stage('soil','craft other:soil',['water'],manual=True)])
        doc=apply_scenario({'chapters':[]},scan,['demo','other'],plan)
        water=doc['scenario']['stage_quest_ids']['water']
        items={t.get('item'):q for q in quests(doc) for t in q['tasks'] if t.get('item')}
        self.assertIn(water,items['other:clay']['dependencies'])
        self.assertNotIn(water,items['demo:shared']['dependencies'])
        self.assertIn(water,{q['id'] for q in ancestors(doc,items['other:soil']['id'])})
        self.assertEqual(len(doc['chapters']),2)
        self.assertTrue(doc['chapters'][0]['title'].startswith('01 · '))
        self.assertTrue(doc['chapters'][1]['title'].startswith('02 · '))
        self.assertEqual({c['group'] for c in doc['chapters']},{plan['title']})
        chapter_for={q['id']:c['id'] for c in doc['chapters'] for q in c['quests']}
        first,second=doc['chapters']
        self.assertEqual(chapter_for[items['demo:shared']['id']],first['id'])
        self.assertEqual(chapter_for[items['other:clay']['id']],second['id'])
        self.assertEqual(chapter_for[doc['scenario']['stage_quest_ids']['soil']],second['id'])
        self.assertEqual(len(chapter_for),len(quests(doc)))
        _assert_dag(doc['chapters'])

    def test_redundant_preparation_edges_reduce_without_changing_any_ancestor(self):
        rows=[{'id':'root','dependencies':[]},
              {'id':'tool','dependencies':['root']},
              {'id':'first','dependencies':['root','tool']},
              {'id':'material','dependencies':['first','root']},
              {'id':'second','dependencies':['first','material','root','tool']}]
        before={q['id']:{p['id'] for p in ancestors({'chapters':[{'quests':rows}]},q['id'])} for q in rows}
        _reduce_scenario_dependencies(rows,{('second','first')})
        after={q['id']:{p['id'] for p in ancestors({'chapters':[{'quests':rows}]},q['id'])} for q in rows}
        self.assertEqual(before,after)
        self.assertEqual(rows[-1]['dependencies'],['first','material'])
        self.assertEqual(rows[2]['dependencies'],['tool'])
        _assert_dag([{'quests':rows}])

    def test_recipe_conflicting_author_order_is_actionable_and_open_mode_is_free(self):
        scan=scan_for(['demo:tool','demo:gear'],{'demo:tool':[('crafting',['demo:gear'])]})
        plan=outline([stage('tool','craft demo:tool'),stage('gear','craft demo:gear',['tool'])])
        with self.assertRaisesRegex(ValueError,'stage order conflicts'):
            apply_scenario({'chapters':[]},scan,['demo'],plan)
        doc=apply_scenario({'chapters':[]},scan,['demo'],plan,progression='open')
        self.assertTrue(all(not q['dependencies'] for q in quests(doc)))

    def test_all_independent_story_branches_converge(self):
        scan=scan_for(['demo:water_rod','demo:soil_rod'])
        doc=apply_scenario({'chapters':[]},scan,['demo'],outline([
            stage('water','craft demo:water_rod',manual=True),stage('soil','craft demo:soil_rod',manual=True)]))
        final=next(q for q in quests(doc) if q['id'] in doc['campaign']['finale_quest_ids'])
        self.assertEqual(set(final['dependencies']),set(doc['scenario']['stage_quest_ids'].values()))
        _assert_dag(doc['chapters'])

    def test_edited_cached_plan_is_revalidated_and_original_ids_survive(self):
        scan=scan_for(['demo:tool'])
        plan=outline([stage('tool','craft demo:tool')])
        doc=apply_scenario(book_for(['demo:tool']),scan,['demo'],plan)
        self.assertEqual(doc['scenario']['stage_quest_ids']['tool'],'q0')
        changed=copy.deepcopy(doc['scenario'])
        changed['stages'][0]['goal']='craft missing:tool'
        with self.assertRaises(ValueError):
            apply_scenario({'chapters':[]},scan,['demo'],changed)

    def test_authored_guide_prerequisite_from_unselected_mod_is_rejected(self):
        scan=scan_for(['demo:tool','other:altar'])
        with patch.object(A,'_GUIDED_REQUIREMENTS',{'demo:tool':['other:altar']}):
            with self.assertRaisesRegex(ValueError,'unselected'):
                apply_scenario({'chapters':[]},scan,['demo'],outline([stage('tool','craft demo:tool')]))

    def test_script_recipe_component_counts_and_nested_preparation_survive_scenario_merge(self):
        from moddb.test_pack_campaign import stack, recipe, fixture
        data='[enchantments={levels:{"minecraft:unbreaking":1}},custom_name=\'{"text":"Awakened Alloy"}\']'
        scan=fixture([recipe(stack('pack:star'),[stack('alloy:block',5,data)]),
                      recipe(stack('alloy:block',2,data),[stack('alloy:block'),stack('magic:essence',4)],2)])
        doc=apply_scenario({'chapters':[]},scan,[],outline([stage('star','craft pack:star')]))
        special=next(q for q in quests(doc) if q.get('_pack_components')==data)
        self.assertEqual(special['_pack_count'],5)
        self.assertEqual(special['tasks'][0]['type'],'checkmark')
        self.assertIn('Unbreaking',' '.join(special['description']))
        items={t.get('item'):t.get('count',1) for q in quests(doc) for t in q['tasks'] if t.get('item')}
        self.assertEqual(items['magic:essence'],12)
        self.assertEqual(items['alloy:block'],3)
        _assert_dag(doc['chapters'])

    def test_cli_scenario_file_uses_real_pipeline(self):
        scan=scan_for(['demo:water_rod'])
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'mods').mkdir()
            source=folder/'scenario.json'
            source.write_text(json.dumps(outline([stage('water','craft demo:water_rod',manual=True)])),encoding='utf-8')
            with patch.object(A,'scan_mods',return_value=scan):
                code=A.run_cli_build(str(folder/'mods'),str(folder/'out'),density='tiny',scenario=str(source))
            self.assertEqual(code,0)
            data=[A.snbt_loads(p.read_text()) for p in (folder/'out/chapters').glob('*.snbt')]
            self.assertTrue(any(t['type']=='checkmark' for c in data for q in c['quests'] for t in q['tasks']))

    def test_engine_hook_honors_scenario_and_preserves_manual_instructions_in_minimal_style(self):
        scan=scan_for(['demo:water_rod'])
        plan=outline([stage('water','craft demo:water_rod',manual=True)])
        options={'campaign':True,'scenario':plan,'campaign_goal':'unmatched obsolete single goal','mimic':False,
                 'density':'tiny','target':'1','vanilla_chapters':False,'collections':False,
                 'aesthetic':'minimal','progression':'linear'}
        doc=A.local_quest_doc(scan,['demo'],options)
        self.assertEqual(doc['campaign']['kind'],'scenario')
        finale=next(q for q in quests(doc) if q['id'] in doc['campaign']['finale_quest_ids'])
        self.assertIn('manual checkpoint',' '.join(finale['description']).lower())
        chapters,groups,extra=A.build_chapters(A.clean_doc(doc,scan,lambda *_:None),lambda *_:None,dict(options,_scan=scan))
        _assert_dag([c for _,c in chapters])
        with tempfile.TemporaryDirectory() as tmp:
            A.write_snbt(Path(tmp),chapters,groups,True,lambda *_:None,{},extra.get('reward_tables'))
            saved=[A.snbt_loads(p.read_text()) for p in (Path(tmp)/'chapters').glob('*.snbt')]
        check=next(q for c in saved for q in c['quests'] if any(t['type']=='checkmark' for t in q['tasks']))
        self.assertIn('manual checkpoint',' '.join(check['description']).lower())

    def test_empty_scenario_is_not_silently_replaced_with_whole_pack_goal(self):
        with self.assertRaises(ValueError):
            A.local_quest_doc(scan_for(['demo:tool']),['demo'],{'campaign':True,'scenario':{},'mimic':False})

    def test_cli_scenario_conflict_or_bad_file_fails_before_scanning(self):
        with patch.object(A,'scan_mods',side_effect=AssertionError('must not scan')):
            self.assertEqual(A.run_cli_build('unused','unused',goal='demo:tool',scenario={}),2)
            self.assertEqual(A.run_cli_build('unused','unused',scenario='missing-scenario-file.json'),2)


if __name__=='__main__':
    unittest.main()
