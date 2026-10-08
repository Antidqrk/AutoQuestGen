"""Layout regressions that reflect what a player sees in the quest map."""
import copy
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

import quest_layout as layouts

from quest_layout import (authored_positions, layout, reference_positions,
                          reference_fallback_style, reference_introductions,
                          campaign_positions)


def quest(qid, *parents):
    return {"id": qid, "dependencies": list(parents)}


def wide_campaign():
    quests = [dict(quest(f"achievement{i}", f"other_chapter{i}"), size=1.2)
              for i in range(34)]
    for i in range(9):
        # Uneven groups are deliberate: the real Arcanum book has both
        # single-achievement collections and six-achievement collections.
        starts = (0, 6, 12, 13, 16, 22, 25, 27, 33, 34)
        quests.append(dict(quest(f"collection{i}", *(f"achievement{j}"
                                                    for j in range(starts[i], starts[i + 1]))), size=1.4))
    quests.append(dict(quest("milestone0", *(f"collection{i}" for i in range(6))), size=1.4))
    quests.append(dict(quest("milestone1", *(f"collection{i}" for i in range(6, 9))), size=1.4))
    quests.append(dict(quest("final", "milestone0", "milestone1"), size=2.0))
    return quests


def dense_material_campaign():
    """120-node recipe: 13 capstones, 79 ingredients, 27 deeper ingredients."""
    quests = []
    groups = (9, 9, 3, 9, 9, 7, 7, 6, 0, 4, 7, 0, 9)
    for branch, count in enumerate(groups):
        leaves = []
        for i in range(count):
            qid = f"material{branch}_{i}"
            quests.append(dict(quest(qid), size=1.1))
            leaves.append(qid)
        quests.append(dict(quest(f"component{branch}", *leaves), size=1.7))
    # Uneven deeper fans reproduce the failure: one branch has very narrow
    # angular gaps while most other branches only need a short material fan.
    for branch, count in ((3, 12), (10, 6), (12, 9)):
        parent = next(q for q in quests if q["id"] == f"material{branch}_0")
        for i in range(count):
            qid = f"preparation{branch}_{i}"
            quests.append(dict(quest(qid), size=1.1))
            parent["dependencies"].append(qid)
    # Shared ingredients stay one node with genuine additional edges.
    next(q for q in quests if q["id"] == "component1")["dependencies"].append("material0_0")
    next(q for q in quests if q["id"] == "component12")["dependencies"].append("material3_1")
    quests.append(dict(quest("star", *(f"component{i}" for i in range(13))), size=2.6))
    return quests


