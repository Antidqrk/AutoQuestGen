"""A goal may guide a whole selected pack without replacing it with one route."""
import copy
from pathlib import Path
import tempfile
import unittest

import autoquestgen as A
from quest_campaign import apply_campaign, _assert_dag
from moddb.test_campaign import book_for, quests
from moddb.test_natural_goal_campaign import botania_scan, ancestors, GAIA
from moddb.test_offline_story import scan_for
from moddb.test_blueprint_export import source_chapter, GOAL


def full_scan(branches=1):
    scan=botania_scan()
    for i in range(branches):
        mid='engineering%d'%i
        item=mid+':advanced_controller'
        scan['mods'].append(dict(mod_id=mid,name='Engineering '+str(i),category='tech',description=''))
        scan['items'][mid]={item}
        scan['craftable'][mid]={item}
    return scan


def selected_ids(scan):
    return [m['mod_id'] for m in scan['mods']]


class CampaignScopeTests(unittest.TestCase):
    def test_goal_sidebar_names_requested_goal_and_keeps_journey_second(self):
        scan=full_scan()
        original=book_for(['engineering0:advanced_controller'])
        result=apply_campaign(original,scan,selected_ids(scan),'kill Gaia Guardian',scope='selected')
        sidebar=result['campaign']['sidebar']
        self.assertEqual(sidebar['group'],'Main Goal')
        self.assertEqual(sidebar['after_group'],'Getting Started')
        chapters={c['id']:c for c in result['chapters']}
        featured=[chapters[key] for key in sidebar['chapter_ids']]
        self.assertEqual([c['_campaign_role'] for c in featured],['goal','journey'])
        self.assertEqual([c['title'] for c in featured],['Gaia Guardian','Your Modpack Journey'])
        self.assertTrue(all(c['group']=='Main Goal' for c in featured))
        self.assertEqual(chapters['engineering0:advanced_controller'],original['chapters'][0])
        self.assertEqual(result,apply_campaign(result,scan,selected_ids(scan),'kill Gaia Guardian',scope='selected'))
        _assert_dag(result['chapters'])

    def test_item_goal_chapter_uses_actual_item_name_and_sidebar_has_no_phantom_summary(self):
        scan=scan_for(['demo:star'])
        scan['names']['demo:star']='Workshop Star'
        result=apply_campaign({'chapters':[]},scan,[],'craft demo:star')
        chapter=result['chapters'][0]
        self.assertEqual(chapter['title'],'Workshop Star')
        self.assertEqual(chapter['group'],'Main Goal')
        self.assertEqual(result['campaign']['sidebar']['chapter_ids'],[chapter['id']])
        self.assertEqual(chapter['_campaign_role'],'goal')
        self.assertEqual(chapter['quests'][-1]['tasks'][0]['item'],'demo:star')

    def test_selected_keeps_unrelated_quests_and_exact_boss_with_separate_story_completion(self):
        scan=full_scan()
        doc=book_for(['botania:lexicon','botania:life_essence','engineering0:advanced_controller'])
        doc['chapters'][1]['quests'][0]['dependencies']=['q0']
        before=copy.deepcopy(doc)
        result=apply_campaign(doc,scan,selected_ids(scan),'kill the gaia garden from botania',scope='selected')
        rows={q['id']:q for q in quests(result)}
        self.assertEqual(doc,before)
        self.assertEqual(rows['q1'],before['chapters'][1]['quests'][0])
        self.assertEqual(rows['q2'],before['chapters'][2]['quests'][0])
        goal=rows[result['campaign']['goal_quest_ids'][0]]
        self.assertEqual(goal['tasks'][0]['entity'],GAIA)
        self.assertIn('complete this goal',' '.join(goal['description']))
        self.assertNotIn('complete this campaign',' '.join(goal['description']))
        self.assertIn('The goal quest checks',result['campaign']['explanation'])
        goal_ancestors={q['id'] for q in ancestors(result,goal['id'])}
        self.assertNotIn('q2',goal_ancestors)
        self.assertIn('botania:gaia_pylon',{t.get('item') for q in ancestors(result,goal['id']) for t in q['tasks']})
        final=rows[result['campaign']['finale_quest_ids'][0]]
        self.assertEqual(final['tasks'][0]['type'],'checkmark')
        self.assertTrue({goal['id'],'q2'} <= {q['id'] for q in ancestors(result,final['id'])})
        self.assertIn('campaign',' '.join(final['description']))
        self.assertTrue(any(c.get('_campaign_summary') for c in result['chapters']))
        _assert_dag(result['chapters'])

    def test_focused_default_still_ends_at_boss_and_omits_unrelated_content(self):
        scan=full_scan()
        result=apply_campaign(book_for(['engineering0:advanced_controller']),scan,selected_ids(scan),'kill Gaia Guardian')
        self.assertEqual(result['campaign']['scope'],'goal')
        self.assertEqual(len(result['chapters']),1)
        self.assertEqual(result['campaign']['goal_quest_ids'],result['campaign']['finale_quest_ids'])
        goal=next(q for q in quests(result) if q['id'] in result['campaign']['goal_quest_ids'])
        self.assertIn('complete this campaign',' '.join(goal['description']))
        self.assertFalse(any(t.get('item','').startswith('engineering') for q in quests(result) for t in q['tasks']))

    def test_moved_task_ids_preserve_foreign_dependencies_and_post_boss_quests(self):
        scan=full_scan()
        doc=book_for(['botania:gaia_pylon','botania:life_essence','engineering0:advanced_controller'])
        pylon=doc['chapters'][0]['quests'][0]
        pylon['tasks'][0]['id']='pylon_task'
        boss={'id':'existing_boss','title':'Original boss','tasks':[{'id':'boss_task','type':'kill','entity':GAIA,'value':3}],
              'dependencies':['pylon_task']}
        doc['chapters'][0]['quests'].append(boss)
        doc['chapters'][1]['quests'][0]['dependencies']=['boss_task']
        doc['chapters'][2]['quests'][0]['dependencies']=['pylon_task']
        result=apply_campaign(doc,scan,selected_ids(scan),'kill Gaia Guardian',scope='selected')
        rows={q['id']:q for q in quests(result)}
        self.assertEqual(rows['existing_boss']['tasks'][0]['id'],'boss_task')
        self.assertEqual(rows['existing_boss']['tasks'][0]['value'],1)
        self.assertEqual(rows['q1']['dependencies'],['boss_task'])
        self.assertEqual(rows['q2']['dependencies'],['pylon_task'])
        self.assertEqual(rows['q0']['tasks'][0]['id'],'pylon_task')
        _assert_dag(result['chapters'])

    def test_open_selected_book_has_no_prerequisite_locks_anywhere(self):
        scan=full_scan()
        doc=book_for(['botania:lexicon','engineering0:advanced_controller'])
        doc['chapters'][1]['quests'][0]['dependencies']=['q0']
        result=apply_campaign(doc,scan,selected_ids(scan),'kill Gaia Guardian',progression='open',scope='selected')
        self.assertTrue(all(not q.get('dependencies') for q in quests(result)))
        self.assertTrue({'q0','q1'} <= {q['id'] for q in quests(result)})

    def test_many_branches_use_bounded_joins_and_keep_all_achievements(self):
        scan=full_scan(43)
        doc=book_for(['engineering%d:advanced_controller'%i for i in range(43)])
        result=apply_campaign(doc,scan,selected_ids(scan),'kill Gaia Guardian',scope='selected')
        final=result['campaign']['finale_quest_ids'][0]
        self.assertTrue({'q%d'%i for i in range(43)} <= {q['id'] for q in ancestors(result,final)})
        summary=next(c for c in result['chapters'] if c.get('_campaign_summary'))
        self.assertLessEqual(max(len(q['dependencies']) for q in summary['quests']),6)
        self.assertLess(len(summary['quests']),15)
        _assert_dag(result['chapters'])

    def test_multiple_requested_items_do_not_make_an_unbounded_join_loop(self):
        items=['demo:machine_%d'%i for i in range(8)]+['other:advanced_controller']
        scan=scan_for(items)
        for progression in ('linear','open'):
            with self.subTest(progression=progression):
                result=apply_campaign(book_for(items),scan,[],', '.join(items[:8]),scope='selected',progression=progression)
                summary=next(c for c in result['chapters'] if c.get('_campaign_summary'))
                self.assertLess(len(summary['quests']),6)
                self.assertLessEqual(max(len(q['dependencies']) for q in summary['quests']),6)
                final=result['campaign']['finale_quest_ids'][0]
                if progression=='linear':
                    self.assertTrue({'q%d'%i for i in range(9)} <= {q['id'] for q in ancestors(result,final)})
                else:
                    self.assertTrue(all(not q['dependencies'] for q in quests(result)))

    def test_selection_scope_is_idempotent_and_switching_needs_a_fresh_book(self):
        scan=full_scan()
        result=apply_campaign(book_for(['engineering0:advanced_controller']),scan,[],'kill Gaia Guardian',scope='selected')
        self.assertEqual(result,apply_campaign(result,scan,[],'kill Gaia Guardian',scope='selected'))
        with self.assertRaisesRegex(ValueError,'fresh quest book'):
            apply_campaign(result,scan,[],'kill Gaia Guardian',scope='goal')
        with self.assertRaisesRegex(ValueError,'scope'):
            apply_campaign({'chapters':[]},scan,[],'kill Gaia Guardian',scope='invented')

    def test_engine_scope_option_alone_preserves_selected_mod_generation(self):
        scan=full_scan(3)
        options=dict(campaign=True,campaign_goal='kill Gaia Guardian',density='normal',seed=2,
                     mimic=False,vanilla_chapters=False,collections=False,progression='linear')
        focused=A.local_quest_doc(scan,selected_ids(scan),options|{'campaign_scope':'goal'})
        selected=A.local_quest_doc(scan,selected_ids(scan),options|{'campaign_scope':'selected'})
        self.assertEqual(len(focused['chapters']),1)
        self.assertGreater(len(selected['chapters']),4)
        items={t.get('item') for q in quests(selected) for t in q['tasks']}
        self.assertTrue({'engineering%d:advanced_controller'%i for i in range(3)} <= items)
        self.assertGreater(len(quests(selected)),len(quests(focused)))
        _assert_dag(selected['chapters'])

    def test_native_blueprint_replaces_only_goal_and_preserves_selected_summary(self):
        scan=scan_for(['demo:star','demo:alloy','demo:gift','other:advanced_controller'],
                      {'demo:star':[('crafting',['demo:alloy'])]})
        doc=apply_campaign(book_for(['demo:star','other:advanced_controller']),scan,[],'demo:star',scope='selected')
        overall=doc['campaign']['finale_quest_ids'][:]
        source=source_chapter()
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'config/ftbquests/quests/chapters'
            folder.mkdir(parents=True)
            path=folder/'the_star.snbt'
            path.write_text(A.snbt_dumps(source),encoding='utf-8')
            before=path.read_bytes()
            scan['instance_dir']=tmp
            opts=dict(mimic=True,layout='reference',campaign_scope='selected',_scan=scan,
                      style_chapters=True,groups=True,aesthetic='lavish',reward='standard',quest_size_style='preserve')
            result=A._use_installed_goal_blueprint(doc,scan,opts)
            self.assertEqual(result['campaign']['goal_quest_ids'],[GOAL])
            self.assertEqual(result['campaign']['finale_quest_ids'],overall)
            self.assertTrue(any(c.get('_campaign_summary') for c in result['chapters']))
            _assert_dag(result['chapters'])
            chapters,groups,extra=A.build_chapters(A.clean_doc(result,scan,lambda *_:None),lambda *_:None,opts)
            _assert_dag([c for _,c in chapters])
            self.assertEqual(path.read_bytes(),before)
            native=next(c for _,c in chapters if c.get('filename')=='the_star')
            self.assertEqual(native['quests'],source['quests'])
            allquests=[q for _,c in chapters for q in c['quests']]
            self.assertTrue(any(GOAL in q.get('dependencies',[]) for q in allquests if q['id']!=GOAL))

    def test_fixed_authored_scenario_ignores_full_pack_scope(self):
        from moddb.test_scenario_campaign import outline,stage
        scan=scan_for(['demo:water_rod','other:advanced_controller'])
        opts=dict(campaign=True,campaign_scope='selected',scenario=outline([stage('water','craft demo:water_rod',manual=True)]),
                  density='normal',mimic=False,vanilla_chapters=False)
        doc=A.local_quest_doc(scan,[],opts)
        self.assertEqual(doc['campaign']['kind'],'scenario')
        self.assertEqual(len(doc['chapters']),1)
        self.assertFalse(any(t.get('item')=='other:advanced_controller' for q in quests(doc) for t in q['tasks']))

    def test_script_item_goal_keeps_external_task_id_dependencies(self):
        from moddb.test_pack_campaign import fixture,recipe,stack
        scan=fixture([recipe(stack('pack:star'),[stack('alloy:block',3)])])
        doc=book_for(['pack:star','alloy:block'])
        doc['chapters'][0]['quests'][0]['tasks'][0]['id']='star_task'
        doc['chapters'][1]['quests'].append({'id':'celebration','title':'Remember the Star',
                                           'tasks':[{'type':'checkmark'}],'dependencies':['star_task']})
        result=apply_campaign(doc,scan,[],'pack:star',scope='selected')
        rows={q['id']:q for q in quests(result)}
        self.assertEqual(rows['q0']['tasks'][0]['id'],'star_task')
        self.assertEqual(rows['celebration']['dependencies'],['star_task'])
        _assert_dag(result['chapters'])


if __name__=='__main__':
    unittest.main()
