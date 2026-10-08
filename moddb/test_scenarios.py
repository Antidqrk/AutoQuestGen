"""Editable offline campaign scenarios remain factual and author-controlled."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from moddb.test_offline_story import scan_for
from moddb.test_natural_goal_campaign import botania_scan
from quest_scenarios import propose_scenario, validate_scenario, available_capabilities, _CAPABILITIES


def restoration_scan():
    scan = botania_scan()
    for capability in _CAPABILITIES:
        for item in capability["required_items"]:
            scan["items"].setdefault(item.split(":")[0], set()).add(item)
    scan["vanilla"].add("minecraft:oak_sapling")
    return scan


class ScenarioTests(unittest.TestCase):
    def test_restoration_proposal_is_deterministic_and_uses_real_tools(self):
        scan = restoration_scan()
        first = propose_scenario(scan, ["botania"], "Begin with Botania in a barren Overworld and restore life", allow_vanilla=True)
        second = propose_scenario(scan, ["botania"], first["premise"], allow_vanilla=True)
        self.assertEqual(first, second)
        self.assertEqual([s["role"] for s in first["stages"]], ["start", "water", "soil", "grass", "trees"])
        self.assertEqual([s["goal"] for s in first["stages"]][1:], [
            "craft botania:water_rod", "craft botania:dirt_rod", "craft botania:grass_seeds", "craft minecraft:oak_sapling"])
        self.assertTrue(first["requires_review"])
        self.assertTrue(all(s["completion"] == "manual" for s in first["stages"]))

    def test_restoration_validate_resolves_tools_and_keeps_manual_world_actions(self):
        scan = restoration_scan()
        proposal = propose_scenario(scan, ["botania"], "Restore a barren world", allow_vanilla=True)
        report = validate_scenario(scan, ["botania"], proposal, strict=True)
        self.assertTrue(report["valid"])
        for row in report["stages"]:
            self.assertEqual(row["task"]["type"], "checkmark")
            self.assertEqual(row["goal_plan"]["kind"], "item")
            self.assertIn("confirm", row["description"].lower())
        water = report["stages"][1]
        self.assertEqual(water["goal_plan"]["exact_goal_ids"], ["botania:water_rod"])
        self.assertIn(["botania:mana_tablet"], [p["exact_goal_ids"] for p in water["goal_plans"]])

    def test_bootstrap_is_explicit_and_does_not_promise_worldgen_changes(self):
        plan = propose_scenario(restoration_scan(), [], "Restore the wasteland", allow_vanilla=True)
        notes = " ".join(plan["notes"])
        for phrase in ("does not change world generation", "water bottle", "dirt", "tall grass", "cannot bootstrap"):
            self.assertIn(phrase, notes)
        report = validate_scenario(restoration_scan(), [], plan)
        self.assertIn("water bottle", " ".join(report["notes"]))
        self.assertEqual(report["title"], plan["title"])

    def test_vanilla_trees_require_author_permission_and_a_real_sapling(self):
        scan = restoration_scan()
        plan = propose_scenario(scan, [], "Restore the wasteland")
        tree = plan["stages"][-1]
        self.assertEqual(tree["goal"], "")
        self.assertIn("disabled", tree["description"])
        self.assertFalse(validate_scenario(scan, [], plan)["valid"])
        scan["vanilla"].remove("minecraft:oak_sapling")
        self.assertNotIn("trees", {c["role"] for c in available_capabilities(scan, [], allow_vanilla=True)})

    def test_only_allowed_installed_mods_can_supply_suggestions(self):
        scan = restoration_scan()
        self.assertEqual(available_capabilities(scan, ["create"]), [])
        plan = propose_scenario(scan, ["create"], "Restore a barren world")
        self.assertTrue(all(not row["goal"] for row in plan["stages"]))
        self.assertIn("installed and selected", plan["stages"][0]["description"])

    def test_missing_or_unobtainable_tool_never_becomes_a_restoration_method(self):
        scan = restoration_scan()
        scan["unobtainable"] = {"botania:water_rod"}
        self.assertNotIn("water", {c["role"] for c in available_capabilities(scan, [])})
        plan = propose_scenario(scan, [], "Restore the wasteland", allow_vanilla=True)
        self.assertEqual(plan["stages"][1]["goal"], "")

    def test_author_mode_and_unknown_plot_remain_editable_empty_drafts(self):
        scan = restoration_scan()
        for options in ({"mode": "authored", "premise": "Restore the world"},
                        {"mode": "assisted", "premise": "A kingdom of time-travelling sky pirates"}):
            result = propose_scenario(scan, [], **options)
            self.assertEqual(result["stages"], [])
            self.assertTrue(result["requires_review"])
            self.assertFalse(validate_scenario(scan, [], result)["valid"])

    def test_author_order_and_goal_text_survive_validation(self):
        scan = restoration_scan()
        stages = [
            {"id": "soil_first", "title": "My first stage", "goal": "craft botania:dirt_rod", "mod": "botania", "depends_on": [], "completion": "objective"},
            {"id": "later_water", "title": "Then my river", "goal": "craft botania:water_rod", "mod": "botania", "depends_on": ["soil_first"], "completion": "manual", "description": "Place water in the village riverbed, then confirm."},
        ]
        original = deepcopy(stages)
        report = validate_scenario(scan, [], stages, strict=True)
        self.assertEqual(stages, original)
        self.assertEqual([s["id"] for s in report["stages"]], ["soil_first", "later_water"])
        self.assertEqual(report["stages"][1]["description"], original[1]["description"])
        self.assertEqual(report["stages"][1]["depends_on"], ["soil_first"])

    def test_arbitrary_concrete_natural_goals_work_for_authored_stages(self):
        row = {"id": "guardian", "title": "Protect the Grove", "goal": "kill the gaia garden from botania", "depends_on": [], "completion": "objective"}
        report = validate_scenario(restoration_scan(), ["botania"], [row], strict=True)
        stage = report["stages"][0]
        self.assertEqual(stage["mod"], "botania")
        self.assertEqual(stage["task"]["type"], "kill")
        self.assertEqual(stage["task"]["entity"], "botania:doppleganger")

    def test_author_cannot_turn_restoration_into_simple_item_detection(self):
        scan = restoration_scan()
        plan = propose_scenario(scan, [], "Restore a barren world", allow_vanilla=True)
        plan["stages"][1]["completion"] = "objective"
        result = validate_scenario(scan, [], plan)
        self.assertFalse(result["valid"])
        self.assertTrue(any("requires manual confirmation" in x["message"] for x in result["issues"]))

    def test_edited_capability_must_still_match_its_actual_tool(self):
        scan = restoration_scan()
        plan = propose_scenario(scan, [], "Restore a barren world", allow_vanilla=True)
        plan["stages"][1]["goal"] = "craft botania:dirt_rod"
        result = validate_scenario(scan, [], plan)
        self.assertFalse(result["valid"])
        self.assertTrue(any("no longer matches" in x["message"] for x in result["issues"]))

    def test_missing_forward_and_circular_dependencies_block_without_reordering(self):
        scan = restoration_scan()
        rows = [
            {"id": "a", "goal": "craft botania:dirt_rod", "depends_on": ["b"]},
            {"id": "b", "goal": "craft botania:water_rod", "depends_on": ["a"]},
        ]
        result = validate_scenario(scan, [], rows)
        self.assertFalse(result["valid"])
        self.assertEqual([r["id"] for r in result["stages"]], ["a", "b"])
        self.assertTrue(any("earlier stage" in x["message"] for x in result["issues"]))
        with self.assertRaisesRegex(ValueError, "Review the scenario"):
            validate_scenario(scan, [], rows, strict=True)

    def test_duplicate_stage_ids_are_reported(self):
        row = {"id": "same", "goal": "craft botania:dirt_rod", "depends_on": []}
        report = validate_scenario(restoration_scan(), [], [row, deepcopy(row)])
        self.assertFalse(report["valid"])
        self.assertTrue(any("same ID" in x["message"] for x in report["issues"]))

    def test_malformed_and_excessive_stage_lists_are_rejected_before_resolution(self):
        scan = restoration_scan()
        with self.assertRaisesRegex(ValueError, "stage records"):
            validate_scenario(scan, [], ["not a stage"])
        with self.assertRaisesRegex(ValueError, "64 stages"):
            validate_scenario(scan, [], [{"id": str(i)} for i in range(65)])
        for config in ({"stages": [], "allow_vanilla": "false"}, {"stages": [], "notes": None},
                       {"stages": [{"id": "test", "capability_id": ["bad"]}]},
                       {"stages": [{"id": 4}]}, {"stages": [], "title": ["bad"]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_scenario(scan, [], config)

    def test_broad_mod_mastery_is_not_a_concrete_story_stage(self):
        result = validate_scenario(restoration_scan(), [], [{"id": "start", "goal": "master Botania", "depends_on": []}])
        self.assertFalse(result["valid"])
        self.assertTrue(any("too broad" in x["message"] for x in result["issues"]))

    def test_failed_primary_goal_is_not_replaced_by_valid_supporting_goal(self):
        result = validate_scenario(restoration_scan(), [], [{"id": "bad", "goal": "craft botania:missing", "requirements": ["craft botania:mana_pool"]}])
        self.assertFalse(result["valid"])
        self.assertNotIn("goal_plan", result["stages"][0])

    def test_manual_checkpoint_needs_an_actual_completion_description(self):
        result = validate_scenario(restoration_scan(), [], [{"id": "empty", "goal": "craft botania:dirt_rod", "completion": "manual"}])
        self.assertFalse(result["valid"])
        self.assertTrue(any("concrete description" in x["message"] for x in result["issues"]))

    def test_cross_mod_water_choices_follow_preferred_start_not_catalog_order(self):
        items = {item for capability in _CAPABILITIES for item in capability["required_items"]}
        scan = scan_for(items)
        scan["vanilla"].add("minecraft:oak_sapling")
        for preferred, expected in (("botania", "botania_restore_water"), ("create", "create_transport_water"), ("ars_nouveau", "ars_conjure_water")):
            with self.subTest(preferred=preferred):
                plan = propose_scenario(scan, [], "Restore a barren Overworld", preferred_start=preferred, allow_vanilla=True)
                self.assertEqual(plan["stages"][0]["mod"], preferred)
                self.assertEqual(plan["stages"][1]["capability_id"], expected)
        create = next(c for c in available_capabilities(scan, []) if c["id"] == "create_transport_water")
        self.assertIn("existing water", create["description"])
        self.assertIn("does not create", create["description"])
        ars = next(c for c in available_capabilities(scan, []) if c["id"] == "ars_conjure_water")
        self.assertIn("water bucket", ars["description"])

    def test_ancient_tree_caveats_and_vanilla_tree_default_are_preserved(self):
        scan = scan_for({item for c in _CAPABILITIES for item in c["required_items"]})
        scan["vanilla"].add("minecraft:oak_sapling")
        default = propose_scenario(scan, [], "Restore the barren Overworld", allow_vanilla=True)
        self.assertEqual(default["stages"][-1]["capability_id"], "vanilla_restore_trees")
        aura = propose_scenario(scan, [], "Restore the barren Overworld", preferred_start="naturesaura", allow_vanilla=True)
        self.assertEqual(aura["stages"][-1]["capability_id"], "naturesaura_restore_trees")
        self.assertIn("leaves gradually die", aura["stages"][-1]["description"])
        self.assertIn("does not work outside the Overworld", aura["stages"][-1]["description"])


if __name__ == "__main__":
    unittest.main()
