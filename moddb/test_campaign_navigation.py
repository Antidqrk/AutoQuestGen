"""The player can find the goal without changing progression or quest identity."""
import copy
import unittest

import autoquestgen as A


def chapter(key, group):
    return {"id": key, "title": key, "group": group, "quests": [
        {"id": key + "_q", "title": key, "tasks": [{"type": "checkmark"}]}]}


class CampaignNavigationTests(unittest.TestCase):
    def test_export_promotes_goal_group_and_keeps_goal_before_journey(self):
        doc = {"chapters": [chapter("machines", "Tech"), chapter("intro", "Getting Started"),
                chapter("journey", "Main Goal"), chapter("goal", "Main Goal")],
               "campaign": {"sidebar": {"group": "Main Goal", "after_group": "Getting Started",
                                         "chapter_ids": ["goal", "journey"]}}}
        original = copy.deepcopy(doc)
        opts = {"groups": True, "layout": "lanes", "decor_art": False, "auto_chain": False}
        exported, groups, _ = A.build_chapters(doc, lambda *_: None, opts)
        self.assertEqual([c["title"] for _, c in exported], ["intro", "goal", "journey", "machines"])
        self.assertEqual([g["title"] for g in groups], ["Getting Started", "Main Goal", "Tech"])
        self.assertEqual([c["order_index"] for _, c in exported], list(range(4)))
        baseline = copy.deepcopy(original)
        baseline.pop("campaign")
        old, _, _ = A.build_chapters(baseline, lambda *_: None, opts)
        identity = lambda entries: {c["title"]: (c["id"], c["quests"][0]["id"], c["quests"][0]["tasks"]) for _, c in entries}
        self.assertEqual(identity(old), identity(exported))

    def test_without_intro_goal_is_first_and_without_campaign_order_is_unchanged(self):
        doc = {"chapters": [chapter("tech", "Tech"), chapter("goal", "Main Goal")],
               "campaign": {"sidebar": {"group": "Main Goal", "chapter_ids": ["goal"]}}}
        self.assertEqual([c["id"] for c in A._campaign_navigation(doc)], ["goal", "tech"])
        doc.pop("campaign")
        self.assertEqual([c["id"] for c in A._campaign_navigation(doc)], ["tech", "goal"])


if __name__ == "__main__":
    unittest.main()
