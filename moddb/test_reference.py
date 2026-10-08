"""Installed-reference fidelity, English resolution and exact goal ancestry."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quest_reference import ReferenceBookError, goal_ancestry, installed_goal_blueprint, load_installed_book


class InstalledReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.instance = Path(self.temp.name)
        self.root = self.instance / "config/ftbquests/quests"
        self.root.mkdir(parents=True)

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def sample(self):
        self.put("chapters/start.snbt", {"id": "C1", "order_index": 0, "title": "Existing title",
                 "quests": [{"id": "Q1", "x": -3.5, "y": 2.0, "size": 1.25,
                             "tasks": [{"id": "T1", "type": "item", "item": {"id": "demo:material", "count": 12}}]}]})
        self.put("chapters/goal.snbt", {"id": "C2", "order_index": 2, "group": "G1",
                 "custom_chapter_flag": True, "quest_links": [{"id": "L1", "linked_quest": "Q1", "x": -1}],
                 "images": [{"id": "I1", "image": "demo:textures/goal.png", "rotation": 13.0, "width": 5.0}],
                 "quests": [{"id": "Q2", "dependencies": ["Q1"], "shape": "pentagon", "size": 5.0,
                             "hide_dependency_lines": True, "x": 0.0, "y": 0.0,
                             "tasks": [{"id": "T2", "type": "item", "count": 3,
                                        "item": {"id": "demo:goal", "components": {"demo:contents": {"kind": "star", "tier": 5}}}}]}]})
        self.put("chapter_groups.snbt", {"chapter_groups": [{"id": "G1", "custom": "keep"}]})
        self.put("data.snbt", {"version": 13, "progression_mode": "flexible", "grid_scale": 0.5})
        self.put("reward_tables/reward.snbt", {"id": "R1", "rewards": [{"id": "RW1", "type": "item", "item": {"id": "demo:gift", "count": 7}}]})

    def test_split_english_resolves_all_object_types_without_losing_fields_or_writing(self):
        self.sample()
        self.put("lang/en_us/chapters/goal.snbt", {
            "chapter.C2.title": "Final chapter", "chapter.C2.chapter_subtitle": ["Convergence"],
            "quest.Q2.title": "The real goal", "quest.Q2.quest_desc": ["Reach the goal."],
            "quest.Q2.quest_subtitle": "At last", "task.T2.title": "Three stars", "image.I1.title": "Goal art"})
        self.put("lang/en_us/file.snbt", {"file.0000000000000001.title": "Installed book"})
        self.put("lang/en_us/group.snbt", {"chapter_group.G1.title": "Main questline"})
        self.put("lang/en_us/reward.snbt", {"reward_table.R1.title": "Milestone rewards"})
        before = {p: p.read_bytes() for p in self.root.rglob("*.snbt")}
        book = load_installed_book(self.instance, json.loads)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*.snbt")})
        chapter = dict(book["chapters"])["goal"]
        self.assertEqual(chapter["title"], "Final chapter")
        self.assertEqual(chapter["subtitle"], ["Convergence"])
        self.assertTrue(chapter["custom_chapter_flag"])
        self.assertEqual(chapter["quest_links"][0]["linked_quest"], "Q1")
        self.assertEqual(chapter["images"][0]["rotation"], 13.0)
        self.assertEqual(chapter["images"][0]["title"], "Goal art")
        quest = chapter["quests"][0]
        self.assertEqual((quest["title"], quest["description"], quest["subtitle"]),
                         ("The real goal", ["Reach the goal."], "At last"))
        self.assertEqual(quest["tasks"][0]["item"]["components"]["demo:contents"], {"kind": "star", "tier": 5})
        self.assertEqual(quest["tasks"][0]["title"], "Three stars")
        self.assertEqual(quest["size"], 5.0)
        self.assertEqual(book["groups"][0], {"id": "G1", "custom": "keep", "title": "Main questline"})
        self.assertEqual(book["data"]["title"], "Installed book")
        self.assertEqual(book["reward_tables"][0][1]["title"], "Milestone rewards")
        self.assertIn("reward_tables/reward.snbt", book["extras"])
        self.assertEqual(len(book["sources"]), len(before))
        self.assertTrue(all(len(s["sha256"]) == 64 for s in book["sources"]))

    def test_single_language_file_and_existing_titles_are_supported(self):
        self.sample()
        self.put("lang/en_us.snbt", {"quest.Q1.title": "First material"})
        self.put("lang/fr_fr.snbt", {"quest.Q1.title": "French title"})
        book = load_installed_book(self.root, json.loads)
        self.assertEqual(book["chapters"][0][1]["title"], "Existing title")
        self.assertEqual(book["chapters"][0][1]["quests"][0]["title"], "First material")
        self.assertIn("lang/fr_fr.snbt", book["extras"])

    def test_ancestry_crosses_chapters_and_keeps_components_without_mutation(self):
        self.sample()
        book = load_installed_book(self.instance, json.loads)
        before = copy.deepcopy(book)
        graph = goal_ancestry(book, "demo:goal")
        self.assertEqual(book, before)
        self.assertEqual(graph["quest_ids"], ["Q1", "Q2"])
        self.assertEqual(graph["cross_chapter_edges"], [["Q1", "Q2"]])
        self.assertEqual(graph["goals"][0]["ancestor_ids"], ["Q1"])
        self.assertEqual(graph["goals"][0]["quest"]["tasks"][0]["count"], 3)
        self.assertFalse(goal_ancestry(book, "demo:missing")["goals"])

    def test_multiple_goals_and_unresolved_dependency_are_explicit(self):
        self.sample()
        book = load_installed_book(self.instance, json.loads)
        chapter = book["chapters"][1][1]
        chapter["quests"].append({"id": "Q3", "dependencies": ["MISSING"],
                                 "tasks": [{"type": "item", "item": "demo:goal"}]})
        graph = goal_ancestry(book, "demo:goal")
        self.assertEqual(len(graph["goals"]), 2)
        self.assertEqual(graph["missing_dependencies"], ["MISSING"])
        self.assertIn(["MISSING", "Q3"], graph["edges"])

    def test_task_prerequisites_preserve_their_narrower_meaning(self):
        self.sample()
        book = load_installed_book(self.instance, json.loads)
        quest = book["chapters"][1][1]["quests"][0]
        quest["dependencies"] = ["T1"]
        graph = goal_ancestry(book, "demo:goal")
        self.assertEqual(graph["quest_ids"], ["Q1", "Q2"])
        self.assertEqual(graph["edges"], [["T1", "Q2"]])
        self.assertEqual(graph["cross_chapter_edges"], [["T1", "Q2"]])
        self.assertEqual(graph["task_dependencies"], {"T1": "Q1"})
        self.assertEqual(quest["dependencies"], ["T1"])
        self.assertEqual(book["task_owners"]["T1"], "Q1")
        self.assertFalse(graph["missing_dependencies"])

    def test_bad_or_empty_book_is_not_silently_presented_as_complete(self):
        with self.assertRaises(ReferenceBookError):
            load_installed_book(self.instance, json.loads)
        self.sample()
        (self.root / "chapters/goal.snbt").write_text("broken", encoding="utf-8")
        with self.assertRaisesRegex(ReferenceBookError, "chapters/goal.snbt"):
            load_installed_book(self.instance, json.loads)

    def test_conflicting_translations_are_reported(self):
        self.sample()
        self.put("lang/en_us/a.snbt", {"quest.Q1.title": "A"})
        self.put("lang/en_us/b.snbt", {"quest.Q1.title": "B"})
        book = load_installed_book(self.instance, json.loads)
        self.assertEqual(book["translation_conflicts"], ["quest.Q1.title"])
        self.assertTrue(book["warnings"])
        self.assertEqual(book["chapters"][0][1]["quests"][0]["title"], "B")

    def test_blueprint_is_closed_preserves_full_chapter_and_labels_source(self):
        self.sample()
        book = load_installed_book(self.instance, json.loads)
        source = book["chapters"][1][1]
        source["quests"].insert(0, copy.deepcopy(book["chapters"][0][1]["quests"][0]))
        source["quests"][1]["dependencies"] = ["T1"]
        before = copy.deepcopy(source)
        blueprint = installed_goal_blueprint(book, "demo:goal")
        self.assertEqual(blueprint["chapter"], before)
        self.assertTrue(blueprint["provenance"]["copied_source_blueprint"])
        self.assertEqual(blueprint["provenance"]["goal_id"], "Q2")
        self.assertEqual(blueprint["provenance"]["quest_count"], 2)
        self.assertEqual(len(blueprint["provenance"]["source_sha256"]), 64)
        blueprint["chapter"]["quests"][1]["size"] = 1
        self.assertEqual(source, before)

    def test_blueprint_never_substitutes_a_later_continuation_for_nonclosed_main_goal(self):
        self.sample()
        book = load_installed_book(self.instance, json.loads)
        continuation = {"id": "C3", "order_index": 3, "quests": [
            {"id": "Q3", "tasks": [{"type": "item", "item": "demo:goal"}]}]}
        book["chapters"].append(("creative", continuation))
        self.assertIsNone(installed_goal_blueprint(book, "demo:goal"))
        main = book["chapters"][1][1]
        main["quests"][0]["dependencies"] = []
        main["quest_links"] = []
        self.assertEqual(installed_goal_blueprint(book, "demo:goal")["provenance"]["goal_id"], "Q2")
        self.assertIsNone(installed_goal_blueprint(book, "demo:not_present"))


if __name__ == "__main__":
    unittest.main()
