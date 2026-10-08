"""FTB atlas animation contract, with bounded original PNG fixtures."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import decor_library as D
from moddb.test_decor_library import png


class DecorAnimationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.entry = {"id": "space_galaxy", "theme": "space", "kind": "animation",
                      "title": "Spiral galaxy", "texture": "autoquestgen:block/space/galaxy",
                      "path": "assets/autoquestgen/textures/block/space/galaxy.png",
                      "width": 8, "height": 6,
                      "animation": {"frame_width": 4, "frame_height": 3, "frames": 4,
                                    "frametime": 2, "interpolate": False}}
        path = self.root / self.entry["path"]
        path.parent.mkdir(parents=True)
        path.write_bytes(png(8, 6))
        self.manifest()

    def manifest(self, entry=None):
        (self.root / "manifest.json").write_text(json.dumps({"schema": 1, "assets": [entry or self.entry]}), encoding="utf-8")

    def test_grid_sheet_uses_native_extensionless_atlas_ref_and_frame_aspect(self):
        catalog = D.load_catalog(self.root)
        asset = catalog.assets[0]
        self.assertTrue(asset.animated)
        self.assertEqual((asset.width, asset.height), (8, 6))
        self.assertEqual((asset.display_width, asset.display_height), (4, 3))
        self.assertEqual(asset.texture, "autoquestgen:block/space/galaxy")
        self.assertFalse(asset.texture.endswith(".png"), "FTB would select static SimpleTexture")
        self.assertEqual(catalog.get(asset.texture), asset)
        self.assertEqual(catalog.validate_references([asset.texture]), (asset,))

    def test_resource_pack_contains_sheet_and_exact_row_major_animation_metadata(self):
        catalog = D.load_catalog(self.root)
        for version, pack_format in (("1.20.1", 15), ("1.21.1", 34)):
            data = D.resource_pack_bytes(version, catalog=catalog)
            self.assertEqual(data, D.resource_pack_bytes(version, catalog=catalog))
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(archive.read(self.entry["path"]), png(8, 6))
                metadata = json.loads(archive.read(self.entry["path"] + ".mcmeta"))
                self.assertEqual(metadata, {"animation": {"width": 4, "height": 3, "frames": [0, 1, 2, 3],
                                                          "frametime": 2, "interpolate": False}})
                self.assertEqual(json.loads(archive.read("pack.mcmeta"))["pack"]["pack_format"], pack_format)
                self.assertFalse(any(name.startswith("assets/minecraft/") for name in archive.namelist()),
                                 "Stock block-directory atlas should load the namespace without overrides")
                self.assertIsNone(archive.testzip())

    def test_animation_must_not_accidentally_use_a_static_png_reference(self):
        self.manifest(dict(self.entry, texture="autoquestgen:textures/block/space/galaxy.png"))
        with self.assertRaises(D.DecorLibraryError):
            D.load_catalog(self.root)
        self.manifest(dict(self.entry, kind="landmark"))
        with self.assertRaises(D.DecorLibraryError):
            D.load_catalog(self.root)

    def test_invalid_grid_timing_and_unimplemented_playback_fields_are_rejected(self):
        for change in ({"frames": 3}, {"frames": 1}, {"frame_width": 3}, {"frame_height": 0},
                       {"frametime": 0}, {"frametime": True}, {"interpolate": "true"},
                       {"script": "not executable"}):
            with self.subTest(change=change):
                self.manifest(dict(self.entry, animation=dict(self.entry["animation"], **change)))
                with self.assertRaises(D.DecorLibraryError):
                    D.load_catalog(self.root)

    def test_animation_metadata_cannot_be_mutated_after_validation_into_invalid_export(self):
        catalog = D.load_catalog(self.root)
        catalog.assets[0].animation["frames"] = 99
        with self.assertRaises(D.DecorLibraryError):
            D.resource_pack_bytes(catalog=catalog)

    def test_static_landmark_remains_a_png_and_has_no_animation_sidecar(self):
        entry = dict(self.entry, kind="landmark", texture="autoquestgen:textures/block/space/galaxy.png")
        entry.pop("animation")
        self.manifest(entry)
        catalog = D.load_catalog(self.root)
        asset = catalog.assets[0]
        self.assertFalse(asset.animated)
        self.assertEqual((asset.display_width, asset.display_height), (8, 6))
        self.assertIsNone(D.animation_metadata(asset))
        with zipfile.ZipFile(io.BytesIO(D.resource_pack_bytes(catalog=catalog))) as archive:
            self.assertFalse(any(name.endswith(".png.mcmeta") for name in archive.namelist()))


if __name__ == "__main__":
    unittest.main()
