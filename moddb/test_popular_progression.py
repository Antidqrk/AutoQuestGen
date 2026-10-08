"""Playable authored branches, travel gates and honest coverage reporting."""
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A
from moddb.test_offline_story import scan_for, rows_for


class PopularProgressionTests(unittest.TestCase):
    def test_collection_cannot_reverse_recipe_or_erase_other_prerequisites(self):
        # MI's authored collection groups circuit boards under circuits even
        # though a board is an ingredient of its circuit. Geometry is no gate.
        ids = ["board", "wire", "circuit", "machine"]
        deps = {"board": [], "wire": [], "circuit": ["board", "wire"],
                "machine": ["circuit", "wire"]}
        original = {key: list(value) for key, value in deps.items()}
        self.assertEqual(A._valid_collection_hubs({0: 2, 3: 2, 1: 2}, ids, deps), {3: 2})
        self.assertEqual(deps, original)
        self.assertEqual(A._valid_collection_hubs({0: 2}, ids, {key: [] for key in ids}), {})

    def test_known_1211_pack_is_not_blocked_by_bundled_future_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            mods = Path(directory)
            with zipfile.ZipFile(mods / "fixture.jar", "w") as jar:
                jar.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="demo"\nversion="1"')
                jar.writestr("assets/demo/models/item/tool.json", '{}')
                jar.writestr("assets/demo/items/tool.json", '{"model":{"type":"minecraft:model","model":"demo:item/tool"}}')
            with patch.object(A, "_instance_mc_version", return_value="1.21.1"):
                self.assertEqual(A._coverage_note(mods, 1), "")
            with patch.object(A, "_instance_mc_version", return_value="1.21.4"):
                self.assertIn("disabled", A._coverage_note(mods, 1))
            with patch.object(A, "_instance_mc_version", return_value=""):
                self.assertIn("disabled", A._coverage_note(mods, 1))

    def test_reviewed_world_acquisition_still_requires_real_valid_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            mods = Path(directory) / "mods"
            mods.mkdir()
            with zipfile.ZipFile(mods / "fixture.jar", "w") as jar:
                jar.writestr("fabric.mod.json", json.dumps({"id": "demo", "name": "Demo", "version": "1"}))
                jar.writestr("assets/demo/lang/en_us.json", json.dumps({
                    "item.demo.codex": "Codex", "item.demo.technical": "Technical", "item.demo.broken": "Broken"}))
                for item in ("codex", "technical", "broken"):
                    jar.writestr("assets/demo/models/item/" + item + ".json", json.dumps({
                        "parent": "minecraft:item/generated", "textures": {"layer0":
                            "demo:item/absent" if item == "broken" else "minecraft:item/book"}}))
            with patch.object(A, "_scan_cache_key", return_value=""), \
                 patch.object(A, "_instance_mc_version", return_value="1.20.1"), \
                 patch.object(A, "_GUIDE_ACQUISITION", {"demo:codex": "Reviewed puzzle reward",
                     "demo:broken": "Reviewed reward", "demo:phantom": "Reviewed reward"}):
                scan = A.scan_mods(mods, 100, lambda *_: None)
            self.assertIn("demo:codex", scan["items"]["demo"])
            self.assertNotIn("demo:codex", scan["unobtainable"])
            self.assertIn("demo:technical", scan["unobtainable"])
            self.assertNotIn("demo:broken", scan["items"]["demo"])
            self.assertNotIn("demo:phantom", scan["items"]["demo"])

    def test_shared_chapter_repairs_interleaved_addon_prerequisite(self):
        scan = scan_for(["base:ore", "base:machine", "addon:upgrade"])
        quests = [{"id": "upgrade", "tasks": [{"item": "addon:upgrade"}]},
                  {"id": "ore", "tasks": [{"item": "base:ore"}]},
                  {"id": "machine", "tasks": [{"item": "base:machine"}], "dependencies": ["ore"]}]
        with patch.object(A, "_GUIDED_REQUIREMENTS", {
                "base:ore": [], "base:machine": ["base:ore"], "addon:upgrade": ["base:machine"]}):
            A._connect_authored_chapters([{"quests": quests}], scan, "linear")
        self.assertEqual(quests[0]["dependencies"], ["machine"])

    def test_structure_version_alternative_uses_the_installed_name(self):
        scan = scan_for(["demo:key"])
        scan["structures"] = {"demo": {"demo:new"}}
        with patch.object(A, "_curated_chain", return_value=[
                ("structure:demo:old|structure:demo:new", "Explore", "Find the installed fortress.")]):
            rows = A._mod_quest_rows(scan["mods"][0], scan, 3, random.Random(1), 0)
        self.assertTrue(any(task.get("structure") == "demo:new" for _title, task, _desc in rows))

    def test_missing_companion_mod_disables_only_its_researched_endgame(self):
        with patch.object(A, "_GUIDE_REQUIRED_MODS", {
                "mekanism:pellet_polonium": ["mekanismgenerators"],
                "mekanism:ingot_steel": [], "other:goal": ["otheraddon"]}):
            self.assertEqual(A._disabled_guide_objectives({"mekanism"}), {"mekanism:pellet_polonium"})
            self.assertEqual(A._disabled_guide_objectives({"mekanism", "mekanismgenerators"}), set())

    def test_bundled_authored_graphs_have_no_circular_prerequisites(self):
        from quest_campaign import _assert_dag
        refs = {candidate.strip() for mid in A._AUTHORED_GRAPH_MODS
                for ref, _title, _desc in A._curated_chain(mid) for candidate in ref.split("|")}
        quests = []
        for ref in sorted(refs):
            parents = {candidate.strip() for choice in A._GUIDED_REQUIREMENTS.get(ref, ())
                       for candidate in choice.split("|") if candidate.strip() in refs and candidate.strip() != ref}
            quests.append({"id": ref, "dependencies": sorted(parents)})
        _assert_dag([{"quests": quests}])

    def test_addon_keeps_base_mod_prerequisites_across_chapters(self):
        scan = scan_for(["base:machine", "addon:upgrade"])
        chapters = [{"id": "base", "quests": [{"id": "machine", "tasks": [{"item": "base:machine"}]}]},
                    {"id": "addon", "quests": [{"id": "upgrade", "tasks": [{"item": "addon:upgrade"}]}]}]
        with patch.object(A, "_GUIDED_REQUIREMENTS", {"addon:upgrade": ["base:machine"]}):
            A._connect_authored_chapters(chapters, scan, "linear")
        self.assertEqual(chapters[1]["quests"][0]["dependencies"], ["machine"])
        # A reference graph that already requires the addon cannot become circular.
        chapters[0]["quests"][0]["dependencies"] = ["upgrade"]
        chapters[1]["quests"][0].pop("dependencies")
        with patch.object(A, "_GUIDED_REQUIREMENTS", {"addon:upgrade": ["base:machine"]}):
            A._connect_authored_chapters(chapters, scan, "linear")
        self.assertFalse(chapters[1]["quests"][0].get("dependencies"))

    def test_explicit_roots_do_not_lock_independent_branches(self):
        items = ["demo:ore", "demo:flower", "demo:metal", "demo:wand"]
        scan = scan_for(items)
        chain = [(item, item, "Original guidance") for item in items]
        requirements = {items[0]: [], items[1]: [], items[2]: [items[0]], items[3]: [items[1]]}
        with patch.object(A, "_curated_chain", return_value=chain), patch.object(A, "_GUIDED_REQUIREMENTS", requirements):
            deps, _ = A._progression_dependencies(rows_for(items), items, scan)
        self.assertEqual(deps[items[1]], [])
        self.assertEqual(deps[items[2]], [items[0]])
        self.assertEqual(deps[items[3]], [items[1]])

    def test_boss_and_structure_gates_survive_shuffled_rows(self):
        scan = scan_for(["demo:key", "demo:relic"])
        scan["dimensions"] = {"demo": {"demo:realm"}}
        scan["structures"] = {"demo": {"demo:citadel"}}
        scan["entities"] = {"demo": {"demo:guardian"}}
        tasks = [{"type": "item", "item": "demo:relic"},
                 {"type": "kill", "entity": "demo:guardian"},
                 {"type": "structure", "structure": "demo:citadel"},
                 {"type": "dimension", "dimension": "demo:realm"},
                 {"type": "item", "item": "demo:key"}]
        requirements = {"demo:key": [], "dim:demo:realm": ["demo:key"],
                        "structure:demo:citadel": ["dim:demo:realm"],
                        "kill:demo:guardian": ["structure:demo:citadel"],
                        "demo:relic": ["kill:demo:guardian"]}
        with patch.object(A, "_GUIDED_REQUIREMENTS", requirements):
            ordered = A._enforce_recipe_order([(str(i), t, "") for i, t in enumerate(tasks)], scan)
            keys = [A._objective_ref(r[1]) for r in ordered]
            deps, _ = A._progression_dependencies(ordered, keys, scan)
        self.assertEqual(keys, list(requirements))
        self.assertEqual(deps, requirements)

    def test_recipe_mode_omits_teaching_gates_and_open_omits_all_gates(self):
        items = ["demo:manual", "demo:ore", "demo:machine"]
        scan = scan_for(items, {items[2]: [("minecraft:crafting_shaped", [items[1]])]})
        with patch.object(A, "_GUIDED_REQUIREMENTS", {items[0]: [], items[1]: [], items[2]: [items[0]]}):
            guided, _ = A._progression_dependencies(rows_for(items), items, scan, "linear")
            recipes, _ = A._progression_dependencies(rows_for(items), items, scan, "loose")
            opened, _ = A._progression_dependencies(rows_for(items), items, scan, "open")
        self.assertEqual(set(guided[items[2]]), set(items[:2]))
        self.assertEqual(recipes[items[2]], [items[1]])
        self.assertTrue(all(not deps for deps in opened.values()))

    def test_small_complete_guide_beats_long_unreviewed_stub(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "chains_research").mkdir()
            (base / "chains_research/demo.json").write_text(json.dumps({"mod": "demo", "chain": [
                {"id": "demo:starter", "title": "Start", "desc": "Build a starter.", "requires": []},
                {"id": "demo:goal", "title": "Finish", "desc": "Use the starter.", "requires": ["demo:starter"]}]}))
            with patch.object(A, "moddb_path", return_value=base), patch.object(A, "_GUIDED_REQUIREMENTS", {}), \
                 patch.object(A, "_AUTHORED_GRAPH_MODS", set()), patch.object(A, "_GUIDE_METADATA", {}), \
                 patch.object(A, "_GUIDE_CHECKS", {}):
                researched = A._load_researched_chains()
                self.assertIn("demo:starter", A._GUIDED_REQUIREMENTS)
                with patch.object(A, "_RESEARCHED", researched), patch.object(A, "_mp_chain", return_value=[("bad", "", "")] * 8):
                    self.assertEqual([row[0] for row in A._curated_chain("demo")], ["demo:starter", "demo:goal"])

    def test_manual_multiblock_milestone_is_a_checkmark_not_phantom_item(self):
        scan = scan_for(["demo:brick", "demo:steel"])
        check = "check:demo:coke_oven"
        chain = [("demo:brick", "Materials", "Prepare bricks."),
                 (check, "Build the Oven", "Assemble the oven, run a batch, then confirm completion."),
                 ("demo:steel", "Steel", "Use the oven to make steel.")]
        with patch.object(A, "_curated_chain", return_value=chain), \
             patch.object(A, "_GUIDE_CHECKS", {check: {"mod": "demo", "icon": "demo:brick"}}), \
             patch.object(A, "_GUIDED_REQUIREMENTS", {"demo:brick": [], check: ["demo:brick"], "demo:steel": [check]}):
            rows = A._mod_quest_rows(scan["mods"][0], scan, 3, random.Random(1), 0)
            keys = [A._objective_ref(row[1]) for row in rows]
            deps, _ = A._progression_dependencies(rows, keys, scan)
            self.assertEqual(deps["demo:steel"], [check])
            self.assertEqual(deps[check], ["demo:brick"])
            task = next(row[1] for row in rows if A._objective_ref(row[1]) == check)
            converted = A._task(task, "test-manual", self.fail)
            self.assertEqual(converted["type"], "checkmark")
            self.assertNotIn("item", converted)
            self.assertNotIn("_guide_ref", converted)

    def test_manual_milestone_does_not_exist_without_owning_mod_or_icon(self):
        check = "check:demo:oven"
        with patch.object(A, "_GUIDE_CHECKS", {check: {"mod": "demo", "icon": "demo:brick"}}):
            self.assertNotIn(check, A._available_guide_checks(scan_for(["demo:other"])))
            self.assertNotIn(check, A._available_guide_checks(scan_for(["other:brick"])))
            self.assertIn(check, A._available_guide_checks(scan_for(["demo:brick"])))

    def test_coverage_resolves_version_alternatives_and_does_not_claim_missing_goals(self):
        chain = [("demo:old|demo:new", "Start", ""), ("demo:missing", "Later", "")]
        scan = scan_for(["demo:new", "unknown:thing"])
        with patch.object(A, "_curated_chain", side_effect=lambda mid: chain if mid == "demo" else []), \
             patch.object(A, "_AUTHORED_GRAPH_MODS", {"demo"}):
            rows = {row["mod_id"]: row for row in A.progression_coverage(scan)}
        self.assertEqual(rows["demo"]["available"], 1)
        self.assertEqual(rows["demo"]["total"], 2)
        self.assertEqual(rows["demo"]["missing"], ["demo:missing"])
        self.assertEqual(rows["unknown"]["kind"], "Recipes & discoveries")

    def test_campaign_can_end_on_verified_manual_project(self):
        from quest_campaign import apply_campaign
        scan = scan_for(["demo:brick"])
        doc = {"chapters": [{"id": "engineering", "title": "Engineering", "quests": [
            {"id": "material", "title": "Materials", "tasks": [{"type": "item", "item": "demo:brick"}]},
            {"id": "oven", "title": "Assemble the Oven", "dependencies": ["material"],
             "tasks": [{"type": "checkmark", "_guide_ref": "check:demo:oven", "_guide_mod": "demo"}]},
            {"id": "steelworks", "title": "Run the Steelworks", "dependencies": ["oven"], "icon": "demo:brick",
             "tasks": [{"type": "checkmark", "_guide_ref": "check:demo:steelworks", "_guide_mod": "demo"}]}]}]}
        result = apply_campaign(doc, scan, ["demo"])
        campaign = result["chapters"][-1]
        self.assertTrue(any("steelworks" in q.get("dependencies", []) for q in campaign["quests"]))
        self.assertEqual(result["campaign"]["contributing_mods"], ["demo"])


if __name__ == "__main__":
    unittest.main()
