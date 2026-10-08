"""Content regressions: playable objectives and causal offline progression.

Run with: python -m unittest discover -s moddb -p test_offline_story.py
No network, game launch, or live quest-folder writes.
"""
import copy
import pathlib
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import autoquestgen as A


def scan_for(items, recipes=None):
    groups = {}
    for item in items:
        groups.setdefault(item.split(":")[0], set()).add(item)
    recipes = recipes or {}
    return {
        "mods": [{"mod_id": mid, "name": mid.title(), "category": "tech",
                  "description": ""} for mid in groups if mid != "minecraft"],
        "items": groups, "craftable": copy.deepcopy(groups), "entities": {},
        "vanilla": {"minecraft:book", "minecraft:iron_ingot"},
        "craft_recipes": recipes,
        "edges": {item: set().union(*(set(ings) for _ty, ings in recs))
                  for item, recs in recipes.items()},
        "names": {}, "tier": {}, "blurbs": {}, "progression": {},
        "dimensions": {}, "structures": {}, "drops": {},
    }


def rows_for(items):
    return [(i, {"type": "item", "item": i, "count": 1}, "") for i in items]


class OfflineStoryTests(unittest.TestCase):
    def test_subtitle_cleanup_preserves_sentence_subjects_through_export(self):
        items = ["create:andesite_alloy", "create:cogwheel"]
        descriptions = ["Andesite Alloy is the starting material for your mechanical workshop.",
                        "Cogwheels transfer rotation between adjacent mechanisms."]
        rows = [{"id": "q%d" % i, "title": "Workshop step %d" % i,
                 "subtitle": ("Andesite Alloy" if i == 0 else "Cogwheel"),
                 "description": ["&7" + descriptions[i], "", "&7Keep room for the next machine."],
                 "tasks": [{"type": "item", "item": item}]} for i, item in enumerate(items)]
        before = copy.deepcopy(rows)
        doc = {"_offline_generated": True, "chapters": [{"id": "workshop", "title": "Workshop", "quests": rows}]}
        A.subtitle_tidy(doc["chapters"])
        self.assertEqual(rows, before)
        scan = scan_for(items)
        chapters, _groups, _extra = A.build_chapters(A.clean_doc(doc, scan, lambda *_: None),
            lambda *_: None, {"_scan": scan, "style_chapters": False, "layout": "lanes", "auto_chain": False})
        for i, quest in enumerate(chapters[0][1]["quests"]):
            self.assertIn(descriptions[i], " ".join(quest["description"]))

    def test_subtitle_cleanup_removes_only_complete_repeated_sentence(self):
        quest = {"title": "Moving Supplies", "subtitle": "Instant resource transportation",
                 "description": ["&7Instant resource transportation. Connect both endpoints before sending items.",
                                 "", "&aLeave the channel configured for the same destination."]}
        A.subtitle_tidy([{"quests": [quest]}])
        self.assertEqual(quest["description"], ["&7Connect both endpoints before sending items.",
                         "", "&aLeave the channel configured for the same destination."])

    def test_plain_item_translation_templates_never_leak_into_generated_prose(self):
        items = ["demo:affinity_ring", "demo:machine"]
        scan = scan_for(items, {items[1]: [("minecraft:crafting_shaped", [items[0]])]})
        scan["names"] = {items[0]: "Ring of %s Affinity", items[1]: "Machine %1$s"}
        original_names = dict(scan["names"])
        doc = A.local_quest_doc(scan, ["demo"], {"mimic": False, "density": "tiny", "seed": 1,
            "vanilla_chapters": False, "collections": False, "progression": "linear"})
        prose = " ".join(str(q.get(key, "")) for c in doc["chapters"] for q in c["quests"]
                         for key in ("title", "subtitle", "description"))
        self.assertNotIn("%s", prose)
        self.assertNotIn("%1$s", prose)
        self.assertIn("Affinity Ring", prose)
        self.assertEqual(scan["names"], original_names)

    def _vanilla_book(self, mode, recipes=None, groups=True):
        items = [item for _group, _title, _icon, rows in A._VANILLA_CHAPTERS for _title, item, _count, _description in rows]
        scan = scan_for(items, recipes)
        doc = A.local_quest_doc(scan, ["minecraft"], {"mimic": False, "density": "tiny", "aesthetic": "minimal",
                                                       "vanilla_chapters": True, "progression": mode, "groups": groups})
        return doc, {t["item"]: q for c in doc["chapters"] for q in c["quests"] for t in q["tasks"] if t.get("item")}

    def test_vanilla_guided_route_has_real_branches_and_cross_chapter_brewing(self):
        from quest_campaign import _assert_dag
        doc, goals = self._vanilla_book("linear", groups=False)
        checks = {"stone_pickaxe": "oak_log", "iron_ingot": "stone_pickaxe", "diamond": "iron_ingot",
                  "shield": "iron_ingot", "torch": "oak_log", "obsidian": "iron_ingot",
                  "brewing_stand": "blaze_rod", "blaze_rod": "obsidian", "ender_eye": "blaze_rod",
                  "elytra": "ender_eye", "dragon_breath": "ender_eye", "shulker_shell": "ender_eye"}
        for goal, parent in checks.items():
            self.assertIn(goals["minecraft:" + parent]["id"], goals["minecraft:" + goal]["dependencies"], goal)
        self.assertIn(goals["minecraft:diamond"]["id"], goals["minecraft:enchanting_table"]["dependencies"])
        self.assertTrue(all(not q.get("dependencies") for c in doc["chapters"] for q in c["quests"]
                            if all(t.get("type") == "checkmark" for t in q["tasks"])))
        _assert_dag(doc["chapters"])

    def test_vanilla_recipe_mode_respects_alternatives_and_open_has_no_locks(self):
        _doc, goals = self._vanilla_book("loose")
        self.assertEqual(goals["minecraft:diamond"]["dependencies"], [])
        self.assertEqual(goals["minecraft:shield"]["dependencies"], [goals["minecraft:iron_ingot"]["id"]])
        self.assertEqual(goals["minecraft:brewing_stand"]["dependencies"], [goals["minecraft:blaze_rod"]["id"]])
        _doc, alternatives = self._vanilla_book("loose", {"minecraft:shield": [("custom", ["demo:magic_wood"])]})
        self.assertEqual(alternatives["minecraft:shield"]["dependencies"], [])
        doc, _goals = self._vanilla_book("open")
        self.assertTrue(all(not q.get("dependencies") for c in doc["chapters"] for q in c["quests"]))

    def test_researched_collection_does_not_restore_unobtainable_tools(self):
        items = ["demo:tool%d" % i for i in range(25)]
        scan = scan_for(items)
        scan["unobtainable"] = {"demo:tool1"}
        with patch.object(A, "_RESEARCHED_WHEELS", {"demo": [("demo:tool0", ["demo:tool1", "demo:tool2"])]}):
            wheels = A._collection_wheels("demo", scan, {"demo:tool0"}, random.Random(0))
        self.assertEqual(wheels, [("demo:tool0", "demo:tool2")])

    def test_bulk_pass_does_not_invent_spellbook_grind(self):
        quests = [{"id": str(i), "title": "Spell Book %d" % i,
                   "tasks": [{"type": "item", "item": "demo:spell_book%d" % i, "count": 1}]}
                  for i in range(16)]
        A.bulk_ask_pass([{"id": "books", "quests": quests}], {})
        self.assertTrue(all(q["tasks"][0]["count"] == 1 for q in quests))

    def test_template_keeps_authored_batch_counts(self):
        items = ["demo:material%d" % i for i in range(14)]
        scan = scan_for(items)
        template = {"quests": [{"tasks": [{"type": "item", "item": item, "count": 64}],
                                "deps": [], "x": i * 2, "y": 0}
                               for i, item in enumerate(items)]}
        with patch.object(A, "_mod_quest_rows", return_value=[]):
            chapters = A._template_chapters_for(scan["mods"][0], [template], scan, 20,
                                                "colossal", random.Random(0),
                                                {"desc_lines": 0}, True, 0, layout="reference")
        A.bulk_ask_pass(chapters, scan)
        self.assertTrue(all(t["count"] == 64 for ch in chapters for q in ch["quests"]
                            for t in q["tasks"]))

    def test_converter_auto_chain_preserves_intentionally_open_generated_book(self):
        doc = {"_offline_generated": True, "chapters": [{"id": "demo", "title": "Demo",
                "quests": [{"id": str(i), "title": "Goal %d" % i,
                            "tasks": [{"type": "item", "item": "minecraft:iron_ingot"}]}
                           for i in range(4)]}]}
        chapters, _groups, _extra = A.build_chapters(doc, lambda _msg: None,
                                                    {"auto_chain": True, "layout": "lanes"})
        self.assertTrue(all(not q.get("dependencies")
                            for _name, chapter in chapters for q in chapter["quests"]))

    def test_progression_settings_change_requirements(self):
        items = ["demo:a", "demo:b", "demo:c"]
        scan = scan_for(items, {"demo:c": [("minecraft:crafting_shaped", {"demo:b"})]})
        chain = [(item, "", "") for item in items]
        with patch.object(A, "_curated_chain", return_value=chain):
            graphs = [A._progression_dependencies(rows_for(items), ["a", "b", "c"], scan, mode)[0]
                      for mode in ("linear", "loose", "open")]
        self.assertNotEqual(graphs[0], graphs[1])
        self.assertNotEqual(graphs[1], graphs[2])
        self.assertTrue(all(not deps for deps in graphs[2].values()))

    def test_singleton_tag_is_required_but_alternatives_stay_choices(self):
        scan = scan_for(["demo:metal", "demo:other", "demo:machine"], {
            "demo:machine": [("minecraft:crafting_shaped", {"#demo:metals"})]})
        scan["tags"] = {"demo:metals": {"demo:metal"}}
        self.assertEqual(A._required_recipe_items("demo:machine", scan), {"demo:metal"})
        scan["tags"]["demo:metals"].add("demo:other")
        self.assertEqual(A._required_recipe_items("demo:machine", scan), set())

    def test_getting_started_recipe_avoids_unpacking_its_own_block(self):
        scan = scan_for(["demo:ore", "demo:ingot", "demo:block"], {
            "demo:ingot": [("minecraft:crafting_shapeless", {"demo:block"}),
                           ("minecraft:smelting", {"demo:ore"})],
            "demo:block": [("minecraft:crafting_shaped", {"demo:ingot"})]})
        recipe = A._one_recipe("demo:ingot", scan)
        self.assertEqual(recipe[1], ["Ore"])

    def test_item_name_is_not_repeated_under_subtitle(self):
        quest = {"title": "The Hammer Falls", "subtitle": "Mechanical Press",
                 "description": ["Connect rotation to the Mechanical Press.", "", "&eMechanical Press&7"]}
        A.subtitle_tidy([{"quests": [quest]}])
        self.assertEqual(quest["description"], ["Connect rotation to the Mechanical Press."])

    def test_alternative_routes_do_not_become_all_required(self):
        scan = scan_for(["demo:ore", "demo:block", "demo:ingot"], {
            "demo:ingot": [("minecraft:smelting", {"demo:ore"}),
                           ("minecraft:crafting_shapeless", {"demo:block"})],
        })
        rows = rows_for(["demo:ore", "demo:block", "demo:ingot"])
        deps, _ = A._progression_dependencies(rows, ["ore", "block", "ingot"], scan)
        self.assertEqual(deps["ingot"], [])

    def test_machine_and_material_branches_join_at_real_product(self):
        scan = scan_for(["create:mechanical_press", "demo:plate", "demo:machine",
                         "minecraft:iron_ingot"], {
            "demo:plate": [("create:pressing", {"minecraft:iron_ingot"})],
            "demo:machine": [("minecraft:crafting_shaped", {"demo:plate"})],
        })
        rows = rows_for(["create:mechanical_press", "minecraft:iron_ingot", "demo:plate", "demo:machine"])
        deps, _ = A._progression_dependencies(rows, ["press", "iron", "plate", "machine"], scan)
        self.assertEqual(set(deps["plate"]), {"press", "iron"})
        self.assertEqual(deps["machine"], ["plate"])
        # Omitting a material quest does not erase the required machine.
        deps, _ = A._progression_dependencies([rows[0], rows[3]], ["press", "machine"], scan)
        self.assertEqual(deps["machine"], ["press"])

    def test_deep_reverse_recipe_order_is_fully_sorted(self):
        items = ["demo:stage%d" % i for i in range(12)]
        scan = scan_for(items, {items[i]: [("minecraft:crafting_shaped", {items[i - 1]})]
                                for i in range(1, len(items))})
        ordered = A._enforce_recipe_order(rows_for(list(reversed(items))), scan)
        self.assertEqual([r[1]["item"] for r in ordered], items)

    def test_shared_chapter_never_gates_unrelated_mod(self):
        items = ["alpha:starter", "beta:starter", "alpha:machine", "beta:machine"]
        scan = scan_for(items, {
            "alpha:machine": [("minecraft:crafting_shaped", {"alpha:starter"})],
            "beta:machine": [("minecraft:crafting_shaped", {"beta:starter"})],
        })
        deps, _ = A._progression_dependencies(rows_for(items), ["a", "b", "aa", "bb"], scan)
        self.assertEqual(deps, {"a": [], "b": [], "aa": ["a"], "bb": ["b"]})

    def test_switching_packs_cannot_leak_recipe_descriptions(self):
        first = scan_for(["demo:material", "demo:old_machine"], {
            "demo:old_machine": [("minecraft:crafting_shaped", {"demo:material"})]})
        second = scan_for(["demo:material", "demo:new_machine"], {
            "demo:new_machine": [("minecraft:crafting_shaped", {"demo:material"})],
            "demo:absent_machine": [("minecraft:crafting_shaped", {"demo:material"})]})
        self.assertIn("Old Machine", A.why_want("demo:material", first))
        text = A.why_want("demo:material", second)
        self.assertIn("New Machine", text)
        self.assertNotIn("Old Machine", text)
        self.assertNotIn("Absent Machine", text)

    def test_descriptions_use_supported_station_and_material_choices(self):
        scan = scan_for(["demo:plate", "create:mechanical_press"], {
            "demo:plate": [("create:pressing", {"#forge:ingots/iron"})]})
        text = " ".join(A.auto_description("demo:plate", scan))
        self.assertIn("Mechanical Press", text)
        self.assertIn("Iron ingots", text)
        self.assertNotIn("Small step", text)
        for seed in range(20):
            self.assertTrue(A._desc("item", "Plate", 1, "Demo", random.Random(seed),
                                    "demo:plate", {}, scan))

    def test_unknown_item_does_not_receive_fabricated_significance(self):
        scan = scan_for(["demo:oddity"])
        for seed in range(30):
            self.assertIsNone(A._desc("item", "Oddity", 1, "Demo", random.Random(seed),
                                     "demo:oddity", {}, scan))

    def test_template_objectives_are_identical_between_seeds(self):
        items = ["demo:machine%d" % i for i in range(12)]
        scan = scan_for(items + ["demo:golden_tunnel"])
        template = {"quests": [
            {"tasks": [{"type": "item", "item": item}], "deps": [i - 1] if i else [],
             "x": i % 4 * 2, "y": i // 4 * 2} for i, item in enumerate(items)
        ] + [{"tasks": [{"type": "item", "item": "demo:golden_ingot"}], "deps": [0]}]}
        outputs = []
        for seed in (11, 42, 123, 456):
            with patch.object(A, "_mod_quest_rows", return_value=[]):
                chapters = A._template_chapters_for(scan["mods"][0], [template], scan, 20,
                                                    "colossal", random.Random(0),
                                                    {"desc_lines": 0}, True, 0, seed=seed)
            outputs.append([(q["id"], q["tasks"]) for ch in chapters for q in ch["quests"]])
        self.assertTrue(all(output == outputs[0] for output in outputs))
        goals = {t["item"] for _qid, tasks in outputs[0] for t in tasks if t.get("item")}
        self.assertEqual(goals, set(items))

    def test_reference_layout_keeps_authored_coordinates_across_seeds(self):
        items = ["demo:machine%d" % i for i in range(8)]
        scan = scan_for(items)
        template = {"quests": [
            {"tasks": [{"type": "item", "item": item}], "deps": [i - 1] if i else [],
             "x": i % 4 * 2.5, "y": i // 4 * 3.5} for i, item in enumerate(items)]}
        for seed in (11, 42, 123):
            with patch.object(A, "_mod_quest_rows", return_value=[]):
                chapters = A._template_chapters_for(scan["mods"][0], [template], scan, 20,
                                                    "colossal", random.Random(0),
                                                    {"desc_lines": 0}, True, 0,
                                                    seed=seed, layout="reference")
            actual = [(q["x"], q["y"]) for ch in chapters for q in ch["quests"]]
            expected = [(q["x"], q["y"]) for q in template["quests"]]
            self.assertEqual(actual, expected)

    def test_template_only_mod_selection_builds_without_vanilla(self):
        items = ["demo:machine%d" % i for i in range(8)]
        scan = scan_for(items)
        template = {"quests": [{"tasks": [{"type": "item", "item": item}],
                                 "deps": [i - 1] if i else [], "x": i * 2, "y": 0}
                                for i, item in enumerate(items)]}
        with patch.object(A, "_load_templates", return_value={"demo": [template]}):
            doc = A.local_quest_doc(scan, ["demo"], {"mimic": True, "density": "large",
                                                    "vanilla_chapters": False,
                                                    "aesthetic": "decorated", "seed": 11})
        self.assertTrue(doc["chapters"])
        self.assertTrue(any(ch.get("_template") for ch in doc["chapters"]))


if __name__ == "__main__":
    unittest.main()
