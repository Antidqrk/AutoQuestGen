"""CLI presets and campaign validation, with every output in a temporary folder."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A
from moddb.test_offline_story import scan_for


class CliBuildTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.mods = self.folder / "mods"
        self.mods.mkdir()
        self.output = self.folder / "output"
        self.stack.enter_context(patch.object(A, "LOG_PATH", self.folder / "test.log"))
        self.stack.enter_context(patch.object(A, "DATA_DIR", self.folder))
        self.stack.enter_context(patch.object(A, "_load_templates", return_value={}))
        self.scan = scan_for(["demo:machine%d" % i for i in range(8)])
        self.scan_mock = self.stack.enter_context(patch.object(A, "scan_mods", return_value=self.scan))
        self.stdout = self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(redirect_stderr(io.StringIO()))

    def capture_pipeline(self, **kwargs):
        doc = {"chapters": [{"id": "demo", "title": "Demo", "quests": [
            {"id": "first", "title": "Start", "tasks": [{"type": "checkmark"}]}]}]}
        built = [("demo", {"id": "ABCD", "title": "Demo", "quests": []})]
        with patch.object(A, "local_quest_doc", return_value=doc) as generate, \
                patch.object(A, "clean_doc", side_effect=lambda value, *_args: value), \
                patch.object(A, "build_chapters", return_value=(built, [], {})) as layout, \
                patch.object(A, "write_snbt") as write:
            result = A.run_cli_build(str(self.mods), str(self.output), **kwargs)
        self.assertEqual(0, result, self.stdout.getvalue())
        write.assert_called_once()
        return generate.call_args.args[2], layout.call_args.args[2]

    def test_no_preset_preserves_existing_defaults(self):
        content, layout = self.capture_pipeline()
        for key, expected in {"density": "large", "aesthetic": "decorated",
                              "layout": "lanes", "creativity": 0.4,
                              "groups": True, "mimic": False, "campaign": False,
                              "campaign_goal": ""}.items():
            self.assertEqual(expected, content[key], key)
        self.assertNotIn("seed", content)
        self.assertEqual("lanes", layout["layout"])
        self.assertEqual("decorated", layout["aesthetic"])

    def test_epic_matches_gui_options_and_shares_one_seed(self):
        for available in (False, True):
            with self.subTest(references=available), \
                    patch.object(A, "_load_templates", return_value={"demo": [{}]} if available else {}):
                content, layout = self.capture_pipeline(preset="epic")
            for key, expected in {"density": "massive", "aesthetic": "lavish",
                                  "layout": "reference", "creativity": 0.55,
                                  "groups": True, "campaign": True, "mimic": True,
                                  "vanilla_chapters": True, "progression": "linear"}.items():
                self.assertEqual(expected, content[key], key)
            self.assertEqual(content["seed"], layout["seed"])
            self.assertGreater(content["seed"], 0)
            self.assertEqual("lavish", layout["aesthetic"])
            self.assertEqual("reference", layout["layout"])
            self.assertEqual("auto", layout["quest_shape"])
            self.assertTrue(layout["style_chapters"])
            self.assertTrue(layout["decor_art"])

    def test_explicit_density_overrides_preset_and_goal_enables_campaign(self):
        content, _ = self.capture_pipeline(preset="epic", density="small")
        self.assertEqual("small", content["density"])
        content, _ = self.capture_pipeline(goal="  craft demo:machine7  ")
        self.assertEqual("large", content["density"])
        self.assertTrue(content["campaign"])
        self.assertEqual("craft demo:machine7", content["campaign_goal"])
        content, _ = self.capture_pipeline(preset="balanced")
        self.assertEqual("balanced", content["aesthetic"])
        self.assertFalse(content["campaign"])

    def test_invalid_goal_rejects_before_generation_and_writes_nothing(self):
        with patch.object(A, "local_quest_doc") as generate, patch.object(A, "write_snbt") as write:
            result = A.run_cli_build(str(self.mods), str(self.output), goal="craft missing:reactor")
        self.assertEqual(2, result)
        generate.assert_not_called()
        write.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertIn("no verified obtainable item", self.stdout.getvalue())

    def test_actual_goal_pipeline_writes_readable_quest_book(self):
        result = A.run_cli_build(str(self.mods), str(self.output), density="small",
                                 preset="epic", goal="craft demo:machine7")
        self.assertEqual(0, result, self.stdout.getvalue())
        chapters = list((self.output / "chapters").glob("*.snbt"))
        self.assertTrue(chapters)
        book = A.quests_to_doc(self.output)
        goals = [task.get("item") for chapter in book["chapters"]
                 for quest in chapter["quests"] for task in quest.get("tasks", [])]
        self.assertIn("demo:machine7", goals)

    def test_other_instance_destination_is_rejected_before_scan_or_write(self):
        other = self.folder / "other-pack" / "config" / "ftbquests" / "quests"
        existing = other / "chapters" / "existing.snbt"
        existing.parent.mkdir(parents=True)
        existing.write_text("existing book", encoding="utf-8")
        result = A.run_cli_build(str(self.mods), str(other), preset="epic")
        self.assertEqual(2, result)
        self.scan_mock.assert_not_called()
        self.assertEqual("existing book", existing.read_text(encoding="utf-8"))
        self.assertEqual([existing], list(other.rglob("*.*")))
        self.assertIn("different instances", self.stdout.getvalue())

    def test_running_game_blocks_live_destination_but_allows_scratch_export(self):
        live = self.folder / "config" / "ftbquests" / "quests"
        with patch.object(A, "minecraft_running", return_value=True):
            result = A.run_cli_build(str(self.mods), str(live), density="small")
            self.assertEqual(1, result)
            self.assertFalse(live.exists())
            result = A.run_cli_build(str(self.mods), str(self.output), density="small")
            self.assertEqual(0, result, self.stdout.getvalue())
            self.assertTrue(list((self.output / "chapters").glob("*.snbt")))

    def test_parser_handles_quoted_goal_and_options_before_build(self):
        argv = ["AutoQuestGen", "--preset", "epic", "--goal", "master Botania",
                "--build", str(self.mods), str(self.output), "--density", "normal"]
        with patch.object(sys, "argv", argv), patch.object(A, "run_cli_build", return_value=0) as build:
            with self.assertRaises(SystemExit) as stopped:
                A.main()
        self.assertEqual(0, stopped.exception.code)
        build.assert_called_once_with(str(self.mods), str(self.output), "normal",
                                  preset="epic", goal="master Botania", scenario=None, campaign_scope="selected", quest_size_style=None,
                                  decor_source="library", decor_theme="auto", decor_collection="epic",
                                  decor_motion=True, decor_size="mixed", book_style="immersive",
                                  shop_enabled=False, shop_style="balanced", shop_prices="normal")

    def test_artwork_choices_flow_to_layout_and_invalid_theme_does_not_scan(self):
        _, layout = self.capture_pipeline(decor_source='accents', decor_theme='restoration')
        self.assertEqual((layout['decor_source'], layout['decor_theme']), ('accents', 'restoration'))
        self.scan_mock.reset_mock()
        self.assertEqual(A.run_cli_build(str(self.mods), str(self.output), decor_theme='invented'), 2)
        self.scan_mock.assert_not_called()

    def test_node_size_option_changes_export_and_epic_default(self):
        _, layout = self.capture_pipeline(preset="epic")
        self.assertEqual(layout["quest_size_style"], "dramatic")
        _, layout = self.capture_pipeline(preset="epic", quest_size_style="uniform")
        self.assertEqual(layout["quest_size_style"], "uniform")
        self.assertEqual(A.run_cli_build(str(self.mods), str(self.output), quest_size_style="mystery"), 2)

    def test_explicit_goal_scope_reaches_the_builder(self):
        argv = ["AutoQuestGen", "--scope", "goal", "--goal", "defeat Gaia Guardian",
                "--build", str(self.mods), str(self.output)]
        with patch.object(sys, "argv", argv), patch.object(A, "run_cli_build", return_value=0) as build:
            with self.assertRaises(SystemExit):
                A.main()
        self.assertEqual(build.call_args.kwargs["campaign_scope"], "goal")

    def test_unknown_scope_fails_before_scan(self):
        self.assertEqual(A.run_cli_build(str(self.mods), str(self.output), campaign_scope="mystery"), 2)
        self.scan_mock.assert_not_called()

    def test_parser_rejects_missing_values_and_unknown_options(self):
        for args in (["--build"], ["--build", "mods", "out", "--goal"],
                     ["--build", "mods", "out", "--preset", "unknown"],
                     ["--build", "mods", "out", "--mystery"], ["--goal", "master Botania"]):
            with self.subTest(args=args), patch.object(sys, "argv", ["AutoQuestGen"] + args), \
                    patch.object(A, "run_cli_build") as build, patch.object(A.tk, "Tk") as gui:
                with self.assertRaises(SystemExit) as stopped:
                    A.main()
                self.assertEqual(2, stopped.exception.code)
                build.assert_not_called()
                gui.assert_not_called()

    def test_no_arguments_still_opens_the_gui(self):
        with patch.object(sys, "argv", ["AutoQuestGen"]), patch.object(A, "_set_aumid"), \
                patch.object(A.tk, "Tk") as root, patch.object(A, "App") as app:
            A.main()
        app.assert_called_once_with(root.return_value)
        root.return_value.mainloop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
