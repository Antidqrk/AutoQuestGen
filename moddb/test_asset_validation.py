"""Regression tests for unavailable goals and inventory-model validation.

These use small, explicit asset fixtures: agreeing with the scanner's own
output is not independent evidence that an item or texture exists.
Run with: python -m unittest discover -s moddb -p test_asset_validation.py
"""
import copy
import json
from pathlib import Path
import pickle
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import autoquestgen as A


def model(**fields):
    return json.dumps(fields).encode()


def quest(key, item=None, deps=()):
    return {"id": key, "title": key, "dependencies": list(deps),
            "tasks": [{"type": "item", "item": item}] if item else [{"type": "checkmark"}]}


class GoalValidationTests(unittest.TestCase):
    def clean(self, chapters, items=("demo:reactor", "demo:brass_ingot")):
        doc = {"chapters": copy.deepcopy(chapters)}
        scan = {"items": {"demo": set(items)}, "vanilla": {"minecraft:book"}}
        return A.clean_doc(doc, scan, lambda _: None)["chapters"]

    def test_missing_goal_is_never_replaced_with_random_same_mod_item(self):
        result = self.clean([{"quests": [quest("Build reactor", "demo:made_up_reactor")]}])
        self.assertEqual([], result[0]["quests"])

    def test_only_unambiguous_namespace_correction_keeps_goal(self):
        result = self.clean([{"quests": [quest("Make brass", "minecraft:brass_ingot")]}])
        self.assertEqual("demo:brass_ingot", result[0]["quests"][0]["tasks"][0]["item"])
        result = self.clean([{"quests": [quest("Make brass", "missing:brass_ingot")]}],
                            ("demo:brass_ingot", "other:brass_ingot"))
        self.assertEqual([], result[0]["quests"])

    def test_template_removal_preserves_all_cross_chapter_prerequisites(self):
        result = self.clean([
            {"_template": True, "quests": [quest("a"), quest("b"),
                quest("missing", "demo:missing", ("a", "b"))]},
            {"quests": [quest("finish", deps=("missing",))]}])
        self.assertEqual(["a", "b"], [q["id"] for q in result[0]["quests"]])
        self.assertEqual(["a", "b"], result[1]["quests"][0]["dependencies"])

    def test_partial_invalid_goal_does_not_keep_misleading_description(self):
        q = quest("Build both machines", "demo:reactor")
        q["tasks"].append({"type": "item", "item": "demo:missing"})
        result = self.clean([{"quests": [q]}])
        self.assertEqual([], result[0]["quests"])

    def test_empty_scan_does_not_whitelist_invented_ids(self):
        result = self.clean([{"quests": [quest("bad", "demo:invented")]}], ())
        self.assertEqual([], result[0]["quests"])

    def test_missing_item_field_does_not_make_empty_item_task(self):
        q = quest("bad")
        q["tasks"] = [{"type": "item"}]
        self.assertEqual([], self.clean([{"quests": [q]}])[0]["quests"])

    def test_table_rewards_and_target_alias_are_validated(self):
        doc = {"chapters": [], "reward_tables": [
            {"id": "good", "rewards": [{"target": "demo:reactor"}, {"item": "demo:missing"}]},
            {"id": "empty", "rewards": [{"item": "demo:missing"}]}]}
        scan = {"items": {"demo": {"demo:reactor"}}}
        A.clean_doc(doc, scan, lambda _: None)
        tables, ids = A.build_reward_tables(doc, lambda _: None)
        self.assertEqual(["good"], list(ids))
        self.assertEqual(1, len(tables))
        self.assertEqual("demo:reactor", tables[0][1]["rewards"][0]["item"])

    def test_campaign_checkmarks_keep_distinct_verified_item_icons_after_export(self):
        doc = {"chapters": [{"id": "campaign", "quests": [
            dict(quest("magic"), icon="botania:mana_pool"),
            dict(quest("machines"), icon="create:mechanical_crafter")]}]}
        scan = {"items": {"botania": {"botania:mana_pool"},
                          "create": {"create:mechanical_crafter"}},
                "vanilla": {"minecraft:book"}}
        A.clean_doc(doc, scan, lambda _: None)
        chapters, groups, _extra = A.build_chapters(doc, lambda _: None, {"_scan": scan})
        expected = ["botania:mana_pool", "create:mechanical_crafter"]
        self.assertEqual(expected, [q["icon"] for q in chapters[0][1]["quests"]])
        self.assertTrue(all(q["tasks"][0]["type"] == "checkmark" for q in chapters[0][1]["quests"]))
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            A.write_snbt(folder, chapters, groups, False, lambda _: None)
            saved = A.snbt_loads(next((folder / "chapters").glob("*.snbt")).read_text(encoding="utf-8"))
        self.assertEqual(expected, [q["icon"] for q in saved["quests"]])

    def test_invalid_quest_icons_are_dropped_without_namespace_or_item_substitution(self):
        scan = {"items": {"demo": {"demo:reactor"}}, "vanilla": {"minecraft:book"}}
        for icon in ("missing:reactor", "demo:not_real", "demo:item/reactor",
                     "demo:textures/item/reactor.png", "{id:'demo:reactor'}",
                     {"id": "demo:reactor"}, 1):
            with self.subTest(icon=icon):
                doc = {"chapters": [{"id": "import", "quests": [dict(quest("start"), icon=icon)]}]}
                raw = copy.deepcopy(doc)
                A.clean_doc(doc, scan, lambda _: None)
                self.assertNotIn("icon", doc["chapters"][0]["quests"][0])
                # Direct conversion must be safe even if an importer skips clean_doc.
                chapters, _, _ = A.build_chapters(raw, lambda _: None, {"_scan": scan})
                self.assertNotIn("icon", chapters[0][1]["quests"][0])

    def test_modded_icon_requires_current_scan_and_vanilla_icon_remains_usable(self):
        for icon, expected in (("demo:reactor", None), ("minecraft:book", "minecraft:book")):
            raw = dict(quest("start"), icon=icon)
            converted = A._quest(raw, "chapter", {"start": "ABC"}, lambda _: None)
            self.assertEqual(expected, converted.get("icon"))


