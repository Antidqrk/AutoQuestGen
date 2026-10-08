"""User-facing visual controls must change their advertised output fields."""
import copy
import itertools
import math
from pathlib import Path
import unittest
import zipfile

import autoquestgen as A


def sample_book():
    quests = [{"id": "start", "title": "Build a workshop", "tasks": [{"type": "checkmark"}]}]
    for branch in ("power", "processing", "storage"):
        for stage in range(1, 5):
            quests.append({"id": f"{branch}{stage}", "title": f"{branch} {stage}",
                           "dependencies": ["start" if stage == 1 else f"{branch}{stage - 1}"],
                           "tasks": [{"type": "item", "item": "minecraft:iron_ingot"}]})
    quests += [{"id": "factory", "title": "Automate the factory",
                "dependencies": ["power4", "processing4", "storage4"],
                "tasks": [{"type": "item", "item": "minecraft:redstone_block"}]}]
    for i in range(4):
        quests.append({"id": f"tool{i}", "title": f"Workshop upgrade {i}",
                       "dependencies": ["processing2"],
                       "tasks": [{"type": "item", "item": "minecraft:iron_pickaxe"}]})
    for i, q in enumerate(quests):
        q.update(x=float(i % 5) * 3, y=float(i // 5) * 3, shape="gear")
    return {"chapters": [{"id": "workshop", "title": "Workshop", "group": "Technology",
                           "description": ["Build power.", "Process materials.", "Store outputs.", "Automate the workshop."],
                           "quests": quests}]}


def built(**changes):
    doc = changes.pop("doc", None) or sample_book()
    opts = dict(seed=17, layout="lanes", creativity=0.3, aesthetic="decorated",
                groups=True, style_chapters=True, group_style="bold", decor_art=True,
                quest_shape="auto", auto_chain=False, progression="linear")
    opts.update(changes)
    return A.build_chapters(copy.deepcopy(doc), lambda *a: None, opts)


def positions(chapters):
    return tuple((q["id"], float(q["x"]), float(q["y"]))
                 for _, ch in chapters for q in ch["quests"])


class VisualSettingsTests(unittest.TestCase):
    def test_campaign_reference_convergence_and_explicit_layout_override(self):
        from test_quest_layout import wide_campaign
        doc = {"chapters": [{"id": "campaign", "title": "The final project",
                             "_campaign": True, "quests": wide_campaign()}]}
        reference = built(doc=doc, layout="reference")[0][0][1]["quests"]
        self.assertEqual((reference[-1]["x"], reference[-1]["y"]), (0, 0))
        self.assertEqual(reference[-1]["size"], 1.9)
        matrix = built(doc=doc, layout="matrix")[0]
        doc["chapters"][0].pop("_campaign")
        self.assertEqual(positions(matrix), positions(built(doc=doc, layout="matrix")[0]))
        self.assertNotEqual(tuple((q["x"], q["y"]) for q in reference),
                            tuple((q["x"], q["y"]) for q in matrix[0][1]["quests"]))

    def test_live_mock_redraws_for_aesthetic_creativity_and_seed(self):
        class Canvas:
            h = 250

            def __init__(self):
                self.drawn = []

            def winfo_width(self):
                return 600

            def delete(self, *_):
                self.drawn.clear()

            def __getattr__(self, name):
                if not name.startswith("create_"):
                    raise AttributeError(name)
                return lambda *args, **kwargs: self.drawn.append((name, args, kwargs))

        opts = dict(decor_art=True, style_chapters=True, layout="lanes",
                    aesthetic="balanced", creativity=0.3, seed=17)
        def render(**changes):
            canvas = Canvas()
            A.QuestPreview.render(canvas, dict(opts, **changes))
            return repr(canvas.drawn)
        self.assertEqual(len({render(aesthetic=a) for a in A.AESTHETIC_LEVELS}), 4)
        self.assertNotEqual(render(creativity=0), render(creativity=1))
        self.assertNotEqual(render(seed=17), render(seed=18))

    def test_every_generation_layout_has_distinct_coordinates(self):
        signatures = {}
        for style in A.LAYOUTS:
            # Reference is a preservation/fallback policy rather than another
            # geometric style. Its behavior is tested explicitly below.
            if style in ("reference", "ai", "ai+tidy"):
                continue
            signatures[style] = positions(built(layout=style)[0])
        for first, second in itertools.combinations(signatures, 2):
            with self.subTest(first=first, second=second):
                self.assertNotEqual(signatures[first], signatures[second])

    def test_creativity_changes_every_layout_and_keeps_clearance(self):
        for style in A.LAYOUTS:
            if style in ("ai", "ai+tidy"):
                continue
            signatures = []
            for value in (0.0, 0.25, 0.5, 0.75, 1.0):
                chapters = built(layout=style, creativity=value)[0]
                signatures.append(positions(chapters))
                for _, ch in chapters:
                    pts = [(float(q["x"]), float(q["y"])) for q in ch["quests"]]
                    self.assertGreater(min(math.dist(a, b) for i, a in enumerate(pts)
                                           for b in pts[i + 1:]), 2.0, (style, value))
            self.assertEqual(len(set(signatures)), 5, style)

    def test_seed_changes_seating_but_never_ids_or_prerequisites(self):
        first = built(seed=17)[0]
        again = built(seed=17)[0]
        other = built(seed=18)[0]
        self.assertEqual(first, again)
        self.assertNotEqual(positions(first), positions(other))
        graph = lambda cs: [(q["id"], q.get("dependencies", [])) for _, ch in cs for q in ch["quests"]]
        self.assertEqual(graph(first), graph(other))

    def test_aesthetic_levels_change_actual_art_budget(self):
        counts = []
        for level in A.AESTHETIC_LEVELS:
            chapters = built(aesthetic=level)[0]
            counts.append(sum(len(ch["images"]) for _, ch in chapters))
        self.assertEqual(counts, [0, 3, 11, 24])
        minimal = built(aesthetic="minimal")[0][0][1]
        self.assertEqual(len({q["shape"] for q in minimal["quests"]}), 1)

    def test_every_forced_shape_overrides_existing_shapes(self):
        for shape in A.QUEST_SHAPES:
            chapters = built(quest_shape=shape)[0]
            for _, ch in chapters:
                self.assertEqual(ch["default_quest_shape"], shape)
                self.assertEqual({q["shape"] for q in ch["quests"]}, {shape})

    def test_header_modes_change_structure_with_and_without_color(self):
        for styled in (False, True):
            headers = [built(group_style=mode, style_chapters=styled)[1][0]["title"]
                       for mode in A.GROUP_STYLES]
            self.assertEqual(len(set(headers)), len(A.GROUP_STYLES))
        chapters, groups, _ = built(groups=False)
        self.assertEqual(groups, [])
        self.assertTrue(all(ch["group"] == "" for _, ch in chapters))

    def test_style_off_removes_generated_color_shapes_and_sizes(self):
        doc = sample_book()
        doc["chapters"][0]["title"] = "&b&lWorkshop"
        ch = built(doc=doc, style_chapters=False)[0][0][1]
        self.assertEqual(ch["title"], "Workshop")
        self.assertEqual(ch["default_quest_shape"], "")
        self.assertTrue(all("shape" not in q and "size" not in q for q in ch["quests"]))

    def test_backgrounds_off_also_hides_imported_art(self):
        doc = sample_book()
        doc["chapters"][0]["images"] = [{"image": A.VANILLA_BACKDROPS["stone"]}]
        self.assertTrue(built(doc=doc, decor_art=True)[0][0][1]["images"])
        self.assertEqual(built(doc=doc, decor_art=False)[0][0][1]["images"], [])

    def test_reference_objectives_obey_selected_layout(self):
        doc = sample_book()
        doc["chapters"][0]["_template"] = {"source": "reference"}
        self.assertNotEqual(positions(built(doc=doc, layout="line")[0]),
                            positions(built(doc=doc, layout="tree")[0]))

    def test_reference_mode_preserves_authored_positions_shapes_and_sizes(self):
        doc = sample_book()
        chapter = doc["chapters"][0]
        chapter["_template"] = {"source": "All the Mods 9 - ATM9"}
        chapter["quests"][0]["size"] = 2.25
        reference = built(doc=doc, layout="reference", quest_size_style="preserve", seed=17)[0][0][1]["quests"]
        repeated = built(doc=doc, layout="reference", quest_size_style="preserve", seed=18, creativity=1)[0][0][1]["quests"]
        for source, actual, other_seed in zip(chapter["quests"], reference, repeated):
            for key in ("x", "y", "shape"):
                self.assertEqual(source[key], actual[key])
                self.assertEqual(actual[key], other_seed[key])
            self.assertEqual(source.get("size", 1), actual.get("size", 1))

    def test_reference_fallback_hides_only_broad_intro_lines_and_keeps_gates(self):
        doc = sample_book()
        raw = doc["chapters"][0]["quests"]
        raw.extend({"id": f"entry{i}", "dependencies": ["start"],
                    "tasks": [{"type": "item", "item": "minecraft:stone"}]}
                   for i in range(20))
        reference = built(doc=doc, layout="reference")[0][0][1]["quests"]
        normal = built(doc=doc, layout="lanes")[0][0][1]["quests"]
        graph = lambda qs: [(q["id"], q.get("dependencies", [])) for q in qs]
        self.assertEqual(graph(reference), graph(normal))
        self.assertEqual([q["id"] for q in reference if q.get("hide_dependent_lines")],
                         [reference[0]["id"]])
        self.assertFalse(any(q.get("hide_dependent_lines") for q in normal))
        doc["chapters"][0]["_template"] = True
        for i, q in enumerate(raw):
            q.update(x=(i % 8) * 2, y=(i // 8) * 2)
        authored = built(doc=doc, layout="reference")[0][0][1]["quests"]
        self.assertFalse(any(q.get("hide_dependent_lines") for q in authored))

    def test_reference_fallback_uses_varied_original_layouts_without_invented_dependencies(self):
        from quest_layout import reference_fallback_style
        doc = sample_book()
        raw = doc["chapters"][0]["quests"]
        chosen = {reference_fallback_style(raw, f"chapter/workshop/{seed}") for seed in range(30)}
        self.assertEqual(chosen, {"lanes", "clusters", "spine"})
        expected_style = reference_fallback_style(raw, "chapter/workshop/17")
        reference = built(doc=doc, layout="reference")[0]
        expected = built(doc=doc, layout=expected_style)[0]
        self.assertEqual(positions(reference), positions(expected))
        self.assertEqual([q.get("dependencies", []) for _, ch in reference for q in ch["quests"]],
                         [q.get("dependencies", []) for _, ch in expected for q in ch["quests"]])

    def test_reference_mode_respects_an_explicit_shape_override(self):
        doc = sample_book()
        doc["chapters"][0]["_template"] = {"source": "reference"}
        quests = built(doc=doc, layout="reference", quest_shape="hexagon")[0][0][1]["quests"]
        self.assertEqual({q["shape"] for q in quests}, {"hexagon"})

    def test_reference_maps_honor_decor_budget_with_safe_stock_art(self):
        for templated in (False, True):
            doc = sample_book()
            if templated:
                doc["chapters"][0]["_template"] = {"source": "reference"}
            counts = []
            for level in A.AESTHETIC_LEVELS:
                images = built(doc=doc, layout="reference", aesthetic=level)[0][0][1]["images"]
                counts.append(len(images))
                self.assertTrue(all(im["image"].startswith("ftbquests:textures/shapes/") for im in images))
                self.assertTrue(all(isinstance(im["color"], int) and 0 <= im["color"] <= 0xffffff for im in images))
            self.assertEqual(counts, [0, 3, 11, 24])
            self.assertEqual(built(doc=doc, layout="reference", decor_art=False)[0][0][1]["images"], [])

    def test_reference_maps_keep_explicit_source_art_when_enabled(self):
        doc = sample_book()
        doc["chapters"][0]["_template"] = {"source": "reference"}
        doc["chapters"][0]["images"] = [{"image": A.VANILLA_BACKDROPS["stone"], "width": 2, "height": 2, "alpha": 230}]
        images = built(doc=doc, layout="reference", aesthetic="lavish")[0][0][1]["images"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["image"], A.VANILLA_BACKDROPS["stone"])
        self.assertEqual((images[0]["width"], images[0]["height"], images[0]["alpha"]), (2, 2, 230))
        self.assertEqual(built(doc=doc, layout="reference", decor_art=False)[0][0][1]["images"], [])

    def test_generated_art_leaves_the_quest_graph_visually_dominant(self):
        images = built(layout="reference", aesthetic="lavish")[0][0][1]["images"]
        layers = {order: [im for im in images if im["order"] == order] for order in (-20, -12, -1)}
        self.assertEqual([len(layers[order]) for order in (-20, -12, -1)], [1, 12, 11])
        self.assertTrue(all(0 < im["alpha"] <= 50 for im in layers[-20]))
        self.assertTrue(all(0 < im["alpha"] <= 36 for im in layers[-12]))
        self.assertTrue(all(im["width"] <= 0.9 and im["height"] <= 0.9 and 0 < im["alpha"] <= 160
                            for im in layers[-1]))

    def test_size_styles_change_actual_item_nodes_without_changing_objectives(self):
        books = {mode: built(quest_size_style=mode)[0][0][1]["quests"]
                 for mode in ("uniform", "varied", "dramatic")}
        self.assertEqual({q["size"] for q in books["uniform"]}, {1.0})
        for mode in ("varied", "dramatic"):
            item_quests = [q for q in books[mode] if any(t["type"] == "item" for t in q["tasks"])]
            self.assertGreaterEqual(len({q["size"] for q in item_quests}), 3)
            self.assertLess(min(q["size"] for q in item_quests), 1)
            self.assertGreater(max(q["size"] for q in item_quests), 1.7)
        self.assertGreater(max(q["size"] for q in books["dramatic"]), max(q["size"] for q in books["varied"]))
        semantics = lambda qs: [(q["id"], q["tasks"], q.get("rewards", []), q.get("dependencies", [])) for q in qs]
        self.assertEqual(semantics(books["uniform"]), semantics(books["dramatic"]))

    def test_explicit_size_choice_is_independent_of_colors_and_forced_shapes(self):
        for styled in (True, False):
            for shape in ("auto", "circle"):
                quests = built(quest_size_style="dramatic", style_chapters=styled, quest_shape=shape)[0][0][1]["quests"]
                self.assertGreaterEqual(len({q["size"] for q in quests}), 3)
                if shape == "circle":
                    self.assertEqual({q["shape"] for q in quests}, {"circle"})

    def test_campaign_landmark_overrides_a_shallower_goal_without_changing_graph(self):
        doc = sample_book()
        doc["campaign"] = {"goal_quest_ids": ["power2"], "finale_quest_ids": ["factory"]}
        ch = built(doc=doc, quest_size_style="dramatic")[0][0][1]
        qs = {q["title"]: q for q in ch["quests"]}
        self.assertEqual(qs["power 2"]["size"], 2.2)
        self.assertEqual(qs["Automate the factory"]["size"], 2.2)
        self.assertLess(qs["power 1"]["size"], qs["power 2"]["size"])

    def test_native_blueprint_size_preservation_is_an_explicit_choice(self):
        original = built(quest_size_style="uniform", decor_art=False)[0][0][1]
        original["_native_reference"] = True
        original["quests"][0]["size"] = 2.35
        doc = {"chapters": [original]}
        kept = built(doc=doc, layout="reference", quest_size_style="preserve", decor_art=False)[0][0][1]
        for before, after in zip(original["quests"], kept["quests"]):
            self.assertEqual([before.get(k) for k in ("id", "size", "x", "y")],
                             [after.get(k) for k in ("id", "size", "x", "y")])
        changed = built(doc=doc, layout="reference", quest_size_style="dramatic", aesthetic="lavish")[0][0][1]
        self.assertGreaterEqual(len({q["size"] for q in changed["quests"]}), 3)
        self.assertTrue(changed["images"])
        semantics = lambda chapter: [(q["id"], q["tasks"], q.get("dependencies")) for q in chapter["quests"]]
        self.assertEqual(semantics(original), semantics(changed))
        for i, first in enumerate(changed["quests"]):
            for second in changed["quests"][i+1:]:
                self.assertGreaterEqual(math.dist((first["x"], first["y"]), (second["x"], second["y"])),
                                        .35 * (first["size"] + second["size"]) - .001)

    def test_sizes_and_tinted_decor_survive_snbt_roundtrip(self):
        chapters = built(layout="reference", quest_size_style="dramatic", aesthetic="lavish")[0]
        for _, chapter in chapters:
            parsed = A.snbt_loads(A.snbt_dumps(chapter))
            self.assertEqual([q["size"] for q in chapter["quests"]], [q["size"] for q in parsed["quests"]])
            self.assertEqual(chapter["images"], parsed["images"])

    def test_generated_stock_art_paths_exist_in_installed_ftb_jar_when_available(self):
        jar = Path(__file__).parent / "work/minecraft-validation/instance/mods/ftb-quests-forge-2001.4.21.jar"
        if not jar.is_file():
            self.skipTest("Local FTB schema fixture not present")
        with zipfile.ZipFile(jar) as archive:
            paths = set(archive.namelist())
            for shape in A._SHAPE_PANELS:
                images = A._stock_chapter_decor("proof", sample_book()["chapters"][0]["quests"], A.AESTHETIC_LEVELS["lavish"], shape, "b")
                for image in images:
                    namespace, path = image["image"].split(":", 1)
                    self.assertIn("assets/" + namespace + "/" + path, paths)

    def test_open_progression_does_not_auto_chain_independent_goals(self):
        doc = sample_book()
        for q in doc["chapters"][0]["quests"]:
            q.pop("dependencies", None)
        ch = built(doc=doc, progression="open", auto_chain=True)[0][0][1]
        self.assertTrue(all(not q.get("dependencies") for q in ch["quests"]))


if __name__ == "__main__":
    unittest.main()
