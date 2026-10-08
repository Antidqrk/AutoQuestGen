"""Preview fidelity, safe local HTML, and multi-pack UI state regressions."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import autoquestgen as A
from quest_preview import write_preview


class PreviewTests(unittest.TestCase):
    def test_book_settings_clear_old_title_icon_and_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            chapters = [("a", {"id": "A", "quests": []})]
            A.write_snbt(out, chapters, [{"id": "1", "title": "Old Group"}], False,
                         lambda *_: None, {"title": "Old Book", "icon": "minecraft:diamond"})
            A.write_snbt(out, chapters, [], True, lambda *_: None,
                         {"title": "", "icon": "", "progression_mode": "flexible"})
            A.write_snbt(out, chapters, [], True, lambda *_: None,
                         {"title": "New Book", "icon": "minecraft:book"})
            data = A.snbt_loads((out / 'data.snbt').read_text())
            groups = A.snbt_loads((out / 'chapter_groups.snbt').read_text())
            self.assertEqual(data['title'], 'New Book')
            self.assertEqual(data['icon'], 'minecraft:book')
            self.assertEqual(data['progression_mode'], 'flexible')
            self.assertEqual(groups['chapter_groups'], [])
            self.assertEqual(len(list(out.glob('chapters.backup-*'))), 2)

    def test_custom_book_icon_must_exist_in_current_pack(self):
        scan = {"vanilla": {"minecraft:book"}, "items": {"demo": {"demo:real"}}}
        self.assertEqual(A.validate_book_settings({"icon": "demo:real"}, scan)["icon"], "demo:real")
        with self.assertRaisesRegex(ValueError, "not an item"):
            A.validate_book_settings({"icon": "demo:fake"}, scan)

    def test_roundtrip_exact_exported_graph_and_escaped_content(self):
        chapters = [("test", {"title": "A </script><script>bad</script>", "quests": [
            {"id": "one", "x": 2.25, "y": -7.5, "shape": "hexagon", "tasks": []},
            {"id": "two", "dependencies": ["one"], "description": ["<&>"], "tasks": []}]})]
        with tempfile.TemporaryDirectory() as tmp:
            text = write_preview(chapters, Path(tmp) / "nested" / "preview.html").read_text(encoding="utf-8")
        data = re.search(r'type="application/json">(.*?)</script>', text, re.S).group(1)
        self.assertEqual(json.loads(data)["chapters"], [chapters[0][1]])
        self.assertNotIn("<script>bad", text)
        self.assertNotRegex(text, r'(?:src|href)=["\']https?://')
        self.assertNotIn("fetch(", text)

    def test_switching_pack_discards_previous_scan_and_selection(self):
        app = Mock()
        app.scan_result = {"items": {"wrong_pack": ["wrong_pack:item"]}}
        app.selected_ids = ["wrong_pack"]
        app._offline_doc = {"chapters": []}
        A.App._on_mods_folder_changed(app)
        self.assertIsNone(app.scan_result)
        self.assertEqual(app.selected_ids, [])
        self.assertIsNone(app._offline_doc)
        app._update_scan_lbl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