class ModelValidationTests(unittest.TestCase):
    def valid(self, models, states=None, textures=("demo:item/good",)):
        return A.textures_ok("demo:tool", models, states or {}, set(textures), {})

    def test_child_alias_overrides_parent_placeholder(self):
        self.assertTrue(self.valid({
            "demo:item/tool": model(parent="demo:item/base", textures={"layer0": "#finish", "finish": "demo:item/good"}),
            "demo:item/base": model(parent="item/generated", textures={"layer0": "demo:item/missing"})}))

    def test_unresolved_or_cyclic_alias_is_rejected(self):
        for textures in ({"layer0": "#missing"}, {"layer0": "#other", "other": "#layer0"}):
            self.assertFalse(self.valid({"demo:item/tool": model(parent="item/generated", textures=textures)}))

    def test_inventory_model_is_not_vetoed_by_unrelated_placed_model(self):
        self.assertTrue(self.valid({
            "demo:item/tool": model(parent="item/generated", textures={"layer0": "demo:item/good"}),
            "demo:block/broken": model(textures={"all": "demo:block/missing"})},
            {"demo:tool": model(variants={"": {"model": "demo:block/broken"}})}))

    def test_missing_override_model_is_rejected(self):
        self.assertFalse(self.valid({"demo:item/tool": model(
            parent="item/generated", textures={"layer0": "demo:item/good"},
            overrides=[{"predicate": {"pulling": 1}, "model": "demo:item/tool_pulling"}])}))

    def test_missing_parent_and_invalid_model_are_rejected(self):
        for raw in (model(parent="demo:item/missing"), b"not json", b"[]"):
            self.assertFalse(self.valid({"demo:item/tool": raw}))

    def test_late_blockstate_variant_is_checked(self):
        variants = {str(i): {"model": "demo:block/good"} for i in range(8)}
        variants["last"] = {"model": "demo:block/missing"}
        self.assertFalse(self.valid({"demo:block/good": model(textures={"all": "demo:item/good"})},
                                   {"demo:tool": model(variants=variants)}))

    def test_parent_cycle_is_rejected(self):
        self.assertFalse(self.valid({"demo:item/tool": model(parent="demo:item/base"),
                                    "demo:item/base": model(parent="demo:item/tool")}))


