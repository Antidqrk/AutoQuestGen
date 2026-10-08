"""Validated original artwork and offline Minecraft resource-pack exports.

This module never downloads images or modifies a Minecraft instance implicitly.
The caller must explicitly invoke install_resource_pack, and enable=True is
required before the selected-resource-pack option is changed.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import time
import uuid
import zipfile
import zlib

NAMESPACE = "autoquestgen"
PACK_FILENAME = "AutoQuestGen-Art.zip"
THEMES = ("botanical", "arcane", "machinery", "power", "space", "exploration",
          "farming", "ocean", "nether", "end", "storage", "restoration")
KINDS = ("background", "frame", "ornament", "emblem", "divider", "panel", "landmark", "animation")
_RESOURCE = re.compile(r"autoquestgen:textures/[a-z0-9_./-]+\.png\Z")
_SPRITE = re.compile(r"autoquestgen:block/[a-z0-9_/-]+\Z")
_ID = re.compile(r"[a-z0-9_./-]+\Z")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class DecorLibraryError(ValueError):
    """Artwork cannot be used safely or the selected game version is unknown."""


def default_library_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "decor_assets"


def _asset_path(root: Path, texture: str, declared_path: str = "", *, animated: bool = False) -> Path:
    pattern = _SPRITE if animated else _RESOURCE
    if not isinstance(texture, str) or not pattern.fullmatch(texture):
        raise DecorLibraryError("Invalid local artwork texture reference: %r" % texture)
    expected = ("assets/" + texture.replace(":", "/textures/", 1) + ".png" if animated
                else "assets/" + texture.replace(":", "/", 1))
    if any(part in ("", ".", "..") for part in expected.split("/")) or "\\" in expected:
        raise DecorLibraryError("Artwork path escapes the library")
    if declared_path and declared_path != expected:
        raise DecorLibraryError("Artwork path does not match its texture: " + texture)
    candidate = (root / expected).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise DecorLibraryError("Artwork path escapes the library")
    return candidate


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Check PNG framing, checksums and decoded scanline size without Pillow."""
    if not data.startswith(_PNG_SIGNATURE) or len(data) > 16 * 1024 * 1024:
        raise DecorLibraryError("Artwork must be a bounded PNG file")
    pos, dimensions, compressed, ended = 8, None, bytearray(), False
    while pos + 12 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise DecorLibraryError("Truncated artwork PNG")
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack_from(">I", data, pos + 8 + length)[0]
        if zlib.crc32(kind + body) & 0xffffffff != crc:
            raise DecorLibraryError("Artwork PNG checksum failed")
        if kind == b"IHDR":
            if dimensions is not None or pos != 8 or length != 13:
                raise DecorLibraryError("Invalid artwork PNG header")
            w, h, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            if not (1 <= w <= 4096 and 1 <= h <= 4096) or depth != 8 or color not in (0, 2, 4, 6) or any((compression, filtering, interlace)):
                raise DecorLibraryError("Artwork must be a non-interlaced 8-bit PNG up to 4096 pixels")
            dimensions = (w, h)
            channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
        elif kind == b"IDAT":
            compressed.extend(body)
        elif kind == b"IEND":
            ended = length == 0 and end == len(data)
            break
        pos = end
    if dimensions is None or not ended or not compressed:
        raise DecorLibraryError("Incomplete artwork PNG")
    stride = dimensions[0] * channels + 1
    expected = stride * dimensions[1]
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, expected + 1)
        if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise DecorLibraryError("Artwork PNG pixel data has the wrong size")
        if any(raw[index] > 4 for index in range(0, len(raw), stride)):
            raise DecorLibraryError("Artwork PNG uses an invalid scanline filter")
    except zlib.error as exc:
        raise DecorLibraryError("Artwork PNG pixel data is corrupt") from exc
    return dimensions


@dataclass(frozen=True)
class DecorAsset:
    id: str
    theme: str
    kind: str
    texture: str
    title: str
    path: str
    width: int
    height: int
    sha256: str
    keywords: tuple[str, ...] = ()
    alpha: int = 255
    animation: dict | None = None

    @property
    def animated(self) -> bool:
        return self.animation is not None

    @property
    def display_width(self) -> int:
        return self.animation["frame_width"] if self.animation else self.width

    @property
    def display_height(self) -> int:
        return self.animation["frame_height"] if self.animation else self.height


