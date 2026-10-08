"""Forge's own model parents do not live in a pack's mods directory."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A


class BuiltinModelTests(unittest.TestCase):
    def validate(self, model, textures, extra=None):
        models = {"demo:item/key": json.dumps(model)}
        models.update(extra or {})
        return A.textures_ok("demo:key", models, {}, set(textures), {})

    def test_forge_layered_item_parent_preserves_real_texture_validation(self):
        model = {"parent": "forge:item/default", "loader": "forge:item_layers",
                 "textures": {"layer0": "demo:item/key", "layer1": "demo:item/key_glow"}}
        self.assertTrue(self.validate(model, ["demo:item/key", "demo:item/key_glow"]))
        self.assertFalse(self.validate(model, ["demo:item/key"]))

    def test_forge_block_default_parent_preserves_derived_texture_validation(self):
        item = {"parent": "demo:block/key"}
        block = json.dumps({"parent": "forge:block/default", "textures": {"all": "demo:block/key"}})
        self.assertTrue(self.validate(item, ["demo:block/key"], {"demo:block/key": block}))
        self.assertFalse(self.validate(item, [], {"demo:block/key": block}))

    def test_unknown_forge_parent_still_rejected(self):
        self.assertFalse(self.validate({"parent": "forge:item/not_a_builtin"}, []))

    def test_neoforge_121_defaults_keep_texture_validation(self):
        for parent in ("neoforge:item/default", "neoforge:block/default"):
            model = {"parent": parent, "textures": {"layer0": "demo:item/key"}}
            self.assertTrue(self.validate(model, ["demo:item/key"]))
            self.assertFalse(self.validate(model, []))
        self.assertFalse(self.validate({"parent": "neoforge:item/not_a_builtin"}, []))

    def test_pack_override_of_builtin_is_still_inspected(self):
        override = json.dumps({"textures": {"layer1": "demo:item/missing"}})
        self.assertFalse(self.validate({"parent": "forge:item/default"}, [],
                                       {"forge:item/default": override}))

    def test_builtin_parent_does_not_hide_missing_override_model(self):
        self.assertFalse(self.validate({"parent": "forge:item/default",
                                       "textures": {"layer0": "demo:item/key"},
                                       "overrides": [{"predicate": {"lit": 1}, "model": "demo:item/missing"}]},
                                      ["demo:item/key"]))

    def test_unused_inherited_particle_does_not_hide_valid_faces(self):
        parent = {"parent": "minecraft:block/block",
                  "textures": {"0": "demo:block/old", "particle": "demo:block/old"},
                  "elements": [{"faces": {"north": {"texture": "#0"}, "south": {"texture": "#0"}}}]}
        child = {"parent": "demo:block/base", "textures": {"0": "demo:block/key"}}
        self.assertTrue(self.validate(child, ["demo:block/key"], {"demo:block/base": json.dumps(parent)}))
        self.assertFalse(self.validate(child, [], {"demo:block/base": json.dumps(parent)}))

    def test_visible_particle_alias_is_never_ignored(self):
        for texture in ("#particle", "#surface"):
            model = {"textures": {"0": "demo:item/key", "surface": "#particle", "particle": "demo:item/missing"},
                     "elements": [{"faces": {"north": {"texture": texture}}}]}
            self.assertFalse(self.validate(model, ["demo:item/key"]))

    def test_child_elements_replace_parent_elements_for_particle_usage(self):
        parent = {"textures": {"0": "demo:item/key", "particle": "demo:item/missing"},
                  "elements": [{"faces": {"north": {"texture": "#particle"}}}]}
        child = {"parent": "demo:block/base", "elements": [{"faces": {"north": {"texture": "#0"}}}]}
        self.assertTrue(self.validate(child, ["demo:item/key"], {"demo:block/base": json.dumps(parent)}))
        child['elements'] = []
        self.assertFalse(self.validate(child, ["demo:item/key"], {"demo:block/base": json.dumps(parent)}))

    def test_generated_layer_can_prove_particle_unused_but_alias_cannot(self):
        model = {"parent": "minecraft:item/generated",
                 "textures": {"layer0": "demo:item/key", "particle": "demo:item/missing"}}
        self.assertTrue(self.validate(model, ["demo:item/key"]))
        model['textures']['layer0'] = '#particle'
        self.assertFalse(self.validate(model, ["demo:item/key"]))

    def test_unknown_renderer_or_unproven_faces_keep_particle_guard(self):
        model = {"parent": "minecraft:block/block",
                 "textures": {"all": "demo:item/key", "particle": "demo:item/missing"}}
        self.assertFalse(self.validate(model, ["demo:item/key"]))
        model['elements'] = [{"faces": {"north": {"texture": "#all"}}}]
        model['loader'] = 'demo:unknown_renderer'
        self.assertFalse(self.validate(model, ["demo:item/key"]))

    def test_alias_chain_and_other_missing_slots_remain_validated(self):
        model = {"textures": {"0": "#surface", "surface": "demo:item/key", "particle": "demo:item/missing"},
                 "elements": [{"faces": {"north": {"texture": "#0"}}}]}
        self.assertTrue(self.validate(model, ["demo:item/key"]))
        model['textures']['unrelated'] = 'demo:item/also_missing'
        self.assertFalse(self.validate(model, ["demo:item/key"]))

    def test_state_overrides_can_parent_base_without_inheriting_its_override_list(self):
        base = {'parent': 'minecraft:item/generated', 'textures': {'layer0': 'demo:item/key'},
                'overrides': [{'predicate': {'mode': 1}, 'model': 'demo:item/active'},
                              {'predicate': {'mode': 2}, 'model': 'demo:item/charged'}]}
        models = {'demo:item/active': json.dumps({'parent': 'demo:item/key', 'textures': {'layer1': 'demo:item/glow'}}),
                  'demo:item/charged': json.dumps({'parent': 'demo:item/key', 'textures': {'layer1': 'demo:item/charge'}})}
        self.assertTrue(self.validate(base, ['demo:item/key', 'demo:item/glow', 'demo:item/charge'], models))
        self.assertFalse(self.validate(base, ['demo:item/key', 'demo:item/glow'], models))

    def test_parent_override_does_not_belong_to_child_inventory_model(self):
        base = {'parent': 'minecraft:item/generated', 'textures': {'layer0': 'demo:item/key'},
                'overrides': [{'predicate': {'mode': 1}, 'model': 'demo:item/missing'}]}
        self.assertTrue(self.validate({'parent': 'demo:item/base'}, ['demo:item/key'],
                                      {'demo:item/base': json.dumps(base)}))


if __name__ == "__main__":
    unittest.main()
