"""Optional barter economy preserves progression, original rewards and IDs."""
import copy
from pathlib import Path
import tempfile
import unittest

import quest_shop as S


def fixture():
    quests = [
        {"id": "iron", "title": "Mine reliably", "tasks": [{"type": "item", "item": "minecraft:iron_pickaxe"}],
         "rewards": [{"type": "xp", "xp": 123}]},
        {"id": "diamond", "title": "A diamond tool", "dependencies": ["iron"],
         "tasks": [{"type": "item", "item": "minecraft:diamond_pickaxe"}]},
        {"id": "enchant", "title": "Enchant the workshop", "dependencies": ["diamond"],
         "tasks": [{"type": "item", "item": "minecraft:enchanting_table"}], "rewards": []},
    ]
    doc = {"chapters": [{"id": "workshop", "title": "Workshop", "quests": quests}]}
    known = {"minecraft:" + row[0] for row in S._OFFERS} | S._EARLY_ACHIEVEMENTS | {S.CURRENCY}
    scan = {"vanilla": known, "items": {}, "mods": [], "unobtainable": set()}
    return doc, scan


class QuestShopTests(unittest.TestCase):
    def apply(self, doc=None, scan=None, **options):
        default_doc, default_scan = fixture()
        return S.apply_shop(doc or default_doc, scan or default_scan, {"shop_enabled": True, **options})

    def test_disabled_is_a_nonmutating_noop(self):
        doc, scan = fixture()
        before = copy.deepcopy(doc)
        self.assertEqual(S.apply_shop(doc, scan), before)
        self.assertEqual(doc, before)

    def test_shop_has_native_consumed_payments_repeatability_and_one_team_reward(self):
        result = self.apply()
        shop = result["chapters"][-1]
        self.assertEqual(shop["group"], "Shop")
        self.assertGreater(len(shop["quests"]), 15)
        for q in shop["quests"]:
            self.assertTrue(q["optional"])
            self.assertTrue(q["can_repeat"])
            self.assertEqual(q["repeat_cooldown"], 0)
            task, reward = q["tasks"][0], q["rewards"][0]
            self.assertEqual(task["item"], S.CURRENCY)
            self.assertTrue(task["consume_items"])
            self.assertGreaterEqual(task["count"], 1)
            self.assertNotIn("task_screen_only", task)
            self.assertTrue(reward["team_reward"])
            self.assertEqual(reward["auto"], "disabled")
            self.assertLessEqual(reward["count"], 64)
            self.assertTrue(any("partial payment" in line for line in q["description"]))

    def test_real_converter_preserves_native_payment_and_repeat_flags(self):
        import autoquestgen as A
        doc, scan = fixture()
        chapters, groups, extra = A.build_chapters(doc, lambda *_: None,
            {"_scan": scan, "shop_enabled": True, "decor_art": False, "auto_chain": False,
             "layout": "lanes", "seed": 17, "groups": True})
        shop = next(c for _, c in chapters if "Supply Shop" in c["title"])
        self.assertGreater(len(shop["quests"]), 15)
        for quest in shop["quests"]:
            self.assertTrue(quest["can_repeat"])
            self.assertEqual(quest["repeat_cooldown"], 0)
            self.assertTrue(quest["optional"])
            task, reward = quest["tasks"][0], quest["rewards"][0]
            self.assertEqual(task["item"], S.CURRENCY)
            self.assertTrue(task["consume_items"])
            self.assertTrue(reward["team_reward"])
            self.assertEqual(reward["auto"], "disabled")
        parsed = A.snbt_loads(A.snbt_dumps(shop))
        self.assertEqual(parsed, shop)

    def test_native_reference_bonus_ids_survive_export_without_replacing_original_ids(self):
        import autoquestgen as A
        doc, scan = fixture()
        chapter = doc["chapters"][0]
        chapter.update(id="1000000000000001", filename="native", _native_reference=True)
        lookup = {q["id"]: "%016X" % (0x2000000000000001 + i)
                  for i, q in enumerate(chapter["quests"])}
        for i, quest in enumerate(chapter["quests"]):
            quest["id"] = lookup[quest["id"]]
            quest["dependencies"] = [lookup[d] for d in quest.get("dependencies", [])]
            quest.update(x=float(i * 3), y=0.0)
            quest["tasks"][0]["id"] = "%016X" % (0x3000000000000001 + i)
        original_reward_id = "4000000000000001"
        chapter["quests"][0]["rewards"][0]["id"] = original_reward_id
        before = copy.deepcopy(doc)
        raw = self.apply(doc, scan)
        raw_bonus_ids = [q["rewards"][-1]["id"] for q in raw["chapters"][0]["quests"]]
        self.assertEqual(len(set(raw_bonus_ids)), 3)
        for key in raw_bonus_ids:
            self.assertRegex(key, r"^[0-7][0-9A-F]{15}$")
            self.assertGreater(int(key, 16), 0)
        self.assertEqual(self.apply(raw, scan), raw)
        chapters, _, _ = A.build_chapters(doc, lambda *_: None,
            {"_scan": scan, "shop_enabled": True, "decor_art": False, "auto_chain": False,
             "layout": "reference", "quest_size_style": "preserve", "seed": 17})
        native = dict(chapters)["native"]
        self.assertEqual(native["quests"][0]["rewards"][0], before["chapters"][0]["quests"][0]["rewards"][0])
        self.assertEqual([q["rewards"][-1]["id"] for q in native["quests"]], raw_bonus_ids)
        self.assertEqual(A.snbt_loads(A.snbt_dumps(native)), native)
        self.assertEqual(doc, before)

    def test_bonus_id_collision_with_existing_objects_is_resolved_stably(self):
        doc, scan = fixture()
        collision = self.apply(doc, scan)["chapters"][0]["quests"][0]["rewards"][-1]["id"]
        doc["chapters"][0]["quests"][0]["rewards"][0]["id"] = collision
        out = self.apply(doc, scan)
        rewards = out["chapters"][0]["quests"][0]["rewards"]
        self.assertEqual(rewards[0]["id"], collision)
        self.assertNotEqual(rewards[-1]["id"], collision)
        self.assertEqual(self.apply(doc, scan), out)

    def test_disk_import_refresh_and_disable_never_duplicate_shop_or_currency(self):
        import autoquestgen as A
        doc, scan = fixture()
        options = {"_scan": scan, "shop_enabled": True, "decor_art": False,
                   "auto_chain": False, "layout": "lanes", "seed": 17, "groups": True}
        def currency(chapters):
            return {r["id"]: (q["id"], r.get("count", 1))
                    for _, c in chapters if not S._is_exported_shop(c)
                    for q in c["quests"] for r in q.get("rewards", []) if r.get("item") == S.CURRENCY}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = None
            for attempt in range(3):
                chapters, groups, _ = A.build_chapters(doc, lambda *_: None,
                    options | {"preserve_ids": attempt > 0})
                shops = [c for _, c in chapters if S._is_exported_shop(c)]
                self.assertEqual(len(shops), 1)
                self.assertEqual(len(shops[0]["quests"]), len(S._OFFERS))
                if baseline is None:
                    baseline = currency(chapters)
                    self.assertEqual(len(baseline), 3)
                else:
                    self.assertEqual(currency(chapters), baseline)
                A.write_snbt(root, chapters, groups, False, lambda *_: None)
                doc = A.quests_to_doc(root)
                self.assertNotIn("shop", doc)
                self.assertTrue(S.has_generated_shop(doc))
            disabled, _, _ = A.build_chapters(doc, lambda *_: None,
                options | {"preserve_ids": True, "shop_enabled": False})
            self.assertFalse(any(S._is_exported_shop(c) for _, c in disabled))
            self.assertEqual(currency(disabled), {})
            self.assertEqual(sum(len(c["quests"]) for _, c in disabled), 3)
            self.assertEqual([r["xp"] for _, c in disabled for q in c["quests"]
                              for r in q.get("rewards", []) if r["type"] == "xp"], [123])

    def test_metadata_free_detection_does_not_claim_an_authors_supply_shop(self):
        import autoquestgen as A
        doc, scan = fixture()
        chapters, _, _ = A.build_chapters(doc, lambda *_: None,
            {"_scan": scan, "shop_enabled": True, "decor_art": False, "auto_chain": False})
        shop = copy.deepcopy(next(c for _, c in chapters if S._is_exported_shop(c)))
        shop["id"] = "1000000000000099"
        author = {"chapters": [shop]}
        self.assertFalse(S.has_generated_shop(author))
        self.assertEqual(S.apply_shop(author, scan, {"shop_enabled": False}), author)
        refreshed = self.apply(author, scan, preserve_ids=True)
        self.assertEqual(refreshed["chapters"][0], shop)

    def test_reserved_chapter_id_with_modified_payment_is_not_deleted_or_duplicated(self):
        import autoquestgen as A
        doc, scan = fixture()
        chapters, _, _ = A.build_chapters(doc, lambda *_: None,
            {"_scan": scan, "shop_enabled": True, "decor_art": False, "auto_chain": False})
        shop = copy.deepcopy(next(c for _, c in chapters if S._is_exported_shop(c)))
        shop["quests"][0]["tasks"][0]["consume_items"] = False
        imported = {"chapters": [shop]}
        self.assertFalse(S.has_generated_shop(imported))
        self.assertEqual(S.apply_shop(imported, scan, {"shop_enabled": False}), imported)
        with self.assertRaisesRegex(ValueError, "existing chapter"):
            self.apply(imported, scan, preserve_ids=True)

    def test_existing_goals_tasks_and_dependencies_are_unchanged_and_rewards_only_added(self):
        doc, scan = fixture()
        before = copy.deepcopy(doc)
        result = self.apply(doc, scan)
        self.assertEqual(doc, before)
        original_quests = before["chapters"][0]["quests"]
        actual = result["chapters"][0]["quests"]
        for old, new in zip(original_quests, actual):
            for field in ("id", "title", "tasks", "dependencies"):
                self.assertEqual(old.get(field), new.get(field))
            self.assertEqual(new["rewards"][:len(old.get("rewards", []))], old.get("rewards", []))
            bonus = new["rewards"][-1]
            self.assertEqual(bonus["item"], S.CURRENCY)
            self.assertTrue(bonus["team_reward"])
        shop_ids = {q["id"] for q in result["chapters"][-1]["quests"]}
        self.assertFalse(any(shop_ids.intersection(q.get("dependencies", [])) for q in actual))
        self.assertEqual(result["shop"]["unlocks"], {"0": None, "1": "iron", "2": "diamond"})

    def test_no_purchase_can_satisfy_an_existing_objective_or_requested_goal(self):
        doc, scan = fixture()
        doc["chapters"][0]["quests"].append({"id": "bread_goal", "tasks": [{"type": "item", "item": {"id": "minecraft:bread"}}]})
        doc["campaign"] = {"exact_goal_ids": ["minecraft:torch"]}
        result = self.apply(doc, scan)
        sold = {q["rewards"][0]["item"] for q in result["chapters"][-1]["quests"]}
        self.assertNotIn("minecraft:bread", sold)
        self.assertNotIn("minecraft:torch", sold)
        self.assertNotIn("minecraft:diamond_pickaxe", sold)
        self.assertEqual(result["shop"]["protected_objectives"], ["minecraft:bread", "minecraft:torch"])

    def test_missing_or_unobtainable_supplies_are_excluded_without_replacement(self):
        doc, scan = fixture()
        scan["vanilla"].remove("minecraft:torch")
        scan["unobtainable"].add("minecraft:bread")
        result = self.apply(doc, scan)
        sold = {q["rewards"][0]["item"] for q in result["chapters"][-1]["quests"]}
        self.assertNotIn("minecraft:torch", sold)
        self.assertNotIn("minecraft:bread", sold)

    def test_missing_verified_currency_refuses_to_invent_an_economy(self):
        doc, scan = fixture()
        scan["unobtainable"].add(S.CURRENCY)
        with self.assertRaisesRegex(ValueError, "verified obtainable"):
            self.apply(doc, scan)

    def test_price_levels_and_stock_styles_materially_change_their_outputs(self):
        prices = {}
        for level in S.SHOP_PRICES:
            result = self.apply(shop_prices=level)
            prices[level] = {q["rewards"][0]["item"]: q["tasks"][0]["count"] for q in result["chapters"][-1]["quests"]}
        self.assertEqual([prices[k]["minecraft:bread"] for k in ("low", "normal", "high")], [1, 2, 3])
        stocks = {style: {q["rewards"][0]["item"] for q in self.apply(shop_style=style)["chapters"][-1]["quests"]}
                  for style in S.SHOP_STYLES}
        self.assertIn("minecraft:scaffolding", stocks["builder"])
        self.assertNotIn("minecraft:scaffolding", stocks["adventurer"])
        self.assertIn("minecraft:carrot", stocks["essentials"])
        self.assertEqual(stocks["balanced"], set.union(*(stocks[s] for s in S.SHOP_STYLES if s != "balanced")))

    def test_repeated_application_is_identical_and_disabling_restores_exact_original(self):
        doc, scan = fixture()
        result = self.apply(doc, scan)
        self.assertEqual(self.apply(result, scan), result)
        self.assertEqual(S.apply_shop(result, scan, {"shop_enabled": False}), doc)
        different = self.apply(result, scan, shop_style="builder")
        self.assertEqual(len([c for c in different["chapters"] if c.get("_shop")]), 1)
        self.assertEqual(len(different["shop"]["currency_awards"]), 3)

    def test_tiny_book_gets_no_invented_milestones_or_free_repeatable_currency(self):
        doc, scan = fixture()
        doc["chapters"][0]["quests"] = [{"id": "intro", "tasks": [{"type": "checkmark"}]}]
        result = self.apply(doc, scan)
        self.assertEqual(result["shop"]["currency_awards"], [])
        self.assertEqual(result["shop"]["unlocks"], {"0": None})
        self.assertTrue(all(q["_shop_tier"] == 0 for q in result["chapters"][-1]["quests"]))
        self.assertTrue(all(q["rewards"][0]["item"] != S.CURRENCY for q in result["chapters"][-1]["quests"]))

    def test_income_is_bounded_and_never_added_to_repeatable_quests(self):
        doc, scan = fixture()
        doc["chapters"][0]["quests"] = [
            {"id": "milestone%d" % i, "tasks": [{"type": "item", "item": "minecraft:iron_pickaxe"}],
             "can_repeat": i == 0} for i in range(100)]
        result = self.apply(doc, scan)
        self.assertEqual(len(result["shop"]["currency_awards"]), 24)
        self.assertLessEqual(result["shop"]["bonus_emeralds"], 72)
        self.assertNotIn("rewards", result["chapters"][0]["quests"][0])


if __name__ == "__main__":
    unittest.main()
