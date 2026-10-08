"""Offline artwork validation and isolated resource-pack installation checks."""
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

import decor_library as D


def png(width=4, height=3):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    pixels = b"".join(b"\0" + bytes([110, 200, 150, 200]) * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


class DecorLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "decor_assets"
        self.path = self.root / "assets/autoquestgen/textures/botanical/vines.png"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(png())
        self.entry = {"id": "botanical_vines", "theme": "botanical", "kind": "frame",
                      "texture": "autoquestgen:textures/botanical/vines.png",
                      "path": "assets/autoquestgen/textures/botanical/vines.png",
                      "title": "Living vines", "keywords": ["mana"], "alpha": 180}
        self.write_manifest([self.entry])

    def write_manifest(self, entries):
        (self.root / "manifest.json").write_text(json.dumps({"schema": 1, "assets": entries}), encoding="utf-8")

    def test_catalog_infers_verified_metadata_and_resolves_source_or_frozen_root(self):
        catalog = D.load_catalog(self.root)
        asset = catalog.get("botanical_vines")
        self.assertEqual((asset.width, asset.height), (4, 3))
        self.assertEqual(asset.sha256, hashlib.sha256(png()).hexdigest())
        self.assertEqual(catalog.by_theme("botanical", "frame"), (asset,))
        self.assertEqual(catalog.get(asset.texture), asset)
        with patch.object(D.sys, "_MEIPASS", str(self.root.parent), create=True):
            self.assertEqual(D.load_catalog().assets, catalog.assets)
        self.assertTrue(D.texture_data_uri(asset.texture, catalog).startswith("data:image/png;base64,"))
        self.assertIsNone(D.texture_data_uri("minecraft:textures/block/stone.png", catalog))

    def test_invalid_or_escaping_texture_paths_are_rejected(self):
        for value in ("https://example.org/image.png", "autoquestgen:textures/../../image.png",
                      "autoquestgen:textures/../image.png", "autoquestgen:textures/C:\\image.png"):
            with self.subTest(value=value):
                self.write_manifest([dict(self.entry, texture=value)])
                with self.assertRaises(D.DecorLibraryError):
                    D.load_catalog(self.root)
        self.write_manifest([dict(self.entry, path="../outside.png")])
        with self.assertRaises(D.DecorLibraryError):
            D.load_catalog(self.root)

    def test_corrupt_pixels_wrong_manifest_hash_dimensions_and_duplicates_fail(self):
        for overrides in ({"width": 5}, {"sha256": "0" * 64}):
            self.write_manifest([dict(self.entry, **overrides)])
            with self.assertRaises(D.DecorLibraryError):
                D.load_catalog(self.root)
        self.write_manifest([self.entry, self.entry])
        with self.assertRaises(D.DecorLibraryError):
            D.load_catalog(self.root)
        self.write_manifest([self.entry])
        corrupt = bytearray(png())
        corrupt[-18] ^= 1
        self.path.write_bytes(corrupt)
        with self.assertRaises(D.DecorLibraryError):
            D.load_catalog(self.root)

    def test_changed_asset_cannot_bypass_loaded_catalog_validation(self):
        catalog = D.load_catalog(self.root)
        self.path.write_bytes(png(5, 3))
        with self.assertRaises(D.DecorLibraryError):
            D.resource_pack_bytes(catalog=catalog)

    def test_resource_pack_is_deterministic_complete_and_version_specific(self):
        catalog = D.load_catalog(self.root)
        for version, fmt in (("1.20.1", 15), ("1.20.4", 22), ("1.21.1", 34)):
            data = D.resource_pack_bytes(version, catalog=catalog)
            self.assertEqual(data, D.resource_pack_bytes(version, catalog=catalog))
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(json.loads(archive.read("pack.mcmeta"))["pack"]["pack_format"], fmt)
                self.assertEqual(archive.read(self.entry["path"]), png())
                self.assertIsNone(archive.testzip())
                self.assertEqual(set(archive.namelist()), {"pack.mcmeta", "README.txt", self.entry["path"]})
        with self.assertRaises(D.DecorLibraryError):
            D.resource_pack_bytes("999.0", catalog=catalog)

    def test_own_unknown_references_fail_while_stock_textures_are_left_alone(self):
        catalog = D.load_catalog(self.root)
        self.assertEqual(len(catalog.validate_references([self.entry["texture"], "ftbquests:textures/shapes/gear/outline.png"])), 1)
        with self.assertRaises(D.DecorLibraryError):
            catalog.validate_references(["autoquestgen:textures/missing.png"])

    def test_install_is_explicit_preserves_options_and_user_packs_and_is_idempotent(self):
        catalog = D.load_catalog(self.root)
        instance = self.root.parent / "instance"
        instance.mkdir()
        options = instance / "options.txt"
        original = b'lang:en_us\r\nresourcePacks:["vanilla","file/My Art.zip"]\r\nfov:0.0\r\n'
        options.write_bytes(original)
        user_pack = instance / "resourcepacks" / "My Art.zip"
        user_pack.parent.mkdir()
        user_pack.write_bytes(b"unchanged user artwork")
        first = D.install_resource_pack(instance, "1.20.1", catalog=catalog)
        self.assertFalse(first["enabled"])
        self.assertEqual(options.read_bytes(), original)
        enabled = D.install_resource_pack(instance, "1.20.1", enable=True, catalog=catalog)
        expected = original.replace(b'"file/My Art.zip"]', b'"file/My Art.zip","file/AutoQuestGen-Art.zip"]')
        self.assertEqual(options.read_bytes(), expected)
        self.assertEqual(user_pack.read_bytes(), b"unchanged user artwork")
        self.assertEqual(len(enabled["backups"]), 1)
        self.assertEqual(Path(enabled["backups"][0]).read_bytes(), original)
        repeated = D.install_resource_pack(instance, "1.20.1", enable=True, catalog=catalog)
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["backups"], [])

    def test_malformed_options_are_preserved_before_any_install_write(self):
        instance = self.root.parent / "instance"
        instance.mkdir()
        options = instance / "options.txt"
        options.write_text("resourcePacks:malformed\n", encoding="utf-8")
        with self.assertRaises(D.DecorLibraryError):
            D.validate_installation(instance, enable=True)
        with self.assertRaises(D.DecorLibraryError):
            D.install_resource_pack(instance, enable=True, catalog=D.load_catalog(self.root))
        self.assertEqual(options.read_text(), "resourcePacks:malformed\n")
        self.assertFalse((instance / "resourcepacks").exists())

    def test_enable_preserves_utf8_bom_and_existing_pack_order(self):
        original = b'\xef\xbb\xbfresourcePacks:["vanilla","file/other.zip"]\nlang:en_us'
        updated = D._enabled_options(original)
        self.assertEqual(updated, original.replace(b'"file/other.zip"]', b'"file/other.zip","file/AutoQuestGen-Art.zip"]'))
        self.assertEqual(D._enabled_options(updated), updated)

    def test_replacing_old_generated_pack_backs_it_up(self):
        dest = self.root.parent / D.PACK_FILENAME
        dest.write_bytes(b"older generated art")
        D.export_resource_pack(dest, catalog=D.load_catalog(self.root))
        backups = list(dest.parent.glob(D.PACK_FILENAME + ".autoquestgen-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"older generated art")

    def test_themes_are_stable_and_follow_subject(self):
        for text, expected in (("Botania Gaia", "botanical"), ("Applied Energistics storage", "storage"),
                               ("Nether blaze lava", "nether"), ("Restore the sanctuary", "restoration")):
            self.assertEqual(D.choose_theme(text), expected)
        self.assertEqual(D.choose_theme("Unknown title"), "exploration")


if __name__ == "__main__":
    unittest.main()
