"""Offline goal language: intent, registry grounding and graceful ambiguity."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A
import quest_goal_language as G
from moddb.test_offline_story import scan_for


def fixture():
    items = {"minecraft:beacon", "minecraft:nether_star", "minecraft:glass", "minecraft:obsidian"}
    for mid in ("botania", "create", "ae2", "mekanismgenerators", "mekanism"):
        items.update(ref for raw, _title, _desc in A._curated_chain(mid)
                     for ref in raw.split("|") if ref.startswith(mid + ":"))
    scan = scan_for(items)
    scan["entities"] = {"botania": {"botania:doppleganger"}, "minecraft": {"minecraft:elder_guardian"}}
    scan["entity_names"] = {"botania:doppleganger": "Guardian of Gaia", "minecraft:elder_guardian": "Elder Guardian"}
    scan["names"] = {"botania:life_essence": "Gaia Spirit", "minecraft:beacon": "Beacon"}
    scan["advancements"] = {"botania": {"botania:main/gaia_guardian_kill", "botania:challenge/gaia_guardian_hardmode"}}
    scan["advancement_info"] = {
        "botania:main/gaia_guardian_kill": {"title": "Guardian of Gaia", "description": "Complete the normal ritual."},
        "botania:challenge/gaia_guardian_hardmode": {"title": "Gaia Guardian II"},
    }
    return scan


class GoalLanguageTests(unittest.TestCase):
    def test_typo_reordered_name_and_exact_entity_preserve_boss_intent(self):
        scan = fixture()
        for phrase in ("kill the gaia garden from botania", "beat Guardian of Gaia", "defeat Gaia Guardian", "kill botania:doppleganger"):
            with self.subTest(phrase=phrase):
                goal = G.match_goal(scan, [], phrase)
                self.assertEqual((goal["kind"], goal["target_id"]), ("entity", "botania:doppleganger"))
                self.assertIn("minecraft:beacon", goal["requires"])
                self.assertEqual(goal["task"]["type"], "kill")

    def test_hardmode_uses_verified_advancement_and_never_regular_kill(self):
        scan = fixture()
        goal = G.match_goal(scan, [], "defeat Gaia Guardian II")
        self.assertEqual(goal["kind"], "advancement")
        self.assertEqual(goal["target_id"], "botania:challenge/gaia_guardian_hardmode")
        self.assertIn("kill:botania:doppleganger", goal["requires"])
        scan["advancements"]["botania"].remove(goal["target_id"])
        with self.assertRaisesRegex(ValueError, "no verified available"):
            G.match_goal(scan, [], "defeat Gaia Guardian II")

    def test_collecting_drops_is_not_a_kill_goal(self):
        result = G.match_goal(fixture(), [], "collect Gaia Spirits")
        self.assertEqual((result["kind"], result["target_id"]), ("item", "botania:life_essence"))

    def test_ambiguous_guardian_asks_for_target(self):
        with self.assertRaisesRegex(ValueError, "ambiguous.*Gaia.*Elder|ambiguous.*Elder.*Gaia"):
            G.match_goal(fixture(), [], "kill guardian")

    def test_known_missing_and_unselected_are_not_broad_mod_goals(self):
        scan = fixture()
        with self.assertRaisesRegex(ValueError, "Select"):
            G.match_goal(scan, ["create"], "kill Gaia Guardian")
        scan["mods"] = [m for m in scan["mods"] if m["mod_id"] != "botania"]
        with self.assertRaisesRegex(ValueError, "not installed"):
            G.match_goal(scan, [], "kill Gaia Guardian")

    def test_labels_do_not_invent_registered_entities(self):
        scan = fixture()
        scan["entities"]["botania"].clear()
        with self.assertRaisesRegex(ValueError, "no verified available"):
            G.match_goal(scan, [], "kill Gaia Guardian")

    def test_nonliving_entities_are_never_kill_goals_or_chooser_entries(self):
        scan = fixture()
        invalid = {"minecraft:arrow", "minecraft:oak_boat", "minecraft:minecart", "minecraft:fireball",
                   "minecraft:item_frame", "minecraft:tnt", "botania:mana_burst"}
        for target in invalid:
            scan["entities"].setdefault(target.split(":")[0], set()).add(target)
        scan["entity_names"]["botania:mana_burst"] = "Mana Burst"
        for target in invalid:
            with self.subTest(target=target), self.assertRaises(ValueError):
                G.match_goal(scan, [], "kill " + target)
        with self.assertRaises(ValueError):
            G.match_goal(scan, [], "kill mana burst")
        offered = {row["target_id"] for row in G.available_goals(scan, [], limit=100000) if row["kind"] == "entity"}
        self.assertFalse(offered & invalid)
        self.assertIn("botania:doppleganger", offered)
        self.assertEqual(G.match_goal(scan, [], "kill Gaia Guardian")["kind"], "entity")

    def test_unknown_concepts_do_not_become_mod_mastery(self):
        for phrase in ("kill purple unicorn from Botania", "complete Botania quantum octopus", "squiggle zorbulator"):
            with self.subTest(phrase=phrase), self.assertRaises(ValueError):
                G.match_goal(fixture(), [], phrase)

    def test_concrete_action_and_material_have_different_completion(self):
        scan = fixture()
        for phrase in ("open Alfheim portal", "activate the elven portal in Botania"):
            action = G.match_goal(scan, [], phrase)
            self.assertEqual(action["kind"], "milestone")
            self.assertIn("confirm", action["description"].lower())
            self.assertIn("botania:alfheim_portal", action["requires"])
        material = G.match_goal(scan, [], "craft botania:alfheim_portal")
        self.assertEqual(material["kind"], "item")
        with self.assertRaises(ValueError):
            G.match_goal(scan, [], "travel to Alfheim in Botania")

    def test_mechanic_projects_use_actual_material_anchors_and_manual_tasks(self):
        scan = fixture()
        for phrase, mid in (("automate mana", "botania"), ("build a Create train", "create"),
                            ("Mekanism fission", "mekanismgenerators"), ("AE2 autocrafting", "ae2")):
            with self.subTest(phrase=phrase):
                result = G.match_goal(scan, [], phrase)
                self.assertEqual((result["kind"], result["mod"]), ("milestone", mid))
                self.assertEqual(result["task"]["type"], "checkmark")
                self.assertGreater(len(result["requires"]), 1)
                self.assertIn("confirm", result["description"].lower())

    def test_incomplete_manual_project_is_not_advertised_available(self):
        scan = fixture()
        scan["items"]["botania"].remove("botania:open_crate")
        with self.assertRaisesRegex(ValueError, "no verified available"):
            G.match_goal(scan, [], "automate mana")
        self.assertNotIn("check:botania:goal_mana_automation", {r["target_id"] for r in G.available_goals(scan, [], limit=100000)})

    def test_companion_mod_required_for_fission_project(self):
        scan = fixture()
        scan["mods"] = [m for m in scan["mods"] if m["mod_id"] != "mekanism"]
        with self.assertRaisesRegex(ValueError, "needs mekanism"):
            G.match_goal(scan, [], "build a fission reactor")

    def test_broad_theme_and_mod_mastery_keep_existing_behavior(self):
        for phrase in ("master Botania", "create a magic pack", "I want to become the strongest wizard",
                       "build a massive factory", "reach outer space", "become a chef", "build a warehouse",
                       "harvest crops", "finish everything", "defeat bosses", "master Create", "Create automation"):
            with self.subTest(phrase=phrase):
                self.assertIsNone(G.match_goal(fixture(), [], phrase))

    def test_dimension_and_structure_names_are_scalable_registry_targets(self):
        scan = scan_for(["demo:token"])
        scan["dimensions"] = {"demo": {"demo:moon"}}
        scan["dimension_names"] = {"demo:moon": "Moon"}
        scan["structures"] = {"demo": {"demo:ruined_observatory"}}
        scan["structure_names"] = {"demo:ruined_observatory": "Ruined Observatory"}
        self.assertEqual(G.match_goal(scan, [], "reach the Moon")["kind"], "dimension")
        self.assertEqual(G.match_goal(scan, [], "visit the ruined observatory")["kind"], "structure")

    def test_generic_entity_labels_and_unique_trophy_evidence(self):
        scan = scan_for(["demo:antler_trophy"])
        scan["entities"] = {"demo": {"demo:boss_internal"}}
        scan["entity_names"] = {"demo:boss_internal": "Crowned Stag"}
        scan["names"] = {"demo:antler_trophy": "Ancient Hart Trophy"}
        scan["drops"] = {"demo:antler_trophy": {"entities": {"demo:boss_internal"}}}
        for phrase in ("defeat crowned stag", "kill the Ancient Hart"):
            self.assertEqual(G.match_goal(scan, [], phrase)["target_id"], "demo:boss_internal")

    def test_exact_unknown_ids_are_never_fuzzy_corrected(self):
        with self.assertRaisesRegex(ValueError, "exact ID"):
            G.match_goal(fixture(), [], "craft minecraft:beacno")

    def test_every_chooser_query_roundtrips_including_shared_ids(self):
        scan = scan_for(["demo:guardian", "minecraft:beacon"])
        scan["entities"] = {"demo": {"demo:guardian"}}
        scan["dimensions"] = {"demo": {"demo:sky"}}
        for row in G.available_goals(scan, [], limit=100000):
            with self.subTest(query=row["query"]):
                match = G.match_goal(scan, [], row["query"])
                self.assertEqual((match["kind"], match["target_id"]), (row["kind"], row["target_id"]))

    def test_cache_reuses_index_but_registry_mutation_invalidates(self):
        scan = scan_for(["demo:widget"])
        first = G._index(scan)
        self.assertIs(first, G._index(scan))
        scan["items"]["demo"].add("demo:new_widget")
        self.assertIsNot(first, G._index(scan))


if __name__ == "__main__":
    unittest.main()
