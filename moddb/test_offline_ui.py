"""Exercise the real offline UI with all writable paths isolated."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autoquestgen as A


class OfflineUiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        cfg = folder / "settings.json"
        cfg.write_text(json.dumps({"layout": "ai+tidy", "language": "Deutsch",
                                  "api_key": "isolated-test-value", "opt_mimic": True}))
        for attr, path in (("CONFIG_PATH", cfg), ("LOG_PATH", folder / "log.txt"),
                           ("EDITOR_SNAPSHOT", folder / "editor.json"),
                           ("LIBRARY_DIR", folder / "library"), ("DATA_DIR", folder)):
            self.stack.enter_context(patch.object(A, attr, path))
        self.stack.enter_context(patch.object(A, "discover_instances", return_value=[]))
        self.stack.enter_context(patch.object(A, "_guess_mods_dir", return_value=str(folder / "mods")))
        self.stack.enter_context(patch.object(A, "_guess_quests_dir", return_value=str(folder / "output")))
        self.stack.enter_context(patch.object(A, "_load_templates", return_value={}))
        self.network = self.stack.enter_context(patch.object(A, "fetch_models"))
        self.stack.enter_context(patch.object(A.messagebox, "showwarning"))
        self.stack.enter_context(patch.object(A.App, "_poll_mc"))
        self.stack.enter_context(patch.object(A.App, "_sfx"))
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = A.App(self.root)
        self.root.update_idletasks()

    def test_offline_only_widgets_and_legacy_settings_migration(self):
        tabs = [self.app.nb.tab(tab, "text").strip() for tab in self.app.nb.tabs()]
        self.assertNotIn("Prompt", tabs)
        for tab in ("Setup", "Generate", "Preview & build", "Repair", "Rewards & Tasks", "Import", "Library", "Preferences"):
            self.assertIn(tab, tabs)
        for name in ("key_entry", "model_cb", "language_combo", "btn_gen",
                     "_maxtok_setup", "_maxtok_gen", "_polish_btn", "prompt_box"):
            self.assertFalse(hasattr(self.app, name), name)
        self.assertEqual("lanes", self.app.layout.get())
        self.assertEqual("English", self.app.language.get())
        self.assertNotIn("ai", A.OFFLINE_LAYOUTS)
        self.assertNotIn("ai+tidy", A.OFFLINE_LAYOUTS)
        self.app._set_busy(True)
        self.app._set_busy(False)
        self.app.on_fetch_models()
        self.app.on_test()
        self.network.assert_not_called()
        self.assertIn("Mods folder not found", self.app._folder_hint.cget("text"))

    def test_sidebar_and_tools_reach_every_hidden_notebook_page(self):
        reached = set()
        for page, button in self.app._nav_buttons.items():
            button.invoke()
            selected = self.app.nb.tab(self.app.nb.select(), "text").strip()
            self.assertEqual(page, selected)
            reached.add(selected)
        for index in range(self.app.tools_menu.index("end") + 1):
            self.app.tools_menu.invoke(index)
            reached.add(self.app.nb.tab(self.app.nb.select(), "text").strip())
        pages = {self.app.nb.tab(page, "text").strip() for page in self.app.nb.tabs()}
        self.assertEqual(pages, reached, "A hidden page has no sidebar or tools-menu route")
        for name in ("Mods", "Log"):
            self.assertEqual(name, self.app.nb.tab(self.app._tab_index(name), "text").strip())

    def test_goal_browser_selection_roundtrips_to_a_real_boss_goal(self):
        from moddb.test_natural_goal_campaign import botania_scan
        from quest_campaign import resolve_goal
        self.app.scan_result = botania_scan()
        self.app.selected_ids = ["botania"]
        top = self.app._show_goal_browser()
        self.addCleanup(lambda: top.destroy() if top.winfo_exists() else None)
        def children(widget):
            direct = list(widget.winfo_children())
            return direct + [nested for child in direct for nested in children(child)]
        widgets = children(top)
        tree = next(w for w in widgets if isinstance(w, ttk.Treeview))
        entry = next(w for w in widgets if isinstance(w, ttk.Entry) and not isinstance(w, ttk.Combobox))
        entry.insert(0, "gaia")
        selected = next(key for key in tree.get_children() if tree.item(key, "values")[1] == "Encounters")
        tree.selection_set(selected)
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()
        use = next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == "Use this goal")
        self.assertNotIn("disabled", use.state())
        use.invoke()
        self.assertTrue(self.app.opt_campaign.get())
        plan = resolve_goal(self.app.scan_result, self.app.selected_ids, self.app.campaign_goal.get())
        self.assertEqual(plan["goal_target"]["target_id"], "botania:doppleganger")
        self.app._describe_campaign_goal()
        self.assertFalse(self.app._campaign_error)
        self.assertIn("creature defeat", self.app._goal_feedback.cget("text"))

    def test_goal_feedback_worker_does_not_block_or_publish_an_outdated_answer(self):
        import queue
        import threading
        from moddb.test_natural_goal_campaign import botania_scan
        self.app.scan_result = botania_scan()
        self.app.selected_ids = ["botania"]
        self.app.opt_campaign.set(True)
        self.app.campaign_goal.set("first goal")
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def resolve(_scan, _selected, goal):
            if goal == "first goal":
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test did not release worker")
                raise ValueError("outdated error")
            return {"explanation": "current answer"}
        with patch("quest_campaign.resolve_goal", side_effect=resolve):
            self.app._describe_campaign_goal_async()
            self.assertTrue(entered.wait(1), "goal computation did not start on the worker")
            self.assertFalse(release.is_set(), "the main UI call waited for the slow goal")
            self.app.campaign_goal.set("second goal")
            self.app._describe_campaign_goal_async()
            release.set()
            answers = []
            while len(answers) < 2:
                kind, payload = self.app.events.get(timeout=3)
                if kind == "goal_feedback":
                    answers.append(payload)
            self.app._accept_campaign_goal(answers[0])
            self.assertNotEqual(self.app._campaign_error, "outdated error")
            self.app._accept_campaign_goal(answers[1])
            self.assertEqual(self.app._goal_feedback.cget("text"), "current answer")
            self.assertEqual(self.app._campaign_error, "")

    def test_existing_settings_default_to_selected_mods_and_focused_route_size_is_explicit(self):
        from moddb.test_natural_goal_campaign import botania_scan
        self.app.scan_result = botania_scan()
        self.app.selected_ids = ["botania"]
        self.app.opt_campaign.set(True)
        self.app.campaign_goal.set("kill the gaia garden from botania")
        self.app.density.set("normal")
        self.assertEqual(self.app.campaign_scope.get(), "selected")
        self.assertEqual(self.app._prompt_opts()["campaign_scope"], "selected")
        self.app._describe_campaign_goal()
        self.assertIn("Selected mods + goal", self.app._goal_feedback.cget("text"))
        self.assertIn("The goal quest checks", self.app._goal_feedback.cget("text"))
        self.assertNotIn("disabled", self.app._density_row.combo.state())
        self.app.campaign_scope.set("goal")
        self.app._describe_campaign_goal()
        self.assertIsNotNone(self.app._focused_goal_count)
        self.assertIn("disabled", self.app._density_row.combo.state())
        for widget in (self.app._target_entry, self.app._vanilla_ch_toggle,
                       self.app._reference_toggle, self.app.themes_box):
            self.assertEqual(str(widget.cget("state")), "disabled")
        self.assertIn("focused route", self.app._count_hint.cget("text"))
        self.assertIn("-quest focused route", self.app._review_summary.cget("text"))
        self.assertNotIn("Normal", self.app._review_summary.cget("text"))
        self.app._save_cfg()
        self.assertEqual(json.loads(A.CONFIG_PATH.read_text())["campaign_scope"], "goal")
        self.app.campaign_scope.set("selected")
        self.app._describe_campaign_goal()
        self.assertNotIn("disabled", self.app._density_row.combo.state())
        self.assertNotIn("disabled", self.app._target_entry.state())
        self.assertIn("Scope: Selected mods + goal", self.app._review_summary.cget("text"))

    def test_epic_preset_expands_a_focused_goal_and_preserves_scenario_stages(self):
        from moddb.test_natural_goal_campaign import botania_scan
        self.app.scan_result = botania_scan()
        self.app.selected_ids = ["botania"]
        self.app.opt_campaign.set(True)
        self.app.campaign_goal.set("defeat Gaia Guardian")
        self.app.campaign_scope.set("goal")
        self.app._describe_campaign_goal()
        self.app._apply_book_preset("epic")
        self.app._describe_campaign_goal()
        self.assertEqual(self.app.campaign_scope.get(), "selected")
        self.assertNotIn("disabled", self.app._density_row.combo.state())
        self.assertIn("Selected mods + goal", self.app._preset_hint.cget("text"))
        self.app._scenario_plan = {"title":"Restore the world", "stages":[{"id":"water"}]}
        self.app._apply_book_preset("epic")
        self.assertEqual(len(self.app._scenario_plan["stages"]), 1)
        self.assertIn("1 authored stages + preparation", self.app._review_summary.cget("text"))
        self.assertIn("scenario's stages", self.app._preset_hint.cget("text"))

    def test_feedback_from_old_scope_cannot_disable_current_whole_book_budget(self):
        from moddb.test_natural_goal_campaign import botania_scan
        self.app.scan_result = botania_scan()
        self.app.selected_ids = ["botania"]
        self.app.opt_campaign.set(True)
        self.app.campaign_goal.set("defeat Gaia Guardian")
        payload = (self.app._goal_serial, self.app.scan_result, ["botania"], self.app.campaign_goal.get(),
                   self.app.progression.get(), "Stale focused response", "", "goal", 29)
        self.app._accept_campaign_goal(payload)
        self.assertNotEqual(self.app._goal_feedback.cget("text"), "Stale focused response")
        self.assertNotIn("disabled", self.app._density_row.combo.state())

    def test_size_hint_distinguishes_reference_scale_and_custom_target(self):
        self.app.opt_campaign.set(False)
        self.app.density.set("normal")
        self.app.target_count.set("")
        self.app._templates_available = True
        self.app.opt_mimic.set(True)
        self.assertIn("55%", self.app._count_hint.cget("text"))
        self.assertNotIn("250-380", self.app._count_hint.cget("text"))
        self.app.opt_mimic.set(False)
        self.assertIn(A.DENSITY_TARGETS["normal"], self.app._count_hint.cget("text"))
        self.app.target_count.set("500")
        self.assertIn("Aim for 500 quests across the book", self.app._count_hint.cget("text"))
        self.app.opt_mimic.set(True)
        self.assertIn("Aim for 500 quests across the book", self.app._count_hint.cget("text"))

    def test_scenario_editor_preserves_order_edits_and_applies_checked_plan(self):
        from moddb.test_scenarios import restoration_scan
        self.app.scan_result = restoration_scan()
        self.app.selected_ids = ["botania"]
        self.app._show_scenario_editor()
        editor = self.app._scenario_editor
        editor.suggest()
        self.assertEqual(len(editor.stages), 5)
        water = editor.stages[1]["id"]
        editor.tree.selection_set(water)
        editor.select()
        editor.stage_title.set("Return the village river")
        editor.description.delete("1.0", "end")
        editor.description.insert("1.0", "Fill the village riverbed with water, then confirm.")
        editor.move(1)
        self.assertEqual(editor.stages[2]["id"], water)
        self.assertEqual(editor.stages[2]["depends_on"], [editor.stages[1]["id"]])
        self.assertTrue(editor.check(show_review=False)["valid"])
        editor.use()
        plan = self.app._scenario_plan
        self.assertEqual(plan["stages"][2]["title"], "Return the village river")
        self.assertIn("village riverbed", plan["stages"][2]["description"])
        self.assertEqual(self.app._prompt_opts()["scenario"], plan)
        saved = json.loads(A.CONFIG_PATH.read_text())
        self.assertEqual(saved["scenario"], plan)
        self.app._describe_campaign_goal()
        self.assertIn("5 connected stages", self.app._goal_feedback.cget("text"))
        self.app.opt_campaign.set(False)
        self.assertIsNone(self.app._prompt_opts()["scenario"])
        self.app._clear_scenario()
        self.assertIsNone(self.app._scenario_plan)

    def test_scenario_draft_can_be_saved_but_unresolved_story_cannot_be_applied(self):
        from moddb.test_scenarios import restoration_scan
        self.app.scan_result = restoration_scan()
        self.app.selected_ids = ["botania"]
        self.app._show_scenario_editor()
        editor = self.app._scenario_editor
        editor.mode.set("authored")
        editor.add()
        editor.stage_title.set("A custom beginning")
        editor.save_draft()
        editor.use()
        self.assertIsNone(self.app._scenario_plan)
        self.assertTrue(editor.top.winfo_exists())
        editor.top.destroy()
        self.app._show_scenario_editor()
        restored = self.app._scenario_editor
        self.assertEqual(restored.stage_title.get(), "A custom beginning")
        self.assertFalse(restored.check(show_review=False)["valid"])
        restored.top.destroy()

    def test_scenario_pack_switch_rechecks_real_installed_goals(self):
        from moddb.test_scenarios import restoration_scan
        from quest_scenarios import propose_scenario
        scan = restoration_scan()
        self.app._scenario_plan = propose_scenario(scan, ["botania"], "Restore a barren world", allow_vanilla=True)
        self.app.scan_result = scan
        self.app.selected_ids = ["botania"]
        self.app.opt_campaign.set(True)
        self.app._describe_campaign_goal()
        self.assertFalse(self.app._campaign_error)
        scan["items"]["botania"].remove("botania:water_rod")
        self.app._describe_campaign_goal()
        self.assertTrue(self.app._campaign_error)
        self.assertIn("disabled", self.app.btn_gen_local.state())

    def test_coverage_window_search_and_pack_library_switch(self):
        from moddb.test_offline_story import scan_for
        self.app.scan_result = scan_for(["botania:mana_pool", "unknown:thing"])
        self.app._update_scan_lbl()
        self.assertIn("authored milestones", self.app._pack_summary.cget("text"))
        top = self.app._show_progression_coverage()
        self.addCleanup(top.destroy)
        def children(widget):
            out = list(widget.winfo_children())
            return out + [nested for child in out for nested in children(child)]
        widgets = children(top)
        tree = next(w for w in widgets if isinstance(w, ttk.Treeview))
        entry = next(w for w in widgets if isinstance(w, ttk.Entry))
        switch = next(w for w in widgets if isinstance(w, ttk.Checkbutton))
        self.assertEqual(set(tree.get_children()), {"botania", "unknown"})
        self.assertEqual(tree.item("unknown", "values")[1], "Recipes & discoveries")
        entry.insert(0, "botania")
        self.assertEqual(tree.get_children(), ("botania",))
        entry.delete(0, "end")
        switch.invoke()
        self.assertGreater(len(tree.get_children()), 100)
        tree.selection_set("botania")
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()
        detail = next(w for w in widgets if isinstance(w, tk.Text))
        self.assertIn("Milestones:", detail.get("1.0", "end"))

    def test_advanced_sections_can_open_and_close_without_losing_values(self):
        keys = {"folder", "appearance", "content", "advanced", "destinations", "sample", "json"}
        self.assertTrue(keys <= set(self.app._disclosures))
        self.app.book_title.set("My custom book")
        for key in sorted(keys):
            button, body = self.app._disclosures[key]
            self.assertEqual("", body.winfo_manager(), key + " should start collapsed")
            button.invoke()
            self.assertEqual("pack", body.winfo_manager(), key + " cannot be expanded")
            button.invoke()
            self.assertEqual("", body.winfo_manager(), key + " cannot be collapsed")
        self.assertEqual("My custom book", self.app.book_title.get())

    def test_build_navigation_requires_supported_scan_and_respects_busy_state(self):
        actions = (self.app.btn_continue_pack, self.app.btn_preview, self.app.btn_gen_local)
        for button in actions:
            self.assertEqual("disabled", str(button.cget("state")))
        self.app.scan_result = {"mods": [], "items": {"demo": {"demo:item"}}, "unsupported": False}
        self.app._update_scan_lbl()
        for button in actions:
            self.assertEqual("normal", str(button.cget("state")))
        self.app.btn_continue_pack.invoke()
        self.assertEqual("Generate", self.app.nb.tab(self.app.nb.select(), "text").strip())
        self.app._set_busy(True)
        for button in actions + (self.app.btn_scan,):
            self.assertEqual("disabled", str(button.cget("state")))
        self.app._set_busy(False)
        for button in actions:
            self.assertEqual("normal", str(button.cget("state")))
        self.app.scan_result["unsupported"] = True
        self.app._update_scan_lbl()
        for button in actions:
            self.assertEqual("disabled", str(button.cget("state")))
        self.assertEqual("normal", str(self.app.btn_scan.cget("state")))

    def test_friendly_dropdown_labels_update_generation_options(self):
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        combos = [w for w in descendants(self.root) if isinstance(w, ttk.Combobox)]
        for variable, label, value, reset, reset_label in (
                (self.app.progression, "Open exploration", "open", "linear", "Guided milestones"),
                (self.app.layout, "Matrix", "matrix", "lanes", "Lanes"),
                (self.app.density, "Small", "small", "large", "Large"),
                (self.app.quest_size_style, "Bold landmarks", "dramatic", "varied", "Varied by progression")):
            combo = next(c for c in combos if label in c.cget("values"))
            combo.set(label)
            combo.event_generate("<<ComboboxSelected>>")
            self.assertEqual(value, variable.get())
            variable.set(reset)
            self.assertEqual(reset_label, combo.get())

    def test_node_size_options_are_saved_and_epic_uses_bold_landmarks(self):
        self.assertEqual(self.app.quest_size_style.get(), "varied")
        self.app.quest_size_style.set("uniform")
        self.assertEqual(self.app._opts()["quest_size_style"], "uniform")
        self.app._save_cfg()
        self.assertEqual(json.loads(A.CONFIG_PATH.read_text())["quest_size_style"], "uniform")
        self.app._apply_book_preset("epic")
        self.assertEqual(self.app.quest_size_style.get(), "dramatic")
        self.app.layout.set("reference")
        self.assertIn("animations that move in Minecraft", self.app._background_hint.cget("text"))

    def test_main_preview_uses_generated_book_and_import_preview_is_explicit(self):
        def document(title):
            return {"chapters": [{"id": title.lower(), "title": title,
                    "quests": [{"id": "start", "title": "Start here",
                                "tasks": [{"type": "checkmark"}]}]}]}
        self.app.scan_result = {"mods": [], "items": {}, "entities": {}}
        self.app.paste.insert("1.0", json.dumps(document("Imported")))
        self.app._update_scan_lbl()
        with patch.object(self.app, "_run", side_effect=lambda fn, *args, **kwargs: fn()), \
                patch.object(A, "local_quest_doc", side_effect=lambda *args: document("Generated")) as generate, \
                patch("quest_preview.write_preview", return_value=A.DATA_DIR / "preview.html") as preview:
            self.app.btn_preview.invoke()
            generate.assert_called_once()
            self.assertIn("Generated", preview.call_args.args[0][0][1]["title"])
            generate.reset_mock()
            self.app.on_preview(imported=True)
            generate.assert_not_called()
            self.assertIn("Imported", preview.call_args.args[0][0][1]["title"])

    def test_inactive_options_are_disabled_and_explained(self):
        self.app.opt_groups.set(False)
        self.assertEqual("disabled", str(self.app._group_style_row.combo.cget("state")))
        self.assertIn("grouping", self.app._group_hint.cget("text"))
        self.app.opt_groups.set(True)
        self.app.opt_style_chapters.set(True)
        self.assertEqual("readonly", str(self.app._group_style_row.combo.cget("state")))
        self.app.aesthetic.set("minimal")
        self.assertEqual("disabled", str(self.app._background_toggle.cget("state")))
        self.assertIn("Minimal", self.app._background_hint.cget("text"))
        self.app.aesthetic.set("lavish")
        self.assertEqual("normal", str(self.app._background_toggle.cget("state")))
        self.assertEqual("normal", str(self.app._reference_toggle.cget("state")))
        self.app.opt_mimic.set(False)
        self.assertFalse(self.app.opt_mimic.get())
        self.app.opt_mimic.set(True)
        self.assertTrue(self.app.opt_mimic.get())

    def test_randomize_uses_effective_offline_settings(self):
        for _ in range(12):
            self.app.on_randomize()
            self.assertIn(self.app.layout.get(), A.OFFLINE_LAYOUTS)
            self.assertGreaterEqual(self.app.temperature.get(), 0)
            self.assertLessEqual(self.app.temperature.get(), 1)
        self.assertTrue(A.CONFIG_PATH.is_file())
        self.assertEqual(self.app.layout.get(), json.loads(A.CONFIG_PATH.read_text())["layout"])

    def test_import_rejects_online_search_and_missing_local_source(self):
        self.app.import_src.set("some online pack name")
        with patch.object(A, "find_pack_chapters") as find:
            self.app.on_import_find()
        find.assert_not_called()
        with patch("urllib.request.urlopen") as network:
            with self.assertRaisesRegex(ValueError, "online search is disabled"):
                A.find_pack_chapters("missing-local-file.zip", lambda _: None, allow_remote=False)
        network.assert_not_called()

    def test_open_progression_survives_actual_gui_option_pipeline(self):
        from moddb.test_offline_story import scan_for
        items = ["demo:machine%d" % i for i in range(8)]
        scan = scan_for(items, {items[i]: [("minecraft:crafting_shaped", {items[i - 1]})]
                                for i in range(1, len(items))})
        self.app.scan_result = scan
        self.app.selected_ids = ["demo"]
        self.app.progression.set("open")
        self.app.opt_chain.set(True)
        self.app.opt_vanilla_ch.set(False)
        popts, bopts = self.app._prompt_opts(), self.app._opts()
        self.assertEqual("open", bopts["progression"])
        doc = A.clean_doc(A.local_quest_doc(scan, ["demo"], popts), scan, lambda _: None)
        chapters, _, _ = A.build_chapters(doc, lambda _: None, bopts)
        self.assertTrue(chapters)
        self.assertTrue(all(not q.get("dependencies") for _, ch in chapters for q in ch["quests"]))

    def test_content_specific_toggles_only_enable_when_effective(self):
        self.assertEqual("disabled", str(self.app._decor_mods_toggle.cget("state")))
        self.app.scan_result = {
            "mods": [{"mod_id": "decorfixture", "name": "Decor Fixture", "category": "decor"}],
            "items": {"decorfixture": {"decorfixture:chair"}}, "craftable": {}}
        self.app.selected_ids = []
        self.app._update_scan_lbl()
        self.assertEqual("normal", str(self.app._decor_mods_toggle.cget("state")))
        self.app.selected_ids = ["decorfixture"]
        self.app._update_scan_lbl()
        self.assertEqual("disabled", str(self.app._decor_mods_toggle.cget("state")))
        self.assertIn("already included", self.app._decor_mods_hint.cget("text"))
        self.app.scan_result["mods"][0]["category"] = "tech"
        self.app._update_scan_lbl()
        self.assertIn("No eligible", self.app._decor_mods_hint.cget("text"))
        self.assertEqual("disabled", str(self.app._themes_only_toggle.cget("state")))
        self.app.themes_box.insert("1.0", "Farming")
        self.app._on_themes_changed()
        self.assertEqual("normal", str(self.app._themes_only_toggle.cget("state")))
        self.app.themes_box.delete("1.0", "end")
        self.app._on_themes_changed()
        self.assertEqual("disabled", str(self.app._themes_only_toggle.cget("state")))

    def test_epic_preset_applies_effective_options_and_available_references(self):
        self.app.target_count.set("12")
        self.app.run_seed.set("123")
        self.app.opt_groups.set(False)
        self.app.opt_style_chapters.set(False)
        self.app.opt_decor.set(False)
        self.app.opt_vanilla_ch.set(False)
        self.app.opt_themes_only.set(True)
        self.app.btn_epic_preset.invoke()
        for variable, expected in (
                (self.app.density, "massive"), (self.app.aesthetic, "lavish"),
                (self.app.layout, "reference"), (self.app.quest_shape, "auto"),
                (self.app.progression, "linear"), (self.app.run_seed, "auto"),
                (self.app.target_count, ""), (self.app.group_style, "bold")):
            self.assertEqual(expected, variable.get())
        for variable in (self.app.opt_groups, self.app.opt_style_chapters,
                         self.app.opt_decor, self.app.opt_vanilla_ch, self.app.opt_campaign):
            self.assertTrue(variable.get())
        self.assertFalse(self.app.opt_themes_only.get())
        self.assertTrue(self.app.opt_mimic.get(), "Installed quest books remain available without bundled templates")
        with patch.object(self.app, "_templates_available", True):
            self.app.btn_epic_preset.invoke()
            self.assertTrue(self.app.opt_mimic.get())
        saved = json.loads(A.CONFIG_PATH.read_text())
        self.assertTrue(saved["opt_campaign"])
        self.assertEqual("reference", saved["layout"])

    def test_campaign_toggle_preserves_goal_and_passes_options(self):
        goal = "  master Botania  "
        self.app.campaign_goal.set(goal)
        self.assertEqual("", self.app._goal_fields.winfo_manager())
        self.app.opt_campaign.set(True)
        self.assertEqual("pack", self.app._goal_fields.winfo_manager())
        self.assertEqual(goal, self.app._goal_entry.get())
        opts = self.app._prompt_opts()
        self.assertTrue(opts["campaign"])
        self.assertEqual(goal.strip(), opts["campaign_goal"])
        self.app.opt_campaign.set(False)
        self.assertEqual("", self.app._goal_fields.winfo_manager())
        self.assertEqual(goal, self.app.campaign_goal.get())
        self.assertFalse(self.app._prompt_opts()["campaign"])
        self.app.opt_campaign.set(True)
        self.assertEqual(goal, self.app._goal_entry.get())

    def test_presets_remain_tunable_and_balanced_preserves_goal_text(self):
        self.app.campaign_goal.set("craft a beacon")
        self.app.btn_epic_preset.invoke()
        self.app.density.set("small")
        self.app.layout.set("matrix")
        self.app.progression.set("open")
        self.app.aesthetic.set("minimal")
        self.app.opt_campaign.set(False)
        opts = self.app._prompt_opts()
        self.assertEqual(("small", "matrix", "open", "minimal", False),
                         tuple(opts[k] for k in ("density", "layout", "progression", "aesthetic", "campaign")))
        self.app._apply_book_preset("balanced")
        self.assertEqual("large", self.app.density.get())
        self.assertEqual("lanes", self.app.layout.get())
        self.assertEqual("balanced", self.app.aesthetic.get())
        self.assertFalse(self.app.opt_campaign.get())
        self.assertEqual("craft a beacon", self.app.campaign_goal.get())
        self.app.target_count.set("77")
        self.app.reward_level.set("lean")
        self.assertEqual("77", self.app._prompt_opts()["target"])
        self.assertEqual("lean", self.app._prompt_opts()["reward"])

    def test_known_campaign_goal_feedback_and_open_progression_explanation(self):
        from moddb.test_offline_story import scan_for
        self.app.scan_result = scan_for(["botania:mana_pool", "minecraft:beacon"])
        self.app.selected_ids = ["botania"]
        self.app._update_scan_lbl()
        self.app.opt_campaign.set(True)
        self.app.campaign_goal.set("craft a beacon")
        self.app._describe_campaign_goal()
        self.assertEqual("", self.app._campaign_error)
        self.assertIn("Beacon", self.app._goal_feedback.cget("text"))
        self.assertIn("final objective", self.app._goal_feedback.cget("text"))
        self.assertEqual("normal", str(self.app.btn_preview.cget("state")))
        self.app.campaign_goal.set("master Botania")
        self.app.progression.set("open")
        self.app._describe_campaign_goal()
        self.assertIn("Botania", self.app._goal_feedback.cget("text"))
        self.assertIn("Open exploration removes unlock requirements", self.app._goal_feedback.cget("text"))
        self.assertIn("master Botania", self.app._review_summary.cget("text"))

    def test_unknown_campaign_goal_blocks_build_and_recovers_when_corrected(self):
        from moddb.test_offline_story import scan_for
        self.app.scan_result = scan_for(["botania:mana_pool"])
        self.app.selected_ids = ["botania"]
        self.app._update_scan_lbl()
        self.app.opt_campaign.set(True)
        for goal in ("race purple comets", "minecraft:not_an_installed_item"):
            with self.subTest(goal=goal):
                self.app.campaign_goal.set(goal)
                self.app._describe_campaign_goal()
                self.assertTrue(self.app._campaign_error)
                self.assertEqual(self.app._campaign_error, self.app._goal_feedback.cget("text"))
                self.assertIn("Check your campaign goal", self.app._review_hint.cget("text"))
                for button in (self.app.btn_preview, self.app.btn_gen_local):
                    self.assertEqual("disabled", str(button.cget("state")))
                    with patch.object(self.app, "_run") as run:
                        button.invoke()
                    run.assert_not_called()
                self.assertEqual("normal", str(self.app.btn_continue_pack.cget("state")))
                with self.assertRaises(ValueError):
                    A.local_quest_doc(self.app.scan_result, self.app.selected_ids, self.app._prompt_opts())
        self.app.opt_campaign.set(False)
        self.assertEqual("normal", str(self.app.btn_preview.cget("state")))
        self.app.campaign_goal.set("master Botania")
        self.app.opt_campaign.set(True)
        self.app._describe_campaign_goal()
        self.assertEqual("", self.app._campaign_error)
        self.assertEqual("normal", str(self.app.btn_preview.cget("state")))
        self.assertEqual("normal", str(self.app.btn_gen_local.cget("state")))


    def test_scenario_undo_restores_mode_start_and_vanilla_permission(self):
        from moddb.test_scenarios import restoration_scan
        self.app.scan_result = restoration_scan()
        self.app.selected_ids = ["botania"]
        self.app._show_scenario_editor()
        editor = self.app._scenario_editor
        editor.suggest()
        editor.mode.set("authored")
        editor.vanilla.set(False)
        original_start = editor.start.get()
        original_ids = [row["id"] for row in editor.stages]
        editor.remove()
        editor.mode.set("assisted")
        editor.vanilla.set(True)
        editor.start.set(next(label for label, mid in editor.mod_choices.items() if mid == "minecraft"))
        editor.undo()
        restored = editor.collect()
        self.assertEqual(restored["mode"], "authored")
        self.assertFalse(restored["allow_vanilla"])
        self.assertEqual(editor.start.get(), original_start)
        self.assertEqual([row["id"] for row in restored["stages"]], original_ids)
        editor.top.destroy()

    def test_malformed_scenario_draft_recovers_without_opening_invalid_rows(self):
        from moddb.test_scenarios import restoration_scan
        self.app.scan_result = restoration_scan()
        self.app.selected_ids = ["botania"]
        for broken in ({"title": "Keep this author work", "stages": [{"id": "duplicate"}, {"id": "duplicate"}]},
                       {"title": "Bad checkbox", "stages": [], "allow_vanilla": "not-a-bool"},
                       {"title": "Bad notes", "stages": [], "notes": 42}):
            with self.subTest(broken=broken):
                self.app._scenario_draft = broken
                self.app._show_scenario_editor()
                editor = self.app._scenario_editor
                self.assertEqual(editor.stages, [])
                self.assertEqual(self.app.cfg["scenario_recovery"], broken)
                self.assertIn("retained", editor.feedback.cget("text"))
                self.assertFalse(editor.check(show_review=False)["valid"])
                self.assertIsNone(self.app._scenario_plan)
                editor.save_draft()
                self.assertEqual(json.loads(A.CONFIG_PATH.read_text())["scenario_recovery"], broken)
                editor.top.destroy()

    def test_fixed_scenario_disables_irrelevant_book_controls_and_clear_restores_them(self):
        from moddb.test_scenarios import restoration_scan
        from quest_scenarios import propose_scenario
        self.app.scan_result = restoration_scan()
        self.app.selected_ids = ["botania"]
        self.app.density.set("large")
        self.app.target_count.set("123")
        self.app._scenario_plan = propose_scenario(self.app.scan_result, ["botania"], "Restore a barren world", allow_vanilla=True)
        self.app.opt_campaign.set(True)
        self.app._sync_campaign_controls()
        controls = [self.app._density_row.combo, self.app._target_entry, self.app._vanilla_ch_toggle,
                    self.app._reference_toggle, self.app.themes_box]
        self.assertTrue(all(str(widget.cget("state")) == "disabled" for widget in controls))
        self.app._clear_scenario()
        self.assertEqual(str(self.app._density_row.combo.cget("state")), "readonly")
        self.assertTrue(all(str(widget.cget("state")) == "normal" for widget in controls[1:]))
        self.assertEqual(self.app.density.get(), "large")
        self.assertEqual(self.app.target_count.get(), "123")


def audit_offline_controls():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OfflineUiTests)
    result = unittest.TestResult()
    suite.run(result)
    return ["%s: %s" % (test.id().split(".")[-1], detail.splitlines()[-1])
            for test, detail in result.failures + result.errors]


if __name__ == "__main__":
    unittest.main()