@dataclass(frozen=True)
class DecorCatalog:
    root: Path
    assets: tuple[DecorAsset, ...]

    def by_theme(self, theme: str, kind: str | None = None) -> tuple[DecorAsset, ...]:
        return tuple(a for a in self.assets if a.theme == theme and (kind is None or a.kind == kind))

    def get(self, id_or_texture: str) -> DecorAsset | None:
        return next((a for a in self.assets if id_or_texture in (a.id, a.texture)), None)

    def read(self, asset: DecorAsset | str) -> bytes:
        record = self.get(asset) if isinstance(asset, str) else asset
        if record is None or record not in self.assets:
            raise DecorLibraryError("Artwork is absent from the validated catalog")
        data = _asset_path(self.root, record.texture, record.path, animated=record.animated).read_bytes()
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise DecorLibraryError("Artwork changed after validation: " + record.id)
        return data

    def validate_references(self, textures) -> tuple[DecorAsset, ...]:
        """Validate all custom references, leaving other namespaces to the app."""
        result = []
        for texture in sorted({value for value in textures if isinstance(value, str)}):
            if not texture.startswith(NAMESPACE + ":"):
                continue
            record = self.get(texture)
            if record is None:
                raise DecorLibraryError("Unknown bundled artwork: " + texture)
            result.append(record)
        return tuple(result)


def load_catalog(root: Path | str | None = None) -> DecorCatalog:
    root = Path(root) if root is not None else default_library_root()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise DecorLibraryError("The bundled artwork catalog could not be read") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or not isinstance(manifest.get("assets"), list):
        raise DecorLibraryError("Unsupported artwork catalog schema")
    assets, ids, textures = [], set(), set()
    for entry in manifest["assets"]:
        if not isinstance(entry, dict):
            raise DecorLibraryError("Invalid artwork catalog entry")
        ident, theme, kind = entry.get("id"), entry.get("theme"), entry.get("kind")
        if not isinstance(ident, str) or not _ID.fullmatch(ident) or ident in ids:
            raise DecorLibraryError("Duplicate or invalid artwork ID: %r" % ident)
        if theme not in THEMES or kind not in KINDS:
            raise DecorLibraryError("Invalid artwork theme or kind: " + ident)
        texture = entry.get("texture", "")
        if texture in textures:
            raise DecorLibraryError("Duplicate artwork texture: " + texture)
        animation = entry.get("animation")
        if (animation is not None) != (kind == "animation"):
            raise DecorLibraryError("Animation artwork must declare its playback metadata: " + ident)
        path = _asset_path(root, texture, entry.get("path", ""), animated=animation is not None)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DecorLibraryError("Missing bundled artwork: " + ident) from exc
        width, height = _png_dimensions(data)
        if animation is not None:
            animation = _validate_animation(animation, width, height, ident)
        digest = hashlib.sha256(data).hexdigest()
        if entry.get("width", width) != width or entry.get("height", height) != height:
            raise DecorLibraryError("Artwork dimensions do not match the catalog: " + ident)
        if entry.get("sha256", digest) != digest:
            raise DecorLibraryError("Artwork checksum does not match the catalog: " + ident)
        alpha = entry.get("alpha", 255)
        if not isinstance(alpha, int) or not 0 <= alpha <= 255:
            raise DecorLibraryError("Invalid artwork opacity: " + ident)
        keywords = entry.get("keywords", [])
        if not isinstance(keywords, list) or any(not isinstance(k, str) for k in keywords):
            raise DecorLibraryError("Invalid artwork keywords: " + ident)
        assets.append(DecorAsset(ident, theme, kind, texture, str(entry.get("title", ident)),
                                 path.relative_to(root.resolve()).as_posix(), width, height, digest,
                                 tuple(keywords), alpha, animation))
        ids.add(ident)
        textures.add(texture)
    if not assets:
        raise DecorLibraryError("The artwork catalog is empty")
    return DecorCatalog(root.resolve(), tuple(sorted(assets, key=lambda a: a.id)))


