"""Installed blueprint integration must preserve native FTB data through export.

Portable source books use temporary directories only. These tests never read or
modify an installed modpack, and distinguish copied reference data from fresh
generation. Reward expectations follow FTB 2101.1.36 ItemReward.readData: an
absent/zero outer count falls back to the count in the serialized item stack.
"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A
from moddb.test_offline_story import scan_for


START = "1000000000000001"
BRANCH = "1000000000000002"
GOAL = "1000000000000003"
START_TASK = "2000000000000001"


def source_chapter():
    return {
        "id": "3000000000000001", "filename": "the_star",
        "title": "&6The original Star", "group": "4000000000000001",
        "order_index": 7, "default_quest_shape": "square",
        "default_hide_dependency_lines": False,
        "subtitle": ["An authored, closed endgame map."],
        "icon": {"id": "demo:star", "count": 1},
        "images": [{"id": "5000000000000001", "image": "demo:textures/star.png",
                    "x": -2.25, "y": 3.5, "width": 7.5, "height": 4.0,
                    "rotation": 15.0, "color": -1}],
        "quest_links": [{"id": "5000000000000002", "linked_quest": START,
                         "x": 8.0, "y": -4.0}],
        "quests": [
            {"id": START, "title": "Materials", "x": -8.5, "y": -3.25,
             "size": 1.25, "shape": "hexagon",
             "description": ["Gather the first materials."],
             "tasks": [{"id": START_TASK, "type": "item", "count": 28,
                        "item": {"id": "demo:alloy", "count": 1},
                        "consume_items": False}],
             "rewards": [{"id": "6000000000000001", "type": "xp", "xp": 80}]},
            {"id": BRANCH, "title": "Infused alloy", "x": 8.0, "y": -3.5,
             "size": 2.5, "shape": "diamond", "dependencies": [START],
             "hide_dependency_lines": True, "optional": True,
             "tasks": [{"id": "2000000000000002", "type": "item", "count": 2,
                        "match_components": True,
                        "item": {"id": "demo:alloy", "count": 1,
                                 "components": {"minecraft:custom_name": '{"text":"Infused Alloy"}',
                                                "minecraft:enchantments": {"levels": {"minecraft:mending": 1}}}}}],
             "rewards": [{"id": "6000000000000002", "type": "xp_levels", "xp_levels": 6}]},
            {"id": GOAL, "title": "&6The Star", "x": 0.0, "y": 0.0,
             "size": 5.0, "shape": "pentagon", "icon_scale": 1.25,
             "dependencies": [BRANCH], "description": ["Finish the real Star."],
             "subtitle": "A final project", "can_repeat": False,
             "tasks": [{"id": "2000000000000003", "type": "item",
                        "item": {"id": "demo:star", "count": 1}}],
             "rewards": [{"id": "6000000000000003", "type": "item",
                          "item": {"id": "demo:gift", "count": 7,
                                   "components": {"minecraft:custom_name": '{"text":"A keepsake"}'}}}]},
        ],
    }


def generated_doc():
    return {"title": "Generated book", "_offline_generated": True,
            "campaign": {"exact_goal_ids": ["demo:star"], "finale_quest_ids": ["old_final"]},
            "chapters": [
                {"id": "generated_start", "title": "Beyond the Star", "group": "Technology",
                 "quests": [{"id": "foreign", "title": "Celebrate the completed Star",
                             "tasks": [{"type": "checkmark"}],
                             "dependencies": ["old_final"]}]},
                {"id": "old_campaign", "title": "Generated finale", "_campaign": True,
                 "quests": [{"id": "old_final", "title": "Generated Star",
                             "tasks": [{"type": "item", "item": "demo:star"}],
                             "dependencies": []}]},
            ]}


def reward_count(reward):
    """The effective item amount read by the installed FTB version."""
    item = reward.get("item")
    return reward.get("count") or (item.get("count", 1) if isinstance(item, dict) else 1)


class BlueprintExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.instance = Path(temp.name)
        self.quest_root = self.instance / "config/ftbquests/quests"
        (self.quest_root / "chapters").mkdir(parents=True)
        self.source = source_chapter()
        self.save_source()
        self.scan = scan_for(["demo:star", "demo:alloy", "demo:gift"],
                             {"demo:star": [("crafting", ["demo:alloy"])]})
        self.scan["instance_dir"] = str(self.instance)
        self.opts = dict(mimic=True, layout="reference", reward="standard",
                         aesthetic="lavish", progression="linear", quest_shape="auto",
                         groups=True, style_chapters=True, decor_art=True, _scan=self.scan)

    def save_source(self):
        (self.quest_root / "chapters/the_star.snbt").write_text(
            A.snbt_dumps(self.source), encoding="utf-8")

    def integrate(self, **overrides):
        return A._use_installed_goal_blueprint(generated_doc(), self.scan, self.opts | overrides)

    def export(self, **overrides):
        doc = self.integrate(**overrides)
        A.clean_doc(doc, self.scan, lambda *_: None)
        chapters, groups, extra = A.build_chapters(doc, lambda *_: None, self.opts | overrides)
        return doc, dict(chapters), groups, extra

    def test_native_full_fidelity_and_source_files_unchanged(self):
        before_files = {p: p.read_bytes() for p in self.quest_root.rglob("*") if p.is_file()}
        doc, chapters, groups, extra = self.export(quest_size_style="preserve")
        chapter = chapters["the_star"]
        expected = copy.deepcopy(self.source)
        expected["group"] = next(g["id"] for g in groups if "Main Goal" in g["title"])
        expected["order_index"] = 0
        self.assertEqual(chapter, expected)
        self.assertEqual(A.snbt_loads(A.snbt_dumps(chapter)), expected)
        self.assertEqual({p: p.read_bytes() for p in before_files}, before_files)
        self.assertTrue(doc["campaign"]["reference_blueprint"]["copied_source_blueprint"])
        self.assertEqual(doc["campaign"]["reference_blueprint"]["goal_id"], GOAL)
        self.assertIn("the_star", extra["templated"])
        self.assertFalse(any(k.startswith("_") for k in chapter))
        self.assertFalse(any(k.startswith("_") for q in chapter["quests"] for k in q))

    def test_foreign_dependencies_follow_preserved_native_goal_id(self):
        doc, chapters, _, _ = self.export()
        self.assertEqual(doc["campaign"]["finale_quest_ids"], [GOAL])
        self.assertFalse(any(q["id"] == "old_final" for c in doc["chapters"] for q in c["quests"]))
        foreign = next(q for name, c in chapters.items() if name != "the_star" for q in c["quests"])
        self.assertEqual(foreign["dependencies"], [GOAL])
        self.assertNotEqual(foreign["id"], "foreign")
        self.assertEqual([q["id"] for q in chapters["the_star"]["quests"]], [START, BRANCH, GOAL])

    def test_reference_disabled_never_loads_or_reuses_installed_book(self):
        original = generated_doc()
        before = copy.deepcopy(original)
        with patch("quest_reference.load_installed_book", side_effect=AssertionError("reference was read")):
            result = A._use_installed_goal_blueprint(original, self.scan, self.opts | {"mimic": False})
            self.assertEqual(result, before)
            fresh = A.local_quest_doc(self.scan, [], self.opts | {
                "mimic": False, "campaign": True, "campaign_goal": "demo:star",
                "density": "tiny", "vanilla_chapters": False})
        self.assertTrue(any(t.get("item") == "demo:star" for c in fresh["chapters"]
                            for q in c["quests"] for t in q["tasks"]))
        self.assertFalse(any(c.get("_native_reference") for c in fresh["chapters"]))
        self.assertNotIn("reference_blueprint", fresh["campaign"])

    def test_missing_installed_book_retains_fresh_campaign(self):
        with tempfile.TemporaryDirectory() as empty:
            scan = self.scan | {"instance_dir": empty}
            original = generated_doc()
            self.assertEqual(A._use_installed_goal_blueprint(original, scan, self.opts), generated_doc())

    def test_unclosed_map_and_external_reward_pool_fall_back_without_mutation(self):
        for change in ("external_dependency", "reward_table"):
            with self.subTest(change=change):
                self.source = source_chapter()
                if change == "external_dependency":
                    self.source["quests"][0]["dependencies"] = ["7000000000000001"]
                else:
                    self.source["quests"][0]["rewards"] = [{"type": "random", "table_id": 123}]
                self.save_source()
                self.assertEqual(self.integrate(), generated_doc())

    def test_closed_task_dependency_keeps_exact_narrow_reference(self):
        self.source["quests"][1]["dependencies"] = [START_TASK]
        self.save_source()
        _, chapters, _, _ = self.export()
        self.assertEqual(chapters["the_star"]["quests"][1]["dependencies"], [START_TASK])

    def test_open_removes_native_locks_without_changing_tasks_or_placement(self):
        _, chapters, _, _ = self.export(progression="open")
        quests = chapters["the_star"]["quests"]
        self.assertTrue(all(not q.get("dependencies") for q in quests))
        self.assertEqual([(q["x"], q["y"]) for q in quests], [(q["x"], q["y"]) for q in self.source["quests"]])
        self.assertEqual([q["tasks"] for q in quests], [q["tasks"] for q in self.source["quests"]])

    def test_layout_override_repositions_native_quests_preserving_graph(self):
        _, chapters, _, _ = self.export(layout="lanes")
        chapter = chapters["the_star"]
        self.assertNotEqual([(q["x"], q["y"]) for q in chapter["quests"]],
                            [(q["x"], q["y"]) for q in self.source["quests"]])
        self.assertEqual([q.get("dependencies", []) for q in chapter["quests"]],
                         [q.get("dependencies", []) for q in self.source["quests"]])
        self.assertTrue(chapter["images"])
        self.assertTrue(all(image["image"].startswith("ftbquests:textures/shapes/") for image in chapter["images"]))

    def test_seed_preserves_reference_and_varies_requested_original_layout(self):
        def positions(layout, seed):
            _, chapters, _, _ = self.export(layout=layout, seed=seed)
            return [(q["x"], q["y"]) for q in chapters["the_star"]["quests"]]
        self.assertEqual(positions("reference", 1), positions("reference", 2))
        self.assertNotEqual(positions("radial", 1), positions("radial", 2))

    def test_native_appearance_options_have_specific_effects(self):
        _, chapters, groups, _ = self.export(decor_art=False, quest_shape="circle", groups=False)
        chapter = chapters["the_star"]
        self.assertEqual(chapter["images"], [])
        self.assertTrue(all(q["shape"] == "circle" for q in chapter["quests"]))
        self.assertEqual(groups, [])
        self.assertEqual(chapter["group"], "")

    def test_native_rewards_scale_effective_amounts_and_keep_components(self):
        for setting, factor in (("lean", .5), ("standard", 1), ("generous", 2)):
            for outer in (None, 0, 9):
                with self.subTest(setting=setting, outer_count=outer):
                    self.source = source_chapter()
                    original_reward = self.source["quests"][-1]["rewards"][0]
                    if outer is not None:
                        original_reward["count"] = outer
                    self.save_source()
                    _, chapters, _, _ = self.export(reward=setting)
                    rewards = [q["rewards"][0] for q in chapters["the_star"]["quests"]]
                    self.assertEqual(rewards[0]["xp"], round(80 * factor))
                    self.assertEqual(rewards[1]["xp_levels"], round(6 * factor))
                    self.assertEqual(reward_count(rewards[2]), round((outer or 7) * factor))
                    self.assertEqual(rewards[2]["item"]["components"], original_reward["item"]["components"])

    def test_native_plain_and_minimal_settings_remove_styling_and_art(self):
        _, chapters, _, _ = self.export(style_chapters=False, aesthetic="minimal")
        chapter = chapters["the_star"]
        self.assertEqual(chapter["title"], "The original Star")
        self.assertEqual(chapter["images"], [])
        self.assertEqual(chapter["default_quest_shape"], "")
        for quest in chapter["quests"]:
            self.assertNotIn("shape", quest)
            self.assertNotIn("size", quest)
            self.assertNotIn("description", quest)

    def test_native_loose_opens_optional_branches_and_gui_rewards_scale(self):
        options = dict(self.opts, progression="loose", reward_mult=2)
        options.pop("reward")
        doc = self.integrate()
        chapters, _, _ = A.build_chapters(doc, lambda *_: None, options)
        quests = dict(chapters)["the_star"]["quests"]
        self.assertEqual(quests[1].get("dependencies", []), [])
        self.assertEqual(quests[2]["dependencies"], [BRANCH])
        self.assertEqual(reward_count(quests[2]["rewards"][0]), 14)


if __name__ == "__main__":
    unittest.main()
