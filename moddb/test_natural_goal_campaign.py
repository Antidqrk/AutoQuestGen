"""Natural goals must survive resolution, campaign planning and FTB export.

Portable fixtures describe installed content; no API, game or live pack writes.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moddb.test_offline_story import scan_for
from moddb.test_campaign import book_for, quests
import autoquestgen as A
from quest_campaign import resolve_goal, apply_campaign, _assert_dag


GAIA = "botania:doppleganger"


def botania_scan():
    guide = json.loads((Path(__file__).parent / "chains_research/botania.json").read_text(encoding="utf-8"))
    items = [row["id"].split("|")[0] for row in guide["chain"]
             if row["id"].startswith("botania:")]
    items += ["minecraft:beacon", "minecraft:nether_star", "minecraft:glass", "minecraft:obsidian"]
    scan = scan_for(items)
    scan["mods"][0].update(name="Botania", category="magic")
    scan["entities"] = {"botania": {GAIA}}
    scan["names"].update({GAIA: "Gaia Guardian", "botania:life_essence": "Gaia Spirit",
                          "botania:gaia_ingot": "Gaia Spirit Ingot"})
    return scan


def final_quests(doc):
    wanted = set(doc["campaign"]["finale_quest_ids"])
    return [q for q in quests(doc) if q["id"] in wanted]


def ancestors(doc, qid):
    rows = {q["id"]: q for q in quests(doc)}
    seen, todo = set(), list(rows[qid].get("dependencies", []))
    while todo:
        parent = todo.pop()
        if parent not in seen:
            seen.add(parent)
            todo.extend(rows[parent].get("dependencies", []))
    return [rows[key] for key in seen]


class NaturalGoalCampaignTests(unittest.TestCase):
    def test_gaia_typo_natural_name_and_entity_id_keep_combat_intent(self):
        scan = botania_scan()
        for phrase in ("kill the gaia garden from botania", "beat the Gaia Guardian",
                       "defeat botania:doppleganger", "kill the Gaia Guardian from Botania"):
            with self.subTest(phrase=phrase):
                plan = resolve_goal(scan, ["botania"], phrase)
                self.assertEqual(plan["kind"], "objective")
                self.assertEqual(plan["goal_target"]["kind"], "entity")
                self.assertEqual(plan["goal_target"]["target_id"], GAIA)
                self.assertEqual(plan["goal_target"]["guide_ref"], "kill:" + GAIA)
                self.assertEqual(plan["exact_goal_ids"], [])
                self.assertIn("botania", plan["focus_mods"])

    def test_sparse_book_grows_a_real_preparation_route_and_ends_at_the_boss(self):
        scan = botania_scan()
        original = book_for(["botania:lexicon"])
        before = copy.deepcopy(original)
        result = apply_campaign(original, scan, ["botania"], "kill the gaia garden from botania")
        goals = final_quests(result)
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["tasks"][0]["type"], "kill")
        self.assertEqual(goals[0]["tasks"][0]["entity"], GAIA)
        self.assertEqual(goals[0]["tasks"][0].get("value", 1), 1)
        items = {t["item"] for q in ancestors(result, goals[0]["id"])
                 for t in q["tasks"] if t.get("type") == "item"}
        self.assertTrue({"botania:gaia_pylon", "botania:terrasteel_ingot",
                         "botania:mana_pool", "botania:pure_daisy", "minecraft:beacon"} <= items)
        self.assertFalse({"botania:life_essence", "botania:gaia_ingot", "botania:dice"} & items)
        self.assertEqual(original, before)
        _assert_dag(result["chapters"])

    def test_tiny_generation_budget_does_not_replace_or_truncate_requested_boss(self):
        scan = botania_scan()
        opts = dict(campaign=True, campaign_goal="kill the gaia garden from botania",
                    density="tiny", target="8", seed=17, mimic=False, progression="linear",
                    vanilla_chapters=False, collections=False, aesthetic="balanced")
        doc = A.local_quest_doc(scan, ["botania"], opts)
        goal = final_quests(doc)[0]
        self.assertEqual(goal["tasks"][0].get("entity"), GAIA)
        prerequisite_items = {t.get("item") for q in ancestors(doc, goal["id"]) for t in q["tasks"]}
        self.assertIn("botania:gaia_pylon", prerequisite_items)
        self.assertIn("botania:terrasteel_ingot", prerequisite_items)
        self.assertGreater(len(prerequisite_items), 8)
        _assert_dag(doc["chapters"])

    def test_existing_boss_id_is_reused_without_turning_its_drops_into_gates(self):
        scan = botania_scan()
        doc = {"chapters": [{"id": "botania", "title": "Botania", "quests": [
            {"id": "ritual", "title": "Arena", "tasks": [{"type": "item", "item": "botania:gaia_pylon"}], "dependencies": []},
            {"id": "existing_boss", "title": "Guardian", "tasks": [{"type": "kill", "entity": GAIA, "value": 3}], "dependencies": ["ritual"]},
            {"id": "after_boss", "title": "Spirit", "tasks": [{"type": "item", "item": "botania:life_essence"}], "dependencies": ["existing_boss"]},
        ]}]}
        result = apply_campaign(doc, scan, ["botania"], "defeat the Gaia Guardian")
        self.assertEqual(result["campaign"]["finale_quest_ids"], ["existing_boss"])
        goal = final_quests(result)[0]
        self.assertEqual(goal["tasks"][0]["value"], 1)
        self.assertNotIn("after_boss", {q["id"] for q in ancestors(result, goal["id"])})
        self.assertEqual(sum(q["id"] == "existing_boss" for q in quests(result)), 1)
        self.assertNotIn("after_boss", {q["id"] for q in quests(result)})
        _assert_dag(result["chapters"])

    def test_real_kill_objective_survives_clean_build_and_snbt_roundtrip(self):
        scan = botania_scan()
        doc = apply_campaign(book_for(["botania:lexicon"]), scan, ["botania"], "kill Gaia Guardian")
        cleaned = A.clean_doc(doc, scan, lambda *_: None)
        chapters, groups, extra = A.build_chapters(cleaned, lambda *_: None,
                                                  {"layout": "reference", "auto_chain": True, "_scan": scan})
        with tempfile.TemporaryDirectory() as folder:
            A.write_snbt(Path(folder), chapters, groups, True, lambda *_: None, {}, extra.get("reward_tables"))
            exported = [A.snbt_loads(p.read_text(encoding="utf-8"))
                        for p in (Path(folder) / "chapters").glob("*.snbt")]
        kills = [t for c in exported for q in c["quests"] for t in q["tasks"]
                 if t.get("type") == "kill" and t.get("entity") == GAIA]
        self.assertEqual(len(kills), 1)
        self.assertEqual(kills[0]["value"], 1)
        _assert_dag(exported)

    def test_open_mode_keeps_boss_and_removes_campaign_locks(self):
        scan = botania_scan()
        doc = A.local_quest_doc(scan, ["botania"], dict(campaign=True,
            campaign_goal="kill the gaia garden from botania", density="tiny", seed=17,
            progression="open", mimic=False, vanilla_chapters=False, collections=False))
        self.assertEqual(final_quests(doc)[0]["tasks"][0].get("entity"), GAIA)
        chapters, _, _ = A.build_chapters(doc, lambda *_: None,
                                         {"layout": "reference", "auto_chain": True, "progression": "open"})
        self.assertTrue(all(not q.get("dependencies") for _, c in chapters for q in c["quests"]))

    def test_loot_request_stays_an_item_request(self):
        scan = botania_scan()
        plan = resolve_goal(scan, ["botania"], "collect Gaia Spirits")
        self.assertEqual(plan["kind"], "item")
        self.assertEqual(plan["exact_goal_ids"], ["botania:life_essence"])
        doc = apply_campaign(book_for(["botania:lexicon"]), scan, ["botania"], "collect Gaia Spirits")
        goal = final_quests(doc)[0]
        self.assertEqual(goal["tasks"][0].get("type"), "item")
        self.assertEqual(goal["tasks"][0].get("item"), "botania:life_essence")

    def test_automation_goal_has_honest_manual_completion(self):
        scan = botania_scan()
        text = "automate mana production in Botania"
        plan = resolve_goal(scan, ["botania"], text)
        self.assertEqual(plan["kind"], "objective")
        self.assertEqual(plan["goal_target"]["kind"], "milestone")
        doc = apply_campaign(book_for(["botania:lexicon"]), scan, ["botania"], text)
        goal = final_quests(doc)[0]
        self.assertEqual(goal["tasks"][0]["type"], "checkmark")
        self.assertIn("confirm", " ".join(goal.get("description", [])).lower())
        items = {t.get("item") for q in ancestors(doc, goal["id"]) for t in q["tasks"]}
        self.assertTrue({"botania:mana_pool", "botania:mana_spreader"} <= items)
        _assert_dag(doc["chapters"])

    def test_ambiguous_guardian_is_not_silently_chosen(self):
        scan = botania_scan()
        scan["entities"]["minecraft"] = {"minecraft:elder_guardian"}
        scan["names"]["minecraft:elder_guardian"] = "Elder Guardian"
        with self.assertRaises(ValueError):
            resolve_goal(scan, ["botania"], "kill the guardian")
        self.assertEqual(resolve_goal(scan, ["botania"], "kill the Gaia Guardian from Botania")
                         ["goal_target"]["target_id"], GAIA)

    def test_exact_id_collision_obeys_explicit_action(self):
        scan = scan_for(["demo:guardian"])
        scan["entities"] = {"demo": {"demo:guardian"}}
        scan["names"]["demo:guardian"] = "Guardian"
        self.assertEqual(resolve_goal(scan, ["demo"], "craft demo:guardian")["kind"], "item")
        self.assertEqual(resolve_goal(scan, ["demo"], "kill demo:guardian")["goal_target"]["kind"], "entity")
        with self.assertRaises(ValueError):
            resolve_goal(scan, ["demo"], "demo:guardian")

    def test_absent_entity_and_unselected_mod_are_rejected(self):
        scan = botania_scan()
        scan["entities"] = {}
        with self.assertRaises(ValueError):
            resolve_goal(scan, ["botania"], "kill the Gaia Guardian from Botania")
        scan = botania_scan()
        scan["mods"].append({"mod_id": "create", "name": "Create", "category": "tech"})
        scan["items"]["create"] = {"create:andesite_alloy"}
        scan["craftable"]["create"] = {"create:andesite_alloy"}
        with self.assertRaises(ValueError):
            resolve_goal(scan, ["create"], "kill the Gaia Guardian from Botania")

    def test_unknown_concrete_goal_is_not_downgraded_to_whole_mod_mastery(self):
        for phrase in ("kill the purple space potato in Botania", "build a quantum toaster in Botania"):
            with self.subTest(phrase=phrase), self.assertRaises(ValueError):
                resolve_goal(botania_scan(), ["botania"], phrase)

    def test_named_mod_mastery_remains_a_broad_campaign(self):
        self.assertEqual(resolve_goal(botania_scan(), ["botania"], "master Botania")["kind"], "branches")

    def test_dimension_goal_named_like_its_mod_means_visit_the_dimension(self):
        scan = scan_for(["twilightforest:raven_feather"])
        scan["mods"][0]["name"] = "The Twilight Forest"
        scan["dimensions"] = {"twilightforest": {"twilightforest:twilight_forest"}}
        scan["dimension_names"] = {"twilightforest:twilight_forest": "Twilight Forest"}
        for phrase in ("enter the Twilight Forest", "visit twilightforest:twilight_forest"):
            with self.subTest(phrase=phrase):
                plan = resolve_goal(scan, ["twilightforest"], phrase)
                self.assertEqual(plan["kind"], "objective")
                self.assertEqual(plan["goal_target"]["kind"], "dimension")
                doc = apply_campaign(book_for(["twilightforest:raven_feather"]), scan, ["twilightforest"], phrase)
                self.assertEqual(final_quests(doc)[0]["tasks"][0].get("dimension"), "twilightforest:twilight_forest")

    def test_structure_goal_targets_the_place_not_its_boss_trophy(self):
        scan = scan_for(["twilightforest:naga_trophy"])
        scan["mods"][0]["name"] = "The Twilight Forest"
        scan["structures"] = {"twilightforest": {"twilightforest:naga_courtyard"}}
        scan["structure_names"] = {"twilightforest:naga_courtyard": "Naga Courtyard"}
        phrase = "find the Naga Courtyard in Twilight Forest"
        doc = apply_campaign(book_for(["twilightforest:naga_trophy"]), scan, ["twilightforest"], phrase)
        task = final_quests(doc)[0]["tasks"][0]
        self.assertEqual(task.get("type"), "structure")
        self.assertEqual(task.get("structure"), "twilightforest:naga_courtyard")

    def test_gaia_ii_uses_the_scanned_hardmode_advancement(self):
        scan = botania_scan()
        hardmode = "botania:challenge/gaia_guardian_hardmode"
        scan["advancements"] = {"botania": {hardmode}}
        scan["advancement_info"] = {hardmode: {"title": "Gaia Guardian II", "icon": "botania:life_essence"}}
        doc = apply_campaign(book_for(["botania:lexicon"]), scan, ["botania"], "defeat Gaia Guardian II")
        goal = final_quests(doc)[0]
        self.assertEqual(goal["tasks"][0].get("type"), "advancement")
        self.assertEqual(goal["tasks"][0].get("advancement"), hardmode)
        before = ancestors(doc, goal["id"])
        self.assertIn("botania:gaia_ingot", {t.get("item") for q in before for t in q["tasks"]})
        self.assertIn(GAIA, {t.get("entity") for q in before for t in q["tasks"]})
        _assert_dag(doc["chapters"])
        without_detector = botania_scan()
        with self.assertRaises(ValueError):
            resolve_goal(without_detector, ["botania"], "defeat Gaia Guardian II")

    def test_alfheim_gateway_does_not_promise_an_enterable_dimension(self):
        with self.assertRaises(ValueError):
            resolve_goal(botania_scan(), ["botania"], "travel to Alfheim in Botania")

    def test_alternative_guide_requirement_chooses_the_complete_available_route(self):
        scan = scan_for(["demo:first", "demo:second", "demo:start"])
        scan["entities"] = {"demo": {"demo:guardian"}}
        needs = {"kill:demo:guardian": ["demo:first|demo:second"],
                 "demo:first": ["demo:missing"], "demo:second": ["demo:start"], "demo:start": []}
        guide = [(ref, ref, "Prepare the encounter.") for ref in needs]
        with patch.object(A, "_GUIDED_REQUIREMENTS", needs), patch.object(A, "_curated_chain", side_effect=lambda mid: guide if mid == "demo" else []):
            doc = apply_campaign(book_for(["demo:start"]), scan, ["demo"], "kill demo:guardian")
        goal = final_quests(doc)[0]
        required = {t.get("item") for q in ancestors(doc, goal["id"]) for t in q["tasks"]}
        self.assertIn("demo:second", required)
        self.assertNotIn("demo:first", required)
        _assert_dag(doc["chapters"])

    def test_cross_mod_advancement_prerequisite_requires_that_mod_selected(self):
        scan = scan_for(["demo:start", "other:medal"])
        scan["entities"] = {"demo": {"demo:guardian"}}
        scan["advancements"] = {"other": {"other:trial"}}
        needs = {"kill:demo:guardian": ["advancement:other:trial"]}
        guide = [("kill:demo:guardian", "Guardian", "Prepare the encounter.")]
        with patch.object(A, "_GUIDED_REQUIREMENTS", needs), patch.object(A, "_curated_chain", side_effect=lambda mid: guide if mid == "demo" else []):
            with self.assertRaisesRegex(ValueError, "(?i)select|unselected"):
                resolve_goal(scan, ["demo"], "kill demo:guardian")
            plan = resolve_goal(scan, ["demo", "other"], "kill demo:guardian")
            self.assertIn("other", plan["focus_mods"])


if __name__ == "__main__":
    unittest.main()
