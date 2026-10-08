"""Identical objectives and rewards must remain separate FTB objects."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A


class ObjectIdTests(unittest.TestCase):
    def convert(self, tasks, rewards=None):
        return A._quest({"id": "test", "tasks": tasks, "rewards": rewards or []},
                        "chapter", {"test": "ABC"}, lambda _: None)

    def test_repeated_same_type_tasks_keep_all_goals_with_unique_stable_ids(self):
        for task in ({"type": "checkmark"}, {"type": "item", "item": "demo:gear", "count": 4},
                     {"type": "kill", "entity": "minecraft:zombie", "count": 3}):
            with self.subTest(task=task):
                single = self.convert([copy.deepcopy(task)])["tasks"][0]
                inputs = [copy.deepcopy(task) for _ in range(4)]
                before = copy.deepcopy(inputs)
                result = self.convert(inputs)["tasks"]
                self.assertEqual(4, len(result))
                self.assertEqual(4, len({t["id"] for t in result}))
                self.assertEqual(single["id"], result[0]["id"])
                expected = {k: v for k, v in single.items() if k != "id"}
                self.assertTrue(all({k: v for k, v in t.items() if k != "id"} == expected for t in result))
                self.assertEqual(result, self.convert(inputs)["tasks"])
                self.assertEqual(before, inputs)
                # Adding a different objective must not renumber the repeated ones.
                extra = self.convert([{"type": "dimension", "dimension": "minecraft:the_nether"}] + inputs)["tasks"]
                self.assertEqual([t["id"] for t in result], [t["id"] for t in extra[1:]])

    def test_repeated_non_xp_rewards_preserve_separate_payouts(self):
        reward = {"type": "item", "item": "minecraft:diamond", "count": 3}
        rewards = self.convert([{"type": "checkmark"}], [reward, reward])["rewards"]
        self.assertEqual(2, len({r["id"] for r in rewards}))
        self.assertEqual([3, 3], [r["count"] for r in rewards])
        self.assertEqual(["minecraft:diamond"] * 2, [r["item"] for r in rewards])

    def test_identical_loot_entries_keep_weights_and_get_unique_ids(self):
        entry = {"type": "item", "item": "minecraft:diamond", "count": 2, "weight": 5}
        doc = {"reward_tables": [{"id": "loot", "rewards": [entry, entry]}]}
        tables, _ = A.build_reward_tables(doc, lambda _: None)
        entries = tables[0][1]["rewards"]
        self.assertEqual(2, len({r["id"] for r in entries}))
        self.assertEqual([(2, 5), (2, 5)], [(r["count"], r["weight"]) for r in entries])

    def test_entire_built_book_has_distinct_chapter_quest_task_reward_ids(self):
        raw = {"chapters": [{"id": "one", "quests": [
            {"id": "first", "tasks": [{"type": "checkmark"}, {"type": "checkmark"}],
             "rewards": [{"type": "item", "item": "minecraft:book"}] * 2},
            {"id": "second", "tasks": [{"type": "checkmark"}]}]}]}
        chapters, _, _ = A.build_chapters(raw, lambda _: None)
        ids = []
        for _, chapter in chapters:
            ids.append(chapter["id"])
            for quest in chapter["quests"]:
                ids.append(quest["id"])
                ids.extend(child["id"] for key in ("tasks", "rewards") for child in quest.get(key, []))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(0 < int(key, 16) < 2 ** 63 for key in ids))


if __name__ == "__main__":
    unittest.main()
