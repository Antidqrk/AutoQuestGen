"""Pack-script goals preserve complete routes, quantities and special stacks."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from moddb.test_offline_story import scan_for
from moddb.test_campaign import book_for, quests
from quest_campaign import apply_campaign, resolve_goal, _assert_dag


def stack(item, count=1, components=""):
    return {"id": item, "count": count, "components": components,
            "identity": item + components, "raw": str(count) + "x " + item + components}


def recipe(output, ingredients, line=1):
    return {"output": output, "ingredients": ingredients, "fluids": [],
            "kind": "modern_industrialization:star_altar", "source": "kubejs/server_scripts/star.js",
            "line": line, "key": "star.js:" + str(line), "counts": {
                x["identity"]: x["count"] for x in ingredients}}


def fixture(routes, extra=(), ordinary=None):
    items = {"modern_industrialization:star_altar", *extra}
    for route in routes:
        items.add(route["output"]["id"])
        items.update(x["id"] for x in route["ingredients"] if not x["id"].startswith("#"))
    scan = scan_for(sorted(items), ordinary)
    scan["pack_recipe_report"] = {"recipes": routes, "skipped": [], "sourcefiles": ["star.js"]}
    return scan


class PackCampaignTests(unittest.TestCase):
    def test_component_creatures_replace_format_templates_and_keep_exact_identity(self):
        data = '[hostilenetworks:data_model="hostilenetworks:ender_dragon",hostilenetworks:data=1255]'
        main = recipe(stack("pack:star"), [stack("hostilenetworks:data_model", 1, data),
            stack("justdirethings:creaturecatcher", 1, '[justdirethings:entitytype="occultism:dragon_familiar"]'),
            stack("productivebees:spawn_egg_configurable_bee", 1,
                  '[entity_data={id:"productivebees:configurable_bee",type:"productivebees:soul_lava"}]')])
        scan = fixture([main])
        scan["names"]["hostilenetworks:data_model"] = "%s Data Model"
        result = apply_campaign(book_for([]), scan, [], "pack:star")
        prepared = [q for q in quests(result) if q.get("_pack_components")]
        self.assertTrue(any("Ender Dragon Data Model" in q["title"] for q in prepared))
        self.assertTrue(any("Dragon Familiar" in q["title"] for q in prepared))
        self.assertTrue(any("Soul Lava Bee Spawn Egg" in q["title"] for q in prepared))
        self.assertTrue(all("%s" not in q["title"] and q["tasks"][0]["type"] == "checkmark" for q in prepared))
        model = next(q for q in prepared if q["_pack_components"] == data)
        self.assertIn("Model data: 1,255", " ".join(model["description"]))
        self.assertNotIn(data, " ".join(model["description"]))

    def test_reviewed_event_drop_bypasses_only_nested_unavailable_optional_recipe(self):
        import autoquestgen as A
        main = recipe(stack("pack:star"), [stack("magic:soul")])
        optional = recipe(stack("magic:soul"), [stack("missing:simulation")], 2)
        scan = fixture([main, optional])
        scan["items"].pop("missing")
        scan["mods"] = [m for m in scan["mods"] if m["mod_id"] != "missing"]
        with patch.object(A, "_GUIDE_ACQUISITION", {}):
            with self.assertRaises(ValueError):
                resolve_goal(scan, [], "pack:star")
        with patch.object(A, "_GUIDE_ACQUISITION", {"magic:soul": "Reviewed event drop"}):
            self.assertNotIn("missing", resolve_goal(scan, [], "pack:star")["focus_mods"])
            # The final explicit project still cannot silently replace its
            # unavailable pack-script route with an unrelated acquisition.
            with self.assertRaises(ValueError):
                resolve_goal(scan, [], "magic:soul")
            scan["unobtainable"] = {"magic:soul"}
            with self.assertRaises(ValueError):
                resolve_goal(scan, [], "pack:star")

    def test_end_ship_head_does_not_require_optional_simulation_recipe(self):
        main = recipe(stack("pack:star"), [stack("minecraft:dragon_head")])
        optional = recipe(stack("minecraft:dragon_head"), [stack("missing:simulation")], 2)
        scan = fixture([main, optional])
        scan["items"].pop("missing")
        scan["mods"] = [m for m in scan["mods"] if m["mod_id"] != "missing"]
        plan = resolve_goal(scan, [], "pack:star")
        self.assertNotIn("missing", plan["focus_mods"])
        self.assertEqual(1, plan["pack_recipe_goals"]["pack:star"]["line"])

    def test_first_complete_script_route_beats_recycling_and_keeps_all_thirteen_inputs(self):
        inputs = [stack("branch%d:material" % i, i + 2) for i in range(13)]
        main = recipe(stack("pack:star"), inputs, 10)
        alternate = recipe(stack("pack:star"), [stack("other:rare_part")], 20)
        scan = fixture([main, alternate], ordinary={"pack:star": [("crafting", ["pack:star_shard"])]})
        doc = book_for(x["id"] for x in inputs)
        original = copy.deepcopy(doc)
        result = apply_campaign(doc, scan, [], "pack:star")
        final = next(q for q in quests(result) if q["tasks"][0].get("item") == "pack:star")
        campaign = result["chapters"][-1]
        spokes = {q["id"]: q for q in campaign["quests"] if q.get("_pack_identity")}
        self.assertEqual(13, len(final["dependencies"]))
        self.assertEqual(set(spokes), set(final["dependencies"]))
        self.assertEqual([i + 2 for i in range(13)], [spokes[key]["tasks"][0]["count"] for key in
                         [next(q["id"] for q in spokes.values() if q["_pack_identity"] == "branch%d:material" % i) for i in range(13)]])
        self.assertTrue(all(q["dependencies"] for q in spokes.values()))
        self.assertTrue(all(q["tasks"][0]["consume_items"] is False for q in spokes.values()))
        self.assertEqual("pentagon", final["shape"])
        self.assertGreaterEqual(final["size"], 2)
        self.assertNotIn("other", result["campaign"]["focus_mods"])
        self.assertIn("modern_industrialization", result["campaign"]["focus_mods"])
        self.assertEqual(10, result["campaign"]["pack_recipe_goals"]["pack:star"]["line"])
        self.assertEqual(doc, original)
        _assert_dag(result["chapters"])

    def test_special_component_milestone_is_manual_and_nested_batches_are_exact(self):
        data = '[enchantments={levels:{"minecraft:unbreaking":1}},custom_name=\'{"text":"Awakened Alloy"}\']'
        main = recipe(stack("pack:star"), [stack("alloy:block", 5, data)], 30)
        awakening = recipe(stack("alloy:block", 2, data), [stack("alloy:block", 1), stack("magic:essence", 4)], 5)
        scan = fixture([main, awakening])
        result = apply_campaign(book_for(["alloy:block", "magic:essence"]), scan, [], "pack:star")
        material = next(q for q in quests(result) if q.get("_pack_identity") == "alloy:block" + data)
        self.assertEqual("checkmark", material["tasks"][0]["type"])
        self.assertNotIn("item", material["tasks"][0])
        self.assertEqual(5, material["_pack_count"])
        self.assertEqual(data, material["_pack_components"])
        self.assertIn("Awakened Alloy", material["title"])
        self.assertIn("Confirm", " ".join(material["description"]))
        supporting = {q["id"]: q for q in quests(result)}
        amounts = {supporting[key]["_pack_identity"]: supporting[key]["_pack_count"] for key in material["dependencies"]}
        self.assertEqual({"alloy:block": 3, "magic:essence": 12}, amounts)
        _assert_dag(result["chapters"])

    def test_plain_goal_never_uses_same_item_component_output_as_ordinary_route(self):
        enhanced = recipe(stack("pack:star", 1, '[custom_name=\'"Enhanced"\']'), [stack("magic:essence")])
        plain = recipe(stack("pack:star"), [stack("plain:part")], 2)
        scan = fixture([enhanced, plain])
        scope = resolve_goal(scan, [], "pack:star")["focus_mods"]
        self.assertIn("plain", scope)
        self.assertNotIn("magic", scope)

    def test_recursive_first_route_is_skipped_for_real_non_circular_route(self):
        cycle = recipe(stack("pack:star"), [stack("pack:shard")], 1)
        real = recipe(stack("pack:star"), [stack("magic:core")], 2)
        scan = fixture([cycle, real], ordinary={"pack:shard": [("crafting", ["pack:star"])]})
        result = apply_campaign(book_for(["magic:core"]), scan, [], "pack:star")
        self.assertEqual(2, result["campaign"]["pack_recipe_goals"]["pack:star"]["line"])
        self.assertFalse(any(q.get("_pack_identity") == "pack:shard" for q in quests(result)))
        _assert_dag(result["chapters"])

    def test_no_viable_script_route_is_explained_instead_of_using_cheap_jar_recipe(self):
        route = recipe(stack("pack:star"), [stack("missing:part")])
        scan = fixture([route], extra=["minecraft:stick"], ordinary={"pack:star": [("crafting", ["minecraft:stick"])]})
        scan["unobtainable"] = {"missing:part"}
        with self.assertRaisesRegex(ValueError, "non-circular pack recipe"):
            resolve_goal(scan, [], "pack:star")

    def test_reverse_chapter_dependency_cannot_form_cycle_and_open_keeps_no_locks(self):
        scan = fixture([recipe(stack("pack:star"), [stack("magic:core", 6)])])
        doc = book_for(["pack:star", "magic:core"])
        doc["chapters"][1]["quests"][0]["dependencies"] = ["q0"]
        result = apply_campaign(doc, scan, [], "pack:star")
        material = next(q for q in quests(result) if q.get("_pack_identity") == "magic:core")
        self.assertEqual([], material["dependencies"])
        _assert_dag(result["chapters"])
        opened = apply_campaign(book_for([]), scan, [], "pack:star", progression="open")
        self.assertTrue(all(not q.get("dependencies") for q in quests(opened)))

    def test_component_route_contributes_upstream_and_machine_mods_to_scope(self):
        data = '[enchantments={levels:{"minecraft:unbreaking":1}}]'
        main = recipe(stack("pack:star"), [stack("alloy:block", 2, data)])
        source = recipe(stack("alloy:block", 1, data), [stack("awakening:essence")], 2)
        scan = fixture([main, source])
        self.assertEqual(["alloy", "awakening", "modern_industrialization", "pack"],
                         resolve_goal(scan, [], "pack:star")["focus_mods"])
        with self.assertRaisesRegex(ValueError, "unselected"):
            resolve_goal(scan, ["pack", "alloy"], "pack:star")

    def test_optional_unpacking_script_does_not_erase_verified_vanilla_crafting(self):
        main = recipe(stack("pack:star"), [stack("minecraft:blaze_powder", 4)])
        unpacking = recipe(stack("minecraft:blaze_powder"), [stack("missing:powder_block")], 2)
        scan = fixture([main, unpacking])
        scan["unobtainable"] = {"missing:powder_block"}
        scan["vanilla_craftable"] = {"minecraft:blaze_powder"}
        result = apply_campaign(book_for([]), scan, [], "pack:star")
        self.assertNotIn("missing", result["campaign"]["focus_mods"])
        self.assertTrue(any(q.get("_pack_identity") == "minecraft:blaze_powder" for q in quests(result)))
        _assert_dag(result["chapters"])

    def test_native_task_dependency_validates_without_rewriting_the_saved_reference(self):
        book = book_for(["magic:core", "pack:star"])
        first, second = quests(book)
        first["tasks"][0]["id"] = "native-task"
        second["dependencies"] = ["native-task"]
        before = copy.deepcopy(book)
        _assert_dag(book["chapters"])
        self.assertEqual(before, book)
        first["dependencies"] = [second["id"]]
        with self.assertRaisesRegex(ValueError, "circular"):
            _assert_dag(book["chapters"])

    def test_task_id_collision_is_not_mistaken_for_valid_dependency(self):
        book = book_for(["magic:core", "pack:star"])
        first, second = quests(book)
        first["tasks"][0]["id"] = second["id"]
        with self.assertRaisesRegex(ValueError, "duplicate quest/task"):
            _assert_dag(book["chapters"])


if __name__ == "__main__":
    unittest.main()
