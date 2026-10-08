"""Real catalog and common quest-writer artwork integration, in temporary dirs."""
from collections import Counter
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import autoquestgen as A
import decor_library as D
import quest_decor as Q


class DecorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = D.load_catalog()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        doc = {"chapters": [{"id": "botanical", "title": "Botanical sanctuary", "quests": [
            {"id": "start", "title": "Gather stone", "tasks": [{"type": "item", "item": "minecraft:stone"}]},
            {"id": "tool", "title": "A useful pickaxe", "dependencies": ["start"],
             "tasks": [{"type": "item", "item": "minecraft:iron_pickaxe"}]}]}]}
        opts = {"seed": 17, "layout": "reference", "aesthetic": "lavish", "decor_art": True,
                "decor_source": "library", "decor_theme": "botanical", "_decor_catalog": self.catalog,
                "style_chapters": True, "quest_size_style": "varied", "auto_chain": False}
        self.chapters, self.groups, self.extra = A.build_chapters(doc, lambda *_: None, opts)

    def write(self, target, *, version="1.20.1", chapters=None):
        A.write_snbt(target, self.chapters if chapters is None else chapters, self.groups,
                     True, lambda *_: None, mc_version=version, allow_while_running=True)

    def existing_book(self, target):
        (target / "chapters").mkdir(parents=True)
        (target / "chapters/old.snbt").write_bytes(b'{id:"OLD_BOOK_PRESERVED"}\n')
        (target / "chapter_groups.snbt").write_bytes(b'{chapter_groups:[]}\n')
        (target / "data.snbt").write_bytes(b'{title:"Existing player book"}\n')
        return self.snapshot(target)

    @staticmethod
    def snapshot(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_original_library_coverage_survives_catalog_expansion_and_all_assets_validate(self):
        self.assertGreaterEqual(len(self.catalog.assets), 72)
        original_kinds = {"background", "frame", "ornament", "emblem", "divider", "panel"}
        for theme in D.THEMES:
            self.assertTrue(original_kinds <= {a.kind for a in self.catalog.by_theme(theme)})
        self.assertEqual(len({a.sha256 for a in self.catalog.assets}), len(self.catalog.assets))
        for asset in self.catalog.assets:
            self.assertEqual(D._png_dimensions(self.catalog.read(asset)), (asset.width, asset.height))

    def test_scratch_common_writer_ships_every_asset_and_valid_refs_for_both_game_versions(self):
        for version, fmt in (("1.20.1", 15), ("1.21.1", 34)):
            with self.subTest(version=version):
                target = self.root / version / "quests"
                prepared = Q.prepare_art_export(target, self.chapters, version)
                self.assertIsNone(prepared[1])
                self.assertFalse(target.exists())
                self.write(target, version=version)
                pack = target / "resourcepacks" / D.PACK_FILENAME
                with zipfile.ZipFile(pack) as archive:
                    paths = {name for name in archive.namelist() if name.endswith(".png")}
                    self.assertEqual(paths, {asset.path for asset in self.catalog.assets})
                    self.assertIsNone(archive.testzip())
                    self.assertEqual(json.loads(archive.read("pack.mcmeta"))["pack"]["pack_format"], fmt)
                    for path in (target / "chapters").glob("*.snbt"):
                        chapter = A.snbt_loads(path.read_text(encoding="utf-8"))
                        refs = [image["image"] for image in chapter["images"]]
                        self.assertGreater(len(refs), 5)
                        for asset in self.catalog.validate_references(refs):
                            self.assertEqual(archive.read(asset.path), self.catalog.read(asset))
                instructions = (target / "resourcepacks/Artwork instructions.txt").read_text(encoding="utf-8")
                self.assertIn("enable AutoQuestGen-Art", instructions)
                self.assertFalse((target.parent / "options.txt").exists())

    def test_exact_instance_install_enables_only_own_pack_and_preserves_player_options(self):
        instance = self.root / "instance"
        target = instance / "config/ftbquests/quests"
        self.existing_book(target)
        original_options = b'lang:en_us\r\nresourcePacks:["vanilla","file/Player Art.zip"]\r\nfov:0.25\r\n'
        (instance / "options.txt").write_bytes(original_options)
        user_pack = instance / "resourcepacks/Player Art.zip"
        user_pack.parent.mkdir()
        user_pack.write_bytes(b"Player-owned resource pack")
        before = self.snapshot(instance)
        prepared = Q.prepare_art_export(target, self.chapters, "1.21.1")
        self.assertEqual(prepared[1], instance)
        self.assertEqual(self.snapshot(instance), before, "Preflight must be read-only")
        self.write(target, version="1.21.1")
        expected = original_options.replace(b'"file/Player Art.zip"]', b'"file/Player Art.zip","file/AutoQuestGen-Art.zip"]')
        self.assertEqual((instance / "options.txt").read_bytes(), expected)
        self.assertEqual(user_pack.read_bytes(), b"Player-owned resource pack")
        backups = list(instance.glob("options.txt.autoquestgen-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original_options)
        with zipfile.ZipFile(instance / "resourcepacks" / D.PACK_FILENAME) as archive:
            self.assertEqual(json.loads(archive.read("pack.mcmeta"))["pack"]["pack_format"], 34)
        self.assertFalse((target / "resourcepacks").exists())

    def test_malformed_player_options_reject_before_replacing_any_book_files(self):
        instance = self.root / "instance"
        target = instance / "config/ftbquests/quests"
        self.existing_book(target)
        (instance / "options.txt").write_bytes(b"resourcePacks:not JSON\n")
        before = self.snapshot(instance)
        with self.assertRaises(D.DecorLibraryError):
            self.write(target)
        self.assertEqual(self.snapshot(instance), before)

    def test_missing_asset_rejects_before_replacing_any_book_files(self):
        target = self.root / "export"
        before = self.existing_book(target)
        missing_root = self.root / "missing_art"
        missing_root.mkdir()
        (missing_root / "manifest.json").write_bytes((self.catalog.root / "manifest.json").read_bytes())
        with patch.object(Q, "load_catalog", side_effect=lambda: D.load_catalog(missing_root)):
            with self.assertRaises(D.DecorLibraryError):
                self.write(target)
        self.assertEqual(self.snapshot(target), before)

    def test_unknown_custom_ref_and_unknown_game_version_preserve_existing_book(self):
        target = self.root / "export"
        before = self.existing_book(target)
        invalid = copy.deepcopy(self.chapters)
        invalid[0][1]["images"][0]["image"] = "autoquestgen:textures/missing.png"
        with self.assertRaises(D.DecorLibraryError):
            self.write(target, chapters=invalid)
        self.assertEqual(self.snapshot(target), before)
        with self.assertRaises(D.DecorLibraryError):
            self.write(target, version="999.0")
        self.assertEqual(self.snapshot(target), before)

    def test_similarly_named_export_folder_is_not_treated_as_a_live_instance(self):
        target = self.root / "config/exports/ftbquests/quests"
        prepared = Q.prepare_art_export(target, self.chapters)
        self.assertIsNone(prepared[1])
        self.write(target)
        self.assertTrue((target / "resourcepacks" / D.PACK_FILENAME).is_file())
        self.assertFalse((self.root / "config/options.txt").exists())

    def test_stock_only_book_does_not_need_or_install_bundled_art(self):
        chapters = copy.deepcopy(self.chapters)
        chapters[0][1]["images"] = [{"image": "ftbquests:textures/shapes/gear/outline.png"}]
        target = self.root / "export"
        with patch.object(Q, "load_catalog", side_effect=AssertionError("Stock art does not need catalog")):
            self.assertIsNone(Q.prepare_art_export(target, chapters))
            self.write(target, chapters=chapters)
        self.assertFalse((target / "resourcepacks").exists())

    def saved_book_fixture(self):
        source = self.root / "original_export"
        (source / "chapters").mkdir(parents=True)
        for slug, chapter in self.chapters:
            (source / "chapters" / (slug + ".snbt")).write_text(A.snbt_dumps(chapter), encoding="utf-8")
        (source / "data.snbt").write_text(A.DATA_SNBT_DEFAULT, encoding="utf-8")
        (source / "chapter_groups.snbt").write_text(A.CHAPTER_GROUPS_EMPTY, encoding="utf-8")
        library = self.root / "library"
        with patch.object(A, "LIBRARY_DIR", library):
            saved = A.save_book_to_library(source, "Saved botanical book")
        self.assertFalse((saved / "resourcepacks").exists(), "Restore must rebuild the artwork from the bundled catalog")
        return library, saved

    def test_library_restore_into_another_instance_installs_all_art_for_target_version(self):
        library, saved = self.saved_book_fixture()
        saved_before = self.snapshot(saved)
        instance = self.root / "another_instance"
        target = instance / "config/ftbquests/quests"
        old_book = self.existing_book(target)
        (instance / "instance.json").write_text(json.dumps({"minecraftVersion": "1.21.1"}), encoding="utf-8")
        original_options = b'lang:en_us\r\nresourcePacks:["vanilla","file/Player Art.zip"]\r\nfov:0.25\r\n'
        (instance / "options.txt").write_bytes(original_options)
        user_pack = instance / "resourcepacks/Player Art.zip"
        user_pack.parent.mkdir()
        user_pack.write_bytes(b"Player-owned artwork remains intact")
        with patch.object(A, "LIBRARY_DIR", library), patch.object(A, "minecraft_running", return_value=False):
            A.restore_book_from_library(saved.name, target)
        self.assertEqual(user_pack.read_bytes(), b"Player-owned artwork remains intact")
        expected_options = original_options.replace(b'"file/Player Art.zip"]', b'"file/Player Art.zip","file/AutoQuestGen-Art.zip"]')
        self.assertEqual((instance / "options.txt").read_bytes(), expected_options)
        options_backups = list(instance.glob("options.txt.autoquestgen-backup-*"))
        self.assertEqual(len(options_backups), 1)
        self.assertEqual(options_backups[0].read_bytes(), original_options)
        chapter_backups = list(target.glob("chapters.backup-*"))
        self.assertEqual(len(chapter_backups), 1)
        self.assertEqual((chapter_backups[0] / "old.snbt").read_bytes(), old_book["chapters/old.snbt"])
        for relative, content in saved_before.items():
            self.assertEqual((target / relative).read_bytes(), content)
        self.assertEqual(self.snapshot(saved), saved_before)
        with zipfile.ZipFile(instance / "resourcepacks" / D.PACK_FILENAME) as archive:
            self.assertEqual(json.loads(archive.read("pack.mcmeta"))["pack"]["pack_format"], 34)
            self.assertEqual({n for n in archive.namelist() if n.endswith(".png")}, {a.path for a in self.catalog.assets})
            self.assertIsNone(archive.testzip())
            for _, chapter in self.chapters:
                for image in chapter["images"]:
                    asset = self.catalog.get(image["image"])
                    self.assertIsNotNone(asset)
                    self.assertEqual(archive.read(asset.path), self.catalog.read(asset))

    def test_library_restore_rejects_malformed_options_before_overwriting_old_book(self):
        library, saved = self.saved_book_fixture()
        instance = self.root / "another_instance"
        target = instance / "config/ftbquests/quests"
        self.existing_book(target)
        (instance / "instance.json").write_text(json.dumps({"minecraftVersion": "1.21.1"}), encoding="utf-8")
        (instance / "options.txt").write_bytes(b"resourcePacks:not JSON\n")
        before = self.snapshot(instance)
        with patch.object(A, "LIBRARY_DIR", library), patch.object(A, "minecraft_running", return_value=False):
            with self.assertRaises(D.DecorLibraryError):
                A.restore_book_from_library(saved.name, target)
        self.assertEqual(self.snapshot(instance), before)


if __name__ == "__main__":
    unittest.main()