class QuestLayoutTests(unittest.TestCase):
    def assert_icon_clearance(self, pos, quests, min_gap):
        for i, a in enumerate(quests):
            for b in quests[i + 1:]:
                minimum = max(min_gap, (a.get("size", 1) + b.get("size", 1)) / 2 + .6)
                self.assertGreaterEqual(math.dist(pos[a["id"]], pos[b["id"]]), minimum - .003,
                                        (a["id"], b["id"], minimum))

    def test_dramatic_nodes_have_clearance_in_every_generated_style(self):
        quests = [dict(quest("start"), size=2.2)]
        for branch in range(8):
            quests += [dict(quest(f"branch{branch}", "start"), size=1.55),
                       dict(quest(f"tool{branch}", f"branch{branch}"), size=.85),
                       dict(quest(f"goal{branch}", f"tool{branch}"), size=2.2)]
        before = copy.deepcopy(quests)
        for style in ("line", "tree", "lanes", "radial", "rosette", "clusters", "spine", "matrix", "spiral", "spread", "reference"):
            with self.subTest(style=style):
                pos = layout(quests, style, 1.13, "dramatic")
                self.assert_icon_clearance(pos, quests, 2.05)
                self.assertEqual(set(pos), {q["id"] for q in quests})
                self.assertEqual(pos, layout(quests, style, 1.13, "dramatic"))
        self.assertEqual(quests, before)

    def test_reference_atlas_compaction_does_not_collapse_larger_icons(self):
        quests = [dict(quest("intro"), size=2.2, tasks=[{"type": "checkmark"}])]
        for branch in range(24):
            quests.append(dict(quest(f"branch{branch}", "intro"), size=1.55))
            if branch < 8:
                quests.append(dict(quest(f"goal{branch}", f"branch{branch}"), size=2.2))
        before = copy.deepcopy(quests)
        pos = layout(quests, "reference", 1.13, "atlas")
        self.assert_icon_clearance(pos, quests, 1.64)
        self.assertEqual(quests, before)
        xs, ys = zip(*pos.values())
        self.assertLess(max(xs) - min(xs), 40)
        self.assertLess(max(ys) - min(ys), 35)

    def test_dramatic_convergence_keeps_large_goal_central_and_branches_clear(self):
        quests = dense_material_campaign()
        for q in quests:
            q['size'] = 2.2 if q['id'] == 'star' else 1.55 if q['id'].startswith('component') else .85
        before = copy.deepcopy(quests)
        pos = campaign_positions(quests, 1.13, "dramatic")
        self.assertEqual(pos['star'], (0, 0))
        self.assert_icon_clearance(pos, quests, 1.95)
        self.assertEqual(quests, before)
        xs, ys = zip(*pos.values())
        self.assertLess(max(xs) - min(xs), 36)
        self.assertLess(max(ys) - min(ys), 36)

    def test_size_repair_is_local_and_does_not_rewrite_authored_reference_maps(self):
        quests = [dict(quest('a'), size=2.2), dict(quest('b', 'a'), size=2.2),
                  dict(quest('far'), size=.85)]
        original = {'a': (0, 0), 'b': (2.05, 0), 'far': (20, 8)}
        pos = layouts.fit_node_sizes(original, quests)
        self.assertEqual(original['a'], (0, 0))
        self.assertEqual(pos['far'], original['far'])
        self.assert_icon_clearance(pos, quests, 2.05)
        authored = [dict(q, x=original[q['id']][0], y=original[q['id']][1]) for q in quests]
        self.assertEqual(reference_positions(authored), original)

    def test_dense_skipped_rank_recipes_reduce_crossings_without_sprawling(self):
        # Portable anonymized topology from a generated 111-node recipe
        # island. The prior map has 204 crossings despite compact bounds.
        data = json.loads((Path(__file__).parent / "moddb/layout_dense_fixture.json").read_text())
        quests = [quest(str(i), *(str(p) for p in row["parents"]))
                  for i, row in enumerate(data["nodes"])]
        before = copy.deepcopy(quests)
        baseline = {str(i): (row["x"], row["y"]) for i, row in enumerate(data["nodes"])}
        pos = layouts._improve_reference_island(quests, baseline, .92, data["seed"])
        _, deps, _ = layouts._graph(quests)
        self.assertEqual(quests, before)
        self.assertEqual(set(pos), set(baseline))
        self.assertEqual(layouts._edge_crossings(baseline, deps), 204)
        self.assertLess(layouts._edge_crossings(pos, deps), 120)
        points = list(pos.values())
        self.assertGreater(min(math.dist(a, b) for i, a in enumerate(points)
                               for b in points[i + 1:]), 2.049)
        self.assertLess(max(math.dist(pos[q], pos[p]) for q in deps for p in deps[q]), 14)

        def area(points):
            xs, ys = zip(*points.values())
            return (max(xs) - min(xs) + 2.05) * (max(ys) - min(ys) + 2.05)

        self.assertLessEqual(area(pos), area(baseline) * 1.6)
        self.assertEqual(pos, layouts._improve_reference_island(quests, baseline, .92, data["seed"]))

    def test_dense_refinement_has_fixed_size_budget_and_skips_explicit_styles(self):
        quests = [quest(str(i), *(str(j) for j in range(max(0, i - 2), i)))
                  for i in range(181)]
        baseline = {q["id"]: (i * 2.5, 0) for i, q in enumerate(quests)}
        with patch.object(layouts, "_virtual_rank_layers", side_effect=AssertionError("budget exceeded")):
            self.assertIs(layouts._improve_reference_island(quests, baseline, .9, "large"), baseline)
        small = [quest("root")] + [quest(str(i), "root") for i in range(20)]
        with patch.object(layouts, "_improve_reference_island", side_effect=AssertionError("explicit style changed")):
            layout(small, "lanes", .9, "explicit")
            self.assertEqual(reference_positions([dict(quest("authored"), x=3, y=4)]), {"authored": (3, 4)})

    def test_dense_material_fan_cannot_expand_every_other_branch(self):
        quests = dense_material_campaign()
        before = copy.deepcopy(quests)
        self.assertEqual(len(quests), 120)
        for seed in ("epic", "1", "2"):
            with self.subTest(seed=seed):
                pos = campaign_positions(quests, 1.13, seed)
                self.assertEqual(pos["star"], (0, 0))
                self.assertEqual(set(pos), {q["id"] for q in quests})
                xs, ys = zip(*pos.values())
                self.assertLess(max(xs) - min(xs), 36)
                self.assertLess(max(ys) - min(ys), 36)
                points = list(pos.values())
                self.assertGreater(min(math.dist(a, b) for i, a in enumerate(points)
                                       for b in points[i + 1:]), 1.94)
                self.assertLess(max(math.dist(pos[q["id"]], pos[d]) for q in quests
                                    for d in q.get("dependencies", []) if d in pos), 32)
        self.assertEqual(quests, before)

    def test_wide_campaign_converges_to_prominent_central_finale(self):
        quests = wide_campaign()
        before = copy.deepcopy(quests)
        pos = campaign_positions(quests, 1.13, "epic")
        self.assertEqual(pos["final"], (0, 0))
        self.assertEqual(quests, before)
        self.assertEqual(set(pos), {q["id"] for q in quests})
        xs, ys = zip(*pos.values())
        self.assertLess(max(xs) - min(xs), 26)
        self.assertLess(max(ys) - min(ys), 26)
        self.assertGreater(min(math.dist(a, b) for i, a in enumerate(pos.values())
                               for b in list(pos.values())[i + 1:]), 1.94)
        for q in quests:
            for prerequisite in q.get("dependencies", []):
                if prerequisite in pos:
                    self.assertGreater(math.hypot(*pos[prerequisite]), math.hypot(*pos[q["id"]]))
        self.assertLess(max(math.dist(pos[q["id"]], pos[d]) for q in quests
                            for d in q.get("dependencies", []) if d in pos), 11)

    def test_campaign_seed_and_variation_change_positions_without_moving_finale(self):
        quests = wide_campaign()
        first = campaign_positions(quests, .15, "a")
        self.assertEqual(first, campaign_positions(quests, .15, "a"))
        self.assertNotEqual(first, campaign_positions(quests, .15, "b"))
        self.assertNotEqual(first, campaign_positions(quests, 1.55, "a"))
        self.assertEqual(campaign_positions(quests, 1.55, "b")["final"], (0, 0))

    def test_campaign_shared_prerequisite_stays_a_single_node_with_both_edges(self):
        quests = wide_campaign()
        quests[35]["dependencies"].append("achievement0")
        before = copy.deepcopy(quests)
        pos = campaign_positions(quests, .7, "shared")
        self.assertEqual(len(pos), len(quests))
        self.assertEqual(quests, before)

    def test_open_campaign_does_not_gain_visual_or_actual_convergence_edges(self):
        quests = wide_campaign()
        for q in quests:
            q["dependencies"] = []
        before = copy.deepcopy(quests)
        self.assertEqual(campaign_positions(quests, .7, "open"), layout(quests, "lanes", .7, "open"))
        self.assertEqual(quests, before)

    def test_parallel_recipe_chains_keep_their_lanes(self):
        quests = [quest("start")]
        for stage in range(1, 6):
            # Deliberately interleave the input; insertion order is not a lane.
            for branch in ("power", "storage", "processing"):
                quests.append(quest(f"{branch}{stage}", "start" if stage == 1
                                    else f"{branch}{stage - 1}"))
        quests.append(quest("factory", "power5", "storage5", "processing5"))
        pos = layout(quests, "lanes", seed=12)
        orders = [tuple(sorted(("power", "storage", "processing"),
                               key=lambda b: pos[f"{b}{stage}"][1]))
                  for stage in range(1, 6)]
        self.assertTrue(all(order == orders[0] for order in orders))
        self.assertGreater(pos["factory"][0], pos["power5"][0])

    def test_large_collection_fan_has_icon_clearance(self):
        quests = [quest("start")] + [quest(str(i), "start") for i in range(100)]
        pos = layout(quests, "lanes", seed=12)
        pts = list(pos.values())
        self.assertGreater(min(math.dist(a, b) for i, a in enumerate(pts)
                               for b in pts[i + 1:]), 2.0)

    def test_layout_cannot_invent_dependencies_or_mutate_quests(self):
        quests = [quest("a"), quest("b", "a"), quest("c"), quest("d", "c", "external")]
        before = copy.deepcopy(quests)
        for style in ("line", "tree", "lanes", "radial", "clusters", "spine", "matrix"):
            self.assertEqual(set(layout(quests, style, seed=4)), {q["id"] for q in quests})
        self.assertEqual(quests, before)

    def test_seed_reproduces_branch_seating(self):
        quests = [quest("start")] + [quest(str(i), "start") for i in range(12)]
        self.assertEqual(layout(quests, seed=8), layout(quests, seed=8))
        self.assertNotEqual(layout(quests, seed=8), layout(quests, seed=9))

    def test_long_chain_bends_without_long_return_edges(self):
        quests = [quest("0")] + [quest(str(i), str(i - 1)) for i in range(1, 35)]
        pos = layout(quests, "lanes", seed=1)
        self.assertGreater(len({y for x, y in pos.values()}), 1)
        self.assertLess(max(math.dist(pos[str(i)], pos[str(i - 1)] )
                            for i in range(1, 35)), 3.0)

    def test_collapsed_and_nonfinite_templates_are_rejected(self):
        self.assertIsNone(authored_positions([quest("a"), quest("b"), quest("c")]))
        self.assertIsNone(authored_positions([dict(quest("a"), x=float("nan"))]))

    def test_stranded_template_endpoint_is_relaid_out(self):
        quests = [dict(quest("a"), x=0, y=0), dict(quest("b", "a"), x=3, y=0),
                  dict(quest("c", "a"), x=100, y=0)]
        self.assertIsNone(authored_positions(quests))

    def test_explicit_reference_mode_keeps_authored_long_routes(self):
        quests = [dict(quest("a"), x=0, y=0), dict(quest("b", "a"), x=3, y=0),
                  dict(quest("c", "a"), x=100, y=0)]
        self.assertEqual(reference_positions(quests), {"a": (0, 0), "b": (3, 0), "c": (100, 0)})

    def test_reference_overlap_repair_accounts_for_icon_sizes(self):
        quests = [dict(quest("a"), x=0, y=0, size=2.0),
                  dict(quest("b", "a"), x=1.0, y=0, size=1.5),
                  dict(quest("c", "b"), x=10, y=5, size=1.0)]
        before = copy.deepcopy(quests)
        pos = reference_positions(quests)
        self.assertGreaterEqual(math.dist(pos["a"], pos["b"]), 1.224)
        self.assertEqual(pos["c"], (10, 5))
        self.assertEqual(quests, before)

    def test_reference_keeps_authored_diagonal_half_grid_pattern(self):
        quests = [dict(quest("a"), x=0, y=0), dict(quest("b", "a"), x=0.5, y=0.5)]
        self.assertEqual(reference_positions(quests), {"a": (0, 0), "b": (0.5, 0.5)})

    def test_reference_fallback_keeps_broad_catalogs_compact(self):
        quests = [quest("start")]
        for branch in range(20):
            for stage in range(4):
                quests.append(quest(f"{branch}/{stage}", "start" if stage == 0
                                    else f"{branch}/{stage - 1}"))
        self.assertEqual(reference_fallback_style(quests, "catalog"), "lanes")
        pos = layout(quests, "reference", seed="catalog")
        self.assertLess(max(math.dist(pos[q["id"]], pos[d]) for q in quests
                            for d in q.get("dependencies", [])), 20)

    def test_reference_rejects_missing_and_collapsed_coordinates(self):
        self.assertIsNone(reference_positions([quest("a"), quest("b", "a")]))
        self.assertIsNone(reference_positions([dict(quest(str(i)), x=0, y=0) for i in range(4)]))

    def test_broad_reading_intro_reveals_compact_real_branch_islands(self):
        quests = [dict(quest("intro"), tasks=[{"type": "checkmark"}])]
        for branch in range(36):
            quests.append(quest(f"{branch}/0", "intro"))
            if branch < 8:
                for stage in range(1, 4):
                    quests.append(quest(f"{branch}/{stage}", f"{branch}/{stage - 1}"))
        before = copy.deepcopy(quests)
        pos = layout(quests, "reference", 1.0, "large intro")
        self.assertEqual(reference_introductions(quests), {"intro"})
        self.assertEqual(quests, before)
        self.assertEqual(pos, layout(quests, "reference", 1.0, "large intro"))
        xs, ys = zip(*pos.values())
        self.assertLess(max(ys) - min(ys), 24)
        self.assertLess(max(xs) - min(xs), 40)
        self.assertGreater(min(math.dist(a, b) for i, a in enumerate(pos.values())
                               for b in list(pos.values())[i + 1:]), 1.6)
        self.assertLess(max(math.dist(pos[q["id"]], pos[d]) for q in quests
                            for d in q.get("dependencies", []) if d != "intro"), 4)

    def test_item_or_exploration_hub_never_counts_as_reading_intro(self):
        quests = [dict(quest("hub"), tasks=[{"type": "item", "item": "minecraft:iron_ingot"}])]
        quests += [quest(str(i), "hub") for i in range(24)]
        self.assertEqual(reference_introductions(quests), set())
        quests[0]["tasks"].append({"type": "checkmark"})
        self.assertEqual(reference_introductions(quests), set())

    def test_long_trunk_with_side_goals_is_a_compact_route(self):
        quests = [quest("0")] + [quest(str(i), str(i - 1)) for i in range(1, 16)]
        quests += [quest("side" + str(i), str(i)) for i in (4, 8, 11)]
        pos = layout(quests, "lanes", seed=1)
        xs, ys = zip(*pos.values())
        self.assertLess((max(xs) - min(xs)) / (max(ys) - min(ys)), 2.5)
        self.assertLess(max(math.dist(pos[q["id"]], pos[d]) for q in quests
                            for d in q.get("dependencies", [])), 5.0)

    def test_valid_authored_map_preserved_and_overlap_repaired(self):
        quests = [dict(quest("a"), x=0, y=0), dict(quest("b", "a"), x=3, y=2)]
        self.assertEqual(authored_positions(quests), {"a": (0, 0), "b": (3, 2)})
        quests[1]["x"], quests[1]["y"] = 0.1, 0.1
        repaired = authored_positions(quests)
        self.assertGreaterEqual(math.dist(repaired["a"], repaired["b"]), 1.84)


if __name__ == "__main__":
    unittest.main()