def _validate_animation(animation: dict, width: int, height: int, ident: str) -> dict:
    """Bounded row-major sprite sheets; never interpret executable expressions."""
    keys = {"frame_width", "frame_height", "frames", "frametime", "interpolate"}
    if not isinstance(animation, dict) or set(animation) - keys:
        raise DecorLibraryError("Unsupported animation metadata: " + ident)
    normalized = dict(animation)
    normalized.setdefault("frametime", 2)
    normalized.setdefault("interpolate", False)
    for key in ("frame_width", "frame_height", "frames", "frametime"):
        if type(normalized.get(key)) is not int or normalized[key] <= 0:
            raise DecorLibraryError("Animation " + key + " must be a positive integer: " + ident)
    fw, fh, count = (normalized[k] for k in ("frame_width", "frame_height", "frames"))
    if (fw > 512 or fh > 512 or count < 2 or count > 256 or normalized["frametime"] > 200
            or width % fw or height % fh or width // fw * (height // fh) != count):
        raise DecorLibraryError("Animation frames do not exactly fit a bounded sprite sheet: " + ident)
    if type(normalized["interpolate"]) is not bool:
        raise DecorLibraryError("Animation interpolation must be true or false: " + ident)
    return normalized


def animation_metadata(asset: DecorAsset) -> dict | None:
    """Vanilla .png.mcmeta for the extensionless FTB AtlasSpriteIcon reference.

    Minecraft's stock blocks atlas includes textures/block from every namespace.
    A .png image string uses SimpleTexture instead and would never animate.
    """
    if not asset.animation:
        return None
    animation = _validate_animation(asset.animation, asset.width, asset.height, asset.id)
    return {"animation": {"width": animation["frame_width"], "height": animation["frame_height"],
                          "frametime": animation["frametime"], "interpolate": animation["interpolate"],
                          "frames": list(range(animation["frames"]))}}


_THEME_WORDS = {
    "botanical": "botania botanical flower mana alfheim gaia livingwood petal",
    "arcane": "arcane magic wizard spell ars nouveau blood thaum alchemy occult ritual",
    "machinery": "machinery machine create factory mekanism industrial thermal pneumatic engineering",
    "power": "power energy reactor generators generation flux powah electricity turbine",
    "space": "space rocket astronaut orbit planet ad astra galactic star",
    "exploration": "exploration explore expedition adventure journey dungeon travel compass",
    "farming": "farming farm agriculture harvest crop food cooking bees honey seed orchard",
    "ocean": "ocean aquatic underwater sea fish fishing coral tide water",
    "nether": "nether hell blaze wither lava infernal crimson warped",
    "end": "end ender dragon chorus purpur void draconic",
    "storage": "storage ae2 applied energistics refinedstorage refined disk network chest drawer",
    "restoration": "restoration restore rebuild repair rebirth renewal cleanse sanctuary bloom",
}


def choose_theme(text: str, mod_ids=()) -> str:
    words = set(re.findall(r"[a-z0-9]+", (str(text) + " " + " ".join(mod_ids)).lower().replace("_", " ")))
    scores = {theme: len(words & set(terms.split())) for theme, terms in _THEME_WORDS.items()}
    return max(THEMES, key=lambda theme: scores[theme]) if any(scores.values()) else "exploration"


def texture_data_uri(texture: str, catalog: DecorCatalog | None = None) -> str | None:
    if not isinstance(texture, str) or not texture.startswith(NAMESPACE + ":"):
        return None
    catalog = catalog or load_catalog()
    return "data:image/png;base64," + base64.b64encode(catalog.read(texture)).decode("ascii")


# Values read from installed unmodified Minecraft clients' version.json.
_PACK_FORMATS = {"1.16.5": 6, "1.17.1": 7, "1.18.2": 8, "1.19.2": 9,
                 "1.20.1": 15, "1.20.4": 22, "1.21": 34, "1.21.1": 34,
                 "1.21.4": 46, "1.21.8": 64}


def resource_pack_format(mc_version: str = "") -> int:
    version = str(mc_version or "").strip()
    if not version:
        return 15
    if version not in _PACK_FORMATS:
        raise DecorLibraryError("Artwork resource-pack format is not verified for Minecraft " + version)
    return _PACK_FORMATS[version]


INSTALL_INSTRUCTIONS = """AutoQuestGen original quest artwork (offline)

1. Copy AutoQuestGen-Art.zip into your Minecraft instance's resourcepacks folder.
2. Enable AutoQuestGen-Art in Options > Resource Packs, then click Done.
3. Install the accompanying quest files into that same instance.

The app can install and enable this pack when you build directly into a selected
Minecraft instance. No account, download, server connection or API key is needed.
Each player needs the client resource pack to see the custom quest artwork.
This pack adds only the autoquestgen namespace; it does not replace game textures.
"""


def resource_pack_bytes(mc_version: str = "", *, catalog: DecorCatalog | None = None) -> bytes:
    catalog = catalog or load_catalog()
    pack_format = resource_pack_format(mc_version)
    metadata = {"pack": {"pack_format": pack_format,
                         "description": "AutoQuestGen - original offline quest artwork"}}
    files = {"pack.mcmeta": json.dumps(metadata, sort_keys=True, indent=2).encode("utf-8"),
             "README.txt": INSTALL_INSTRUCTIONS.encode("utf-8")}
    for asset in catalog.assets:
        files[asset.path] = catalog.read(asset)
        animation = animation_metadata(asset)
        if animation:
            files[asset.path + ".mcmeta"] = json.dumps(animation, sort_keys=True, indent=2).encode("utf-8")
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for filename, data in sorted(files.items()):
            info = zipfile.ZipInfo(filename, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return result.getvalue()


def _write_atomic(path: Path, data: bytes, backups: list[Path]) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name + ".autoquestgen-backup-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        backup.write_bytes(path.read_bytes())
        backups.append(backup)
    fd, temp = tempfile.mkstemp(prefix=".autoquestgen-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return True


def export_resource_pack(dest_zip: Path | str, mc_version: str = "", *, catalog: DecorCatalog | None = None) -> Path:
    destination = Path(dest_zip)
    _write_atomic(destination, resource_pack_bytes(mc_version, catalog=catalog), [])
    return destination


def _enabled_options(original: bytes) -> bytes:
    try:
        text = original.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DecorLibraryError("Minecraft options are not UTF-8; resource packs were not changed") from exc
    lines = text.splitlines(keepends=True)
    indexes = [i for i, line in enumerate(lines) if line.startswith("resourcePacks:")]
    if len(indexes) > 1:
        raise DecorLibraryError("Minecraft options contain duplicate resourcePacks entries")
    newline = "\r\n" if "\r\n" in text else "\n"
    if indexes:
        index = indexes[0]
        try:
            packs = json.loads(lines[index].split(":", 1)[1])
        except ValueError as exc:
            raise DecorLibraryError("Minecraft resourcePacks option is malformed; it was preserved") from exc
        if not isinstance(packs, list) or any(not isinstance(p, str) for p in packs):
            raise DecorLibraryError("Minecraft resourcePacks option is not a list of names")
    else:
        index, packs = len(lines), ["vanilla"]
    ref = "file/" + PACK_FILENAME
    if ref in packs:
        return original
    packs.append(ref)
    line = "resourcePacks:" + json.dumps(packs, ensure_ascii=False, separators=(",", ":")) + newline
    if indexes:
        # Preserve the lack of a trailing newline as well as unrelated lines.
        if not lines[index].endswith(("\n", "\r")):
            line = line.rstrip("\r\n")
        lines[index] = line
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.append(line)
    prefix = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
    return prefix + "".join(lines).encode("utf-8")


def validate_installation(instance_dir: Path | str, *, enable: bool = False) -> Path:
    """Read-only destination/options preflight for callers writing quest files."""
    instance = Path(instance_dir).resolve()
    if not instance.is_dir():
        raise DecorLibraryError("The selected Minecraft instance folder does not exist")
    target = instance / "resourcepacks" / PACK_FILENAME
    options = instance / "options.txt"
    if (target.parent.is_symlink() or target.is_symlink() or options.is_symlink()
            or not target.resolve().is_relative_to(instance)
            or not options.resolve().is_relative_to(instance)):
        raise DecorLibraryError("Artwork installation will not follow redirected instance files")
    if target.exists() and not target.is_file():
        raise DecorLibraryError("The artwork pack destination is not a file")
    if target.parent.exists() and not target.parent.is_dir():
        raise DecorLibraryError("The resourcepacks destination is not a folder")
    if enable:
        _enabled_options(options.read_bytes() if options.exists() else b"")
    return instance


def install_resource_pack(instance_dir: Path | str, mc_version: str = "", *, enable: bool = False,
                          catalog: DecorCatalog | None = None) -> dict:
    """Install only the generated ZIP; explicitly enabling preserves user packs.

    This is for a confirmed instance folder, not inferred from a quest path.
    Changed existing files get unique backups. Matching installs are no-ops.
    """
    instance = validate_installation(instance_dir, enable=enable)
    target = instance / "resourcepacks" / PACK_FILENAME
    options = instance / "options.txt"
    catalog = catalog or load_catalog()
    data = resource_pack_bytes(mc_version, catalog=catalog)
    # Validate option parsing before either destination is changed.
    option_data = _enabled_options(options.read_bytes() if options.exists() else b"") if enable else None
    backups = []
    changed = _write_atomic(target, data, backups)
    if option_data is not None:
        changed = _write_atomic(options, option_data, backups) or changed
    return {"path": str(target), "changed": changed, "enabled": bool(enable),
            "backups": [str(path) for path in backups], "asset_count": len(catalog.assets)}
