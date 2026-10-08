"""Book size uses verified content and preserves reference prerequisite trunks."""
import random
import unittest
from unittest.mock import patch

import autoquestgen as A
from moddb.test_offline_story import scan_for
from quest_campaign import _assert_dag


def references(mod_count=3, goals=48):
    items = [f'workshop{m}:machine_{i}' for m in range(mod_count) for i in range(goals)]
    scan = scan_for(items)
    templates = {}
    for m in range(mod_count):
        mid = f'workshop{m}'
        # A trunk and several two-step branches: every retained child must
        # still depend on the exact original parents, not a spliced substitute.
        rows = []
        for i in range(goals):
            parent = [] if i == 0 else ([i - 1] if i < 8 or i % 2 == 0 else [6])
            rows.append(dict(tasks=[dict(type='item',item=f'{mid}:machine_{i}',count=1)],
                             deps=parent,x=i%8,y=i//8))
        templates[mid] = [dict(topic='Factory',quests=rows)]
    return scan, templates


class ScopeBudgetTests(unittest.TestCase):
    def test_explicit_reference_budget_removes_leaves_without_rewriting_ancestors(self):
        scan, templates = references(1)
        mod = scan['mods'][0]
        args = (mod, templates[mod['mod_id']], scan, 11, 'normal', random.Random(7),
                {'desc_lines':0}, True, 0)
        with patch.object(A,'_mod_quest_rows',return_value=[]):
            automatic = A._template_chapters_for(*args,layout='reference')
            limited = A._template_chapters_for(*args,layout='reference',limit_to_budget=True)
        self.assertGreater(len(automatic[0]['quests']),len(limited[0]['quests']))
        self.assertLessEqual(sum(len(c['quests']) for c in limited),12)
        original = {q['id']:q for c in automatic for q in c['quests']}
        for chapter in limited:
            for quest in chapter['quests']:
                self.assertEqual(quest,original[quest['id']])
        _assert_dag(limited)

    def test_one_mod_with_many_source_chapters_shares_one_allocation(self):
        scan, templates = references(1)
        mod = scan['mods'][0]
        chapters = templates[mod['mod_id']] * 4
        with patch.object(A,'_mod_quest_rows',return_value=[]):
            limited=A._template_chapters_for(mod,chapters,scan,10,'normal',random.Random(1),
                {'desc_lines':0},True,0,layout='reference',limit_to_budget=True)
        self.assertGreaterEqual(len(limited),1)
        self.assertLessEqual(sum(len(c['quests']) for c in limited),11)
        self.assertTrue(all(len(c['quests'])>=4 for c in limited))
        _assert_dag(limited)

    def test_reference_enabled_manual_target_changes_real_engine_selection(self):
        scan, templates = references()
        opts=dict(density='normal',seed=19,mimic=True,vanilla_chapters=False,
                  collections=False,campaign=False,aesthetic='minimal',progression='linear')
        with patch.object(A,'_load_templates',return_value=templates):
            selected=[m['mod_id'] for m in scan['mods']]
            docs=[A.local_quest_doc(scan,selected,opts|{'target':target}) for target in ('','24','54')]
        counts=[sum(len(c['quests']) for c in doc['chapters']) for doc in docs]
        self.assertLess(counts[1],counts[2])
        self.assertLess(counts[2],counts[0])
        known=set().union(*scan['items'].values())
        for doc in docs:
            self.assertEqual(sum(bool(c.get('_template')) for c in doc['chapters']),3)
            self.assertTrue(all(t['item'] in known for c in doc['chapters'] for q in c['quests']
                                for t in q['tasks'] if t.get('type')=='item'))
            _assert_dag(doc['chapters'])

    def test_scarce_content_is_not_padded_and_libraries_are_excluded(self):
        items=['workshop:controller','geckolib:example_item']
        scan=scan_for(items)
        opts=dict(mimic=False,vanilla_chapters=False,collections=False,campaign=False,
                  aesthetic='minimal',progression='linear')
        small=A.local_quest_doc(scan,[],opts|{'density':'small'})
        massive=A.local_quest_doc(scan,[],opts|{'density':'massive'})
        for doc in (small,massive):
            objectives=[t['item'] for c in doc['chapters'] for q in c['quests']
                        for t in q['tasks'] if t.get('type')=='item']
            self.assertEqual(objectives,['workshop:controller'])
            self.assertLess(sum(len(c['quests']) for c in doc['chapters']),10)


if __name__=='__main__':
    unittest.main()