class ConditionalRecipeTests(unittest.TestCase):
    @staticmethod
    def shaped(output, ingredient="minecraft:iron_ingot"):
        return {"type": "minecraft:crafting_shaped", "pattern": ["I"],
                "key": {"I": {"item": ingredient}}, "result": {"item": output}}

    def test_nested_wrapper_extracts_actual_recipe_not_conditions(self):
        leaf = self.shaped("fixture:upgrade")
        leaf["key"]["P"] = {"tag": "minecraft:planks"}
        wrapped = {"type": "forge:conditional", "recipes": [{"conditions": [{
            "type": "forge:and", "values": [
                {"type": "forge:item_exists", "item": "fixture:upgrade"},
                {"type": "forge:not", "value": {"type": "forge:mod_loaded", "modid": "absent"}}]}],
            "recipe": {"type": "forge:conditional", "recipes": [{"conditions": [], "recipe": leaf}]}}]}
        mods, items = {"fixture"}, {"fixture:upgrade"}
        self.assertEqual({"fixture:upgrade"}, A._recipe_result_ids(wrapped, mods, items))
        self.assertEqual({"minecraft:iron_ingot", "#minecraft:planks"},
                         A._recipe_ingredient_ids(wrapped, mods, items))
        self.assertEqual("minecraft:crafting_shaped", A._recipe_variants(wrapped, mods, items)[0]["type"])

    def test_only_first_passing_route_is_selected(self):
        wrapped = {"type": "forge:conditional", "recipes": [
            {"conditions": [{"type": "forge:mod_loaded", "modid": "absent"}],
             "recipe": self.shaped("fixture:upgrade", "absent:crystal")},
            {"conditions": [{"type": "forge:or", "values": [
                {"type": "forge:false"}, {"type": "forge:mod_loaded", "modid": "fixture"}]}],
             "recipe": self.shaped("fixture:upgrade", "minecraft:gold_ingot")},
            {"conditions": [], "recipe": self.shaped("fixture:upgrade", "minecraft:diamond")}]}
        self.assertEqual({"minecraft:gold_ingot"}, A._recipe_ingredient_ids(wrapped, {"fixture"}, set()))

    def test_unknown_guard_does_not_invent_available_recipe_or_fallback(self):
        wrapped = {"type": "forge:conditional", "recipes": [
            {"conditions": [{"type": "fixture:config_enabled", "key": "hard_mode"}],
             "recipe": self.shaped("fixture:upgrade", "fixture:rare_part")},
            {"conditions": [], "recipe": self.shaped("fixture:upgrade")}]}
        self.assertEqual(set(), A._recipe_result_ids(wrapped, {"fixture"}, {"fixture:upgrade"}))
        # Unknown inverted is still unknown; treating it as false enables the recipe.
        self.assertIsNone(A._recipe_condition({"type": "forge:not", "value": {
            "type": "fixture:unknown"}}, {"fixture"}, set()))

    def test_disabled_plain_recipe_cannot_become_craftable(self):
        recipe = self.shaped("fixture:upgrade")
        recipe["conditions"] = [{"type": "forge:mod_loaded", "modid": "absent"}]
        self.assertEqual(set(), A._recipe_result_ids(recipe, {"fixture"}, {"fixture:upgrade"}))

    def test_scanner_rescues_real_wrapper_outputs_but_not_disabled_compat(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with zipfile.ZipFile(folder / "a-fixture.jar", "w") as z:
                z.writestr("META-INF/mods.toml", 'modLoader="javafml"\n[[mods]]\nmodId="aqgconditional"\nversion="1"\n')
                for name in ("upgrade", "blocked", "late_mod"):
                    z.writestr("assets/aqgconditional/models/item/" + name + ".json",
                               model(parent="minecraft:item/generated", textures={"layer0": "aqgconditional:item/good"}))
                z.writestr("assets/aqgconditional/textures/item/good.png", b"fixture")
                recipes = {
                    "upgrade": [{"type": "forge:and", "values": [{"type": "forge:item_exists", "item": "aqgconditional:upgrade"}]}],
                    "blocked": [{"type": "forge:mod_loaded", "modid": "absent"}],
                    "late_mod": [{"type": "forge:mod_loaded", "modid": "latecompanion"}],
                    "phantom": [{"type": "forge:item_exists", "item": "absent:phantom"}],
                }
                for name, conditions in recipes.items():
                    z.writestr("data/aqgconditional/recipes/" + name + ".json", json.dumps({
                        "type": "forge:conditional", "recipes": [{"conditions": conditions,
                        "recipe": self.shaped("aqgconditional:" + name)}]}))
            # A later jar and a second metadata entry must be known before the first recipe is read.
            with zipfile.ZipFile(folder / "z-companion.jar", "w") as z:
                z.writestr("META-INF/mods.toml", 'modLoader="javafml"\n[[mods]]\nmodId="maincompanion"\nversion="1"\n[[mods]]\nmodId="latecompanion"\nversion="1"\n')
            with patch.object(A, "_scan_cache_key", return_value=""), \
                    patch.object(A, "_vanilla_items_from_client", return_value={"minecraft:iron_ingot"}):
                scan = A.scan_mods(folder, 100, lambda _: None)
            self.assertEqual({"aqgconditional:upgrade", "aqgconditional:late_mod"}, scan["craftable"]["aqgconditional"])
            self.assertNotIn("aqgconditional:upgrade", scan["unobtainable"])
            self.assertIn("aqgconditional:blocked", scan["unobtainable"])
            self.assertNotIn("aqgconditional:phantom", scan["items"]["aqgconditional"])
            self.assertEqual([("minecraft:crafting_shaped", frozenset({"minecraft:iron_ingot"}))],
                             scan["craft_recipes"]["aqgconditional:upgrade"])


class ScannerFixtureTests(unittest.TestCase):
    def test_custom_geometry_survives_but_absent_compat_item_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with zipfile.ZipFile(folder / "fixture.jar", "w") as z:
                z.writestr("META-INF/mods.toml", 'modLoader="javafml"\nloaderVersion="[47,)"\nlicense="MIT"\n[[mods]]\nmodId="aqgfixture"\ndisplayName="Fixture"\nversion="1"\n')
                z.writestr("assets/aqgfixture/geo/item/custom_tool.geo.json", "{}")
                z.writestr("assets/aqgfixture/lang/en_us.json", json.dumps({
                    "item.aqgfixture.custom_tool": "Custom Tool", "item.aqgfixture.absent": "Absent"}))
                for name in ("custom_tool", "absent"):
                    z.writestr("data/aqgfixture/recipes/%s.json" % name, json.dumps({
                        "type": "minecraft:crafting_shapeless", "ingredients": [{"item": "minecraft:stick"}],
                        "result": {"item": "aqgfixture:" + name}}))
            with patch.object(A, "_scan_cache_key", return_value=""), \
                    patch.object(A, "_vanilla_items_from_client", return_value={"minecraft:stick"}):
                scan = A.scan_mods(folder, 100, lambda _: None)
            self.assertIn("aqgfixture:custom_tool", scan["items"]["aqgfixture"])
            self.assertNotIn("aqgfixture:absent", scan["items"]["aqgfixture"])

    def test_wrong_client_version_never_supplies_vanilla_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mods = root / "Instances" / "pack" / "mods"
            mods.mkdir(parents=True)
            jar = root / "Install" / "versions" / "1.21.11" / "1.21.11.jar"
            jar.parent.mkdir(parents=True)
            with zipfile.ZipFile(jar, "w") as z:
                z.writestr("assets/minecraft/models/item/diamond.json", "{}")
                z.writestr("assets/minecraft/models/item/copper_axe.json", "{}")
                z.writestr("assets/minecraft/lang/en_us.json", json.dumps({"item.minecraft.copper_axe": "Copper Axe"}))
            with patch.object(A, "_instance_mc_version", return_value="1.20.1"):
                self.assertEqual(set(), A._vanilla_items_from_client(mods))
            with patch.object(A, "_instance_mc_version", return_value=""):
                self.assertEqual(set(), A._vanilla_items_from_client(mods))

    def test_client_and_cache_switches_do_not_leak_recipe_facts(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(A, "_VANILLA_SOURCE", {"minecraft:old": "wrong pack"}), \
                patch.object(A, "_VANILLA_CRAFTABLE", {"minecraft:old"}):
            folder = Path(directory)
            with patch.object(A, "_instance_mc_version", return_value="1.20.1"):
                A._vanilla_items_from_client(folder / "mods")
            self.assertEqual({}, A._VANILLA_SOURCE)
            self.assertEqual(set(), A._VANILLA_CRAFTABLE)
            cached = {"vanilla_sources": {"minecraft:stick": "crafted from planks"},
                      "vanilla_craftable": {"minecraft:stick"}}
            path = folder / "cache.pkl"
            path.write_bytes(pickle.dumps({"key": "fixture", "scan": cached}))
            with patch.object(A, "_scan_cache_key", return_value="fixture"), \
                    patch.object(A, "_scan_cache_path", return_value=path):
                A.scan_mods(folder, 100, lambda _: None)
            self.assertEqual(cached["vanilla_sources"], A._VANILLA_SOURCE)
            self.assertEqual(cached["vanilla_craftable"], A._VANILLA_CRAFTABLE)

    def test_mod_release_number_is_never_a_minecraft_version(self):
        with tempfile.TemporaryDirectory() as directory:
            inst = Path(directory)
            mods = inst / "mods"
            mods.mkdir()
            (mods / "iris-fabric-1.11.6.jar").touch()
            self.assertEqual("", A._instance_mc_version(inst))
            (mods / "iris-fabric-1.11.6+mc26.3.jar").touch()
            self.assertEqual("26.3", A._instance_mc_version(inst))
            (inst / "minecraftinstance.json").write_text(json.dumps({
                "baseModLoader": {"minecraftVersion": "26.3"}}))
            self.assertEqual("26.3", A._instance_mc_version(inst))

    def test_new_item_formats_are_reported_and_cannot_generate_old_books(self):
        for version in ("1.21.4", "1.21.11", "26.3"):
            self.assertIn(version, A._unsupported_version_note(version))
        self.assertEqual("", A._unsupported_version_note("1.20.1"))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "mods"
            folder.mkdir()
            with zipfile.ZipFile(folder / "fixture.jar", "w") as z:
                z.writestr("fabric.mod.json", json.dumps({"id": "modernfixture", "version": "1"}))
                z.writestr("assets/modernfixture/items/new_item.json", json.dumps({
                    "model": {"type": "minecraft:model", "model": "modernfixture:item/new_item"}}))
            with patch.object(A, "_scan_cache_key", return_value=""), \
                    patch.object(A, "_instance_mc_version", return_value="26.3"):
                scan = A.scan_mods(folder, 100, lambda _: None)
            self.assertTrue(scan["unsupported"])
            self.assertIn("26.3", scan["blocked"])
            self.assertNotIn("performance or interface", scan["blocked"])
            self.assertEqual(set(), scan["vanilla"])
            self.assertIn("tags", scan)
            with self.assertRaisesRegex(ValueError, "26.3"):
                A.local_quest_doc(scan, [], {})
            with self.assertRaisesRegex(ValueError, "26.3"):
                A.clean_doc({"chapters": []}, scan, lambda _: None)
            with patch.object(A, "_instance_mc_version", return_value=""):
                self.assertIn("newer item-definition", A._coverage_note(folder, 1))

    def test_client_version_participates_in_scan_cache_key(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch.object(A, "_instance_mc_version", return_value="1.20.1"):
                old = A._scan_cache_key(folder, 100)
            with patch.object(A, "_instance_mc_version", return_value="26.3"):
                new = A._scan_cache_key(folder, 100)
            self.assertNotEqual(old, new)


if __name__ == "__main__":
    unittest.main()
