"""Concrete pack script parsing, without a JavaScript runtime or pack writes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quest_packdata import load_pack_recipes, parse_recipe_script


class PackRecipeTests(unittest.TestCase):
    def parse(self, chain):
        return parse_recipe_script("ServerEvents.recipes(event => {\n" + chain + "\n})", "fixture.js")

    def test_literal_atm_star_keeps_thirteen_inputs_and_component_identity(self):
        special = 'allthemodium:unobtainium_vibranium_alloy_block[enchantments={levels:{"minecraft:unbreaking":1}},custom_name=\'{"text":"Awakened Alloy"}\']'
        inputs = ["28x allthemodium:unobtainium_allthemodium_alloy_block",
                  "15x allthecompressed:nether_star_block_3x", "2x " + special,
                  "allthetweaks:oblivion_shard", "mysticalagradditions:creative_essence",
                  "allthetweaks:nexium_emitter", "allthetweaks:withers_compass",
                  "allthetweaks:improbable_probability_device", "allthetweaks:dragon_soul",
                  "allthetweaks:philosophers_fuel", "allthetweaks:pulsating_black_hole",
                  "allthetweaks:dimensional_seed", "allthetweaks:patrick_star"]
        chain = "event.recipes.modern_industrialization.star_altar(2048, 200)\n"
        chain += "\n".join(".itemIn(`" + item + "`)" for item in inputs)
        chain += ".itemOut('allthetweaks:atm_star')"
        result = self.parse(chain)
        self.assertEqual([], result["skipped"])
        recipe, = result["recipes"]
        self.assertEqual("allthetweaks:atm_star", recipe["output"]["identity"])
        self.assertEqual(13, len(recipe["ingredients"]))
        self.assertEqual(28, recipe["ingredients"][0]["count"])
        self.assertEqual(15, recipe["ingredients"][1]["count"])
        self.assertEqual(2, recipe["counts"][special])
        self.assertEqual(special, recipe["ingredients"][2]["identity"])
        self.assertTrue(recipe["ingredients"][2]["components"].startswith("[enchantments="))
        json.dumps(result)  # The API is directly cache/report serializable.

    def test_comments_and_quoted_code_never_create_recipes(self):
        fake = "event.recipes.modern_industrialization.star_altar(1, 2).itemIn('x:a').itemOut('x:b')"
        text = "// " + fake + "\n/* " + fake + " */\nconst fake = `" + fake + "`;\n"
        self.assertEqual([], parse_recipe_script(text)["recipes"])
        result = self.parse("event.recipes.modern_industrialization.star_altar(1,2) /* .itemIn('bad:fake') */\n"
                            ".itemIn('x:a').itemOut(\"x:b\")")
        self.assertEqual(["x:a"], [x["id"] for x in result["recipes"][0]["ingredients"]])

    def test_dynamic_route_is_skipped_whole_without_partial_cheaper_recipe(self):
        for value in ("`${count}x x:a`", "item.prism", "'x:' + material", "Item.of('x:a')"):
            with self.subTest(value=value):
                result = self.parse("event.recipes.modern_industrialization.star_altar(1,2)"
                                    ".itemIn('x:expensive').itemIn(" + value + ").itemOut('x:star')")
                self.assertEqual([], result["recipes"])
                self.assertEqual(1, len(result["skipped"]))

    def test_component_output_is_distinct_from_plain_item_and_quotes_survive(self):
        result = self.parse("event.recipes.modern_industrialization.star_altar(1,2)"
                            ".itemIn('x:a').itemOut(`x:star[custom_name='{\"text\":\"A // star\"}']`)\n"
                            "event.recipes.modern_industrialization.star_altar(1,2)"
                            ".itemIn('x:b').itemOut('x:star')")
        first, second = result["recipes"]
        self.assertEqual(first["output"]["id"], second["output"]["id"])
        self.assertNotEqual(first["output"]["identity"], second["output"]["identity"])
        self.assertIn('"A // star"', first["output"]["components"])

    def test_conditional_and_loop_recipes_are_not_assumed_to_run(self):
        chain = "event.recipes.modern_industrialization.star_altar(1,2).itemIn('x:a').itemOut('x:b')"
        for text in ("if (unknown) {" + chain + "}", "for (let x of things) {" + chain + "}",
                     "if (unknown) " + chain + ";"):
            with self.subTest(text=text):
                result = self.parse(text)
                self.assertEqual([], result["recipes"])
                self.assertEqual(1, len(result["skipped"]))

    def test_unsupported_methods_cannot_silently_drop_requirements(self):
        result = self.parse("event.recipes.modern_industrialization.star_altar(1,2)"
                            ".itemIn('x:a').itemOut('x:b').notConsumable('x:tool')")
        self.assertEqual([], result["recipes"])
        self.assertIn("Unsupported", result["skipped"][0]["reason"])

    def test_fluids_tags_and_explicit_recipe_id_are_preserved(self):
        result = self.parse("event.recipes.modern_industrialization.mixer(32,100)"
                            ".itemIn('2x #c:ingots/gold').itemIn('3x #c:ingots/gold')"
                            ".fluidIn('1000x minecraft:water').itemOut('2x x:mix').id('kubejs:mix')")
        recipe, = result["recipes"]
        self.assertEqual(5, recipe["counts"]["#c:ingots/gold"])
        self.assertEqual(1000, recipe["fluids"][0]["count"])
        self.assertEqual(2, recipe["output"]["count"])
        self.assertEqual("kubejs:mix", recipe["id"])

    def test_load_is_recursive_deterministic_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            instance = Path(temp)
            script = instance / "kubejs/server_scripts/nested/star.js"
            script.parent.mkdir(parents=True)
            script.write_text("ServerEvents.recipes(event => { event.recipes.modern_industrialization.star_altar(1,2)"
                              ".itemIn('x:a').itemOut('x:b') })", encoding="utf-8")
            before = script.read_bytes()
            result = load_pack_recipes(instance)
            self.assertEqual(["kubejs/server_scripts/nested/star.js"], result["sourcefiles"])
            self.assertEqual(result, load_pack_recipes(instance))
            self.assertEqual(before, script.read_bytes())
            self.assertEqual([script], list(instance.rglob("*.js")))
            self.assertTrue(result["limitations"])

    def test_shaped_counts_pattern_symbols_and_preserves_explicit_id(self):
        result = self.parse("event.shaped('magic:creative_essence', ['CAC', 'ABA', 'CAC'], {"
                            "A:'magic:essence_block', C:'magic:gem_block', B:'magic:crystal'"
                            "}).id('pack:creative_essence')")
        recipe, = result["recipes"]
        self.assertEqual("minecraft:crafting_shaped", recipe["kind"])
        self.assertEqual({"magic:gem_block": 4, "magic:essence_block": 4, "magic:crystal": 1}, recipe["counts"])
        self.assertEqual("pack:creative_essence", recipe["id"])

    def test_literal_alternatives_become_separate_valid_routes_not_an_and_gate(self):
        result = self.parse("event.shaped('pack:device', ['ABA'], {"
                            "A:Ingredient.of(['tech:machine','magic:artifact']),"
                            "B:[Item.of('pack:core')]})")
        self.assertEqual([], result["skipped"])
        self.assertEqual(2, len(result["recipes"]))
        self.assertEqual({"tech:machine": 2, "pack:core": 1}, result["recipes"][0]["counts"])
        self.assertEqual({"magic:artifact": 2, "pack:core": 1}, result["recipes"][1]["counts"])
        self.assertNotEqual(result["recipes"][0]["key"], result["recipes"][1]["key"])

    def test_component_or_expression_preserves_each_exact_choice(self):
        result = self.parse("event.shaped('pack:soul', ['A'], {"
                            "A:Ingredient.of('tool:catcher[data=\"magic:dragon\"]').or('magic:dragon_egg')})")
        self.assertEqual(2, len(result["recipes"]))
        self.assertEqual('[data="magic:dragon"]', result["recipes"][0]["ingredients"][0]["components"])
        self.assertEqual("magic:dragon_egg", result["recipes"][1]["ingredients"][0]["identity"])

    def test_mod_loaded_ternary_is_static_and_absent_placeholder_disables_route(self):
        text = "event.shaped('pack:soul', ['A'], {A:Platform.isLoaded('magic') ? 'magic:chain' : " \
               "'ftbquests:missing_item[ftbquests:missing_item=\"magic:chain\"]'})"
        present = parse_recipe_script(text, loaded_mods={"magic"})
        self.assertEqual({"magic:chain": 1}, present["recipes"][0]["counts"])
        for mods in (set(), None):
            result = parse_recipe_script(text, loaded_mods=mods)
            self.assertEqual([], result["recipes"])
            self.assertEqual(1, len(result["skipped"]))

    def test_shapeless_output_and_ingredient_quantities_are_exact(self):
        result = self.parse("event.shapeless(Item.of('pack:bundle', 3), ['3x minecraft:ender_pearl', 'minecraft:bone_meal'])")
        recipe, = result["recipes"]
        self.assertEqual(3, recipe["output"]["count"])
        self.assertEqual({"minecraft:ender_pearl": 3, "minecraft:bone_meal": 1}, recipe["counts"])

    def test_bad_shapes_and_dynamic_or_conditional_crafting_are_skipped(self):
        for text in ("event.shaped('pack:x', ['AB'], {A:'pack:a'})",
                     "event.shaped('pack:x', ['A'], {A:'2x pack:a'})",
                     "event.shapeless('pack:x', ['10x pack:a'])",
                     "event.shaped('pack:x', ['A'], {A:chooseIngredient()})",
                     "if (false) { event.shaped('pack:x', ['A'], {A:'pack:a'}) }",
                     "event.shapeless('pack:x', ['pack:a']).replaceIngredient('pack:a','pack:b')"):
            with self.subTest(text=text):
                result = self.parse(text)
                self.assertEqual([], result["recipes"])
                self.assertEqual(1, len(result["skipped"]))


if __name__ == "__main__":
    unittest.main()
