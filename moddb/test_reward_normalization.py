"""Repeated direct XP rewards collapse without altering payout or loot odds."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A


class RewardNormalizationTests(unittest.TestCase):
    def test_actual_quest_conversion_sums_points_and_levels_separately(self):
        raw = {"id": "terra_plate", "tasks": [{"type": "checkmark"}], "rewards": [
            {"type": "xp", "xp": 60}, {"type": "xp", "xp": 60},
            {"type": "xp_levels", "xp_levels": 2}, {"type": "xp", "amount": 100},
            {"type": "xp_levels", "xp_levels": 3}]}
        built = A._quest(raw, "botania", {"terra_plate": "ABC"}, lambda _: None)
        self.assertEqual([("xp", 220), ("xp_levels", 5)],
                         [(reward["type"], reward[reward["type"]]) for reward in built["rewards"]])
        self.assertEqual(5, len(raw["rewards"]), "Conversion must not mutate the source reward list")

    def test_non_xp_rewards_nbt_order_and_first_ids_are_unchanged(self):
        item = {"id": "item", "type": "item", "item": {"id": "demo:tool", "Count": 1,
                 "tag": {"display": {"Name": "A specific tool"}}}, "count": 1}
        loot = {"id": "loot", "type": "random", "table_id": 42}
        rewards = [{"id": "first", "type": "xp", "xp": 60}, item,
                   {"id": "second", "type": "xp", "xp": 25}, loot, copy.deepcopy(item)]
        before = copy.deepcopy(rewards)
        normalized = A._combine_quest_xp_rewards(rewards)
        self.assertEqual(["first", "item", "loot", "item"], [r["id"] for r in normalized])
        self.assertEqual(85, normalized[0]["xp"])
        self.assertIs(item, normalized[1])
        self.assertIs(loot, normalized[2])
        self.assertEqual(before, rewards)
        self.assertEqual(normalized, A._combine_quest_xp_rewards(normalized))

    def test_weighted_loot_table_xp_entries_remain_separate(self):
        doc = {"reward_tables": [{"id": "loot", "rewards": [
            {"type": "xp", "xp": 60, "weight": 2},
            {"type": "xp", "xp": 100, "weight": 5}]}]}
        tables, _mapping = A.build_reward_tables(doc, lambda _: None)
        self.assertEqual([(60, 2), (100, 5)],
                         [(r["xp"], r["weight"]) for r in tables[0][1]["rewards"]])

    def test_built_and_saved_book_contains_one_exact_xp_payout(self):
        doc = {"chapters": [{"id": "botania", "quests": [{"id": "mana_pool",
            "title": "Mana Pool", "tasks": [{"type": "checkmark"}], "rewards": [
                {"type": "xp", "xp": 60}, {"type": "xp", "xp": 25}]}]}]}
        chapters, groups, extra = A.build_chapters(doc, lambda _: None, {"layout": "lanes"})
        rewards = chapters[0][1]["quests"][0]["rewards"]
        self.assertEqual(1, len(rewards))
        self.assertEqual(85, rewards[0]["xp"])
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            A.write_snbt(folder, chapters, groups, False, lambda _: None,
                         reward_tables=extra.get("reward_tables"))
            saved = A.snbt_loads(next((folder / "chapters").glob("*.snbt")).read_text(encoding="utf-8"))
        self.assertEqual(rewards, saved["quests"][0]["rewards"])


if __name__ == "__main__":
    unittest.main()
