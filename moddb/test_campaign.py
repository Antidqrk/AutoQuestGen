"""Campaign goals must stay grounded in the installed content and real graph."""
import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from quest_campaign import apply_campaign, resolve_goal, _assert_dag
from moddb.test_offline_story import scan_for
import autoquestgen as A


def book_for(items):
    return {"chapters": [{"id": item, "title": item.split(":")[0], "quests": [
        {"id": "q" + str(i), "title": item.split(":")[-1].replace("_", " ").title(),
         "tasks": [{"type": "item", "item": item}], "dependencies": []}
    ]} for i, item in enumerate(items)]}


def quests(doc):
    return [q for c in doc["chapters"] for q in c["quests"]]


class CampaignTests(unittest.TestCase):
    def test_domain_goal_changes_scope(self):
        scan = scan_for(["create:mechanical_crafter", "botania:gaia_ingot"])
        scan["mods"][1]["category"] = "magic"
        self.assertEqual(resolve_goal(scan, [], "an ultimate magic campaign")["focus_mods"], ["botania"])
        self.assertEqual(resolve_goal(scan, [], "automate a factory")["focus_mods"], ["create"])

    def test_create_as_verb_does_not_select_the_mod(self):
        scan = scan_for(["create:mechanical_crafter", "botania:gaia_ingot"])
        scan["mods"][1]["category"] = "magic"
        for text in ("create a magic pack", "CREATE A MAGIC PACK", "create magic campaign"):
            self.assertEqual(resolve_goal(scan, [], text)["focus_mods"], ["botania"])
        for text in ("master Create", "Create automation", "complete Create", "build a factory", "MASTER CREATE"):
            self.assertEqual(resolve_goal(scan, [], text)["focus_mods"], ["create"])

    def test_unknown_goal_and_unavailable_domain_are_explained(self):
        scan = scan_for(["botania:mana_pool"])
        for text in ["purple sentient sandwich orchestra", "reach outer space", "craft atm:star", "make an ATM Star"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                resolve_goal(scan, [], text)

    def test_goal_can_be_a_verified_display_name(self):
        scan = scan_for(["create:precision_mechanism"])
        scan["names"]["create:precision_mechanism"] = "Precision Mechanism"
        self.assertEqual(resolve_goal(scan, [], "craft the precision mechanism")["exact_goal_ids"], ["create:precision_mechanism"])

    def test_explicit_goal_scope_follows_cross_mod_recipe(self):
        scan = scan_for(["demo:machine", "create:precision_mechanism", "minecraft:iron_ingot"],
                        {"demo:machine": [("minecraft:crafting_shaped", ["create:precision_mechanism"])],
                         "create:precision_mechanism": [("minecraft:crafting_shaped", ["minecraft:iron_ingot"])]})
        self.assertEqual(resolve_goal(scan, [], "demo:machine")["focus_mods"], ["create", "demo", "minecraft"])
        with self.assertRaisesRegex(ValueError, "unselected"):
            resolve_goal(scan, ["demo"], "demo:machine")

    def test_vanilla_goal_does_not_pull_in_optional_modded_recycling_routes(self):
        scan = scan_for(["minecraft:beacon", "minecraft:nether_star", "minecraft:glass", "minecraft:obsidian", "demo:nether_star_block"],
                        {"minecraft:nether_star": [("crafting", ["demo:nether_star_block"])]})
        self.assertEqual(resolve_goal(scan, [], "craft a beacon")["focus_mods"], ["minecraft"])

    def test_absent_budget_target_is_synthesized_with_real_prerequisites(self):
        scan = scan_for(["demo:machine", "demo:gear", "minecraft:iron_ingot"],
                        {"demo:machine": [("crafting", ["demo:gear"])],
                         "demo:gear": [("crafting", ["minecraft:iron_ingot"])]})
        original = book_for(["minecraft:iron_ingot"])
        result = apply_campaign(original, scan, [], "demo:machine")
        rows = {q["tasks"][0].get("item"): q for q in quests(result)}
        self.assertEqual(rows["demo:machine"]["dependencies"], [rows["demo:gear"]["id"]])
        self.assertEqual(rows["demo:gear"]["dependencies"], ["q0"])
        self.assertEqual(len(quests(original)), 1)
        _assert_dag(result["chapters"])

    def test_alternative_ingredients_do_not_become_two_mandatory_gates(self):
        scan = scan_for(["demo:machine", "demo:copper_gear", "demo:iron_gear"],
                        {"demo:machine": [("crafting", ["demo:copper_gear"]), ("crafting", ["demo:iron_gear"])]})
        result = apply_campaign(book_for(["demo:copper_gear", "demo:iron_gear"]), scan, [], "demo:machine")
        target = next(q for q in quests(result) if q["tasks"][0].get("item") == "demo:machine")
        self.assertEqual(target["dependencies"], [])

    def test_existing_goal_keeps_id_and_does_not_create_reverse_recipe_cycle(self):
        scan = scan_for(["demo:machine", "demo:gear"], {"demo:machine": [("crafting", ["demo:gear"])]})
        doc = book_for(["demo:machine", "demo:gear"])
        doc["chapters"][1]["quests"][0]["dependencies"] = ["q0"]
        result = apply_campaign(doc, scan, [], "demo:machine")
        self.assertEqual(next(q for q in result["chapters"][-1]["quests"] if q["tasks"][0].get("item") == "demo:machine")["id"], "q0")
        _assert_dag(result["chapters"])

    def test_large_campaign_includes_every_meaningful_branch(self):
        items = ["mod%d:advanced_controller" % i for i in range(19)]
        scan = scan_for(items)
        result = apply_campaign(book_for(items), scan, [], "finish every mod")
        self.assertEqual(len(result["campaign"]["contributing_mods"]), 19)
        graph = {q["id"]: q.get("dependencies", []) for q in quests(result)}
        frontier = list(result["campaign"]["finale_quest_ids"])
        seen = set()
        while frontier:
            key = frontier.pop()
            if key not in seen:
                seen.add(key)
                frontier.extend(graph[key])
        self.assertTrue(set("q%d" % i for i in range(19)) <= seen)
        self.assertLessEqual(max(len(q.get("dependencies", [])) for q in result["chapters"][-1]["quests"]), 6)
        _assert_dag(result["chapters"])

    def test_large_campaign_checkmark_joins_use_real_capstone_icons(self):
        items = ["mod%d:advanced_controller" % i for i in range(43)]
        result = apply_campaign(book_for(items), scan_for(items), [], "finish every mod")
        campaign = result["chapters"][-1]
        final = campaign["quests"][-1]
        self.assertEqual(final["tasks"][0]["type"], "checkmark")
        self.assertEqual(final["icon"], campaign["icon"])
        self.assertIn(final["icon"], items)
        joins = [q for q in campaign["quests"] if q["tasks"][0].get("type") == "checkmark"
                 and len(q.get("dependencies", [])) > 1]
        self.assertTrue(joins)
        self.assertTrue(all(q.get("icon") in items for q in joins))

    def test_open_campaign_keeps_goals_without_locks(self):
        items = ["create:precision_mechanism", "botania:gaia_ingot"]
        result = apply_campaign(book_for(items), scan_for(items), [], progression="open")
        self.assertTrue(all(not q.get("dependencies") for q in result["chapters"][-1]["quests"]))
        self.assertEqual(len(result["campaign"]["contributing_mods"]), 2)

    def test_finale_prose_names_actual_collection_and_detection(self):
        items = ["botania:loki_ring", "demo:advanced_controller"]
        scan = scan_for(items)
        scan["names"] = {items[0]: "Ring of Loki", items[1]: "Advanced Controller"}
        result = apply_campaign(book_for(items), scan, [])
        finale = result["chapters"][-1]["quests"][-1]
        description = " ".join(finale["description"])
        self.assertIn("Ring of Loki", description)
        self.assertIn("Advanced Controller", description)
        self.assertIn("without consuming", description)
        self.assertNotIn("selected mods", description)
        self.assertTrue(all(t.get("consume_items") is False for t in finale["tasks"]))

    def test_same_goal_is_idempotent(self):
        scan = scan_for(["demo:advanced_controller"])
        first = apply_campaign(book_for(["demo:advanced_controller"]), scan, [])
        self.assertEqual(first, apply_campaign(first, scan, []))

    def test_integrated_minimal_vanilla_goal_retains_guidance_and_target(self):
        scan = scan_for(["minecraft:beacon", "minecraft:nether_star", "minecraft:glass", "minecraft:obsidian"])
        result = A.local_quest_doc(scan, [], dict(campaign=True, campaign_goal="craft a beacon",
                                  aesthetic="minimal", density="tiny", vanilla_chapters=False,
                                  mimic=False, progression="open"))
        self.assertEqual(result["campaign"]["focus_mods"], ["minecraft"])
        target = next(q for q in quests(result) if q["tasks"][0].get("item") == "minecraft:beacon")
        self.assertTrue(target.get("description"))
        self.assertEqual(target["dependencies"], [])
        chapters, _, _ = A.build_chapters(result, lambda *_: None,
                                         {"auto_chain": True, "aesthetic": "minimal", "layout": "lanes", "progression": "open"})
        self.assertTrue(all(not q.get("dependencies") for _, c in chapters for q in c["quests"]))

    def test_broken_input_graph_is_rejected(self):
        scan = scan_for(["demo:advanced_controller"])
        doc = book_for(["demo:advanced_controller"])
        doc["chapters"][0]["quests"][0]["dependencies"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "missing prerequisite"):
            apply_campaign(doc, scan, [])


if __name__ == "__main__":
    unittest.main()
