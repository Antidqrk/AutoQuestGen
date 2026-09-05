#!/usr/bin/env python3
"""
AutoQuestGen - a GUI that builds an FTB Quests book for a Minecraft
1.20.1 (Forge / NeoForge) modpack.

    scan mods  ->  build an AI prompt (mod list + real item / entity IDs)
               ->  AI writes a quest-design JSON   (or paste / load one)
               ->  validate + clean + (optionally) auto-chain / add rewards
               ->  write real FTB Quests SNBT into config/ftbquests/quests

Requires the standard library plus `requests`  ->  pip install requests
tkinter ships with the python.org Windows installer.

Run:  python autoquestgen.py         (or double-click run.bat)
"""

from __future__ import annotations

import base64
import collections
import hashlib
import io
import json
import math
import os
import pickle
import queue
import random
import re
import shutil
import subprocess
import threading
import time
import tomllib
import traceback
import zipfile
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import winsound
except Exception:  # pragma: no cover
    winsound = None

APP_NAME = "AutoQuestGen"          # release-audit naming: quest books FOR FTB Quests,
                                   # but no FTB/Minecraft in the app's own name
VERSION = "7.5"
CREDIT_LINE = "Created by antidqrk  ·  Built with Claude (Anthropic)"
HERE = Path(__file__).parent


def _resource_dir() -> Path:
    """Where the read-only bundled data lives (moddb/, icon, logo).

    Dev: next to this script. Frozen (PyInstaller): sys._MEIPASS - the onefile
    unpack dir, or the _internal dir in a onedir build. Path(__file__) also
    points inside _MEIPASS when frozen, but going through sys._MEIPASS is the
    documented contract, and it keeps working if the entry script ever moves.
    """
    import sys
    return Path(getattr(sys, "_MEIPASS", HERE))


def _writable_dir() -> Path:
    """Where the app WRITES: config, log, editor snapshot, scan caches.

    Never _MEIPASS - that folder is deleted when a frozen app exits, so a
    config saved there would silently vanish. Frozen: the exe's own folder
    (portable-app behaviour), falling back to %LOCALAPPDATA%/AutoQuestGen when
    that folder refuses writes (exe parked in Program Files). Dev: the script
    folder, exactly as before.
    """
    import sys
    if not getattr(sys, "frozen", False):
        return HERE
    exe_dir = Path(sys.executable).parent
    try:
        probe = exe_dir / ".aqg_write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return exe_dir
    except Exception:
        d = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AutoQuestGen"
        d.mkdir(parents=True, exist_ok=True)
        return d


RESOURCE_DIR = _resource_dir()
DATA_DIR = _writable_dir()


def moddb_path(*parts: str) -> Path:
    """THE way to reach bundled moddb data - dev and frozen resolve
    differently, and every ad-hoc HERE/"moddb" join breaks under PyInstaller."""
    return RESOURCE_DIR.joinpath("moddb", *parts)


CONFIG_PATH = DATA_DIR / "autoquestgen_config.json"
LOG_PATH = DATA_DIR / "autoquestgen.log"
EDITOR_SNAPSHOT = DATA_DIR / "editor_snapshot.json"
ICON_PATH = RESOURCE_DIR / "ftbquestsgen.ico"


# ========================================================================== #
#  1. SNBT writer + JSON -> FTB Quests SNBT converter
# ========================================================================== #

class _Long(int):
    pass


class _Double(float):
    pass


_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


_VALID_CODE = "0123456789abcdefklmnorABCDEFKLMNOR"


def _txt(s) -> str:
    """FTB Quests reads '&' as a Minecraft colour-code prefix. '&a', '&l' etc. are
    fine and kept; a bare '&' followed by a space or other char is invalid
    formatting, so those become the word 'and'."""
    s = str(s)
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "&":
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if nxt in _VALID_CODE:
                out.append(c)
                out.append(nxt)
                i += 2
                continue
            # bare ampersand -> "and", tidying surrounding spaces
            while out and out[-1] == " ":
                out.pop()
            out.append(" and " if (nxt == " " or (out and out[-1] != " ")) else "and")
            i += 1
            if nxt == " ":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out).strip()


def _has_fmt_code(s) -> bool:
    """True if the string already carries a Minecraft & formatting code (&a, &l, ...)."""
    s = str(s)
    return any(s[i] == "&" and i + 1 < len(s) and s[i + 1] in _VALID_CODE
               for i in range(len(s)))


def _key(k: str) -> str:
    return k if _BARE_KEY_RE.match(k) else '"%s"' % _esc(k)


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, _Long):
        return "%dL" % int(v)
    if isinstance(v, _Double):
        return repr(float(v)) + "d"
    if isinstance(v, float):
        return repr(v) + "d"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return '"%s"' % _esc(v)
    raise TypeError("not a scalar: %r" % (v,))


def _is_scalar(v) -> bool:
    return isinstance(v, (bool, int, float, str))


def snbt_dumps(value, indent: int = 0) -> str:
    pad = "\t" * indent
    cpad = "\t" * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{ }"
        lines = ["{"]
        for k in sorted(value.keys()):
            lines.append("%s%s: %s" % (cpad, _key(k), snbt_dumps(value[k], indent + 1)))
        lines.append("%s}" % pad)
        return "\n".join(lines)
    if isinstance(value, (list, tuple)):
        items = list(value)
        if not items:
            return "[ ]"
        if all(_is_scalar(x) for x in items):
            return "[" + " ".join(_scalar(x) for x in items) + "]"
        if len(items) == 1 and isinstance(items[0], dict):
            return "[{" + snbt_dumps(items[0], indent + 1)[1:] + "]"
        lines = ["["]
        for x in items:
            lines.append("%s%s" % (cpad, snbt_dumps(x, indent + 1)))
        lines.append("%s]" % pad)
        return "\n".join(lines)
    return _scalar(value)


# ---- relaxed SNBT reader (for the Repair tab) --------------------------- #

_NUM_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?[bBsSlLfFdD]?")
_BAREWORD_RE = re.compile(r"[A-Za-z0-9._+\-]+")


class SNBTError(ValueError):
    pass


def snbt_loads(text: str):
    """Parse the relaxed SNBT that FTB Quests writes into plain Python.
    Numbers keep their d / L suffix via _Double / _Long so they round-trip."""
    s = text
    n = len(s)
    i = 0

    def skip():
        nonlocal i
        while i < n and (s[i] in " \t\r\n,"):
            i += 1

    def parse_string():
        nonlocal i
        q = s[i]
        i += 1
        out = []
        while i < n:
            c = s[i]
            if c == "\\":
                nxt = s[i + 1] if i + 1 < n else ""
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                i += 2
                continue
            if c == q:
                i += 1
                return "".join(out)
            out.append(c)
            i += 1
        raise SNBTError("unterminated string")

    def parse_number(tok: str):
        suf = tok[-1] if tok and tok[-1] in "bBsSlLfFdD" else ""
        body = tok[:-1] if suf else tok
        is_int = body.lstrip("+-").isdigit()          # keep 64-bit ints exact
        try:
            if suf in ("l", "L"):
                return _Long(int(body) if is_int else int(float(body)))
            if suf in ("b", "B", "s", "S"):
                return int(body) if is_int else int(float(body))
            if suf in ("d", "D"):
                return _Double(float(body))
            if suf in ("f", "F"):
                return float(body)
            if "." in body or "e" in body.lower():
                return float(body)
            return int(body)
        except ValueError:
            return tok

    def parse_value():
        nonlocal i
        skip()
        if i >= n:
            raise SNBTError("unexpected end")
        c = s[i]
        if c == "{":
            return parse_compound()
        if c == "[":
            return parse_list()
        if c in "\"'":
            return parse_string()
        m = _NUM_RE.match(s, i)
        if m and m.end() > i and (m.group() not in ("+", "-", ".")):
            i = m.end()
            return parse_number(m.group())
        m = _BAREWORD_RE.match(s, i)
        if m:
            i = m.end()
            w = m.group()
            if w == "true":
                return True
            if w == "false":
                return False
            return w
        raise SNBTError("unexpected char %r at %d" % (c, i))

    def parse_compound():
        nonlocal i
        i += 1  # {
        out = {}
        while True:
            skip()
            if i < n and s[i] == "}":
                i += 1
                return out
            if i >= n:
                raise SNBTError("unterminated compound")
            if s[i] in "\"'":
                key = parse_string()
            else:
                m = _BAREWORD_RE.match(s, i)
                if not m:
                    raise SNBTError("bad key at %d" % i)
                key = m.group()
                i = m.end()
            skip()
            if i < n and s[i] == ":":
                i += 1
            out[key] = parse_value()

    def parse_list():
        nonlocal i
        i += 1  # [
        # typed array prefix  [I; ...]
        skip()
        if i + 1 < n and s[i] in "IBLl" and s[i + 1] == ";":
            i += 2
        out = []
        while True:
            skip()
            if i < n and s[i] == "]":
                i += 1
                return out
            if i >= n:
                raise SNBTError("unterminated list")
            out.append(parse_value())

    skip()
    root = parse_value()
    return root


# A mod is only barred wholesale when it is genuinely rotten. textures_ok()
# already drops individual missing-texture items, and every mod ships a few
# unused internal models - measured across this pack, healthy mods top out
# around 8% while the broken ones sit at 27% and 42%. Cutting in that gap
# keeps good decor mods instead of condemning them for normal noise.
BROKEN_MOD_LIMIT = 0.15


def ftb_id(*parts: str) -> str:
    """A stable 16-hex FTB Quests object id.

    IMPORTANT: FTB Quests parses these with Long.parseLong(s, 16), which throws
    for anything >= 2^63. An id whose first hex digit is 8-F is silently thrown
    away on load and REGENERATED — which detaches chapters from their group and
    breaks any saved progress. Mask the sign bit off so every id we mint parses.
    """
    seed = "\x1f".join(parts)
    val = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16)
    val &= 0x7FFFFFFFFFFFFFFF
    return format(val or 1, "016X")


_HEXID_RE = re.compile(r"^[0-9A-Fa-f]{16}$")


def _safe_hexid(hex_id: str) -> str:
    """Clear the sign bit on an existing id so FTB's Long.parseLong(s,16) accepts it.
    An id with the top bit set was never loadable, so nothing can be attached to it."""
    return format(int(hex_id, 16) & 0x7FFFFFFFFFFFFFFF or 1, "016X")


def slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return text or "chapter"


DATA_SNBT_DEFAULT = """{
	default_autoclaim_rewards: "disabled"
	default_consume_items: false
	default_quest_disable_jei: false
	default_quest_shape: "circle"
	default_reward_team: false
	detection_delay: 20
	disable_gui: false
	drop_book_on_death: false
	drop_loot_crates: false
	emergency_items_cooldown: 300
	grid_scale: 0.5d
	hide_excluded_quests: false
	icon: "minecraft:book"
	lock_message: ""
	loot_crate_no_drop: {
		boss: 0
		monster: 600
		passive: 4000
	}
	pause_game: false
	progression_mode: "linear"
	show_lock_icons: true
	title: "Quest Book"
	version: 13
}
"""
CHAPTER_GROUPS_EMPTY = "{\n\tchapter_groups: [ ]\n}\n"
MANIFEST_NAME = ".autoquestgen_manifest.json"


TASK_TYPES = ["item", "checkmark", "kill", "advancement", "dimension", "location",
              "biome", "structure", "stat", "stage", "fluid", "energy", "observation", "xp"]
REWARD_TYPES = ["item", "xp", "xp_levels", "loot", "choice", "random", "command",
                "advancement", "toast", "stage"]


def _int_of(v, d=1):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def _task(raw: dict, seed: str, warn, tables=None) -> dict | None:
    tables = tables or {}
    t = str(raw.get("type", "item")).lower()
    tid = ftb_id(seed, "task", json.dumps(raw, sort_keys=True, default=str))
    out = {"id": tid, "type": t}

    if t == "item":
        item = raw.get("item") or raw.get("target")
        if not item:
            warn("item task without 'item': %r" % raw)
            return None
        out["item"] = item
        c = _int_of(raw.get("count", 1))
        if c > 1:
            out["count"] = _Long(c)
        if raw.get("consume_items") is not None:
            out["consume_items"] = bool(raw["consume_items"])
        if raw.get("only_from_crafting"):
            out["only_from_crafting"] = True
        return out

    if t == "kill":
        ent = raw.get("entity") or raw.get("target")
        if not ent:
            warn("kill task without 'entity': %r" % raw)
            return None
        out["entity"] = ent
        out["value"] = _Long(max(1, _int_of(raw.get("value", raw.get("count", 1)))))
        return out

    if t in ("dimension", "biome", "structure", "stage"):
        key = {"dimension": "dimension", "biome": "biome",
               "structure": "structure", "stage": "stage"}[t]
        val = raw.get(key) or raw.get("target")
        if not val:
            warn("%s task missing '%s': %r" % (t, key, raw))
            return None
        out[key] = val
        return out

    if t == "advancement":
        adv = raw.get("advancement") or raw.get("target")
        if not adv:
            warn("advancement task without 'advancement': %r" % raw)
            return None
        out["advancement"] = adv
        out["criterion"] = raw.get("criterion", "")
        return out

    if t == "location":
        out["dimension"] = raw.get("dimension", "minecraft:overworld")
        pos = raw.get("position") or [raw.get("x", 0), raw.get("y", 0), raw.get("z", 0)]
        out["x"], out["y"], out["z"] = (_int_of(pos[0], 0), _int_of(pos[1], 0), _int_of(pos[2], 0))
        out["w"] = _int_of(raw.get("w", raw.get("radius", 1)), 1)
        out["h"] = _int_of(raw.get("h", raw.get("radius", 1)), 1)
        out["d"] = _int_of(raw.get("d", raw.get("radius", 1)), 1)
        out["ignore_dimension"] = bool(raw.get("ignore_dimension", False))
        return out

    if t == "stat":
        out["stat"] = raw.get("stat", "minecraft:jump")
        out["value"] = _int_of(raw.get("value", 1))
        return out

    if t == "fluid":
        out["fluid"] = raw.get("fluid", "minecraft:water")
        out["amount"] = _Long(_int_of(raw.get("amount", 1000), 1000))
        return out

    if t == "energy":
        out["value"] = _Long(_int_of(raw.get("value", raw.get("amount", 1000)), 1000))
        if raw.get("max_input"):
            out["max_input"] = _Long(_int_of(raw["max_input"], 100))
        return out

    if t == "observation":
        out["to_observe"] = raw.get("to_observe") or raw.get("target") or raw.get("block") \
            or raw.get("entity") or "minecraft:stone"
        out["observe_type"] = _int_of(raw.get("observe_type", 0), 0)
        out["timer"] = _Long(_int_of(raw.get("timer", 0), 0))
        return out

    if t == "xp":
        out["value"] = _Long(_int_of(raw.get("value", raw.get("xp", 30)), 30))
        out["points"] = bool(raw.get("points", True))
        return out

    if t in ("checkmark", "check", "manual"):
        out["type"] = "checkmark"
        if raw.get("title"):
            out["title"] = _txt(raw["title"])
        return out

    warn("unknown task type %r -> checkmark: %r" % (t, raw))
    return {"id": tid, "type": "checkmark", "title": _txt(raw.get("title", "TODO"))}


def _table_long(hex_id: str):
    v = int(hex_id, 16)
    if v >= 2 ** 63:
        v -= 2 ** 64
    return _Long(v)


def _reward(raw: dict, seed: str, warn, tables=None) -> dict | None:
    tables = tables or {}
    t = str(raw.get("type", "item")).lower()
    rid = ftb_id(seed, "reward", json.dumps(raw, sort_keys=True, default=str))
    out = {"id": rid, "type": t}

    if t == "item":
        item = raw.get("item") or raw.get("target")
        if not item:
            return None
        out["item"] = item
        c = _int_of(raw.get("count", 1))
        if c != 1:
            out["count"] = c
        if raw.get("random_bonus"):
            out["random_bonus"] = _int_of(raw["random_bonus"], 0)
        return out

    if t in ("xp", "xp_levels"):
        amt = _int_of(raw.get("xp", raw.get("xp_levels", raw.get("amount", 100))), 100)
        out["type"] = t
        out["xp_levels" if t == "xp_levels" else "xp"] = amt
        return out

    if t in ("loot", "random", "choice", "all_table", "all"):
        out["type"] = {"all": "all_table"}.get(t, t)
        key = str(raw.get("table") or raw.get("table_id") or "").strip()
        hex_id = tables.get(key) or (key if _HEXID_RE.match(key) else None)
        if not hex_id:
            warn("reward references unknown table %r -> skipped" % key)
            return None
        out["table_id"] = _table_long(hex_id)
        return out

    if t == "command":
        cmd = raw.get("command")
        if not cmd:
            return None
        out["command"] = cmd
        out["elevate_perms"] = bool(raw.get("elevate_perms", raw.get("permission_level", 2)))
        out["silent"] = bool(raw.get("silent", True))
        return out

    if t == "advancement":
        adv = raw.get("advancement")
        if not adv:
            return None
        out["advancement"] = adv
        out["criterion"] = raw.get("criterion", "")
        return out

    if t == "toast":
        out["description"] = _txt(raw.get("description") or raw.get("title") or "Well done!")
        return out

    if t == "stage":
        out["stage"] = raw.get("stage", "")
        out["remove"] = bool(raw.get("remove", False))
        return out

    warn("unknown reward type %r -> skipped: %r" % (t, raw))
    return None


# ---- reward tables -------------------------------------------------------- #

def build_reward_tables(doc: dict, warn):
    """Return (files, key_to_hex). files = [(filename, table_dict)]."""
    raw = doc.get("reward_tables") or doc.get("reward_pools") or []
    files, key_to_hex = [], {}
    used = set()
    for i, rt in enumerate(raw):
        key = str(rt.get("id") or rt.get("name") or ("table_%d" % i))
        hex_id = ftb_id("rewardtable/" + key)
        key_to_hex[key] = hex_id
        slug = slugify(key)
        while slug in used:
            slug += "_"
        used.add(slug)
        entries = []
        for e in rt.get("rewards") or rt.get("items") or []:
            w = _int_of(e.get("weight", 1), 1)
            rr = _reward(e, "table/" + key, warn)
            if rr:
                # FLAT, not {"reward": rr}. FTBQ's RewardTable reads each entry
                # compound directly - {item:"...", type:"item", weight:5} - so
                # nesting made every entry deserialise to AIR. 60 of 60 dead,
                # 61 of 404 quests paying nothing.
                #
                # It looked healthy from the outside, which is why it survived:
                # "weight" sat at the level FTBQ reads, so the tables had
                # correct-looking weights attached to invisible rewards.
                # Verified flat against a published pack's own reward table
                # (Life-in-the-Village-4, reward_tables/ores_4.snbt) rather
                # than inferred from the class files, whose "reward" string
                # is a field name and not the NBT key.
                ent = dict(rr)
                ent["weight"] = w
                entries.append(ent)
        if not entries:
            continue
        table = {
            "id": hex_id,
            "order_index": i,
            "title": _txt(rt.get("title", key)),
            "icon": rt.get("icon", "minecraft:chest"),
            "use_title": bool(rt.get("use_title", True)),
            "hide_tooltip": bool(rt.get("hide_tooltip", False)),
            "loot_size": _int_of(rt.get("loot_size", 1), 1),
            "empty_weight": _int_of(rt.get("empty_weight", 0), 0),
            "rewards": entries,
        }
        files.append((slug, table))
    return files, key_to_hex


def _quest(raw: dict, chapter_seed: str, id_map: dict, warn, tables=None) -> dict:
    tables = tables or {}
    # Prefer the key the id_map pass assigned. It disambiguates duplicates and
    # anonymous quests, which otherwise all keyed on the string "None" and
    # collapsed onto one id - silently becoming the same quest.
    qkey = raw.get("_idkey") or str(raw.get("id") or raw.get("title"))
    seed = chapter_seed + "/" + qkey
    if qkey not in id_map:
        id_map[qkey] = qid_for(chapter_seed, qkey)
    q = {"id": id_map[qkey]}
    if raw.get("title"):
        q["title"] = _txt(raw["title"])
    if raw.get("subtitle"):
        q["subtitle"] = _txt(raw["subtitle"])
    desc = raw.get("description")
    if isinstance(desc, str):
        desc = [desc]
    if desc:
        q["description"] = [_txt(x) for x in desc]
    q["x"] = _Double(float(raw.get("x", 0.0)))
    q["y"] = _Double(float(raw.get("y", 0.0)))
    if raw.get("shape"):
        q["shape"] = raw["shape"]
    if raw.get("size"):
        try:
            q["size"] = _Double(float(raw["size"]))
        except (TypeError, ValueError):
            pass
    if raw.get("hide") in (True, "true", 1):
        q["hide"] = True
    deps = []
    for d in raw.get("dependencies", []) or []:
        d = str(d)
        if d in id_map:
            deps.append(id_map[d])
        else:
            warn("quest %r depends on unknown %r (dropped)" % (qkey, d))
    if deps:
        q["dependencies"] = deps
    tasks = []
    for rt in raw.get("tasks", []) or []:
        ct = _task(rt, seed, warn, tables)
        if ct:
            tasks.append(ct)
    if not tasks:
        tasks = [{"id": ftb_id(seed, "task", "fallback"), "type": "checkmark"}]
    q["tasks"] = tasks
    rewards = []
    for rr in raw.get("rewards", []) or []:
        cr = _reward(rr, seed, warn, tables)
        if cr:
            rewards.append(cr)
    if rewards:
        q["rewards"] = rewards
    if raw.get("optional"):
        q["optional"] = True
    if raw.get("min_required_dependencies") is not None:
        q["min_required_dependencies"] = _int_of(raw["min_required_dependencies"], 0)
    return q


def _chapter_icon(raw_chapter: dict, quests: list) -> str:
    if raw_chapter.get("icon"):
        return raw_chapter["icon"]
    for q in quests:
        for tk_ in q.get("tasks", []) or []:
            if tk_.get("type", "item") == "item" and tk_.get("item"):
                return tk_["item"]
    return "minecraft:book"


# ---- layout engine ----------------------------------------------------- #

LAYOUTS = ["line", "tree", "radial", "spiral", "clusters", "spread",
           "lanes", "rosette", "matrix", "spine", "ai", "ai+tidy"]
LAYOUT_HIDE_LINES = {"radial", "spiral", "clusters"}


def break_dep_cycles(chapters, warn=None) -> int:
    """Cut dependency cycles across the whole book. -> edges removed

    FTB Quests NEVER checks this on load. Quest.verifyDependencies is called
    only from QuestButton, the in-game editor, so a cycle written into SNBT
    loads silently and every quest in it - plus everything downstream - is
    permanently uncompletable. A QA pass drove 2-cycles, 5-cycles, self-
    dependencies and cross-chapter cycles all the way through to written files
    with no warning anywhere.

    The offline builder does not produce cycles; a model reply can, and the
    user regenerates with AI every time. Cut the edge that CLOSES each cycle,
    which preserves the intended order and loses the least.
    """
    quests = [q for _n, ch in chapters for q in (ch.get("quests") or [])]
    by_id = {q.get("id"): q for q in quests if q.get("id")}
    state, removed = {}, 0

    def visit(qid):
        nonlocal removed
        st = state.get(qid)
        if st == 2:
            return
        state[qid] = 1
        q = by_id.get(qid)
        keep = []
        for d in (q.get("dependencies") or []) if q else []:
            if d == qid:                      # self-dependency
                removed += 1
                continue
            if state.get(d) == 1:             # this edge closes a cycle
                removed += 1
                if warn:
                    warn("  cut dependency %s -> %s (would form a cycle)"
                         % (qid, d))
                continue
            if d in by_id:
                visit(d)
            keep.append(d)
        if q is not None:
            if keep:
                q["dependencies"] = keep
            else:
                q.pop("dependencies", None)
        state[qid] = 2

    for qid in list(by_id):
        if state.get(qid) != 2:
            visit(qid)
    return removed


def _dep_levels(quests, in_ids):
    """rank (longest path from a root) for each quest id, using only in-chapter deps."""
    deps = {q["id"]: [d for d in (q.get("dependencies") or []) if d in in_ids] for q in quests}
    rank = {}

    def r(qid, seen):
        if qid in rank:
            return rank[qid]
        if qid in seen or not deps.get(qid):
            rank[qid] = 0
            return 0
        seen = seen | {qid}
        v = 1 + max((r(d, seen) for d in deps[qid]), default=-1)
        rank[qid] = v
        return v
    for q in quests:
        r(q["id"], set())
    return rank


def _declutter(pos, iters=60, min_d=1.9):
    ids = list(pos)
    # Points sitting on exactly the same spot have no direction to push apart
    # in, so nudge duplicates onto a small ring first. Without this a chapter
    # whose quests all arrived at (0,0) stays a single stack forever.
    seen = {}
    for k, (x, y) in list(pos.items()):
        key = (round(x, 4), round(y, 4))
        n = seen.get(key, 0)
        seen[key] = n + 1
        if n:
            a = n * 2.399963229728653
            pos[k] = (x + min_d * 0.6 * math.cos(a) * (1 + n * 0.12),
                      y + min_d * 0.6 * math.sin(a) * (1 + n * 0.12))
    for _ in range(iters):
        moved = False
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                ax, ay = pos[ids[i]]
                bx, by = pos[ids[j]]
                dx, dy = ax - bx, ay - by
                d = math.hypot(dx, dy) or 0.001
                if d < min_d:
                    push = (min_d - d) / 2
                    ux, uy = dx / d, dy / d
                    pos[ids[i]] = (ax + ux * push, ay + uy * push)
                    pos[ids[j]] = (bx - ux * push, by - uy * push)
                    moved = True
        if not moved:
            break
    return pos


def _shorten_links(pos, quests, in_ids, max_len=6.5, rounds=14):
    """Pull quests back toward their prerequisites. -> pos (mutated)

    Every layout here places quests by RANK and lets the dependency lines fall
    where they may. That is fine while a quest's prerequisite is one rank back,
    and wrong the moment it is not: a chapter's finale commonly depends on the
    OPENING quest, and "lanes" parks the finale past the last column, so the
    line between them ran the full width of the chapter. Measured on CREATE
    FEMBY at the user's own settings: Abyssal Decor drew a 35.8-unit line
    across a 44-unit-wide chapter, Re:Deco 26.5, Mekanism 23.2, against 3.2 to
    4.3 for a chapter that reads properly.

    The cost is not only the line. Chapter art is sized from the quest bounding
    box, so one flung-out quest inflates the backdrop to match, which is the
    acre of empty tiled floor beside the cluster.

    So: any quest sitting further than `max_len` from the centre of its own
    prerequisites is drawn back along that line to `max_len`. Iterated, because
    moving a quest shortens its parent edge and lengthens its children's, and
    decluttered afterwards so pulling two quests to the same place does not
    stack them. Roots and quests already close enough are left exactly where
    their layout put them - this straightens outliers, it does not re-layout.
    """
    if not pos or len(pos) < 3:
        return pos
    deps = {q["id"]: [d for d in (q.get("dependencies") or [])
                      if d in in_ids and d != q["id"]] for q in quests}

    # How many children each parent has. A parent with k children cannot hold
    # them all within a fixed radius AND keep them apart: they have to fit on
    # a circle, so the circumference needs k * min_gap of room. Pulling every
    # child to the same radius regardless of k is why a dense chapter traded
    # one constraint for the other - at density=colossal, links reached 8.5
    # only by squeezing neighbours to 1.29 apart. Give a crowded parent the
    # radius its brood actually needs and both constraints can hold at once.
    brood: dict = {}
    for _q, _ds in deps.items():
        for _d in _ds:
            brood[_d] = brood.get(_d, 0) + 1

    def _allow(ds2):
        k = max(brood.get(d, 1) for d in ds2)
        if k <= 4:
            return max_len
        return max(max_len, (k * 1.75) / (2.0 * math.pi) * 1.25)

    def tighten():
        """One pull toward prerequisites. -> how many quests moved"""
        far = []
        for qid, ds in deps.items():
            ds2 = [d for d in ds if d in pos]
            if not ds2 or qid not in pos:
                continue
            ax = sum(pos[d][0] for d in ds2) / len(ds2)
            ay = sum(pos[d][1] for d in ds2) / len(ds2)
            x, y = pos[qid]
            dist = math.hypot(x - ax, y - ay)
            lim = _allow(ds2)
            if dist > lim:
                far.append((qid, ax, ay, x, y, dist, lim))
        for qid, ax, ay, x, y, dist, lim in far:
            k = lim / dist
            pos[qid] = (ax + (x - ax) * k, ay + (y - ay) * k)
        return len(far)

    # Interleaved, and ENDING on a tighten. Decluttering last undid the work -
    # it pushes overlapping quests apart with no idea which of them was the
    # outlier, and measured that way SecurityCraft's longest line went UP, from
    # 19.5 to 30.7. Short, cheap declutter passes between pulls keep quests off
    # each other without being the last word on where anything sits.
    for _ in range(rounds):
        if not tighten():
            break
        _declutter(pos, iters=10, min_d=1.7)
    tighten()
    # Ending on a tighten leaves quests STACKED: the pull aims several
    # children at the same parent without looking at where the others went
    # (measured 1.09 apart on one pack, 1.27 on another - closer together than
    # an icon is wide). So spacing gets the last word. Re-tightening after it
    # only put the piles back, 1.09 -> 0.89, and the rounds above have already
    # brought the graph in far enough that a spacing pass moves quests by a
    # fraction of a unit rather than flinging one across the chapter.
    _declutter(pos, iters=25, min_d=1.75)
    # Spacing gets the last word, but on a DENSE chapter it has a lot of
    # pushing to do and can carry a child back out past the bar: measured at
    # density=colossal with a radial layout, links reached 8.6 to 9.5 on three
    # packs while the profile this was tuned against stayed at 6.6. So settle
    # the two constraints against each other instead of running each once -
    # a few alternations with a gentler spacing step, which converges because
    # each pull is bounded by max_len and each push by min_d.
    for _ in range(4):
        if not tighten():
            break
        _declutter(pos, iters=8, min_d=1.55)
    # Spacing is the constraint that must HOLD. A link that grows inside a big
    # chapter is judged against that chapter's own extent and stays fine;
    # quests drawn on top of each other are wrong at any size. Eight
    # iterations did not converge on a crowded chapter - density=colossal caps
    # a chapter at 130 quests - and left neighbours 1.29 apart, closer than an
    # icon is wide. This one runs long enough to finish the job.
    _declutter(pos, iters=60, min_d=1.75)
    return pos


def layout_positions(quests, style, jitter, seed):
    """Return {quest_id: (x, y)} for a chapter, or None to keep AI coordinates."""
    if not quests:
        return None
    ids = [q["id"] for q in quests]
    in_ids = set(ids)
    rank = _dep_levels(quests, in_ids)
    rng = random.Random(seed)
    j = jitter
    # Which quest sits where WITHIN a rank was previously ids.index() - the
    # order they happened to be built in - so a chapter drew itself the same
    # way on every run and "generate again" produced a book the user could not
    # tell apart. Rank still decides the tier, so the progression a player
    # reads is unchanged; only the seating inside a tier moves. Seeded, so the
    # same seed still reproduces a book exactly.
    tie = {qid: rng.random() for qid in ids}
    ids = sorted(ids, key=lambda q: (rank[q], tie[q]))

    def _given():
        return {q["id"]: (float(q.get("x", 0) or 0), float(q.get("y", 0) or 0))
                for q in quests}

    def _degenerate(p):
        """True when the supplied coordinates carry no information (everything
        on one spot), which happens whenever the model didn't send x/y."""
        return len({(round(x, 3), round(y, 3)) for x, y in p.values()}) < max(2, len(p) // 3)

    if style in ("ai", "ai+tidy"):
        pos = _given()
        if _degenerate(pos):
            style = "spiral"        # nothing usable came back - lay it out ourselves
        elif style == "ai+tidy":
            return _declutter(pos, min_d=1.8)
        else:
            return None             # keep the model's coordinates as-is

    pos = {}
    if style == "line":
        by_rank = {}
        for qid in ids:
            by_rank.setdefault(rank[qid], []).append(qid)
        for rk, group in by_rank.items():
            for k, qid in enumerate(group):
                pos[qid] = (2.0 * rk + rng.uniform(-j, j),
                            (k - (len(group) - 1) / 2) * 2.2 + rng.uniform(-j, j))

    elif style == "tree":
        by_rank = {}
        for qid in ids:
            by_rank.setdefault(rank[qid], []).append(qid)
        for rk, group in by_rank.items():
            for k, qid in enumerate(group):
                pos[qid] = ((k - (len(group) - 1) / 2) * 2.5 + rng.uniform(-j, j),
                            2.2 * rk + rng.uniform(-j, j))

    elif style == "radial":
        by_rank = {}
        for qid in ids:
            by_rank.setdefault(rank[qid], []).append(qid)
        maxrk = max(by_rank) or 1
        for rk, group in sorted(by_rank.items()):
            radius = 0.0 if (rk == 0 and len(group) == 1) else 2.6 + 5.0 * (rk / maxrk) ** 0.85
            for k, qid in enumerate(group):
                ang = (k / max(1, len(group))) * math.tau + rk * 0.7
                pos[qid] = (radius * math.cos(ang) + rng.uniform(-j, j),
                            radius * math.sin(ang) + rng.uniform(-j, j))

    elif style == "spiral":
        # a real Archimedean spiral: one continuous arm winding outward,
        # points spaced by roughly constant arc length so it reads as a spiral.
        order = sorted(ids, key=lambda q: (rank[q], tie[q]))
        ang = 3.4                      # start a little way out so the centre isn't a knot
        b = 0.62                       # radial growth per radian
        step_arc = 2.35                # spacing between consecutive quests
        for qid in order:
            r = b * ang
            pos[qid] = (r * math.cos(ang) + rng.uniform(-j * 0.3, j * 0.3),
                        r * math.sin(ang) + rng.uniform(-j * 0.3, j * 0.3))
            ang += step_arc / max(r, 1.4)

    elif style == "clusters":
        # group quests that share a parent into a blob; lay the blobs out by rank
        deps = {q["id"]: [d for d in (q.get("dependencies") or []) if d in in_ids] for q in quests}
        anchor = {}
        for qid in ids:
            anchor[qid] = deps[qid][0] if deps.get(qid) else "__root__"
        blobs = {}
        for qid in ids:
            blobs.setdefault(anchor[qid], []).append(qid)
        brank = {}
        for a, members in blobs.items():
            brank[a] = min(rank[m] for m in members)
        by_br = {}
        for a in blobs:
            by_br.setdefault(brank[a], []).append(a)
        for rk, alist in sorted(by_br.items()):
            for bi, a in enumerate(alist):
                grp = blobs[a]
                gx = 3.4 * rk
                gy = (bi - (len(alist) - 1) / 2) * 4.0
                for k, qid in enumerate(grp):
                    if len(grp) == 1:
                        dx = dy = 0.0
                    else:
                        ang2 = (k / len(grp)) * math.tau
                        rr = 1.05 + 0.6 * (len(grp) > 3)
                        dx, dy = rr * math.cos(ang2), rr * math.sin(ang2)
                    pos[qid] = (gx + dx + rng.uniform(-j * 0.3, j * 0.3),
                                gy + dy + rng.uniform(-j * 0.3, j * 0.3))
        _declutter(pos, min_d=1.7)

    elif style == "lanes":
        # Arcanum's parallel-track chapters: the opening quest fans into a few
        # lanes that run side by side and converge again at the finale. Reads as
        # "pick your route", which a single chain never does.
        order = sorted(ids, key=lambda q: (rank[q], tie[q]))
        head, tail = order[:1], order[-1:] if len(order) > 4 else []
        body = order[len(head):len(order) - len(tail)]
        nlane = 3 if len(body) >= 9 else (2 if len(body) >= 4 else 1)
        lane_y = [(k - (nlane - 1) / 2) * 4.2 for k in range(nlane)]
        for qid in head:
            pos[qid] = (0.0, 0.0)
        cols = -(-len(body) // nlane) if body else 1
        # CONTIGUOUS runs per lane. Round-robin (k % nlane) sent consecutive
        # quests to different lanes, so every dependency line zig-zagged the
        # full height of the chapter - the spaghetti in the screenshot.
        for k, qid in enumerate(body):
            lane, col = k // cols, k % cols
            lane = min(lane, nlane - 1)
            pos[qid] = (3.0 + 2.6 * col + rng.uniform(-j * 0.2, j * 0.2),
                        lane_y[lane] + rng.uniform(-j * 0.2, j * 0.2))
        for qid in tail:
            pos[qid] = (3.0 + 2.6 * cols + 2.2, 0.0)

    elif style == "rosette":
        # the repeating flower motif: a hub quest ringed by its follow-ups. Only
        # quests that actually branch become hubs - ringing a 1-child node just
        # redraws a chain - and a hub already placed as someone's satellite is
        # ringed where it really sits, not where its grid cell would be.
        deps = {q["id"]: [d for d in (q.get("dependencies") or []) if d in in_ids]
                for q in quests}
        kids = {}
        for qid in ids:
            if deps.get(qid):
                kids.setdefault(deps[qid][0], []).append(qid)
        hubs = [q for q in sorted(ids, key=lambda z: (rank[z], tie[z]))
                if len(kids.get(q, ())) >= 2]
        if not hubs:                       # nothing branches - a ring per chapter
            hubs = sorted(ids, key=lambda z: rank[z])[:1]
        per_row = max(1, int(math.ceil(len(hubs) ** 0.5)))
        PITCH = 9.0
        cell = 0
        for hub in hubs:
            if hub not in pos:
                pos[hub] = ((cell % per_row) * PITCH, (cell // per_row) * PITCH)
                cell += 1
            hx, hy = pos[hub]
            ring = [k for k in kids.get(hub, []) if k not in pos]
            for k, qid in enumerate(ring):
                ang = (k / max(1, len(ring))) * math.tau - math.pi / 2
                rr = 2.4 if len(ring) <= 6 else 3.2
                pos[qid] = (hx + rr * math.cos(ang), hy + rr * math.sin(ang))
        loose = [q for q in sorted(ids, key=lambda z: rank[z]) if q not in pos]
        base = ((cell // per_row) + 1) * PITCH
        for k, qid in enumerate(loose):
            pos[qid] = ((k % per_row) * PITCH, base + (k // per_row) * PITCH)
        _declutter(pos, min_d=1.7)

    elif style == "matrix":
        # the dense collection grid: tidy rows, one tier per row, so a long
        # "get one of each" chapter reads as a table instead of a sprawl.
        order = sorted(ids, key=lambda q: (rank[q], tie[q]))
        width = max(4, min(8, int(round(len(order) ** 0.5)) + 1))
        for k, qid in enumerate(order):
            pos[qid] = (1.9 * (k % width), 1.9 * (k // width))

    elif style == "spine":
        # a long horizontal backbone with side-branches, like Arcanum's main
        # progression map: the critical path is obvious, extras hang off it.
        by_rank = {}
        for qid in ids:
            by_rank.setdefault(rank[qid], []).append(qid)
        for rk, group in sorted(by_rank.items()):
            pos[group[0]] = (2.8 * rk, 0.0)
            for k, qid in enumerate(group[1:]):
                side = 1 if k % 2 == 0 else -1
                step = 1 + k // 2
                pos[qid] = (2.8 * rk + rng.uniform(-j * 0.2, j * 0.2), side * 2.3 * step)

    elif style == "spread":
        by_rank = {}
        for qid in ids:
            by_rank.setdefault(rank[qid], []).append(qid)
        for rk, group in by_rank.items():
            for k, qid in enumerate(group):
                pos[qid] = (2.4 * rk + rng.uniform(-1.2, 1.2),
                            (k - (len(group) - 1) / 2) * 2.4 + rng.uniform(-1.2, 1.2))
        _declutter(pos, min_d=2.1)

    return _shorten_links(pos, quests, in_ids)


# The aesthetic setting, as NUMBERS the offline builder can act on.
#
# It used to be prose and nothing else: `aesthetic` was read in four places, all
# of them AI prompt assembly. So on the offline path - which is the primary path
# now, there is no API key - the setting reached nothing. Measured: all four
# levels produced a byte-identical book, md5 34bbba69405fe2b3 across the lot.
# A setting that cannot change the output is a bug, not a style.
#
# A level is a budget for each of the THREE LAYERS a chapter is dressed in, not
# one number. Splitting it is what makes the levels actually differ: with a
# single `images` count the sourcing topped out at one backdrop per chapter, so
# balanced / decorated / lavish came out byte-identical (md5 98d93acc7cc800f3 on
# the reference pack, cb7896283662cde4 on ArcanumLand, 49d442eb1e0beb06 on THE
# FORGOTTEN SMP) - three levels, one book. A budget the supply cannot spend is
# not a budget.
#
#   panel   the tinted shape card behind the whole chapter. Comes from the
#           ftb-quests jar itself, so it needs nothing from the pack and a
#           chapter can never render bare.
#   field   the chapter's own material, TILED at close to native scale. A 16px
#           texture stretched over the chapter is the 14x magnification that
#           made the old backdrop look like a bug; tiled, it reads as a wall.
#   motifs  small opaque cutouts ringed OUTSIDE the quests. This is the layer
#           that makes a chapter look authored rather than generated.
#
# The ladder is the corpus, not taste: 283 of 838 real chapters carry ZERO
# images (minimal), and among the rest p25=2, median=9, p75=24 - balanced,
# decorated, lavish. Alphas likewise: real packs fade backdrops and never fade
# motifs (alpha 255 on 4,265 of 4,383 sampled images).
#
# `shapes` and `desc_lines` fall the other way, and deliberately. Lavish buys
# frame, never noise: it does not get more words per quest and does not get more
# shape variance. A lavish book that is harder to read is a worse book.
AESTHETIC_LEVELS = {
    "minimal": {
        "panel": 0, "field": 0, "motifs": 0, "panel_alpha": 0,
        "images": 0, "shapes": False, "desc_lines": 0,
        "prompt": "Keep quest text short. Plain functional icons. Few decorative items.",
    },
    "balanced": {
        "panel": 1, "field": 0, "motifs": 2, "panel_alpha": 100,
        "images": 3, "shapes": True, "desc_lines": 2,
        "prompt": "Write 1-2 sentence descriptions. Pick evocative icons. Use a decorative "
                  "mod item as a reward here and there.",
    },
    "decorated": {
        "panel": 1, "field": 4, "motifs": 6, "panel_alpha": 100,
        "images": 11, "shapes": True, "desc_lines": 3,
        "prompt": "Write 2-3 sentence flavourful descriptions with lore. Choose the most "
                  "iconic item for every chapter and quest icon. Add 1 'showcase' side "
                  "quest per chapter that collects a decorative-mod set, and use decorative "
                  "blocks / furniture / paintings as bonus rewards.",
    },
    "lavish": {
        "panel": 1, "field": 12, "motifs": 11, "panel_alpha": 110,
        "images": 24, "shapes": True, "desc_lines": 4,
        "prompt": "Write rich 3-4 sentence descriptions with worldbuilding and tips. Every "
                  "chapter opens with a themed intro quest. 2-3 decoration showcase side "
                  "quests per chapter (furniture, chairs, paintings, lamps, statues, plushies). "
                  "Rewards frequently include decorative blocks and building materials.",
    },
}

# Every shape ftb-quests ships a card for in EVERY build we can check. All nine
# carry textures/shapes/<s>/background.png at 128x128 RGBA, mean RGB 255/255/255
# with shaped alpha - pure white, so FTBQ's multiply blend makes the tint come
# out as EXACTLY the tint. Nine shapes x sixteen chat colours is 144 distinct
# chapter cards out of a mod that is installed by definition, which is why the
# panel is the first layer and not the last: there is no pack, however bare, in
# which a chapter renders with nothing.
# "none" was in this tuple and is gone. Of the four ftb-quests jars on this
# machine (2001.4.11 / .14 / .21 / .22) only 4.11 ships no shapes/none/
# background.png. This tuple is the whitelist deciding what the panel may name,
# so a shape in it that some build does not ship is a purple-and-black chapter
# card waiting for an older pack. Nothing reaches "none" today - _theme_for only
# ever returns square/hexagon/pentagon/circle/gear/octagon/diamond - but the
# point of a whitelist is that it holds when something does. Anything not listed
# already falls through to rsquare, which every build ships.
_SHAPE_PANELS = ("circle", "diamond", "gear", "heart", "hexagon",
                 "octagon", "pentagon", "rsquare", "square")


def _aesthetic(opts: dict) -> dict:
    """The AESTHETIC_LEVELS row for these opts. Unknown / missing -> balanced."""
    return AESTHETIC_LEVELS.get(
        str((opts or {}).get("aesthetic", "balanced")), AESTHETIC_LEVELS["balanced"])


# Backdrop keys -> a REAL vanilla 1.20.1 texture (ships in the client jar, always
# loads, no resource pack). These are the advancement-screen backgrounds.
VANILLA_BACKDROPS = {
    "stone":     "minecraft:textures/gui/advancements/backgrounds/stone.png",
    "adventure": "minecraft:textures/gui/advancements/backgrounds/adventure.png",
    "nether":    "minecraft:textures/gui/advancements/backgrounds/nether.png",
    "end":       "minecraft:textures/gui/advancements/backgrounds/end.png",
    "husbandry": "minecraft:textures/gui/advancements/backgrounds/husbandry.png",
}

# group keyword -> (title colour code, quest shape, emblem, vanilla backdrop key)
GROUP_THEMES = [
    (("vanilla", "overworld", "start", "basic"), "e", "square", "runes", "stone"),
    (("tech", "create", "machine", "industr", "engineer"), "b", "hexagon", "gear", "stone"),
    (("magic", "arcane", "botania", "spell", "occult"), "d", "pentagon", "runes", "end"),
    (("nature", "farm", "food", "garden", "animal"), "a", "circle", "leaf", "husbandry"),
    (("adventure", "explore", "dungeon", "dimension", "boss", "combat"), "6", "gear", "sword", "adventure"),
    (("nether", "hell", "fire", "blaze"), "c", "octagon", "sword", "nether"),
    (("end", "dragon", "void", "chorus"), "5", "octagon", "sword", "end"),
    (("decor", "furnitur", "aesthetic", "build", "paint"), "9", "diamond", "sparkle", "stone"),
    (("farm", "food", "cook", "crop", "harvest"), "a", "circle", "leaf", "husbandry"),
    (("support", "util", "storage", "expansion", "misc"), "7", "square", "sparkle", "stone"),
]
_PALETTE_CODES = ["e", "b", "d", "a", "6", "c", "9", "5", "2", "3"]
_BACKDROP_CYCLE = ["stone", "adventure", "husbandry", "end", "stone", "nether"]


def _theme_for(group_or_title: str, idx: int):
    """(title colour code, quest shape, emblem, vanilla backdrop key).
    `idx` should be STABLE per group (not per chapter) so a whole group looks
    consistent — pass the group's position, or a hash of its name."""
    g = (group_or_title or "").lower()
    for keys, code, shape, emblem, backdrop in GROUP_THEMES:
        if any(k in g for k in keys):
            return code, shape, emblem, backdrop
    code = _PALETTE_CODES[idx % len(_PALETTE_CODES)]
    return code, "square", "sparkle", _BACKDROP_CYCLE[idx % len(_BACKDROP_CYCLE)]


def _role_shapes(quests, gshape: str) -> dict:
    """quest id -> shape, by the part it plays in the chapter. -> dict

    _quest_shape reads one quest in isolation, so it can only speak when the
    TASK is distinctive - a kill, a dimension, three tasks at once. A chapter
    of plain "collect one item" quests gives it nothing to say and every node
    falls through to the group shape, which is why whole chapters shipped as
    thirty-five identical diamonds.

    Role is the information that is always there. Where a quest sits in the
    graph is exactly what a player wants to read off the map at a glance:

      square    an ENTRY - nothing precedes it, start anywhere you like
      octagon   a CAPSTONE - nothing follows it, the chapter ends here
      circle    a SIDE quest - optional, skip it without blocking anything

    Anything in the middle of the graph keeps whatever _quest_shape decided.
    A chapter with one entry and one capstone therefore reads as a shape it
    could not have read as before, without any shape being decorative.
    """
    ids = {q["id"] for q in quests}
    deps = {q["id"]: [d for d in (q.get("dependencies") or []) if d in ids]
            for q in quests}
    has_child = set()
    for _q, ds in deps.items():
        has_child.update(ds)
    n = len(quests)
    entries = [q["id"] for q in quests if not deps[q["id"]]]
    caps = [q["id"] for q in quests if deps[q["id"]] and q["id"] not in has_child]
    # Suppressed PER ROLE, not for the chapter as a whole. Many chapters are
    # one root fanning out to leaves - 1 entry and 28 capstones out of 35 - so
    # a single all-or-nothing guard threw away the entry marker, which was
    # informative, to avoid the capstone marker, which was not. A role earns a
    # shape when it picks out a minority; "you are one of 28 endings" is not
    # worth a shape.
    cap = max(1, int(n * 0.25))
    out = {}
    if len(entries) <= cap:
        for qid in entries:
            out[qid] = "square"
    if len(caps) <= cap:
        for qid in caps:
            out[qid] = "octagon"
    for q in quests:
        if q.get("optional"):
            out[q["id"]] = "circle"
    # Whatever role could not say, the GOAL can. A chapter of plain collect
    # quests still divides into the things a player is working toward - a
    # machine, a weapon, a piece of armour - and the stock they are made of.
    # That split is real, it is legible on the map, and unlike role it is
    # never uniform in a chapter worth reading.
    for q in quests:
        if q["id"] in out:
            continue
        names = " ".join(str(t.get("item") or "") for t in (q.get("tasks") or []))
        if any(k in names for k in _ITEM_TIER0):
            out[q["id"]] = gshape if gshape else "hexagon"
        elif any(k in names for k in _BULK):
            out[q["id"]] = "rsquare"

    # LAST RESORT: a chapter where every quest is the same KIND of thing.
    # A weapon mod is the clean example - Spartan Weaponry and Simply Swords
    # are twelve-plus quests that are all tier-0 goals, so the goal/material
    # split says the same word about every one of them and the chapter ships
    # as one repeated shape, which is the complaint this whole function
    # exists to answer. Depth is the information still available: how far into
    # the chapter a quest sits is real, a player can read it off the map, and
    # it is never uniform in a chapter that has any progression at all.
    proj = {q["id"]: (out.get(q["id"]) or _quest_shape(q, gshape))
            for q in quests}
    if len(quests) >= 8 and len(set(proj.values())) < 2:
        rank = _dep_levels(quests, ids)
        top = max(rank.values()) if rank else 0
        if top:
            band = [gshape or "circle", "hexagon", "octagon"]
            for q in quests:
                k = min(2, int(3 * rank[q["id"]] / (top + 1)))
                out[q["id"]] = band[k]
    return out


def _quest_shape(q: dict, group_shape: str) -> str:
    tasks = q.get("tasks") or []
    types = {t.get("type", "item") for t in tasks}
    if "checkmark" in types and len(tasks) == 1:
        return "diamond"
    if "kill" in types:
        return "gear"
    if len(tasks) >= 3:
        return "hexagon"
    if "dimension" in types:
        return "octagon"
    return group_shape


# The nine shapes FTB Quests 1.20.1 registers (QuestShape.java). "auto" keeps
# the derived per-group + per-task shapes above, which carry information
# (hexagon = multi-task, gear = kill, diamond = checkmark); forcing one shape
# book-wide is the user's right but not the default.
QUEST_SHAPES = ["circle", "square", "rsquare", "diamond", "pentagon",
                "hexagon", "heptagon", "octagon", "gear"]


def _forced_shape(opts: dict) -> str:
    """The user-picked book-wide quest shape, or "" for auto/unknown."""
    v = str((opts or {}).get("quest_shape", "auto") or "auto").strip().lower()
    return v if v in QUEST_SHAPES else ""


GROUP_STYLES = ["bold", "bracket", "stage", "rule"]


def _group_title(plain: str, code: str, idx: int, style: str) -> str:
    """Group headers in the sidebar. Good packs make these read as section
    dividers rather than just another line of text - Arcanum wraps them in
    brackets and numbers them by stage, which gives the sidebar a spine."""
    name = _txt(plain)
    if style == "bracket":
        return "&%s&l[ %s ]" % (code, name.upper())
    if style == "stage":
        return "&%s&l&n-=[ %s ]=-" % (code, name.upper())
    if style == "rule":
        return "&%s&l&n%s" % (code, name)
    return "&%s&l%s" % (code, name)


def _load_design_rules() -> dict:
    """Chapter-shape norms measured from real published quest books.

    Not taste. 568 chapter shape records across 189 mods, plus a full per-quest
    parse of a reference pack (1754 quests, 1955 dependency edges). Several of
    these numbers contradicted what this generator was doing.
    """
    try:
        return json.loads(
            (moddb_path() / "design_rules.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


_DESIGN = _load_design_rules()


def _design(path, default):
    cur = _DESIGN
    for k in path.split("."):
        cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is None:
            return default
    return cur


def shape_as_trunk(ids: list, gate=None) -> tuple:
    """Give a chapter the shape real packs actually have.

    Measured over a fully-parsed reference pack (1754 quests, 2110 dependency
    edges): 56.6% of quests are terminal LEAVES that nothing depends on, only
    18.2% are branch points, and a 60-quest chapter has a critical path of
    about 6. Real chapters are a short trunk with clusters hanging off it.

    This generator emitted tiered lattices where every quest gated the next
    tier - a rope. That is the structural cause of both the "checklist" feel
    and the long crossing dependency lines, and it is fixable in the graph
    before a single word is written.

    -> ({quest_id: [dep_id]}, {quest_id: is_leaf})

    The second map is SHAPE ONLY. Callers must not turn it into `optional` -
    that made `optional` a synonym for `leaf` and broke CHK-16/17; see the note
    at the caller in build_chapters.
    """
    n = len(ids)
    deps: dict = {}
    leaf: dict = {}
    if n == 0:
        return deps, leaf
    if n <= 3:
        for i, qid in enumerate(ids):
            deps[qid] = ([gate] if gate else []) if i == 0 else [ids[i - 1]]
            leaf[qid] = False
        return deps, leaf

    # Trunk length is what decides how WIDE each screen row is, because
    # layout_positions sets y = 2.2 * _dep_levels(...) - a dependency rank IS a
    # screen row. Leaves hang on the nearest earlier trunk node, so a rank holds
    # one trunk node plus the (step - 1) leaves before it: row width == step.
    #
    # The old spine_len = 2.9 * n**0.36 gave 10 trunk nodes for a 29-quest
    # chapter, hence step == 2, hence a comb. Measured on build_chapters output
    # from scan.pkl: median row width was exactly 2.0 on ALL 25 chapters and
    # only 2/25 cleared immersion_spec CHK-03 (narrow_row_fraction <= 0.60 AND
    # median row >= 3). Authored mean row width, derived as 1/cpf over 729
    # deduped chapters in shapes.json, is 3.4 at 10-19 quests and 5.0 at 20-34.
    # So size the trunk from the width we want instead of from n: step lands on
    # `want`, and depth stops at 7 rows instead of 11.
    # want == 4 all the way down: a 6-9 quest chapter given want 3 falls back to
    # a 3-wide comb (measured: n=9 -> rows 1,3,3,2, median 2.5, FAIL), whereas
    # want 4 collapses it to a single fan (rows 1,4,4) and passes.
    want = 4 if n < 20 else 5
    spine_len = max(1, min(6, n // want))
    step = max(1, n // spine_len)
    spine_ix = sorted({min(i * step, n - 1) for i in range(spine_len)})
    spine = set(spine_ix)

    for pos, i in enumerate(spine_ix):
        deps[ids[i]] = ([gate] if gate else []) if pos == 0 \
            else [ids[spine_ix[pos - 1]]]
        leaf[ids[i]] = False

    for i, qid in enumerate(ids):
        if i in spine:
            continue
        # hang each leaf on the nearest EARLIER trunk node, so lines stay
        # short and local instead of crossing the whole chapter
        anchor = spine_ix[0]
        for j in spine_ix:
            if j <= i:
                anchor = j
            else:
                break
        deps[qid] = [] if anchor == i else [ids[anchor]]
        leaf[qid] = True
    return deps, leaf


def _size_hierarchy(quests: list) -> None:
    """Root enlarged, satellites not (immersion_spec CHK-18).

    Size is a landmark system, and a landmark only works if there is ONE of
    it. The previous rule ("any quest with fanout >= 3 is a hub -> 1.5") made
    every trunk node a landmark once shape_as_trunk widened the rows: measured
    on the built the reference pack book (2026-08-29), 91 of 304 quests were
    enlarged (share 0.299 against the authored 0.20 cap) and 24 of 25
    size-using chapters had a TIE for largest, so "the big quest" pointed at
    nothing. Authored books enlarge the ENTRANCE - the quest a player reads
    first - and CHK-18 measures exactly that: the largest quest should be a
    chapter root, and enlarged quests should stay <= 20% of the book.

    So: the chapter's entry root is the unique largest at 1.5; the payoff
    (terminal of the longest path) gets a smaller 1.25 nod in chapters big
    enough to have a journey (>= 10, which also keeps the pooled enlarged
    share <= 0.20 on a book of small chapters); everything else - hubs
    included - stays default size."""
    ids = {q["id"] for q in quests}
    fanout = {}
    for q in quests:
        for d in (q.get("dependencies") or []):
            if d in ids:
                fanout[d] = fanout.get(d, 0) + 1
    # roots per the scorer: no dependency that resolves INSIDE the chapter
    # (a cross-chapter gate id does not make the first quest a non-root)
    roots = [q for q in quests
             if not any(d in ids for d in (q.get("dependencies") or []))]
    entry = max(roots, key=lambda q: (fanout.get(q["id"], 0),
                                      -quests.index(q))) if roots else None
    finals = [q for q in quests if q["id"] not in fanout]
    last = finals[-1]["id"] if finals else None
    for q in quests:
        if q.get("size"):
            continue
        if entry is not None and q["id"] == entry["id"]:
            q["size"] = 1.5
        elif (q["id"] == last and len(quests) >= 10
              and not (entry is not None and last == entry["id"])):
            q["size"] = 1.25


def _bounds(pos_or_quests):
    xs, ys = [], []
    if isinstance(pos_or_quests, dict):
        vals = pos_or_quests.values()
    else:
        vals = [(float(q.get("x", 0) or 0), float(q.get("y", 0) or 0)) for q in pos_or_quests]
    for x, y in vals:
        xs.append(x)
        ys.append(y)
    if not xs:
        return (-4, -4, 8, 8)
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _wheelify(quests: list, pos: dict, seed=None) -> None:
    """Place big satellite groups on a circle around their hub, like the
    reference books' rune and flower wheels. Any node with 5+ single-dep
    children gets them ringed (two rings past 10); everyone else keeps the
    layout's positions.

    Takes `seed` because it OVERWRITES whatever the layout decided for the
    members of a wheel, and on a small pack a chapter can be almost entirely
    one wheel - measured on an 18-jar pack, the layout produced 10 of 11
    quests in different places between two seeds and wheelify put all 11 back,
    so the book was identical every run however the layout was seeded. Where a
    member sits on a ring is arbitrary, which makes the ring the right place
    to spend variation: seeded, the wheel is rotated and its members dealt out
    in a different order, and it is still a clean wheel."""
    if not pos:
        return
    kids = {}
    for q in quests:
        deps = q.get("dependencies") or []
        if len(deps) == 1:
            kids.setdefault(deps[0], []).append(q["id"])
    for hub, ring in kids.items():
        if len(ring) < 5 or hub not in pos:
            continue
        if seed is not None:
            r2 = random.Random("wheel|%s|%s" % (hub, seed))
            ring = list(ring)
            r2.shuffle(ring)
            spin = r2.random() * math.tau
        else:
            spin = 0.0
        hx, hy = pos[hub]
        n1 = min(len(ring), 12)
        for k, qid in enumerate(ring):
            if k < n1:
                ang = (k / n1) * math.tau - math.pi / 2 + spin
                r = 2.6 if len(ring) <= 8 else 3.1
            else:
                ang = ((k - n1) / max(1, len(ring) - n1)) * math.tau + spin
                r = 4.6
            pos[qid] = (hx + r * math.cos(ang), hy + r * math.sin(ang))


def _chapter_backdrop(ck: str, rc: dict, scan: dict, used: set, n: int = 1) -> list:
    """The textures a chapter is dressed in, derived from what that chapter
    actually contains. -> up to `n` reslocs harvested this run, [] for none.

    Only the FIRST is registered in `used`. That one is the chapter's identity
    and no other chapter may wear it; the rest are accents, and two Create
    chapters sharing a cog is coherence, not repetition. Consuming all of them
    would exhaust a thin namespace in three chapters and push the rest onto the
    whole-pack fallback, which is the "same image everywhere" complaint again by
    another route.

    The old source was a ten-row keyword table over the group NAME, feeding five
    recycled advancement-screen backgrounds. Measured on three real packs it
    produced 4 distinct textures per book with the same stone.png on 16 of 25,
    11 of 25 and 24 of 28 chapters - which is what "it's the same image every
    chapter" means. Worse, the corpus uses those five backgrounds 0 times in
    62,478 chapter images, so it was not even the convention it imitated.

    Deriving from content instead means a Create chapter is dressed in brass and
    a Twilight Forest chapter in mossy castle brick, with no curated list - which
    is the only thing that can work on a pack nobody has looked at.

    The ladder exists because only ~10% of installed mods ship own-namespace
    opaque blocks: a chapter built out of wands and rings has no material of its
    own, so it borrows from a mod it touches, then from minecraft, which every
    instance has. `used` is book-scope, so two chapters never wear the same
    texture while an unused one is still on the shelf."""
    pool = (scan or {}).get("backdrops") or {}
    if not pool:
        return []
    # What the chapter IS. The icon is the one image a player sees before
    # opening it, so it outvotes; the opening quest sets the tone after that.
    hist: dict = {}

    def bump(item, w):
        ns = str(item or "").split(":", 1)[0].strip()
        if ns:
            hist[ns] = hist.get(ns, 0) + w
    bump(rc.get("icon"), 6)
    for i, q in enumerate(rc.get("quests") or []):
        w = 3 if i == 0 else 1
        for t in (q.get("tasks") or []):
            bump(t.get("item"), w)

    order = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))
    tiers = [ns for ns, _ in order[:1]]                       # the chapter's own mod
    tiers += sorted((ns for ns, _ in order[1:]),              # then any mod it touches,
                    key=lambda n: -len(pool.get(n) or ()))    # richest first
    tiers.append("minecraft")

    claimed: list = []

    def probe(cands: list) -> list:
        """The unworn head of `cands`, entered at a stable offset. -> [resloc]
        Deterministic, so the same scan always builds the same book; the linear
        probe means a repeat only happens once the list is genuinely exhausted.
        The offset is per-chapter, so two chapters of the same mod walk into the
        same namespace at different points and pick up different accents."""
        if not cands:
            return []
        i = int(ftb_id(ck), 16) % len(cands)
        out = []
        for k in range(len(cands)):
            c = cands[(i + k) % len(cands)]
            if c in used:           # never wear another chapter's identity,
                continue            # in any slot
            out.append(c)
            if len(out) >= n:
                break
        if out and not claimed:
            used.add(out[0])        # only the head, and only once per chapter
            claimed.append(out[0])
        return out

    # Three or more is what it takes for a namespace to read as a LOOK rather
    # than as one texture; below that a second chapter from the same mod has
    # nothing left to differ by. So rich namespaces are asked first, in order,
    # and thin ones only once none of them has anything unworn left.
    rich = [ns for ns in tiers if len(pool.get(ns) or ()) >= 3]
    thin = [ns for ns in tiers if ns not in rich and pool.get(ns)]
    # The last rung reaches for the whole pack. Nothing the chapter touches
    # ships usable art on 10 of 28 THE FORGOTTEN SMP chapters, because its
    # vanilla and furniture mods ship no opaque square block at all and this
    # scan sees only mods/ - the client jar is read elsewhere, so `minecraft`
    # can legitimately be empty. Off-theme, but a chapter with no material of
    # its own has no theme to be off, and the alternative was ten chapters
    # wearing the same stone.png, which is the complaint being fixed.
    ladder = [sorted(e[0] for e in pool[ns]) for ns in rich + thin]
    ladder.append(sorted({e[0] for v in pool.values() for e in v}))

    got: list = []
    for cands in ladder:
        # A thin namespace cannot fill a lavish chapter on its own, so keep
        # walking down the ladder for accents rather than tiling one brick
        # twenty times. Only the first rung that answers claims an identity.
        got += [c for c in probe(cands) if c not in got][:n - len(got)]
        if len(got) >= n:
            break
    return got


def _decor_images(ck, quests_or_pos, texs, lv, shape="", code="f", nq=0) -> list:
    """A chapter's art, in three layers. -> list of FTBQ chapter-image dicts

    `texs` are reslocs the caller has already sourced - harvested from a jar
    this run by _chapter_backdrop, or the vanilla last resort. Nothing here
    invents a path, because a path this function made up would render
    purple-and-black with nothing upstream having checked it. The one exception
    is the panel, and it comes from ftb-quests' own jar: if that texture were
    missing the mod could not draw a quest.

    `lv` is the AESTHETIC_LEVELS row. Each layer spends its own budget, so the
    levels differ by WHAT is drawn and not only how much: minimal draws nothing,
    balanced gets a tinted card and a couple of accents, decorated adds a tiled
    floor, lavish fills the ring. This is where "lavish" stops being a word in
    an AI prompt and becomes something the offline builder does.

    The old version drew ONE image: the sourced texture stretched over the whole
    chapter, 14 to 33 grid units across at up to 1:2.35. A 16px block texture at
    that size is not a backdrop, it is a magnified pixel - the second half of
    "it doesn't even fit". Tiling drops the median tile to 7.8 units at
    decorated and 4.5 at lavish, and NOTHING this function emits is off 1:1
    (measured across three packs, max aspect 1.000 at every level). The panel
    underneath is what carries the colour the stretch used to be asked for."""
    bx, by, bw, bh = _bounds(quests_or_pos)
    cx, cy = bx + bw / 2, by + bh / 2
    pw, ph = max(bw + 6.0, 10.0), max(bh + 6.0, 10.0)
    out: list = []

    def add(img, x, y, w, h, alpha, order, rot=0.0):
        out.append({
            "id": ftb_id("img", ck, img, "%d" % len(out)),
            "x": _Double(round(x, 2)), "y": _Double(round(y, 2)),
            "width": _Double(round(w, 2)), "height": _Double(round(h, 2)),
            "rotation": _Double(float(rot)), "image": img,
            "alpha": int(alpha), "order": int(order),
        })

    # 1. PANEL. Tinted with the SAME code as the chapter's sidebar title, so the
    # card and its name are one colour by construction - today those came off
    # two unrelated wheels. FTBQ multiplies the icon by the colour, and the
    # shape textures are pure white, so the tint lands exactly.
    # The card is SQUARE and sized to contain the chapter, not sized to it: the
    # shapes are gears, circles and pentagons, and a circle drawn into a 2.6:1
    # chapter is an ellipse - the same "doesn't even fit" the stretched backdrop
    # was. Containing it costs some tinted margin on a wide chapter, which at
    # alpha 100 of 255 is a faint medallion behind the quests. Distortion has no
    # equivalent excuse.
    if lv.get("panel"):
        sh = shape if shape in _SHAPE_PANELS else "rsquare"
        side = max(pw, ph)
        add("ftbquests:textures/shapes/%s/background.png" % sh,
            cx, cy, side, side, lv.get("panel_alpha", 100), -20)
        out[-1]["color"] = int(_CODE_HEX.get(code, "#ffffff")[1:], 16)

    if not texs:
        return out          # a pack with no harvested art still gets its card

    # A three-quest chapter cannot carry a lavish chapter's budget: twenty-four
    # images over three nodes buries them. Every layer is capped by what there
    # is to decorate, not by the level alone - which is why the measured lavish
    # average lands at 22.2 images per chapter and not the nominal 24.
    room = 3 + 2 * nq

    # 2. FIELD. Every tile is SQUARE, because every harvested texture is square
    # (the scan rejects w != h) and a square source drawn into a rectangle is
    # precisely the squash the user is looking at. So the grid is sized off the
    # budget and the tiles keep 1:1 even where that leaves the last row short -
    # a floor that stops is fine, a stretched brick is not. Two or three
    # textures alternate so it reads as a floor rather than as a repeat.
    nf = min(int(lv.get("field", 0)), max(0, room - len(out)))
    if nf:
        tile = math.sqrt(pw * ph / nf)
        cols = max(1, int(math.ceil(pw / tile)))
        fx, fy = cx - pw / 2, cy - ph / 2
        for k in range(nf):
            r, c = divmod(k, cols)
            if fy + tile * r >= cy + ph / 2:
                break
            add(texs[k % min(3, len(texs))],
                fx + tile * (c + 0.5), fy + tile * (r + 0.5),
                tile, tile, 55, -12)

    # 3. MOTIFS, opaque and at icon scale, ringed OUTSIDE the quests. Real packs
    # do not fade their art - alpha was 255 on 4,265 of 4,383 corpus images;
    # only backdrops are faint. The radius is the circumscribed one plus a
    # margin, so a motif cannot land under a quest node whatever the aspect
    # ratio. Rotations follow the corpus histogram (0 x3580, 22 x230, 90 x162,
    # 45 x133, -90 x111, 180 x73) rather than being sprinkled at random.
    nm = min(int(lv.get("motifs", 0)), max(0, room - len(out)))
    if nm:
        rad = math.hypot(pw, ph) / 2 + 2.5
        rots = (0.0, 0.0, 0.0, 0.0, 22.0, 45.0, 90.0, -90.0, 180.0)
        seed = int(ftb_id(ck), 16)
        for i in range(nm):
            ang = (i / nm) * math.tau - math.pi / 2
            t = texs[(seed + i) % len(texs)]
            add(t, cx + rad * math.cos(ang), cy + rad * math.sin(ang),
                2.0, 2.0, 255, 0, rots[(seed + i * 7) % len(rots)])
    return out


def _chapter_desc(rc: dict, lines_max: int = 4) -> list:
    """A chapter's intro text, as the list of lines FTBQ expects. -> list

    `lines_max` is the aesthetic level's line allowance; 0 (minimal) means a
    chapter opens on its quests and nothing else."""
    if lines_max <= 0:
        return []
    d = rc.get("description") or rc.get("subtitle") or ""
    if isinstance(d, str):
        d = [d] if d.strip() else []
    lines = [" ".join(str(x).split()) for x in d if str(x).strip()]
    return [l if l.startswith("&") else "&7" + l for l in lines][:lines_max]


def _dedupe_titles(out: list, scan: dict, warn) -> int:
    """Make every quest title in the book unique. -> how many were renamed

    Two mods shipping the same thing give the same quest the same name:
    farmersdelight:potato_crate and quark:potato_crate both come out as
    "Potato Crate", and a player looking at their completed list, or typing
    into the quest search, sees two entries that are indistinguishable. The
    same happens when an authored chain title collides with a derived one.

    Disambiguated by the thing that actually differs - which mod it belongs
    to - taken from the task's own namespace rather than the chapter, so it is
    right even for a quest sitting in a shared chapter. The FIRST use keeps
    the plain name: adding "(Farmer's Delight)" to both reads as noise when
    only one of them is the surprise.
    """
    seen: dict = {}
    renamed = 0
    for _slug, cd in out:
        for q in cd.get("quests") or []:
            t = str(q.get("title") or "").strip()
            if not t:
                continue
            if t not in seen:
                seen[t] = 1
                continue
            ns = ""
            for task in (q.get("tasks") or []):
                it = task.get("item")
                it = it.get("id") if isinstance(it, dict) else it
                if it and ":" in str(it):
                    ns = str(it).split(":", 1)[0]
                    break
            label = _mod_display_name(ns, scan) if ns else ""
            cand = "%s (%s)" % (t, label) if label and label.lower() not in t.lower() else ""
            if not cand or cand in seen:
                seen[t] += 1
                cand = "%s (%d)" % (t, seen[t])
            q["title"] = cand
            seen[cand] = 1
            renamed += 1
    if renamed:
        warn("renamed %d duplicate quest title(s)" % renamed)
    return renamed


def _soften_first_ask(out: list, cap: int = 2) -> int:
    """Make the book's opening ask small, whichever quest ends up first.

    The opening cost was pinned by hand once - 16 oak logs down to 1 - and
    then quietly came undone: adding a single front-matter page changed the
    dependency order, a different quest became the first the player can
    start, and the book opened by asking for twelve iron ingots. Pinning one
    row cannot hold, because WHICH row is first is decided downstream of
    where its number is written, by a graph that did not exist yet.

    So clamp by position in the graph, not by identity. Every item quest in
    chapter one that nothing precedes is an opening move, and an opening move
    should be something the player has already done. Quests deeper in the
    chapter keep the costs they were written with.
    """
    if not out:
        return 0
    _slug, cd = out[0]
    qs = cd.get("quests") or []
    if not qs:
        return 0
    ids = {q["id"] for q in qs}
    rank = _dep_levels(qs, ids)
    with_item = [q for q in qs
                 if any((t or {}).get("item") for t in (q.get("tasks") or []))]
    if not with_item:
        return 0
    first = min(rank.get(q["id"], 0) for q in with_item)
    softened = 0
    for q in with_item:
        if rank.get(q["id"], 0) != first:
            continue
        for t in (q.get("tasks") or []):
            if not (t or {}).get("item"):
                continue
            try:
                n = int(t.get("count", 1) or 1)
            except (TypeError, ValueError):
                n = 1
            if n > cap:
                t["count"] = cap
                softened += 1
    return softened


def build_chapters(doc: dict, warn, opts: dict | None = None):
    """Return (chapters, groups, extra). chapters = [(slug, chapter_dict)]."""
    opts = opts or {}
    preserve = opts.get("preserve_ids", False)
    alevel = _aesthetic(opts)
    raw_chapters = doc.get("chapters") or []

    reward_tables, table_map = build_reward_tables(doc, warn)

    def qid_for(cs, qk):
        if preserve and _HEXID_RE.match(qk):
            return _safe_hexid(qk)
        return ftb_id(cs + "/" + qk)

    # global quest-key -> ftb id, so cross-chapter dependencies resolve
    id_map: dict = {}
    seen_ck: set = set()
    for idx, rc in enumerate(raw_chapters):
        # A chapter key must be UNIQUE. A QA pass showed two chapters sharing
        # a plan id produce a byte-identical id space - same chapter id, image
        # id, quest, task and reward ids - and at load the second chapter's
        # dependencies resolve into the FIRST one, because FTB looks ids up
        # globally. Duplicate chapter TITLES collide the same way. Disambiguate
        # rather than trusting the model to number them.
        ck = str(rc.get("id") or rc.get("title") or ("chapter_%d" % idx))
        if ck in seen_ck:
            warn("duplicate chapter key %r - disambiguating" % ck)
            ck = "%s#%d" % (ck, idx)
        seen_ck.add(ck)
        rc["_ckey"] = ck        # the later loop must use the SAME key
        cs = "chapter/" + ck
        for q in rc.get("quests", []) or []:
            # An untitled, id-less quest used to key on the string "None", so
            # every one of them collapsed onto a single id and silently became
            # the same quest. Fall back to the chapter and position instead.
            qk = str(q.get("id") or q.get("title") or "")
            if not qk or qk == "None":
                qk = "%s/anon%d" % (ck, len(id_map))
            if qk in id_map:
                warn("duplicate quest id %r across chapters - disambiguating" % qk)
                qk = "%s/%s#%d" % (ck, qk, len(id_map))
            id_map[qk] = qid_for(cs, qk)
            q["_idkey"] = qk        # so _quest resolves the SAME key later

    # chapter groups
    styled = opts.get("style_chapters", False)
    use_groups = opts.get("groups", True)
    group_id: dict = {}
    group_pos: dict = {}
    groups: list = []
    if use_groups:
        for rc in raw_chapters:
            g = str(rc.get("group") or "").strip()
            if not g:
                continue
            # Key the id off the PLAIN name. Reading a book back gives styled
            # titles ("&e&lVanilla"); hashing those made the group id change on
            # every editor round-trip, orphaning chapters from their group.
            plain = re.sub(r"&[0-9a-fk-orA-FK-OR]", "", g).strip() or g
            if plain in group_id:
                continue
            gid = ftb_id("group/" + plain)
            group_id[g] = gid
            group_id[plain] = gid
            group_pos[g] = group_pos[plain] = len(groups)
            gcode = _theme_for(plain, len(groups))[0]
            groups.append({"id": gid,
                           "title": _group_title(plain, gcode, len(groups),
                                                 opts.get("group_style", "bold"))
                                    if styled else _txt(plain)})

    used: set = set()
    # Book-scope, so chapter N knows what the first N-1 are already wearing.
    # Threaded through opts rather than the signature: the GUI, the AI path and
    # three test harnesses all call build_chapters, and none of them should have
    # to change to get art that varies.
    scan = opts.get("_scan") or {}
    used_tex: set = set()
    out: list = []
    banner_texts: dict = {}
    for idx, rc in enumerate(raw_chapters):
        ck = rc.get("_ckey") or str(rc.get("id") or rc.get("title") or ("chapter_%d" % idx))
        cs = "chapter/" + ck
        raw_quests = rc.get("quests", []) or []
        quests = [_quest(q, cs, id_map, warn, table_map) for q in raw_quests]

        if opts.get("auto_chain") and not any(q.get("dependencies") for q in quests):
            order = sorted(range(len(quests)),
                           key=lambda i: (float(raw_quests[i].get("x", 0) or 0),
                                          float(raw_quests[i].get("y", 0) or 0)))
            for pos in range(1, len(order)):
                quests[order[pos]]["dependencies"] = [quests[order[pos - 1]]["id"]]
            if len(order) > 1:
                warn("chapter %r: auto-chained %d quests" % (ck, len(order)))

        if opts.get("xp_rewards"):
            base = int((50 + 25 * idx) * opts.get("reward_mult", 1.0))
            for q in quests:
                if not q.get("rewards"):
                    q["rewards"] = [{"id": ftb_id(q["id"], "xpreward"), "type": "xp", "xp": base}]

        # ---- layout ----
        style = opts.get("layout", "line")
        jitter = 0.15 + 1.4 * opts.get("creativity", 0.3)
        # Layout gets its OWN seed: cs must stay clean because quest ids hash
        # from it, and a regenerate that renumbers every quest wipes the
        # player's completed set. Positions are cosmetic, so they may vary.
        pos = layout_positions(quests, style, jitter,
                               cs + "/" + str(_run_seed(opts)))
        if pos:
            _wheelify(quests, pos, cs + "/" + str(_run_seed(opts)))
            _declutter(pos, min_d=1.6)
            # LAST, after the wheels and the declutter. Shortening inside
            # layout_positions alone achieved nothing here: both passes above
            # move quests with no view of the dependency graph, so they put the
            # outliers straight back (measured: SecurityCraft's longest line
            # 19.5 -> 30.7 with the pull applied only upstream of them).
            _shorten_links(pos, quests, {q["id"] for q in quests})
            for q in quests:
                if q["id"] in pos:
                    q["x"] = _Double(round(pos[q["id"]][0], 3))
                    q["y"] = _Double(round(pos[q["id"]][1], 3))

        # ---- per-group theme (colour / shape / emblem) ----
        # index is STABLE per group so every chapter in a group looks the same
        grp_name = str(rc.get("group") or "").strip()
        grp = grp_name or str(rc.get("title") or ck)
        theme_idx = group_pos.get(grp_name, idx)
        code, gshape, emblem, backdrop = _theme_for(grp, theme_idx)
        # Quest shape picker: a forced shape overrides the derived theme shape
        # everywhere — the theme default, every per-quest shape (including ones
        # an imported/remixed chapter already carries), and the chapter default
        # written below, so it applies even with chapter styling off.
        forced = _forced_shape(opts)
        if forced:
            gshape = forced
        raw_title = str(rc.get("title", ck))
        if styled and not _has_fmt_code(raw_title):
            # _txt() first so a stray "&" in the name ("Arts & Alchemy") can't
            # eat the colour code we're about to add
            title = "&%s&l%s" % (code, _txt(raw_title))
        else:
            title = raw_title
        if forced:
            for q in quests:
                q["shape"] = forced
        if styled:
            _size_hierarchy(quests)
            # minimal means minimal: every node keeps the chapter's default
            # shape, so the map reads as one uniform grid.
            if not forced and alevel["shapes"]:
                roles = _role_shapes(quests, gshape)
                for q in quests:
                    if not q.get("shape"):
                        q["shape"] = (roles.get(q["id"])
                                      or _quest_shape(q, gshape))

        slug = slugify(rc.get("filename") or (raw_title if _HEXID_RE.match(ck) else ck))
        while slug in used:
            slug += "_"
        used.add(slug)

        images = []
        for ri in rc.get("images", []) or []:
            images.append({
                "id": ri.get("id") or ftb_id("img", ck, str(ri)),
                "x": _Double(float(ri.get("x", 0.0))),
                "y": _Double(float(ri.get("y", 0.0))),
                "width": _Double(float(ri.get("width", 2.0))),
                "height": _Double(float(ri.get("height", 2.0))),
                "rotation": _Double(float(ri.get("rotation", 0.0))),
                "image": ri.get("image", ""),
                "alpha": int(ri.get("alpha", 255)),
                "order": int(ri.get("order", 0)),
            })
        if opts.get("decor_art") and not images:
            # Derived from the chapter's own contents. VANILLA_BACKDROPS is now
            # only the floor for a scan with no harvested textures at all - an
            # old cached scan, or a jar-free instance - because a bare chapter
            # is a worse regression than a repeated one.
            want = int(alevel.get("field", 0)) + int(alevel.get("motifs", 0))
            texs = _chapter_backdrop(ck, rc, scan, used_tex, max(1, want)) \
                or ([VANILLA_BACKDROPS.get(backdrop, VANILLA_BACKDROPS["stone"])]
                    if want else [])
            # gshape and code are the chapter's own shape and title colour, so
            # the panel matches the name above it instead of coming off a
            # separate wheel.
            images = _decor_images(ck, pos or quests, texs, alevel,
                                   gshape if styled else "", code, len(quests))

        cid = _safe_hexid(ck) if (preserve and _HEXID_RE.match(ck)) else ftb_id(cs)
        out.append((slug, {
            "id": cid,
            "group": group_id.get(grp_name, ""),
            "order_index": idx,
            "filename": slug,
            "title": _txt(title),
            "icon": _chapter_icon(rc, quests),
            "default_quest_shape": gshape if (styled or forced) else "",
            "default_hide_dependency_lines": style in LAYOUT_HIDE_LINES,
            "images": images,
            "quests": quests,
            "quest_links": [],
            # SUBTITLE, not description. The comment that used to sit here
            # said FTBQ shows the description above the quests - it does not,
            # and never has for chapters: Chapter.class contains no
            # "description" string at all, only "subtitle" (verified against
            # the ftb-quests-forge-2001.4.22 jar this instance runs). Every
            # intro written since yesterday was being serialised into a field
            # the game silently discards - the third silently-dead layer this
            # text has passed through, after not being generated and then not
            # being emitted. Quests DO have a description field; chapters only
            # have this.
            **({"subtitle": _chapter_desc(rc, alevel["desc_lines"])}
               if _chapter_desc(rc, alevel["desc_lines"]) else {}),
        }))
    # A dependency cycle loads SILENTLY and locks every quest in it forever:
    # FTB validates dependencies only in its in-game editor, never on load.
    # The offline builder does not create cycles, but a model reply can - and
    # this book is regenerated with AI every time.
    _cut = break_dep_cycles(out, warn)
    if _cut:
        warn("  cut %d dependency edge(s) that would have formed a cycle" % _cut)
    _soften_first_ask(out)
    _dedupe_titles(out, scan, warn)
    return out, groups, {"banners": {}, "reward_tables": reward_tables}


def _patch_data_snbt(path: Path, book: dict, log):
    """Update title / icon / progression_mode in an existing or new data.snbt."""
    try:
        data = snbt_loads(path.read_text(encoding="utf-8")) if path.exists() else \
            snbt_loads(DATA_SNBT_DEFAULT)
        if not isinstance(data, dict):
            raise SNBTError("not a compound")
    except Exception:
        data = snbt_loads(DATA_SNBT_DEFAULT)
    if book.get("title"):
        data["title"] = _txt(book["title"])
    if book.get("icon"):
        data["icon"] = book["icon"]
    if book.get("progression_mode") in ("linear", "flexible"):
        data["progression_mode"] = book["progression_mode"]
    path.write_text(snbt_dumps(data) + "\n", encoding="utf-8")
    log("  updated data.snbt")


def minecraft_running() -> bool:
    """Is a Minecraft java process alive right now?"""
    # A SECOND definition of this function used to sit 8,700 lines below and
    # silently win, and it only looked for javaw.exe - so the guard protecting
    # a live book was blind to every launcher that runs java.exe instead. The
    # duplicate is deleted; its one good idea, CREATE_NO_WINDOW, is folded in
    # here so the check does not flash a console on a windowed build.
    _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        for exe in ("javaw.exe", "java.exe"):
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq " + exe, "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=_flags).stdout
            if exe in out.lower():
                return True
        return False
    except Exception:
        return False                     # cannot tell - do not block the write


def write_snbt(quests_dir: Path, chapters: list, groups: list, backup: bool, log,
               book: dict | None = None, reward_tables: list | None = None,
               confirm_shrink=None, allow_while_running: bool = False) -> None:
    """confirm_shrink(old_n, new_n) -> bool is asked before a write that would
    replace a much larger book with a much smaller one. A one-chapter themed
    generate silently wiping a 25-chapter book is not something to do quietly.

    Refuses to write while Minecraft is running: FTB Quests keeps the book in
    memory and saves it back over the files, so a write under a live game is
    silently destroyed - and once, mid-write, the game read a half-deleted
    state as the real book and saved THAT back, erasing everything. The guard
    belongs here, in the one function every write goes through, not in the
    memory of whoever happens to be pressing Generate.
    """
    # Narrowed to the destinations the danger actually applies to. FTB Quests
    # only holds - and only saves back over - the book under a loaded
    # instance's config/ftbquests. A write anywhere else (a scratch folder, an
    # export, the CLI smoke test) is not something the game will ever touch,
    # and refusing it bought no safety while blocking the one check that
    # proves the frozen exe works on a machine where the game is open.
    _live = "config" in {x.lower() for x in Path(quests_dir).parts}         and "ftbquests" in {x.lower() for x in Path(quests_dir).parts}
    if _live and not allow_while_running and minecraft_running():
        raise RuntimeError(
            "Minecraft is RUNNING. FTB Quests will overwrite anything written "
            "now (and can erase the book entirely). Close the game, then "
            "generate again.")
    quests_dir.mkdir(parents=True, exist_ok=True)
    chapters_dir = quests_dir / "chapters"
    new_slugs = [slug for slug, _ in chapters]

    existing = len(list(chapters_dir.glob("*.snbt"))) if chapters_dir.exists() else 0
    if (confirm_shrink and existing >= 5 and len(new_slugs) * 3 < existing
            and not confirm_shrink(existing, len(new_slugs))):
        log("  write cancelled — kept the existing %d chapters" % existing)
        raise RuntimeError("cancelled: kept the existing %d-chapter book" % existing)

    if backup and chapters_dir.exists() and any(chapters_dir.iterdir()):
        bak = quests_dir / ("chapters.backup-%s" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copytree(chapters_dir, bak)
        log("  backed up existing chapters/ -> %s" % bak.name)
    chapters_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = quests_dir / MANIFEST_NAME
    try:
        prev = json.loads(manifest_path.read_text(encoding="utf-8")).get("chapters", [])
    except Exception:
        prev = []
    # Sweep the FOLDER, not just the manifest. The manifest only exists in a
    # book this tool wrote, so pointing at a hand-made or pack-shipped book -
    # the single most common real use - left every old chapter in place while
    # the app reported a replacement. A release gate measured the result: 194
    # items from 19 mods that are not even installed, surviving into a
    # "replaced" book, and the wizard saying "This will REPLACE your
    # 11-chapter book" while 8 of the 11 lived on.
    #
    # Safe to delete because the backup above already copied chapters/ to
    # chapters.backup-<timestamp>, and confirm_shrink still asks before a big
    # shrink. Replacing a book has to mean replacing it.
    on_disk = sorted(f.stem for f in chapters_dir.glob("*.snbt"))
    for old in sorted(set(prev) | set(on_disk)):
        if old not in new_slugs:
            stale = chapters_dir / (old + ".snbt")
            if stale.exists():
                stale.unlink()
                log("  removed stale chapters/%s.snbt" % old)

    cg = quests_dir / "chapter_groups.snbt"
    if groups:
        cg.write_text(snbt_dumps({"chapter_groups": groups}) + "\n", encoding="utf-8")
        log("  wrote chapter_groups.snbt  (%d groups)" % len(groups))
    elif not cg.exists():
        cg.write_text(CHAPTER_GROUPS_EMPTY, encoding="utf-8")
        log("  wrote chapter_groups.snbt")

    data = quests_dir / "data.snbt"
    if book and any(book.get(k) for k in ("title", "icon", "progression_mode")):
        _patch_data_snbt(data, book, log)
    elif not data.exists():
        data.write_text(DATA_SNBT_DEFAULT, encoding="utf-8")
        log("  wrote data.snbt (default)")

    for slug, chapter in chapters:
        (chapters_dir / (slug + ".snbt")).write_text(snbt_dumps(chapter) + "\n", encoding="utf-8")
        log("  wrote chapters/%s.snbt  (%d quests)" % (slug, len(chapter["quests"])))

    if reward_tables:
        rt_dir = quests_dir / "reward_tables"
        rt_dir.mkdir(exist_ok=True)
        try:
            prev_rt = json.loads(manifest_path.read_text(encoding="utf-8")).get("reward_tables", [])
        except Exception:
            prev_rt = []
        new_rt = [slug for slug, _ in reward_tables]
        for old in prev_rt:
            if old not in new_rt and (rt_dir / (old + ".snbt")).exists():
                (rt_dir / (old + ".snbt")).unlink()
        for slug, table in reward_tables:
            (rt_dir / (slug + ".snbt")).write_text(snbt_dumps(table) + "\n", encoding="utf-8")
            log("  wrote reward_tables/%s.snbt  (%d entries)" % (slug, len(table["rewards"])))
        manifest_path.write_text(json.dumps(
            {"chapters": new_slugs, "reward_tables": new_rt}, indent=2), encoding="utf-8")
    else:
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            m = {}
        m["chapters"] = new_slugs
        manifest_path.write_text(json.dumps(m, indent=2), encoding="utf-8")


# ========================================================================== #
#  1b. Repair + reverse-convert existing quest files
# ========================================================================== #

def _fix_text_field(obj: dict, key, changes: list, where: str):
    v = obj.get(key)
    if isinstance(v, str) and "&" in v:
        obj[key] = _txt(v)
        changes.append("%s: escaped '&'" % where)
    elif isinstance(v, list):
        nv = [_txt(x) if isinstance(x, str) else x for x in v]
        if nv != v:
            obj[key] = nv
            changes.append("%s: escaped '&'" % where)


def repair_quests(quests_dir: Path, backup: bool, log) -> dict:
    """Parse every chapters/*.snbt, normalise, rewrite. Returns a summary dict."""
    chapters_dir = quests_dir / "chapters"
    files = sorted(chapters_dir.glob("*.snbt")) if chapters_dir.is_dir() else []
    if not files:
        raise ValueError("no chapters/*.snbt found in %s" % quests_dir)

    if backup:
        bak = quests_dir / ("chapters.backup-%s" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copytree(chapters_dir, bak)
        log("  backed up chapters/ -> %s" % bak.name)

    parsed = []
    for f in files:
        try:
            d = snbt_loads(f.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                raise SNBTError("top level is not a compound")
            parsed.append((f, d))
        except Exception as e:
            log("  ! %s failed to parse: %s  (left untouched)" % (f.name, e))

    changes = []
    # pass 1: ids
    all_qids = set()
    for f, ch in parsed:
        if not (isinstance(ch.get("id"), str) and _HEXID_RE.match(ch["id"])):
            ch["id"] = ftb_id("repair-chapter", f.stem)
            changes.append("%s: new chapter id" % f.name)
        for q in ch.get("quests") or []:
            if not (isinstance(q.get("id"), str) and _HEXID_RE.match(str(q.get("id")))):
                q["id"] = ftb_id("repair-quest", f.stem, str(q.get("title") or id(q)))
                changes.append("%s: new quest id" % f.name)
            all_qids.add(str(q["id"]).upper())
            for coll in ("tasks", "rewards"):
                for e in q.get(coll) or []:
                    if isinstance(e, dict) and not (isinstance(e.get("id"), str)
                                                    and _HEXID_RE.match(str(e.get("id")))):
                        e["id"] = ftb_id("repair", f.stem, coll, str(e))
                        changes.append("%s: new %s id" % (f.name, coll[:-1]))

    # pass 2: text + deps + required fields
    for f, ch in parsed:
        _fix_text_field(ch, "title", changes, "%s title" % f.name)
        _fix_text_field(ch, "subtitle", changes, "%s subtitle" % f.name)
        ch.setdefault("order_index", 0)
        ch.setdefault("quest_links", [])
        ch.setdefault("filename", f.stem)
        for q in ch.get("quests") or []:
            _fix_text_field(q, "title", changes, "%s quest" % f.name)
            _fix_text_field(q, "subtitle", changes, "%s quest" % f.name)
            _fix_text_field(q, "description", changes, "%s quest" % f.name)
            deps = q.get("dependencies")
            if isinstance(deps, list):
                good = [d for d in deps if str(d).upper() in all_qids]
                if len(good) != len(deps):
                    changes.append("%s: dropped %d dead dependency" % (f.name, len(deps) - len(good)))
                if good:
                    q["dependencies"] = good
                else:
                    q.pop("dependencies", None)
            if not q.get("tasks"):
                q["tasks"] = [{"id": ftb_id("repair", f.stem, "task", str(q.get("id"))),
                               "type": "checkmark"}]
                changes.append("%s: added a checkmark task to an empty quest" % f.name)
            for t in q.get("tasks") or []:
                _fix_text_field(t, "title", changes, "%s task" % f.name)

    for f, ch in parsed:
        f.write_text(snbt_dumps(ch) + "\n", encoding="utf-8")

    # data.snbt / chapter_groups.snbt text fixes
    for name in ("data.snbt", "chapter_groups.snbt"):
        p = quests_dir / name
        if not p.exists():
            continue
        try:
            d = snbt_loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log("  ! %s failed to parse: %s" % (name, e))
            continue
        before = snbt_dumps(d)
        if name == "data.snbt" and isinstance(d, dict):
            _fix_text_field(d, "title", changes, "data.snbt")
        if name == "chapter_groups.snbt" and isinstance(d, dict):
            for g in d.get("chapter_groups") or []:
                if isinstance(g, dict):
                    _fix_text_field(g, "title", changes, "group")
        if snbt_dumps(d) != before:
            p.write_text(snbt_dumps(d) + "\n", encoding="utf-8")
            log("  rewrote %s" % name)

    log("repair: %d file(s) parsed, %d change(s)" % (len(parsed), len(changes)))
    for c in changes[:80]:
        log("  - " + c)
    return {"files": len(parsed), "changes": len(changes)}


def quests_to_doc(quests_dir: Path) -> dict:
    """Reverse-convert existing chapters/*.snbt into the design-JSON shape,
    keeping the real 16-hex ids so progress survives a round-trip."""
    chapters_dir = quests_dir / "chapters"
    files = sorted(chapters_dir.glob("*.snbt")) if chapters_dir.is_dir() else []
    id_title = {}
    raw = []
    for f in files:
        try:
            d = snbt_loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        raw.append((f, d))
        for q in d.get("quests") or []:
            if isinstance(q.get("id"), str):
                id_title[str(q["id"]).upper()] = q.get("title") or str(q["id"])
    raw.sort(key=lambda fd: (_int_of(fd[1].get("order_index", 999), 999), fd[0].name))

    groups = {}
    gp = quests_dir / "chapter_groups.snbt"
    if gp.exists():
        try:
            gd = snbt_loads(gp.read_text(encoding="utf-8"))
            for g in (gd or {}).get("chapter_groups", []) if isinstance(gd, dict) else []:
                if isinstance(g, dict) and g.get("id"):
                    groups[str(g["id"]).upper()] = g.get("title", "")
        except Exception:
            pass

    chapters = []
    for f, d in raw:
        qs = []
        for q in d.get("quests") or []:
            deps = [str(x).upper() for x in (q.get("dependencies") or []) if str(x).upper() in id_title]
            tasks, rewards = [], []
            for t in q.get("tasks") or []:
                if not isinstance(t, dict):
                    continue
                tt = t.get("type", "item")
                if tt == "item":
                    it = t.get("item")
                    it = it.get("id") if isinstance(it, dict) else it
                    tasks.append({"type": "item", "item": it,
                                  "count": int(t.get("count", 1) or 1)})
                elif tt == "checkmark":
                    tasks.append({"type": "checkmark", "title": t.get("title", "")})
                elif tt == "kill":
                    tasks.append({"type": "kill", "entity": t.get("entity"),
                                  "value": int(t.get("value", 1) or 1)})
                elif tt == "dimension":
                    tasks.append({"type": "dimension", "dimension": t.get("dimension")})
                elif tt == "advancement":
                    tasks.append({"type": "advancement", "advancement": t.get("advancement")})
                else:
                    # PASS THROUGH what we do not recognise, rather than
                    # dropping it. This whitelist knew five task types, so
                    # opening the Rewards & Tasks tab and saving silently
                    # deleted every OTHER kind - all 23 structure tasks in a
                    # the reference pack book, and the same on every pack tested.
                    # The user is editing rewards; nothing about that should
                    # cost them a quest's objective.
                    #
                    # A reader that understands a subset must copy the rest
                    # verbatim, because the alternative is silent data loss
                    # that looks like a successful save.
                    tasks.append({k: v for k, v in t.items() if k != "id"})
            for r in q.get("rewards") or []:
                if not isinstance(r, dict):
                    continue
                rt = r.get("type", "item")
                if rt == "item":
                    it = r.get("item")
                    it = it.get("id") if isinstance(it, dict) else it
                    rewards.append({"type": "item", "item": it, "count": int(r.get("count", 1) or 1)})
                elif rt in ("xp", "xp_levels"):
                    rewards.append({"type": rt, "xp": int(r.get("xp", r.get("xp_levels", 100)) or 100)})
                else:
                    # Same reasoning as tasks: loot-table, command and choice
                    # rewards are real and were being erased on save.
                    rewards.append({k: v for k, v in r.items() if k != "id"})
            qd = {"id": str(q.get("id")), "title": q.get("title", ""),
                  "x": float(q.get("x", 0) or 0), "y": float(q.get("y", 0) or 0),
                  "tasks": tasks}
            if q.get("subtitle"):
                qd["subtitle"] = q["subtitle"]
            # optional marks a side quest. It was not read back, so a save
            # turned 225 optional quests into required ones - which changes
            # what the player must do to finish a chapter, not just how it
            # looks.
            if q.get("optional"):
                qd["optional"] = True
            desc = q.get("description")
            if desc:
                qd["description"] = desc if isinstance(desc, list) else [desc]
            if deps:
                qd["dependencies"] = deps
            if rewards:
                qd["rewards"] = rewards
            qs.append(qd)
        cd = {"id": str(d.get("id") or f.stem), "title": d.get("title", f.stem),
              "filename": d.get("filename") or f.stem, "quests": qs}
        if d.get("icon"):
            cd["icon"] = d["icon"]
        # The chapter intro was not read back either, so a save erased all 11
        # of them - the text that says what the mod IS. In the doc format the
        # intro lives under "description"; on disk it lives under "subtitle",
        # because that is the only intro field Chapter.class actually reads.
        # Accept both spellings here: "subtitle" from books this app now
        # writes, "description" from older output where the text sat in a
        # field the game ignored - reading it back is how that text gets
        # RESCUED into the live field on the next save instead of lost.
        if d.get("subtitle"):
            cd["description"] = d["subtitle"]
        elif d.get("description"):
            cd["description"] = d["description"]
        if d.get("group") and str(d["group"]).upper() in groups:
            cd["group"] = groups[str(d["group"]).upper()]
        chapters.append(cd)
    return {"chapters": chapters}


# ========================================================================== #
#  2. Mod scanning
# ========================================================================== #

CATEGORY_KEYWORDS = {
    "tech": ["create", "mekanism", "immersive", "thermal", "pneumaticcraft", "industrial",
             "modernindustrial", "ae2", "appliedenergistics", "refinedstorage", "powah",
             "flux", "gadget", "securitycraft", "computercraft", "engineer", "machine",
             "factory", "automation", "steam", "electric", "energy", "reactor",
             "tinker", "railcraft", "logistic"],
    "magic": ["botania", "goety", "occultism", "ars_", "arsnouveau", "bloodmagic", "mahou",
              "eidolon", "forbidden", "enigmatic", "irons_spellbooks", "reliquary",
              "magic", "arcane", "spell", "wizard", "witch", "sorcer", "rune",
              "alchemy", "mana", "eldritch", "necro", "summon", "ritual", "malum",
              "hexerei", "apotheosis"],
    "world": ["twilightforest", "biomesoplenty", "aether", "cataclysm", "l_enders", "undergarden",
              "tropicraft", "betterend", "betternether", "alexscaves", "deeperdarker",
              "dimension", "biome", "terra", "oh_the_biomes", "dungeon", "structure",
              "cave", "nether", "end_", "exploration", "atlas", "wilds"],
    "mob": ["alexsmobs", "mowziesmobs", "mutantmonsters", "illagerinvasion", "hostility",
            "graveyard", "creatures", "critters", "mobs", "monster", "beast", "dragon",
            "boss", "illage", "spillage", "zombie", "undead"],
    "food": ["farmersdelight", "croptopia", "letsdo", "delightful", "brewery", "pizza", "cuisine",
             "farm", "crop", "food", "cook", "kitchen", "harvest", "garden", "bakery",
             "vinery", "beekeep", "agricult"],
    "utility": ["jei", "jade", "waystones", "curios", "carryon", "sophisticated", "traveler",
                "backpack", "storage", "chunk", "map", "corpse", "grave", "inventory",
                "sorting", "search", "tooltip", "minimap", "clipboard", "modernfix",
                "performance", "optimiz", "library", "lib", "api", "core"],
    "decor": ["chipped", "decorative", "supplementaries", "handcrafted", "macaw", "mcw",
              "furniture", "rechiseled", "athena", "deco", "paintings", "chairs",
              "build", "aesthetic", "ornament", "lamp", "window", "roof", "medieval_deco"],
}
# Libraries, APIs and client-side tweaks: real mods, but they add no content a
# player can be sent after, so they never get a chapter or a quest goal.
LIBRARY_MODS = {
    "geckolib", "architectury", "cloth_config", "cloth-config", "clothconfig",
    "fabric_api", "forgeconfigapiport", "kotlinforforge", "collective",
    "resourcefullib", "resourcefulconfig", "resourcefullibrary", "puzzleslib",
    "balm", "bookshelf", "placebo", "curios", "caelus", "cardinal_components",
    "creativecore", "framework", "mixinextras", "moonlight", "prism",
    "supermartijn642corelib", "supermartijn642configlib", "terrablender",
    "athena", "yungsapi", "jade", "jei", "modernfix", "ferritecore", "lazydfu",
    "starlight", "sodium", "embeddium", "rubidium", "oculus", "iris", "canary",
    "memoryleakfix", "entityculling", "betterfpsdist", "immediatelyfast",
    "sereneseasons_api", "blueprint", "kiwi", "flywheel", "ponder", "registrate",
    "patchouli", "guideme", "cupboard", "fantasyfurniture_api", "doapi",
    "modifiers", "sophisticatedcore", "titanium", "codechickenlib", "mantle",
    "trinkets", "playeranimator", "azurelib", "smartbrainlib", "chunkloaders",
    # its own editor items are not player goals, and it ships
    # ftbquests:missing_item - the "unresolved id" placeholder
    "ftbquests", "ftblibrary", "ftbteams", "ftbranks",
}


def _load_unquestable() -> set:
    """Mods research PROVED have nothing to quest. -> {modid}

    A researched chain file with an empty chain is a verified finding, not a
    gap: Curios API registers zero items because it is a slot API, InControl
    is a spawn-rules engine driven by config, and Champions adds mob affixes.
    Left in, the generator manufactures filler quests from whatever incidental
    ids such a mod happens to carry.

    Data-driven on purpose - the hardcoded LIBRARY_MODS list below could never
    keep up, and it already missed both InControl and Champions.
    """
    out = set()
    d = moddb_path() / "chains_research"
    if not d.is_dir():
        return out
    # A dedicated audit file lists mods checked and found to have nothing to
    # quest - JEI, Jade, JourneyMap and the rest are UI and scripting tools.
    try:
        audit = json.loads((d / "_utility_audit.json").read_text(encoding="utf-8"))
        for row in (audit if isinstance(audit, list) else []):
            if isinstance(row, dict) and row.get("questable") is False:
                out.add(str(row.get("mod") or "").lower())
    except Exception:
        pass
    for f in d.glob("*.json"):
        if f.name.startswith("_"):
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        # An empty chain ALONE is not proof - a file can be half-written. Pair
        # it with the researcher saying so explicitly.
        if not (doc.get("chain") or []) and "NO QUESTABLE CONTENT" in                 str(doc.get("notes") or "").upper():
            out.add(str(doc.get("mod") or f.stem).lower())
    return out


_UNQUESTABLE = _load_unquestable()


def _loads_lenient(raw):
    """Parse JSON the way Minecraft does, not the way json.loads does.

    Minecraft reads lang and data files with a lenient Gson that tolerates
    // comments and trailing commas. dimdungeons ships // comments in its
    en_us.json, so json.loads threw, the exception was swallowed, and the mod
    contributed ZERO lang names to the scan - 313 keys lost in silence.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except Exception:
        pass
    # strip // comments that are not inside a string, then trailing commas
    out, in_str, esc, i = [], False, False, 0
    while i < len(raw):
        c = raw[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and i + 1 < len(raw) and raw[i + 1] == "/":
            while i < len(raw) and raw[i] != "\n":
                i += 1
            continue
        elif c == "/" and i + 1 < len(raw) and raw[i + 1] == "*":
            j = raw.find("*/", i + 2)
            i = (j + 2) if j >= 0 else len(raw)
            continue
        else:
            out.append(c)
        i += 1
    txt = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try:
        return json.loads(txt)
    except Exception:
        return None


def _load_conflicts() -> list:
    """Rules that only apply when two mods are installed together.

    Some facts are invisible to a per-mod scan by construction. Create
    Connected's copycat blocks silently MIGRATE into Copycats+ blocks when
    both are present, so a quest naming a create_connected:copycat_* item can
    never complete - the item becomes the copycats: version in the player's
    hand. Neither jar mentions the other.
    """
    try:
        d = json.loads((moddb_path() / "mod_conflicts.json")
                       .read_text(encoding="utf-8"))
        return list(d.get("shadowed") or [])
    except Exception:
        return []


_CONFLICTS = _load_conflicts()


def drop_shadowed(scan: dict) -> int:
    """Remove ids that a co-installed mod silently replaces. -> count dropped."""
    present = set(scan.get("items") or {})
    n = 0
    for rule in _CONFLICTS:
        if not set(rule.get("when") or ()) <= present:
            continue
        pre = str(rule.get("drop_prefix") or "")
        if not pre:
            continue
        mid = pre.split(":", 1)[0]
        keep = {i for i in (scan["items"].get(mid) or ()) if not i.startswith(pre)}
        n += len(scan["items"].get(mid) or ()) - len(keep)
        scan["items"][mid] = keep
    return n


def library_has_real_items(mod_id: str, scan: dict) -> bool:
    """Does this library actually ship anything a player can get? -> bool

    A QA pass found that explicitly picking a library built a whole chapter
    from its dev placeholders - Kiwi's read "Item2", "Item3", "Recover Item4",
    GeckoLib's said "Turn on rain to see the fertilizer model!" - because the
    gate keyed on INTENT ("you picked it, so you meant it") rather than on
    whether the mod has content. Both ship zero data/ entries, so every one of
    those quests is uncompletable.

    Intent was the wrong axis. A library that genuinely ships obtainable items
    is fine to quest; one whose items have no recipe and no loot is not,
    however deliberately it was chosen.
    """
    ids = scan.get("items", {}).get(mod_id) or ()
    craft = scan.get("craftable", {}).get(mod_id) or set()
    return bool(craft) and len(craft) >= max(2, len(ids) // 20)


def is_curated_library(mod_id: str) -> bool:
    """Was this mod judged unquestable BY HAND, rather than by a name rule?

    is_library answers two different questions at once: "somebody decided this
    is a library" and "the name looks like one". Only the second should ever be
    overridable. The callers' escape hatch - honour an explicit pick of <=8
    mods if the library ships real items - exists to rescue a mod the NAME
    heuristic misfired on, and it was silently cancelling curated entries too:
    ftbquests is in LIBRARY_MODS, but library_has_real_items returns True for
    it (7 craftables >= max(2, 14//20)), so the list entry did nothing and the
    quest mod itself got a chapter that dead-ends on ftbquests:detector.
    A fix that landed and then had no effect is worse than no fix, because the
    entry reads as proof the problem is handled.
    """
    mid = (mod_id or "").lower()
    return mid in LIBRARY_MODS or mid in _UNQUESTABLE


def is_library(mod_id: str, name: str) -> bool:
    mid = mod_id.lower()
    if mid in LIBRARY_MODS or mid in _UNQUESTABLE:
        return True
    n = (name or "").lower()
    if mid.endswith(("lib", "api", "core")) and len(mid) > 5:
        return True
    return any(k in mid or k in n for k in
               ("corelib", "core_lib", "coremod", " api", "_api", "api_",
                "library", "_lib", "lib_", "config_api"))


# a few packs name mods in ways the keyword sweep gets wrong
CATEGORY_OVERRIDES = {
    "botania": "magic", "quark": "utility", "create": "tech",
    "twilightforest": "world", "cataclysm": "world", "tropicraft": "world",
    "alexsmobs": "mob", "goety": "magic", "securitycraft": "tech",
    "farmersdelight": "food", "supplementaries": "decor",
}


def categorize(mod_id: str, name: str) -> str:
    mid = mod_id.lower()
    if mid in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[mid]
    blob = (mod_id + " " + name).lower()
    # a mod that calls itself decor IS decor, whatever else its name mentions
    # ("MOA DECOR: GARDEN" is furniture, not a farming mod)
    if any(k in mid for k in ("decor", "furnitur", "furniture")) or \
       any(k in blob for k in ("decor:", "decoration", "furniture mod")):
        return "decor"
    # score every category, strongest wins — a single stray keyword shouldn't
    # drop a magic mod into "utility" just because it ships a library
    best, best_n = "unknown", 0
    for cat, kws in CATEGORY_KEYWORDS.items():
        n = sum(1 for k in kws if k in blob)
        if n > best_n:
            best, best_n = cat, n
    return best


_LANG_ITEM_RE = re.compile(r"^(item|block)\.([a-z0-9_]+)\.([a-z0-9_./]+)$")
_LANG_ENTITY_RE = re.compile(r"^entity\.([a-z0-9_]+)\.([a-z0-9_./]+)$")
# "item.alexsmobs.centipede_leggings.desc" -> what the item actually DOES.
# Mods write these for the in-game tooltip; they say why an item is worth
# having, which is what a quest description is for.
_LANG_DESC_RE = re.compile(
    r"^(?:item|block)\.([a-z0-9_]+)\.([a-z0-9_]+)\.(?:desc|description|tooltip)$")
# every REAL, obtainable item ships an item model json — this is the ground truth,
# lang files also carry keys for blockstate variants (lit_blaze_burner), dyed
# blocks (cyan_sail), fluids and tooltips that are not items you can hold.
# "name": "<ns>:<id>" inside a loot pool entry.
_LOOT_ID_RE = re.compile(r'"name"\s*:\s*"([a-z0-9_.-]+:[a-z0-9_./-]+)"')
# Miscellany chapters were capped at two per category, so ["I","II"] was
# always enough. Chunking by size can produce more, and an IndexError here
# would crash the build on exactly the large packs the change is meant to help.
_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
          "XI", "XII", "XIII", "XIV", "XV", "XVI")

_MODEL_ITEM_RE = re.compile(r"^assets/([a-z0-9_]+)/models/item/([a-z0-9_]+)\.json$")
# Any vanilla id quoted in a data file - used as proof that a model-only id is
# a real item rather than a render variant. See _vanilla_items_from_client.
_VAN_ID_RE = re.compile(r'"(minecraft:[a-z0-9_]+)"')
# GeckoLib-rendered items live here instead of models/item.
_MODEL_GEO_RE = re.compile(r"^assets/([a-z0-9_]+)/geo/(?:item/)?([a-z0-9_]+)\.geo\.json$")
_NON_ITEM_MARK = ("creative_", "_creative", "debug_", "_debug", "technical_",
                  "_placeholder", "spawner", "command_block", "structure_void",
                  "light_block", "jigsaw")

JUNK_SUFFIXES = ("_slab", "_stairs", "_wall", "_fence", "_fence_gate", "_button",
                 "_pressure_plate", "_trapdoor", "_door", "_sign", "_hanging_sign",
                 "_carpet", "_pane", "_bars")


_TEX_RE = re.compile(r"^assets/([a-z0-9_]+)/textures/(.+)\.png$")

# Chapter art has to come from what the pack actually ships, because a texture
# path that does not resolve renders as purple-and-black - the most visible
# failure there is. So the resloc is BUILT FROM the zip entry name we just read,
# never reconstructed from an item id and never guessed. The short "ns:block/foo"
# spelling is what FTBQ resolves to assets/ns/textures/block/foo.png, and it is
# what 93.6% of the corpus's chapter images use.
_BACKDROP_RE = re.compile(r"^assets/([a-z0-9_]+)/textures/(block|item)/(.+)\.png$")
# Overlays, masks and connected-texture pieces are fragments meant to be drawn
# ON something; alone they read as debris.
_BACKDROP_BAD = re.compile(
    r"_ctm|/ctm/|overlay|_mask|connected|_flow|_still|_frame|_emissive|_glow")
# 46 of 59,685 asset paths in one local pack carry uppercase, which is not a
# legal ResourceLocation - the game would refuse them at load.
_RESLOC_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


def _opaque_square_png(head: bytes):
    """(w, h) if `head` starts a square, provably-opaque PNG we can draw as
    chapter art; None otherwise.

    Header bytes only - no zlib and no PIL, neither of which this app has. The
    three rules each rule out a specific way art goes wrong: non-square means a
    stretched smear; 8/32/64 excludes both sprite atlases and the 1x1 stubs the
    1.20.1 jar keeps where the panorama images used to be; and a texture with
    transparency drawn as a backdrop shows the void through its own holes.
    Colour types 4 and 6 MAY be opaque, but proving it costs a full inflate of
    every PNG in every jar, so they are rejected rather than paid for."""
    if len(head) < 26 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w = int.from_bytes(head[16:20], "big")
    h = int.from_bytes(head[20:24], "big")
    if w != h or w not in (16, 32, 64):
        return None
    ctype = head[25]
    if ctype in (0, 2):            # greyscale / truecolour: no alpha channel
        return (w, h)
    if ctype != 3:
        return None
    # Palette: opaque unless a tRNS chunk shows up before the pixel data.
    off = 33                       # 8 sig + 8 chunk header + 13 IHDR + 4 CRC
    while off + 8 <= len(head):
        ln = int.from_bytes(head[off:off + 4], "big")
        typ = head[off + 4:off + 8]
        if typ == b"tRNS":
            return None
        if typ == b"IDAT":
            return (w, h)
        off += 12 + ln
    return None                    # ran out of header before IDAT - reject


def _model_texmap(raw: bytes):
    """-> ({texture slot: id}, parent model id or None).

    Slot names matter. Minecraft resolves textures CHILD-FIRST: a child's
    `textures` entry replaces the parent's for the same slot. Checking each
    model's textures independently therefore condemns items that render
    perfectly - Macaw's parent template block/parent/pillar_wall_post names a
    placeholder `mcwfences:block/stone_bricks` that every real variant
    overrides, and that placeholder was vetoing the whole family.
    """
    try:
        d = json.loads(raw)
    except Exception:
        return {}, None
    out = {}
    tex = d.get("textures")
    if isinstance(tex, dict):
        for k, v in tex.items():
            if isinstance(v, str) and not v.startswith("#"):
                out[k] = v if ":" in v else "minecraft:" + v
    par = d.get("parent")
    if isinstance(par, str):
        par = par if ":" in par else "minecraft:" + par
    else:
        par = None
    return out, par


def _model_parts(raw: bytes):
    """-> (texture ids it names directly, its parent model id or None)."""
    try:
        d = json.loads(raw)
    except Exception:
        return set(), None
    out = set()
    tex = d.get("textures")
    if isinstance(tex, dict):
        for v in tex.values():
            if isinstance(v, str) and not v.startswith("#"):
                out.add(v if ":" in v else "minecraft:" + v)
    par = d.get("parent")
    if isinstance(par, str):
        par = par if ":" in par else "minecraft:" + par
    else:
        par = None
    return out, par


def _recipe_result_ids(obj) -> set:
    """Pull output item ids out of any vanilla/modded recipe json."""
    out = set()

    def take(v):
        if isinstance(v, str) and ":" in v:
            out.add(v)
        elif isinstance(v, dict):
            for k in ("item", "id", "name"):
                if isinstance(v.get(k), str) and ":" in v[k]:
                    out.add(v[k])
        elif isinstance(v, list):
            for x in v:
                take(x)
    if isinstance(obj, dict):
        for key in ("result", "results", "output", "outputs", "resultItem"):
            if key in obj:
                take(obj[key])
    return out


def _instance_mc_version(inst: Path) -> str:
    """Which Minecraft version this instance actually runs, from its own
    metadata. Guessing wrong poisons the whole vanilla item list."""
    for name in ("minecraftinstance.json", "instance.json", "mmc-pack.json"):
        f = inst / name
        if not f.is_file():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for path in (("baseModLoader", "minecraftVersion"),
                     ("gameVersion",), ("minecraftVersion",),
                     ("intendedVersion",)):
            cur = d
            for k in path:
                cur = cur.get(k) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, str) and re.match(r"^1\.\d+", cur):
                return cur
        for comp in (d.get("components") or []):
            if isinstance(comp, dict) and comp.get("uid") == "net.minecraft":
                v = comp.get("version")
                if isinstance(v, str):
                    return v
    # fall back to the mod jars' own declared version
    try:
        for jar in sorted((inst / "mods").glob("*.jar"))[:40]:
            m = re.search(r"1\.20\.\d+|1\.\d\d?\.\d+", jar.name)
            if m:
                return m.group(0)
    except Exception:
        pass
    return ""


# Where each vanilla item actually comes from, read from the client jar's own
# loot tables and recipes. Without this the model free-writes vanilla facts and
# gets them wrong in ways that look authoritative: it described Echo Shards as
# End-dimension drops because the word "echo" pattern-matches to the End, when
# they are Ancient City chest loot in the Overworld's Deep Dark.
_VANILLA_SOURCE: dict = {}

# Every vanilla id that has a recipe, from the same jar walk. The scan only
# learns craftability from MOD recipes - 10928 ids of which 70 are minecraft: -
# so without this the craftable-first ranking treats every vanilla item as
# unobtainable and sorts real goals behind junk. (Titles no longer read it:
# they name the item rather than guess a verb.) Empty when no exact-version
# client jar was found, in which case the ranking simply loses that signal.
_VANILLA_CRAFTABLE: set = set()

_LOOT_PLACE = {
    "ancient_city": "Ancient City chests in the Deep Dark (Overworld)",
    "ancient_city_ice_box": "Ancient City ice-box chests (Deep Dark, Overworld)",
    "end_city_treasure": "End City treasure chests",
    "nether_bridge": "Nether Fortress chests",
    "bastion_treasure": "Bastion Remnant treasure chests",
    "bastion_bridge": "Bastion Remnant bridge chests",
    "bastion_hoglin_stable": "Bastion Remnant hoglin-stable chests",
    "bastion_other": "Bastion Remnant chests",
    "ruined_portal": "Ruined Portal chests",
    "buried_treasure": "buried treasure (follow an explorer map)",
    "shipwreck_treasure": "shipwreck treasure chests",
    "shipwreck_supply": "shipwreck supply chests",
    "shipwreck_map": "shipwreck map chests",
    "stronghold_library": "Stronghold library chests",
    "stronghold_corridor": "Stronghold corridor chests",
    "stronghold_crossing": "Stronghold crossing chests",
    "desert_pyramid": "Desert Temple chests",
    "jungle_temple": "Jungle Temple chests",
    "igloo_chest": "Igloo basement chests",
    "woodland_mansion": "Woodland Mansion chests",
    "pillager_outpost": "Pillager Outpost chests",
    "underwater_ruin_big": "large ocean-ruin chests",
    "underwater_ruin_small": "small ocean-ruin chests",
    "simple_dungeon": "dungeon chests",
    "abandoned_mineshaft": "abandoned mineshaft chests",
    "village": "village chests",
    "trail_ruins_rare": "Trail Ruins suspicious sand/gravel (rare finds)",
    "trail_ruins_common": "Trail Ruins suspicious sand/gravel",
}


def _load_vanilla_sources(client_jar) -> dict:
    """item -> a short, TRUE sentence fragment about where it comes from."""
    out: dict = {}
    try:
        z = zipfile.ZipFile(client_jar)
        names = z.namelist()
    except Exception:
        return out

    def note(item, text, weight):
        # keep EVERY real source, not just the best-scoring one. Choosing a
        # single winner kept producing true-but-misleading lines ("iron ingot:
        # found in abandoned mineshaft chests"). Handing the model all the
        # genuine routes lets it write something accurate instead.
        out.setdefault(item, []).append((weight, text))

    for n in names:
        if not (n.startswith("data/minecraft/loot_tables/") and n.endswith(".json")):
            continue
        rel = n[len("data/minecraft/loot_tables/"):-5]
        try:
            txt = z.read(n).decode("utf-8", "replace")
        except Exception:
            continue
        ids = set(re.findall(r'"minecraft:([a-z0-9_]+)"', txt))
        if not ids:
            continue
        kind, _, tail = rel.partition("/")
        if kind == "chests":
            place = _LOOT_PLACE.get(tail)
            if not place:
                place = "%s chests" % tail.replace("_", " ")
            text, w = "found in %s" % place, 3
        elif kind == "entities":
            text, w = "dropped by %s" % tail.replace("_", " "), 2
        elif kind == "gameplay":
            text, w = "obtained from %s" % tail.replace("_", " "), 1
        elif kind == "blocks":
            # mining beats a chest mention: "diamond -> abandoned mineshaft
            # chests" is true but misleading; players mine diamonds.
            if not tail.endswith(("_ore", "_block", "ore")):
                continue
            # An ore whose table has a silk_touch alternative drops a RAW item,
            # not the ore block - so "iron ingot: mined from iron ore" is
            # wrong, you get raw_iron and smelt it. Only claim the mining route
            # for ids the table can actually yield without Silk Touch.
            if "silk_touch" in txt:
                try:
                    d = json.loads(txt)
                except Exception:
                    d = None
                drops = set()
                if isinstance(d, dict):
                    for pool in (d.get("pools") or []):
                        for ent in (pool.get("entries") or []):
                            for sub in (ent.get("children") or [ent]):
                                nm = sub.get("name")
                                if isinstance(nm, str) and nm.startswith("minecraft:"):
                                    cond = json.dumps(sub.get("conditions") or [])
                                    if "silk_touch" not in cond:
                                        drops.add(nm.split(":", 1)[1])
                if drops:
                    ids = ids & drops
            text, w = "mined from %s" % tail.replace("_", " "), 4
        else:
            continue
        for i in ids:
            if kind == "blocks" and i == tail:
                # a block that drops itself IS mined - say so rather than
                # letting a chest mention win (ancient_debris is mined in the
                # Nether, not looted from a bastion)
                note("minecraft:" + i, "mined as a block", 4)
                continue
            note("minecraft:" + i, text, w)

    # crafted/smelted items: say so, with their key ingredient
    for n in names:
        if not (n.startswith("data/minecraft/recipes/") and n.endswith(".json")):
            continue
        try:
            d = json.loads(z.read(n).decode("utf-8", "replace"))
        except Exception:
            continue
        res = d.get("result")
        if isinstance(res, dict):
            res = res.get("item") or res.get("id")
        if not isinstance(res, str):
            continue
        # having walked to a real recipe result, keep the id: this loop is the
        # only place the app ever sees vanilla craftability, and it was being
        # thrown away with the rest of the recipe.
        _VANILLA_CRAFTABLE.add(res if ":" in res else "minecraft:" + res)
        # do NOT skip items that already have a loot source: since sources are
        # collected as a list, "already present" was skipping the recipe for
        # every item that also appears in a chest, which is why iron_ingot
        # reported mineshaft chests instead of "smelted from raw iron"
        t = str(d.get("type", ""))
        verb = "smelted" if "smelting" in t or "blasting" in t else "crafted"
        ings = set(re.findall(r'"item"\s*:\s*"minecraft:([a-z0-9_]+)"',
                              json.dumps(d)))
        ings.discard(res.split(":")[-1])
        if ings:
            # smelting an ore product IS how you get an ingot - it must beat a
            # chest mention outright, not tie with it
            w2 = 5 if (verb == "smelted" and res.endswith(("_ingot", "_nugget"))) else 1
            note(res, "%s from %s" % (verb, ", ".join(sorted(ings)[:3]).replace("_", " ")), w2)
        else:
            note(res, "%s at a crafting table" % verb, 0)
    final = {}
    for k, rows in out.items():
        rows.sort(key=lambda z: -z[0])
        seen, picked = set(), []
        for _w, txt in rows:
            if txt not in seen:
                seen.add(txt)
                picked.append(txt)
            if len(picked) >= 3:
                break
        final[k] = "; ".join(picked)
    return final


def _vanilla_items_from_client(mods_folder: Path) -> set:
    """Read the real vanilla item list out of the Minecraft client jar when we
    can find it. Falls back to the curated list, which only covers the obvious
    few hundred - the AI legitimately uses things like minecraft:bamboo."""
    inst = mods_folder.parent
    want_ver = _instance_mc_version(inst)
    roots = [inst.parent.parent / "Install" / "versions",     # CurseForge layout
             inst.parent.parent / "versions",
             inst / "versions"]
    best: set = set()
    best_exact = False
    for root in roots:
        if not root.is_dir():
            continue
        for jar in sorted(root.glob("*/*.jar")):
            try:
                z = zipfile.ZipFile(jar)
                names = z.namelist()
            except Exception:
                continue
            if "assets/minecraft/models/item/diamond.json" not in names:
                continue
            model_only = set()
            for n in names:
                m = _MODEL_ITEM_RE.match(n)
                if m and m.group(1) == "minecraft":
                    model_only.add("minecraft:" + m.group(2))
            # the lang file is the complete list - models miss a few hundred
            out = set()
            try:
                d = json.loads(z.read("assets/minecraft/lang/en_us.json")
                               .decode("utf-8", "replace"))
                for k in d:
                    mm = re.match(r"^(?:item|block)\.minecraft\.([a-z0-9_]+)$", k)
                    if mm:
                        out.add("minecraft:" + mm.group(1))
            except Exception:
                pass
            # The lang file names BLOCK STATES as well as items, and a block
            # state is not something a player can ever put in a bag. 160 of the
            # 1398 names 1.20.1 hands us can be drawn in the world but never in
            # a hand: redstone_wire, piston_head, moving_piston, nether_portal,
            # water, the wall_sign and candle_cake families, and the crop
            # blocks (carrots is the planted crop; the item is carrot). A
            # themed chapter seeded from this pool shipped "Obtain Redstone
            # Wire" and "Craft Nether Portal" - 43 of 914 themed item quests
            # asked for something the game has no item for, and no player could
            # ever finish one of them.
            #
            # So keep only names the client knows how to render in an
            # inventory. Filtering on the key PREFIX instead would not work:
            # vanilla names almost every placeable item under block.minecraft.*
            # rather than item.minecraft.*, so trusting the prefix would throw
            # away a thousand real items - stone, chests, every real cake.
            #
            # Both render paths have to count. Through 1.21.1 an item is drawn
            # from models/item/<id>.json; 1.21.4 moved that to items/<id>.json
            # and left only some behind, so testing models alone would cut a
            # 1.21.4 pack from 1552 names to 590. Together they hold steady:
            # 1.20.1 keeps 1238 of 1398, 1.21.11 keeps 1505 of 1675, and every
            # version in between drops the same ~165 block states.
            #
            # The stragglers that carry an item.* key and still fail are dead
            # names, not losses: the pre-1.20 *_pottery_shard spellings (now
            # sherd), plus "sign", "smithing_template" and "lodestone_compass",
            # which name families of ids rather than any id.
            held = set(model_only)
            for n in names:
                mm = re.match(r"^assets/minecraft/items/([a-z0-9_]+)\.json$", n)
                if mm:
                    held.add("minecraft:" + mm.group(1))
            out &= held
            # ...but models miss in one direction and INVENT in the other. 437
            # vanilla model files have no item lang key, and they are render
            # variants rather than items: bow_pulling_0, broken_elytra,
            # bundle_filled, 266 armour-trim overlays, and clock_00 through
            # clock_63. One of them reached a themed book as "Assemble Crossbow
            # Arrow" - minecraft:crossbow_arrow is the model of an arrow drawn
            # inside a loaded crossbow, not a thing anyone can hold.
            #
            # Dropping them all would be wrong too. The 17 smithing templates
            # ARE real, and are missing only because vanilla names them under
            # trim_pattern.minecraft.rib instead of item.minecraft.<id> - the
            # same composed-name case already handled for mods.
            #
            # So the same test used for mod items decides it: a recipe, a loot
            # table or an item tag naming the id is proof it is registered.
            # That keeps all 17 templates and drops all 420 render variants.
            model_only -= out
            if model_only:
                proof: set = set()
                for n in names:
                    if not (n.endswith(".json")
                            and ("/recipes/" in n or "/loot_tables/" in n
                                 or "/tags/items/" in n)):
                        continue
                    try:
                        raw = z.read(n).decode("utf-8", "replace")
                    except Exception:
                        continue
                    proof.update(_VAN_ID_RE.findall(raw))
                out |= (model_only & proof)
            # Prefer the jar whose version MATCHES this instance. Taking the
            # richest jar instead pulled 1.21 items - copper tools, copper
            # chests, copper bulbs - into a 1.20.1 book, where they simply do
            # not exist. A newer jar is always "richer"; that is exactly why
            # size is the wrong test.
            ver = ""
            mv = re.search(r"1\.\d+(?:\.\d+)?", jar.stem)
            if mv:
                ver = mv.group(0)
            exact = bool(want_ver) and ver == want_ver
            if exact and not best_exact:
                best, best_exact = out, True
                try:
                    _VANILLA_SOURCE.update(_load_vanilla_sources(jar))
                except Exception:
                    pass
            elif exact and len(out) > len(best):
                best = out
            elif not best_exact and len(out) > len(best):
                best = out
    if want_ver and not best_exact:
        log_once = "  ! no %s client jar found - vanilla list may include items "\
                   "from another version" % want_ver
        try:
            print(log_once)
        except Exception:
            pass
    return best



_PATCHOULI_MACRO = re.compile(r"\$\([^)]*\)")


def _clean_book_text(t: str) -> str:
    """Strip Patchouli's inline formatting macros from a guide-book page."""
    t = _PATCHOULI_MACRO.sub("", t or "")
    t = t.replace("/$", " ").replace("$(br)", " ").replace("$(br2)", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _scan_guide_books(zf, names, lang: dict, blurbs: dict, book_items: set):
    """Read the mod's in-game guide book (Patchouli and friends).

    Mod authors explain their own content here far better than any heuristic
    can, and an item having a book entry at all is a strong signal that it
    matters - it is the author telling you what is worth knowing about.
    """
    for n in names:
        low = n.lower()
        if not n.endswith(".json"):
            continue
        if "patchouli_books" not in low or "/entries/" not in low:
            continue
        # Patchouli books ship one directory per LANGUAGE. With no filter,
        # whichever sorts last wins - goety's ja_jp book supplied the blurb
        # for 41 items, so the quest book showed Japanese. Take en_us, and
        # accept an unlocalised book (no language directory) as English.
        _lang = re.search(r"/patchouli_books/[^/]+/([a-z]{2}_[a-z]{2})/", low)
        if _lang and _lang.group(1) != "en_us":
            continue
        try:
            d = json.loads(zf.read(n).decode("utf-8-sig"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        icon = d.get("icon")
        if isinstance(icon, dict):
            icon = icon.get("item") or icon.get("id")
        if not isinstance(icon, str) or ":" not in icon:
            continue
        icon = icon.split("{")[0].strip()
        book_items.add(icon)
        if icon in blurbs:
            continue
        for pg in (d.get("pages") or []):
            if not isinstance(pg, dict):
                continue
            t = pg.get("text")
            if not isinstance(t, str):
                continue
            t = lang.get(t, t)
            if not isinstance(t, str):
                continue
            t = _clean_book_text(t)
            if 40 <= len(t) <= 220 and "%" not in t:
                blurbs[icon] = t
                break


def _recipe_ingredient_ids(obj) -> set:
    """Every item id a recipe consumes (ingredients / key / pattern entries)."""
    out = set()

    def take(v, depth=0):
        if depth > 6:
            return
        if isinstance(v, str):
            if ":" in v and not v.startswith("#"):
                out.add(v)
        elif isinstance(v, dict):
            for k in ("item", "id"):
                if isinstance(v.get(k), str) and ":" in v[k] and not v[k].startswith("#"):
                    out.add(v[k])
            # Tag ingredients are the norm for equipment recipes
            # ({"tag":"forge:ingots/ironwood"}). Ignoring them left 72% of
            # Twilight Forest's items with no crafting depth at all, so the
            # questline had nothing to order by. Kept as a #marker and
            # resolved against the pack's item tags after the scan.
            if isinstance(v.get("tag"), str) and ":" in v["tag"]:
                out.add("#" + v["tag"].lstrip("#"))
            # "materials" is Refurbished Furniture's workbench format - 444
            # recipes in one pack, every one invisible before this key was
            # known, so the whole mod had no crafting depth. Named here rather
            # than walking EVERY dict value: that was tried and measured, and
            # it reads junk as ingredients - goety:ritual's "goety:craft"
            # requirement token, farmersdelight:cutting's sound event id
            # ("minecraft:item.axe.strip"), quark:exclusion's recipe ids -
            # because any modded field whose value contains ":" looks like an
            # item. A whitelist only ever adds fields somebody verified.
            for k in ("ingredients", "key", "ingredient", "input", "inputs",
                      "base", "addition", "template", "materials"):
                if k not in v:
                    continue
                # A SHAPED recipe's key map is {"A": {...}, "B": {...}} - the
                # names are pattern symbols, so recursing on the map itself
                # matched none of the names above and walked straight past
                # every ingredient. Shaped is the commonest recipe type in
                # Minecraft, so most of the crafting graph was missing, and
                # nothing downstream could tell: ordering, why_want and the
                # rationale scoring all just saw fewer edges.
                if k == "key" and isinstance(v[k], dict):
                    for sym in v[k].values():
                        take(sym, depth + 1)
                else:
                    take(v[k], depth + 1)
        elif isinstance(v, list):
            for x in v:
                take(x, depth + 1)
    if isinstance(obj, dict):
        for key in ("ingredients", "key", "ingredient", "input", "inputs",
                    "base", "addition", "template", "materials"):
            if key not in obj:
                continue
            # Same as inside take(): a top-level shaped recipe is the ordinary
            # case, so this is the branch that was losing most of the graph.
            if key == "key" and isinstance(obj[key], dict):
                for sym in obj[key].values():
                    take(sym)
            else:
                take(obj[key])
    return out


def _craft_tiers(edges: dict, limit: int = 24, vanilla: set = (),
                 alts: dict = None) -> dict:
    """item -> crafting depth. depth(x) = 1 + max(depth(its MODDED ingredients)).

    This is what makes a questline read correctly: a raven feather is an
    ingredient of the magic map focus, so it must come first. Derived from the
    pack's own recipes, so it works for every mod without hand-curation.

    Vanilla ingredients count as depth 0 and are not recursed into. A player can
    get iron whenever they like, so charging a mod's first item for the ore ->
    ingot chain behind it is meaningless: it put create:andesite_alloy, the very
    first thing Create asks for, at depth 9.
    """
    tier: dict = {}
    visiting: set = set()
    van = set(vanilla or ())

    def d(item, depth=0):
        if item in van:
            return 0
        if item in tier:
            return tier[item]
        if depth > limit or item in visiting:
            return 0
        ing = edges.get(item)
        if not ing and not (alts or {}).get(item):
            tier[item] = 0
            return 0
        ing = ing or set()
        visiting.add(item)
        best = 0
        for g in ing:
            if g != item and g not in van:
                best = max(best, d(g, depth + 1))
        # each tag group contributes its EASIEST member
        for group in (alts or {}).get(item, ()):
            cheapest = None
            for g in group:
                if g == item:
                    continue
                v = 0 if g in van else d(g, depth + 1)
                cheapest = v if cheapest is None else min(cheapest, v)
            if cheapest is not None:
                best = max(best, cheapest)
        visiting.discard(item)
        tier[item] = min(best + 1, limit)
        return tier[item]
    for it in list(edges):
        d(it)
    return tier

class _NestedJar(io.BytesIO):
    """A jar shipped INSIDE another jar under META-INF/jarjar/ (Forge JarJar).
    Presents just enough of pathlib.Path's surface (.name, .stat().st_size,
    openable by zipfile.ZipFile since it IS a BytesIO) for both scan passes
    to treat it exactly like a jar sitting in the mods folder."""
    def __init__(self, name: str, data: bytes):
        super().__init__(data)
        self.name = name
        self._size = len(data)

    def stat(self):
        return type("_st", (), {"st_size": self._size})()


# Scan caching: the user regenerates constantly, and re-reading every jar is
# the slowest step of every cycle - 16.5s on the 114-jar pack whether or not
# anything changed, paid again on every app launch. The result depends only on
# the jars themselves, so pickle it keyed on every top-level jar's
# (name, size, mtime) - nested JarJar content rides inside a parent whose
# size/mtime covers it. The key also folds in this file's own size/mtime:
# three workflows are fixing the scanner today, and a scanner fix that keeps
# serving yesterday's scan is worse than an unnecessary 17s rescan.
_SCAN_CACHE_VERSION = 2


def _scan_cache_path(folder: Path) -> Path:
    # One file per mods folder, replaced in place, so stale caches for a
    # folder never accumulate however many packs the user flips between.
    return DATA_DIR / ("scan_cache_%s.pkl" % hashlib.sha1(
        str(folder.resolve()).lower().encode("utf-8")).hexdigest()[:12])


def _scan_cache_key(folder: Path, per_mod_cap: int) -> str:
    try:
        import sys
        # Frozen: stat the exe, not __file__ - the extracted copy inside
        # _MEIPASS gets a fresh mtime on some launches, which would junk the
        # cache every run for no reason.
        _src_path = Path(sys.executable) if getattr(sys, "frozen", False) \
            else Path(__file__)
        _src = _src_path.stat()
        sig = ["v%d" % _SCAN_CACHE_VERSION, "cap%d" % per_mod_cap,
               "src|%d|%d" % (_src.st_size, _src.st_mtime_ns)]
        for p in sorted(folder.glob("*.jar")) + sorted(folder.glob("*.zip")):
            st = p.stat()
            sig.append("%s|%d|%d" % (p.name, st.st_size, st.st_mtime_ns))
        return hashlib.sha1("\n".join(sig).encode("utf-8")).hexdigest()
    except Exception:
        return ""            # unstattable folder -> never cache


def textures_ok(item_id: str, all_models: dict, all_states: dict,
                all_textures: set, cache: dict) -> bool:
    """Walk the model's parent chain; every non-vanilla texture it ends up
    naming must actually ship, or the item renders as a missing-texture box.

    Module-level on purpose: this used to be a closure inside scan_mods, and
    guard_audit.py - whose whole job is catching guards that silently stop
    firing - could no longer instrument it by name, printed NOT FOUND, and
    still said PASS. The texture guard lost audit coverage without anyone
    noticing, which is precisely the failure mode the audit exists for. The
    scan's caches come in as arguments instead of captures so the by-name
    wrapper sees every call."""
    if item_id in cache:
        return cache[item_id]
    ns, short = (item_id.split(":", 1) + [""])[:2]
    ok = True
    starts = ["%s:item/%s" % (ns, short)]
    # A block item renders through its BLOCKSTATE's models. When a
    # blockstate exists it is the authority, and the speculative
    # "<ns>:block/<name>" model must NOT be consulted: Quark ships a stale
    # block/feeding_trough naming a texture it no longer includes, while
    # the block itself renders fine through trough_empty/trough_full. That
    # dead asset was vetoing a real, craftable item.
    st = all_states.get("%s:%s" % (ns, short))
    if not st:
        starts.append("%s:block/%s" % (ns, short))
    if st:
        try:
            sd = json.loads(st)
            cand = []
            for v in (sd.get("variants") or {}).values():
                cand += v if isinstance(v, list) else [v]
            for part in (sd.get("multipart") or []):
                a = part.get("apply")
                cand += a if isinstance(a, list) else [a]
            for c in cand[:8]:
                mdl = (c or {}).get("model") if isinstance(c, dict) else None
                if isinstance(mdl, str):
                    starts.append(mdl if ":" in mdl else "minecraft:" + mdl)
        except Exception:
            pass
    # check the item model chain AND the same-named block model — a block
    # item often renders through the block model, and that's where these
    # decoration mods lose their textures.
    for start in starts:
        key = start
        seen_models = set()
        resolved: dict = {}
        for depth in range(6):               # depth-limited parent walk
            raw = all_models.get(key)
            if raw is None:
                # a dangling parent (not the starting model, and not vanilla)
                # means the model can't render at all
                if depth > 0 and key.split(":", 1)[0] != "minecraft":
                    ok = False
                break
            if key in seen_models:
                break
            seen_models.add(key)
            tmap, parent = _model_texmap(raw)
            for slot, val in tmap.items():
                resolved.setdefault(slot, val)   # child wins over parent
            if parent is None:
                break
            if parent.split(":", 1)[0] == "minecraft":
                break                        # vanilla parent, nothing to check
            key = parent
        # Check only what SURVIVED the child-first merge. A slot the child
        # overrode is never rendered from the parent's value, so a parent
        # template's placeholder must not condemn the item.
        for slot, val in resolved.items():
            if val.split(":", 1)[0] == "minecraft":
                continue                     # vanilla textures ship in the client
            if val not in all_textures:
                if os.environ.get("AQG_TRACE_ID") == item_id:
                    print("[trace]   %s slot %r -> missing texture %r"
                          % (start, slot, val))
                ok = False
                break
        if not ok:
            break
    cache[item_id] = ok
    return ok


def _jar_format_census(folder: Path) -> dict:
    """How many jars of each mod-metadata format this folder holds. -> dict

    Reads entry NAMES only, so it costs one pass and cannot fail on a jar with
    a corrupt member. Nested jars are not counted: a bundled dependency is not
    a mod the user chose, and counting it would flatter the coverage number
    this exists to report honestly.
    """
    import zipfile as _zip
    out = {"total": 0, "modern": 0, "legacy": 0, "fabric": 0, "bare": 0,
           "items": 0}
    for j in sorted(folder.glob("*.jar")):
        out["total"] += 1
        try:
            with _zip.ZipFile(j) as z:
                names = set(z.namelist())
        except Exception:
            out["bare"] += 1
            continue
        if any(n.startswith("assets/") and "/models/item/" in n for n in names):
            out["items"] += 1
        if ("META-INF/mods.toml" in names
                or "META-INF/neoforge.mods.toml" in names):
            out["modern"] += 1
        elif "fabric.mod.json" in names:
            out["fabric"] += 1
        elif "mcmod.info" in names:
            out["legacy"] += 1
        else:
            out["bare"] += 1
    return out


def _coverage_note(folder: Path, n_mods: int) -> str:
    """Why this pack produced less than the user expected. -> str, or ""

    Silent when the pack read cleanly. Judged on what was actually READABLE,
    not on which loader the jars were built for: an early version warned that
    an 18-jar Fabric pack could not be read when all 18 had in fact been
    scanned - they were performance mods that add no items, which is a
    different sentence entirely. Items live in assets/, and assets do not care
    who loads them, so the loader is only evidence when the metadata format
    itself is one this app cannot parse.
    """
    c = _jar_format_census(folder)
    n = c["total"]
    if not n:
        return "that folder has no .jar files in it."
    # 1.12 and earlier really is unreadable here: no mods.toml to parse, and
    # the asset layout predates the one every path in this file assumes.
    if c["legacy"] and c["legacy"] > c["modern"]:
        return ("%d of these %d jars use mcmod.info, the mod format Minecraft "
                "used up to 1.12, and AutoQuestGen builds books for 1.20.1. "
                "Only %d mod%s here could be read, so this book covers a "
                "fraction of the pack. Point it at a 1.20.1 pack."
                % (c["legacy"], n, n_mods, "" if n_mods == 1 else "s"))
    # Nothing in the folder adds an item, whoever built it.
    if not c["items"]:
        return ("none of these %d jars add any items - they look like "
                "performance or interface mods. There is nothing to build "
                "quests out of." % n)
    # Readable, modern, has items, and yet most of it did not come through.
    if c["items"] >= 6 and n_mods and n_mods * 2 < c["items"]:
        return ("%d of these %d jars ship items but only %d mod%s could be "
                "read, so this book covers part of the pack rather than all "
                "of it." % (c["items"], n, n_mods, "" if n_mods == 1 else "s"))
    return ""


def _why_no_mods(folder: Path) -> str:
    """Why a folder full of jars produced nothing. -> a sentence, or ""

    Kept as the zero-mods entry point; the wording and the census live in
    _coverage_note so the two cases can never drift apart and say different
    things about the same folder.
    """
    return _coverage_note(folder, 0)


def scan_mods(folder: Path, per_mod_cap: int, log, progress=None) -> dict:
    _ckey = _scan_cache_key(folder, per_mod_cap)
    _cpath = _scan_cache_path(folder)
    if _ckey and _cpath.is_file():
        try:
            with open(_cpath, "rb") as _fh:
                _cached = pickle.load(_fh)
            if _cached.get("key") == _ckey:
                log("  scan cache hit (%s) - jars unchanged since last scan"
                    % _cpath.name)
                if progress:
                    progress(1.0, "loaded cached scan")
                return _cached["scan"]
        except Exception:
            pass             # unreadable / stale-format cache -> full rescan
    jars = sorted(folder.glob("*.jar")) + sorted(folder.glob("*.zip"))
    # Forge JarJar: a mod may ship whole modules only as nested jars under
    # META-INF/jarjar/. On the 338-jar pack, thermal_core exists NOWHERE at
    # top level - it rides inside thermal_foundation and holds 356 item
    # models plus the only copy of assets/thermal/lang/en_us.json, so
    # skipping nested jars lost an entire mod AND the display names for
    # every other thermal module. Recurse exactly one level and append the
    # nested jars to the same list both passes iterate.
    _nested = []
    for jar in jars:
        try:
            with zipfile.ZipFile(jar) as _zf:
                for _nm in _zf.namelist():
                    if _nm.startswith("META-INF/jarjar/") and _nm.endswith(".jar"):
                        try:
                            _nested.append(_NestedJar(_nm.rsplit("/", 1)[-1],
                                                      _zf.read(_nm)))
                        except Exception:
                            pass
        except Exception:
            pass
    jars += sorted(_nested, key=lambda j: j.name)
    mods, items, entities, craftable, seen = [], {}, {}, {}, set()
    backdrops: dict = {}            # namespace -> [(resloc, w, h)] for chapter art
    broken_rate: dict = {}
    craft_edges: dict = {}          # item -> the items its recipe consumes
    # The same recipes kept ONE ENTRY PER FILE, because a union is not a
    # recipe. 2174 of this pack's 13033 outputs have two or more distinct
    # ingredient sets, so a sentence built from craft_edges can name a
    # combination that exists nowhere: create:netherite_backtank has a
    # copper-backtank route and a netherite-chestplate route, and the union
    # read "Crafted from Copper Backtank and Netherite Chestplate" - an
    # instruction no player can follow. The union also loses the recipe TYPE,
    # which is how a cutting board and a smithing table both got called
    # "Crafted". edges stays unioned: tier and dependency code wants every
    # route, and only the prose needs to pick one.
    craft_recipes: dict = {}        # item -> [(recipe type, frozenset(ings))]
    recipe_reqs: dict = {}          # item -> [mods each RECIPE needs]
    loot_items: set = set()         # anything a loot table can yield
    no_block_item: set = set()      # blocks that exist but cannot be held
    struct_loot: dict = {}          # chest table -> what it holds
    drops: dict = {}                # item -> {entities/blocks/gameplay: sources}
    blurbs: dict = {}               # item -> authored one-line description
    disp_names: dict = {}           # item -> its in-game display name
    book_items: set = set()         # items the mods' own guide books cover
    dimensions: dict = {}           # mod -> the dimension ids it adds
    structures: dict = {}           # mod -> the worldgen structures it adds
    struct_info: dict = {}          # structure id -> {biomes, step, spawn, spacing}
    biome_tags: dict = {}           # "ns:path" -> biome ids / nested #tags
    tag_members: dict = {}          # "ns:path" -> the item ids in that tag
    total = max(1, len(jars))

    # Pass 0: every texture that actually ships, so we can reject items whose
    # model points at a missing PNG — those render as the pink/black checkerboard.
    all_textures: set = set()
    all_models: dict = {}          # "ns:item/foo" / "ns:block/foo" -> raw json
    all_states: dict = {}          # "ns:foo" -> raw blockstate json
    all_advs: dict = {}            # "ns:path" -> advancement json (progression tree)
    _MODEL_ANY = re.compile(r"^assets/([a-z0-9_]+)/models/(.+)\.json$")
    _STATE_ANY = re.compile(r"^assets/([a-z0-9_]+)/blockstates/([a-z0-9_]+)\.json$")
    _ADV_ANY = re.compile(r"^data/([a-z0-9_]+)/advancements?/(.+)\.json$")
    # Advancements name items in two shapes: "item": "mod:thing" and
    # "items": ["mod:thing", ...]. Match both - picking only one silently
    # halves this source depending on which version the mod was written for.
    # A display name that denies being the item, or marks itself as developer
    # scaffolding, is the mod telling us plainly that this is not questable.
    _DISCLAIMED_NAME = re.compile(
        r"^\s*(?:this\s+is\s+not|not\s+an?)\b|^\s*dev[\s.]|<\s*missing\s*>"
        r"|do\s*not\s*use", re.I)
    _ADV_ITEM_RE = re.compile(r'"item"\s*:\s*"([a-z0-9_.-]+:[a-z0-9_./-]+)"')
    _ADV_LIST_RE = re.compile(r'"items"\s*:\s*\[([^\]]*)\]')
    _ADV_TOK_RE = re.compile(r'"([a-z0-9_.-]+:[a-z0-9_./-]+)"')
    for n, jar in enumerate(jars):
        if progress and n % 10 == 0:
            progress(0.02 + 0.18 * n / total, "indexing textures  %d / %d" % (n, total))
        try:
            zf0 = zipfile.ZipFile(jar)
        except Exception:
            continue
        _names0 = zf0.namelist()
        _nameset0 = set(_names0)
        for nm in _names0:
            t = _TEX_RE.match(nm)
            if t:
                all_textures.add("%s:%s" % (t.group(1), t.group(2)))
                bd = _BACKDROP_RE.match(nm)
                # The .mcmeta sidecar marks an animation strip. Drawn flat it is
                # a vertical filmstrip of every frame at once.
                if bd and not _BACKDROP_BAD.search(nm) \
                        and (nm + ".mcmeta") not in _nameset0:
                    rl = "%s:%s/%s" % (bd.group(1), bd.group(2), bd.group(3))
                    if _RESLOC_RE.match(rl):
                        try:
                            with zf0.open(nm) as _fh:
                                _wh = _opaque_square_png(_fh.read(2048))
                        except Exception:
                            _wh = None
                        if _wh:
                            backdrops.setdefault(bd.group(1), []).append(
                                (rl, _wh[0], _wh[1]))
                continue
            mm = _MODEL_ANY.match(nm)
            if mm:
                key = "%s:%s" % (mm.group(1), mm.group(2))
                if key not in all_models:
                    try:
                        all_models[key] = zf0.read(nm)
                    except Exception:
                        pass
                continue
            sm = _STATE_ANY.match(nm)
            if sm:
                key = "%s:%s" % (sm.group(1), sm.group(2))
                if key not in all_states:
                    try:
                        all_states[key] = zf0.read(nm)
                    except Exception:
                        pass
                continue
            am = _ADV_ANY.match(nm)
            if am:
                try:
                    all_advs["%s:%s" % (am.group(1), am.group(2))] = json.loads(zf0.read(nm))
                except Exception:
                    pass

    # Cap each namespace. `chipped` alone yields 6,216 near-identical brick
    # variants; uncapped it swamps the pool, bloats the cached scan, and gives
    # one chapter sixty shades of the same wall. Sort then stride, so the
    # survivors are spread across the namespace instead of being the first 64
    # alphabetically - which on most mods is sixty-four flavours of "acacia_".
    for _ns, _lst in backdrops.items():
        _lst.sort()
        if len(_lst) > 64:
            _st = len(_lst) // 64
            backdrops[_ns] = [_lst[i * _st] for i in range(64)]

    # Per-scan memo for the module-level textures_ok (hoisted so guard_audit
    # can instrument it by name - see the docstring on textures_ok).
    _tex_cache: dict = {}

    for n, jar in enumerate(jars):
        if progress and n % 5 == 0:
            progress(0.22 + 0.73 * n / total, "scanning  %d / %d  %s" % (n, total, jar.name[:34]))
        try:
            zf = zipfile.ZipFile(jar)
        except Exception as e:
            log("  ! skip %s (%s)" % (jar.name, e))
            continue
        names = zf.namelist()
        toml_name = next((c for c in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")
                          if c in names), None)
        mod_id = disp = desc = authors = url = logo_file = ""
        if toml_name:
            try:
                meta = tomllib.loads(zf.read(toml_name).decode("utf-8", "replace"))
                entry = (meta.get("mods") or [{}])[0]
                mod_id = str(entry.get("modId") or "").strip()
                disp = str(entry.get("displayName") or mod_id).strip()
                desc = " ".join(str(entry.get("description") or "").split())[:400]
                authors = " ".join(str(entry.get("authors") or "").split())[:120]
                url = str(entry.get("displayURL") or "").strip()
                logo_file = str(entry.get("logoFile") or "").strip()
            except Exception as e:
                log("  ! bad mods.toml in %s (%s)" % (jar.name, e))
                continue
        elif "fabric.mod.json" in names:
            # Fabric/Quilt jars carry fabric.mod.json instead of mods.toml.
            # Reading only the Forge metadata made every Fabric mod invisible
            # to the scanner - and half the top Modrinth packs are Fabric.
            try:
                fm = json.loads(zf.read("fabric.mod.json").decode("utf-8", "replace"))
                mod_id = str(fm.get("id") or "").strip()
                disp = str(fm.get("name") or mod_id).strip()
                desc = " ".join(str(fm.get("description") or "").split())[:400]
                au = fm.get("authors") or []
                authors = " ".join(str(a.get("name") if isinstance(a, dict) else a)
                                   for a in au[:4])[:120]
                url = str((fm.get("contact") or {}).get("homepage") or "").strip()
                logo_file = str(fm.get("icon") or "").strip()
            except Exception as e:
                log("  ! bad fabric.mod.json in %s (%s)" % (jar.name, e))
                continue
        else:
            continue
        if not mod_id or mod_id in seen:
            continue
        seen.add(mod_id)
        logo = None
        for cand in ([logo_file] if logo_file else []) + [
                "icon.png", "logo.png", "%s.png" % mod_id,
                "assets/%s/icon.png" % mod_id, "pack.png",
                "META-INF/%s" % logo_file if logo_file else ""]:
            if cand and cand in names:
                try:
                    b = zf.read(cand)
                    if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) < 400000:
                        logo = b
                        break
                except Exception:
                    pass
        mods.append({"mod_id": mod_id, "name": disp, "filename": jar.name,
                     "size": jar.stat().st_size, "category": categorize(mod_id, disp),
                     "description": desc, "authors": authors, "url": url, "logo": logo})
        it, en = items.setdefault(mod_id, set()), entities.setdefault(mod_id, set())
        cr = craftable.setdefault(mod_id, set())

        # --- source 1: item models — the ground truth for "this id is an item" ---
        model_items: set = set()
        for n in names:
            mm = _MODEL_ITEM_RE.match(n)
            if mm and not any(k in mm.group(2) for k in _NON_ITEM_MARK):
                model_items.add("%s:%s" % (mm.group(1), mm.group(2)))
        # GeckoLib items ship NO models/item file at all - they are rendered
        # from assets/<ns>/geo/<name>.geo.json. Alien Evolution's title item,
        # prototype_omnitrix, has 27 data references and was dropped outright
        # by the strict rule. A geo model is exactly as much proof that an item
        # renders as a flat model is.
        for n in names:
            gm = _MODEL_GEO_RE.match(n)
            if gm and not any(k in gm.group(2) for k in _NON_ITEM_MARK):
                model_items.add("%s:%s" % (gm.group(1), gm.group(2)))

        # --- source 2: recipe outputs — proof an item is actually OBTAINABLE ---
        recipe_items: set = set()
        for n in names:
            if not (n.endswith(".json") and ("/recipe/" in n or "/recipes/" in n)):
                continue
            if "/advancements/" in n:          # recipe-unlock advancements, not recipes
                continue
            try:
                rj = json.loads(zf.read(n))
            except Exception:
                continue
            outs = _recipe_result_ids(rj)
            recipe_items |= outs
            ing = _recipe_ingredient_ids(rj)
            if ing:
                _one = (str(rj.get("type") or ""), frozenset(ing))
                for o in outs:
                    craft_edges.setdefault(o, set()).update(ing)
                    craft_recipes.setdefault(o, []).append(_one)
            # Which mods THIS recipe needs, kept per-recipe. craft_edges unions
            # ingredients across every recipe for an output, so it cannot say
            # whether a particular route is usable. Dreadsteel's ingot - the
            # sole gate to 10 of its 11 items - requires iceandfire:dread_shard
            # while its own mods.toml declares no such dependency, so a pack
            # without Ice and Fire gets a chapter of uncompletable quests and
            # nothing warns anyone.
            _need = {i.split(":", 1)[0] for i in ing
                     if ":" in i and not i.startswith("#")}
            _need -= {"minecraft", "forge", "c"}
            for o in outs:
                recipe_reqs.setdefault(o, []).append(_need)

        # --- loot sources: an item with no usable RECIPE may still drop ---
        for n in names:
            if not (n.endswith(".json")
                    and ("/loot_table" in n or "/loot_tables" in n)):
                continue
            try:
                raw = zf.read(n).decode("utf-8", "replace")
            except Exception:
                continue
            found = {mm.group(1) for mm in _LOOT_ID_RE.finditer(raw)}
            loot_items |= found
            # A BLOCK whose own loot table never yields itself has no
            # BlockItem: you cannot hold it, so no quest can ask for it.
            # brewery:wild_hops is one, and it had a model, a lang key and a
            # blockstate, so every existing test passed it - it became the
            # ROOT quest and the icon of a 49-quest chapter, with 48 quests
            # descending from a task that can never complete. Breaking the
            # block drops brewery:hops_seeds instead.
            bp = n.replace("\\", "/").split("/")
            if (len(bp) >= 5 and bp[2] in ("loot_table", "loot_tables")
                    and bp[3] == "blocks" and found):
                bid = "%s:%s" % (bp[1], bp[-1][:-5])
                if bid not in found:
                    no_block_item.add(bid)
            # Provenance, not just existence. loot_items only proves SOME
            # table yields the item, which cannot answer the player asking
            # where it comes from - so a boss drop like the Lich Trophy
            # shipped titled "Make ..." with a blank description, the exact
            # complaint in the mined reactions ("it says FIND, it doesn't
            # say SLAY"). The table's own path already says which kind of
            # source it is: entities/ means a mob drops it, blocks/ means
            # breaking one does, gameplay/ covers fishing, bartering and
            # the like. Keep that per item; the prose side decides what is
            # worth saying.
            if (len(bp) >= 5 and bp[2] in ("loot_table", "loot_tables")
                    and bp[3] in ("entities", "blocks", "gameplay") and found):
                _src = "%s:%s" % (bp[1], "/".join(bp[4:])[:-5])
                for _i in found:
                    drops.setdefault(_i, {}).setdefault(bp[3], set()).add(_src)
            # Keep PLACE tables per-table as well: a structure's loot table is
            # normally named after the structure, which is the only grounded
            # way to say what a place is actually for. Not only /chests/ -
            # mods file structure loot wherever they like (tropicraft's
            # home_tree.json sits at the loot_tables root, twilightforest
            # keeps a structures/ tree), and gating on /chests/ silently
            # threw those away, which is most of why only 24% of eligible
            # structures could say what was inside them. Only blocks/ and
            # entities/ are excluded: those describe an ITEM's provenance
            # (kept in `drops` above), not a location's contents, and a
            # block table's leaf is a block name that would collide with
            # structures constantly ("dark_tower" the block vs the tower).
            if (len(bp) >= 4 and bp[2] in ("loot_table", "loot_tables")
                    and bp[3] not in ("blocks", "entities") and found):
                key = "%s:%s" % (bp[1], "/".join(bp[3:])[:-5])
                struct_loot.setdefault(key, set()).update(found)

        # --- source 2b: the mod's own advancements name its own items ---
        # An advancement is the mod pointing at an item and saying "get this",
        # which is proof of registration in exactly the way a lang key is not.
        # Only this mod's own namespace counts: an advancement that asks for a
        # diamond is about minecraft:diamond, not about anything here.
        adv_items: set = set()
        for n in names:
            if not (n.endswith(".json") and _ADV_ANY.match(n)):
                continue
            try:
                raw = zf.read(n).decode("utf-8", "replace")
            except Exception:
                continue
            got = {mm.group(1) for mm in _ADV_ITEM_RE.finditer(raw)}
            for mm in _ADV_LIST_RE.finditer(raw):
                got |= set(_ADV_TOK_RE.findall(mm.group(1)))
            adv_items |= {i for i in got if i.split(":", 1)[0] == mod_id}

        # --- source 3: item tags — ids grouped into tags are real items ---
        tag_items: set = set()
        for n in names:
            if not (n.endswith(".json") and ("/tags/item/" in n or "/tags/items/" in n)):
                continue
            try:
                parts = n.replace("\\", "/").split("/")
                tns = parts[1] if len(parts) > 2 else "minecraft"
                after = n.split("/tags/item/", 1)[-1] if "/tags/item/" in n                     else n.split("/tags/items/", 1)[-1]
                tname = "%s:%s" % (tns, after[:-5])
                for v in (json.loads(zf.read(n)).get("values") or []):
                    v = v.get("id") if isinstance(v, dict) else v
                    if isinstance(v, str) and ":" in v and not v.startswith("#"):
                        tag_items.add(v)
                        tag_members.setdefault(tname, set()).add(v)
            except Exception:
                pass

        # --- source 4: lang file — display names + last-ditch fallback ---
        lang_items: set = set()
        lang_prefixes: set = set()
        jar_lang: dict = {}
        for lf in [n for n in names if n.endswith("/lang/en_us.json")]:
            data = _loads_lenient(zf.read(lf))
            if not isinstance(data, dict):
                continue
            if isinstance(data, dict):
                jar_lang.update(data)
            for key in data:
                _pp = key.split(".")
                if len(_pp) > 3 and _pp[0] in ("item", "block") and _pp[1] == mod_id:
                    # item.<mod>.<a>.<b>... - remember every dotted name form
                    lang_prefixes.add("_".join(_pp[2:]))
                    lang_prefixes.add(_pp[-2])
                    lang_prefixes.add("%s_%s" % (_pp[-2], _pp[2]))
                md = _LANG_DESC_RE.match(key)
                if md:
                    val = data.get(key)
                    if (isinstance(val, str) and 12 <= len(val) <= 160
                            and "%" not in val):
                        blurbs.setdefault("%s:%s" % (md.group(1), md.group(2)),
                                          val.strip())
                    continue
                m = _LANG_ITEM_RE.match(key)
                if m:
                    ns, path = m.group(2), m.group(3)
                    if "/" not in path and "." not in path:
                        lang_items.add("%s:%s" % (ns, path))
                        val = data.get(key)
                        if isinstance(val, str) and 1 < len(val) < 60:
                            disp_names.setdefault("%s:%s" % (ns, path), val)
                    continue
                m = _LANG_ENTITY_RE.match(key)
                if m:
                    ns, path = m.group(1), m.group(2)
                    if "/" not in path and "." not in path:
                        en.add("%s:%s" % (ns, path))

        # dimensions the mod adds. "Reach this place" is a real milestone and
        # has no item to ask for, so without this a mod's headline feature is
        # invisible to the generator - it is why a Twilight Forest chapter used
        # to open on a decorative portal trophy instead of the portal itself.
        for n in names:
            parts = n.replace("\\", "/").split("/")
            if (len(parts) >= 4 and parts[0] == "data"
                    and parts[2] == "dimension" and n.endswith(".json")):
                # Path below the type dir IS part of the id, so keep it. Latent
                # here - no installed pack nests a dimension - but it is the
                # same mistake as the structure one below, which was live.
                dimensions.setdefault(parts[1], set()).add(
                    "%s:%s" % (parts[1], "/".join(parts[3:])[:-5]))
            # Structures a mod generates. Exploration and worldgen mods add
            # places, not items - a structure task is the only honest way to
            # quest them, and real packs do use it (2.4% of their tasks).
            if (len(parts) >= 5 and parts[0] == "data"
                    and parts[2] == "worldgen" and parts[3] == "structure"
                    and n.endswith(".json")):
                # A structure's id includes every path segment below
                # worldgen/structure, so taking only the filename INVENTS one:
                # data/aquamirae/worldgen/structure/surface/arch.json is
                # aquamirae:surface/arch, and "aquamirae:arch" matches nothing
                # the game will ever generate. The quest can then never
                # complete, and it is not visible until someone plays it.
                # 29 such ids in ArcanumLand, 10 in a large test pack, 7 in THE
                # FORGOTTEN SMP - and zero in the reference pack, which is why the
                # only pack this app was ever tested against could not show it.
                sid = "%s:%s" % (parts[1], "/".join(parts[4:])[:-5])
                structures.setdefault(parts[1], set()).add(sid)
                # The structure file itself says where the place generates -
                # facts a description can be built from when no loot table
                # names what is inside. Without them 10 of this pack's 16
                # structure quests shared one identical "worth the trip"
                # sentence: the single worst blank-beats-filler violation in
                # the book, surviving only because a hardcoded sentence never
                # passes through the >2-repeat uniqueness guard.
                try:
                    sj = _loads_lenient(zf.read(n))
                except Exception:
                    sj = None
                if isinstance(sj, dict):
                    rec = {}
                    if isinstance(sj.get("biomes"), (str, list)):
                        rec["biomes"] = sj["biomes"]
                    if isinstance(sj.get("step"), str):
                        rec["step"] = sj["step"]
                    if isinstance(sj.get("spawn_overrides"), dict):
                        rec["spawn"] = sorted(sj["spawn_overrides"])
                    if rec:
                        struct_info.setdefault(sid, {}).update(rec)
            # How often the place is attempted, from the structure_set that
            # lists it. `spacing` is chunks between placement ATTEMPTS, not a
            # guaranteed distance - any prose built on it must say "roughly".
            if (len(parts) >= 5 and parts[0] == "data"
                    and parts[2] == "worldgen" and parts[3] == "structure_set"
                    and n.endswith(".json")):
                try:
                    ssj = _loads_lenient(zf.read(n))
                except Exception:
                    ssj = None
                if isinstance(ssj, dict):
                    pl = ssj.get("placement")
                    sp = pl.get("spacing") if isinstance(pl, dict) else None
                    if isinstance(sp, (int, float)) and sp > 1:
                        for ent in ssj.get("structures") or []:
                            s = ent.get("structure") if isinstance(ent, dict) else None
                            if isinstance(s, str) and ":" in s:
                                struct_info.setdefault(s, {}) \
                                    .setdefault("spacing", int(sp))
            # Biome tags, so a structure whose `biomes` is "#mod:some_tag" can
            # be resolved to actual biome names at description time. Kept as
            # written (values may nest further #tags); merged across jars
            # because the game merges same-named tag files the same way.
            if (len(parts) >= 6 and parts[0] == "data" and parts[2] == "tags"
                    and parts[3] == "worldgen" and parts[4] == "biome"
                    and n.endswith(".json")):
                tname = "%s:%s" % (parts[1], "/".join(parts[5:])[:-5])
                try:
                    for v in (json.loads(zf.read(n)).get("values") or []):
                        v = v.get("id") if isinstance(v, dict) else v
                        if isinstance(v, str) and v:
                            biome_tags.setdefault(tname, set()).add(v)
                except Exception:
                    pass

        # the mod's own guide book: authored explanations, and a list of the
        # items its author thought worth writing about
        try:
            _scan_guide_books(zf, names, jar_lang, blurbs, book_items)
        except Exception:
            pass

        # A real, showable item needs BOTH an item model (so it renders) and a
        # display name (so it isn't an internal variant). Recipe/tag ids alone
        # let through things like `twilightforest:fiery_helmet_lapis_trim` and
        # `medieval_deco:guillotine_top`, which show as missing-texture squares.
        if model_items and lang_items:
            real = model_items & lang_items
            # A mod can name a placeable food under a "_block" lang key while
            # the ITEM keeps the bare id: bakery:bread is block.bakery.bread_block,
            # with no item.bakery.bread at all. Strict intersection threw away
            # Bakery's whole bread line and Candlelight's Beef Wellington -
            # a cooking mod losing its signature dishes. A model plus a
            # "<name>_block" display name is still a real, showable item.
            suffixed = {i for i in (model_items - lang_items)
                        if "%s_block" % i in lang_items
                        or "%s_item" % i in lang_items}
            # Some items are named by a DOTTED sub-key instead of a flat one:
            # deeperdarker's warden upgrade is
            # item.deeperdarker.smithing_template.warden_upgrade.title, so no
            # flat "item.<mod>.<name>" key exists even though the item does.
            if lang_prefixes:
                suffixed |= {i for i in (model_items - lang_items)
                             if i.split(":", 1)[1] in lang_prefixes}
            # Some mods COMPOSE display names at runtime and ship no per-item
            # key at all. Domum Ornamentum names its blocks from a format
            # string (domum_ornamentum.extra.name.format) and Storage Drawers
            # multiplies a material key by a type key, so the intersection
            # silently dropped brick_extra, cactus_extra and 84 real drawers.
            # A blockstate is proof the block is REGISTERED, which is the thing
            # actually being tested here - the lang key was only ever a proxy.
            suffixed |= {i for i in (model_items - lang_items) if i in all_states}
            # Same argument, one step further: a mod that CRAFTS an id, or
            # writes an advancement for it, has registered it just as surely
            # as a blockstate does. herbalbrews:tea_blossom ships a model, a
            # texture and data/herbalbrews/advancements/main/find_tea_blossom
            # - the mod is telling us to go find this thing - yet no lang key
            # exists, so the intersection dropped the seed the entire mod
            # grows from. A chain researcher found it by reading the jar and
            # the validator then called the researcher wrong.
            suffixed |= {i for i in (model_items - lang_items)
                         if i in recipe_items or i in adv_items}
            real |= suffixed
        elif model_items:
            real = set(model_items)
        else:
            # No models at all: fall back to lang/recipe ids, but only ones in
            # this mod's own namespace. A stray `item.modifiers.both_hands`
            # tooltip key in a library jar is not an item.
            real = {i for i in (set(lang_items) | recipe_items | tag_items)
                    if "/" not in i and i.split(":")[0] == mod_id}
        # drop missing-texture items, and remember how leaky this mod is: a mod
        # that ships many broken models almost certainly ships more that this
        # static check can't see, so it shouldn't drive quests at all.
        before = len(real)
        # AQG_TRACE_ID=<namespaced id> reports where the scan loses one item.
        # Replicating this logic outside the scan gave a different answer than
        # the scan itself, which is exactly when you stop replicating and
        # instrument the real thing.
        _tr = os.environ.get("AQG_TRACE_ID")
        if _tr and _tr.split(":", 1)[0] == mod_id:
            print("[trace] %s model=%s lang=%s state=%s recipe=%s real=%s tex_ok=%s"
                  % (_tr, _tr in model_items, _tr in lang_items,
                     _tr in all_states, _tr in recipe_items, _tr in real,
                     textures_ok(_tr, all_models, all_states,
                                 all_textures, _tex_cache)))
        real = {i for i in real
                if textures_ok(i, all_models, all_states,
                               all_textures, _tex_cache)}
        # An item rendered by code still needs a flat model for the inventory
        # slot, and that model carries a lang key, so "<thing>_inventory"
        # satisfies every test above while being a picture of an item rather
        # than an item. Alex's Mobs says so itself: the display name of
        # alexsmobs:falconry_glove_inventory is "This is not a Falconry Glove".
        # Only when the real id is present too - the suffix alone proves
        # nothing, and neighbouring suffixes are worse than useless: two of the
        # three "_broken" ids nearby are genuine damaged decor blocks.
        real -= {i for i in real
                 if i.endswith("_inventory")
                 and i[:-len("_inventory")] in real}
        # Some of these say so in words, which catches the ones the suffix
        # rule cannot - alexsmobs:falconry_glove_HAND is the same phantom with
        # a different ending, and its display name is "This is not a Falconry
        # Glove". Deliberately narrow. Another lane's first attempt at this
        # matched 109 ids and was badly wrong: "(WIP)" marks eight REAL
        # Actually Additions items, "dummy" is the entire point of
        # dummmmmmy:target_dummy, botania:placeholder is a genuine crafting
        # item, and colourfulgoats:missing_carpet is a real joke cosmetic. So
        # only self-denial and explicit developer markers count.
        real -= {i for i in real if _DISCLAIMED_NAME.search(disp_names.get(i) or "")}
        # ...and blocks with no BlockItem, established above from their own
        # loot tables. Keep any that a RECIPE or a tag names, since that would
        # mean the item form does exist and this table is simply unusual.
        real -= {i for i in (no_block_item & real)
                 if i not in recipe_items and i not in tag_items}
        nbroken = before - len(real)
        if before:
            broken_rate[mod_id] = nbroken / before
        it |= real
        cr |= {i for i in recipe_items if i in real}

        # index colour siblings so _junk_score can tell "dark_wand" (content)
        # from "dark_oak_stairs" (one of sixteen)
        _sib: dict = {}
        for _i in it:
            _sp = _i.split(":", 1)[1].split("_")
            if len(_sp) > 1 and _sp[0] in _COLOUR_WORDS:
                _rest = "_".join(_sp[1:])
                _sib[_rest] = _sib.get(_rest, 0) + 1
        if _sib:
            _COLOUR_SIBLINGS[mod_id] = _sib

        if len(it) > per_mod_cap:
            # The cap keeps the item pool manageable, but it must never drop an
            # id something else in the app depends on. goety ships 1251 real
            # items; the cut removed goety:dark_wand - the mod's signature
            # item, named in its own curated chain - and clean_doc then treated
            # it as fake and swapped it for filler. Pinned ids ride above the
            # cap.
            pinned = set()
            for ref, _t, _d in (_curated_chain(mod_id) or []):
                for cand in str(ref).split("|"):
                    cand = cand.strip()
                    if cand.startswith(("kill:", "dim:", "structure:")):
                        continue
                    if cand in it:
                        pinned.add(cand)
            for _e in (_HARVESTED_DB.get(mod_id) or []):
                if isinstance(_e, dict) and _e.get("item") in it:
                    pinned.add(_e["item"])
            # (the advancement tree is built AFTER this loop, so it cannot be
            # consulted here - curated chains and pack consensus already cover
            # the ids that matter)
            pinned &= it
            ranked = sorted(it - pinned,
                            key=lambda x: (_junk_score(x), x not in cr, x))
            keep = pinned | set(ranked[: max(0, per_mod_cap - len(pinned))])
            items[mod_id] = keep
            craftable[mod_id] = cr & keep
    log("scanned %d mods, %d item IDs (%d craftable), %d entity IDs"
        % (len(mods), sum(len(v) for v in items.values()),
           sum(len(v) for v in craftable.values()),
           sum(len(v) for v in entities.values())))
    # --- progression, straight from each mod's own advancement tree ----------
    # Mod authors encode "do this, then this" in data/<mod>/advancements/*.json.
    # Depth in that tree is a real progression tier, the icon is the item the
    # step is about, and the title is a human name for it. Far better than
    # guessing an order from item names.
    lang_names: dict = {}
    for jar in jars:
        try:
            zl = zipfile.ZipFile(jar)
        except Exception:
            continue
        for nm in zl.namelist():
            if nm.endswith("/lang/en_us.json"):
                try:
                    lang_names.update(json.loads(zl.read(nm).decode("utf-8", "replace")))
                except Exception:
                    pass

    progression: dict = {}
    parent_of = {k: v.get("parent") for k, v in all_advs.items()}

    def _depth(key, seen=None):
        seen = seen or set()
        p = parent_of.get(key)
        if not p or p in seen or p not in parent_of:
            return 0
        return 1 + _depth(p, seen | {key})

    def _root(key, seen=None):
        seen = seen or set()
        p = parent_of.get(key)
        if not p or p in seen or p not in parent_of:
            return key
        return _root(p, seen | {key})

    for key, adv in all_advs.items():
        mod = key.split(":", 1)[0]
        disp = adv.get("display") or {}
        icon = disp.get("icon") or {}
        item = icon.get("item") or icon.get("id")
        if not isinstance(item, str) or ":" not in item:
            continue
        title = disp.get("title")
        if isinstance(title, dict):
            title = lang_names.get(title.get("translate", ""), "") or title.get("text", "")
        if not isinstance(title, str):
            title = ""
        desc = disp.get("description")
        if isinstance(desc, dict):
            desc = lang_names.get(desc.get("translate", ""), "") or desc.get("text", "")
        if not isinstance(desc, str):
            desc = ""
        # advancement text carries runtime placeholders ("Defeat a %s", "%1$s")
        # which would render literally in the quest book — drop those lines
        if "%" in desc:
            desc = ""
        if "%" in title:
            title = ""
        # An advancement describes an ACTION, and only some of those actions
        # are "get this item". "Kill a FNAF Animatronic" was attached to the
        # Faz-Wrench and printed as the description of a quest to COLLECT a
        # wall trim - 46 quests carried an instruction that contradicted their
        # own task. Keep the authored line only when it is about obtaining.
        #
        # Two holes, both found by chasing one surviving quest ("collect BaG
        # Wall Bottom (Tile Trim)" described as "Get killed by a Blood & Gears
        # Animatronic"). First, every verb listed was one the PLAYER performs,
        # so a death advancement - written from the receiving side - walked
        # straight through. Eight of this pack's 1222 authored lines open that
        # way and not one is a way to obtain anything. Anchored, like the list
        # above: "getting hit" buried mid-sentence is description rather than
        # instruction, and matching anywhere costs a good Doppelganger blurb to
        # gain nothing. Second, and the reason that quest survived a fix to the
        # blurb below, the guard sat AFTER the progression row was appended -
        # and the chapter builder reads its description from r["desc"], not
        # from the blurbs. One line cannot be true for the item in one dict and
        # false for it in the other, so the guard belongs above both.
        if re.match(r"\s*(kill|slay|defeat|destroy|enter|reach|visit|travel|"
                    r"survive|die|explore|ride|tame|breed|trade)\b"
                    r"|\s*(get|got|be|being|getting)\s+(killed|crushed|damaged|"
                    r"hit|hurt|attacked|slain|eaten|struck|burned|burnt|blown)\b"
                    r"|\s*(died|dies)\b|\s*(take|taking)\s+damage\b", desc, re.I):
            desc = ""
        # The verb list is a wording heuristic, and 41 of this pack's 53
        # icon-only impossible/death advancement lines walk straight past it
        # ("Experience the entire Mr. Hippo cutscene!", "Oops.. Get crushed by
        # a FNAF: MW Door.." - the anchor never sees past "Oops"). There is a
        # structural tell that needs no wording: when the advancement's ONLY
        # mention of the item is its display icon AND its criteria fire on
        # minecraft:impossible (granted from code) or on the player being
        # killed, it is not a recipe for getting the item - the icon is set
        # dressing for the advancement screen. The trigger gate is deliberate:
        # Create's icon-only lines ("Here Be Contraptions") fire on
        # inventory_changed and are genuinely about their items - icon-only
        # alone would blank five true Create sentences. The known cost is a
        # few Botania lines (its code-granted advancements all fire on
        # impossible, and "Create a Mana Enchanter" really is about the
        # enchanter); blank beats a wall of FNAF cutscene instructions on
        # collect quests, and the researched Botania chain covers those items
        # anyway.
        if desc:
            _trigs = {str((c or {}).get("trigger", ""))
                      for c in (adv.get("criteria") or {}).values()
                      if isinstance(c, dict)}
            if _trigs and _trigs <= {"minecraft:impossible",
                                     "minecraft:entity_killed_player"} \
                    and item not in json.dumps(
                        {k: v for k, v in adv.items() if k != "display"}):
                desc = ""
        # "challenge" / "hardmode" trees are post-game extras, not the main
        # progression — push them past everything else
        extra = 100 if any(w in _root(key).lower()
                           for w in ("challenge", "hardmode", "secret")) else 0
        progression.setdefault(mod, []).append(
            {"depth": _depth(key) + extra, "item": item,
             "title": title.strip()[:60], "desc": desc.strip()[:160]})
        # Keep the authored line against the ITEM as well. Mods write real
        # how-to text here ("Obtain an Acacia Blossom from breaking Acacia
        # Leaves") and it beats any template we could generate - but until now
        # it only reached quests that came from the advancement path.
        d2 = desc.strip()
        if 12 <= len(d2) <= 160 and item not in blurbs:
            blurbs[item] = d2
    for mod, rows in progression.items():
        rows.sort(key=lambda r: (r["depth"], r["item"]))
        seen_i = set()
        progression[mod] = [r for r in rows
                            if not (r["item"] in seen_i or seen_i.add(r["item"]))]
    usable = {m: r for m, r in progression.items() if len(r) >= 4}
    if usable:
        log("  progression trees from advancements: %s"
            % ", ".join("%s(%d)" % (m, len(r))
                        for m, r in sorted(usable.items(), key=lambda x: -len(x[1]))[:6]))

    leaky = sorted((k for k, v in broken_rate.items() if v > BROKEN_MOD_LIMIT), key=str)
    if leaky:
        log("  ! %d mod(s) ship broken textures, skipped for quests: %s"
            % (len(leaky), ", ".join(leaky[:6]) + (" ..." if len(leaky) > 6 else "")))
    vanilla = _vanilla_items_from_client(folder)
    if vanilla:
        log("  vanilla item list read from the client jar (%d ids)" % len(vanilla))
    else:
        vanilla = set(_VANILLA_ITEMS)
    # filled by the same jar walk, and only when the version matched exactly.
    # Log it like the item list so a missing jar is visible rather than quietly
    # turning every vanilla title into a bare name.
    vanilla_craftable = set(_VANILLA_CRAFTABLE)
    if vanilla_craftable:
        log("  vanilla recipes read from the client jar (%d craftable ids)"
            % len(vanilla_craftable))
    else:
        log("  ! no vanilla recipe data - vanilla quest titles will not claim "
            "an item is craftable")
    # A tag ingredient is a CHOICE, not a requirement: "any ingot" is satisfied
    # by the cheapest ingot the player can get. Merging every member into the
    # required set made each recipe inherit its deepest possible ingredient and
    # pushed create:andesite_alloy, Create's opening item, to depth 23.
    craft_alts: dict = {}
    resolved = 0
    for out_id, ings in craft_edges.items():
        for g in [x for x in ings if x.startswith("#")]:
            ings.discard(g)
            members = tag_members.get(g[1:])
            if members:
                craft_alts.setdefault(out_id, []).append(set(members))
                resolved += 1
    if resolved:
        log("  resolved %d tag ingredient(s) from %d item tags"
            % (resolved, len(tag_members)))
    tiers = _craft_tiers(craft_edges, vanilla=vanilla, alts=craft_alts)
    if tiers:
        log("  crafting tiers derived from %d recipes (max depth %d)"
            % (len(craft_edges), max(tiers.values())))
    # Now that every jar has been read we know which mods are PRESENT, so a
    # recipe requiring an absent one can be recognised as unusable. An item
    # whose every route needs a mod this pack does not ship is not craftable
    # here, whatever its recipes say.
    _present = set(items) | {"minecraft", "forge", "c"}
    missing_dep: dict = {}
    for _o, _reqs in recipe_reqs.items():
        if any(r <= _present for r in _reqs):
            continue                      # at least one route is usable
        # Only interesting when the item's OWN mod is installed. An id from a
        # mod this pack does not ship was never questable anyway; the case
        # that matters is a mod that IS here whose recipe needs one that is
        # not - dreadsteel gating 10 of its 11 items behind iceandfire while
        # declaring no such dependency.
        if _o.split(":", 1)[0] not in items:
            continue
        # A blocked RECIPE is not an unobtainable item. goety:ectoplasm needs
        # a mod this pack lacks, and drops from crypt loot regardless - it is
        # in a curated chain, so treating "no usable recipe" as "unobtainable"
        # would have deleted a legitimate quest. Only items with no loot
        # source either are genuinely unreachable.
        if _o in loot_items:
            continue
        _absent = set()
        for r in _reqs:
            _absent |= (r - _present)
        if _absent:
            missing_dep[_o] = sorted(_absent)
    if missing_dep:
        _by = {}
        for _o in missing_dep:
            _by.setdefault(_o.split(":", 1)[0], 0)
            _by[_o.split(":", 1)[0]] += 1
        log("  %d item(s) need a mod this pack does not ship: %s"
            % (len(missing_dep),
               ", ".join("%s x%d" % kv for kv in
                         sorted(_by.items(), key=lambda kv: -kv[1])[:4])))
    # UNOBTAINABLE: an item nothing can give you. No recipe, no loot table
    # entry, no item-tag membership, and not the result of any craft edge.
    #
    # A release gate found four Cataclysm quests asking the player to HOLD
    # blocks that exist only as placed worldgen inside structures -
    # altar_of_fire, door_of_seal, emp, cursed_tombstone - each with 0 own
    # recipes, 0 own loot tables, and 0 mentions in any of that mod's 195
    # recipe or 173 loot files. One gated five more quests: 8 dead in one
    # chapter. The same class reproduced on a second pack from a different
    # mod, so it is the missing GATE, not a Cataclysm quirk.
    #
    # Deliberately narrower than missing_dep, which is about a recipe blocked
    # by an absent mod. This is about an item with no route at all. The
    # generator already routes 19 quests in that same chapter to structure
    # tasks correctly - these should have been structure tasks too.
    # PHANTOM IDS: an item whose namespace is in the pack's DATA but not its
    # ASSETS is not in this pack at all. Mods ship compat recipes for mods you
    # may not have installed - ConfluenceOtherworld carries a whole
    # data/confluence/recipe tree for terra_entity, terra_guns, terra_curio
    # and terra_furniture - and the recipe-rescue path was reading those
    # outputs as real items. Measured on that pack: 487 offered ids from four
    # namespaces that ship no assets whatsoever, 129 of which reached the
    # book. In game every one is a red "Missing Item" on a quest that can
    # never be completed.
    #
    # The test is the one the game itself applies: does this id have an item
    # model or a blockstate anywhere in the pack? The core models-and-lang
    # path already guarantees that; only the rescues (recipes, advancements,
    # loot, tags) can add an id without one. Cost on a pack with no phantom
    # namespaces is nil - the reference pack loses 12 of 17,998, all of them ids
    # that would have rendered as a missing-model cube.
    _phantom = set()
    for _mid, _ids in items.items():
        for _i in _ids:
            ns, _, short = str(_i).partition(":")
            if ("%s:item/%s" % (ns, short) in all_models
                    or "%s:block/%s" % (ns, short) in all_models
                    or "%s:%s" % (ns, short) in all_states
                    or _i in vanilla):
                continue
            _phantom.add(_i)
    if _phantom:
        log("  dropped %d phantom item id(s) - no model or blockstate ships "
            "for them, so they are compat entries for mods this pack does "
            "not have" % len(_phantom))
        for _mid in list(items):
            _keep = [i for i in items[_mid] if i not in _phantom]
            # preserve the container type - downstream does |= against these
            items[_mid] = set(_keep) if isinstance(items[_mid], set) else _keep

    _tagged: set = set()
    for _v in (tag_members or {}).values():
        _tagged |= set(_v)
    _craftable_any: set = set()
    for _v in craftable.values():
        _craftable_any |= set(_v)
    unobtainable = set()
    for _mid, _ids in items.items():
        for _i in _ids:
            if (_i in _craftable_any or _i in loot_items or _i in _tagged
                    or craft_edges.get(_i) or _i in drops):
                continue
            unobtainable.add(_i)
    if unobtainable:
        log("  %d item(s) have no recipe, loot table or tag - they will not "
            "be used as quest goals" % len(unobtainable))

    # Reported whether or not anything was found. The case that actually hurt
    # was NOT the empty scan - it was 80 jars of which one was readable, which
    # built a four-chapter book and looked like a success.
    blocked = _coverage_note(folder, len(mods))
    if blocked:
        log("  %s %s" % ("NOTHING TO BUILD:" if not mods
                         else "ONLY PART OF THIS PACK COULD BE READ:", blocked))
    out = {"blocked": blocked,
           "mods": mods, "items": items, "entities": entities,
           "missing_dep": missing_dep, "unobtainable": unobtainable,
           "craftable": craftable, "broken_rate": broken_rate,
           "progression": progression, "vanilla": vanilla, "tier": tiers,
           "vanilla_craftable": vanilla_craftable,
           "edges": craft_edges, "craft_recipes": craft_recipes,
           "alts": craft_alts, "blurbs": blurbs,
           "book_items": book_items, "dimensions": dimensions,
           "names": disp_names, "structures": structures,
           "struct_info": struct_info, "biome_tags": biome_tags,
           "struct_loot": struct_loot, "drops": drops,
           "backdrops": backdrops}
    # Applied here rather than left to callers: a rule that only fires when
    # two mods are installed together is exactly the kind a caller forgets.
    _shadow = drop_shadowed(out)
    if _shadow:
        log("  dropped %d id(s) that a co-installed mod silently replaces"
            % _shadow)
    if _ckey:
        # Saved AFTER drop_shadowed so a hit replays the finished scan, not a
        # half-processed one. The cache is an optimisation: failing to write
        # it (disk full, read-only install dir) must never fail the scan.
        try:
            with open(_cpath, "wb") as _fh:
                pickle.dump({"key": _ckey, "scan": out}, _fh,
                            protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            pass
    return out


# ========================================================================== #
#  3. Prompt building
# ========================================================================== #

DENSITY_TARGETS = {
    "tiny":     "40-70",
    "small":    "110-170",
    "normal":   "250-380",
    "large":    "450-650",
    "massive":  "750-1050",
    "colossal": "1200-1700",
}
DENSITY_ORDER = ["tiny", "small", "normal", "large", "massive", "colossal"]
# midpoint used by the offline builder (real packs: median ~29 quests/chapter)
DENSITY_MID = {"tiny": 55, "small": 140, "normal": 310, "large": 550,
               "massive": 900, "colossal": 1450}

# Derived, so the prose the AI is given and the numbers the offline builder acts
# on can never drift apart. Same strings as before; every call site is unchanged.
AESTHETIC_TEXT = {k: v["prompt"] for k, v in AESTHETIC_LEVELS.items()}
REWARD_TEXT = {
    "lean": "Rewards are small: a few items or 20-50 xp.",
    "standard": "Rewards are moderate: a useful item stack and 50-150 xp per quest.",
    "generous": "Rewards are plentiful: multiple item stacks, rare bonus items on "
                "milestone quests, and 100-400 xp.",
}
PROGRESSION_TEXT = {
    "linear": "Strict main line: almost every quest depends on the previous one.",
    "loose": "Mostly linear with several optional branches that rejoin the main line.",
    "open": "Web-like: chapters unlock early, many parallel paths, few hard gates.",
}


def _functional_ids(ids, cap):
    """Keep the caller's ordering — it has usually already ranked these by
    usefulness. Only drop the obvious shape/decoration variants."""
    ids = list(ids)
    keep = [x for x in ids if _junk_score(x) < 2]
    if len(keep) < min(cap, 8):
        keep = [x for x in ids if _junk_score(x) < 3] or ids
    return keep[:cap]


# Text fields the AI is allowed to touch in prose-only mode, and nothing else.
_PROSE_FIELDS = ("title", "subtitle", "description")


def merge_prose_only(base: dict, ai: dict, log=lambda *_a: None) -> tuple:
    """Take ONLY the wording from an AI reply; keep all structure from base.

    The split matters because the two sides know different things. The offline
    builder holds the research - 300+ progression chains, guide entry rituals,
    obtainability, ordering, the immersion checks - and the model holds none of
    it. The model is better at one thing: how a sentence reads.

    So the model is not asked to be trustworthy, it is prevented from being
    structural. Quests, tasks, rewards, dependencies, ids, positions and shapes
    all come from base; only title/subtitle/description are taken from the
    reply, and only for quests that already exist. A reply that invents a quest,
    swaps an item or drops a chapter changes nothing.

    The previous "improve" mode asked the model to add rewards, fix item ids and
    invent 2-3 new quests per chapter - exactly the operations that produced
    uncompletable content when the generator did them without a gate. -> (doc, stats)
    """
    out = json.loads(json.dumps(base))          # deep copy, base wins by default
    ai_q = {}
    for ch in (ai.get("chapters") or []):
        if not isinstance(ch, dict):
            continue
        for q in (ch.get("quests") or []):
            if isinstance(q, dict) and q.get("id"):
                ai_q[str(q["id"])] = q
    ai_ch = {str(c.get("id") or c.get("title") or ""): c
             for c in (ai.get("chapters") or []) if isinstance(c, dict)}

    changed = kept = ignored = 0
    for ch in (out.get("chapters") or []):
        src_c = ai_ch.get(str(ch.get("id") or "")) or ai_ch.get(str(ch.get("title") or ""))
        if isinstance(src_c, dict):
            for f in ("title", "subtitle", "description"):
                v = src_c.get(f)
                if isinstance(v, (str, list)) and v and v != ch.get(f):
                    ch[f] = v
                    changed += 1
        for q in (ch.get("quests") or []):
            src = ai_q.get(str(q.get("id") or ""))
            if not isinstance(src, dict):
                kept += 1
                continue
            for f in _PROSE_FIELDS:
                v = src.get(f)
                if isinstance(v, (str, list)) and v and v != q.get(f):
                    q[f] = v
                    changed += 1
    # GROUP NAMES - the shelf headings running down the side of the book.
    # Renaming these is fair game and the model is good at it, but a group is
    # SHARED by several chapters, so a rename has to be consistent or it
    # silently splits one shelf into two. Collect old -> new per chapter and
    # apply only the renames every chapter agreed on.
    prop: dict = {}
    for ch in (base.get("chapters") or []):
        old_g = str(ch.get("group") or "").strip()
        if not old_g:
            continue
        src_c = (ai_ch.get(str(ch.get("id") or ""))
                 or ai_ch.get(str(ch.get("title") or "")))
        new_g = str((src_c or {}).get("group") or "").strip()
        if new_g and new_g != old_g:
            prop.setdefault(old_g, set()).add(new_g)
    renames = {o: next(iter(v)) for o, v in prop.items() if len(v) == 1}
    split = sorted(o for o, v in prop.items() if len(v) > 1)
    if renames:
        for ch in (out.get("chapters") or []):
            g = str(ch.get("group") or "").strip()
            if g in renames:
                ch["group"] = renames[g]
        changed += len(renames)
        log("  group renames: " + ", ".join(
            "%s -> %s" % kv for kv in sorted(renames.items())))
    if split:
        log("  ignored inconsistent rename of group(s) " + ", ".join(split)
            + " - a group is shared by several chapters, and renaming it "
              "differently in each would split it in two")

    # anything the model invented that base never had
    base_ids = {str(q.get("id")) for c in (base.get("chapters") or [])
                for q in (c.get("quests") or [])}
    ignored = len([i for i in ai_q if i not in base_ids])
    log("  prose merge: %d fields rewritten, %d quests untouched, "
        "%d invented quests ignored" % (changed, kept, ignored))
    return out, {"changed": changed, "kept": kept, "ignored": ignored}


def build_prose_prompt(doc: dict, language: str, opts: dict) -> str:
    """Ask for wording only, and say plainly that structure will be discarded."""
    lines = [
        "You are rewriting ONLY THE WORDING of an existing FTB Quests book "
        "(Minecraft 1.20.1). Everything else is already correct and verified.",
        "",
        "Return the SAME JSON shape. For each quest you may change ONLY:",
        '  "title"       - short, specific, memorable, no "Craft X" / "Obtain X"',
        '  "subtitle"    - one short line, optional',
        '  "description" - array of lines; say WHY the player wants this thing',
        "",
        "And for each CHAPTER you may change:",
        '  "title"    - the chapter name in the book',
        '  "group"    - the shelf heading it sits under (its theme)',
        "",
        "Several chapters share one group. Rename a group the SAME WAY in "
        "every chapter that uses it, or the rename is thrown away - renaming "
        "it differently in each would split one shelf into two.",
        "",
        "Do NOT add, remove or reorder quests or chapters. Do NOT change any "
        "id, task, item, count, reward, dependency, icon, size or position. "
        "Those fields are ignored on the way back in, so changing them only "
        "wastes your output.",
        "",
        "Write like a pack author, not a wiki. A description earns its place by "
        "answering something the task line does not already show - what the "
        "item unlocks, where it is found, what the trap is. If there is nothing "
        "true to add, leave the description as it is.",
        "[STYLE] " + AESTHETIC_TEXT.get(opts.get("aesthetic", "balanced"), ""),
        "All text in %s." % language,
        "",
        "BOOK:",
        json.dumps(doc, ensure_ascii=False),
        "",
        "Output only the JSON.",
    ]
    return chr(10).join(lines)


def build_prompt(scan: dict, selected_ids: list, language: str, opts: dict) -> str:
    density = opts.get("density", "normal")
    target = str(opts.get("target", "")).strip() or DENSITY_TARGETS.get(density, "120-180")
    use_groups = opts.get("groups", True)
    aesthetic = opts.get("aesthetic", "balanced")
    reward = opts.get("reward", "standard")
    progression = opts.get("progression", "linear")
    layout = opts.get("layout", "line")
    creativity = float(opts.get("creativity", 0.3))
    n_side = 1 + round(creativity * 3)

    sel = set(selected_ids)
    mods = [m for m in scan["mods"] if m["mod_id"] in sel] if sel else list(scan["mods"])
    core = [m for m in mods if m["category"] in ("tech", "magic", "world", "mob")]
    support = [m for m in mods if m["category"] in ("utility", "food", "decor")]
    other = [m for m in mods if m["category"] == "unknown"]
    decor_ids = sorted({m["mod_id"] for m in mods if m["category"] == "decor"})

    L = []
    L.append("You are a Minecraft modpack quest-book designer for FTB Quests (MC 1.20.1).")
    L.append("Design a rich 'guide book' that teaches players every mod's content.")
    L.append("")
    L.append("[STRUCTURE]")
    L.append("1. Vanilla MC: 3-5 chapters, 10-15 quests each. start -> stone -> iron -> "
             "diamond -> enchant -> nether -> brewing -> end.")
    L.append("2. Each core mod: 1-2 chapters, 10-15 quests each, intro -> mastery.")
    L.append("3. Support mods: reference their items in rewards.")
    L.append("4. Total quests: %s (main line + side quests)." % target)
    if use_groups:
        L.append('5. Give every chapter a "group" field ("Vanilla", "Tech", "Magic", '
                 '"Adventure", "Support"...) so chapters are grouped in the book.')
    L.append("")
    themes = [t for t in (opts.get("themes") or []) if str(t).strip()]
    if themes:
        L.append("")
        L.append("[THEMED QUESTLINES] The player asked for these questlines by name:")
        for t in themes:
            L.append("  - %s" % t)
        L.append("Give EACH one its own chapter whose every quest serves that idea, "
                 "ordered from the first thing a player can reach to the mastery item. "
                 "Pull items from any of the mods listed below plus vanilla, as long "
                 "as they genuinely fit the theme.")
        if opts.get("themes_only", True):
            L.append("Build ONLY these chapters - do not add a chapter per mod.")
    L.append("")
    L.append("[STYLE] " + AESTHETIC_TEXT.get(aesthetic, AESTHETIC_TEXT["balanced"]))
    if decor_ids and aesthetic in ("decorated", "lavish"):
        L.append("Decoration mods available for showcase quests / rewards: " + ", ".join(decor_ids))
    L.append("[REWARDS] " + REWARD_TEXT.get(reward, REWARD_TEXT["standard"]))
    L.append("[PROGRESSION] " + PROGRESSION_TEXT.get(progression, PROGRESSION_TEXT["linear"]))
    L.append("[SIDE QUESTS] ~%d optional side quest(s) per chapter, each attached to a "
             "main-line quest via `dependencies`." % n_side)
    if creativity >= 0.5:
        L.append("[CREATIVITY] Be inventive: unexpected quest concepts, varied task types, "
                 "playful titles, hidden/bonus quests, mini goal-chains. Avoid repetition.")
    L.append("")
    L.append("[DEPENDENCIES] Chain with `dependencies` (quest ids). The first quest of "
             "chapter N depends on the last main-line quest of chapter N-1. IDs are global.")
    L.append("")
    if layout in ("ai", "ai+tidy"):
        L.append("[LAYOUT] You place the quests: give every quest x and y so the chapter "
                 "looks like a designed diagram - main spine, branches fanning out, "
                 "no overlaps. Spacing ~2.5 between quests.")
    else:
        L.append("[LAYOUT] Coordinates are auto-generated later; still give each quest x "
                 "and y roughly (x = 2*step along the line, y offsets for branches).")
    L.append("")
    L.append("[TASK TYPES] pick the fitting one per quest:")
    L.append('  item {"type":"item","item":"<id>","count":N}   '
             'kill {"type":"kill","entity":"<id>","value":N}')
    L.append('  advancement {"type":"advancement","advancement":"minecraft:story/..."}   '
             'dimension {"type":"dimension","dimension":"<id>"}')
    L.append('  biome {"type":"biome","biome":"<id>"}   structure {"type":"structure","structure":"<id>"}')
    L.append('  location {"type":"location","dimension":"<id>","x":X,"y":Y,"z":Z,"w":8,"h":8,"d":8}')
    L.append('  stat {"type":"stat","stat":"minecraft:mob_kills","value":N}   '
             'checkmark {"type":"checkmark","title":"..."}   '
             'fluid / energy for tech mods')
    L.append("Use kill/structure/biome/location for adventure & dimension mods (Twilight Forest, "
             "Cataclysm, Tropicraft...). Use item/advancement/fluid/energy for tech & magic mods.")
    L.append('[REWARD TYPES] item {"type":"item","item":"<id>","count":N}   '
             'xp {"type":"xp","xp":N}   command {"type":"command","command":"/..."}   '
             'toast {"type":"toast","description":"..."}   loot/choice {"type":"choice","table":"<id>"}')
    L.append('[REWARD TABLES] optionally add a top-level "reward_tables":[{"id":"rare","title":"...",'
             '"loot_size":1,"rewards":[{"item":"minecraft:diamond","count":2,"weight":10},'
             '{"type":"xp","xp":200,"weight":4}]}] and reference them from quest rewards with '
             '{"type":"choice","table":"rare"}.')
    L.append("Use ONLY the item/entity IDs listed below; never invent IDs.")
    L.append("")
    L.append("[OUTPUT] Only JSON, no markdown, no prose:")
    L.append('{"title":"...","chapters":[{"id":"ch1","group":"Vanilla","title":"...",'
             '"icon":"minecraft:book","quests":[{"id":"c1q1","x":0.0,"y":0.0,"title":"...",'
             '"description":["..."],"dependencies":["c1q0"],'
             '"tasks":[{"type":"item","item":"minecraft:oak_log","count":16}],'
             '"rewards":[{"type":"item","item":"minecraft:apple","count":4}]}]}]}')
    L.append("Quest ids must be unique across the whole book. All text in %s." % language)
    L.append("")

    descs = opts.get("mod_desc") or {}

    def block(title, group):
        L.append("=== %s ===" % title)
        for m in group:
            d = descs.get(m["mod_id"]) or m.get("description") or ""
            L.append("- %s (modid:%s) %s" % (m["name"], m["mod_id"], d[:280]))
        L.append("")

    if core:
        block("CORE MODS (gameplay / progression)", core)
    if support:
        block("SUPPORT MODS (tools / storage / food / decor)", support)
    if other:
        block("OTHER MODS", other)

    # ---- item / entity id lists, on a hard character budget ----------------
    # Unbounded this reached 256,000 chars for a 114-mod pack — a ~68k-token
    # prompt that made the model chew until the request timed out. Give every
    # mod a fair share of a fixed budget, best (craftable, least junky) first.
    budget = int(opts.get("id_budget", 45000))
    craft_map = scan.get("craftable", {})
    with_ids = [m for m in mods if scan["items"].get(m["mod_id"])]
    per_mod = max(12, budget // max(1, len(with_ids)) // 26) if with_ids else 0

    L.append("=== VERIFIED ITEM IDs — use ONLY these (any minecraft: id is also fine) ===")
    L.append("A  *  before an id means it is craftable / obtainable in survival — "
             "STRONGLY prefer starred ids for \"item\" tasks. Unstarred ids exist but may "
             "be creative-only or a rare drop; fine as rewards, risky as a task goal.")
    L.append("Each mod shows its best ids, not all of them. If you need an item a mod "
             "obviously has but isn't listed, use a listed one instead - never invent an id.")
    spent = 0
    for m in mods:
        ids = sorted(scan["items"].get(m["mod_id"], []))
        if not ids:
            continue
        cr = craft_map.get(m["mod_id"], set())
        # craftable + low junk score first, so the visible slice is the useful slice
        ranked = sorted(ids, key=lambda x: (_junk_score(x), x not in cr, x))
        show = _functional_ids(ranked, per_mod)
        line = "  " + ", ".join(("*" + i) if i in cr else i for i in show)
        if spent + len(line) > budget:
            L.append("  ... remaining mods omitted for length")
            break
        spent += len(line)
        L.append("%s (%d ids, showing %d):" % (m["mod_id"], len(ids), len(show)))
        L.append(line)
    L.append("")
    L.append("=== VERIFIED ENTITY IDs (kill tasks) ===")
    for m in mods:
        ids = _mob_entities(scan["entities"].get(m["mod_id"], ()),
                            set(scan["items"].get(m["mod_id"], ())))
        if ids:
            L.append("%s: %s" % (m["mod_id"], ", ".join(ids[:30])))
    L.append("")
    L.append("Output the JSON now.")
    return "\n".join(L)


# --- batched generation -----------------------------------------------------
# One giant call for a whole book means a single provider hiccup loses
# everything (and a 500+ quest answer can't fit a token cap anyway). Instead:
# ask for a chapter PLAN, then fill one chapter per call, falling back to the
# offline builder for any chapter the model fumbles.

def _plan_mods(scan: dict, selected_ids: list, opts: dict) -> list:
    sel = set(selected_ids)
    mods = [m for m in scan["mods"] if not sel or m["mod_id"] in sel]
    # A library is never a quest subject, even when it is in the selection.
    # "All mods ticked" is not a deliberate choice of GeckoLib, and its only
    # items are dev placeholders - which produced "Opening GeckoLib 4" quests.
    # Explicitly picking a SMALL set still honours the user's intent.
    # The comment above and this test used to disagree, and a QA pass caught
    # it: picking a library built a whole chapter from its dev placeholders.
    # Kiwi's read "Item2", "Item3", "Recover Item4"; GeckoLib's said "Turn on
    # rain to see the fertilizer model!". Both ship ZERO data/ entries, so
    # every one of those quests is uncompletable.
    # Deciding by INTENT was the wrong axis. A library that genuinely ships
    # obtainable items is fine to quest; one whose items have no recipe and no
    # loot anywhere is not, however deliberately it was picked.
    mods = [m for m in mods
            if not is_library(m["mod_id"], m["name"])
            or (m["mod_id"] in sel and len(sel) <= 8
                and not is_curated_library(m["mod_id"])
                and library_has_real_items(m["mod_id"], scan))]
    if not opts.get("include_decor", False):
        # Same deliberateness test as the library filter above: "all mods
        # ticked" fills sel with EVERY mod id, so a bare `in sel` let every
        # decor mod through and include_decor True/False built byte-identical
        # books (self_audit dead-options; md5 9b9d5155 both ways). Only an
        # explicit SMALL selection exempts a decor mod.
        mods = [m for m in mods
                if (m["mod_id"] in sel and len(sel) <= 8)
                or m["category"] != "decor"]
    brate = scan.get("broken_rate", {})
    # Never exempt a mod that ships missing textures, even when picked - it
    # renders as pink-and-black boxes in the book either way. "All mods
    # selected" is not a deliberate choice of a broken mod.
    mods = [m for m in mods if brate.get(m["mod_id"], 0) <= BROKEN_MOD_LIMIT]
    return [m for m in mods
            if scan["items"].get(m["mod_id"]) or scan["entities"].get(m["mod_id"])]


def build_plan_prompt(scan: dict, selected_ids: list, language: str, opts: dict) -> str:
    """Small prompt: just the chapter outline, no quests."""
    density = opts.get("density", "normal")
    target = str(opts.get("target", "")).strip() or DENSITY_TARGETS.get(density, "120-180")
    themes = [t for t in (opts.get("themes") or []) if str(t).strip()]
    mods = _plan_mods(scan, selected_ids, opts)
    descs = opts.get("mod_desc") or {}

    L = ["You are planning the chapter outline of a Minecraft FTB Quests book (MC 1.20.1).",
         "Do NOT write any quests yet - only the list of chapters.", ""]
    if themes:
        L.append("The player asked for these questlines by name - give each one a chapter:")
        for t in themes:
            L.append("  - %s" % t)
        if opts.get("themes_only", True):
            L.append("Plan ONLY these chapters.")
    if not themes or not opts.get("themes_only", True):
        L.append("Total quests across the whole book should land near %s." % target)
        if opts.get("vanilla_chapters", True):
            L.append("Open with 2-3 vanilla progression chapters (wood -> iron -> "
                     "diamond -> nether -> end), then one chapter per substantial mod.")
        else:
            L.append("One chapter per substantial mod.")
        L.append("")
        L.append("MODS AVAILABLE (modid - name - what it is):")
        for m in mods[:60]:
            d = (descs.get(m["mod_id"]) or m.get("description") or "")[:110]
            L.append("  %s - %s - %s" % (m["mod_id"], m["name"], d))
    L.append("")
    if opts.get("groups", True):
        L.append('Give every chapter a "group": Vanilla, Tech, Magic, Adventure, '
                 '"Farm and Food", Utility, Decoration or Expansion.')
    L.append('"focus" must be the modid the chapter is about, or "vanilla", '
             'or the theme name.')
    L.append("Order chapters so a player can progress through them front to back.")
    L.append("All text in %s." % language)
    L.append("")
    L.append("Reply with ONLY this JSON:")
    L.append('{"title":"<book name>","chapters":[{"id":"ch1","group":"Vanilla",'
             '"title":"Overworld Beginnings","icon":"minecraft:crafting_table",'
             '"focus":"vanilla","quests":12,"summary":"one line on what it covers"}]}')
    return "\n".join(L)


def _vanilla_flooded(quests: list, focus: str) -> bool:
    """True when a chapter that is supposed to be about a mod came back as
    almost nothing but vanilla items.

    This is the shape of a silent prompt failure rather than a taste call: a
    Create chapter whose every task is minecraft:* means the model was handed
    the wrong item list. Cheap to check, and it turns an invisible whole-book
    regression into one rebuilt chapter.
    """
    if not focus or focus == "vanilla":
        return False
    seen = 0
    modded = 0
    for q in quests or []:
        for t in (q.get("tasks") or []):
            it = t.get("item")
            if isinstance(it, str) and ":" in it:
                seen += 1
                if not it.startswith("minecraft:"):
                    modded += 1
    return seen >= 5 and modded <= seen * 0.15


# Description styling. FTB renders & codes, so a description can carry emphasis
# instead of being a wall of flat white text. Kept to a small vocabulary so the
# book reads consistently rather than like a ransom note.
DESC_BODY = "&7"        # grey body - easier on the eye than default white
DESC_KEY = "&e"         # the item or mechanic being named
DESC_WARN = "&6"        # something the player must not miss
DESC_FLAV = "&8&o"      # dark italic flavour aside


def _load_playthrough() -> tuple:
    """What a player actually hit while playing. -> (position, confusion)

    Guides teach the intended path; a playthrough shows the real one, and the
    thing it gives that nothing else can is CONFUSION - a skilled player
    saying, in the moment, that they cannot tell what something does. Those
    lines are quest descriptions writing themselves.

    Three lanes wrote three slightly different shapes, so this reads all of
    them. Version discipline is enforced here rather than trusted: an item
    marked exists_in_1201 false is dropped outright, because a phantom id
    makes an uncompletable quest, while a REACTION is kept regardless of the
    pack's version - confusion about a mechanic is not version-scoped.
    """
    pos: dict = {}
    cross: dict = {}                 # positions seen only in an off-version pack
    conf: dict = {}
    d = moddb_path()
    for f in sorted(d.glob("playthrough_*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue                     # a lane wrote a bare list, not items
        for iid, rec in (doc.get("items") or {}).items():
            if not isinstance(rec, dict) or ":" not in iid:
                continue
            if rec.get("exists_in_1201") is False:
                continue                 # a phantom id is an uncompletable quest
            # MEDIAN beats first-appearance where it exists. An agent found
            # tconstruct:repair_kit at first=0.19 but median=0.93 - mentioned
            # early, actually USED at the end - so gating on first appearance
            # would place it about 35 episodes too early. First appearance is
            # when a thing is named; the median is when it matters.
            v = rec.get("median_progression")
            if v is None:
                v = rec.get("first_progression")
            if v is None:
                v = rec.get("first_position")
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = None
            if v is not None and 0.0 <= v <= 1.0:
                # A pack on ANOTHER Minecraft version still shows a real
                # player's route, and for a mod whose tree did not change
                # between versions that route is perfectly good evidence - 25
                # ids in this pack are positioned by nothing else, all of them
                # Create and Building Gadgets, whose progression is identical
                # either side. But it must never outrank an on-version
                # observation. Taking a plain minimum let it do exactly that:
                # enderio:grains_of_infinity went from 1.00 to 0.00, endgame
                # to opening quest, on the strength of an off-version pack -
                # 22 ids were being pulled earlier this way.
                if rec.get("cross_version"):
                    cross[iid] = min(cross.get(iid, 1.0), v)
                else:
                    pos[iid] = min(pos.get(iid, 1.0), v)
            for q in (rec.get("quotes") or []):
                if not isinstance(q, dict):
                    continue
                kind = str(q.get("kind") or q.get("tone") or "")
                if "confus" not in kind:
                    continue
                t = " ".join(str(q.get("quote") or q.get("text") or "").split())
                if not (25 < len(t) < 400):
                    continue
                # The quote must be ABOUT this item, not merely near it. The
                # lanes attach a reaction to whatever id was mentioned in the
                # window, which produced "I don't know what kind of gun it
                # would be" on silentgear:paxel and one controller line shared
                # between silentgear:lining and refinedstorage:controller. As
                # a quest description that is worse than saying nothing.
                short = iid.split(":", 1)[1].replace("_", " ")
                words = [w for w in short.split() if len(w) > 3]
                low = t.lower()
                if not words or not any(w in low for w in words):
                    continue
                conf.setdefault(iid, t)
    # fill, never overwrite: off-version evidence only speaks where nothing
    # on-version has spoken. Confusion is not filtered this way at all - a
    # player saying they cannot tell what a machine does is not a fact about
    # the Minecraft version.
    for iid, v in cross.items():
        pos.setdefault(iid, v)
    return pos, conf


_PLAY_POS, _PLAY_CONFUSION = _load_playthrough()


# Descriptions authors write for the launcher, not for a quest book. Across
# 114 installed mods only these actually need rejecting: 11 blank, 9
# placeholders, 5 too short to say anything, 3 carrying socials, 1 with colour
# codes. The rest are already exactly what a chapter wants to say.
_DESC_JUNK = ("none", "todo", "desc", "description", "deco", "wip", "hello",
              "n/a", "test", "mod", "a mod", "my mod")
_DESC_SOCIAL = re.compile(
    r"(https?://\S+|\bdiscord\b|\bpatreon\b|\btwitter\b|\bcurseforge\b|@\w+).*$",
    re.I | re.S)
_FMT_CODE = re.compile("§.")


def mod_blurb(mid: str, scan: dict, limit: int = 190) -> str:
    """The author's own one-line summary of their mod. -> str (may be "")

    Every Forge jar carries META-INF/mods.toml with a description the author
    wrote, and every Fabric jar has the same in fabric.mod.json. The scan has
    read it all along and only the AI prompt was using it, so an offline book
    opened all 23 of its chapters on nothing at all - while securitycraft sat
    there saying "Adds a load of things to keep your house safe with and
    defend yourself from attackers", which is the chapter intro already
    written by the person who knows best.
    """
    for m in (scan.get("mods") or ()):
        if m.get("mod_id") != mid:
            continue
        raw = _FMT_CODE.sub("", str(m.get("description") or ""))
        d = _DESC_SOCIAL.sub("", raw)
        d = " ".join(d.split()).strip(" -–—:;,")
        # If cutting the links and credits took most of the text, what is left
        # is the preamble to a credits dump rather than a summary. Cataclysm's
        # 350-character description is a texture-pack link and a patron list;
        # the 35 characters before it read "Hello Also Listen Cataclysmic
        # Tunes", which is not what that mod is.
        if len(raw) > 120 and len(d) < 60 and len(d) < len(raw) * 0.5:
            return ""
        if len(d) < 20 or d.lower().strip(" .!") in _DESC_JUNK:
            return ""
        # "Create" describing itself as "Create" helps nobody
        if d.lower().rstrip(".!").strip() == str(m.get("name") or "").lower().strip():
            return ""
        if len(d) > limit:
            # _clip, not a bare word-safe cut: a read-through found intros
            # ending mid-CONSTRAINT ("atop a powered Cursed..."), which is an
            # unclear-requirement the scorer counts as prose. Word-safe is not
            # sentence-safe; _clip prefers a full stop when one lands past 60%
            # of the cap.
            d = _clip(d, limit)
        return d if d.endswith((".", "!", "?", "...")) else d + "."
    return ""


def _blurb_sentence(b: str) -> str:
    """Normalise a jar-sourced item line to the book's sentence convention.

    Every other prose source already follows it - researched chains are
    authored with full stops, _desc's template lines end with one, and
    mod_blurb (directly above, the precedent this copies) has appended the
    missing period to the mod-level summary all along. The per-item jar text
    was the one source entering the document raw: measured on this pack, only
    55% of the 1213 scanned blurbs end with a sentence mark, so "Craft a
    trash can" sat unterminated in the same chapter as fully punctuated chain
    lines. One voice means one convention.

    The period is gated on SHAPE and the gate fails closed - a full stop
    forced onto a non-sentence reads worse than none:
      * credits keep their form ("Yuri_O - Spawn Of Hell", the line Cataclysm
        ships on each of its music discs)
      * stat fragments keep theirs ("+0.1 Speed on Sand")
      * anything under four words, or not ending in a letter, is left alone
        ("Here Be Contraptions" is a title, not a sentence)
    The first character is uppercased only when the opening token is a plain
    word, so a blurb that deliberately opens with a registry id or a
    lowercase mod name keeps its casing.
    """
    t = " ".join(str(b or "").split())
    if not t:
        return t
    if t[0].islower() and t.split(" ", 1)[0].isalpha():
        t = t[0].upper() + t[1:]
    if t.endswith((".", "!", "?", "...", '"', "'")):
        return t
    if (not t[-1].isalpha() or len(t.split()) < 4
            or re.match(r"\S{1,24}\s+[-–—]\s", t)
            or t[0] in "+-" or re.search(r"[+-]\d", t)):
        return t
    return t + "."


def _reads_cleanly(t: str) -> bool:
    """Is this fit to show a player? -> bool

    Guide notes are stitched together from transcripts and citations, and
    some come out damaged in ways no amount of stripping repairs: blue_skies
    reads "built like a Nether portal frame but from the dimensionpurple
    trees, purple everythingend up in a c". Putting these into chapter intros
    is what made the damage visible. A gate is the honest answer - the
    author's own blurb, or nothing, beats corrupted prose.
    """
    if not t or len(t) < 25:
        return False
    if t.count("'") % 2 or t.count('"') % 2:
        return False                      # a quote opened and never closed
    if t.count(")") != t.count("("):
        return False                      # a citation was cut out mid-bracket
    if re.search(r"[a-z]{19,}", t):
        return False                      # "dimensionpurple", "everythingend"
    if not t.rstrip().endswith((".", "!", "?", '"', "'")):
        return False                      # stops mid-thought
    return True


def chapter_intro(mid: str, scan: dict) -> str:
    """What to put at the top of a mod's chapter. -> str (may be "")

    Prefers a guide's account of how you actually REACH the mod, because that
    is the thing a player is stuck on and the thing no jar states. Falls back
    to the author's own summary. Blank if neither says anything - the rule
    that blank beats filler has not changed.
    """
    if not mid:
        return ""
    entry = guide_entry(mid, limit=260)
    if _reads_cleanly(entry):
        return entry
    return mod_blurb(mid, scan)


def guide_entry(mid: str, limit: int = 420) -> str:
    """How a guide says you actually REACH this mod's dimension. -> str

    The reason this exists: Tropicraft has no portal frame at all. You harvest
    a pineapple with a VANILLA sword (modded swords drop nothing), make a
    bamboo mug, and drink it sitting in a chair on a beach at sunset. Three
    independent guides describe the same ritual. None of that is derivable
    from the jar, and without it the chapter opened on a sapling - which is
    exactly what the user reported.
    """
    rec = _GUIDE_NOTES.get(mid) or {}
    lines = [" ".join(str(x).split()) for x in (rec.get("entry") or [])]
    if not lines:
        return ""
    # numbered steps first if the guide gave them; they are the instructions
    steps = [l for l in lines if re.match(r"^\d+[.)]\s", l)]
    body = " ".join(steps or lines)
    body = _strip_research_marks(body)
    # Same reasoning as mod_blurb above: sentence-safe beats word-safe, and a
    # guide entry cut off inside its own instructions is worse than a shorter
    # complete one.
    return _clip(body, limit)


# A YouTube id is 11 chars of [A-Za-z0-9_-]; requiring a digit and a case mix
# keeps ordinary words from matching.
_VID = r"(?=[A-Za-z0-9_-]{11}(?![A-Za-z0-9_-]))(?=[^ ]*[0-9])[A-Za-z0-9_-]{11}"


def _strip_research_marks(t: str) -> str:
    """Remove the researcher's citations and asides from player-facing prose.

    Agents cite the video they got a fact from and flag Whisper mishearings.
    Both are notes to me, not to a player reading a quest book.
    """
    t = re.sub(r"\(?\bSource[sd]?\b:?\s*" + _VID + r"[^.)]*\)?\.?", "", t)
    t = re.sub(r"\b" + _VID + r"\b\s*(is explicit|says|notes|adds)?\s*:?\s*",
               "", t)
    t = re.sub(r"\([^()]*Whisper[^()]*\)", "", t)
    t = re.sub(r"\b(Three|Two|Both|All)\s+(independent\s+)?"
               r"(guides?|sources?)\s+(describe|confirm|agree on)[^.]*\.", "", t)
    # "corroborated by THREE videos (...)" - the same claim in the other
    # phrasing the agents use. It reached a chapter intro verbatim, empty
    # parenthetical and all, which is how this was found.
    t = re.sub(r",?\s*\b(corroborated|confirmed|verified|attested)\s+(by|in|across)\s+"
               r"\w+\s+(videos?|guides?|sources?|parts?)\b", "", t, flags=re.I)
    # A stripped id can leave a headless clause - "SHCPj68Hgpc tries walking
    # in" became " tries walking in". Give it back a subject rather than
    # shipping a sentence with no one doing anything.
    t = re.sub(r"(^|(?<=[.!?]\s))\s*(?=(tries|says|shows|does|walks|goes|uses|"
               r"notes|explains|warns|points)\b)", "A guide ", t)
    # Removing several ids from one parenthetical leaves "(, , )".
    t = re.sub(r"\(\s*(?:,\s*)*\)", "", t)
    t = re.sub(r"\s*,\s*\)", ")", t)
    t = re.sub(r"\(\s*\)", "", t)
    # A registry id is how the researcher writes; a player reads a name. This
    # shipped into a chapter intro as "THROW a minecraft:diamond into the
    # pool". http:// is excluded so a surviving link is not turned into prose.
    t = re.sub(r"\b(?!https?\b)[a-z0-9_]+:[a-z0-9_]+(?:[/.][a-z0-9_]+)*\b",
               lambda m: _pretty_name(m.group(0)), t)
    # A long quoted span is the researcher showing their working - the actual
    # words a video said - and it only ever restates the instruction already
    # given above it, in worse prose, often cut mid-sentence by a length cap.
    # 30 chars keeps a short quoted TERM, which can carry real meaning.
    # An apostrophe inside the quote ("I'd put down my bucket") must not end
    # it, or the span is measured as one character and never reaches 30.
    # The closer is MANDATORY and the opener must not follow a word character:
    # a bare apostrophe after "Alex" is a possessive, not a quote, and an
    # earlier version that accepted end-of-string as a closer read every
    # possessive as an unterminated quote and deleted the rest of the line -
    # "Alex's Caves adds five underground biomes..." shipped as just "Alex",
    # on 32 of 86 guide entry lines (mean 85 chars destroyed). The trade:
    # a quote left unclosed by an upstream length cap now ships visible
    # instead of vanishing - prose a player can read beats prose that
    # silently ate its own sentence.
    t = re.sub(r"\s*(?<![A-Za-z0-9])['\"‘“](?:[^'\"’”]|'(?=[a-z]))" + "{30,}"
               + r"['\"’”]", "", t)
    t = re.sub(r"\.{2,}(?!\.)", ".", t)      # "flint and steel.." from a joined line
    t = re.sub(r"\s+([.,;:])", r"\1", t)     # gap left where a citation was cut
    return re.sub(r"\s{2,}", " ", t).strip()


_CONSUMERS: dict = {}


def _build_consumers(scan: dict) -> None:
    """Reverse the recipe graph once: item -> what it goes into."""
    if _CONSUMERS:
        return
    for made, ings in (scan.get("edges") or {}).items():
        for g in (ings or ()):
            # An item is never a reason to want itself. 98 ids in this pack
            # have an edge to themselves, because a recipe that returns part
            # of its input reads as both: create's crushing/obsidian.json
            # takes obsidian and gives back powdered obsidian plus obsidian
            # at 75%. The book printed "Asks for Obsidian. Goes into the
            # Obsidian." - a sentence that tells a player nothing and reads
            # as a bug. _one_recipe already drops the same edge on the
            # forward side; this reverse map did not. (auto_description's
            # "Made from" list still does not either - 6 latent cases across
            # the pack fixtures, none of which reach a book at present.)
            if g == made:
                continue
            _CONSUMERS.setdefault(g, set()).add(made)


def why_want(item_id: str, scan: dict, limit: int = 2) -> str:
    """A reason to want this, from what it is an ingredient of. -> str

    A player, mid-quest: "I don't know if I need a bunch of these - I'm just
    doing it for the quest, right?" The book had told him WHAT to get and not
    WHY, so he could not judge how much to make or whether it mattered.

    Measured across the offline book: 43% of quests carried no description at
    all and only 5% stated a reason. The recipe graph already knows one - what
    an item feeds into is exactly why a player wants it - so no prose has to
    be invented to say it.
    """
    _build_consumers(scan)
    mod = item_id.split(":", 1)[0]
    names = scan.get("names") or {}
    outs = [c for c in sorted(_CONSUMERS.get(item_id) or ())
            if c.split(":", 1)[0] == mod and _junk_score(c) < 3]
    if not outs:
        return ""

    # Name the consumer a player CARES about. Sorted alphabetically, livingrock
    # cited "Livingrock Slate and Shimmerrock" - two decor variants - when the
    # real answer is whatever the mod's own progression leads to. Prefer items
    # a guide taught, then ones the research chain kept, then the least
    # variant-like.
    _taught = set()
    for _r in _GUIDE_NOTES.values():
        _taught.update(_r.get("taught") or ())
    _chain = {i for rows in _RESEARCHED.values() for i, _t, _d in rows}

    def rank(c):
        return (0 if c in _taught else 1,
                0 if c in _chain else 1,
                -_GUIDE_WEIGHT.get(c, 0.0),
                _junk_score(c),
                len(c))
    outs.sort(key=rank)
    picked = [names.get(c) or _pretty_name(c) for c in outs[:limit]]
    # Second person, and NOT "Goes into the ..." - that is one of the five
    # generator-tail skeletons immersion CHK-11 hard-fails on, and the same
    # phrase is what SELF_REF flags when the consumer echoes the item. The
    # content (real consumers off the recipe graph) is unchanged; only the
    # frame moves to the register real authors write in (CHK-20).
    if len(picked) == 1:
        return "You'll use it in the %s." % picked[0]
    return "You'll use it in the %s and the %s." % (picked[0], picked[1])


def _clip(text: str, n: int) -> str:
    """Cut to n characters at a SENTENCE boundary, never mid-clause.

    A QA pass found 35 descriptions ending mid-word at the cap, and the first
    fix (cut at a space, append "...") traded that for a subtler failure: the
    ellipsis path fired on 176 of the 287 over-cap researched-chain descs and
    put 13 mid-clause lines in one book, several of them cut exactly where
    the load-bearing constraint was ("atop a powered Cursed...", "between
    12200 and 14000 ticks - the sunset..." losing the ticks). A description
    that stops mid-clause reads as a bug and can drop the one fact the player
    needed; a complete earlier sentence merely says less. So: cut at the last
    sentence end that fits, wherever it lands - the old ">60% of the cap"
    gate just rerouted short-first-sentence descs onto the ellipsis path -
    and when no sentence end fits at all, keep the FIRST sentence whole even
    though it overruns, because the authored fact intact beats the authored
    fact amputated. Measured over the chain corpus this leaves 2 descs over
    any cap worth worrying about; callers that need a hard limit should cap
    upstream, not shred sentences here.
    """
    t = " ".join(str(text or "").split())
    if len(t) <= n:
        return t
    k = 0
    for m in re.finditer(r"[.!?](?=\s)", t):
        if m.end() > n:
            break
        k = m.end()
    if k:
        return t[:k]
    m = re.search(r"[.!?](?=\s|$)", t)
    return t[:m.end()] if m else t


def _strip_module_flag(text: str) -> str:
    """Drop a trailing "MODULE FLAG: rope." researcher note. -> str

    A note an agent wrote to record which Quark config option gates an item,
    not something a player reads. guide_tip has stripped it for a while, but
    the chain LOADER - the primary path for these descs - shipped it
    verbatim, which made MODULE and FLAG the most common all-caps tokens in
    a built book. One regex, shared, so the two consumers of researched text
    cannot drift apart again.
    """
    return re.sub(r"\s*MODULE FLAGS?:[^.]*\.?\s*$", "", str(text or ""),
                  flags=re.I).strip()


def fill_blank_descriptions(chapters: list, scan: dict) -> int:
    """Give still-blank quests a line, but only where it is SPECIFIC. -> n

    auto_description only speaks for items that appear in researched chains or
    guide notes, which is right for it - a synthesised sentence about an
    unresearched item is how "Goes into the" came to open 9% of descriptions
    and how 26 lightbulb variants all said "Made from Lightbulb".

    But the guard there is a whitelist, and a whitelist cannot grow to cover
    mods nobody has researched - which is most mods, and the whole point of
    the offline builder. So this uses the property that actually distinguishes
    a useful line from filler: a useful one is about ONE item. Generate the
    candidates, count them, and keep only those that few quests would share.
    The generic fallback kills itself - "Not craftable, this one has to be
    found" is true of 28 items here, scores 28, and is dropped without anyone
    having to add it to a list of banned phrases.
    """
    craft: set = set()
    for v in (scan.get("craftable") or {}).values():
        craft |= set(v)
    edges = scan.get("edges") or {}
    names = scan.get("names") or {}
    drops_map = scan.get("drops") or {}   # absent in pre-drops pickled scans

    def nm(i):
        return names.get(i) or _pretty_name(i)

    def _src_nm(src):
        # Display-name map first (block sources usually are items). A nested
        # table path cuts both ways - aether's sheepuff/brown puts the mob
        # FIRST, goety's natural/mountaineer puts it LAST - but in all 45
        # nested sources this pack ships, the mob or block is the LONGEST
        # segment and the short one is a variant colour or a category word,
        # so "Dropped by the Sheepuff", never "Dropped by the Brown".
        return names.get(src) or _pretty_name(
            "%s:%s" % (src.split(":", 1)[0],
                       max(src.split(":", 1)[-1].split("/"), key=len)))

    def _drop_line(iid):
        # Loot provenance, weakest claim last. Entity sources go through
        # unfiltered - a mob named after its drop ("Dropped by the Lich"
        # on the Lich Trophy) is the right answer, not a circularity. Block
        # sources are where the near-circular lines live, and substring
        # checks miss them: quest_ram_trophy is not a substring problem,
        # its source quest_ram_WALL_trophy just says the item's own name
        # plus one word. Token-subset is the tell - a source whose name
        # tokens contain every token of the item's is the item restated,
        # so it is dropped, which also kills every block-drops-itself pair.
        dr = drops_map.get(iid) or {}

        def _short(s):
            return s.split(":", 1)[-1].split("/")[-1]

        # several sources -> name ONE, the shortest: "zombie" is the canonical
        # bearer where "zombie_villager" is the variant, and one true source
        # beats a list the line has no room for
        mobs = sorted(dr.get("entities") or (),
                      key=lambda s: (len(_short(s)), s))
        if mobs:
            return "Dropped by the %s." % _src_nm(mobs[0])
        itoks = set(iid.split(":", 1)[-1].split("_"))
        blocks = []
        for s in sorted(dr.get("blocks") or ()):
            short = s.split(":", 1)[-1].split("/")[-1]
            if short.startswith("potted_") or set(short.split("_")) >= itoks:
                continue
            blocks.append(s)
        if blocks:
            # deliberately not the banned "drops from" tail skeleton
            return "Drops when you break %s." % _src_nm(
                min(blocks, key=lambda s: (len(_short(s)), s)))
        gp = sorted(dr.get("gameplay") or ())
        if gp:
            return "Found in %s loot." % _src_nm(gp[0])
        return None

    cand: dict = {}     # iid -> plain line, what the uniqueness count judges
    pretty: dict = {}   # iid -> same line with DESC_KEY, what actually ships
    n = 0
    for ch in chapters:
        for q in (ch.get("quests") or []):
            d = q.get("description") or []
            if (" ".join(d) if isinstance(d, list) else str(d)).strip():
                continue
            t = (q.get("tasks") or [{}])[0]
            iid = t.get("item")
            if not iid or _junk_score(iid) >= 2:
                continue
            # Researched prose first, and it bypasses the uniqueness count
            # below: those lines were hand-written about ONE item each, so
            # they cannot be the parametrised boilerplate the count exists to
            # catch. Until _RESEARCHED_TEXT the chains were only readable by
            # chain position, and an item that entered the book by any other
            # path shipped blank while its authored text sat unread
            # (twilightforest:fiery_ingot, measured 2026-08-31). Description
            # only - the researched TITLE is deliberately not applied here,
            # because replacing a title the book already accepted is a voice
            # and findability judgement, not a blank-filling one.
            _rt = _RESEARCHED_TEXT.get(iid)
            if _rt:
                q["description"] = [DESC_BODY + _rt[1]]
                n += 1
                continue
            # One real recipe, named in full, with the verb its type earns.
            # The old line took the first two entries of the UNION of every
            # recipe for this item and called them a crafting grid, which
            # produced a combination that exists in no recipe
            # (magic_vibe_decorations:bluepumpkin wants a blue candle OR a
            # light blue one, never both) and sent players to a grid for a
            # smithing table and a cutting board.
            got = _one_recipe(iid, scan)
            # Same self-edge as in _build_consumers: this fallback path only
            # runs for a pickled scan too old to carry craft_recipes, and
            # there it would say "Crafted from Obsidian." on the obsidian
            # quest. _one_recipe filters g != item for the same reason.
            ing = [x for x in sorted(edges.get(iid) or ())
                   if not x.startswith("#") and x != iid]
            if got:
                # cand holds the PLAIN text - the uniqueness count below must
                # see two identical sentences as identical, and colour codes
                # in the counted string would stop them colliding. The
                # formatted twin (ingredients in DESC_KEY, same shape
                # _recipe_desc ships) is kept aside and only swapped in for
                # lines the count lets through, so both emitters of this
                # sentence finally agree on the look (they measurably did
                # not: 3-4 of the book's ~20 recipe-verb lines shipped plain
                # depending on hash seed, every one from this pass, and
                # Abyssal Decor showed both styles inside one chapter).
                cand[iid] = "%s %s." % (got[0], _and_list(got[1]))
                pretty[iid] = _recipe_line(got[0], got[1])
            elif ing and not scan.get("craft_recipes"):
                # second person, no "Crafted from" - CHK-11 tail skeleton
                ing_nm = [nm(x) for x in ing[:2]]
                cand[iid] = "You craft this with %s." % " and ".join(ing_nm)
                pretty[iid] = _recipe_line("You craft this with", ing_nm)
            else:
                # No recipe line could be built - before conceding "you'll
                # have to find it", check whether the scan knows WHERE. The
                # concession was literally false on real mob drops
                # (netherexp:banshee_rod, a banshee kill, shipped it), and
                # the drop line replaces vagueness with the actual source.
                # The uniqueness count below still judges every line: one
                # prolific mob dropping nine indexed items produces nine
                # identical lines that score above 2 and die as boilerplate.
                dl = _drop_line(iid)
                if dl:
                    cand[iid] = dl
                elif not ing and iid not in craft:
                    cand[iid] = ("No recipe makes this - "
                                 "you'll have to find it.")
    seen: dict = {}
    for line in cand.values():
        seen[line] = seen.get(line, 0) + 1
    for ch in chapters:
        for q in (ch.get("quests") or []):
            d = q.get("description") or []
            if (" ".join(d) if isinstance(d, list) else str(d)).strip():
                continue
            iid = (q.get("tasks") or [{}])[0].get("item")
            line = cand.get(iid)
            if line and seen.get(line, 0) <= 2:
                q["description"] = [DESC_BODY + pretty.get(iid, line)]
                n += 1
    return n


# WHY-line templates for rationale_pass. Every one names 1-2 REAL consumers
# from the recipe graph - the content is sourced, only the frame varies.
# Deliberately none of them use the generator-tail skeletons the immersion
# spec hard-fails on ("goes into", "asks for", "made from", "drops from"),
# and most are second person with a consequence verb, which is the register
# real authors write in (immersion CHK-20/21).
# Every variant addresses the READER. Five of the eight used to state the
# fact to nobody - "Later recipes call for it", "It's a step on the way to" -
# which is the register a wiki writes in, not the one a pack author does, and
# it is what CHK-20 measures. The facts are unchanged; only the person is.
# Kept four distinct shapes each, because a single repeated skeleton is what
# CHK-11 hard-fails on.
_WHY_TWO = (
    "You'll want a few - it's used in both the %s and the %s.",
    "You'll want a couple back - it's needed for the %s and the %s.",
    "Don't spend it all: you'll see it used for the %s and the %s later.",
    "The %s and the %s both take one, so you'll want spares.",
)
_WHY_ONE = (
    "It's needed for the %s, so you'll want it again before long.",
    "Keep one spare - you'll see it used for the %s.",
    "The %s calls for it later, so you'll want one put aside.",
    "It unlocks the %s for you, which is what makes it worth doing early.",
)


def rationale_pass(chapters: list, scan: dict) -> int:
    """Add a WHY to quests that gate real content and say nothing about it. -> n

    check_book's no-rationale failure, in a real player's words: "I don't know
    if I need a bunch of these - I'm just doing it for the quest, right?"
    Measured on this pack's own items: 72% of quests whose item feeds two or
    more recipes named none of them. auto_description only speaks for
    researched items and why_want only looks same-namespace, so most quests
    never got a reason at all.

    Every line here is SOURCED from the recipe graph: it names actual
    consumers of the item, ranked the way why_want ranks them (guide-taught
    first, then researched-chain, then least variant-like) so the book cites
    the Mechanical Press and not two decor variants. An item whose consumers
    are ALL variant-noise gets nothing - blank beats filler - and the
    uniqueness rule applies: a finished line shared by more than two quests
    is dropped rather than printed as boilerplate.
    """
    import zlib
    edges = scan.get("edges") or {}
    names = scan.get("names") or {}
    cons_map: dict = {}
    for made, ings in edges.items():
        for g in (ings or ()):
            if g == made:       # an item is never a reason to want itself
                continue
            cons_map.setdefault(g, set()).add(made)

    _taught = set()
    for _r in _GUIDE_NOTES.values():
        _taught.update(_r.get("taught") or ())
    _chain = {i for rows in _RESEARCHED.values() for i, _t, _d in rows}

    def disp(c):
        # The fallback matches check_book's detection string exactly, so a
        # printed name is always findable by the reader AND the check.
        return names.get(c) or c.split(":", 1)[-1].replace("_", " ").title()

    def rank(item, c):
        return (c.split(":", 1)[0] != item.split(":", 1)[0],  # own mod first
                0 if c in _taught else 1,
                0 if c in _chain else 1,
                -_GUIDE_WEIGHT.get(c, 0.0),
                _junk_score(c),
                len(c))

    # pass 1: compose candidates, so the uniqueness count sees the whole book
    cand: dict = {}
    for ch in chapters:
        for q in (ch.get("quests") or []):
            item = (q.get("tasks") or [{}])[0].get("item")
            # Vanilla needs no rationale - nobody opens a quest book to learn
            # what an iron ingot is for. Same exemption check_book applies.
            if not item or item.startswith("minecraft:"):
                continue
            cons = cons_map.get(item) or set()
            if len(cons) < 2:
                continue        # nothing downstream - blank is right
            d = q.get("description") or []
            body = re.sub(r"&[0-9a-fk-or]", "", " ".join(
                str(x) for x in (d if isinstance(d, list) else [d]))).lower()
            if any((names.get(c) or c.split(":", 1)[-1].replace("_", " "))
                   .lower() in body for c in cons):
                continue        # already says why - leave it alone
            # Name consumers a player CARES about: junk-scored variants
            # (26 lightbulbs, stairs-of-everything) never make the line, and
            # if EVERY consumer is variant noise the quest stays blank.
            outs = sorted((c for c in cons if _junk_score(c) < 3
                           and not c.startswith("#")),
                          key=lambda c: rank(item, c))
            if not outs:
                continue
            picked = [disp(c) for c in outs[:2]]
            # stable across runs (str hash is seeded per process, crc is not)
            k = zlib.crc32(item.encode("utf-8"))
            if len(picked) > 1:
                line = _WHY_TWO[k % len(_WHY_TWO)] % tuple(picked)
            else:
                line = _WHY_ONE[k % len(_WHY_ONE)] % picked[0]
            cand[id(q)] = (q, line)

    seen: dict = {}
    for _q, line in cand.values():
        seen[line] = seen.get(line, 0) + 1
    n = 0
    for q, line in cand.values():
        if seen.get(line, 0) > 2:
            continue            # boilerplate by measurement - the law
        d = q.get("description") or []
        if not isinstance(d, list):
            d = [str(d)]
        styled = DESC_BODY + line
        q["description"] = (d + ["", styled]) if any(
            str(x).strip() for x in d) else [styled]
        n += 1
    return n


# CHK-11 is a hard fail on ANY of five generator-tail skeletons ("asks for",
# "<participle> from", "goes into", "out in the world", "drops from"). The
# templates above no longer emit them, but SOURCED prose still can - a guide
# note or a mod author's own blurb saying "drops from wither skeletons" is out
# of this app's control and 2 such lines shipped in the 2026-08-29 book. Each
# rewrite below is meaning-preserving and mechanical (no new claims), which is
# why it is safe to run on prose this app did not write.
# The left boundary is (?<![A-Za-z]), NOT \b: a colour code glues right onto
# the word ("&7Crafted from ...") and '7'+'C' are both word chars, so \b never
# fires there - which is exactly how 3 curated lines slipped past the first
# version of this scrub while the scorer (which strips codes before matching)
# still counted them. A letter on the left still blocks ("Tasks for").
_TAIL_SCRUB = (
    (re.compile(r"(?<![A-Za-z])asks for\b"), "calls for"),
    (re.compile(r"(?<![A-Za-z])Asks for\b"), "Calls for"),
    (re.compile(r"(?<![A-Za-z])ask for\b"), "call for"),
    (re.compile(r"(?<![A-Za-z])Ask for\b"), "Call for"),
    (re.compile(r"(?<![A-Za-z])goes into\b"), "feeds into"),
    (re.compile(r"(?<![A-Za-z])Goes into\b"), "Feeds into"),
    (re.compile(r"(?<![A-Za-z])out in the world\b"), "in the wild"),
    (re.compile(r"(?<![A-Za-z])Out in the world\b"), "In the wild"),
    (re.compile(r"(?<![A-Za-z])drops from\b"), "comes from"),
    (re.compile(r"(?<![A-Za-z])Drops from\b"), "Comes from"),
    (re.compile(r"(?<![A-Za-z])drop from\b"), "come from"),
    (re.compile(r"(?<![A-Za-z])Drop from\b"), "Come from"),
)
_TAIL_PARTICIPLE = re.compile(
    r"(?<![A-Za-z])(crafted|made|built|assembled|forged|smelted) from\b", re.I)


def _scrub_tail_line(s: str) -> str:
    for rx, rep in _TAIL_SCRUB:
        s = rx.sub(rep, s)
    # "crafted from X" -> "crafted using X": same station, same ingredients
    return _TAIL_PARTICIPLE.sub(lambda m: m.group(1) + " using", s)


def scrub_generator_tails(chapters: list) -> int:
    """Rewrite any surviving CHK-11 tail skeleton in quest text. -> n changed

    Runs LAST, after every pass that can add prose, because it is the
    guarantee: the immersion target is tail_share == 0.0 on unseen packs,
    and only a final sweep can promise that for text taken from jars and
    guide transcripts.
    """
    n = 0
    for ch in chapters:
        for q in (ch.get("quests") or []):
            d = q.get("description")
            if d:
                lines = [d] if not isinstance(d, list) else d
                new = [_scrub_tail_line(str(x)) for x in lines]
                if new != [str(x) for x in lines]:
                    q["description"] = new
                    n += 1
            sub = q.get("subtitle")
            if sub:
                ns = _scrub_tail_line(str(sub))
                if ns != str(sub):
                    q["subtitle"] = ns
                    n += 1
    return n


def subtitle_pass(chapters: list, scan: dict) -> int:
    """Give 40-60% of quests a short subtitle (immersion CHK-10). -> n

    The subtitle is FTBQ's glance-hint channel and no generation path wrote
    one (measured 0 of 304), while authored packs sit at 27-100% coverage
    (Prominence 59.5% at median 9 words). Everything written here is sourced,
    per the blank-beats-filler law:

      * the item's own DISPLAY NAME - when the title is flavour ("The
        Engineer's Thumb"), the subtitle says what the quest actually wants,
        which is also the findability guarantee in its natural channel;
      * the mod's own per-item BLURB, clipped short - when the title already
        names the item, so a display-name subtitle would only echo it.

    A subtitle that echoes its title is never emitted (the scorer's
    anti-gaming guard), a line shared by more than two quests dies
    (uniqueness law), and coverage is capped inside the authored band by a
    deterministic per-quest draw, not a global rng.
    """
    import zlib
    names = scan.get("names") or {}
    blurbs = scan.get("blurbs") or {}
    allq = [q for ch in chapters for q in (ch.get("quests") or [])]
    nq = len(allq)
    if not nq:
        return 0

    def plain(s):
        return re.sub(r"&[0-9a-fk-orA-FK-OR]", "", str(s or "")).strip()

    cand = []                     # (priority, crc, quest, line)
    for q in allq:
        if q.get("subtitle"):
            continue
        title = plain(q.get("title")).lower()
        t0 = (q.get("tasks") or [{}])[0]
        item = t0.get("item")
        line, pri = None, None
        if item:
            disp = names.get(item) or _pretty_name(item)
            dl = disp.lower().strip()
            if dl and dl not in title and title not in dl:
                line, pri = disp, 0
            else:
                b = blurbs.get(item)
                if b:
                    b = _clip(" ".join(str(b).split()), 90)
                    bl = b.lower().strip(" .!?")
                    if bl and bl not in title and title not in bl:
                        line, pri = b, 1
        if line:
            cand.append((pri, zlib.crc32(str(q.get("id") or q.get("title"))
                                         .encode("utf-8")), q, line))

    # uniqueness law: a line >2 quests would share is boilerplate, drop it
    seen: dict = {}
    for _p, _c, _q, line in cand:
        seen[line] = seen.get(line, 0) + 1
    cand = [c for c in cand if seen[c[3]] <= 2]

    # Coverage band 0.40-0.60. Take at most 55% of quests (margin under the
    # cap), best-sourced first, ties broken by the crc so the cut is stable
    # across runs and not biased toward any one chapter.
    cand.sort(key=lambda c: (c[0], c[1]))
    n = 0
    for _p, _c, q, line in cand[:int(nq * 0.55)]:
        q["subtitle"] = line
        n += 1
    return n


def role_length_pass(chapters: list, scan: dict) -> int:
    """Roots carry the long text, leaves stay short (immersion CHK-12). -> n

    Authored books front-load each chapter: the entry quest reads like a page
    (root median >= 2x leaf median in the corpus); ours measured 22 vs 21
    words - one uniform paragraph length everywhere, which is a direct symptom
    of the enumerated feel. Leaves are NOT trimmed (their short lines are the
    check_book rationale wins); instead the chapter's entry quest is grown
    from the two long-form sources this app is allowed to quote:

      * the mod author's own blurb (mods.toml / fabric.mod.json), and
      * a guide's account of how you actually reach the mod (guide_entry),

    each appended only when it is not already substantially in the text.
    A multi-mod chapter quotes the blurb of the mod that contributed the most
    quests - a checkable fact, counted right here.
    """
    n = 0
    for ch in chapters:
        qs = ch.get("quests") or []
        if len(qs) < 4:
            continue
        ids = {q.get("id") for q in qs}
        roots = [q for q in qs
                 if not any(d in ids for d in (q.get("dependencies") or ()))]
        if not roots:
            continue
        # namespaces of this chapter's tasks, the way the builder derives _mid
        counts: dict = {}
        for q in qs:
            t0 = (q.get("tasks") or [{}])[0]
            ns = str(t0.get("item") or t0.get("structure") or "").split(":", 1)[0]
            if ns and ns != "minecraft":
                counts[ns] = counts.get(ns, 0) + 1
        for root in roots:
            d = root.get("description") or []
            if not isinstance(d, list):
                d = [str(d)]
            have = re.sub(r"&[0-9a-fk-orA-FK-OR]", "",
                          " ".join(str(x) for x in d)).lower()
            words = len(have.split())
            if words >= 48 or not counts:
                continue
            texts = []
            if len(counts) == 1:
                mid = next(iter(counts))
                texts = [mod_blurb(mid, scan)]
                ge = guide_entry(mid, limit=420)
                if _reads_cleanly(ge):
                    texts.append(ge)
            else:
                # most-quests-first, and take up to TWO blurbs: one was not
                # enough words on chapters whose biggest mod ships a one-line
                # blurb, and each line names its mod so none is boilerplate
                ranked = sorted(counts, key=lambda k: (-counts[k], k))
                for mid in ranked:
                    b = mod_blurb(mid, scan)
                    if b:
                        texts.append("From %s: %s"
                                     % (_mod_display_name(mid, scan), b))
                    if len(texts) >= 2:
                        break
            # Last resort, from display names alone (a sanctioned source):
            # the chapter's dependency span. Row order IS crafting-depth
            # order (see the sort at the row builder), and the trunk's spine
            # follows it, so first and last item are the progression's two
            # ends even after the layout jitters sibling reading order.
            itq = [(q.get("tasks") or [{}])[0].get("item") for q in qs]
            itq = [i for i in itq if i]
            if len(itq) >= 2:
                nmz = scan.get("names") or {}
                a = nmz.get(itq[0]) or _pretty_name(itq[0])
                b = nmz.get(itq[-1]) or _pretty_name(itq[-1])
                if a.lower() != b.lower():
                    texts.append("You'll work your way up from the %s to "
                                 "the %s." % (a, b))
            for t in texts:
                t = " ".join(str(t or "").split())
                if len(t) < 25:
                    continue
                # skip text the root already carries (chapter_intro feeds the
                # opener from the same two sources)
                if t[:40].lower() in have:
                    continue
                d = (d + ["", DESC_BODY + t]) if any(
                    str(x).strip() for x in d) else [DESC_BODY + t]
                have += " " + t.lower()
                words += len(t.split())
                n += 1
                if words >= 48:
                    break
            root["description"] = d
    return n


def _made_from_sentence(item_id: str, scan: dict) -> str:
    """What this is made from, phrased so it answers something. -> str

    The graph is walked in one direction by why_want: an item nothing
    consumes looked like an item with nothing to say, when the honest answer
    was simply on the other side of the recipe. Says who it is addressed to
    and what knowing it gets the player, because a description that states a
    fact to nobody is the register this book is trying not to write in.
    """
    names = scan.get("names") or {}
    ings = [g for g in sorted((scan.get("edges") or {}).get(item_id) or ())
            if _junk_score(g) < 3]
    if not ings:
        return ""
    ings.sort(key=lambda g: (g.split(":", 1)[0] != item_id.split(":", 1)[0],
                             _junk_score(g), len(g)))
    got = [names.get(g) or _pretty_name(g) for g in ings[:2]]
    if len(got) > 1:
        return ("You make it from %s and %s, so you'll want those in hand "
                "first." % (got[0], got[1]))
    return "You make it from %s, so you'll want that in hand first." % got[0]


def auto_description(item_id: str, scan: dict) -> list:
    """Compose a description for a quest that has none, from grounded facts.

    43% of offline quests shipped with NO description, which is the blunt form
    of the complaint that the book reads as plain text with nothing in it. The
    fix is not to invent prose: everything here is sourced.

      1. what a guide narrator warned about this item
      2. the mod's OWN blurb for it, shipped in the jar
      3. what the recipe graph says it goes into

    A quest that still has nothing after all three genuinely has nothing to
    say, and gets left alone rather than padded.
    """
    parts = []
    blurb = _blurb_sentence((scan.get("blurbs") or {}).get(item_id))
    if blurb:
        # The blurb is the mod's own copy, so it says WHAT the thing is, in
        # the third person, to nobody in particular. why_want says why YOU
        # want it, off the recipe graph. Returning the blurb alone left every
        # blurb-backed quest with no reason and no reader - which is most of
        # the gap on CHK-20 (second person 0.400 against 0.45) and CHK-21.
        # Appended only when it fits inside the same cap, so a long blurb is
        # never truncated to make room for a sentence that is nice to have.
        why = why_want(item_id, scan) or _made_from_sentence(item_id, scan)
        text = blurb
        if why and why.lower()[:14] not in blurb.lower():
            joined = blurb.rstrip() + " " + why
            if len(joined) <= 300:
                text = joined
        return style_desc(_clip(text, 300), guide_tip(item_id))

    # A GENERATED line is only worth printing when the item is worth talking
    # about. Emitted for everything, it became the filler the user objected to
    # in the first place: "Goes into the" opened 9% of all descriptions, 26
    # lightbulb variants each said "Made from Lightbulb", and securitycraft's
    # mine produced the nonsense "Made from Mine and Iron Ore". Blank beats
    # filler, so a variant or a junk-scored id is left alone and only items
    # that carry real signal get a synthesised sentence.
    _taught = set()
    for _r in _GUIDE_NOTES.values():
        _taught.update(_r.get("taught") or ())
    _chain = {i for rows in _RESEARCHED.values() for i, _t, _d in rows}
    if (_junk_score(item_id) >= 2
            or item_id not in (_taught | _chain | set(_GUIDE_WEIGHT))):
        return []
    why = why_want(item_id, scan)
    if why and (not parts or why.lower()[:12] not in parts[0].lower()):
        parts.append(why)
    if not parts:
        # Nothing consumes it and the mod ships no blurb - so say what it is
        # MADE FROM instead. A terminal item is exactly the kind that ends a
        # branch, and "nothing to say about it" is usually just the graph
        # being walked in one direction.
        names = scan.get("names") or {}
        ings = [g for g in sorted((scan.get("edges") or {}).get(item_id) or ())
                if _junk_score(g) < 3]
        ings.sort(key=lambda g: (g.split(":", 1)[0] != item_id.split(":", 1)[0],
                                 _junk_score(g), len(g)))
        if ings:
            _mf = _made_from_sentence(item_id, scan)
            if _mf:
                parts.append(_mf)
    if not parts:
        return []
    body = _clip(" ".join(parts), 300)
    return style_desc(body, guide_tip(item_id))


def ensure_findable(title: str, desc, item_id: str, scan: dict):
    """Make sure a quest can be found again by the thing it asks for.

    A player watching himself lose a quest he had already read:
      "Let me see if I can figure out where that uh sleep quest was that gave
       like a description"
    He remembers it exists and cannot get back to it. That is a findability
    failure, not a writing one, and it is the argument against titling every
    quest purely for flavour.

    The fix is NOT to strip the flavour - "The Engineer's Thumb" is why the
    book reads well. It is to guarantee the item's real name appears
    somewhere, so a search for "wrench" lands on it. Measured before this:
    57% of titles and 24% of descriptions named their item; 10% of quests
    named it in neither.
    """
    disp = (scan.get("names") or {}).get(item_id) or _pretty_name(item_id)
    words = {w for w in re.findall(r"[a-z]+", disp.lower()) if len(w) > 2}
    if not words:
        return desc
    have = " ".join([str(title or "")] + [str(x) for x in (desc or [])]).lower()
    if any(w in have for w in words):
        return desc
    # Name it AND say what it is for - a bare "Asks for X" is the very thing
    # that leaves a player doing something "just for the quest". The frame is
    # second person and NOT "Asks for ..." - that exact bigram is a CHK-11
    # generator-tail skeleton (37 of the 89 tail hits measured 2026-08-29) and
    # the trigger for the scorer's self-reference flag. The display name still
    # appears verbatim, so findability is intact.
    why = why_want(item_id, scan)
    # Only speak when there is a REASON to give. "You'll need <Item> here."
    # with no why was found on 37 of 308 quests by a read-through -
    # parametrisation slipped it past the uniqueness rule, because every
    # instance is a distinct string and the same sentence. A skeleton whose
    # only content is the item name adds nothing the task line does not show,
    # so without a why the item is named bare, which keeps findability
    # without pretending to explain.
    if why:
        line = "%s%s%s%s - %s" % (DESC_BODY, DESC_KEY, disp, DESC_BODY, why)
        return (list(desc) + ["", line]) if desc else [line]
    bare = "%s%s%s" % (DESC_KEY, disp, DESC_BODY)
    return (list(desc) + ["", bare]) if desc else [bare]


def guide_tip(item_id: str, limit: int = 190) -> str:
    """A warning a guide narrator gave about this item, if any. -> str

    This is the payoff of transcribing guides. A gate or trap note is the
    thing a player actually needs told, and it is unreachable from jar data:
    that Create Diesel Generators' pumpjack hole has no JEI recipe, or that a
    Blue Skies portal cannot be lit with flint and steel. Descriptions used to
    be generated with no idea what confuses people.
    """
    # A player saying in the moment that they could not work something out
    # beats any warning written from the outside, so it wins.
    _c = _PLAY_CONFUSION.get(item_id)
    if _c:
        return _strip_research_marks(_c)[:limit]
    best = ""
    for rec in _GUIDE_NOTES.values():
        for src in ("gates", "traps"):
            for txt in (rec.get(src) or {}).get(item_id, ()):
                t = " ".join(str(txt).split())
                # prefer the most specific note, not merely the longest
                if len(t) > len(best) and len(t) <= limit * 2:
                    best = t
    if not best:
        return ""
    # Strip the researcher's own emphasis markers. "HARD GATE:" is a note an
    # agent wrote to me, not something a player should read - style_desc
    # already labels the line "Tip:".
    best = re.sub(r"^(MAJOR |HARD )?(GATE|TRAP|WARNING|NOTE)"
                  r"( ON COST| ON [A-Z ]+)?[:,]\s*", "", best, flags=re.I)
    best = re.sub(r"^, invisible to any jar or recipe scan[:,]?\s*", "", best)
    best = _strip_research_marks(best)
    # "MODULE FLAG: rope." and "MODULE FLAGS: pipes, encased_pipes." were
    # printed at the end of real quest descriptions; the strip lives in
    # _strip_module_flag so the chain loader applies the same cleanup.
    best = _strip_module_flag(best)
    best = best[:1].upper() + best[1:] if best else best
    if len(best) > limit:
        cut = best[:limit].rsplit(". ", 1)[0]
        best = (cut + ".") if len(cut) > 60 else best[:limit].rsplit(" ", 1)[0] + "..."
    return best


def style_desc(body: str, tip: str = "", flavour: str = "") -> list:
    """-> a list of FTB description lines with colour codes applied."""
    out = []
    if body:
        out.append(DESC_BODY + _txt(body).strip())
    if tip:
        out.append("")
        out.append("%sTip: %s%s" % (DESC_WARN, DESC_BODY, _txt(tip).strip()))
    if flavour:
        out.append("")
        out.append(DESC_FLAV + _txt(flavour).strip())
    return out


# Block shapes a mod re-emits for every material it adds. An item ending in one
# is a building variant, never a milestone.
_SHAPE_SUFFIX = (
    "stairs", "slab", "wall", "fence", "fence_gate", "door", "trapdoor",
    "button", "pressure_plate", "pane", "carpet", "sign", "hanging_sign",
    "boat", "chest_boat", "raft", "bricks", "tiles", "pillar", "lamp",
    "hedge", "curtain", "carpet", "window", "shutter", "planks",
    "post", "beam", "table", "stool", "shelf",
)


def _foreign_mod_tokens(mid: str, scan: dict) -> set:
    """Name tokens that are OTHER mods' ids. Derived from the pack itself plus
    the harvested database, so it needs no maintained list."""
    known = set(scan.get("items") or ()) | set(_HARVESTED_ORDER)
    out = set()
    for other in known:
        if other == mid or len(other) < 3:
            continue
        for tok in other.split("_"):
            if len(tok) >= 4:
                out.add(tok)
    return out - set(mid.split("_"))


def _variant_tokens(items: set) -> set:
    """Name tokens a mod repeats across many items - wood types, colours, ore
    stone types, and mod-specific prefixes like securitycraft's 'reinforced'.

    Derived per mod rather than hard-coded so it works for any pack: a token
    carried by several of a mod's items is a variant axis, not content.
    """
    n = len(items)
    if n < 10:
        return set()
    freq = collections.Counter()
    for i in items:
        for tok in i.split(":")[-1].split("_"):
            freq[tok] += 1
    cut = max(3, int(n * 0.02))
    return {t for t, c in freq.items() if c >= cut}


def _dominant_tokens(items: set) -> set:
    """Tokens carried by a large share of a mod's items - a family PREFIX.

    Steam 'n' Rails has 289 track_* ids out of 600. Most collapse as variants,
    but ones ending in a rare word from another mod ("track_byg_witch_hazel")
    escaped, because "witch" and "hazel" are not frequent enough to look like
    variant axes. If the leading token already covers a third of the mod, the
    whole item is one of that family whatever follows it.
    """
    n = len(items)
    if n < 30:
        return set()
    freq = collections.Counter()
    for i in items:
        for tok in set(i.split(":")[-1].split("_")):
            freq[tok] += 1
    return {t for t, c in freq.items() if c >= n * 0.28}


def _family_key(item: str, variants: set, tier: int = 0,
                dominant: set = ()) -> str:
    """Collapse an item to its family. Two items with the same key are the same
    thing in a different material or number (oak/birch boat, azulejo_1..15), so
    only one of them deserves a quest."""
    short = item.split(":")[-1]
    short = re.sub(r"_\d+$", "", short)          # azulejo_14 -> azulejo
    parts = short.split("_")
    if dominant:
        hit = [t for t in parts if t in dominant]
        if hit and len(parts) > 1:
            return "%s#dom" % hit[0]
    if len(parts) == 1:
        return short                      # a one-word name is the thing itself
    toks = [t for t in parts if t not in variants and not t.isdigit()]
    if not toks:
        # every token is a variant axis, so the whole name is a variant:
        # "reinforced_blackstone" is reinforced(prefix) + blackstone(material)
        # and carries no content word at all. One bucket for the whole mod.
        return "__variant_family__"
    # Bucket by crafting depth as well as name. oak_boat and birch_boat sit at
    # the same depth and are one family; ironwood_ingot and knightmetal_ingot
    # are tiers apart and are separate content, so collapsing them by the word
    # "ingot" alone silently deleted a real progression step.
    return "%s#%d" % ("_".join(toks), tier // 3)


# What published packs ACTUALLY quest, measured across 1454 mod jars: 5816
# quested items out of 30416, from 1974 chapters / 235 mod namespaces. Lift is
# how much more often a pattern appears in quested items than base rate, mod-
# balanced so one huge decor mod cannot skew it.
_GUIDE_WEIGHT: dict = {}
_GUIDE_TRAP: dict = {}
_GUIDE_SIG: set = set()
_LIFT_BOOST = (
    ("generator", 5.5), ("_eye", 4.6), ("_heart", 4.4), ("reactor", 3.6),
    ("_ingot", 3.9), ("book_", 3.9), ("_key", 3.7), ("_template", 4.0),
    ("smithing", 4.0), ("engine", 2.1), ("machine", 2.0), ("_casing", 1.6),
    ("altar", 2.0), ("_core", 1.8), ("terminal", 1.8), ("backpack", 1.8),
    ("jetpack", 1.8), ("wrench", 1.7), ("_gem", 1.6), ("_alloy", 1.6),
)
# Penalties here are near-absolute in the data, not soft preferences:
# _trim was quested 3 times in 1564 items; stripped/potted/chiseled: 0.
_LIFT_PENALTY = (
    ("_ore", 0.14), ("_nugget", 0.11), ("_slab", 0.07), ("_stairs", 0.09),
    ("_wall", 0.06), ("_sign", 0.06), ("_trim", 0.04), ("stripped_", 0.02),
    ("potted_", 0.02), ("chiseled_", 0.02), ("cracked_", 0.02),
    ("_hanging_sign", 0.02), ("_boat", 0.02), ("_carpet", 0.05),
)
_ARMOUR_PIECES = ("_chestplate", "_leggings", "_boots")


def _fun_score(item_id: str, craftable: bool, consumers: int, is_block: bool) -> float:
    """How much a published pack would want to quest this, from measured lift.

    Two findings drive most of it:
      * Authors quest the SMELTED result, never the ore or the nugget
        (_ingot 3.9x, _ore 0.14x, _nugget 0.11x).
      * Ingredient centrality predicts questing for non-blocks (4.1x from 0 to
        16+ recipes) but is FLAT for blocks - a machine is quested because it
        is a machine, not because something consumes it.
    """
    short = item_id.split(":", 1)[1] if ":" in item_id else item_id
    sc = 0.0
    for pat, lift in _LIFT_BOOST:
        if pat in short:
            sc += min(6.0, (lift - 1.0) * 1.6)
            break
    for pat, lift in _LIFT_PENALTY:
        if pat in short:
            sc -= 9.0
            break
    # Armour: of 202 complete sets, 51.9% quest the HELMET ONLY and a lone
    # chestplate/leggings/boots quest was observed ZERO times.
    if short.endswith("_helmet"):
        sc += 2.4
    elif short.endswith(_ARMOUR_PIECES):
        sc -= 3.0
    if not is_block and consumers >= 8:
        sc += 3.5
    elif not is_block and consumers >= 3:
        sc += 1.5
    if craftable:
        sc += 1.8                       # 25.2% quested vs 13.1% uncraftable
    # What guides dwell on is what players care about. This is the only term
    # here derived from human speech rather than from pack statistics.
    sc += _GUIDE_WEIGHT.get(item_id, 0.0)
    # A narrator calling something the point of the mod, or a waste of time,
    # is a stronger signal than any count of how often the word appeared.
    if item_id in _GUIDE_SIG:
        sc += 4.0
    sc -= 3.0 * _GUIDE_TRAP.get(item_id, 0)
    return sc


def rank_mod_items(mid: str, scan: dict) -> list:
    """Order one mod's items by how much they deserve a quest, from data only.

    No per-mod knowledge. Signals, all read from the pack's own jars:
      + how many DISTINCT FAMILIES consume it. Counting raw recipes instead let
        securitycraft's 453 reinforced_* blocks rank top: they are used to craft
        each other, so the cluster inflates its own centrality.
      + whether the mod's own advancements name it
      + whether it is a tool / equipment rather than a block
      - stripping the mod's variant tokens leaves a vanilla item (a reskin)
      - it is a building shape (stairs/slab/wall/...)
    """
    items = set(scan.get("items", {}).get(mid, ()))
    if not items:
        return []
    # An item nothing can give you cannot be a quest GOAL, however well it
    # scores. A release gate found four such quests shipping - blocks that
    # exist only as placed worldgen inside structures - one of them gating
    # five others, 8 dead quests in a single chapter. Dropped here rather
    # than penalised, because a score can always be outweighed and a quest
    # the player cannot finish is not a matter of degree.
    items -= set(scan.get("unobtainable") or ())
    if not items:
        return []
    edges = scan.get("edges") or {}
    tiers = scan.get("tier") or {}
    vanilla = scan.get("vanilla") or _VANILLA_ITEM_SET
    vanilla_short = {v.split(":", 1)[1] for v in vanilla}
    adv = {r.get("item") for r in (scan.get("progression") or {}).get(mid) or []}
    books = scan.get("book_items") or set()
    craft = set(scan.get("craftable", {}).get(mid) or ())
    variants = _variant_tokens(items)
    # Cross-mod compatibility items name the mod they bridge to
    # ("railways:track_byg_rainbow_oak"). They only exist if that other mod is
    # present, they are pure variants of the base item, and there are dozens of
    # them - exactly the filler a questline must not open on.
    # Foreign-mod tokens are used ONLY to score compat items down, never merged
    # into `variants`. Merging them stripped meaningful words out of family
    # keys across every mod and pushed ingredient-after-product pairs from 4.8%
    # to 5.8%, for a 0.3% junk gain that was not worth it.
    others = _foreign_mod_tokens(mid, scan)
    dominant = _dominant_tokens(items)
    fam = {it: _family_key(it, variants, tiers.get(it, 0), dominant)
           for it in items}

    # centrality measured in FAMILIES, not recipes
    consumers = collections.defaultdict(set)
    for out_id, ings in edges.items():
        okey = fam.get(out_id) or _family_key(out_id, variants,
                                              tiers.get(out_id, 0), dominant)
        for i in ings:
            if i in items and fam[i] != okey:
                consumers[i].add(okey)

    def reskin(it, toks):
        stripped = "_".join(t for t in toks if t not in variants)
        if stripped and stripped in vanilla_short:
            return True
        # "reinforced_cobbled_deepslate" -> the tail is a whole vanilla name
        for k in range(1, len(toks)):
            if "_".join(toks[k:]) in vanilla_short:
                return True
        return False

    def score(it):
        short = it.split(":", 1)[1]
        toks = short.split("_")
        sc = min(len(consumers.get(it, ())), 10) * 2.2
        if it in adv:
            sc += 7.0
        if it in books:
            # the mod's own guide book has an entry for this - the strongest
            # "this matters" signal a jar can give, since a human wrote it
            sc += 8.0
        # A small nudge only. At +4 this promoted every late-game sword and
        # chestplate over the early materials a questline has to open on.
        if any(k in short for k in ("ingot", "gem", "alloy", "dust", "essence")):
            sc += 1.5
        if it in craft:
            sc += 2.0
        # measured desirability: what published packs actually quest
        sc += _fun_score(it, it in craft, len(consumers.get(it, ())),
                         short.endswith(("_block", "_stone", "_bricks", "_planks")))
        if len(toks) > 1 and not [t for t in toks if t not in variants]:
            sc -= 20.0                             # nothing but variant tokens
        if reskin(it, toks):
            sc -= 14.0
        if short.endswith(_SHAPE_SUFFIX):
            sc -= 10.0
        if others and [t for t in toks if t in others]:
            sc -= 11.0                             # names another mod = compat
        if any(w in short for w in ("music_disc", "spawn_egg", "_statue",
                                    "_banner", "terrarium", "floating_",
                                    "_disc", "_cushion", "_lantern", "_vase")):
            sc -= 12.0
        sc -= _junk_score(it) * 3.0
        sc -= 0.4 * max(0, len(toks) - 2)
        return sc

    # One representative per family, by score alone. Preferring the plainer
    # name on a tie reads nicer ("track_acacia" over "track_byg_rainbow_oak")
    # but measurably costs ordering accuracy at every window I tried - 4.8% of
    # ingredient-after-product pairs became 5.4-5.8% - because swapping the
    # representative changes the dependency graph among the picks. Not worth it.
    best = {}
    for it in items:
        key = fam[it]
        cur = best.get(key)
        if cur is None or score(it) > score(cur):
            best[key] = it

    reps = list(best.values())
    reps.sort(key=lambda x: (-score(x), tiers.get(x, 99), x))
    return reps


def _item_worth(it: str, mid: str, scan: dict) -> bool:
    """Is this a thing a player would actually set out to get, as opposed to a
    furnishing? Judged from the pack's own data only, so it holds for mods that
    were never seen before."""
    items = set(scan.get("items", {}).get(mid, ()))
    short = it.split(":", 1)[1]
    if short.endswith(_SHAPE_SUFFIX):
        return False
    if any(w in short for w in ("music_disc", "spawn_egg", "_statue", "_banner",
                                "terrarium", "floating_", "_disc")):
        return False
    variants = _variant_tokens(items)
    parts = short.split("_")
    if len(parts) > 1 and not [t for t in parts if t not in variants]:
        return False
    if _junk_score(it) >= 3:
        return False
    return True


# Things that always feel like a reward: stock valuables everyone can use.
_REWARD_STAPLES = (
    "minecraft:diamond", "minecraft:emerald", "minecraft:gold_ingot",
    "minecraft:iron_ingot", "minecraft:copper_ingot", "minecraft:lapis_lazuli",
    "minecraft:redstone", "minecraft:ender_pearl", "minecraft:experience_bottle",
    "minecraft:amethyst_shard", "minecraft:netherite_scrap", "minecraft:quartz",
    "minecraft:blaze_rod", "minecraft:honeycomb", "minecraft:slime_ball",
    "minecraft:gunpowder", "minecraft:glowstone_dust", "minecraft:obsidian",
)


def reward_pool(scan: dict) -> list:
    """Items worth receiving. A reward should feel like a payout - handing the
    player a decorative fish tank for finishing a quest is worse than handing
    them nothing, and that is what mirroring the chapter's own items produced
    inside furniture chapters."""
    van = set(scan.get("vanilla") or ())
    out = [i for i in _REWARD_STAPLES if not van or i in van]
    for mid, items in (scan.get("items") or {}).items():
        if is_library(mid, mid):
            continue
        for it in items:
            short = it.split(":", 1)[1]
            if any(short.endswith(k) for k in ("_ingot", "_gem", "_alloy",
                                               "_crystal", "_shard", "_nugget")):
                if _junk_score(it) < 2 and _item_worth(it, mid, scan):
                    out.append(it)
    return out


# held-model variants of a real item: registered, named, and never obtainable.
_MODEL_VARIANT = ("_hand", "_in_hand", "_gui")


def payout_lanes(scan: dict) -> dict:
    """Per-namespace payout lists: what THIS mod can hand over, alternating a
    thing that ACTS with a thing you stockpile.

    Why this exists. `reward_pool` keeps only `_ingot/_gem/_alloy/_crystal/
    _shard/_nugget`, so every item reward in the book was a crafting input, and
    the payout branch then picked one by a GLOBAL progression index - blind to
    which mod the quest was even about. Measured on `build_chapters` output
    (scan.pkl, 408 quests / 156 item rewards): non-bulk reward share 0.019,
    capability-reward chapters 3/24 = 0.125, and reward/task namespace lift
    -0.1 points against a within-chapter shuffle null. A lift of zero means the
    rewards could have been dealt out at random and no measurement would
    change - so the player cannot read the payout as payment for the task.

    The vocabulary is the app's own `_ITEM_TIER0` (machines, altars, gear -
    "the things a chapter is actually about") and `_ITEM_TIER2` (tools, food,
    containers), matched on word boundaries by `_matches_marker`. Deliberately
    NOT the checker's taxonomy, so passing CHK-05/06 is evidence and not
    circularity. Nothing here is pack-specific: it reads `scan["items"]`, the
    scan's tier map for ordering, and the existing `_junk_score` /
    `_item_worth` filters, so an unseen pack gets lanes from what it ships.
    """
    tiers = scan.get("tier") or {}
    src = dict(scan.get("items") or {})
    # vanilla is a namespace like any other - chapter one asks for minecraft:
    # items and must be payable from minecraft: things you can use.
    src.setdefault("minecraft", sorted(i for i in (scan.get("vanilla") or ())
                                       if i.startswith("minecraft:")))
    out = {}
    for mid, items in src.items():
        if mid != "minecraft" and is_library(mid, mid):
            continue
        cap, use, mat, rest = [], [], [], []
        for it in items:
            if ":" not in it:
                continue
            short = it.split(":", 1)[1]
            if short.endswith(_MODEL_VARIANT):
                continue
            if _junk_score(it) >= 3 or not _item_worth(it, mid, scan):
                continue
            if _matches_marker(short, _ITEM_TIER0):
                cap.append(it)
            elif _matches_marker(short, _ITEM_TIER2):
                use.append(it)
            elif any(short.endswith(k) for k in ("_ingot", "_gem", "_alloy",
                                                 "_crystal", "_shard", "_nugget")):
                mat.append(it)
            else:
                rest.append(it)
        key = lambda x: (tiers.get(x, 5), x)
        cap.sort(key=key)
        use.sort(key=key)
        mat.sort(key=key)
        # Capability first, then usables; caps keep one mod from swamping the
        # book. Interleaved 1:1 with that mod's own materials - always paying
        # the fancy thing would be the "hand out one cosmetic per chapter"
        # exploit, and the authored corpus is 41% non-bulk, not 100%.
        nonbulk = cap[:20] + use[:10]
        lane = []
        for a, b in zip(nonbulk, mat + [None] * len(nonbulk)):
            lane.append(a)
            if b:
                lane.append(b)
        lane += [m for m in mat if m not in lane][:len(nonbulk)]
        if not lane and rest:
            # A mod with no machines, tools or materials still deserves to pay
            # its own quests: a furniture mod paying out furniture reads as
            # payment, a furniture task paid with another mod's ingot does
            # not. Measured on the built book (2026-08-29): every one of the
            # laneless namespaces fell through to the global pool, which is
            # why Tech Miscellany matched its task namespace on 0 of 3
            # reward-bearing quests and CHK-04's lift sat below its +4.0pp
            # target. `rest` already passed the same worth/junk/variant
            # filters as the lanes above - this is not a relaxation, just the
            # same standard applied to mods whose best items name no tier.
            rest.sort(key=key)
            lane = rest[:12]
        if lane:
            out[mid] = lane
    return out


def _pool_order(pool: list, scan: dict) -> list:
    """Order a chosen set of items so an ingredient always precedes what it
    builds - measured only among THESE items.

    Absolute crafting depth was too noisy to order by: tag alternatives and
    vanilla side-chains put create:precision_mechanism (late) above
    create:andesite_alloy (first). Restricting the graph to the items actually
    being used removes all of that - the only question left is which of these
    twenty feed which.
    """
    edges = scan.get("edges") or {}
    alts = scan.get("alts") or {}
    want = set(pool)
    # pack-author gating on top of the recipe graph
    mid = pool[0].split(":")[0] if pool else ""
    gate_pairs = _GATING.get(mid) or set()

    def ingredients(it):
        out = set(edges.get(it) or ())
        for grp in alts.get(it, ()):
            out |= grp
        return out

    # transitive pool-ancestors of each item, walking the full recipe graph but
    # only recording hits that are themselves in the pool
    need = {}
    for it in pool:
        seen, stack, hits, steps = {it}, [it], set(), 0
        while stack and steps < 4000:
            steps += 1
            cur = stack.pop()
            for g in ingredients(cur):
                if g in seen:
                    continue
                seen.add(g)
                if g in want:
                    hits.add(g)
                stack.append(g)
        for a, b in gate_pairs:
            if b == it and a in want:
                hits.add(a)
        hits.discard(it)
        need[it] = hits

    # Soft ordering keys that exist for EVERY mod, no curation required:
    #   - the mod's own advancement-tree depth (its author's declared order)
    #   - the average position real pack authors gave the item, from the
    #     harvested consensus (only entries 2+ packs agree on)
    # Hard constraints (recipes, pack gating) still decide what CAN come next;
    # these decide which of the ready items SHOULD. Recipes alone missed every
    # non-crafting gate - drops, bosses, custom serializers - which is exactly
    # where the "late item early" reports kept coming from.
    adv_depth = {}
    for r in (scan.get("progression") or {}).get(mid) or []:
        if r.get("item"):
            adv_depth.setdefault(r["item"], r.get("depth", 0))
    cons_pos = {}
    for e in (_HARVESTED_DB.get(mid) or []):
        if isinstance(e, dict) and e.get("item") and e.get("packs", 0) >= 2:
            cons_pos.setdefault(e["item"], e.get("pos", 0.5))

    rank = {it: i for i, it in enumerate(pool)}
    nrank = max(1, len(pool) - 1)

    def soft(x):
        # STRICT priority, not an average. Mods have one real path and the
        # consensus of many independent pack authors is the best record of it
        # that exists - averaging it with weaker signals diluted exactly the
        # information we trust most (measured: 13.1%% -> see quality_lab).
        if x in cons_pos:
            return cons_pos[x]
        if x in adv_depth:
            return min(1.0, adv_depth[x] / 12.0)
        return rank[x] / nrank

    out, placed = [], set()
    remaining = list(pool)
    while remaining:
        ready = [x for x in remaining if not (need[x] - placed)]
        if not ready:                      # a cycle - fall back to importance
            ready = [min(remaining, key=lambda x: (len(need[x] - placed), rank[x]))]
        ready.sort(key=lambda x: (soft(x), len(need[x]), rank[x]))
        for x in ready:
            out.append(x)
            placed.add(x)
        remaining = [x for x in remaining if x not in placed]
    return out


def pick_quest_items(mid: str, scan: dict, n: int) -> list:
    """Choose n items for one mod's questline and order them for play.

    Two separate jobs, and conflating them is what made chapters read as random
    items. rank_mod_items decides WHICH items are worth a quest - it drops the
    material variants and vanilla reskins that made SecurityCraft ask for 453
    reinforced blocks. _pool_order then decides WHAT ORDER, so an ingredient
    never comes after the thing it builds.
    """
    pool = rank_mod_items(mid, scan)
    if not pool:
        return []
    # How much of this mod is actually questable? A furniture mod has no
    # twentieth milestone - asking for one produces the black_curtain /
    # brown_curtain / cyan_curtain filler that made chapters feel padded.
    # Content mods are unaffected: their real items score well above zero.
    solid = sum(1 for it in pool[:n * 2] if _item_worth(it, mid, scan))
    n = max(4, min(n, solid if solid else n))
    return _pool_order(pool[:n], scan)


def _version_ok_ids(mid: str, scan: dict) -> set:
    """Consensus ids that survive a reality check against the installed jar.

    The harvest spans Minecraft versions - packs on 1.21 contribute orderings
    for items that do not exist in 1.20.1, and mods with no 1.20.1 build at
    all still acquire a consensus. Rather than trust the corpus, intersect it
    with what THIS pack actually ships; a consensus id the jar does not have
    is noise whatever version it came from.
    """
    have = set(scan.get("items", {}).get(mid, ()))
    if not have:
        return set()
    return {e for e in (_HARVESTED_ORDER.get(mid) or []) if e in have}


def _load_guide_signal() -> tuple:
    """What human guide videos teach, mined from transcripts. -> (order, weight)

    A fifth ordering source, and the only one that reflects how a person
    actually TEACHES a mod. The others all read the pack or the jar:
      * consensus knows what packs quest, not what a beginner is shown first
      * advancements start wherever the mod author put them (Create's tree
        opens on brass_hand, which no player touches early)
      * recipes give hard constraints but no sense of importance

    Measured on Create across four guides: create:wrench appears 6% into the
    video with 11 praise cues, next to shaft, water_wheel, cogwheel and
    goggles - precisely the early-game set the generator was missing when the
    user complained the questline "doesn't have the essential tools at the
    start, like the wrench".

    order:  mod -> [ids, earliest-taught first]
    weight: id  -> desirability lift, so a heavily-taught item survives the
            per-mod cap instead of being replaced by filler.
    """
    order: dict = {}
    weight: dict = {}
    d = moddb_path() / "guides"
    if not d.is_dir():
        return order, weight
    for f in sorted(d.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = []
        for iid, h in (doc.get("items") or {}).items():
            m = int(h.get("mentions") or 0)
            v = int(h.get("videos") or 0)
            # One mention in one video is as likely to be a mishearing as a
            # fact - Whisper renders "Ars Nouveau" as "RS Nouveau". Require
            # corroboration before believing it.
            if v < 2 and m < 3:
                continue
            at = float(h.get("first_at") or 0.5)
            # an explicit "first thing you do" outranks where the word landed
            if h.get("early_cue"):
                at -= 0.15
            if h.get("late_cue"):
                at += 0.15
            rows.append((at, iid))
            lift = min(6.0, 0.6 * m ** 0.5 + 1.2 * (v - 1)
                       + 0.8 * int(h.get("praise") or 0) ** 0.5)
            if h.get("warn"):
                lift -= 1.5 * int(h["warn"]) ** 0.5
            weight[iid] = max(weight.get(iid, 0.0), lift)
        rows.sort()
        if rows:
            order[doc.get("mod") or f.stem] = [i for _a, i in rows]
    return order, weight


_GUIDE_ORDER, _gw = _load_guide_signal()
_GUIDE_WEIGHT.update(_gw)


def _note_rows(v) -> list:
    """Normalise one human_notes section. -> [(ids, text)]

    Agents write these two shapes interchangeably: a bare sentence, or
    {"ids": [...], "note": "...", "quote": "..."}. Both are useful; only the
    second can be attached to a specific item.
    """
    out = []
    for x in (v if isinstance(v, list) else [v]):
        if isinstance(x, str):
            if x.strip():
                out.append(([], x.strip()))
        elif isinstance(x, dict):
            txt = " ".join(str(x.get(k) or "").strip()
                           for k in ("note", "quote") if x.get(k)).strip()
            ids = [i for i in (x.get("ids") or []) if isinstance(i, str)]
            if txt:
                out.append((ids, txt))
    return out


def _load_guide_notes() -> dict:
    """What guide narrators SAY about a mod, keyed by mod.

    The numeric mention counts are only half of what a transcript carries. The
    prose holds facts no jar or recipe scan can reach - one agent recorded that
    Create Diesel Generators' pumpjack hole recipe does not appear in JEI at
    all, so a player following JEI simply stalls. Another recorded that Blue
    Skies portals cannot be lit with flint and steel. Those are exactly the
    things a quest description exists to say.

    -> mod -> {"entry": [...], "gates": {id: [text]}, "traps": {id: [text]},
               "signature": {ids}, "loose": [...]}
    """
    out: dict = {}
    d = moddb_path() / "guides"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        hn = doc.get("human_notes") or {}
        if not hn:
            continue
        mid = doc.get("mod") or f.stem
        rec = {"entry": [], "gates": {}, "traps": {}, "signature": set(),
               "loose": [], "taught": []}
        for sect in ("entry", "gates", "traps", "signature", "order",
                     "boss_order", "prep", "ladder", "structures"):
            for ids, txt in _note_rows(hn.get(sect)):
                # An agent that could not source a section says so rather than
                # inventing one. Do not treat that disclaimer as content.
                if "NOT CAPTURED" in txt or "must not be invented" in txt:
                    continue
                if sect == "signature":
                    rec["signature"].update(ids)
                if sect == "entry":
                    rec["entry"].append(txt)
                # An ordered section with ids is a sequence a person wrote
                # deliberately, which beats any statistic derived from where a
                # word happened to fall in a transcript. AE2's says the mod
                # "does not start at a crafting bench, it starts at WORLDGEN"
                # and leads with the meteorite compass - an opening no jar,
                # recipe graph or mention-count would ever produce.
                if sect in ("order", "ladder", "tiers", "boss_order"):
                    for i in ids:
                        if i not in rec["taught"]:
                            rec["taught"].append(i)
                bucket = "traps" if sect == "traps" else (
                    "gates" if sect in ("gates", "prep") else None)
                if bucket:
                    for i in ids:
                        rec[bucket].setdefault(i, []).append(txt)
                    if not ids:
                        rec["loose"].append(txt)
        out[mid] = rec
    return out


_GUIDE_NOTES = _load_guide_notes()
_GUIDE_TRAP: dict = {}
_GUIDE_SIG: set = set()
for _m, _r in _GUIDE_NOTES.items():
    _GUIDE_SIG |= _r["signature"]
    for _i in _r["traps"]:
        # "Trap" covers two different things. Create Diesel Generators'
        # pumpjack_hole is flagged because its recipe is invisible in JEI -
        # the item is the mod's central gate, and demoting it would be exactly
        # wrong. Its ethanol bucket is flagged because it is genuinely the
        # worst fuel in the mod. An item that is ALSO a gate or a signature
        # item is essential-with-a-gotcha: keep it, and let the warning reach
        # the player through the description instead.
        if _i in _r["gates"] or _i in _r["signature"]:
            continue
        _GUIDE_TRAP[_i] = _GUIDE_TRAP.get(_i, 0) + 1


def best_progression(mid: str, scan: dict) -> list:
    """The best-known play order for one mod: [{item|kill, title, why}, ...].

    Same priority the offline builder uses, in one place so BOTH paths get it:
      1. a hand-checked chain from the mod's wiki (mod_progression.py)
      2. the consensus ordering mined from ~100 real packs
      3. the mod's own advancement tree
    The AI prompt previously read only source 3, so Tropicraft - which has no
    advancements - got NO ordering at all and opened on a random sapling, and
    Create was told brass_hand comes first because that is where its
    advancement tree starts. Sources 1 and 2 exist precisely to fix that.
    """
    known = set(scan.get("items", {}).get(mid, ()))
    ents = set(scan.get("entities", {}).get(mid, ()))
    vanilla = scan.get("vanilla") or _VANILLA_ITEM_SET
    structs = set((scan.get("structures") or {}).get(mid, ()))
    out, seen = [], set()

    def add(ref, title, why):
        for cand in str(ref).split("|"):        # "a|b" = renamed between versions
            cand = cand.strip()
            if cand.startswith("dim:"):
                # "reach this dimension" is not an item. The Twilight portal and
                # the Tropics have NO obtainable item, so asking for one gave a
                # decorative trophy as the chapter opener.
                if cand not in seen:
                    seen.add(cand)
                    out.append({"dimension": cand[4:], "title": title, "why": why})
                    return
            if cand.startswith("structure:"):
                # Worldgen mods add PLACES, not items. Terralith, Repurposed
                # Structures, Dungeons Arise and every YUNG's mod register
                # zero items - Terralith has no assets/ directory at all - so
                # a structure step is the only honest way to quest them.
                sid = cand[10:]
                if sid in structs and cand not in seen:
                    seen.add(cand)
                    out.append({"structure": sid, "title": title, "why": why})
                    return
            elif cand.startswith("kill:"):
                if cand[5:] in ents and cand not in seen:
                    seen.add(cand)
                    out.append({"kill": cand[5:], "title": title, "why": why})
                    return
            elif (cand in known or cand in vanilla) and cand not in seen:
                seen.add(cand)
                out.append({"item": cand, "title": title, "why": why})
                return

    # Curated steps are hand-checked, so they are trusted as written. The mined
    # and advancement sources are not: a mod can ship an advancement for
    # crafting a decorative miniature, which is not progression. Junk-filter
    # those two, never the curated chain.
    curated_refs = _curated_chain(mid) or []
    for ref, title, desc in curated_refs:
        # A chain answers the ORDER question first, which is right - but it
        # used to answer the PROSE question too, and that made guide data
        # dead weight: ablation showed removing every video source changed one
        # mod's order and 13 descriptions in 3000, because all 14 guided mods
        # also had a chain. A step with no prose of its own can take the
        # narrator's warning instead of shipping bare.
        add(ref, title, desc or guide_tip(str(ref).split("|")[0].strip()))
    # A mod that adds a dimension needs a "get there" step. Curated chains say
    # this explicitly; for every other mod, derive it - reaching the place is
    # the milestone, and there is usually no item that represents it.
    if not any(str(r[0]).startswith("dim:") for r in curated_refs):
        for dim in sorted((scan.get("dimensions") or {}).get(mid, ())):
            # If a guide explained how to get in, say so. That instruction is
            # the whole content of the step; without it this quest was an
            # unexplained "go to the place".
            _why = guide_entry(mid) or                 "A whole dimension this mod adds. Getting there is the goal."
            add("dim:" + dim, "Journey to %s" % _pretty_name(dim), _why)
            break
    # Guide videos before consensus: a human teacher's order is what a player
    # expects, and add() keeps the first position an id gets, so consensus
    # still supplies the long tail of items no video happens to mention.
    _have = set(known) | set(vanilla)
    # A sequence an agent wrote out explicitly outranks one inferred from
    # where words landed in a transcript, so taught order goes first.
    for it in (_GUIDE_NOTES.get(mid) or {}).get("taught", ()):
        if it in _have and _junk_score(it) < 3:
            add(it, "", guide_tip(it))
    for it in _GUIDE_ORDER.get(mid, []):
        if it in _have and _junk_score(it) < 3:
            add(it, "", "")
    _ok = _version_ok_ids(mid, scan)
    for it in _HARVESTED_ORDER.get(mid, []):
        if it in _ok and _junk_score(it) < 3:
            add(it, "", "")
    for r in (scan.get("progression") or {}).get(mid) or []:
        if _junk_score(r.get("item", "")) < 3:
            add(r.get("item", ""), r.get("title", ""), "")
    return out


def _resolve_focus(focus: str, scan: dict) -> str:
    """Map whatever the plan called the chapter's mod onto a real modid.

    The plan is asked for a modid but answers with the display name ("Create",
    "SecurityCraft", "Twilight Forest"). Comparing that against scan["items"],
    which is keyed by lowercase modid, missed every time and quietly sent the
    VANILLA-only branch of the prompt - so every AI chapter came back 100%
    vanilla no matter which mod it was supposed to be about.
    """
    f = (focus or "").strip()
    if not f or f.lower() == "vanilla":
        return f.lower()
    items = scan.get("items", {})
    if f in items:
        return f
    low = f.lower()
    if low in items:
        return low
    squash = re.sub(r"[^a-z0-9]", "", low)
    if squash in items:
        return squash
    for m in scan.get("mods", []):
        mid = m.get("mod_id", "")
        if mid not in items:
            continue
        if squash == re.sub(r"[^a-z0-9]", "", str(m.get("name", "")).lower()):
            return mid
        if squash and squash == re.sub(r"[^a-z0-9]", "", mid):
            return mid
    # display names need not resemble the modid ("MrCrayfish's Furniture Mod"
    # ships as refurbished_furniture), so fall back to containment either way
    if squash:
        cands = []
        for m in scan.get("mods", []):
            mid = m.get("mod_id", "")
            if mid not in items:
                continue
            nm = re.sub(r"[^a-z0-9]", "", str(m.get("name", "")).lower())
            if nm and (squash in nm or nm in squash):
                cands.append(mid)
        if len(cands) == 1:
            return cands[0]
        for mid in items:                 # last resort: unambiguous prefix
            if squash.startswith(mid) or mid.startswith(squash):
                return mid
    return low


def mod_play_order(mid: str, scan: dict, cap: int = 140) -> list:
    """Every target for one mod, in play order: curated/gating-aware milestones
    first, then the ranked remainder in pool order. One list, one truth - the
    prompt shows it and the assembler enforces it, so the model cannot put the
    twig wand (Botania's second craft) thirty quests deep no matter what it
    guesses. Entries are item ids, "kill:<entity>" or "dim:<dimension>"."""
    seen, out = set(), []

    def add(key):
        if key and key not in seen:
            seen.add(key)
            out.append(key)

    for r in best_progression(mid, scan):
        if r.get("item"):
            add(r["item"])
        elif r.get("kill"):
            add("kill:" + r["kill"])
        elif r.get("dimension"):
            add("dim:" + r["dimension"])
    for it in pick_quest_items(mid, scan, cap):
        add(it)
    rest = [i for i in rank_mod_items(mid, scan)
            if i not in seen and _item_worth(i, mid, scan)]
    for it in _pool_order(rest[: max(0, cap - len(out))], scan):
        add(it)
    return out[:cap]


def build_chapter_prompt(spec: dict, scan: dict, opts: dict, language: str,
                         prev_last) -> str:
    """Prompt for ONE chapter's quests. Bounded input and output."""
    focus = _resolve_focus(str(spec.get("focus") or ""), scan)
    n = max(4, min(40, _int_of(spec.get("quests", 12), 12)))
    craft = scan.get("craftable", {})
    ids, ents = [], []
    ids_ordered = False
    if focus and focus != "vanilla" and focus in scan["items"]:
        # ONE ordered list instead of milestones + a flat unordered pool. With
        # the flat pool the model invented positions for everything not in the
        # milestone block - which is how the twig wand, Botania's second
        # craft, ended up thirty quests deep.
        ids = [i for i in mod_play_order(focus, scan, 110)
               if ":" in i and not i.startswith(("kill:", "dim:", "structure:"))]
        ids_ordered = True
        ents = _mob_entities(scan["entities"].get(focus, ()),
                             set(scan["items"][focus]))[:20]
    else:                                   # vanilla, or a cross-mod theme
        _v = sorted(scan.get("vanilla") or _VANILLA_ITEMS)
        ids = [i for i in _v if _junk_score(i) < 2][:170]
        if focus and focus != "vanilla":
            for mid in list(scan["items"])[:40]:
                pool = sorted(scan["items"][mid],
                              key=lambda x: (_junk_score(x), x not in craft.get(mid, ()), x))
                ids += _functional_ids(pool, 6)
            ids = ids[:260]

    L = ["Write the quests for ONE chapter of a Minecraft FTB Quests book (MC 1.20.1).", ""]
    L.append("CHAPTER: %s" % spec.get("title", "Chapter"))
    if spec.get("summary"):
        L.append("ABOUT: %s" % spec["summary"])
    L.append("Write exactly %d quests, ordered from the first thing a new player can "
             "reach to the chapter's mastery item." % n)
    if ids_ordered:
        L.append("The [USABLE ITEM IDS] list below is ALREADY IN PLAY ORDER, "
                 "earliest first, derived from this pack's recipes and real "
                 "packs' gating. Your quests MUST follow that relative order - "
                 "never place a later-listed item before an earlier-listed one.")
    # Hand the model the mod's OWN progression, taken from its advancement tree.
    # Without this the AI invents an order and the questline reads as random items.
    prog = best_progression(focus, scan) if focus and focus != "vanilla" else []
    if len(prog) >= 4:
        L.append("")
        L.append("[THIS MOD'S REAL PROGRESSION] hand-checked against the mod's wiki, "
                 "the consensus of ~100 published packs, and the mod's own "
                 "advancement tree — earliest first. FOLLOW THIS ORDER. Start on "
                 "step 1: that is genuinely where a new player begins, including "
                 "how they first REACH this mod's content. Finish on the last.")
        for k, r in enumerate(prog[:min(n + 6, 26)], 1):
            # Every step kind needs its own label. This was a chained ternary
            # with no structure branch, so a worldgen step rendered as
            # "KILL None" and the model was asked to write a quest about it.
            if r.get("dimension"):
                tgt = "REACH DIMENSION " + str(r["dimension"])
            elif r.get("structure"):
                tgt = "FIND STRUCTURE " + str(r["structure"])
            elif r.get("kill"):
                tgt = "KILL " + str(r["kill"])
            else:
                tgt = str(r.get("item") or "")
            L.append("  %2d. %-44s %s" % (k, tgt, r.get("title") or ""))
            # the "why" is hand-checked from the mod's wiki - it is the real
            # explanation of how a step works, so the model should use it
            # rather than invent one
            if r.get("why"):
                L.append("        WHY: %s" % r["why"])
            # What a guide narrator warned about this item. Unreachable from
            # any jar, and exactly what a description exists to say - without
            # it the model writes confident prose about a step it does not
            # know is a trap.
            _t = guide_tip(r.get("item") or "")
            if _t and _t != (r.get("why") or ""):
                L.append("        WATCH OUT: %s" % _t)
        L.append("Where a step above has a WHY, put that explanation in that "
                 "quest's description - reworded, but keep every fact.")
        L.append("Where a step has a WATCH OUT, that is a real trap players hit. "
                 "Warn about it in that quest's description, in your own words.")
        L.append("Fill any gaps with items that sit naturally between these steps.")
        L.append("NEVER ask for an item before something its recipe needs — an "
                 "ingredient always comes first.")
    L.append("")
    L.append("[STYLE] " + AESTHETIC_TEXT.get(opts.get("aesthetic", "balanced"),
                                             AESTHETIC_TEXT["balanced"]))
    if _STYLE:
        rules = (_STYLE.get("title_rules") or [])[:8]
        drules = (_STYLE.get("desc_rules") or [])[:8]
        banned = (_STYLE.get("banned") or [])[:8]
        if rules:
            L.append("[TITLE STYLE] " + "  ".join("- " + str(r) for r in rules))
        if drules:
            L.append("[DESC STYLE] " + "  ".join("- " + str(r) for r in drules))
        if banned:
            L.append("[NEVER WRITE] " + "; ".join(str(b) for b in banned))
        pats = _STYLE.get("title_patterns") or []
        shown = []
        for pt in pats[:6]:
            ex = (pt.get("examples_original") or [])[:3]
            if ex:
                shown.append("%s: %s" % (pt.get("pattern", "?"), ", ".join(map(str, ex))))
        if shown:
            L.append("[TITLE PATTERNS - imitate the SHAPE, invent your own words] "
                     + " | ".join(shown))
    L.append("[REWARDS] " + REWARD_TEXT.get(opts.get("reward", "standard"),
                                            REWARD_TEXT["standard"]))
    L.append("")
    L.append("[DESCRIPTIONS] Give at least HALF the quests a description, and every "
             "quest that gates progress (a portal, a key tool, a boss) MUST have one "
             "that says HOW - the player should never have to leave the book to find "
             "out how to reach something.")
    if _VANILLA_SOURCE and ids:
        facts = [(i, _VANILLA_SOURCE[i]) for i in ids[:120]
                 if i.startswith("minecraft:") and i in _VANILLA_SOURCE]
        if facts:
            L.append("")
            L.append("[VANILLA FACTS] Where these actually come from, read from "
                     "the game's own loot tables and recipes. Use these and do "
                     "NOT invent an origin - a wrong 'fact' stated confidently "
                     "is worse than saying nothing:")
            for i, txt in facts[:40]:
                L.append("  %-34s %s" % (i, txt))
    L.append("If a step needs a machine, altar, multiblock or station, that thing "
             "gets its OWN quest BEFORE the step that uses it - never explain how "
             "to build or operate it inside another quest's description. A "
             "description that teaches a multi-step build is a quest you failed "
             "to write.")
    L.append("Descriptions are a list of lines and support Minecraft colour codes. "
             "Use them: '%s' for body text, '%s' to name the item or mechanic, "
             "'%s' for a warning or must-not-miss step, '%s' for a short italic "
             "aside. Never leave a line plain white, and never use a bare '&'."
             % (DESC_BODY, DESC_KEY, DESC_WARN, DESC_FLAV))
    L.append('Example: "description":["&7Rotation is the whole mod.","",'
             '"&6Tip: &7place cogwheels so their teeth mesh.","",'
             '"&8&oIt never did run quietly."]')
    L.append("")
    L.append('[TASKS] item {"type":"item","item":"<id>","count":N}   '
             'kill {"type":"kill","entity":"<id>","value":N}   '
             'checkmark {"type":"checkmark","title":"..."}')
    L.append('[REWARDS] {"type":"item","item":"<id>","count":N}   '
             '{"type":"xp","xp":N}   {"type":"xp_levels","xp_levels":N}')
    L.append("")
    L.append("Use ONLY these item ids (any minecraft: id is also fine). Never invent one:")
    L.append(", ".join(ids))
    if ents:
        L.append("")
        L.append("Entity ids for kill tasks: " + ", ".join(ents))
    L.append("")
    if str(opts.get("layout", "")).startswith("ai"):
        L.append('Also give every quest "x" and "y" so the chapter reads as a designed '
                 'diagram: a main spine running left to right about 2.5 apart, optional '
                 'branches offset above and below. No two quests may share a position.')
    if prev_last:
        L.append('The first quest must have "dependencies":["%s"] so this chapter '
                 "continues from the previous one." % prev_last)
    L.append('Chain the rest with "dependencies" (each quest depends on an earlier '
             "quest id in THIS chapter). Quest ids must be unique strings.")
    L.append("All text in %s." % language)
    L.append("")
    L.append("Reply with ONLY this JSON:")
    L.append('{"quests":[{"id":"%sq1","title":"...","description":["..."],'
             '"tasks":[{"type":"item","item":"minecraft:oak_log","count":16}],'
             '"rewards":[{"type":"xp","xp":50}]}]}' % spec.get("id", "c"))
    return "\n".join(L)


# ========================================================================== #
#  3b. Offline quest builder  (no AI — whole book straight from the mod scan)
# ========================================================================== #

_GROUP_FOR_CAT = {
    "tech": "Tech", "magic": "Magic", "world": "Adventure", "mob": "Adventure",
    "food": "Farm and Food", "utility": "Utility", "decor": "Decoration",
    "unknown": "Expansion",
}
# --- what makes a good quest goal -------------------------------------------
# tier 0: the things a chapter is actually *about* — machines, altars, gear
_ITEM_TIER0 = ("machine", "engine", "controller", "mechanism", "reactor", "generator",
               "altar", "pedestal", "catalyst", "spellbook", "grimoire", "codex",
               "sword", "pickaxe", "axe", "bow", "crossbow", "hammer", "staff",
               "wand", "scythe", "spear", "trident", "shield",
               "helmet", "chestplate", "leggings", "boots", "backpack", "jetpack",
               "totem", "relic", "amulet", "ring", "charm", "talisman")
# tier 1: signature materials and components you chase
_ITEM_TIER1 = ("ingot", "gem", "crystal", "shard", "alloy", "essence", "core",
               "star", "heart", "nugget", "dust", "plate", "gear", "rod", "coil",
               "circuit", "capacitor", "cell", "canister", "tank", "drill", "press",
               "casing", "mold", "blueprint", "schematic", "seed", "sapling", "berry")
# tier 2: usable tools / utility blocks
_ITEM_TIER2 = ("furnace", "crusher", "grinder", "mixer", "smelter", "assembler",
               "crafter", "workbench", "table", "chest", "barrel", "tank", "pipe",
               "conveyor", "belt", "motor", "pump", "battery", "lamp", "bucket",
               "boat", "cart", "saddle", "food", "stew", "soup", "pie", "bread")
_ITEM_GOOD = _ITEM_TIER0 + _ITEM_TIER1 + _ITEM_TIER2

# hard-rejected: never a quest goal. Building-block variants are the big one —
# a "Quark" chapter full of stools, carpets and vertical slabs is noise.
_ITEM_BAD = (
    # technical / internal
    "spawn_egg", "debug", "creative", "barrier", "command_block", "_placeholder",
    "unknown_", "test_", "_top", "_bottom", "_base", "_half", "_side", "_end",
    "_open", "_closed", "_on", "_off", "_lit", "_unlit", "_empty", "_filled",
    "_stage", "_variant", "_part", "_fake", "_dummy", "_null",
    # decorative replicas: craftable ornaments that look like progression in a
    # mod's advancement tree but gate nothing (twilightforest miniatures)
    "miniature_structure", "_logo",
    # seasonal and event cosmetics - never progression
    "_spooky", "_xmas", "_christmas", "_halloween", "_easter", "_valentine",
    "_birthday", "_anniversary", "april_fools",
    # collectibles and furnishings that are never a goal: measured as the most
    # common tokens across everything the ranker wrongly promoted
    "music_disc", "_disc", "spawn_egg", "_statue", "_banner", "_trophy_minor",
    "curtain", "_carpet", "floating_", "terrarium", "_bucket_empty",
    # cosmetic / trim variants
    "_trim", "_pattern", "pattern_", "_sherd", "smithing_template", "cosmetic_",
    "_banner", "banner_pattern", "music_disc", "_disc",
    # building-block shape variants — the "random items" complaint
    "_slab", "_stairs", "_wall", "_fence", "_fence_gate", "_gate", "_button",
    "_pressure_plate", "_trapdoor", "_door", "_pane", "_bars", "_carpet", "_rug",
    "_pillar", "_column", "_post", "_beam", "_banister", "_railing", "_ladder",
    "_shingle", "_shingles", "_tile", "_tiles", "_brick", "_bricks", "_planks",
    "_plank", "_log", "_wood", "_leaves", "_leaf", "_vine", "_moss",
    "_sand", "_gravel", "_dirt", "_grass", "_path", "_soil", "_mud",
    "vertical_", "polished_", "chiseled_", "chiselled_", "cut_", "smooth_",
    "mossy_", "cracked_", "weathered_", "waxed_", "framed_", "hollow_", "stripped_",
    # furniture / decor clutter
    "_stool", "_chair", "_couch", "_sofa", "_bench", "_desk", "_shelf", "_cabinet",
    "_drawer", "_wardrobe", "_curtain", "_blind", "_sconce",
    "_chandelier", "_lampshade", "_mirror", "_clock", "_rug_",
    "_poster", "_frame", "_statue", "_bust", "_vase",
    "_plate_stack", "_bottle_empty", "_trash",
    "_window", "_shutter", "_awning", "_pipe_small", "_spire", "_bulkhead",
    "potted_", "_sign", "_hanging_sign", "_flag",
)

# Nouns that are usually furniture but are real content in the right mod: a
# cooking pot is Farmer's Delight's core station, a beer mug is Brewery's whole
# point. Rejecting these outright ("_pot" was there to catch flower_pot) threw
# away the best quest targets those mods have. Demoted, not banned - they lose
# to a mod's genuine milestones but still beat a stack of curtains.
_ITEM_SOFT_BAD = (
    "_pot", "_bowl", "_cup", "_mug", "_lantern", "_lamp", "_candle", "_torch",
    "_stand", "_painting", "_bed", "_barrel", "_crate", "_basket", "_keg",
)


_COLOUR_WORDS = ("white", "orange", "magenta", "yellow", "lime", "pink",
                 "gray", "grey", "cyan", "purple", "blue", "brown",
                 "green", "red", "black", "light", "dark")
_COLOUR_SIBLINGS: dict = {}


def _colour_variant(item_id: str) -> bool:
    """Is a colour-prefixed name REALLY one of a colour set?

    Rejecting every colour-prefixed id caught the decoration it was aimed at
    but also killed real content: goety:dark_wand (a dark-magic mod, not a
    colour) and vinery:red_grape_seeds. The honest test is whether siblings
    exist - a genuine colour set has several. Needs the sibling index, which
    scan_mods fills; with no index we cannot tell, so we do NOT reject.
    """
    ns, _, short = item_id.partition(":")
    parts = short.split("_")
    if len(parts) < 2:
        return False
    rest = "_".join(parts[1:])
    sibs = _COLOUR_SIBLINGS.get(ns)
    if not sibs:
        return False
    return sibs.get(rest, 0) >= 3


def _matches_marker(name: str, markers) -> bool:
    """Does an item name carry one of these markers, on a WORD boundary?

    Plain substring matching threw away real items: "chocolate_gateau" matched
    "_gate" (meant for fence_gate), "small_cooking_pot" matched "_pot" (meant
    for flower_pot), "beer_mug" matched "_mug". A marker beginning with "_" is
    a suffix and must land on the final token; anything else must match a whole
    token, except explicit multi-word markers which are matched literally.
    """
    toks = name.split("_")
    tokset = set(toks)
    for m in markers:
        if m.startswith("_"):
            if toks and toks[-1] == m[1:]:
                return True
        elif "_" in m:
            if m in name:
                return True
        elif m in tokset:
            return True
        elif m.endswith("_") and name.startswith(m):
            return True
    return False


def _junk_score(item_id: str) -> int:
    """0 = a real goal, higher = more like decoration/filler. >=3 means reject."""
    p = item_id.split(":")[-1]
    if any(k in p for k in _NOT_A_GOAL):
        return 3            # a creative-tab picture is not something to fetch
    if _matches_marker(p, _ITEM_BAD):
        return 3
    soft = 1 if _matches_marker(p, _ITEM_SOFT_BAD) else 0
    # a colour-prefixed name is nearly always a decoration variant, whatever
    # noun follows it ("green_table_cloth", "light_gray_stool") — check this
    # before the tier lists so a stray "table"/"lamp" can't rescue it
    head = p.split("_")[0]
    if head in _COLOUR_WORDS and _colour_variant(item_id):
        return 3
    if any(g in p for g in _ITEM_TIER0):
        return soft
    if any(g in p for g in _ITEM_TIER1):
        return soft
    if any(g in p for g in _ITEM_TIER2):
        return min(2, 1 + soft)
    return min(2, 2 + soft) if soft else 2
_PASSIVE_MOB = ("sheep", "cow", "pig", "chicken", "boar", "deer", "rabbit", "bunny",
                "cod", "salmon", "crab", "turtle", "frog", "duck", "goat", "llama",
                "penguin", "seal", "butterfly", "firefly", "snail")
_BULK = ("ingot", "dust", "nugget", "gem", "plate", "rod", "shard", "seed", "berry",
         "log", "plank", "ore", "raw_", "crystal", "flower", "leaf")

_VANILLA_CHAPTERS = [
    ("Vanilla", "Overworld Beginnings", "minecraft:crafting_table", [
        # ONE log, not sixteen. This is the first thing anybody who opens the
        # book is asked to do, and the reference packs make it a quest the
        # player has effectively already finished - you are holding wood
        # before you ever open the book, so the book's first act is to tick.
        # Sixteen made the opening move a chore instead of an orientation.
        # (CHK-22: first cost <= 2 - the one measurement in the spec about
        # first impressions rather than structure.)
        ("Punch Some Wood", "minecraft:oak_log", 1,
         "You have probably done this already, which is the point. Take the "
         "tick, see how the book works, and read the next one before it asks "
         "you for anything that costs you an afternoon."),
        ("Stone Tools", "minecraft:stone_pickaxe", 1,
         "Wood mines nothing you actually want. A stone pickaxe is the "
         "gate to iron, and iron is the gate to most of the mods in this "
         "pack - so this is the cheapest quest here that genuinely "
         "unlocks something."),
        ("The Iron Age", "minecraft:iron_ingot", 12,
         "Almost every mod in this pack asks you for iron before it asks "
         "for anything exotic, so twelve is not the end of what you will "
         "need - it is roughly the point where you can stop mining and "
         "start building. Smelt raw iron in any furnace."),
        ("Hold the Line", "minecraft:shield", 1,
         "1 iron ingot and 6 planks. Worth making before you go "
         "anywhere new, because several of the mods here add things that "
         "hit far harder than a zombie, and a shield is the only answer "
         "you can craft this early."),
        ("Light It Up", "minecraft:torch", 32,
         "32 sounds like a lot until you are lighting a branch "
         "mine. Coal and a stick make 4 at a time, and nothing you do "
         "underground for the rest of this pack gets easier without "
         "them."),
        ("A Full Belly", "minecraft:bread", 8,
         "3 wheat per loaf. 8 is enough to stop food being the "
         "thing that ends your trip, and if this pack adds a farming mod "
         "you will find far better food than bread once you go looking "
         "for it."),
        ("Diamonds", "minecraft:diamond", 5,
         "5 is a pickaxe and a spare, or the start of an enchanting "
         "setup. They sit in the bottom 16 layers; bring torches and "
         "something to put lava out, because the fastest way to lose five "
         "diamonds is to mine into it holding them."),
        ("Enchanting", "minecraft:enchanting_table", 1,
         "2 diamonds, obsidian and a book. Surround it with bookshelves "
         "to reach the better enchantments - and check whether this pack "
         "adds its own way to enchant, because several mods do it more "
         "cheaply than vanilla does."),
        ("Brew Day", "minecraft:brewing_stand", 1,
         "You need a blaze rod, which means the Nether - so this one "
         "waits for the next chapter. Potions are the difference between "
         "surviving a boss and watching one, and most of the bosses in "
         "this pack were added by a mod rather than by Mojang."),
    ]),
    ("Vanilla", "Into the Nether", "minecraft:obsidian", [
        ("Open the Way", "minecraft:obsidian", 10,
         "10 blocks is a portal with the corners left out. You can build "
         "it without a diamond pickaxe by pouring water over lava in "
         "place, which is worth knowing if diamonds are not going well."),
        ("Fireproofing", "minecraft:magma_cream", 6,
         "Fire resistance turns the Nether from lethal into merely "
         "hostile. Magma cubes drop these, or you can craft them from "
         "slime and blaze powder - bring 6 and you have enough for the "
         "trip out and the trip back."),
        ("Blaze Rods", "minecraft:blaze_rod", 6,
         "Blazes spawn in nether fortresses. 6 gives you brewing powder "
         "and an eye or two of ender with room to spare, and a fair "
         "number of mods in this pack want a rod for their own first "
         "machine."),
        ("Nether Gold", "minecraft:gold_ingot", 16,
         "Wear 1 piece of gold armour and the piglins leave you alone. "
         "16 ingots is enough to trade with them for a while, and "
         "gold is common enough in the Nether that you will find it on "
         "the way to everything else."),
        ("Netherite", "minecraft:netherite_ingot", 1,
         "4 ancient debris and 4 gold. Debris hides below Y=16 and "
         "does not burn, so beds and TNT find it faster than a pickaxe "
         "does. 1 ingot upgrades 1 piece of gear - choose the piece "
         "you never take off."),
        ("Three Skulls", "minecraft:wither_skeleton_skull", 3,
         "Wither skeletons drop these rarely, so bring a looting sword "
         "and expect the fortress to take a while. 3 skulls and 4 "
         "soul sand is a Wither, and a Wither is a nether star - which "
         "more than one mod here treats as its real starting line."),
    ]),
    ("Vanilla", "The End", "minecraft:end_stone", [
        ("Eyes of Ender", "minecraft:ender_eye", 12,
         "Blaze powder and an ender pearl each. 12 is the worst case "
         "for filling a portal frame - throw them sparingly, because each "
         "one has a chance to shatter, and you will want a few spare to "
         "find the stronghold in the first place."),
        # Was "Slay the Dragon", which the task cannot possibly check. In the
        # 1.20.1 client jar dragon_breath has no recipe and appears in no loot
        # table at all - not even entities/ender_dragon.json, which is empty of
        # drops - so the only route to it is filling a bottle from the purple
        # cloud the dragon breathes *while it is alive*. The item is evidence
        # the fight started, never that it ended, and the quest ticked green
        # thirty seconds in: exactly the "told me the quest was complete when
        # clearly it was not" complaint. Titled for the bottling now, and kept
        # a verb rather than the bare item name so it stays findable.
        ("Bottle the Breath", "minecraft:dragon_breath", 1,
         "Take a glass bottle into the fight and fill it from the purple "
         "cloud while the dragon is still breathing. It is the one thing "
         "in the End you cannot go back for afterwards, and lingering "
         "potions need it."),
        ("Wings", "minecraft:elytra", 1,
         "End cities, on the ships, guarded by a shulker. Bring blocks "
         "and patience: the outer islands are a long gateway hop away, "
         "and once you have these the rest of this pack gets a great deal "
         "smaller."),
        ("Chorus Harvest", "minecraft:chorus_fruit", 16, "Teleporting snacks from the outer islands."),
        ("Shulker Shells", "minecraft:shulker_shell", 4, "Portable storage, hard-won."),
    ]),
]


# Vanilla items worth building quests around. The mod scan only sees mod jars,
# so without this a "diamond" or "combat" theme can only reach for mod items.
_VANILLA_ITEMS = tuple("minecraft:" + s for s in """
oak_log birch_log spruce_log jungle_log acacia_log dark_oak_log mangrove_log cherry_log
oak_planks stick crafting_table furnace chest barrel torch ladder
wooden_pickaxe stone_pickaxe iron_pickaxe golden_pickaxe diamond_pickaxe netherite_pickaxe
wooden_axe stone_axe iron_axe diamond_axe netherite_axe
wooden_sword stone_sword iron_sword golden_sword diamond_sword netherite_sword
wooden_shovel iron_shovel diamond_shovel wooden_hoe iron_hoe diamond_hoe netherite_hoe
bow crossbow arrow spectral_arrow trident shield fishing_rod flint_and_steel shears
leather_helmet leather_chestplate leather_leggings leather_boots
iron_helmet iron_chestplate iron_leggings iron_boots
golden_helmet golden_chestplate golden_leggings golden_boots
diamond_helmet diamond_chestplate diamond_leggings diamond_boots
netherite_helmet netherite_chestplate netherite_leggings netherite_boots
coal charcoal raw_iron iron_ingot raw_gold gold_ingot raw_copper copper_ingot
diamond emerald lapis_lazuli redstone quartz netherite_ingot netherite_scrap ancient_debris
iron_block gold_block diamond_block emerald_block copper_block netherite_block
redstone_block lapis_block coal_block raw_iron_block raw_gold_block raw_copper_block
cobblestone stone deepslate granite diorite andesite calcite tuff obsidian
crying_obsidian glass glass_bottle bucket water_bucket lava_bucket milk_bucket
redstone_torch repeater comparator observer piston sticky_piston dispenser dropper
hopper lever tripwire_hook target lightning_rod daylight_detector note_block
rail powered_rail detector_rail activator_rail minecart chest_minecart hopper_minecart
furnace_minecart tnt_minecart tnt
anvil grindstone smithing_table stonecutter loom cartography_table fletching_table
blast_furnace smoker campfire soul_campfire cauldron composter beehive bee_nest
brewing_stand blaze_powder blaze_rod glass_bottle fermented_spider_eye
ghast_tear magma_cream nether_wart gunpowder glowstone_dust glowstone
enchanting_table bookshelf book experience_bottle name_tag
wheat bread carrot potato baked_potato beetroot melon_slice pumpkin sugar_cane
apple golden_apple enchanted_golden_apple golden_carrot cake cookie pumpkin_pie
beef cooked_beef porkchop cooked_porkchop chicken cooked_chicken mutton cooked_mutton
cod cooked_cod salmon cooked_salmon rabbit_stew mushroom_stew beetroot_soup
honey_bottle honeycomb sweet_berries glow_berries
bone bone_meal string spider_eye rotten_flesh leather feather egg slime_ball
ender_pearl ender_eye blaze_rod nether_star wither_skeleton_skull dragon_breath
dragon_egg elytra shulker_shell chorus_fruit popped_chorus_fruit end_crystal
netherrack soul_sand soul_soil basalt blackstone nether_bricks nether_gold_ore
crimson_stem warped_stem shroomlight
end_stone purpur_block end_rod
prismarine_shard prismarine_crystals nautilus_shell heart_of_the_sea conduit
sponge sea_lantern kelp dried_kelp turtle_helmet scute
saddle lead compass clock map spyglass recovery_compass echo_shard
totem_of_undying trident nether_star beacon
amethyst_shard amethyst_block spyglass
copper_ingot lightning_rod spyglass
""".split())


_VANILLA_ITEM_SET = frozenset(_VANILLA_ITEMS)

try:                                    # hand-checked per-mod opening chains
    from mod_progression import chain_for as _mp_chain
except Exception:                       # optional file, never fatal
    def _mp_chain(mod_id):
        return []


def _curated_chain(mod_id):
    """Hand-checked chain for a mod, from either source.

    mod_progression.py holds chains written in-repo; moddb/chains_research/
    holds researched ones where every id was verified against the mod's real
    jar. On conflict the LONGER chain wins: the in-repo file contains some old
    short stubs written from memory (ae2, mekanism, thermal at 5-7 steps,
    never jar-checked), and letting those shadow a 24-step verified chain
    silently discarded the better data.
    """
    a = _mp_chain(mod_id) or []
    b = globals().get("_RESEARCHED", {}).get(mod_id) or []
    return a if len(a) >= len(b) else b

# Empirical orderings mined from 45 popular CurseForge packs: for each mod, the
# items their authors put first, agreed by 3+ packs. Used for mods that have no
# hand-checked chain. Facts about ordering only - no quest text is copied.
def _load_mod_db() -> dict:
    """mod -> [item, ...] in the order published packs agree on.

    Two sources, newest first: moddb/moddb.json (rebuilt by moddb/harvest.py
    from Modrinth, re-runnable and provenance-tracked in moddb/packs.json) and
    the older harvested_order.json. Facts about ordering only; no authored text
    from anyone's pack is stored or reused.
    """
    out: dict = {}
    for path, shape in ((moddb_path() / "moddb.json", "rows"),
                        (RESOURCE_DIR / "harvested_order.json", "list")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for mod, entries in (raw or {}).items():
            if mod in out:
                continue
            if shape == "rows":
                out[mod] = [e["item"] for e in entries
                            if isinstance(e, dict) and e.get("item")]
            elif isinstance(entries, list):
                out[mod] = [e for e in entries if isinstance(e, str)]
    return {k: v for k, v in out.items() if v}


_HARVESTED_ORDER = _load_mod_db()


def _load_mod_db_rows() -> dict:
    """The consensus rows WITH their positional data - {mod: [{item,packs,pos}]}.
    _HARVESTED_ORDER flattens to bare ids; the positions are the ordering
    signal, so keep them."""
    try:
        raw = json.loads((moddb_path() / "moddb.json").read_text(encoding="utf-8"))
        return {k: v for k, v in (raw or {}).items() if isinstance(v, list)}
    except Exception:
        return {}


_HARVESTED_DB = _load_mod_db_rows()


def _load_researched_chains() -> dict:
    """Hand-researched chains from moddb/chains_research/*.json.

    Same standing as mod_progression.py: every id in these files was checked
    against the mod's actual jar before being written, which is the difference
    between a chain that works and one that quietly asks for an item that does
    not exist. Loaded as (id, title, desc) triples so both consumers of a
    curated chain treat them identically.
    """
    out: dict = {}
    folder = moddb_path() / "chains_research"
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.json")):
        if f.name.startswith("_"):
            continue            # _utility_audit.json and friends are not chains
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        mod = str(d.get("mod") or f.stem).strip()
        rows = []
        for e in (d.get("chain") or []):
            if not isinstance(e, dict):
                continue
            i = str(e.get("id") or "").strip()
            if not i or (":" not in i
                         and not i.startswith(("kill:", "dim:", "structure:"))):
                continue
            # Strip the researcher's MODULE FLAG note BEFORE clipping - the
            # note is a suffix, so stripping after the clip would miss any
            # desc where the cap already ate the end of it. The cap is 320,
            # not auto_description's 300: these descs are authored whole
            # sentences, and at 200 the clip was rewriting 287 of them (176
            # onto the old ellipsis path). At 320 only 2 corpus descs are
            # touched at all and the mean shipped length barely moves - the
            # cap is now a guard against a runaway paragraph, not an editor.
            rows.append((i, str(e.get("title") or "").strip()[:60],
                         _clip(_strip_module_flag(e.get("desc")), 320)))
        if mod and len(rows) >= 4:
            out[mod] = rows
    return out


_RESEARCHED = _load_researched_chains()


def _researched_text_index() -> dict:
    """item id -> (title, desc) over every researched row, desc-bearing only.

    The chains carry authored prose for thousands of ids, but until this index
    existed it was only readable by chain POSITION - applied to entries that
    landed inside curated[:n] when a chapter was built. An item that entered
    the book by any other path (progression, collections, theme picks) lost
    its text: twilightforest:fiery_ingot shipped BLANK while "The Hydra's
    fiery blood and tears, forged together with an iron ingot" sat unread in
    its chain file. Keyed by id, the text follows the item wherever it lands.
    "a|b" alternatives index under both ids; kill:/dim:/structure: rows have
    no item to key on.
    """
    out: dict = {}
    for rows in _RESEARCHED.values():
        for ref, t, d in rows:
            if not (d or "").strip():
                continue
            for cand in str(ref).split("|"):
                cand = cand.strip()
                if cand and not cand.startswith(("kill:", "dim:", "structure:")):
                    out.setdefault(cand, (t, d))
    return out


_RESEARCHED_TEXT = _researched_text_index()


def _load_researched_collections() -> dict:
    """mod -> [(hub, [members...])] from the research files.

    A hand-picked wheel beats a token-derived one: a researcher knows the
    sixteen runes are a set and that eight kinds of cobblestone are not.
    """
    out: dict = {}
    folder = moddb_path() / "chains_research"
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.json")):
        if f.name.startswith("_"):
            continue            # audits and indexes, not chain files
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        mod = str(d.get("mod") or f.stem).strip()
        wheels = []
        for w in (d.get("collections") or []):
            if not isinstance(w, dict):
                continue
            hub = str(w.get("hub") or "").strip()
            mem = [str(x).strip() for x in (w.get("members") or [])
                   if isinstance(x, str) and ":" in x]
            if hub and len(mem) >= 4:
                wheels.append((hub, mem))
        if wheels:
            out[mod] = wheels
    return out


_RESEARCHED_WHEELS = _load_researched_collections()


def _all_pinned_ids() -> set:
    """Every id a verified source names - curated chains and pack consensus.
    These were checked against real jars, so nothing downstream should ever
    treat them as junk or fake."""
    out = set()
    for rows in _RESEARCHED.values():
        for ref, _t, _d in rows:
            for cand in str(ref).split("|"):
                cand = cand.strip()
                if cand and not cand.startswith(("kill:", "dim:", "structure:")):
                    out.add(cand)
    for wheels in _RESEARCHED_WHEELS.values():
        for hub, mem in wheels:
            out.add(hub)
            out.update(mem)
    return out


_PINNED_IDS = _all_pinned_ids()


def _load_style_guide() -> dict:
    """Original title/description patterns, written for this project.
    Advisory only - it shapes the AI prompt, never copies anyone's text."""
    try:
        return json.loads(
            (moddb_path() / "style_guide.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


_STYLE = _load_style_guide()


# ========================================================================== #
#  Questline import - copy chapters 1:1 from another modpack
# ========================================================================== #

def _chapter_main_mod(doc: dict) -> str:
    """Which mod a chapter is about, by its task items' namespaces."""
    ns = collections.Counter()
    for q in doc.get("quests") or []:
        for t in (q.get("tasks") or []):
            it = t.get("item")
            if isinstance(it, dict):
                it = it.get("id") or it.get("item")
            if isinstance(it, str) and ":" in it:
                ns[it.split(":")[0]] += 1
    if not ns:
        return ""
    mod, _ = ns.most_common(1)[0]
    if mod == "minecraft" and len(ns) > 1:
        mod = ns.most_common(2)[1][0]
    return mod


def find_pack_chapters(source: str, log) -> list:
    """Chapters inside a modpack, for the questline-import feature.

    `source` is a local instance folder, a local .zip/.mrpack, or - when
    neither exists on disk - a Modrinth search term ("All the Mods 10"),
    resolved through the same open API the harvester uses. Returns
    [{"title","mod","nquests","doc","pack"}] with the raw chapter documents
    untouched, so an install can be a true 1:1 copy.
    """
    import urllib.request, urllib.parse, io
    docs = []

    def from_zip(zf, pack_name):
        for n in zf.namelist():
            if "/chapters/" not in n.replace("\\", "/") or not n.endswith(".snbt"):
                continue
            try:
                d = snbt_loads(zf.read(n).decode("utf-8", "replace"))
            except Exception:
                continue
            if isinstance(d, dict) and d.get("quests"):
                docs.append((pack_name, d))

    src = Path(str(source))
    if src.is_dir():
        for f in list(src.glob("**/chapters/*.snbt"))[:400]:
            try:
                d = snbt_loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(d, dict) and d.get("quests"):
                docs.append((src.name, d))
    elif src.is_file() and src.suffix.lower() in (".zip", ".mrpack", ".jar"):
        with zipfile.ZipFile(src) as zf:
            from_zip(zf, src.stem)
    else:
        UA = {"User-Agent": "AutoQuestGen (questline import)"}

        def api(u):
            req = urllib.request.Request(u, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        q = urllib.parse.quote(str(source))
        facets = urllib.parse.quote('[["project_type:modpack"]]')
        res = api("https://api.modrinth.com/v2/search?query=%s&facets=%s&limit=5"
                  % (q, facets))
        hits = (res or {}).get("hits") or []
        if not hits:
            log("no modpack found on Modrinth for '%s'" % source)
            return []
        proj = hits[0]
        log("found pack: %s (%s downloads)" % (proj.get("title"), proj.get("downloads")))
        vers = api("https://api.modrinth.com/v2/project/%s/version" % proj["project_id"])
        files = [f for v in vers[:3] for f in (v.get("files") or []) if f.get("url")]
        if not files:
            log("pack has no downloadable files")
            return []
        log("downloading %s (%.0f MB)..." % (files[0]["filename"],
                                             (files[0].get("size") or 0) / 1e6))
        raw = urllib.request.urlopen(
            urllib.request.Request(files[0]["url"], headers=UA), timeout=300).read()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            from_zip(zf, str(proj.get("title") or source))

    out = []
    for pack, d in docs:
        title = re.sub(r"&[0-9a-fk-orA-FK-OR]", "", str(d.get("title") or "?")).strip()
        out.append({"title": title or "?", "mod": _chapter_main_mod(d),
                    "nquests": len(d.get("quests") or []), "doc": d, "pack": pack})
    out.sort(key=lambda r: (-r["nquests"], r["title"]))
    log("found %d chapter(s)" % len(out))
    return out


def remix_chapter(doc: dict, scan: dict, rng, log) -> dict:
    """Keep an imported chapter's skeleton, regenerate everything authored.

    Kept: which items/mobs/dimensions the quests target, and the dependency
    graph between them - facts about how the mod is played, the same line the
    harvest database draws. Replaced with this app's own output: every title,
    subtitle and description, the layout, shapes and sizes, and all rewards.

    A remix is honest about what it is: the creative text is genuinely new
    (drawn from the mod's own advancement/guide text and this app's writers,
    not the source pack's prose), but the PROGRESSION DESIGN still came from
    the source author. Fine for a personal book; for something published, the
    generator's own output is the clean path.
    """
    mid = _chapter_main_mod(doc)
    craft = set((scan.get("craftable") or {}).get(mid) or ())
    blurbs = scan.get("blurbs") or {}
    rpool = reward_pool(scan)
    _tm = scan.get("tier") or {}
    rpool.sort(key=lambda x: (0 if x.startswith("minecraft:") else 1,
                              _tm.get(x, 5), x))

    quests = doc.get("quests") or []
    n = max(1, len(quests))
    for i, q in enumerate(quests):
        # strip every piece of authored expression
        for k in ("title", "subtitle", "description", "text", "icon",
                  "shape", "size", "x", "y"):
            q.pop(k, None)
        t0 = (q.get("tasks") or [{}])[0]
        it = t0.get("item")
        cnt = _int_of(t0.get("count", 1), 1)
        if isinstance(it, str):
            q["title"] = _item_title(it, cnt, rng)
            d = _desc("item", _pretty_name(it), cnt, mid or "this mod", rng,
                      it, blurbs, scan)
            if d:
                q["description"] = [d]
        elif t0.get("type") == "kill" and isinstance(t0.get("entity"), str):
            q["title"] = "%s %s" % (rng.choice(_KILL_VERBS),
                                    _pretty_name(t0["entity"]))
        elif t0.get("type") == "dimension":
            q["title"] = "Journey to %s" % _pretty_name(str(t0.get("dimension", "?")))
        elif t0.get("type") == "advancement":
            adv = str(t0.get("advancement", "")).split("/")[-1].split(":")[-1]
            q["title"] = _pretty_name(adv) if adv else "Achievement"
        elif t0.get("type") == "checkmark":
            q["title"] = "Getting Started" if i == 0 else                 rng.choice(("Check In", "Milestone", "Take Stock", "Waypoint"))
        else:
            q["title"] = "Quest %d" % (i + 1)
        # rewards: this app's pool, scaled to how deep in the chapter we are
        hi = max(6, int(len(rpool) * min(1.0, 0.3 + i / n)))
        lo = max(0, hi - max(8, len(rpool) // 3))
        rews = []
        if rpool and rng.random() < 0.6:
            src = rpool[lo + rng.randrange(max(1, hi - lo))]
            rews.append({"id": ftb_id("remix", q["id"], "r0"), "type": "item",
                         "item": src, "count": rng.choice([1, 1, 2, 4])})
        if not rews or rng.random() < 0.4:
            rews.append({"id": ftb_id("remix", q["id"], "rxp"), "type": "xp",
                         "xp": 25 + 12 * i})
        q["rewards"] = rews
        q["shape"] = _quest_shape(q, "circle")
    _size_hierarchy(quests)

    # fresh layout from the dependency graph, never the source pack's map
    pos = layout_positions(quests, rng.choice(["tree", "spine", "clusters"]),
                           0.25, rng.random())
    if pos:
        for q in quests:
            if q["id"] in pos:
                q["x"] = _Double(round(pos[q["id"]][0], 3))
                q["y"] = _Double(round(pos[q["id"]][1], 3))
    disp = next((m["name"] for m in (scan.get("mods") or [])
                 if m.get("mod_id") == mid), None)
    doc["title"] = _txt(disp or _pretty_name(mid or "Remixed Chapter"))
    doc.pop("subtitle", None)
    doc.pop("images", None)
    if quests:
        it0 = (quests[0].get("tasks") or [{}])[0].get("item")
        doc["icon"] = it0 if isinstance(it0, str) else "minecraft:book"
    log("  remixed '%s': kept %d-quest skeleton, regenerated all text, "
        "layout and rewards" % (doc.get("title"), len(quests)))
    return doc


def _group_for_imported(pick: dict, scan: dict) -> str:
    """Which of the book's normal groups an imported chapter belongs in.

    Dumping every import into one "Imported" bin fights the book's own
    organisation; a Botania chapter belongs with Magic wherever it came from.
    """
    mid = pick.get("mod") or ""
    cat = next((m.get("category") for m in (scan.get("mods") or [])
                if m.get("mod_id") == mid), None)
    return _GROUP_FOR_CAT.get(cat or "", "Expansion")


def install_imported_chapters(quests_dir: Path, picks: list, scan: dict, log,
                              group_title: str = "Imported",
                              remix: bool = False,
                              merge_groups: bool = False) -> int:
    """Copy the picked chapters into the user's book, 1:1.

    Everything an author placed - titles, descriptions, positions, shapes,
    rewards - is kept as written; that is the point of the feature. What has
    to change: every id is re-minted (imports must not collide with the
    existing book, and FTB rejects ids with the sign bit set), internal
    dependencies are remapped to the new ids, and tasks whose items do not
    exist in THIS pack are dropped - a copied quest for a mod the user lacks
    can never be completed. Reward-table references are stripped for the same
    reason: the tables live in the source pack.

    Imports are for the user's own book. Redistributing a pack containing
    someone else's copied questline needs that author's permission.
    """
    if minecraft_running():
        raise RuntimeError("Minecraft is RUNNING - close it before importing, "
                           "or FTB Quests will overwrite the import.")
    known = set(scan.get("vanilla") or _VANILLA_ITEM_SET)
    for v in (scan.get("items") or {}).values():
        known |= v
    ents = set()
    for v in (scan.get("entities") or {}).values():
        ents |= set(v)

    quests_dir.mkdir(parents=True, exist_ok=True)
    (quests_dir / "chapters").mkdir(exist_ok=True)
    used_slugs = {f.stem for f in (quests_dir / "chapters").glob("*.snbt")}

    gfile = quests_dir / "chapter_groups.snbt"
    groups = []
    if gfile.exists():
        try:
            groups = (snbt_loads(gfile.read_text(encoding="utf-8"))
                      or {}).get("chapter_groups") or []
        except Exception:
            groups = []
    plain = re.sub(r"&[0-9a-fk-orA-FK-OR]", "", group_title).strip() or "Imported"
    gid = None
    if merge_groups:
        gid = "__PER_CHAPTER__"          # resolved per pick below
    for g in groups:
        if re.sub(r"&[0-9a-fk-orA-FK-OR]", "",
                  str(g.get("title") or "")).strip() == plain:
            gid = g["id"]
            break
    if gid is None:
        gid = ftb_id("group/" + plain)
        groups.append({"id": gid, "title": "&6&l" + _txt(plain)})
        gfile.write_text(snbt_dumps({"chapter_groups": groups}) + "\n",
                         encoding="utf-8")
        log("  added chapter group '%s'" % plain)

    def _ensure_group(name):
        """Group id for `name`, creating the group if the book lacks it."""
        pl = re.sub(r"&[0-9a-fk-orA-FK-OR]", "", name).strip() or "Imported"
        for g in groups:
            if re.sub(r"&[0-9a-fk-orA-FK-OR]", "",
                      str(g.get("title") or "")).strip() == pl:
                return g["id"]
        ngid = ftb_id("group/" + pl)
        code = _theme_for(pl, len(groups))[0]
        groups.append({"id": ngid, "title": "&%s&l%s" % (code, _txt(pl))})
        gfile.write_text(snbt_dumps({"chapter_groups": groups}) + "\n",
                         encoding="utf-8")
        log("  added chapter group '%s'" % pl)
        return ngid

    installed = 0
    for pick in picks:
        d = json.loads(json.dumps(pick["doc"]))      # deep copy, never mutate source
        old_new = {}
        for q in d.get("quests") or []:
            oid = str(q.get("id"))
            old_new[oid] = ftb_id("import", pick["pack"], pick["title"], oid)
        kept_q = []
        dropped_tasks = dropped_quests = 0
        for q in d.get("quests") or []:
            q["id"] = old_new[str(q["id"])]
            q["dependencies"] = [old_new[str(x)]
                                 for x in (q.get("dependencies") or [])
                                 if str(x) in old_new]
            tasks = []
            for t in q.get("tasks") or []:
                t["id"] = ftb_id("import", q["id"], "t%d" % len(tasks))
                it = t.get("item")
                if isinstance(it, dict):
                    it = it.get("id") or it.get("item")
                    if isinstance(it, str):
                        t["item"] = it.split("{")[0]
                if isinstance(t.get("item"), str) \
                        and t["item"].split("{")[0] not in known:
                    dropped_tasks += 1
                    continue
                if t.get("type") == "kill" and str(t.get("entity")) not in ents \
                        and not str(t.get("entity", "")).startswith("minecraft:"):
                    dropped_tasks += 1
                    continue
                tasks.append(t)
            q["tasks"] = tasks
            rewards = []
            for rw in q.get("rewards") or []:
                if "table_id" in rw or rw.get("type") in ("random", "loot", "choice"):
                    continue                     # tables live in the source pack
                it = rw.get("item")
                if isinstance(it, dict):
                    it = it.get("id") or it.get("item")
                    if isinstance(it, str):
                        rw["item"] = it.split("{")[0]
                if isinstance(rw.get("item"), str) \
                        and rw["item"].split("{")[0] not in known:
                    continue
                rw["id"] = ftb_id("import", q["id"], "r%d" % len(rewards))
                rewards.append(rw)
            q["rewards"] = rewards or [{"id": ftb_id("import", q["id"], "rxp"),
                                        "type": "xp", "xp": 50}]
            if tasks:
                kept_q.append(q)
            else:
                dropped_quests += 1
        alive = {q["id"] for q in kept_q}
        for q in kept_q:
            q["dependencies"] = [x for x in q["dependencies"] if x in alive]
        if remix and kept_q:
            d["quests"] = kept_q
            d = remix_chapter(d, scan, random.Random(d.get("id") or pick["title"]),
                              log)
            kept_q = d["quests"]
        if not kept_q:
            log("  ! '%s': nothing importable (no task exists in this pack)"
                % pick["title"])
            continue
        d["quests"] = kept_q
        d["id"] = ftb_id("import/ch", pick["pack"], pick["title"])
        d["group"] = (_ensure_group(_group_for_imported(pick, scan))
                      if merge_groups else gid)
        if isinstance(d.get("icon"), str) and d["icon"].split("{")[0] not in known:
            d["icon"] = "minecraft:book"
        d.pop("images", None)                    # backgrounds reference source-pack art
        slug = slugify("imported_" + pick["title"]) or "imported"
        while slug in used_slugs:
            slug += "_"
        used_slugs.add(slug)
        d["filename"] = slug
        (quests_dir / "chapters" / (slug + ".snbt")).write_text(
            snbt_dumps(d) + "\n", encoding="utf-8")
        log("  imported '%s' (%d quests kept, %d task(s) and %d quest(s) "
            "dropped - items not in this pack)"
            % (pick["title"], len(kept_q), dropped_tasks, dropped_quests))
        installed += 1
    return installed


def _load_gating() -> dict:
    """(earlier_item, later_item) pairs that published packs agree on.

    A pack's quest graph says "you cannot do B until A" - real knowledge about
    how a mod works, learned from people who played it. Recipes alone miss
    this: nothing in Twilight Forest's recipe data says the Naga must die
    before the Lich Tower opens. Only pairs seen in 2+ independent chapters are
    kept, so one author's idiosyncratic layout cannot distort the order.
    """
    try:
        raw = json.loads((moddb_path() / "gating.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict = {}
    for mod, pairs in (raw or {}).items():
        seen: dict = {}
        for pr in pairs:
            if isinstance(pr, list) and len(pr) == 2:
                seen[(pr[0], pr[1])] = seen.get((pr[0], pr[1]), 0) + 1
        agreed = {k for k, n in seen.items() if n >= 2}
        # drop any pair whose reverse is also attested - that is disagreement,
        # not knowledge
        agreed = {(a, b) for (a, b) in agreed if (b, a) not in agreed}
        if agreed:
            out[mod] = agreed
    return out


_GATING = _load_gating()


# words that stay lowercase inside a title, the way real pack books write them
_TITLE_MINOR = {"of", "the", "and", "in", "on", "a", "an", "to", "for", "with"}


def _pretty_name(item_id: str) -> str:
    """Title Case, as an item's in-game name is written. Sentence case read as
    a typo in the book: "Retinal scanner", "Mana pool", "Naga scale"."""
    s = item_id.split(":")[-1].replace("_", " ").strip()
    if not s:
        return item_id
    words = s.split()
    out = []
    for i, w in enumerate(words):
        if i and w in _TITLE_MINOR:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _rank_mod_items(ids: list, drop_junk: bool = True) -> list:
    """Best quest goals first. With drop_junk, decoration/shape variants are
    removed entirely rather than just sorted to the back."""
    scored = [(_junk_score(i), len(i.split(":")[-1]), i) for i in ids]
    if drop_junk:
        keep = [s for s in scored if s[0] < 3]
        if len(keep) >= 4:          # only enforce if the mod has enough left
            scored = keep
    return [i for _, _, i in sorted(scored)]


# Things you build/wield ONE of. Asking for three altars reads as filler;
# shared by _count_for and bulk_ask_pass so the two can never disagree.
_ONE_OF_A_KIND = ("machine", "engine", "controller", "altar", "pedestal",
                  "helmet", "chestplate", "leggings", "boots", "sword",
                  "pickaxe", "axe", "staff", "wand", "tome", "bow")


def _count_for(item_id: str, rng) -> int:
    # Measured across 2455 real tasks: 91.3% ask for exactly ONE. Varied stack
    # sizes felt "generated"; asking for 1 is what published packs do.
    if rng.random() < 0.88:
        return 1
    p = item_id.split(":")[-1]
    if any(b in p for b in _BULK):
        return rng.choice([8, 12, 16, 16, 24, 32, 64])
    if any(g in p for g in _ONE_OF_A_KIND):
        return 1
    return rng.choice([1, 1, 2, 4, 8])


def bulk_ask_pass(chapters: list, scan: dict) -> None:
    """Guarantee each big chapter one real bulk ask (immersion_spec CHK-14).

    Authored packs put a "stock up" quest in every substantial chapter -
    measured share of >1-count item tasks is 0.04-0.12, and NO authored
    chapter of >= 15 item tasks has zero of them. This generator leaves counts
    to _count_for, which is 88% "ask for one" by design - and the curated
    chain / harvested-order / progression paths that fill most mod chapters
    hard-code count 1 besides. Measured on the built the reference pack book
    (2026-08-29): every one of the 22 mod chapters had ZERO bulk asks; the
    only bulk in the book was the vanilla specs' authored counts, and the
    17-item-task Create chapter tripped the check.

    A floor, not a rewrite: chapters keep their counts, and only a chapter
    with >= 12 item tasks (margin under the spec's 15 so a task dropped in
    cleaning cannot re-expose the failure) and fewer than ~one-in-fourteen
    bulk asks gets topped up. One per fourteen stays inside the 0.04-0.12
    band even on a book where every chapter qualifies. Candidates are picked
    the way the vanilla specs do it: stackable materials (_BULK fragments)
    first, never tools/machines (_ONE_OF_A_KIND), never the opener or the
    finale, and never a quest whose prose already says "first" - a count of
    16 under "Your first X" would contradict the sentence above it. A bare
    item-name title is restated through _item_title so it can say "16x X" /
    "Gather Xs"; a curated title is left alone (the lich_trophy lesson)."""
    for ch in chapters:
        qs = ch.get("quests") or []
        rows = []                       # (quest_index, item_task)
        for i, q in enumerate(qs):
            for t in (q.get("tasks") or []):
                if (t.get("type") or "item") == "item" and t.get("item"):
                    rows.append((i, t))
        if len(rows) < 12:
            continue
        have = sum(1 for _i, t in rows if _int_of(t.get("count", 1), 1) > 1)
        want = max(1, len(rows) // 14)
        if have >= want:
            continue
        rng = random.Random("bulkask/%s" % ch.get("id"))
        cands = []
        for i, t in rows:
            if i == 0 or i == len(qs) - 1:
                continue                # opener orients, finale is a trophy
            if _int_of(t.get("count", 1), 1) > 1:
                continue
            p = str(t["item"]).split(":")[-1]
            if any(g in p for g in _ONE_OF_A_KIND):
                continue
            d = qs[i].get("description")
            prose = " ".join(d) if isinstance(d, list) else str(d or "")
            if "first" in prose.lower():
                continue
            stackable = any(b in p for b in _BULK)
            bare = qs[i].get("title") == _pretty_name(t["item"])
            cands.append((0 if stackable else 1, 0 if bare else 1, i, t))
        cands.sort()
        for _s, _b, i, t in cands[:want - have]:
            c = rng.choice([8, 12, 16]) if _s == 0 else 4
            t["count"] = c
            if qs[i].get("title") == _pretty_name(t["item"]):
                qs[i]["title"] = _item_title(t["item"], c, rng)


_OBTAIN_VERBS = ("Obtain", "Acquire", "Craft", "Secure", "Stockpile", "Track down", "Bring home")
_KILL_VERBS = ("Hunt", "Cull", "Clear out", "Face down", "Slay")
_INTRO_TITLES = ("First Steps: %s", "Welcome to %s", "%s: Day One", "Opening %s", "Meet %s")
# entity-id fragments that are NOT killable mobs (projectiles, multiparts, vehicles…)
_NOT_A_MOB = ("_part", "_projectile", "_body", "_head", "_tail", "_neck", "_egg",
              "_effect", "_block", "_bomb", "_bullet", "_blast", "_beam", "_laser",
              "_orb", "_portal", "_mine", "_stele", "_echo", "_strike", "_shot",
              "boat", "minecart", "contraption", "seat", "falling_", "_spawn",
              "arrow", "spear", "bardiche", "blueprint", "glue", "package",
              "charm", "_aoe", "area_effect", "fishing", "_hook", "_fx", "dummy",
              "marker", "display", "_clone", "_illusion", "_decoy", "chain_block",
              # Proved by a QA pass that read class headers rather than names:
              # shotgun_pellet extends AbstractArrow, will_o_wisp and antidote
              # extend ThrowableItemProjectile, dark_potion extends
              # ThrownPotion, boulder reaches Entity through EntityMagicEffect.
              # A kill task on any of them can never fire - FTB Quests listens
              # for LivingDeathEvent and none of these is a LivingEntity.
              "_pellet", "wisp", "antidote", "_potion", "boulder", "_shard",
              "_missile", "_bolt", "_dart", "_ball", "_wave", "_totem")
# Items that exist only to be a picture. alexsmobs:tab_icon became the quest
# "Perfect View in the Plains"; biomesoplenty:bop_icon the same; and
# ftbquests:missing_item is the placeholder FTB Quests draws when an id cannot
# be resolved - a quest asking for the "this item is broken" icon.
_NOT_A_GOAL = ("tab_icon", "_icon", "icon_", "missing_item", "placeholder",
               "creative_tab", "_tab")
_BOSS_MARK = ("boss", "_king", "_queen", "titan", "_lord", "warden", "dragon",
              "leviathan", "colossus", "guardian", "elder", "_god", "harbinger",
              "naga", "lich", "hydra", "ur_ghast", "phantom_knight", "alpha_",
              "kraken", "behemoth", "ancient_remnant")


def _mob_entities(entity_ids, item_ids: set) -> list:
    """Keep only ids that plausibly name a killable creature; spawn-egg mobs first."""
    egg, plain = [], []
    for e in sorted(entity_ids):
        short = e.split(":")[-1]
        if any(k in short for k in _NOT_A_MOB):
            continue
        (egg if ("%s_spawn_egg" % e) in item_ids else plain).append(e)
    return egg + plain


# What each recipe type tells the player to DO. A description that names
# ingredients is an instruction, and an instruction with the wrong verb sends
# the player to the wrong block: four of the shipped "Crafted from" lines were
# a smithing table and a cutting board, and a player who puts those items in a
# grid gets nothing. Every type here is one whose station the sentence can
# state; anything absent - create:mixing, goety:ritual, botania:mana_infusion,
# refurbished_furniture:oven_baking - abstains, because we would be naming
# ingredients without being able to say where they go. Blank beats a wrong
# instruction.
# Second person on purpose, and never "<participle> from": "Crafted from" /
# "Smelted from" are the crafted/made-from generator-tail skeleton immersion
# CHK-11 hard-fails on (36 of 89 measured tail hits, 2026-08-29), and "you"
# is the register the authored corpus writes in (CHK-20). The verb still
# names the station's action, so the wrong-block guarantee is unchanged.
_RECIPE_VERB = {
    "minecraft:crafting_shaped":    "You craft this with",
    "minecraft:crafting_shapeless": "You craft this with",
    "minecraft:smelting":           "You smelt this from",
    "minecraft:blasting":           "You smelt this from",
    "minecraft:smoking":            "You cook this from",
    "minecraft:campfire_cooking":   "You cook this from",
    "minecraft:stonecutting":       "You cut this from",
    "minecraft:smithing_transform": "You smith this from",
}


def _one_recipe(item: str, scan: dict):
    """The single recipe a description should describe -> (verb, [names]).

    A crafting-table route wins when there is one, because it is the route a
    player can follow with no other block; otherwise the shortest describable
    recipe, since the shortest is the cheapest to explain and usually to make.

    Two things are refused rather than papered over. A tag ingredient is a
    CHOICE the scan discards into `alts`, so listing only the concrete slots
    would describe a recipe with a hole in it. And more than four ingredients
    cannot be listed without truncating - the old line took the first two of
    refurbished_furniture:raw_vegetable_pizza's five and read "Crafted from
    Beetroot and Carrot", which is not the recipe.
    """
    recs = (scan or {}).get("craft_recipes")
    if not recs:
        return None                     # a scan from before this map existed
    disp = (scan or {}).get("names") or {}
    best = None
    for ty, ings in (recs.get(item) or ()):
        verb = _RECIPE_VERB.get(ty)
        if not verb:
            continue
        ings = [g for g in ings if g != item]
        if any(g.startswith("#") for g in ings) or not (1 <= len(ings) <= 4):
            continue
        # The mod's own display name, so the sentence spells the ingredient
        # the way the player will read it in the recipe book.
        names = sorted({disp.get(g) or _pretty_name(g) for g in ings})
        rank = (0 if verb == _RECIPE_VERB["minecraft:crafting_shaped"] else 1,
                len(names))
        if best is None or rank < best[0]:
            best = (rank, verb, names)
    return None if best is None else (best[1], best[2])


def _recipe_line(verb: str, names) -> str:
    """The ONE shape a recipe sentence ships in, ingredients in DESC_KEY.

    Both emitters of this sentence must call this. _recipe_desc and
    fill_blank_descriptions were built independently and disagreed on the
    look: the same "You craft this with X and Y." shipped with the
    ingredients highlighted from one path and plain from the other, and
    Abyssal Decor showed both styles in one chapter (measured: 3-4 of ~20
    recipe-verb lines plain depending on hash seed, all from the fill pass). The player
    cannot tell the paths apart, so the sentence must not either.
    No DESC_BODY prefix here - callers add it, because the fill pass has
    to run its uniqueness count on the UNFORMATTED text first (colour
    codes in the counted string would let two identical sentences stop
    colliding and silently weaken the <=2 guard)."""
    return "%s %s%s." % (verb, DESC_KEY, _and_list(names) + DESC_BODY)


def _recipe_desc(item: str, scan: dict):
    """"Crafted from andesite alloy and a cogwheel." Read straight from ONE
    recipe, so it is true for any mod without curation."""
    got = _one_recipe(item, scan)
    if got:
        verb, names = got
        return DESC_BODY + _recipe_line(verb, names)
    if (scan or {}).get("craft_recipes"):
        return None
    # Older pickled scans carry no per-recipe map; keep what they used to do.
    edges = (scan or {}).get("edges") or {}
    ings = [g for g in (edges.get(item) or ()) if g != item]
    if not (2 <= len(ings) <= 4):
        return None
    names = sorted({_pretty_name(g) for g in ings})
    if len(names) < 2 or len(names) > 4:
        return None
    # same CHK-11/CHK-20 reframe as _RECIPE_VERB above
    return DESC_BODY + _recipe_line("You craft this with", names)


def _desc(kind: str, name: str, count: int, mod: str, rng,
          item: str = "", blurbs: dict = None, _scan_ref: dict = None):
    """A short grey (&7) line, or None.

    An authored line from the mod's own advancement tree always wins: "Craft a
    Blood Sprayer from the drops of a Crimson Mosquito" tells the player how to
    get the thing, which no template can. Templates stay as the fallback and
    are still used sparingly, the way real packs do.
    """
    real = (blurbs or {}).get(item)
    if real:
        return DESC_BODY + _blurb_sentence(real)
    # Second best after the mod's own words: what the recipe actually needs.
    # Factual, useful, and available for anything craftable - far better than
    # padding coverage with "small step, big payoff later".
    if _scan_ref and rng.random() < 0.55:
        rd = _recipe_desc(item, _scan_ref)
        if rd:
            return rd
    if rng.random() < 0.85:
        return None
    nm = name
    if kind == "item":
        opts = [
            "%s is bread-and-butter for %s." % (nm, mod),
            "Stock up - you'll burn through these.",
            "%s unlocks the next step here." % nm,
            "Worth making early.",
            "Keep a few spare.",
        ] if count > 1 else [
            "Your first %s. More to come." % nm,
            "%s opens up the rest of %s." % (nm, mod),
            "Small step, big payoff later.",
            "You'll want this before going further.",
        ]
        return "&7" + rng.choice(opts)
    if kind == "kill":
        return "&7" + rng.choice([
            "Thin out the local %s." % nm.lower(),
            "%s are trouble." % nm,
            "Good practice before the real fights.",
        ])
    return None


def _mob_drop_desc(entity: str, scan: dict):
    """"Foliaath drops Foliaath Seed." — read from the mob's own loot table.

    A kill quest for an unresearched mod had literally nothing true to say:
    _desc's three canned lines are about the PLAYER's mood, not the mob, so 4
    of 5 generic kill quests shipped blank and the fifth said "Thin out the
    local cubera". Meanwhile the jar answers the mined complaint directly
    ("I don't know if I need a bunch of these, I'm just doing it for the
    quest") — the mob's loot table says exactly what killing it is worth.
    scan["drops"] already keeps entity provenance per ITEM; this is the same
    data read the other way round, so no new scan field is needed and old
    pickled scans simply abstain.

    Three refusals keep the sentence true. Only ids the scan knows as items:
    a naive id sweep over raw loot JSON also catches predicate ids
    (mowziesmobs:moon_phase, minecraft:inverted), and this is the filter that
    strips them. Only MOD-namespaced drops: "drops Rotten Flesh" is true of
    half the mobs in any pack and says nothing about this one. And never the
    mob's own id: block-shaped mobs drop themselves, and "Blobfish drops
    Blobfish" is the lightbulb problem again. If nothing survives, abstain —
    blank beats filler. (Measured on this pack: 263 of 380 loot-table mobs
    keep a line, every line distinct because the mob's own name is in it.)
    """
    drops = (scan or {}).get("drops") or {}
    if not drops or ":" not in entity:
        return None
    _, path = entity.split(":", 1)
    by_mod = scan.get("items") or {}
    disp = scan.get("names") or {}
    got = set()
    for item, srcs in drops.items():
        for src in (srcs.get("entities") or ()):
            # tables can sit deeper than entities/<mob>.json (sheep/white)
            if src == entity or src.startswith(entity + "/"):
                break
        else:
            continue
        ins, _, ipath = item.partition(":")
        if ins == "minecraft" or ipath == path:
            continue
        if item not in (by_mod.get(ins) or ()) and item not in disp:
            continue                    # predicate id, not an item
        got.add(item)
    if not got:
        return None
    names = [disp.get(i) or _pretty_name(i) for i in sorted(got)[:3]]
    return "%s%s drops %s%s." % (DESC_BODY, _pretty_name(entity), DESC_KEY,
                                 _and_list(names) + DESC_BODY)


def _entity_drop_map(scan: dict) -> dict:
    """entity id -> set of item ids its own loot table drops.

    scan["drops"] is keyed by ITEM; this is the same inversion _mob_drop_desc
    performs, done once so a whole mob list can be ranked by it. Same item
    filter too: a modded id must be one the scan knows as an item (a raw loot
    sweep also catches predicate ids like mowziesmobs:moon_phase), and a
    minecraft: id must be on the scan's vanilla list when there is one. Old
    pickled scans have no drops map and get an empty dict — every caller
    treats that as "no signal", never as "not killable".
    """
    drops = (scan or {}).get("drops") or {}
    by_mod = scan.get("items") or {}
    disp = scan.get("names") or {}
    van = scan.get("vanilla") or ()
    out: dict = {}
    for item, srcs in drops.items():
        ins = item.split(":", 1)[0]
        if ins == "minecraft":
            if van and item not in van:
                continue
        elif item not in (by_mod.get(ins) or ()) and item not in disp:
            continue
        for src in (srcs.get("entities") or ()):
            # tables can sit deeper than entities/<mob>.json (sheep/white)
            out.setdefault(src.split("/")[0], set()).add(item)
    return out


def _boss_by_drops(entity: str, drop_ids) -> bool:
    """Bosshood read from the mob's own loot table, not its name.

    _BOSS_MARK matches names, and names lie in both directions: it calls
    twilightforest:hydra_mortar (a projectile) and illageandspillage:
    boss_randomizer (a spawner utility) bosses, and misses cataclysm's ignis,
    maledictus, scylla and netherite_monstrosity, goety's apostle and vizier,
    and twilightforest's minoshroom — 8 real bosses in this pack. What those
    8 share is the mod author's own signature: a boss drops its personal
    music disc (music_disc_ignis from ignis) or its mounted-head trophy
    (minoshroom_trophy from minoshroom). No projectile has a loot table at
    all, so the test cannot produce the mortar-as-finale failure.
    """
    path = entity.split(":")[-1]
    for d in drop_ids:
        dp = d.split(":")[-1]
        if dp.endswith("_trophy") or ("music_disc" in dp and path in dp):
            return True
    return False


def _plural(name: str) -> str:
    """Pluralise the last word of an item name for bulk quests."""
    parts = name.rsplit(" ", 1)
    w = parts[-1]
    low = w.lower()
    # Mass nouns and already-plural names take no suffix. This test must come
    # FIRST: "Glass" ends in "s", so the -es rule turned it into "Glasses",
    # and "Lamp of Cinders" became "Cinderses".
    if low.endswith(("gear", "sand", "dust", "wood", "stone", "ore", "glass",
                     "leather", "wool", "ice", "flesh", "water", "milk",
                     "steel", "iron", "gold", "s")):
        pass
    elif low.endswith(("x", "z", "ch", "sh")):
        w += "es"
    elif low.endswith("y") and len(w) > 1 and w[-2].lower() not in "aeiou":
        w = w[:-1] + "ies"
    else:
        w += "s"
    return (parts[0] + " " + w) if len(parts) > 1 else w


def _item_title(item_id: str, count: int, rng) -> str:
    """Real packs vary: bare item name, 'Nx X', 'Gather Xs'.

    What they do NOT write is "Craft X" / "Obtain X" as a title.
    moddb/style_guide.json bans that form outright - the task icon sitting
    under the title already shows the item AND the verb, so the words are
    spent saying nothing. It was 15.0% of this book's titles, and it supplied
    four of the five largest first-word clusters (Make, Assemble, Build,
    Craft): a player scanning the quest map read the same word over and over
    instead of the thing each quest was about. The guide's own advice for the
    required crafting steps players Ctrl-F for is the bare item name.

    Dropping the verb also settles the verb-choice problem rather than
    managing it. The verb used to be chosen from a craftability flag, and a
    caller without craftability data guessed - that is how a boss drop got
    titled "Make Lich Trophy", with the player complaint "it says FIND, it
    doesn't say SLAY" pointing the same way. A bare name cannot make that
    mistake, and it does not need an answer to a question the scan cannot
    always answer. (Kill quests keep their verbs: _KILL_VERBS is chosen off
    the task type, so it agrees with the task by construction.)

    Bulk verbs survive, because the count is real data and the plural carries
    them away from the banned form - "Gather Iron Ingots" is not the item's
    name. A mass noun that does not pluralise ("Redstone Dust") would land
    right back on it, so there the verb goes too.
    """
    nm = _pretty_name(item_id)
    r = rng.random()
    if count > 2 and r < 0.34:
        return "%dx %s" % (count, nm)
    if count > 2 and r >= 0.56:
        pl = _plural(nm)
        if pl != nm:
            return "%s %s" % (rng.choice(("Stockpile", "Gather", "Collect",
                                          "Lay in")), pl)
    return nm


def missing_prereqs(quests: list, mid: str, scan: dict, limit: int = 3) -> list:
    """Items a chapter EXPLAINS how to use but never asks the player to get.

    A description that teaches a multi-step thing is doing a quest's job. The
    Terrasteel quest walked through building and activating the Terrestrial
    Agglomeration Plate - a multiblock with its own structure - while the plate
    itself (botania:terra_plate) had no quest at all. That is a missing
    stepping stone, and it is detectable: the description names something that
    exists in this mod and is not a task anywhere in the chapter.

    Returns ids worth promoting to their own quest, earliest mention first.
    """
    names = scan.get("names") or {}
    items = set(scan.get("items", {}).get(mid, ()))
    if not items:
        return []
    have = set()
    for q in quests:
        for t in (q.get("tasks") or []):
            if isinstance(t.get("item"), str):
                have.add(t["item"])
    # display name -> id, longest first so "Mana Pool" cannot shadow
    # "Dilluted Mana Pool"
    cand = []
    for it in items:
        if it in have:
            continue
        disp = names.get(it) or _pretty_name(it)
        if len(disp) < 6 or " " not in disp:
            continue                      # single words match far too loosely
        if _junk_score(it) >= 3:
            continue
        cand.append((len(disp), disp.lower(), it))
    cand.sort(reverse=True)

    found, seen = [], set()
    for q in quests:
        body = " ".join(str(x) for x in (q.get("description") or [])).lower()
        if not body:
            continue
        body = re.sub(r"&[0-9a-fk-orA-FK-OR]", "", body)
        for _l, disp, it in cand:
            if it in seen:
                continue
            if disp in body:
                seen.add(it)
                found.append(it)
                if len(found) >= limit:
                    return found
    return found


def _collection_wheels(mid: str, scan: dict, spine_items: set, rng,
                       max_wheels: int = 4, max_members: int = 14) -> list:
    """Satellite quests for a mod's big collectible families.

    The reference books hang radial wheels off the spine - sixteen runes
    around the Runic Altar, every flower around the Apothecary. Grouping is BY
    the shared token (rune_*, *_petal): exactly the tokens _variant_tokens
    flags as variant axes, which is why the family machinery - built to
    COLLAPSE these - could never find them. The wheel is where they belong:
    optional keep-you-busy content, all gated on one hub.

    Returns [(hub_item, member_item), ...]. The hub is the family's best
    member; the emitter adds it as a spine row if the spine lacks it.
    """
    items = set(scan.get("items", {}).get(mid, ()))
    if len(items) < 20:
        return []

    # a researcher's hand-picked set beats anything derived from name tokens
    hand = _RESEARCHED_WHEELS.get(mid)
    if hand:
        out = []
        for hub, members in hand[:max_wheels]:
            if hub not in items:
                continue
            for m in members[:max_members]:
                if m in items and m != hub and m not in spine_items:
                    out.append((hub, m))
        if out:
            return out

    ok = {}
    for it in items:
        short = it.split(":", 1)[1]
        if short.endswith(_SHAPE_SUFFIX) or _matches_marker(short, _ITEM_BAD):
            continue
        ok[it] = short.split("_")

    tok_members = {}
    for it, parts in ok.items():
        for t in set(parts):
            if len(t) >= 3:
                tok_members.setdefault(t, set()).add(it)
    # a wheel token names 5-24 concrete things; broader is a mod prefix
    # (securitycraft's "reinforced"), narrower is not a collection
    cands = sorted(((t, m) for t, m in tok_members.items()
                    if 5 <= len(m) <= 24 and len(m) <= len(items) * 0.15),
                   key=lambda z: -len(z[1]))
    ranked = rank_mod_items(mid, scan)
    rank = {it: i for i, it in enumerate(ranked)}
    half = max(1, len(ranked)) * 0.55
    out, used = [], set()
    nwheels = 0
    for t, members in cands:
        if nwheels >= max_wheels:
            break
        fresh = [m for m in members if m not in used]
        if len(fresh) < 5:
            continue
        # wheel quality: the ranker already knows decor from content. A rune
        # or gear-set wheel ranks high; a table-cloth or metamorphic-stone
        # wheel ranks in the tail. Median member rank decides.
        rs = sorted(rank.get(m, len(ranked)) for m in fresh)
        if rs[len(rs) // 2] > half:
            continue
        if t in ("block", "item", "raw"):     # grammar words, never a theme
            continue
        used |= set(fresh)
        nwheels += 1
        hub = min(fresh, key=lambda x: rank.get(x, 9999))
        for m in sorted(fresh):
            if m != hub and m not in spine_items:
                out.append((hub, m))
                if sum(1 for h, _x in out if h == hub) >= max_members:
                    break
    return out


_STRUCT_SKIP = ("test", "debug", "empty", "void", "template", "_piece",
                "spawn", "start_pool", "config")


# Words a loot-table leaf carries because of what the ROOM is, not which
# structure it is in - "tower_room", "stronghold_boss_room", "troll_vault".
# The token matcher below strips these before asking whether a table name
# belongs to a structure; without the strip, "room" would have to appear in
# the structure's own id for any per-room table to match. The digits cover
# numbered room variants (hill1/hill2/hill3, tower_room_2).
_STRUCT_LOOT_GENERIC = frozenset(
    "room cache vault boss key treasure dead end jackpot basement library "
    "common rare supply big small 1 2 3 4".split())


def structure_reward(sid: str, scan: dict, limit: int = 3) -> str:
    """What is actually inside this place. -> str

    A player standing in front of a landmark: "I wonder what the main purpose
    is of these cities... no, I guess the quest probably tell us." He cannot
    infer a structure's purpose by looking at it and DEFERS TO THE BOOK as the
    explanation of record. A quest that says only "go and find it" fails him
    at exactly the moment he consulted it.

    The mod already ships the answer: a structure's chest loot table is
    normally named after the structure. Nothing is invented here - if no table
    matches, the caller keeps the generic line rather than guessing.

    Two matchers, unioned. The raw substring test came first and it finds
    tables the token pass cannot (goety:crypt vs crypt.json). But underscores
    defeat it: the Dark Tower's tables are named tower_room and tower_library,
    and "dark_tower" contains neither. The token pass strips the words any
    dungeon room could carry (room, vault, boss...) and asks whether what is
    LEFT belongs to this structure's own name - tower_room -> {tower} <=
    {dark, tower}. That looser net is exactly the pass that can INVENT, so a
    token match is only kept when it is unambiguous: if the same table would
    also claim a SECOND structure of this mod (lich_tower and mushroom_tower
    both absorb tower_room; all three hollow hills absorb hill1-3), saying it
    of either one would be a guess, and a wrong loot list is worse than the
    generic line. Measured cost of the rejection (the reference pack, 141 eligible
    structures): 42% coverage -> 36%, every structure in the difference being
    one the loose match would have lied about.
    """
    ns, short = (sid.split(":", 1) + [""])[:2]
    names = scan.get("names") or {}

    def _tok_match(leaf: str, cand: str) -> bool:
        toks = set(leaf.split("_")) - _STRUCT_LOOT_GENERIC
        if toks and toks <= set(cand.split("_")):
            return True
        a, b = leaf.replace("_", ""), cand.replace("_", "")
        return bool(a) and bool(b) and (a in b or b in a)

    # every structure sharing this namespace, for the ambiguity test
    sibs = {s.split(":", 1)[1]
            for ss in (scan.get("structures") or {}).values()
            for s in ss if s.startswith(ns + ":")}
    best = []
    for tbl, items in (scan.get("struct_loot") or {}).items():
        t_ns, t_path = (tbl.split(":", 1) + [""])[:2]
        if t_ns != ns:
            continue
        leaf = t_path.rsplit("/", 1)[-1]
        if leaf != short and short not in leaf and leaf not in short:
            if not _tok_match(leaf, short):
                continue
            if sum(1 for c in sibs if _tok_match(leaf, c)) > 1:
                continue
        for i in items:
            # A slash means a nested loot_table REFERENCE, not an item -
            # _LOOT_ID_RE keeps "name" fields and a pool can name another
            # table there ("goety:gameplay/plushie_reward"). One shipped:
            # the hedge maze advertised "Chests/useless" as loot.
            if "/" in i:
                continue
            if _junk_score(i) < 3 and i not in best:
                best.append(i)
    if not best:
        return ""
    # A mod's OWN items are why its structure is worth visiting. Sorted
    # without this, goety:crypt advertised "String, Gold Ingot and Iron Ingot"
    # - true, and true of nearly every chest in the game - while Grave Dust,
    # the thing you actually go there for, fell off the end.
    best.sort(key=lambda i: (i.split(":", 1)[0] == "minecraft",
                             _junk_score(i),
                             -_GUIDE_WEIGHT.get(i, 0.0),
                             len(i)))
    got = [names.get(i) or _pretty_name(i) for i in best[:limit]]
    if len(got) == 1:
        return "Its chests hold %s." % got[0]
    return "Its chests hold %s and %s." % (", ".join(got[:-1]), got[-1])


def _structure_facts(sid: str, scan: dict) -> str:
    """Where a place generates and how often, from its own worldgen JSON.

    The second-best answer to "what is this place?" when no loot table names
    what is inside. Sourced, never invented: the structure file's `biomes`
    reference and the structure_set's `spacing` are the mod author's own
    numbers. A biome list is only worth printing when it narrows the search -
    resolving to 4 or fewer biomes is a hint ("Generates in Snowy Forest"),
    while a mod-wide 35-biome tag tells the player nothing he could act on,
    so past 4 the fact is dropped rather than dumped.
    """
    info = (scan.get("struct_info") or {}).get(sid) or {}
    tags = scan.get("biome_tags") or {}

    def resolve(ref, depth=0):
        # -> set of biome ids, or None when any reference cannot be resolved.
        # Partial knowledge must not print: "Generates in Beach" is a lie if
        # the unresolvable half of the tag was every jungle biome.
        if isinstance(ref, list):
            out = set()
            for r in ref:
                got = resolve(r, depth)
                if got is None:
                    return None
                out |= got
            return out
        if not isinstance(ref, str):
            return None
        if ref.startswith("#"):
            if depth >= 3:          # tag cycles exist in the wild; cap, not hang
                return None
            vals = tags.get(ref[1:])
            if vals is None:        # e.g. a #minecraft: tag only the client jar has
                return None
            return resolve(sorted(vals), depth + 1)
        return {ref}

    parts = []
    biomes = resolve(info.get("biomes")) if "biomes" in info else None
    if biomes and len(biomes) <= 4:
        got = sorted(_pretty_name(b) for b in biomes)
        if len(got) == 1:
            parts.append("Generates in %s." % got[0])
        else:
            parts.append("Generates in %s and %s."
                         % (", ".join(got[:-1]), got[-1]))
    sp = info.get("spacing")
    if isinstance(sp, int) and sp > 1:
        parts.append("One is placed roughly every %d chunks." % sp)
    return " ".join(parts)


def structure_quests(mid: str, scan: dict, rng, limit: int = 3) -> list:
    """-> [(title, task, desc)] for a mod's most distinctive structures.

    Exploration and worldgen mods add PLACES, not items. Without this they
    either get no chapter or get one built from whatever incidental blocks
    they happen to register - which is how a structure mod ends up asking for
    a stair variant. Finding the place is the actual content.
    """
    structs = sorted((scan.get("structures") or {}).get(mid, ()))
    if not structs:
        return []
    picks = []
    for sid in structs:
        short = sid.split(":", 1)[1]
        if any(b in short for b in _STRUCT_SKIP):
            continue
        picks.append(sid)
    if not picks:
        return []
    # prefer the ones with distinctive names over numbered variants
    picks.sort(key=lambda x: (bool(re.search(r"_\d+$", x)), len(x)))
    out = []
    for sid in picks[:limit]:
        nice = _pretty_name(sid)
        # Say what the place is FOR. A player who cannot tell by looking
        # consults the book at exactly that moment - "worth the trip" answers
        # nothing, while the structure's own loot table names the reward.
        reward = structure_reward(sid, scan)
        # "out in the world" is a CHK-11 generator-tail skeleton (19 measured
        # hits, 2026-08-29); "while you're exploring" says the same thing in
        # the second person the authored corpus uses (CHK-20).
        # "an Acropolis", not "a Acropolis" - 15% of eligible structure names
        # start with a vowel, and the book shipped the error.
        art = "an" if nice[:1].lower() in "aeiou" else "a"
        if reward:
            desc = ("%sFind %s %s%s%s while you're exploring. %s"
                    % (DESC_BODY, art, DESC_KEY, nice, DESC_BODY, reward))
        else:
            # No loot table matched. This branch used to ship one fixed
            # "worth the trip" sentence - 10 of 16 structure quests read
            # identically, at exactly the moment a player asks what a place
            # is FOR. Say where it generates instead, and when the worldgen
            # JSON gives nothing either, say nothing: blank beats filler.
            desc = ""
            facts = _structure_facts(sid, scan)
            if facts:
                desc = "%s%s" % (DESC_BODY, facts)
        out.append((nice, {"type": "structure", "structure": sid}, desc))
    return out


# Words that carry no identity: every second mod is called "The <something>
# Mod", and the leading word is then worthless as a qualifier.
_QUAL_SKIP = {"the", "a", "an", "and", "of", "mod", "mods", "reforged",
              "forge", "fabric", "minecraft", "edition", "remastered"}


def _mod_qualifier(disp: str, avoid: str) -> str:
    """The one word that identifies a mod, taken from its OWN display name.

    "The Twilight Forest" -> "Twilight", "Dungeon Now Loading" -> "Dungeon".
    A leading publisher tag is stripped ("[Let's Do] Meadow" -> "Meadow") and
    possessives are passed over, because "Jaden's Nether Expansion" identifies
    its structures as Nether ones, not as Jaden's. `avoid` is the place name
    itself, so The Graveyard's crypt never becomes "Graveyard Graveyard" - it
    stays bare instead, which is the honest answer when the mod's name adds
    nothing the title does not already say.
    """
    disp = re.sub(r"^\s*[\[(][^\])]*[\])]\s*", "", str(disp or ""))
    low = " %s " % avoid.lower()
    for w in re.findall(r"[A-Za-z][A-Za-z'-]*", disp):
        if "'" in w or w.lower() in _QUAL_SKIP:
            continue
        if (" %s " % w.lower()) in low:
            continue
        return w[:1].upper() + w[1:]
    return ""


def _qualify_colliding_structure_titles(doc: dict, scan: dict) -> int:
    """Two mods, one place name. -> how many titles were qualified

    A structure title is a bare place name on purpose: the style guide wants
    it that way, and structure_reward already answers what the place is FOR.
    That holds right up until two mods ship the same place. "Labyrinth" from
    The Twilight Forest and "Labyrinth" from Dungeon Now Loading are one entry
    as far as the book is concerned, and a player standing in one of them has
    no way to tell which of the two he just completed - the same complaint as
    "let me see if I can figure out where that sleep quest was".

    Measured over the pack scans on hand: the reference pack has no collision at
    all, Arcanum ships "Graveyard" twice (Ice and Fire, Alshanex Familiars)
    and one test pack ships "Labyrinth" twice. So this fires on the books that
    have the problem and leaves the one that does not alone.

    That is also why it runs over the finished book rather than inside
    structure_quests: structure_quests is handed one mod at a time and cannot
    see the other mod, and qualifying on what the SCAN registers instead of
    what the BOOK prints would have renamed three FEMBY titles whose twins
    never survived the quest trim - three longer titles bought for nothing.

    Only bare generated names are touched. A researched chain that already
    named the place ("Gravedigger" for graveyard:small_grave) keeps its own
    title; this app has been burned before overwriting a good authored title
    with a generated one.
    """
    quests = [q for ch in (doc.get("chapters") or []) for q in (ch.get("quests") or [])]
    used: dict = {}
    for q in quests:
        t = str(q.get("title") or "")
        used[t] = used.get(t, 0) + 1
    disp = {m.get("mod_id"): m.get("name") for m in (scan.get("mods") or [])}
    # Fixed up front, so EVERY member of a collision gets qualified. Renaming
    # only the first and stopping once the name is unique again leaves a bare
    # "Graveyard" beside "Ice Graveyard", and the player in the other mod's
    # graveyard is still working it out by elimination.
    collide = {t for t, c in used.items() if c > 1}
    fixed = 0
    for q in quests:
        title = str(q.get("title") or "")
        if title not in collide:
            continue
        # bare == generated here; anything else was written by a chain
        sid = next((t.get("structure") for t in (q.get("tasks") or [])
                    if t.get("type") == "structure"
                    and isinstance(t.get("structure"), str)
                    and _pretty_name(t["structure"]) == title), None)
        if not sid:
            continue
        qual = _mod_qualifier(disp.get(sid.split(":", 1)[0]), title)
        if not qual:
            continue
        new = "%s %s" % (qual, title)
        # A qualified title that overshoots the width the book is laid out for
        # trades one style break for another, and one that lands on a title
        # already in use has disambiguated nothing.
        if len(new) > 28 or new in used:
            continue
        used[new] = 1
        q["title"] = _txt(new)
        fixed += 1
    return fixed


def _mod_quest_rows(m: dict, scan: dict, n: int, rng, creativity: float) -> list:
    """One target, one quest. A mod with only a couple of usable items used to
    emit "Welcome to Foo" and "Foo" pointing at the same item, which reads as
    the book repeating itself. Wrapping the builder catches every return path -
    the curated-chain branch returns early and skipped an inline check."""
    rows_in = list(_mod_quest_rows_raw(m, scan, n, rng, creativity))
    # A structure mod's real content is its places, not its incidental blocks.
    # Slot a few "go find this" quests near the front - real packs open on an
    # objective far more often than on a gather.
    srows = structure_quests(m["mod_id"], scan, rng)
    for k, sr in enumerate(srows):
        rows_in.insert(min(1 + k * 5, len(rows_in)), sr)

    seen = set()
    uniq = []
    for title, task, desc in rows_in:
        key = (task.get("item") or task.get("structure")
               or "kill:" + str(task.get("entity") or task))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((title, task, desc))
    uniq = _lead_with_the_mob_name(uniq, m["mod_id"], scan)
    return _enforce_recipe_order(uniq[:n], scan)


def _lead_with_the_mob_name(rows: list, mid: str, scan: dict) -> list:
    """Put the mob's name first in items that are NAMED after a mob.

    Cataclysm registers its boss tracks as music_disc_<mob>, so the book got
    nine quests in a row opening "Music Disc ..." - the distinguishing word
    sat 11 characters in, which is the "let me see if I can figure out where
    that sleep quest was" complaint exactly. Minecraft itself never writes
    them that way round: it is Zombie Spawn Egg, Creeper Head, Pillager Banner
    Pattern. The mob leads, the thing follows. So this is not a rewrite, it is
    reading the id in the order the game would have named it.

    Two guards, because the same id shape appears for things that are NOT
    mob-named. Tropicraft's chairs and umbrellas are rideable entities, so
    pink_chair also parses as <head>_<entity> - but there `chair` is the
    variant axis, carried by sixteen different heads, and "Chair Pink" is
    gibberish. The mob must therefore name exactly ONE family: when the name
    identifies a single item it is that item's identity, when it is spread
    across families it is an axis. And the family needs three or more members
    before the shared prefix is costing anyone anything.

    Only bare `_pretty_name` titles are touched. A researched chain or a
    curated row has already chosen its words and outranks a derived guess -
    the lich_trophy lesson.
    """
    items = set(scan.get("items", {}).get(mid, ()))
    mobs = {e.split(":", 1)[1]
            for e in _mob_entities(scan.get("entities", {}).get(mid) or (), items)}
    if not mobs:
        return rows
    heads = collections.defaultdict(set)     # head -> {mob path under it}
    owners = collections.Counter()           # mob path -> #heads carrying it
    for it in items:
        path = it.split(":", 1)[-1]
        for mob in mobs:
            if path.endswith("_" + mob) and len(path) > len(mob) + 1:
                heads[path[: -(len(mob) + 1)]].add(mob)
                break
    for head, ms in heads.items():
        for mob in ms:
            owners[mob] += 1
    lead = {h for h, ms in heads.items()
            if sum(1 for mob in ms if owners[mob] == 1) >= 3}
    if not lead:
        return rows

    out = []
    for title, task, desc in rows:
        it = task.get("item")
        path = it.split(":", 1)[-1] if isinstance(it, str) else ""
        for mob in (mobs if path else ()):
            if not path.endswith("_" + mob) or len(path) <= len(mob) + 1:
                continue
            head = path[: -(len(mob) + 1)]
            if head not in lead or owners[mob] != 1:
                break
            if title == _pretty_name(it):
                title = "%s %s" % (_pretty_name(mob), _pretty_name(head))
            break
        out.append((title, task, desc))
    return out


def _enforce_recipe_order(rows: list, scan: dict) -> list:
    """Move any row that is asked for before a HARD ingredient of it.

    Ordering sources only cover the front of a chapter. Botania's researched
    chain is 29 steps but the chapter runs to 69, and the filler tail was
    appended in desirability order with no recipe check - so Pollidisiac
    landed at 40 while the Rune of Lust it requires sat at 67. The player is
    asked to craft a flower out of a rune they have not been sent for.

    Only `edges` count. An `alts` group means "any ONE of these", so it is not
    a prerequisite - treating alternatives as hard once made andesite_alloy
    look like it needed a golden sheet.

    A stable insertion pass, not a full topological sort: it preserves the
    taught order wherever that order is already legal, and only demotes the
    rows that actually break a recipe.
    """
    edges = scan.get("edges") or {}
    ids = [r[1].get("item") for r in rows]
    pos = {i: k for k, i in enumerate(ids) if i}
    if not pos:
        return rows
    out, placed = [], set()
    pending = list(rows)
    for _pass in range(4):               # bounded: cycles must not hang this
        again = []
        for row in pending:
            it = row[1].get("item")
            need = [g for g in (edges.get(it) or ()) if g in pos]
            if it and any(g not in placed and pos[g] > pos.get(it, 0)
                          for g in need):
                again.append(row)        # an ingredient is still ahead of it
                continue
            out.append(row)
            if it:
                placed.add(it)
        if not again:
            return out
        if len(again) == len(pending):   # nothing moved; a cycle, keep as-is
            return out + again
        pending = again
    return out + pending


def _mod_quest_rows_raw(m: dict, scan: dict, n: int, rng, creativity: float) -> list:
    """-> [(title, task_dict, description), ...] for one mod, at most n rows.
    Prefers craftable items so quests always point at something obtainable."""
    mid = m["mod_id"]
    all_id_set = set(scan["items"].get(mid, ()))
    craft = set(scan.get("craftable", {}).get(mid, ()))
    all_ids = list(all_id_set)
    # Data-derived pick: drops material variants and vanilla reskins, then
    # orders by what feeds what. This is what stops a SecurityCraft chapter
    # asking for six wood variants of the same boat instead of the keypad.
    items = pick_quest_items(mid, scan, n)
    if len(items) < 4:                             # tiny or unreadable mod
        craft_ranked = _rank_mod_items([i for i in all_ids if i in craft])
        other_ranked = _rank_mod_items([i for i in all_ids if i not in craft])
        items = craft_ranked + other_ranked
    # Deliberately NOT padded back up to n. Topping a short list up with the
    # next-best-ranked ids is what refilled the chapter with the variants the
    # pick had just removed - a mod with eight things worth having gets eight
    # quests, and the chapter is better for it.
    rows = []

    # --- follow the mod's own progression when it ships one -----------------
    # Priority: a hand-checked chain from mod_progression.py (real "how do I
    # start" steps from the mod's wiki), then the mod's own advancement tree.
    # Either beats guessing an order from item names.
    curated = []
    _ents = set(scan["entities"].get(mid, ()))
    _structs = set((scan.get("structures") or {}).get(mid, ()))
    chain = _curated_chain(mid)
    if not chain:
        # Mined pack consensus is UNVETTED: it records what other authors asked
        # for, including their filler. Passing it straight through let a bakery
        # chapter come out 13/14 furniture. Curated entries stay as written.
        chain = [(i, "", "") for i in _HARVESTED_ORDER.get(mid, [])
                 if _item_worth(i, mid, scan)]
    for entry in chain:
        ref, title, desc = entry
        # "a|b" lists alternatives - mods rename ids between versions
        for cand in str(ref).split("|"):
            cand = cand.strip()
            if cand.startswith("kill:"):
                if cand[5:] in _ents:
                    curated.append({"kill": cand[5:], "title": title, "desc": desc})
                    break
            elif cand.startswith("dim:"):
                curated.append({"dimension": cand[4:], "title": title, "desc": desc})
                break
            elif cand.startswith("structure:"):
                # A worldgen mod's chain is mostly places. Verify against the
                # scan, exactly as kill steps are verified, so a renamed or
                # absent structure cannot become an uncompletable task.
                if cand[10:] in _structs:
                    curated.append({"structure": cand[10:], "title": title,
                                    "desc": desc})
                break
            elif cand in all_id_set or cand in (scan.get("vanilla") or _VANILLA_ITEM_SET):
                curated.append({"item": cand, "title": title, "desc": desc})
                break
    # Only trust progression entries whose item we can actually vouch for:
    # a validated modded id, or a vanilla id from our known list. Advancement
    # icons also point at technical blocks (structure_void) and decoration.
    _van = scan.get("vanilla") or _VANILLA_ITEM_SET
    # An advancement's ICON is very often a stock vanilla item - The Graveyard
    # illustrates its tree with skeleton_skull, cobweb and torch. Those are
    # decoration for the advancement screen, not goals, and asking a player to
    # fetch a torch inside a Graveyard chapter is exactly the filler that made
    # chapters feel padded. A mod chapter is about that MOD's items.
    prog = [r for r in ((scan.get("progression") or {}).get(mid) or [])
            if (r["item"] in all_id_set or r["item"] in _van)
            and _junk_score(r["item"]) < 3]
    used = set()

    # curated chain first — it opens the chapter on the mod's real first step
    for c in curated[:n]:
        if len(rows) >= n:
            break
        # hand-checked text first, then the mod's own authored line for that
        # item - a harvested chain carries no prose of its own, so without the
        # blurb fallback those quests shipped with no description at all
        d = ("&7" + _blurb_sentence(c["desc"])) if c.get("desc") else None
        if not d and c.get("item"):
            _b = (scan.get("blurbs") or {}).get(c["item"])
            if _b:
                d = DESC_BODY + _blurb_sentence(_b)
        if "kill" in c:
            rows.append((c["title"], {"type": "kill", "entity": c["kill"], "value": 1}, d))
        elif c.get("dimension"):
            rows.append((c["title"] or "Journey Onward",
                         {"type": "dimension", "dimension": c["dimension"]}, d))
        elif c.get("structure"):
            # a place, not an item - it has no id to mark as used
            rows.append((c["title"] or _pretty_name(c["structure"]),
                         {"type": "structure", "structure": c["structure"]}, d))
        elif not c.get("item"):
            continue                    # nothing questable in this entry
        else:
            used.add(c["item"])
            rows.append((c["title"] or _item_title(c["item"], 1, rng),
                         {"type": "item", "item": c["item"], "count": 1}, d))
    if curated:
        prog = [r for r in prog if r["item"] not in used]
    prog = [r for r in prog
            if str(r.get("item", "")).split(":")[0] == mid
            and _item_worth(r["item"], mid, scan)]

    if len(prog) >= 4:
        keep = prog[:n]
        if len(prog) > n:                       # thin the middle, keep both ends
            head, tail = prog[:max(2, n // 2)], prog[-(n - max(2, n // 2)):]
            keep = head + tail
        for i, r in enumerate(keep):
            if len(rows) >= n:
                break
            it = r["item"]
            if it in used:
                continue
            used.add(it)
            title = r["title"] or _item_title(it, 1, rng)
            first = (i == 0)
            last = (i == len(keep) - 1)
            c = 1 if (first or last) else _count_for(it, rng)
            # Progression rows carry their own copy of the jar advancement
            # text (the scan mirrors it into blurbs at build time, but this
            # path reads r["desc"], not the blurbs dict), so it must go
            # through the same sentence normaliser - the same string ending
            # punctuated in one quest and bare in another is the opposite of
            # one voice.
            d = _blurb_sentence(r["desc"])
            if not d and rng.random() < 0.25:
                d = _desc("item", _pretty_name(it), c, m["name"], rng,
                          it, scan.get("blurbs"), scan) or ""
            rows.append((title[:60],
                         {"type": "item", "item": it, "count": c},
                         ("&7" + d[:150]) if d else None))
        items = [i for i in items if i not in used]

    if items and not rows:
        rows.append((rng.choice(_INTRO_TITLES) % m["name"],
                     {"type": "item", "item": items[0], "count": 1},
                     ("&7First stop in %s." % m["name"]) if rng.random() < 0.25 else None))
    # kill quests only for adventure/mob mods (tech mods have no real mobs)
    edrops = _entity_drop_map(scan)
    if m["category"] in ("world", "mob"):
        mobs = _mob_entities(scan["entities"].get(mid, ()), all_id_set)
        # Rank by loot signal before taking the top slice. The slice used to
        # be alphabetical, so Tropicraft's kill quests were ashen, cowktail
        # (a passive flower-cow) and brown_basilisk_lizard while iguana,
        # tree_frog and the Eih boss sat unpicked at i/t/e. A mob whose loot
        # table holds its mod's OWN items is what the mod wants hunted, and
        # is exactly the mob _mob_drop_desc can say something true about:
        # measured on this pack's six mob mods, this sort takes the top-6
        # picks with a sourced description from 15/36 to 36/36. A stable sort
        # on purpose — no-loot mobs (167 of 324 here) are DEMOTED, never
        # dropped, and keep their spawn-egg-first order among themselves.
        def _loot_rank(e):
            ids = edrops.get(e) or ()
            ep = e.split(":")[-1]
            own = sum(1 for i in ids if not i.startswith("minecraft:")
                      and i.split(":")[-1] != ep)
            return (-own, -len(ids))
        mobs.sort(key=_loot_rank)
        for e in mobs[:min(len(mobs), max(1, n // 3))]:
            if len(rows) >= n:
                break
            boss = (any(b in e.split(":")[-1] for b in _BOSS_MARK)
                    or _boss_by_drops(e, edrops.get(e) or ()))
            v = 1 if boss else rng.choice([1, 3, 5, 5, 8, 10])
            if boss:
                rows.append(("The %s" % _pretty_name(e),
                             {"type": "kill", "entity": e, "value": 1},
                             "&7This is the one that matters." if rng.random() < 0.5 else None))
            else:
                passive = any(p in e.split(":")[-1] for p in _PASSIVE_MOB)
                verb = rng.choice(("Hunt", "Round up", "Cull")) if passive \
                    else rng.choice(_KILL_VERBS)
                # what the mob's own loot table says it is worth, before the
                # canned _desc lines — see _mob_drop_desc for why
                rows.append(("%s %s" % (verb, _pretty_name(e)),
                             {"type": "kill", "entity": e, "value": v},
                             _mob_drop_desc(e, scan)
                             or _desc("kill", _pretty_name(e), v, m["name"], rng)))
    # only invent a capstone when the mod gave us no progression of its own
    capstone = None if used else (items[1] if len(items) > 1 else None)
    pool = [i for i in items if i != capstone and i not in used]
    # Order by crafting depth so a quest never asks for the product before its
    # ingredient (raven feather -> magic map focus). Falls back to the existing
    # usefulness ranking when a mod ships no recipes we can read.
    tiers = scan.get("tier") or {}
    if tiers:
        pool.sort(key=lambda i: (tiers.get(i, 99), _junk_score(i)))
    slots = max(0, n - len(rows) - (1 if capstone else 0))
    if pool and slots:
        step = max(1, len(pool) // slots)
        for it in pool[::step]:
            if len(rows) >= n - (1 if capstone else 0):
                break
            c = _count_for(it, rng)
            rows.append((_item_title(it, c, rng),
                         {"type": "item", "item": it, "count": c},
                         _desc("item", _pretty_name(it), c, m["name"], rng,
                               it, scan.get("blurbs"), scan)))
    if capstone and len(rows) < n:
        # "Master <Mod>" is a milestone claim, so the task under it has to earn
        # one. The capstone is items[1] - the SECOND entry of a ranked list -
        # so the claim was being made by index, not by evidence: "Master Dusty
        # Decorations" asked for a rusty metal block, "Master Abyssal Decor"
        # for grime, "Master MOA DECOR: LIGHTS" for a bare bulb. That is the
        # player complaint "I don't know why it just told me the quest was
        # complete when clearly it was not" - the title promised a finale the
        # task never checked. The paired "is what <Mod> is all about" line
        # asserted significance off the same index lookup and is gone with it;
        # blank beats a claim we cannot support.
        #
        # Keep the framing only where sourced data says the item really is the
        # end of the mod: the last link of a hand-checked chain, or an item at
        # the mod's deepest crafting tier (and only when the mod HAS depth - in
        # a flat mod every item ties for deepest, which is not evidence). For
        # an unresearched mod none of that exists, which is the common case:
        # measured over the pack, all 14 capstones sat at tier 1-2 against mod
        # maxima of 2-8. There the fallback must be the plain item title, not a
        # softer guess, or the unearned promise returns through another door.
        _tail = [str(e[0]).split("|")[0].strip() for e in (chain or [])][-1:]
        _tv = [tiers[i] for i in all_id_set if i in tiers]
        if (capstone in _tail
                or (_tv and min(_tv) < max(_tv) and tiers.get(capstone) == max(_tv))):
            rows.append(("Master %s" % m["name"],
                         {"type": "item", "item": capstone, "count": 1}, None))
        else:
            rows.append((_item_title(capstone, 1, rng),
                         {"type": "item", "item": capstone, "count": 1},
                         _desc("item", _pretty_name(capstone), 1, m["name"],
                               rng, capstone, scan.get("blurbs"), scan)))
    rows = rows[:n]

    # A boss is a chapter's finale, not its opening act. Twilight Forest should
    # end on the Naga/Lich, Botania on the Gaia Guardian — so float boss kills
    # to the back, keeping everything else in progression order.
    if curated:
        return rows          # a hand-checked chain already has the right order

    def _is_boss(row):
        # The finale slot demands SOURCED evidence (_boss_by_drops), not just
        # a name: _BOSS_MARK alone floated twilightforest:hydra_mortar — a
        # projectile with no loot table — to the back of a chapter as its
        # climax. The name test still widens the boss BRANCH above (title and
        # value), where a false positive costs a phrasing, not the ending.
        t = row[1]
        if t.get("type") != "kill":
            return False
        e = str(t.get("entity", ""))
        return _boss_by_drops(e, edrops.get(e) or ())
    normal = [r for r in rows if not _is_boss(r)]
    bosses = [r for r in rows if _is_boss(r)]
    return normal + bosses


# --- themed questlines ------------------------------------------------------
# A theme is a short phrase ("steam power", "diamond gear", "beekeeping") or a
# literal item id. Each becomes one questline whose quests all serve that idea.
_THEME_SYNONYMS = {
    "steam": ("steam", "boiler", "engine", "piston", "pressure", "furnace", "coal", "brass"),
    "power": ("engine", "generator", "motor", "battery", "energy", "flux", "dynamo", "turbine"),
    "automation": ("belt", "conveyor", "hopper", "arm", "funnel", "chute", "pipe",
                   "filter", "sorter", "assembler", "crafter", "deployer"),
    "redstone": ("redstone", "repeater", "comparator", "observer", "piston", "lever", "circuit"),
    "mining": ("pickaxe", "drill", "ore", "raw_", "shovel", "lantern", "rail", "minecart"),
    "smelting": ("furnace", "blast", "smelter", "kiln", "ingot", "alloy", "crucible"),
    "farming": ("seed", "crop", "hoe", "wheat", "carrot", "potato", "berry",
                "sapling", "compost", "fertilizer", "harvest"),
    "cooking": ("food", "stew", "soup", "pie", "bread", "cake", "pot", "skillet",
                "knife", "cooked", "meal", "feast"),
    "brewing": ("potion", "brewing", "cauldron", "flask", "elixir", "tincture", "vial"),
    "combat": ("sword", "axe", "bow", "crossbow", "arrow", "shield", "armor",
               "helmet", "chestplate", "leggings", "boots", "blade"),
    "magic": ("mana", "rune", "spell", "wand", "staff", "tome", "grimoire", "altar",
              "ritual", "sigil", "essence", "arcane", "enchant"),
    "exploration": ("map", "compass", "boat", "elytra", "telescope", "lantern", "waystone"),
    "storage": ("chest", "barrel", "crate", "drawer", "backpack", "shulker", "pouch", "bag"),
    "building": ("brick", "plank", "stone", "glass", "concrete", "scaffold", "chisel"),
    "diamond": ("diamond",), "iron": ("iron",), "gold": ("gold",),
    "copper": ("copper",), "netherite": ("netherite", "ancient_debris"),
    "wood": ("log", "plank", "wood", "sapling"),
    "nether": ("nether", "blaze", "ghast", "quartz", "soul", "basalt", "crimson", "warped"),
    "end": ("end_", "ender", "chorus", "shulker", "purpur", "elytra", "dragon"),
    "ocean": ("prismarine", "coral", "kelp", "trident", "nautilus", "conduit", "fish"),
    "beekeeping": ("bee", "honey", "hive", "comb", "nest"),
    "decoration": ("lamp", "banner", "pot", "frame", "statue", "candle", "carpet"),
}


def _theme_tokens(theme: str):
    """-> (primary words, synonym words). Primary words are what the user typed;
    synonyms broaden the net but never match on their own strength."""
    t = theme.strip().lower()
    if ":" in t:                                   # a literal item id
        return ([t.split(":")[-1]], [])
    words = [w for w in re.split(r"[^a-z0-9]+", t) if len(w) > 2]
    syn = set()
    for w in words:
        for key, syns in _THEME_SYNONYMS.items():
            if w == key or w.startswith(key) or key.startswith(w):
                syn.update(syns)
    return (words or [t], sorted(syn - set(words)))


def _word_hit(word: str, parts: list) -> bool:
    """Match whole name-parts, not raw substrings — so 'bee' never matches
    'beef_stew', 'arm' never matches 'charm', and 'chest' never matches
    'chestplate'. Only a short inflection (plural, -s/-es) counts as the word."""
    for p in parts:
        if p == word:
            return True
        if len(word) >= 4 and p.startswith(word) and len(p) - len(word) <= 2:
            return True
    return False


def _theme_rows(theme: str, scan: dict, mod_ids, n: int, rng, craft_first=True) -> list:
    """Build the quest rows for one themed questline, drawing from every
    selected mod plus vanilla so the line genuinely spans the pack."""
    primary, syns = _theme_tokens(theme)
    want_ns = theme.split(":")[0].lower() if ":" in theme else None
    theme_grp = _theme_group(theme)
    # mods whose own theme matches this questline's - their items lead
    cat_of = {m["mod_id"]: m["category"] for m in scan["mods"]}
    on_theme = {mid for mid in mod_ids
                if _GROUP_FOR_CAT.get(cat_of.get(mid, "unknown")) == theme_grp}
    craft = set()
    pool = set(scan.get("vanilla") or _VANILLA_ITEMS)   # vanilla always in scope
    for mid in mod_ids:
        pool |= set(scan["items"].get(mid, ()))
        craft |= set(scan.get("craftable", {}).get(mid, ()))
    # this used to union the whole vanilla ITEM list into the craft set, which
    # declared every vanilla id craftable and made the craftable-first ranking
    # below inert. Only ids with a real recipe belong here.
    craft |= set(scan.get("vanilla_craftable") or ())
    hits = []
    for i in pool:
        ns, short = (i.split(":", 1) + [""])[:2]
        if _junk_score(i) >= 3:
            continue
        parts = short.split("_")
        pscore = sum(2 for w in primary if _word_hit(w, parts))
        sscore = sum(1 for w in syns if _word_hit(w, parts))
        if not pscore and not sscore:
            continue
        # rank source: asked-for namespace > vanilla > on-theme mod > anything else
        rank = 3
        if want_ns and ns == want_ns:
            rank = 0
        elif ns == "minecraft":
            rank = 1
        elif ns in on_theme:
            rank = 2
        hits.append((rank, -(pscore + sscore), 0 if i in craft else 1,
                     _junk_score(i), len(short), i))
    hits.sort()
    items = [h[-1] for h in hits]
    rows = []
    for it in items[:n]:
        c = _count_for(it, rng)
        rows.append((_item_title(it, c, rng),
                     {"type": "item", "item": it, "count": c},
                     _desc("item", _pretty_name(it), c, theme.title(), rng)))
    # a kill goal if the theme reads like combat and the pack has real mobs
    if any(t in ("sword", "combat", "blade", "boss", "slay", "hunt", "fight", "kill")
           for t in list(primary) + list(syns)):
        for mid in mod_ids:
            mobs = _mob_entities(scan["entities"].get(mid, ()),
                                 set(scan["items"].get(mid, ())))
            if mobs and len(rows) < n:
                e = mobs[0]
                rows.insert(len(rows) // 2,
                            ("%s %s" % (rng.choice(_KILL_VERBS), _pretty_name(e)),
                             {"type": "kill", "entity": e, "value": rng.choice([5, 8, 10])},
                             None))
                break
    return rows[:n]


def theme_chapters(scan: dict, selected_ids: list, themes: list, opts: dict) -> list:
    """-> [(group, title, icon, rows)] — one chapter per requested theme."""
    density = opts.get("density", "normal")
    per = {"tiny": 6, "small": 9, "normal": 14, "large": 20,
           "massive": 28, "colossal": 40}.get(density, 14)
    sel = set(selected_ids)
    mod_ids = [m["mod_id"] for m in scan["mods"]
               if (m["mod_id"] in sel) or
               (not sel and not is_library(m["mod_id"], m["name"]))]
    rng = random.Random("themes/" + "|".join(themes))
    out = []
    for th in themes:
        th = th.strip()
        if not th:
            continue
        rows = _theme_rows(th, scan, mod_ids, per, rng)
        if len(rows) < 3:
            continue                       # nothing in the pack fits — skip quietly
        grp = _theme_group(th)
        title = th if th.isupper() or " " in th else th.title()
        if ":" in th:
            title = _pretty_name(th)
        out.append((grp, title, rows[0][1].get("item"), rows))
    return out


def _theme_group(theme: str) -> str:
    t = theme.lower()
    for key, grp in (("magic", "Magic"), ("arcane", "Magic"), ("spell", "Magic"),
                     ("mana", "Magic"), ("ritual", "Magic"),
                     ("steam", "Tech"), ("power", "Tech"), ("automation", "Tech"),
                     ("redstone", "Tech"), ("machine", "Tech"), ("energy", "Tech"),
                     ("farm", "Farm and Food"), ("cook", "Farm and Food"),
                     ("food", "Farm and Food"), ("bee", "Farm and Food"),
                     ("brew", "Farm and Food"),
                     ("combat", "Adventure"), ("boss", "Adventure"),
                     ("explor", "Adventure"), ("dungeon", "Adventure"),
                     ("nether", "Adventure"), ("end", "Adventure"),
                     ("build", "Decoration"), ("decor", "Decoration"),
                     ("storage", "Utility"), ("mining", "Utility")):
        if key in t:
            return grp
    return "Themes"



def _same_line(prev_spec, cur_spec) -> bool:
    """Should this chapter be locked behind the previous one?

    Only when it genuinely continues it: the vanilla progression chapters lead
    into each other, and "Foo I" leads into "Foo II". A mod chapter must NOT be
    gated behind an unrelated one - locking Create's brass behind a vanilla
    diamond pickaxe is exactly the kind of thing that makes a book feel like a
    corridor instead of a set of things to go and do.
    """
    if not prev_spec:
        return False
    pg = str(prev_spec.get("group", "")).strip().lower()
    cg = str(cur_spec.get("group", "")).strip().lower()
    pf = str(prev_spec.get("focus", "")).strip().lower()
    cf = str(cur_spec.get("focus", "")).strip().lower()
    if pg == cg == "vanilla" or (pf and pf == cf == "vanilla"):
        return True                      # overworld -> nether -> end
    if pf and cf and pf == cf:
        return True                      # same mod, part 2
    import re as _re
    stem = lambda t: _re.sub(r"[ _]+(i{1,3}|iv|v|[0-9]+)$", "",
                             _re.sub(r"&.", "", str(t or "")).strip().lower())
    ps, cs = stem(prev_spec.get("title")), stem(cur_spec.get("title"))
    return bool(ps) and ps == cs         # "X Miscellany I" -> "X Miscellany II"


# ---- CHK-01 / CHK-02: give the book a way to SAY something ---------------- #
#
# Re-measured on build_chapters output (the book a player opens, not the doc)
# from scan.pkl on 2026-08-29, immediately before this change:
#     checkmark tasks ..... 0 of 381   (mix: item 336, structure 23, kill 20,
#                                       dimension 2, and nothing else)
#     ch1_opens_free ...... False
#     ch1_oriented_frac ... 0.000 (0 of 9 - all nine of "Overworld Beginnings"
#                                  are item fetches and it opens on 16 oak logs)
#
# TASK_TYPES has listed "checkmark" since the beginning and _task() has always
# built one; no generation path ever asked for it. The consequence is that
# every one of our 381 quests had to charge the player - acquire, kill or
# travel - before the book was allowed to tell them anything at all.
#
# Authored practice (moddb/immersion_spec.json CHK-01/CHK-02, corpora cf86 and
# arc27): the first-shown chapter-one quest is a checkmark in 67.4% of 86 books
# against 25.6% for later chapters (z=6.71, the largest first-chapter
# separation anywhere in that study); 49.6% of chapter-one quests carry a
# checkmark against 4.5% across 445 later chapters; ch1_oriented_frac averages
# 0.692 over 18 packs.
#
# The exploit this is deliberately written not to take: six blank checkmark
# quests appended to chapter one move the naive free-task fraction from 0.000
# to 0.400 and pass a checkmark-count rule while changing nothing anybody would
# read. So a row is emitted only where there is something TRUE to put on it -
# the mod author's own blurb out of mods.toml, a guide's account of how you
# actually reach the mod, the roster of mods a "Miscellany" chapter really
# collects, or a count taken off this pack's own scan. A chapter with none of
# those gets no checkmark and no empty box: cataclysm's mods.toml description
# is a patron list, mod_blurb correctly returns "", and that chapter is
# therefore left exactly as it was.


# The two vanilla destination chapters have no mods.toml to quote and no guide
# entry, so without these they stay silent - and "Into the Nether" opening on
# "Ten blocks and a flint and steel" is the second-most-visited chapter in the
# book saying nothing. These are 1.20.1 vanilla facts, so they hold for any
# pack, and they are matched on the chapter's own task items rather than on its
# title so a retitled chapter still finds its page.
_VANILLA_TOPIC_PAGES = [
    ({"minecraft:blaze_rod", "minecraft:netherite_ingot", "minecraft:nether_wart",
      "minecraft:magma_cream", "minecraft:ancient_debris",
      "minecraft:wither_skeleton_skull"},
     "Before You Go",
     "The Nether is a different set of rules, not a harder Overworld. Water "
     "evaporates where you place it, beds explode, compasses and clocks spin "
     "uselessly, and there is no night to wait out. Take more building blocks "
     "than you think you need, a way back to the portal you came through, and "
     "gold if you would rather trade with the piglins than fight them."),
    ({"minecraft:ender_eye", "minecraft:elytra", "minecraft:shulker_shell",
      "minecraft:chorus_fruit", "minecraft:dragon_breath"},
     "What You Are Walking Into",
     "One island, one dragon, and then a void with a thousand more islands "
     "past it. The end gateway on the rim only opens once the dragon is dead, "
     "and the outer islands are where the shulkers and the chorus fruit are. "
     "The way home is the exit portal you arrive beside - there is no other, "
     "so do not lose it."),
]


def _row_mods(rows) -> list:
    """The mod ids a chapter's own tasks come from, commonest first. -> list

    Same derivation the chapter-intro lookup already uses further down, kept
    here so a spec can be described before it becomes a chapter.
    """
    c = collections.Counter()
    for r in rows or ():
        t = r[1] if len(r) > 1 and isinstance(r[1], dict) else {}
        ns = str(t.get("item") or t.get("structure")
                 or t.get("entity") or "").split(":", 1)[0]
        if ns and ns != "minecraft":
            c[ns] += 1
    return [k for k, _n in c.most_common()]


def _mod_display_name(mid: str, scan: dict) -> str:
    for m in (scan.get("mods") or ()):
        if m.get("mod_id") == mid:
            nm = str(m.get("name") or "").strip()
            if nm:
                nm = (nm.replace("_", " ").title()
                      if nm.islower() and " " not in nm else nm)
                # "[Let's Do] Farm & Charm" put a literal & into a description,
                # which made the row builder treat the whole page as
                # pre-coloured and ship it with no colour code at all - the one
                # uncoloured description in the book. Names come out of
                # mods.toml, so any of them can carry one.
                return nm.replace("&", "and")
            break
    return str(mid).replace("_", " ").title()


def _uncoloured(t: str) -> str:
    """Plain prose the row builder is free to colour itself. -> str

    The builder decides between style_desc() and "already formatted, pass it
    through" on whether the string contains an ampersand, so one that leaks in
    from a mod name or a jar blurb costs the page its colour code entirely.
    """
    return re.sub(r"\s+", " ", str(t or "").replace("&", "and")).strip()


def _and_list(names, limit: int = 5) -> str:
    names = [str(n).strip() for n in names if str(n).strip()]
    if not names:
        return ""
    extra = len(names) - limit
    names = names[:limit]
    if extra > 0:
        return "%s and %d more" % (", ".join(names), extra)
    if len(names) == 1:
        return names[0]
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def _derived_chapter_intro(mid: str, scan: dict, nrows: int) -> str:
    """An opener built from what the SCAN knows about a mod. -> str (may be "")

    Used only when chapter_intro found neither a guide entry nor the mod's own
    blurb. Without it such a chapter opened cold: no page saying what the mod
    is, which also cost the book a free task and a root description long
    enough to read as an introduction. On a pack with no research coverage
    that is most of its chapters.

    Every clause is checked against the scan before it is written, so nothing
    here can claim a mob the pack does not have. A mod that measures as
    nothing still returns "" - blank beats filler.
    """
    if not mid or nrows < 3:
        return ""
    name = _mod_display_name(mid, scan)
    items = len((scan.get("items") or {}).get(mid) or ())
    mobs = len((scan.get("entities") or {}).get(mid) or ())
    structs = len((scan.get("structures") or {}).get(mid) or ())
    dims = len((scan.get("dimensions") or {}).get(mid) or ()) \
        if isinstance(scan.get("dimensions"), dict) else 0
    if items < 8:
        return ""                    # too small to describe honestly

    bits = ["%s adds %d items to this pack, and the %d quests here are the "
            "ones worth going after." % (name, items, nrows)]
    facts = _derived_chapter_facts(mid, scan, nrows)
    if facts:
        bits.append(facts)
    return " ".join(bits)


def _derived_chapter_facts(mid: str, scan: dict, nrows: int) -> str:
    """The measurable extras a chapter opener can add. -> str (may be "")

    Kept separate from the sentence above so it can also lengthen a REAL
    intro. A chapter's opener is the one page that should say the most, and
    when a mod's own blurb is a single marketing line the opener came out
    shorter than the quests below it - which is backwards, and is what CHK-12
    measures (root descriptions against leaf descriptions).
    """
    mobs = len((scan.get("entities") or {}).get(mid) or ())
    structs = len((scan.get("structures") or {}).get(mid) or ())
    dims = len((scan.get("dimensions") or {}).get(mid) or ())         if isinstance(scan.get("dimensions"), dict) else 0
    extra = []
    if mobs >= 3:
        extra.append("%d creatures you can run into" % mobs)
    if structs >= 2:
        extra.append("%d structures that generate in the world" % structs)
    if dims:
        extra.append("a dimension of its own")
    out = []
    if extra:
        out.append("It also brings %s." % _and_list(extra, 3))
    out.append("There are %d quests here, and nothing in the chapter needs "
               "another mod first - you can start it whenever you like."
               % nrows)
    return " ".join(out)


def _chapter_opener_row(title: str, rows: list, scan: dict):
    """A free quest saying what a chapter IS, or None. -> (title, task, desc)

    None is the common and correct answer. It means the pack shipped nothing
    true about this chapter, and an empty checkbox is worse than no checkbox.
    """
    if not rows:
        return None
    mids = _row_mods(rows)
    if not mids:
        # An all-vanilla chapter. It owns no mod to quote, but if its own tasks
        # say where it goes, the destination is worth a page.
        have = {str((r[1] or {}).get("item") or "") for r in rows
                if isinstance(r[1], dict)}
        for sig, qt, body in _VANILLA_TOPIC_PAGES:
            if len(have & sig) >= 2:
                return (qt, {"type": "checkmark"}, _uncoloured(body))
        return None                      # themed or generic: nothing to say
    n = len(rows)
    if len(mids) == 1:
        body = chapter_intro(mids[0], scan)
        if not body:
            # No guide entry and no jar blurb - fall back to what the scan
            # itself measured. Still returns None when even that is empty.
            body = _derived_chapter_intro(mids[0], scan, len(rows))
        elif len(body) < 200:
            # A one-line marketing blurb makes the chapter's opening page
            # shorter than the quests underneath it, which is backwards: the
            # opener is the page that should say the most. Top it up with the
            # same measured facts rather than padding it with adjectives.
            _f = _derived_chapter_facts(mids[0], scan, len(rows))
            if _f:
                body = body.rstrip() + " " + _f
        if not body:
            return None                  # nothing true to say - say nothing
        # A count and nothing more. An earlier draft named the chapter's first
        # and last item - measured against build_chapters, the first-shown item
        # quest differs from rows[0] on 6 of 25 chapters because sibling nodes
        # are jittered into a different reading order, so the sentence would
        # have been wrong roughly a quarter of the time. Quest counts and
        # namespace sets, by contrast, survive clean_doc and build_chapters
        # unchanged on all 25 (verified doc-side against book-side).
        return ("What %s Adds" % _mod_display_name(mids[0], scan),
                {"type": "checkmark"},
                _uncoloured("%s %d quests follow." % (body.rstrip(), n)))
    plain = re.sub(r"&.", "", str(title or "")).strip() or "This chapter"
    # The only place a player can find out which mods a shared chapter holds.
    # Named after the chapter, because five chapters all opening on a quest
    # called "What Is In This Chapter" is five quests a player cannot tell
    # apart in a search or a completed list.
    return ("What Is In %s" % plain if plain != "This chapter"
            else "What Is In This Chapter", {"type": "checkmark"}, _uncoloured(
        "%s collects the smaller mods in this pack: %s. %d quests follow."
        % (plain, _and_list([_mod_display_name(x, scan) for x in mids], 6), n)))


def _front_matter_rows(specs: list, scan: dict, opts: dict | None = None) -> list:
    """Pages the first chapter opens on, before it asks for anything. -> rows

    Every page is built from a fact taken off this pack - its mod count, its
    own group names, the chapters it ended up with, the items chapter one
    actually asks for. There is no generic welcome to fall back on, because a
    generic welcome is exactly the filler CHK-02's text clause exists to keep
    out: a page whose facts could not be filled is not emitted.
    """
    if not specs or not specs[0][3]:
        return []
    pages = []
    groups = []
    for s in specs:
        g = str(s[0] or "").strip()
        if g and g.lower() != "vanilla" and g not in groups:
            groups.append(g)
    if len(groups) >= 2:
        pages.append((
            "How the Chapters Are Sorted",
            "Chapters are grouped by what their mods do - %s, %d groups in all. "
            "A mod big enough to fill a chapter gets one to itself; "
            "everything smaller is collected into a shared chapter for "
            "its group, so a mod with 3 things worth doing is still in "
            "here somewhere and you will not have to go hunting for it."
            % (_and_list(groups, 7), len(groups))))
    owned = []
    for s in specs:
        if len(_row_mods(s[3])) == 1:
            t = re.sub(r"&.", "", str(s[1] or "")).strip()
            if t and t not in owned:
                owned.append(t)
    if len(owned) >= 2:
        pages.append((
            "What This Pack Is Built On",
            "The %d mods with a chapter to themselves: %s. Each got its "
            "own chapter because it adds enough to be worth following on "
            "its own terms. None of them has to be played before another, "
            "the book does not mind which one you open first, and you can "
            "leave one half finished for as long as you like without "
            "losing anything you have already claimed."
            % (len(owned), _and_list(owned, 6))))
    firsts = []
    for r in specs[0][3]:
        it = (r[1] or {}).get("item") if isinstance(r[1], dict) else None
        nm = _pretty_name(it) if it else ""
        if nm and nm not in firsts:
            firsts.append(nm)
    if len(firsts) >= 3:
        # The second half is claimed only where it is checkable: _row_mods
        # coming back empty means every task in this chapter is in the
        # minecraft namespace. On a pack built without the vanilla chapters it
        # is left unsaid rather than said and wrong.
        tail = (" All of it is plain Minecraft - it is here so the book has "
                "somewhere to start, and so you can see how a quest reads "
                "before a mod you have never played hands you one. The mod "
                "chapters begin after it.") if not _row_mods(specs[0][3]) else ""
        pages.append((
            "What Comes Next",
            # "asks for" is a CHK-11 generator-tail skeleton; "starts with"
            # states the same checkable fact without it.
            "This chapter starts with %s, roughly in that order. It is the "
            "shortest part of the book and the only part you can finish "
            "without touching a mod at all.%s"
            % (_and_list(firsts, 5), tail)))
    # WHAT THE SHAPES MEAN. Quest shape carries the part a quest plays in its
    # chapter - entry, capstone, optional, goal, material, depth - and until
    # this page existed nothing said so, which makes a real signal look like
    # decoration. Emitted only when the shapes are actually derived: with a
    # single shape forced book-wide, or chapter styling off, the legend would
    # describe a book the player is not holding.
    _o = opts or {}
    _forced = _forced_shape(_o)
    if _o.get("style_chapters", True) and not _forced:
        pages.append((
            "What the Shapes Mean",
            "The outline around each quest tells you what it is before you "
            "read a word of it. A square starts a line and needs nothing "
            "first. An octagon ends one - finish it and that thread is done. "
            "A circle is optional and blocks nothing. The rest mark what you "
            "are being asked for: a piece of gear or a machine you are "
            "working toward, or the plain stock you build it out of. Colour "
            "belongs to the chapter's group, not to the quest."))

    # WHERE THE REWARDS COME FROM. Gated on the SAME predicate the crate
    # builder uses further down, so this page appears exactly when crates do.
    # An earlier version guessed from the rows and never fired once: a page
    # describing loot the player will never be handed is the filler the rest
    # of this function refuses to write, and so is a page that is true but
    # invisible.
    try:
        _rp = len(reward_pool(scan))
        _ca = len({i for _s in (scan.get("craftable") or {}).values() for i in _s})
    except Exception:
        _rp = _ca = 0
    if _rp >= 12 or _ca >= 16:
        pages.append((
            "How You Get Paid",
            "Most quests hand you something the moment you claim them, and "
            "some hand you a crate instead. A crate rolls out of a pool built "
            "from this pack's own materials, so what falls out is worth "
            "something in the mods you are actually playing rather than "
            "another stack of the same ore. The richer crates sit deeper in a "
            "chapter, behind the quests that cost you the most to finish, and "
            "you can open them whenever you like - nothing in here spoils."))

    # COLOUR. The group colour is applied per chapter by _theme_for, so this
    # is describing something the player can see on the screen in front of
    # them, not a convention being invented on this page.
    if len(groups) >= 2:
        pages.append((
            "Colour Tells You Where You Are",
            "Every chapter title is coloured by the group it belongs to, and "
            "the tinted card behind each chapter's quests is the same colour "
            "again. Once you have opened one Tech chapter you can find the "
            "rest without reading a single title. The colours mean nothing "
            "about difficulty - they group by what the mods DO, so a green "
            "chapter is not gentler than a blue one, only different."))

    # THE SHAPE OF THE BOOK. Chapter sizes are known here and vary a lot, and
    # a player deciding what to commit to wants the number before the click.
    _sized = sorted(((len(sp[3]), re.sub(r"&.", "", str(sp[1] or "")).strip())
                     for sp in specs[1:] if sp[3]), reverse=True)
    if len(_sized) >= 3 and _sized[0][0] >= 8:
        pages.append((
            "The Big Ones",
            "Chapters are not the same size, and it is worth knowing which "
            "are the long ones before you start: %s. A short chapter is not a "
            "lesser one - it usually means the mod does a few things well. "
            "Nothing obliges you to finish a chapter before opening another, "
            "and the book keeps your progress in all of them at once."
            % _and_list(["%s (%d quests)" % (t, n) for n, t in _sized[:3]], 3)))

    # NOT EVERY QUEST WANTS AN ITEM. Built from the task types actually
    # present in this book, so a pack with no bosses is never told to go and
    # kill one. A player who assumes every quest is a fetch quest reads right
    # past the ones that are not.
    _kinds = set()
    for sp in specs:
        for r in sp[3]:
            t = r[1] if isinstance(r, (list, tuple)) and len(r) > 1 else None
            if isinstance(t, dict):
                _kinds.add(str(t.get("type") or "item"))
    _named = []
    if "item" in _kinds:
        _named.append("hand something over, or just hold it")
    if "kill" in _kinds:
        _named.append("kill a particular thing")
    if "structure" in _kinds:
        _named.append("stand inside a structure you had to find")
    if "dimension" in _kinds:
        _named.append("set foot in another dimension")
    if "advancement" in _kinds:
        _named.append("earn an advancement")
    if "checkmark" in _kinds:
        _named.append("simply be read and ticked, like this one")
    if len(_named) >= 3:
        pages.append((
            "Not Every Quest Wants an Item",
            "Quests in this book can ask you to %s. The shape of the task is "
            "part of the point: a chapter that only ever asked for items "
            "would turn the whole pack into a shopping list, and half of what "
            "these mods added is somewhere to go or something to survive "
            "rather than something to carry home."
            % _and_list(_named, 6)))

    # How many to actually use. CHK-02 needs 0.25 and CHK-08 wants chapter one
    # to be at least half reading, which is how the reference books open: you
    # arrive, you learn how the thing works, and only then does it ask you for
    # something. Target the higher bar, still capped by how many TRUE pages
    # there were to write - a padded welcome is the filler this function
    # exists to keep out, and half of nothing is still nothing.
    n = len(specs[0][3])
    # +1 because "Read This First" is PREPENDED after this line: without it
    # pages[:want] silently drops the last page every time, which is how the
    # shape legend was written, appended, and then thrown away before anyone
    # could read it.
    want = min(len(pages) + 1, max(2, n)) if pages else 0
    # "Read This First" is written last and prepended, because it states the
    # book's own totals and those totals include the front matter itself. A
    # book that miscounts its quests in its own first sentence is the failure
    # mode this project keeps repeating, so the number is settled after `want`.
    nchap = len(specs)
    nq = sum(len(s[3]) for s in specs) + want
    nmods = len({m.get("mod_id") for m in (scan.get("mods") or ())
                 if m.get("mod_id")})
    if nchap and nmods:
        pages.insert(0, (
            "Read This First",
            "You have already done this one - claim it and move on. This book "
            "is a map of what %d mods added to this pack, sorted into %d "
            "chapters and %d quests. It is a map and not a gate: skipping a "
            "quest costs you its reward and nothing else, and nothing in here "
            "expires." % (nmods, nchap, nq)))
    return [(t, {"type": "checkmark"}, _uncoloured(d)) for t, d in pages[:want]]


# A merged chapter holds the mods too small for a chapter of their own. It is
# a real structural need - a mod in the pack and absent from the book is the
# failure this prevents - but "Decoration Miscellany III" advertises the
# bookkeeping instead of the contents, and a player reading five numbered
# Miscellanies is being shown the seams. These read as shelves a pack author
# would write. Enough per group that a pack needing five chunks still gets
# five distinct names, and the pick varies with the run seed.
_MERGE_NAMES = {
    "Tech": ["Gears and Circuits", "The Workshop", "Applied Engineering",
             "Machines and Mechanisms", "The Assembly Line", "Bench and Bus"],
    "Magic": ["Arcane Oddities", "Rites and Relics", "The Hedge Wizard",
              "Whispered Arts", "Charms and Curios", "The Quiet Study"],
    "Adventure": ["Roads Less Travelled", "Far Country", "Into the Wilds",
                  "Expeditions", "Beasts and Bosses", "The Long Way Round"],
    "Farm and Food": ["The Larder", "Field and Table", "Harvest and Hearth",
                      "The Kitchen Garden", "Preserves and Provisions",
                      "Second Helpings"],
    "Utility": ["The Toolbox", "Small Conveniences", "Everyday Carry",
                "Sundries", "Pockets and Packs", "Bits and Pieces"],
    "Decoration": ["Home Comforts", "Furnishings", "Trim and Trappings",
                   "Hearth and Home", "The Decorator's Trade",
                   "Paint and Panelling"],
    "Expansion": ["Further Afield", "New Horizons", "The Frontier",
                  "Odds and Additions", "Uncharted", "Room to Grow"],
    "Vanilla": ["Familiar Ground", "The Basics", "Groundwork",
                "First Principles", "Home Territory", "Common Craft"],
}


def _run_seed(opts: dict) -> int:
    """The seed that makes two runs of the same pack differ. -> int

    Everything else in this app is deliberately deterministic: ids hash from
    stable strings so regenerating never orphans a player's progress. That is
    right for ids and wrong for PROSE - it also meant a pack produced one book
    and only ever that book, so "generate again" was a no-op the user could
    see. This is the one deliberate source of run-to-run variation, kept in
    opts so a run can be reproduced by pinning it.
    """
    v = opts.get("seed")
    if v in (None, "", "auto"):
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(hashlib.sha1(str(v).encode("utf-8")).hexdigest()[:8], 16)


def _merge_title(gname: str, idx: int, total: int, seed: int) -> str:
    """A shelf name for a merged chapter - never "<group> Miscellany N"."""
    pool = _MERGE_NAMES.get(gname)
    if not pool:
        return gname if total == 1 else "%s %s" % (
            gname, _ROMAN[idx] if idx < len(_ROMAN) else str(idx + 1))
    r = random.Random("%s|%d" % (gname, seed))
    order = list(pool)
    r.shuffle(order)
    return order[idx % len(order)]


def local_quest_doc(scan: dict, selected_ids: list, opts: dict) -> dict:
    """Build a complete quest-book JSON from the mod scan alone. Same shape the
    AI is asked for, so it flows through clean_doc / build_chapters unchanged."""
    density = opts.get("density", "normal")
    try:
        budget = int(str(opts.get("target", "")).strip())
    except (TypeError, ValueError):
        budget = DENSITY_MID.get(density, 150)
    reward = opts.get("reward", "standard")
    progression = opts.get("progression", "linear")
    creativity = float(opts.get("creativity", 0.3))
    use_groups = opts.get("groups", True)
    alevel = _aesthetic(opts)
    rmult = {"lean": 0.6, "standard": 1.0, "generous": 1.9}.get(reward, 1.0)
    rng = random.Random("offline/%s/%s/%d" % (density, progression, budget))

    themes = [t.strip() for t in (opts.get("themes") or []) if t.strip()]
    themes_only = bool(themes) and opts.get("themes_only", True)

    sel = set(selected_ids)
    focused = bool(sel) and len(sel) <= 8      # user picked a handful — honour it exactly
    mods = [m for m in scan["mods"] if not sel or m["mod_id"] in sel]
    # libraries add nothing a player can be sent after — unless explicitly picked
    # A library is never a quest subject, even when it is in the selection.
    # "All mods ticked" is not a deliberate choice of GeckoLib, and its only
    # items are dev placeholders - which produced "Opening GeckoLib 4" quests.
    # Explicitly picking a SMALL set still honours the user's intent.
    mods = [m for m in mods
            if not is_library(m["mod_id"], m["name"])
            or (m["mod_id"] in sel and len(sel) <= 8
                and not is_curated_library(m["mod_id"])
                and library_has_real_items(m["mod_id"], scan))]
    # decoration mods are furniture catalogues: hundreds of near-identical blocks
    # that make for filler quests. Off by default; the user can opt in or pick
    # a decoration mod explicitly.
    if not opts.get("include_decor", False):
        # Same deliberateness test as the library filter above: "all mods
        # ticked" fills sel with EVERY mod id, so a bare `in sel` let every
        # decor mod through and include_decor True/False built byte-identical
        # books (self_audit dead-options; md5 9b9d5155 both ways). Only an
        # explicit SMALL selection exempts a decor mod.
        mods = [m for m in mods
                if (m["mod_id"] in sel and len(sel) <= 8)
                or m["category"] != "decor"]
    # mods that ship broken textures render as pink-and-black boxes in the book,
    # so they are excluded even when explicitly selected
    # A RATIO alone is the wrong test on a small mod. Building Gadgets 2 has
    # 8 items, 6 of them craftable and fine, but three stale models left over
    # from a namespace rename put its broken rate at 0.27 - so the whole mod
    # was dropped and selecting it produced an EMPTY BOOK with no warning.
    # Judge on what SURVIVES: a mod with enough good items is usable however
    # much rubbish sits beside them.
    brate = scan.get("broken_rate", {})
    mods = [m for m in mods
            if brate.get(m["mod_id"], 0) <= BROKEN_MOD_LIMIT
            or len(scan.get("craftable", {}).get(m["mod_id"]) or ()) >= 5]
    mods = [m for m in mods
            if scan["items"].get(m["mod_id"]) or scan["entities"].get(m["mod_id"])
            or m["category"] in ("world", "mob")]
    # Silence is the wrong answer to "I picked a mod and got nothing", but
    # this function has no logger - so the reason travels on the document and
    # the caller can show it.
    _dropped = {}
    for _mid in sorted(sel - {m["mod_id"] for m in mods}):
        if _mid in scan.get("items", {}):
            _dropped[_mid] = ("no questable content" if is_library(_mid, _mid)
                              else "too many broken textures, or nothing "
                                   "obtainable")

    def wt(m):
        mid = m["mod_id"]
        craftn = len(scan.get("craftable", {}).get(mid, ())) or \
            len(scan["items"].get(mid, ()))
        mobn = len(_mob_entities(scan["entities"].get(mid, ()),
                                 set(scan["items"].get(mid, ())))) \
            if m["category"] in ("world", "mob") else 0
        return min(40, craftn // 8 + mobn)          # capped so no mod hogs the budget
    cat_order = {"tech": 0, "magic": 1, "world": 2, "mob": 3, "food": 4,
                 "decor": 5, "utility": 6, "unknown": 7}
    mods.sort(key=lambda m: (cat_order.get(m["category"], 9), -wt(m), m["name"].lower()))

    # A mod earns its own chapter only if it has real substance. Everything
    # thinner is merged into a per-group "Miscellany" chapter. Real packs sit
    # around 22 chapters (p75 ~31) — cap near there so the book stays navigable.
    # When the user hand-picked a few mods, every one of them gets its own
    # chapter and nothing else is invented.
    if focused:
        own = list(mods)
        merged = []
    else:
        max_own = {"tiny": 3, "small": 6, "normal": 11, "large": 16,
                   "massive": 22, "colossal": 30}.get(density, 11)
        min_wt = {"tiny": 10, "small": 9, "normal": 8, "large": 7,
                  "massive": 6, "colossal": 5}.get(density, 8)
        own = [m for m in sorted(mods, key=wt, reverse=True)[:max_own] if wt(m) >= min_wt]
        own_ids = {m["mod_id"] for m in own}
        own.sort(key=lambda m: (cat_order.get(m["category"], 9), -wt(m)))
        merged = [m for m in mods if m["mod_id"] not in own_ids]

    # Themed questlines: one chapter per theme, drawn from every selected mod.
    theme_specs = theme_chapters(scan, selected_ids, themes, opts) if themes else []
    if themes_only and theme_specs:
        own, merged = [], []

    # Vanilla chapters are scaffolding for a whole-pack book; when the user
    # focused on a couple of mods or asked for specific themes they'd just be noise.
    include_vanilla = opts.get("vanilla_chapters",
                               not focused and not (themes_only and theme_specs))
    vanilla_specs = _VANILLA_CHAPTERS if include_vanilla else []
    fixed = sum(len(v) for _, _, _, v in vanilla_specs)
    want = max(0, budget - fixed)

    # weight the mod-quest budget across own chapters + merged chapters (one
    # per group, split in two if a group is huge). NOT flattened any more:
    # wt() caps at 40 "so no mod hogs the budget", and feeding those capped
    # weights straight into the allocation made every chapter the same size -
    # measured on the built the reference pack book (2026-08-29): 25 chapters, all
    # of them 5-18 quests, p75/p25 = 1.50 against the authored 2.5+
    # (immersion_spec CHK-19: real books keep small side chapters AND give
    # the headline mod a big one - max chapter > 40 quests). So the
    # allocation weight for an own chapter is the mod's UNCAPPED substance
    # (bounded at 90 so one giant mod still cannot take the whole book);
    # wt() itself keeps its cap for ordering and own-selection.
    def wt_alloc(m):
        mid2 = m["mod_id"]
        craftn = len(scan.get("craftable", {}).get(mid2, ())) or \
            len(scan["items"].get(mid2, ()))
        mobn = len(_mob_entities(scan["entities"].get(mid2, ()),
                                 set(scan["items"].get(mid2, ())))) \
            if m["category"] in ("world", "mob") else 0
        return min(90, craftn // 8 + mobn)
    units = [("own", m, max(6, wt_alloc(m) + 4)) for m in own]
    mgroups: dict = {}
    for m in merged:
        mgroups.setdefault(_GROUP_FOR_CAT.get(m["category"], "Expansion"), []).append(m)
    for gname, ms in mgroups.items():
        ms.sort(key=wt, reverse=True)
        # Chunk by SIZE, not always into two. Splitting a category in half
        # regardless of how big it is meant that on a large pack one chunk
        # held 20+ mods while its quest allocation was 9 to 36, and the
        # chapter fills from the highest-ranked items across the whole chunk -
        # so the mods at the bottom contributed nothing at all. Measured on a
        # 337-mod pack: only 62% of QUESTABLE mods appeared anywhere in the
        # book, against 97% and 94% on two smaller packs, and "Decoration
        # Miscellany" was 9 quests representing a single mod.
        #
        # A mod present in the pack and absent from the book is the failure. A
        # mod with one quest is not. Eight per chapter keeps a Miscellany
        # readable while giving every member room to appear.
        chunks = ([ms] if len(ms) <= 8
                  else [ms[i:i + 8] for i in range(0, len(ms), 8)])
        for ci2, chunk in enumerate(chunks):
            wsum2 = min(40, 4 + sum(max(2, wt(x)) for x in chunk))
            title = _merge_title(gname, ci2, len(chunks), _run_seed(opts))
            units.append(("merge", (gname, title, chunk), wsum2))
    # The floors here (6 per unit) and in the Miscellany share below are each
    # harmless alone and ruinous in series on a small budget: with ~30 units
    # the floors alone allocate 180+ quests, so density=tiny shipped 148
    # against an advertised 40-70 and target=40 shipped 228 (measured on
    # the reference pack, all mods, 2026-08-29 - and target=100 shipped the SAME
    # 228, so small budgets were not merely over, they were ignored). The
    # floors stay - a 3-quest chapter is not a chapter - so the budget is
    # enforced the other way round: keep only as many units as the budget can
    # pay a floor for, deduct the free rows appended later (one opener per
    # chapter plus front matter), and shave the jittered allocation back down
    # so it sums to what is left. A focused selection is exempt from the unit
    # trim: the promise above is that every hand-picked mod gets a chapter.
    minq = 6
    afford = max(1, (want - 5) // (minq + 1))   # +1 pays each chapter's opener
    if not focused and len(units) > afford:
        keep = set(sorted(range(len(units)),
                          key=lambda k: (-units[k][2], k))[:afford])
        # Weight alone drops the SHARED chapters first, because one mod big
        # enough for its own chapter outweighs a chunk of small ones - and a
        # shared chapter is the only home eight mods have. On a small budget
        # that meant most of the pack vanished: measured at density=tiny,
        # 3 of 8 mods absent on one pack and 64 of 76 on another, with quests
        # to spare. Keep the flagships, but not ALL of them at the cost of
        # every small mod in the pack: the lightest kept own-chapters give way
        # to the heaviest shared ones until a quarter of the book (at least
        # one chapter) is breadth.
        if any(u[0] == "merge" for u in units):
            _want_merge = max(1, afford // 4)
            _kept_merge = [k for k in keep if units[k][0] == "merge"]
            if len(_kept_merge) < _want_merge:
                _spare = sorted((k for k in range(len(units))
                                 if k not in keep and units[k][0] == "merge"),
                                key=lambda k: (-units[k][2], k))
                _giveable = sorted((k for k in keep if units[k][0] == "own"),
                                   key=lambda k: (units[k][2], k))
                for _m in _spare[:_want_merge - len(_kept_merge)]:
                    if not _giveable:
                        break
                    keep.discard(_giveable.pop(0))
                    keep.add(_m)
        # A trimmed OWN unit used to take its mod out of the book altogether:
        # the mod was too big to have been put in a shared chunk, and its own
        # chapter just got cut, so it had nowhere left to be. That is the
        # "a mod in the pack and absent from the book" failure arriving by the
        # back door, and it is why density=tiny lost mods with quests to
        # spare. Fold them into a shared chapter of their group instead - one
        # quest each is much better than nothing, which is what a shared
        # chapter is for.
        # Both kinds. A trimmed OWN unit loses one mod; a trimmed SHARED unit
        # loses up to eight at a stroke, which is the larger half of the same
        # failure and the one that was still happening: at density=tiny a pack
        # lost aquaculture, farmersdelight and supplementaries together
        # because the whole shared chapter holding them was cut.
        _homeless = []
        for i in range(len(units)):
            if i in keep:
                continue
            if units[i][0] == "own":
                _homeless.append(units[i][1])
            else:
                _homeless.extend(units[i][1][2])
        units = [u for i, u in enumerate(units) if i in keep]
        for _m in _homeless:
            _g = _GROUP_FOR_CAT.get(_m.get("category"), "Expansion")
            _host = None
            for _u in units:
                if _u[0] == "merge" and _u[1][0] == _g and len(_u[1][2]) < 12:
                    _host = _u
                    break
            if _host is None:
                for _u in units:
                    if _u[0] == "merge" and len(_u[1][2]) < 12:
                        _host = _u
                        break
            if _host is not None:
                _host[1][2].append(_m)
    overhead = ((len(vanilla_specs) + len(theme_specs) + len(units) - 1) + 3
                if alevel["desc_lines"] > 0 else 0)
    want = max(0, want - overhead)
    cap = {"tiny": 20, "small": 30, "normal": 46, "large": 65,
           "massive": 90, "colossal": 130}.get(density, 46)
    # CHK-19, part 2: linear shares compress. With weights 20-90 and a dozen
    # units, want*w/usum lands every chapter within 2x of the mean, which is
    # exactly the p75/p25 = 1.50 wall measured above. Raising the shares to a
    # power > 1 spreads them the way authored books do - the light units drop
    # to their floor (a small side chapter is a feature, not a bug) and the
    # heavy ones buy real depth. Tuned on the built book: 1.0 gives ratio
    # 1.50 (fail), 1.6 gives 1.75 (fail - the mid-weight units still hold
    # too much), 2.2 clears the 2.5 target with the flagship below.
    # Miscellany units take a further discount: they are side rooms by
    # design, and on a normal budget the jitter alone was lifting them a
    # quest or two off their floor - which is exactly the p25 CHK-19's ratio
    # divides by (measured: ten Miscellany chapters at 7, p25 7, ratio 2.29;
    # discounted they rest at the floor, p25 6). On a large budget the
    # discount is a constant factor and they still grow with everyone else.
    pw = {i: (u[2] * (0.75 if u[0] == "merge" else 1.0)) ** 2.2
          for i, u in enumerate(units)}
    usum = sum(pw.values()) or 1
    # CHK-19, part 1: every authored book gives its headline mod one BIG
    # chapter (spec: max chapter > 40 quests; ours capped out at 18). The
    # flagship is the own unit with the most craftable substance - craftable
    # count, not wt(), because a 40+ chapter must be FILLED, and items are
    # what fills reliably (a mob-heavy mod tops out at n//3 kill quests).
    # Only when the budget can spare it: a flagship that IS the book would
    # break the same variance it exists to create.
    flag = -1
    if not focused and want >= 3 * 42 and cap > 42:
        best = -1
        for i, (kind, data, _w) in enumerate(units):
            if kind != "own":
                continue
            craftn = len(scan.get("craftable", {}).get(data["mod_id"], ())) or \
                len(scan["items"].get(data["mod_id"], ()))
            if craftn > best:
                best, flag = craftn, i
    # A Miscellany's floor sits one lower than a mod chapter's: it is a
    # side room by design, and CHK-19's p25 leg needs the small chapters to
    # actually BE small (p25 7 needs p75 17.5+, which a ~300-quest budget
    # cannot pay; p25 6 needs 15+, which it can).
    # A shared chapter's floor has to cover its MEMBERSHIP, not just be small.
    # Chunks hold up to eight mods; at a floor of five, three of them could
    # never appear however the rows were ordered, and a mod present in the
    # pack and absent from the book is the failure this whole arrangement
    # exists to prevent. The floor is still the smallest number that keeps
    # that promise, so shared chapters stay the side rooms CHK-19's p25 leg
    # needs them to be.
    floors = {i: (max(5, len(u[1][2])) if u[0] == "merge" else minq)
              for i, u in enumerate(units)}
    alloc = {}
    for i, u in enumerate(units):
        base = want * pw[i] / usum
        jit = random.Random("alloc/%d/%s" % (i, density)).uniform(0.72, 1.34)
        alloc[i] = max(floors[i], min(cap, round(base * jit)))
    if flag >= 0:
        alloc[flag] = min(cap, max(alloc[flag], 42))
    # Jitter and floors both round up on average; shave the excess back.
    # NOT proportionally to room any more: proportional shave takes the most
    # from the tallest units, which hands the power-law spread above straight
    # back (measured: ratio 1.86 with proportional shave, over the same
    # weights). Reverse water-fill instead - always trim the current tallest
    # non-flagship unit by 1, so the excess comes off the top while the mid
    # tier that CHK-19's p75 leg measures keeps its height, floors intact.
    # The flagship is exempt: shaving it back down would quietly undo the
    # one chapter the max>40 leg is about.
    over = sum(alloc.values()) - want
    while over > 0:
        cand = [i for i in alloc if i != flag and alloc[i] > floors[i]]
        if not cand:
            break
        i = max(cand, key=lambda k: (alloc[k], k))
        alloc[i] -= 1
        over -= 1
    # The shave has a mirror: on a LARGE budget the per-unit cap clamps the
    # heavy units and nothing hands their clipped share to anyone else, so
    # target=1000 shipped 853 once the wheel padding above stopped hiding it.
    # Water-fill in reverse for the same variance reason: the deficit goes to
    # the tallest unit with room below the cap - a bigger budget makes the
    # headline chapters deeper, not every side room wider.
    short = want - sum(alloc.values())
    while short > 0:
        cand = [i for i in alloc if alloc[i] < cap]
        if not cand:
            break
        i = max(cand, key=lambda k: (alloc[k], k))
        alloc[i] += 1
        short -= 1

    specs = []
    for (g, t, ic, vrows) in vanilla_specs:
        specs.append((g, t, ic, [(vt, {"type": "item", "item": vi, "count": vc}, vd)
                                 for (vt, vi, vc, vd) in vrows]))
    specs.extend(theme_specs)
    for i, (kind, data, _w) in enumerate(units):
        n = alloc[i]
        if kind == "own":
            m = data
            rows = _mod_quest_rows(m, scan, n, rng, creativity)
            # collection wheels: satellites around the family's spine item.
            # The hub hint rides on the task dict and is honoured when
            # dependencies are assigned, then stripped.
            if rows and opts.get("collections", True):
                spine = {r[1].get("item") for r in rows if r[1].get("item")}
                # Satellites are seasoning, not the meal. Cap them at roughly
                # half the spine: unbounded they reached 57% of every quest in
                # the book, which is the "filler" failure in a new costume.
                sat_budget = max(4, int(len(rows) * 0.5))
                wheels = _collection_wheels(m["mod_id"], scan, spine, rng)[:sat_budget]
                # This line used to hand _item_title a hardcoded craftable=True
                # and so wrote "Make Lich Trophy" over a boss drop whose
                # researched chain already called it "Head on the Wall" - a
                # nine-book sweep found nine false Craft verbs and every one
                # was written here. _item_title no longer takes a verb from
                # craftability at all, so the whole class is gone.
                for hub in {h for h, _x in wheels if h and h not in spine}:
                    ch = _count_for(hub, rng)
                    rows.append((_item_title(hub, ch, rng),
                                 {"type": "item", "item": hub, "count": ch},
                                 _desc("item", _pretty_name(hub), ch, m["name"],
                                       rng, hub, scan.get("blurbs"), scan)))
                    spine.add(hub)
                for hub, member in wheels:
                    c2 = _count_for(member, rng)
                    task = {"type": "item", "item": member, "count": c2}
                    if hub:
                        task["_wheel_hub"] = hub
                    rows.append((_item_title(member, c2, rng),
                                 task,
                                 _desc("item", _pretty_name(member), c2,
                                       m["name"], rng, member,
                                       scan.get("blurbs"), scan)))
            if rows:
                # Re-run the recipe pass AFTER the wheels are appended. It
                # runs inside _mod_quest_rows, which happens before this, so
                # satellite quests never saw it - a QA pass proved 12 real
                # inversions across 7 mods, the exact Pollidisiac-before-its-
                # Rune case _enforce_recipe_order's own docstring claims to
                # fix. The docstring was true only of spine quests.
                rows = _enforce_recipe_order(rows, scan)
                # Same reason, for the same rows: a mob-named family is
                # exactly the shape _collection_wheels harvests, so all nine
                # Cataclysm disc quests are appended here and never saw the
                # pass inside _mod_quest_rows. It is idempotent - a retitled
                # row no longer matches its own _pretty_name.
                rows = _lead_with_the_mob_name(rows, m["mod_id"], scan)
                # The wheels above land ON TOP of the n this chapter was
                # budgeted, so every own chapter shipped up to 1.5n and the
                # book overshot its band at EVERY density (normal: 426 vs an
                # advertised 250-380). n is the chapter's total, satellites
                # included. Trimming the recipe-ordered tail is safe: a kept
                # satellite whose hub was cut just loses its hub link - the
                # _wheel_hub lookup is by item and tolerates absence.
                rows = rows[:n]
                nm = m["name"]
                if nm.islower() and " " not in nm:
                    nm = nm.replace("_", " ").title()
                specs.append((_GROUP_FOR_CAT.get(m["category"], "Expansion"),
                              nm, None, rows))
        else:
            gname, title, ms = data
            share = max(3, n // max(1, len(ms)))
            # ROUND ROBIN, not concatenate-then-truncate. A shared chapter
            # holds up to eight mods and is allocated as few as five quests;
            # building `share` rows per mod in weight order and then cutting
            # the list to n meant the two heaviest mods ate the whole chapter
            # and the other six appeared nowhere in the book. Measured before
            # this change: 38% of questable mods reached the book on a 338-jar
            # pack, 44% on a 128-jar one - Biomes O' Plenty, Building Gadgets
            # and Chipped all silently absent from a pack that ships them.
            #
            # Interleaved, the truncation keeps each mod's FIRST quest before
            # anyone's second, so a shared chapter represents everyone it is
            # named for. Weight still decides the order within each round, so
            # the biggest mod is still the one that gets a second quest first.
            _per = [_mod_quest_rows(m, scan, share, rng, creativity)
                    for m in sorted(ms, key=wt, reverse=True)]
            rows = []
            for _k in range(max((len(x) for x in _per), default=0)):
                for _x in _per:
                    if _k < len(_x):
                        rows.append(_x[_k])
            if rows:
                # floor 5, matching the Miscellany alloc floor above: with the
                # old max(6, n) a unit allocated its floor of 5 was silently
                # topped back up to 6, which pinned every side chapter at 7
                # once the free opener landed (see the CHK-19 notes above)
                specs.append((gname, title, None, rows[:max(5, n)]))

    # ---- CHK-01 / CHK-02: front matter, and a free opener per chapter ----
    # Placed after the spec loop rather than inside it so the front matter can
    # count the chapters and quests it is describing, and so both kinds of row
    # arrive after _enforce_recipe_order / _lead_with_the_mob_name have already
    # rewritten the item rows they run over. "minimal" writes no prose at all,
    # and an untexted checkmark is the empty box this must never ship, so the
    # whole block is gated on the same desc_lines the chapter intro uses.
    if alevel["desc_lines"] > 0:
        specs = [list(s) for s in specs]
        for si, s in enumerate(specs):
            if si == 0:
                continue             # the first chapter gets the front matter
            op = _chapter_opener_row(s[1], s[3], scan)
            if op:
                s[3] = [op] + list(s[3])
        fm = _front_matter_rows(specs, scan, opts)
        if fm:
            specs[0][3] = fm + list(specs[0][3])
        specs = [tuple(s) for s in specs]

    # ---- weighted crates, built from craftable items across the pack ----
    # Crates pay out from the reward pool - staples plus each mod's real
    # materials. Sampling any craftable id instead filled them with decoration
    # (grime, a painting, a fish tank), which reads as a booby prize.
    rpool = reward_pool(scan)
    _tm = scan.get("tier") or {}
    rpool.sort(key=lambda x: (0 if x.startswith("minecraft:") else 1,
                              _tm.get(x, 5), x))
    rset = set(rpool)
    # CHK-04/05/06: per-namespace payout lists, so a quest can be paid out of
    # the mod it was about instead of by a global index. See payout_lanes().
    lanes = payout_lanes(scan)
    tiers_map = scan.get("tier") or {}
    craft_all = sorted({i for s in scan.get("craftable", {}).values() for i in s})
    rtables, pool_ids = [], []
    if len(rpool) >= 12 or len(craft_all) >= 16:
        rr = random.Random("rtables/" + density)
        good = sorted(set(rpool), key=lambda x: (tiers_map.get(x, 0), x))
        if len(good) < 16:
            picks = rr.sample(craft_all, min(len(craft_all), 120))
            good += [p for p in picks if any(g in p for g in _ITEM_GOOD)]
        span = max(8, len(good) // 4)
        tiers = [("common_crate", "Common Crate", 0, [(1, 8), (4, 4), (8, 2)]),
                 ("uncommon_crate", "Uncommon Crate", 1, [(1, 6), (2, 3), (4, 1)]),
                 ("rare_crate", "Rare Crate", 2, [(1, 5), (2, 2)]),
                 ("vault_crate", "Vault Crate", 3, [(1, 4), (2, 1)])]
        for tid, ttl, k, cw in tiers:
            its = good[k * span:(k + 2) * span] or good
            if not its:
                continue
            rws = [{"item": it, "count": cw[j % len(cw)][0], "weight": cw[j % len(cw)][1]}
                   for j, it in enumerate(its[:14])]
            rws.append({"type": "xp_levels", "xp_levels": 2 + 2 * k, "weight": 3})
            rtables.append({"id": tid, "title": ttl, "loot_size": 1, "rewards": rws})
            pool_ids.append(tid)

    chapters = []
    prev_last = None
    prev_spec = None
    nchap = max(1, len(specs))
    for ci, (grp, title, icon, rows) in enumerate(specs, 1):
        ck = "ch%d" % ci
        ids = ["%sq%d" % (ck, i) for i in range(len(rows))]
        cur_spec = {"group": grp, "title": title, "focus": ""}
        chain_from = prev_last if _same_line(prev_spec, cur_spec) else None
        prev_spec = cur_spec
        # aim for the shape real packs use: ~6 tiers deep, fanning out
        # wheel satellites: depend on their hub, sit outside the tree tiers
        hub_of = {}
        for qi2, (_t2, task2, _d2) in enumerate(rows):
            hub = task2.pop("_wheel_hub", None)
            if hub:
                for qj2, (_t3, task3, _d3) in enumerate(rows):
                    if task3.get("item") == hub:
                        hub_of[qi2] = qj2
                        break
        # Measured: median dependency depth 4, median widest tier 11 - real
        # chapters are about three times wider than they are deep, and the
        # longest single path is only ~15% of the chapter. Targeting depth by
        # sqrt(size) made them far too deep and stringy, which is what drew
        # all those long crossing dependency lines.
        # trunk-and-leaves, per the measured norms (see shape_as_trunk)
        _tdeps, _tleaf = shape_as_trunk(ids, chain_from)
        # CHK-13: loot-table rewards retreat to the chapter FINALE. Authored
        # packs hold table rewards for the end (spec: share <= 0.05 of reward
        # entries, mean dependency-order position >= 0.75); handing a crate to
        # every 10th quest and 5% of ordinary ones put ours at share 0.179,
        # position 0.636. "Finale" is measured the way the scorer measures it:
        # longest-path rank over in-chapter edges (trunk deps plus each wheel
        # satellite -> its hub), ties broken by id string - the LAST quest of
        # dep_order, skipping satellites because their build branch exits
        # before the reward code. Only chapters of >= 12 quests stage a crate:
        # one per chapter on every chapter would land ~25 of ~350 entries
        # (0.07) and overshoot the share cap.
        _edges13 = {qid: list(_tdeps.get(qid, ())) for qid in ids}
        for _si, _hi in hub_of.items():
            _edges13[ids[_si]] = [ids[_hi]]
        _memo13 = {}

        def _rank13(qid, _stk=()):
            if qid in _memo13:
                return _memo13[qid]
            if qid in _stk:        # cycle guard, mirrors the scorer's ranks()
                return 0
            ps = _edges13.get(qid) or ()
            v = 0 if not ps else 1 + max(_rank13(p, _stk + (qid,)) for p in ps)
            _memo13[qid] = v
            return v

        _sats = {ids[_si] for _si in hub_of}
        finale_id = next((qid for qid in sorted(
            ids, key=lambda q: (_rank13(q), q), reverse=True)
            if qid not in _sats), None) if len(rows) >= 12 else None
        # A SHARED chapter holds several mods, so its title cannot tell the
        # player which mod any one quest belongs to. The reward can: paying
        # out of the quest's own namespace is the only cue available in a
        # chapter called "Gears and Circuits". So in a mixed chapter an item
        # task always pays an item, where a single-mod chapter keeps the 2/3
        # mix that matches real packs (there, every quest is that mod anyway,
        # and the variety is worth more than the redundant signal).
        _chap_ns = {str((r[1] or {}).get("item") or "").split(":", 1)[0]
                    for r in rows
                    if isinstance(r[1], dict) and r[1].get("item")}
        _chap_ns.discard("")
        _mixed_chapter = len(_chap_ns) > 1
        quests = []
        for i, (qtitle, task, desc) in enumerate(rows):
            q = {"id": ids[i], "x": 0.0, "y": 0.0, "title": qtitle, "tasks": [task]}
            if i in hub_of:
                q["dependencies"] = [ids[hub_of[i]]]
                # no `optional` here either, for the CHK-16/17 reason spelled
                # out at the trunk branch below. Wheel satellites are ~30% of
                # every wheel chapter, so flagging them left 11 of 25 chapters
                # in the 15-75% band FTBQ has no authored precedent for
                # (CHK-16 0.440) and 10 of 25 reporting completion with a
                # third of the page undone (CHK-17 0.400). A satellite is a
                # spoke of the wheel a player is looking at, not side content.
                quests.append(q)
                if desc:
                    q["description"] = (style_desc(desc) if "&" not in str(desc)
                                        else [desc])
                # this branch returns early, so it needs the guarantee too -
                # satellite quests are exactly the ones a player scrolls past
                if task.get("item"):
                    if not q.get("description"):
                        q["description"] = auto_description(task["item"], scan)
                    q["description"] = ensure_findable(
                        qtitle, q.get("description"), task["item"], scan)
                # CHK-07 (no unpaid chapter tail): this early exit skipped the
                # reward code entirely, so satellites were the only unpaid
                # quests in the book - and dep_order ranks them behind their
                # hub with id ties, so a wheel chapter ended on an unpaid
                # trailing run (measured 2026-08-29: worst run 2, both wheel
                # satellites). Pay the same flat xp an ordinary quest falls
                # back to; xp keeps CHK-04/05 (item-reward measures) untouched.
                q["rewards"] = [{"type": "xp",
                                 "xp": max(10, min(int((15 + 6 * i + 4 * ci)
                                                       * rmult), 400))}]
                continue
            if desc:
                # curated "why" text arrives plain; give it the same colour
                # treatment the AI path is asked for so the book reads alike
                _tip = guide_tip(task.get("item") or "")
                q["description"] = (style_desc(desc, _tip) if "&" not in str(desc)
                                    else [desc])
                if task.get("item"):
                    q["description"] = ensure_findable(
                        qtitle, q["description"], task["item"], scan)
            elif guide_tip(task.get("item") or ""):
                # no prose for this step, but a narrator warned about it -
                # that warning is worth more than a generated sentence
                q["description"] = style_desc("", guide_tip(task.get("item") or ""))
            if task.get("item"):
                if not q.get("description"):
                    q["description"] = auto_description(task["item"], scan)
                # applies to every branch, including quests with no prose at
                # all - a quest with neither a naming title nor a description
                # is the least findable kind there is
                q["description"] = ensure_findable(
                    qtitle, q.get("description"), task["item"], scan)
            # Shape the chapter as a TREE, not a single file. Measured over 545
            # chapters from 70 real packs: median dependency depth 6, widest
            # tier 13, and only 1% are strictly linear. A pure chain is what
            # makes a book feel like a corridor.
            deps = list(_tdeps.get(ids[i], []))
            if deps:
                q["dependencies"] = deps
            # `optional` deliberately NOT set from _tleaf. It is not a leaf
            # property: immersion_spec CHK-16 measured 494 authored chapters and
            # the distribution is bimodal - 57.5% sit at exactly 0% optional,
            # 87.0% at <=15%, and only 1.2% reach the >=75% catalogue pole.
            # Flagging every trunk leaf made `optional` a synonym for `leaf`,
            # and widening the trunk above for CHK-03 makes that strictly worse
            # (wider rows == more leaves): pooled optional went 0.577 -> 0.812,
            # so FTBQ - which counts only non-optional quests toward chapter
            # completion - fired "chapter complete" at a median 21% of the page
            # done, CHK-17 1.000 against a target of 0.15. Take the majority
            # authored pole instead: a generated chapter is a designed path, so
            # completion means what it says. The catalogue pole is not emitted.
            # rewards, tuned to 39-pack averages: item is the #1 reward type,
            # then xp (median ~50); loot-table 'choice' only on milestones.
            is_boss = task["type"] == "kill" and task.get("value", 9) <= 2
            is_last = i == len(rows) - 1
            # the dep-order finale is a milestone even when it is not row-last:
            # it is the quest the crate now waits on (CHK-13, see finale_id)
            is_finale = finale_id is not None and ids[i] == finale_id
            milestone = is_boss or is_last or is_finale or (i > 0 and i % 10 == 0)
            rews = []
            base_xp = int((15 + 6 * i + 4 * ci) * rmult)
            if milestone:
                # crates ONLY on the finale (CHK-13). Bosses and every-10th
                # milestones keep their xp payout below; the boss's vault
                # crate moved to the end of the chapter that contains it.
                if pool_ids and is_finale:
                    tier = pool_ids[min(len(pool_ids) - 1,
                                        1 + (ci % (len(pool_ids) - 1)))]
                    rews.append({"type": "choice", "table": tier})
                if is_boss:
                    rews.append({"type": "xp_levels", "xp_levels": 5})
                elif rng.random() < 0.31:
                    # xp_levels probability rises 0.081 -> 0.307 with depth in
                    # real packs (3.8x). That rise IS the sense of escalation.
                    rews.append({"type": "xp_levels", "xp_levels": 3})
                if not rews or rng.random() < 0.5:
                    # flat magnitude: measured XP median stays ~100 from tier 0
                    # to tier 11. What escalates is the KIND of reward.
                    rews.append({"type": "xp", "xp": min(int(base_xp * 2), 800)})
            else:
                # item is the most common reward type in real packs — lead with it
                if task["type"] == "item" and (_mixed_chapter
                                                or rng.random() < 0.66):
                    # a head start on something coming up, but only when that
                    # item is worth having - otherwise pay out a staple
                    # Pay out of the reward pool. A head start on an upcoming
                    # item is only offered when that item is itself a material
                    # worth stockpiling - "_item_worth" was too loose a gate and
                    # still handed out paintings, braziers and a fish tank.
                    src = None
                    tns = str(task.get("item") or "").split(":", 1)[0]
                    j = min(len(rows) - 1, i + rng.randint(2, 5))
                    fut = rows[j][1]
                    if (fut["type"] == "item" and fut["item"] in rset
                            and fut["item"].split(":", 1)[0] == tns):
                        # A head start still has to be recognisably FOR this
                        # quest. Unrestricted, this branch paid a Miscellany
                        # chapter's mcwwindows task with a graveyard ingot,
                        # which is what drove reward/task namespace lift to
                        # -0.1 points against a within-chapter shuffle null.
                        src = fut["item"]
                    lane = lanes.get(tns) or ()
                    if src is None and lane:
                        # Pay out of the mod the quest was about - see
                        # payout_lanes(). Same progression window as the rpool
                        # draw below, so an opening quest still cannot pay an
                        # endgame item. rpool stays as the fallback for a
                        # namespace that ships nothing worth handing over.
                        prog = (ci + i / max(1, len(rows))) / max(1, nchap)
                        hi = min(len(lane),
                                 max(3, int(len(lane) * min(1.0, 0.35 + prog))))
                        src = lane[rng.randrange(hi)]
                    if src is None and rpool:
                        # Match the payout to how far in the player is. Drawing
                        # from the whole pool handed out a Gaia Ingot for
                        # lighting a torch in the opening chapter, which makes
                        # the whole progression meaningless.
                        prog = (ci + i / max(1, len(rows))) / max(1, nchap)
                        hi = max(6, int(len(rpool) * min(1.0, 0.25 + prog)))
                        lo = max(0, hi - max(8, len(rpool) // 3))
                        src = rpool[lo + rng.randrange(max(1, hi - lo))]
                    if src:
                        rews.append({"type": "item", "item": src,
                                     "count": rng.choice([1, 1, 2, 4])})
                # (a 5% chance of a crate on ordinary quests used to live here;
                # deleted for CHK-13 - mid-chapter tables are exactly what the
                # authored corpus does not do)
                if not rews:
                    rews.append({"type": "xp", "xp": max(10, min(base_xp, 400))})
                elif rng.random() < 0.12:
                    rews.append({"type": "xp", "xp": max(10, min(base_xp, 400))})
            q["rewards"] = rews
            quests.append(q)
        prev_last = ids[-1] if ids else prev_last
        # Which mod is this chapter about? The specs carry no modid, but every
        # task in a mod chapter names one, so take the namespace the chapter's
        # own tasks agree on. A vanilla or themed chapter has no single owner
        # and correctly gets nothing.
        _ns = [str(r[1].get("item") or r[1].get("structure") or "").split(":", 1)[0]
               for r in rows if isinstance(r[1], dict)]
        _ns = [n for n in _ns if n and n != "minecraft"]
        # Exactly one namespace, no threshold. The data splits cleanly: every
        # real mod chapter here is 100% one namespace and every Miscellany
        # chapter is 17-50% across 2-7 mods. A majority rule gave "Magic
        # Miscellany" the description of a Five Nights at Freddy's decor mod
        # on a 44% plurality, which is worse than saying nothing.
        _mid = _ns[0] if _ns and len(set(_ns)) == 1 else ""
        # minimal writes no chapter intro at all, so the setting reaches the doc
        # and not just the chapter builder that consumes it.
        _intro = chapter_intro(_mid, scan) if alevel["desc_lines"] > 0 else ""
        chapters.append({
            "id": ck,
            "group": (grp if use_groups else ""),
            "title": title,
            # First row with an item, not row 0: chapters now open on a free
            # orientation quest (CHK-01), and taking row 0 blindly gave every
            # one of them the "minecraft:book" fallback icon.
            "icon": icon or next((r[1]["item"] for r in rows
                                  if isinstance(r[1], dict) and r[1].get("item")),
                                 "minecraft:book"),
            **({"description": [_intro]} if _intro else {}),
            "quests": quests,
        })

    # BEFORE the prose passes: bulk asks retitle bare item titles ("16x X"),
    # and the subtitle/rationale passes key their work off the final title.
    bulk_ask_pass(chapters, scan)
    fill_blank_descriptions(chapters, scan)
    # AFTER fill_blank: a blank quest first learns how to GET its item, then
    # this appends why it is WANTED. Measured before this pass, 72% of quests
    # whose item feeds >=2 recipes named none of them (check_book
    # no-rationale); the recipe graph knew the answer the whole time.
    rationale_pass(chapters, scan)
    # the glance-hint channel (CHK-10) and the front-loaded entry quest
    # (CHK-12); both add prose, so the tail scrub must come after them
    subtitle_pass(chapters, scan)
    role_length_pass(chapters, scan)
    # LAST: sourced prose (blurbs, guide notes) can carry the five
    # generator-tail skeletons CHK-11 hard-fails on; only a final sweep can
    # guarantee tail_share stays 0.0 on packs nobody has seen
    scrub_generator_tails(chapters)

    return {
        "title": opts.get("book_title") or "Modpack Field Guide",
        "reward_tables": rtables,
        "chapters": chapters,
        # so a caller can say WHY a picked mod produced nothing, instead of
        # handing back an empty book in silence
        "excluded_mods": _dropped,
    }


# ========================================================================== #
#  4. AI client
# ========================================================================== #

PROVIDER_PRESETS = {
    "Gemini (OpenAI-compat)": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-2.0-flash"},
    # Anthropic ships an OpenAI-compatible endpoint, so the same Bearer-auth
    # /chat/completions client works — only the URL and model change.
    "Claude (OpenAI-compat)": {
        "url": "https://api.anthropic.com/v1/chat/completions",
        "model": "claude-haiku-4-5-20251001"},
    "DeepSeek": {"url": "https://api.deepseek.com/chat/completions", "model": "deepseek-chat"},
    "OpenAI": {"url": "https://api.openai.com/v1/chat/completions", "model": "gpt-4o-mini"},
    "Ollama (local)": {"url": "http://localhost:11434/v1/chat/completions", "model": "llama3.1"},
    "Custom": {"url": "", "model": ""},
}


def auth_headers(url: str, api_key: str) -> dict:
    """Auth header for a provider endpoint.

    api.anthropic.com accepts Bearer on /chat/completions but rejects it on
    /v1/models, which needs x-api-key - so "Test connection" 401'd against a
    key that generated fine. Keys are stripped because a copy-paste newline
    rides into the header and fails auth for no visible reason.
    """
    key = (api_key or "").strip()
    if not key:
        return {}
    if "api.anthropic.com" in (url or ""):
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": "Bearer " + key}


def derive_models_url(chat_url: str) -> str:
    return re.sub(r"/chat/completions/?$", "/models", chat_url.strip())


def fetch_models(chat_url: str, api_key: str) -> list:
    if requests is None:
        raise RuntimeError("requests not installed")
    url = derive_models_url(chat_url)
    r = requests.get(url, headers=auth_headers(url, api_key), timeout=(10, 30))
    r.raise_for_status()
    d = r.json()
    rows = d.get("data") or d.get("models") or []
    out = []
    for x in rows:
        if isinstance(x, dict):
            mid = x.get("id") or x.get("name") or x.get("model")
            if mid:
                out.append(str(mid).split("/")[-1])
    return sorted(set(out))


def call_ai(url, api_key, model, prompt, max_tokens, log, stop_flag, temperature=0.4,
            read_timeout=180, max_rounds=4) -> str:
    if requests is None:
        raise RuntimeError("the 'requests' package is not installed  (pip install requests)")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers.update(auth_headers(url, api_key))
    messages = [
        {"role": "system", "content": "You output only valid minified JSON. No markdown fences."},
        {"role": "user", "content": prompt},
    ]
    full = ""
    t0 = time.time()
    for attempt in range(max_rounds):
        if stop_flag():
            raise RuntimeError("cancelled")
        body = {"model": model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        log("  AI call %d (prompt %d chars, cap %d tok) ..."
            % (attempt + 1, len(prompt), max_tokens))
        # Transient provider failures (overloaded / rate-limited) are common on
        # free tiers — back off and retry instead of losing the whole batch.
        r = None
        for retry in range(4):
            if stop_flag():
                raise RuntimeError("cancelled")
            try:
                r = requests.post(url, headers=headers, json=body,
                                  timeout=(10, read_timeout))
            except requests.exceptions.ReadTimeout:
                raise RuntimeError(
                    "the model did not answer within %ds. The request was probably too "
                    "large — lower Max tokens, or use a smaller book/batch." % read_timeout)
            except requests.exceptions.RequestException as e:
                if retry == 3:
                    raise RuntimeError("network error: %s" % e)
                wait = 3 * (retry + 1)
                log("    network hiccup, retrying in %ds (%s)" % (wait, str(e)[:70]))
                time.sleep(wait)
                continue
            if r.status_code in (429, 500, 502, 503, 529) and retry < 3:
                wait = min(30, 4 * (2 ** retry))
                log("    provider busy (HTTP %d) — retry %d/3 in %ds"
                    % (r.status_code, retry + 1, wait))
                time.sleep(wait)
                continue
            break
        if r is None:
            raise RuntimeError("no response from the provider")
        if r.status_code >= 400:
            hint = ""
            if r.status_code in (429, 503):
                hint = ("   The provider is overloaded or rate-limiting you — wait a "
                        "minute and run it again, or switch model/provider.")
            raise RuntimeError("HTTP %d: %s%s" % (r.status_code, r.text[:300], hint))
        choice = r.json()["choices"][0]
        chunk = choice["message"]["content"] or ""
        full += chunk
        finish = choice.get("finish_reason")
        log("    got %d chars in %.0fs (finish_reason=%s)"
            % (len(chunk), time.time() - t0, finish))
        if finish != "length":
            break
        if attempt == max_rounds - 1:
            log("    ! answer still truncated after %d rounds — using what we have"
                % max_rounds)
            break
        messages.append({"role": "assistant", "content": chunk})
        messages.append({"role": "user", "content":
                         "Continue the JSON exactly where you stopped. No repeats, no prose."})
    return full


_MODRINTH_UA = "AutoQuestGen/1.4 (FTB Quests book generator)"
_MODRINTH_CACHE: dict = {}


def modrinth_lookup(mod_id: str, name: str) -> dict | None:
    """Best-effort mod description + icon from the Modrinth API."""
    if requests is None:
        return None
    if mod_id in _MODRINTH_CACHE:
        return _MODRINTH_CACHE[mod_id]
    h = {"User-Agent": _MODRINTH_UA}
    proj = None
    try:
        r = requests.get("https://api.modrinth.com/v2/project/%s" % mod_id, headers=h, timeout=12)
        if r.status_code == 200:
            proj = r.json()
        else:
            s = requests.get("https://api.modrinth.com/v2/search", headers=h, timeout=12,
                             params={"query": name, "limit": 1,
                                     "facets": '[["project_type:mod"]]'})
            hits = s.json().get("hits") if s.status_code == 200 else None
            if hits:
                slug = hits[0]["slug"]
                r2 = requests.get("https://api.modrinth.com/v2/project/%s" % slug, headers=h, timeout=12)
                if r2.status_code == 200:
                    proj = r2.json()
    except Exception:
        proj = None
    if not proj:
        _MODRINTH_CACHE[mod_id] = None
        return None
    info = {"desc": " ".join(str(proj.get("description") or "").split())[:500],
            "title": proj.get("title") or name,
            "url": "https://modrinth.com/mod/%s" % proj.get("slug", mod_id)}
    icon = proj.get("icon_url")
    if icon and icon.lower().endswith(".png"):
        try:
            ib = requests.get(icon, headers=h, timeout=12).content
            if ib[:8] == b"\x89PNG\r\n\x1a\n" and len(ib) < 500000:
                info["logo_bytes"] = ib
        except Exception:
            pass
    _MODRINTH_CACHE[mod_id] = info
    return info


# ========================================================================== #
#  5. Validate / clean
# ========================================================================== #

def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object found in the input")
    blob = m.group(0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        depth = end = 0
        instr = esc = False
        for i, c in enumerate(blob):
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            elif c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
        if end:
            try:
                return json.loads(blob[:end])
            except json.JSONDecodeError:
                pass
        # Last resort: pull out every complete top-level object from the first
        # array we find. A model that fumbles one quest shouldn't cost us the
        # other twenty — salvage what parsed and carry on.
        salvaged = _salvage_objects(blob)
        if salvaged:
            return {"quests": salvaged, "_salvaged": True}
        raise


def _salvage_objects(blob: str) -> list:
    """Every complete {...} inside the outermost [...] that json can still read."""
    start = blob.find("[")
    if start < 0:
        return []
    out, depth, obj_start = [], 0, None
    instr = esc = False
    for i in range(start, len(blob)):
        c = blob[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            continue
        if c == '"':
            instr = True
        elif c == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    out.append(json.loads(blob[obj_start:i + 1]))
                except Exception:
                    pass
                obj_start = None
    return out


def clean_doc(doc: dict, scan: dict, log) -> dict:
    known = set()
    by_ns: dict = {}
    for s in scan.get("items", {}).values():
        known |= s
        for i in s:
            by_ns.setdefault(i.split(":")[0], []).append(i)
    for v in by_ns.values():
        v.sort()

    vanilla = scan.get("vanilla") or set()

    def ok_item(x) -> bool:
        x = str(x)
        if not x or ":" not in x:
            return False
        ns = x.split(":")[0]
        if ns == "minecraft":
            # only trust minecraft: ids we can vouch for — models happily invent
            # "minecraft:brass_ingot" when they mean create:brass_ingot
            return (x in vanilla) if vanilla else True
        if not known:                 # nothing scanned -> can't judge, allow
            return True
        return x in known

    # short name -> real id, so an invented "minecraft:brass_ingot" becomes
    # "create:brass_ingot" rather than something unrelated from the same mod
    by_short: dict = {}
    for _i in known:
        by_short.setdefault(_i.split(":", 1)[1], _i)

    def fix_item(x, seed=""):
        """Return a valid item id: the same item under its real namespace where
        one exists, else something from the same mod, else None."""
        if ok_item(x):
            return str(x)
        xs = str(x)
        short = xs.split(":", 1)[1] if ":" in xs else xs
        if short in by_short:
            return by_short[short]
        ns = xs.split(":")[0] if ":" in xs else ""
        pool = by_ns.get(ns)
        if pool:
            # only stand in something worth wanting - hashing into the raw
            # namespace pool once replaced a bad id with a pressure plate in
            # the middle of the Twilight Forest chapter
            worthy = [i for i in pool if _junk_score(i) < 2] or pool
            return worthy[hash(seed + xs) % len(worthy)]
        return None

    chapters = doc.get("chapters") or []
    all_q = set()
    for ch in chapters:
        for q in ch.get("quests") or []:
            all_q.add(str(q.get("id") or q.get("title")))

    all_entities = set()
    for _s in scan.get("entities", {}).values():
        all_entities |= set(_s)

    swapped = task_drop = rew_drop = dropped = 0
    for ch in chapters:
        if ch.get("icon") and not ok_item(ch["icon"]):
            f = fix_item(ch["icon"], ch.get("id", ""))
            ch["icon"] = f or "minecraft:book"
        for q in ch.get("quests") or []:
            qseed = str(q.get("id") or q.get("title"))
            deps = [d for d in (q.get("dependencies") or []) if str(d) in all_q]
            dropped += len(q.get("dependencies") or []) - len(deps)
            q["dependencies"] = deps

            if q.get("icon") and not ok_item(q["icon"]):
                q["icon"] = fix_item(q["icon"], qseed) or None
                if q["icon"] is None:
                    q.pop("icon", None)

            new_tasks = []
            for tk_ in q.get("tasks") or []:
                ttype = tk_.get("type", "item")
                if ttype == "item":
                    raw = tk_.get("item") or tk_.get("target")
                    if raw and not ok_item(raw):
                        rep = fix_item(raw, qseed)
                        if rep:
                            tk_["item"] = rep
                            tk_.pop("target", None)
                            swapped += 1
                        else:
                            task_drop += 1
                            continue
                elif ttype == "kill":
                    # a kill task whose entity is missing or not a real mob
                    # renders as an unfinishable quest ("kill None")
                    ent = tk_.get("entity")
                    if not isinstance(ent, str) or ent not in all_entities:
                        task_drop += 1
                        continue
                new_tasks.append(tk_)
            q["tasks"] = new_tasks

            if q.get("rewards"):
                keep = []
                for rw in q["rewards"]:
                    if rw.get("type", "item") == "item" and rw.get("item") and not ok_item(rw["item"]):
                        rep = fix_item(rw["item"], qseed + "r")
                        if rep:
                            rw["item"] = rep
                        else:
                            rew_drop += 1
                            continue
                    keep.append(rw)
                q["rewards"] = keep

    # --- two quests, one item -------------------------------------------------
    # Only when BOTH are single-task. A multi-task quest repeating items asked
    # for individually is a capstone ("collect all four gadgets") and is how
    # good packs close a chapter, so those are left alone.
    #
    # The redundant one is DROPPED and its dependents rewired to its parent.
    # Re-pointing it at another item instead looks like a tidy fix but invents
    # nonsense: the title still describes the old item, which is how a quest
    # called "Naga Armorer" ended up asking for an acacia fence.
    repeat = 0
    for ch in chapters:
        quests = ch.get("quests") or []
        taken, keep, remap = set(), [], {}
        for q in quests:
            tasks = q.get("tasks") or []
            if not tasks:                  # nothing left to do -> unfinishable
                deps0 = [d for d in (q.get("dependencies") or [])]
                remap[str(q.get("id"))] = deps0[0] if deps0 else None
                repeat += 1
                continue
            if len(tasks) != 1:
                for t in tasks:
                    if isinstance(t.get("item"), str):
                        taken.add(t["item"])
                keep.append(q)
                continue
            it = tasks[0].get("item")
            if isinstance(it, str) and it in taken:
                deps = [d for d in (q.get("dependencies") or [])]
                remap[str(q.get("id"))] = deps[0] if deps else None
                repeat += 1
                continue
            if isinstance(it, str):
                taken.add(it)
            keep.append(q)
        if remap:
            for q in keep:
                out_deps = []
                for d in (q.get("dependencies") or []):
                    d = str(d)
                    seen_hops = 0
                    while d in remap and seen_hops < 8:
                        d = remap[d]
                        seen_hops += 1
                        if d is None:
                            break
                    if d is not None:
                        out_deps.append(d)
                q["dependencies"] = list(dict.fromkeys(out_deps))
            ch["quests"] = keep
    if repeat:
        log("  ! dropped %d duplicate quest(s)" % repeat)

    # Last, because it needs the final title set: the duplicate-quest pass
    # above can be what removes a collision, and qualifying a title that is
    # about to be dropped helps nobody.
    qualified = _qualify_colliding_structure_titles(doc, scan)
    if qualified:
        log("  named %d structure(s) after their mod to tell them apart" % qualified)

    for tag, n in (("swapped %d bad task item(s) for real ones", swapped),
                   ("dropped %d item task(s) with no valid replacement", task_drop),
                   ("dropped %d reward(s) with a fake item", rew_drop),
                   ("pruned %d broken dependency link(s)", dropped)):
        if n:
            log("  ! " + tag % n)
    return doc


def summarize(doc: dict) -> str:
    chs = doc.get("chapters") or []
    nq = sum(len(c.get("quests") or []) for c in chs)
    groups = sorted({str(c.get("group") or "").strip() for c in chs} - {""})
    tt = {}
    for c in chs:
        for q in c.get("quests") or []:
            for t in q.get("tasks") or []:
                tt[t.get("type", "item")] = tt.get(t.get("type", "item"), 0) + 1
    parts = ["%d chapters" % len(chs), "%d quests" % nq]
    if groups:
        parts.append("groups: " + ", ".join(groups))
    parts.append("tasks: " + ", ".join("%s x%d" % (k, v) for k, v in sorted(tt.items())))
    return " | ".join(parts)


# ========================================================================== #
#  5b. Decorative resource pack (procedural PNG art for chapter backgrounds)
# ========================================================================== #

def _png_encode(size: int, rgba: bytes) -> bytes:
    import struct
    import zlib

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    st = size * 4
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        raw.extend(rgba[y * st:(y + 1) * st])
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def _render_texture(kind: str, size: int = 160) -> bytes:
    ss = 2
    hi = size * ss
    inv = 1.0 / hi
    acc = [0.0] * (size * size * 4)
    rng = random.Random(kind)
    palettes = {
        "nebula": [(0.55, 0.35, 0.95), (0.30, 0.55, 0.95), (0.85, 0.40, 0.75)],
        "runes": [(0.35, 0.9, 1.0)],
    }
    blobs = [(rng.uniform(-0.1, 1.1), rng.uniform(-0.1, 1.1), rng.uniform(0.30, 0.75),
              rng.choice(palettes.get(kind, [(0.6, 0.5, 0.9)])))
             for _ in range(16)]
    stars = [(rng.random(), rng.random(), rng.uniform(0.006, 0.05),
              rng.uniform(0.4, 1.0)) for _ in range(70)]
    sparkles = [(rng.random(), rng.random(), rng.uniform(0.05, 0.13)) for _ in range(4)]

    def px(x, y):
        r = g = b = a = 0.0
        if kind == "starfield":
            for (sx, sy, ss2, br) in stars:
                d = math.hypot(x - sx, y - sy)
                if d < ss2:
                    k = (1 - d / ss2) * br
                    r += k; g += k; b += k * 1.15; a += k
            for (px_, py_, s) in sparkles:
                dx, dy = abs(x - px_), abs(y - py_)
                v = max(max(0.0, 1 - dy / (s * 0.08) - dx / s),
                        max(0.0, 1 - dx / (s * 0.08) - dy / s))
                r += v; g += v; b += v; a += v
        elif kind == "nebula":
            for (bx, by, br, col) in blobs:
                d = math.hypot(x - bx, y - by)
                k = max(0.0, 1 - d / br) ** 1.6 * 0.42
                r += col[0] * k; g += col[1] * k; b += col[2] * k; a += k
            for (sx, sy, ss2, br) in stars[:40]:
                d = math.hypot(x - sx, y - sy)
                if d < ss2 * 0.7:
                    k = (1 - d / (ss2 * 0.7)) * br
                    r += k; g += k; b += k; a += k
        elif kind == "moon":
            cx, cy, mr = 0.5, 0.5, 0.34
            sx2, sy2 = 0.66, 0.40
            dm = math.hypot(x - cx, y - cy)
            ds = math.hypot(x - sx2, y - sy2)
            if dm < mr and ds > mr:
                t = dm / mr
                r += 1.0 - 0.1 * t; g += 0.97 - 0.15 * t; b += 0.86 - 0.2 * t; a += 1.0
            glow = max(0.0, 1 - dm / 0.5) ** 3 * 0.35
            r += glow; g += glow * 0.9; b += glow * 0.6; a += glow
        elif kind == "sparkle":
            cx, cy = 0.5, 0.5
            dx, dy = abs(x - cx), abs(y - cy)
            v = max(max(0.0, 1 - dy / 0.02 - dx / 0.5),
                    max(0.0, 1 - dx / 0.02 - dy / 0.5),
                    max(0.0, 1 - math.hypot(dx, dy) / 0.12))
            r += v; g += v; b += v * 1.1; a += v
        elif kind == "runes":
            cx, cy = 0.5, 0.5
            ang = math.atan2(y - cy, x - cx)
            rad = math.hypot(x - cx, y - cy)
            ring = max(0.0, 1 - abs(rad - 0.38) / 0.03)
            seg = (math.sin(ang * 8) > 0.6)
            v = ring * (0.9 if seg else 0.3)
            r += v * 0.4; g += v * 0.9; b += v; a += v
        elif kind == "glow":
            d = math.hypot(x - 0.5, y - 0.5)
            v = max(0.0, 1 - d / 0.55) ** 2.4 * 0.6
            r += v * 0.7; g += v * 0.6; b += v; a += v
        elif kind == "frame":
            e = min(x, y, 1 - x, 1 - y)
            border = max(0.0, 1 - abs(e - 0.045) / 0.02)
            inner = max(0.0, 1 - abs(e - 0.085) / 0.008) * 0.5
            corner = 1.0 if (min(x, 1 - x) < 0.13 and min(y, 1 - y) < 0.13) else 0.0
            v = max(border, inner) + corner * max(0.0, 1 - abs(e - 0.06) / 0.05)
            r += v * 0.35; g += v * 0.7; b += v; a += min(1.0, v)
        elif kind == "temple":
            # centred stepped-pyramid silhouette, wide base at the bottom
            halfw = 0.10 + y * 0.40
            step = (int(y * 7) % 2) * 0.022
            if 0.14 < y < 0.93 and abs(x - 0.5) < (halfw - step):
                shade = 0.22 + 0.4 * y
                r += shade * 0.85; g += shade * 0.55; b += shade * 1.15; a += 0.8
        elif kind.startswith("emblem"):
            cx, cy = 0.5, 0.5
            ang = math.atan2(y - cy, x - cx)
            rad = math.hypot(x - cx, y - cy)
            col = {"emblem_gear": (0.55, 0.75, 1.0), "emblem_leaf": (0.45, 0.95, 0.5),
                   "emblem_sword": (1.0, 0.75, 0.4)}.get(kind, (0.8, 0.7, 1.0))
            if kind == "emblem_gear":
                teeth = 0.30 + 0.05 * (math.cos(ang * 8) > 0)
                v = max(0.0, 1 - abs(rad - teeth) / 0.05) + (0.6 if rad < 0.16 else 0.0)
            elif kind == "emblem_leaf":
                v = max(0.0, 1 - abs(rad - 0.28 * (0.6 + 0.4 * math.cos(ang * 2))) / 0.06)
                v += 0.5 if rad < 0.05 else 0.0
            else:  # sword
                blade = abs(x - 0.5) < 0.05 and 0.12 < y < 0.8
                guard = abs(y - 0.68) < 0.04 and abs(x - 0.5) < 0.18
                v = 1.0 if (blade or guard) else 0.0
            r += col[0] * v; g += col[1] * v; b += col[2] * v; a += min(1.0, v)
        return (min(1.0, r), min(1.0, g), min(1.0, b), min(1.0, a))

    for j in range(hi):
        y = (j + 0.5) * inv
        oy = j // ss
        for i in range(hi):
            x = (i + 0.5) * inv
            ox = i // ss
            r, g, b, a = px(x, y)
            k = (oy * size + ox) * 4
            acc[k] += r * a; acc[k + 1] += g * a; acc[k + 2] += b * a; acc[k + 3] += a
    n = ss * ss
    out = bytearray(size * size * 4)
    for p in range(size * size):
        a = acc[p * 4 + 3] / n
        if a > 1e-6:
            out[p * 4] = int(min(1.0, acc[p * 4] / n / a) * 255)
            out[p * 4 + 1] = int(min(1.0, acc[p * 4 + 1] / n / a) * 255)
            out[p * 4 + 2] = int(min(1.0, acc[p * 4 + 2] / n / a) * 255)
        out[p * 4 + 3] = int(min(1.0, a) * 255)
    return _png_encode(size, bytes(out))


# public-domain 8x8 bitmap font (dhepper/font8x8 basic subset), LSB = leftmost bit
_FONT8 = {
    " ": "0000000000000000", "!": "183C3C1818001800", "'": "0C06030000000000",
    ",": "000000000E0E0600", "-": "0000003F00000000", ".": "0000000018180000",
    ":": "0018180000181800", "/": "6030180C06030100", "&": "1C36361C6E3B336E",
    "0": "3E63737B6F673E00", "1": "0C0E0C0C0C0C3F00", "2": "1E33301C06333F00",
    "3": "1E33301C3033 1E00", "4": "383C36337F307800", "5": "3F031F3030331E00",
    "6": "1C06031F33331E00", "7": "3F333018 0C0C0C00", "8": "1E33331E33331E00",
    "9": "1E33333E30180E00",
    "A": "0C1E33333F333300", "B": "3F66663E66663F00", "C": "3C660303 03663C00",
    "D": "1F36666666361F00", "E": "7F46161E16467F00", "F": "7F46161E16060F00",
    "G": "3C660303 73667C00", "H": "3333333F33333300", "I": "1E0C0C0C0C0C1E00",
    "J": "7830303033331E00", "K": "6766361E36666700", "L": "0F06060646667F00",
    "M": "63777F7F6B636300", "N": "63676F7B73636300", "O": "1C36636363361C00",
    "P": "3F66663E06060F00", "Q": "1E3333333B1E3800", "R": "3F66663E36666700",
    "S": "1E33070E38331E00", "T": "3F2D0C0C0C0C1E00", "U": "3333333333333F00",
    "V": "33333333331E0C00", "W": "6363636B7F776300", "X": "6363361C1C366300",
    "Y": "3333331E0C0C1E00", "Z": "7F633118 4C667F00",
}
_FONT8 = {k: v.replace(" ", "") for k, v in _FONT8.items()}


def _glyph_rows(ch):
    hexs = _FONT8.get(ch.upper(), _FONT8[" "])
    return [int(hexs[i:i + 2], 16) for i in range(0, 16, 2)]


def _render_banner(text: str, scale: int = 7,
                   colour=(255, 233, 150), outline=(18, 12, 40)) -> bytes:
    text = re.sub(r"\s+", " ", str(text).strip().upper())[:22]
    cw, ch = 8, 8            # glyph advance / height (1px gap between 7-wide glyphs)
    pad = scale
    W = len(text) * cw * scale + pad * 2
    H = ch * scale + pad * 2
    buf = bytearray(W * H * 4)

    def put(px, py, col, aa=255):
        if 0 <= px < W and 0 <= py < H:
            k = (py * W + px) * 4
            buf[k], buf[k + 1], buf[k + 2], buf[k + 3] = col[0], col[1], col[2], aa

    for gi, chx in enumerate(text):
        rows = _glyph_rows(chx)
        ox = pad + gi * cw * scale
        for ry, bits in enumerate(rows):
            for rx in range(8):
                if bits & (1 << rx):
                    for sy in range(scale):
                        for sx in range(scale):
                            put(ox + rx * scale + sx, pad + ry * scale + sy, colour)
    # outline pass: any transparent pixel touching a filled one -> outline colour
    src = bytes(buf)
    for y in range(H):
        for x in range(W):
            k = (y * W + x) * 4
            if src[k + 3]:
                continue
            hit = False
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and src[(ny * W + nx) * 4 + 3]:
                        hit = True
            if hit:
                buf[k], buf[k + 1], buf[k + 2], buf[k + 3] = outline[0], outline[1], outline[2], 255
    return _png_encode_rect(W, H, bytes(buf))


def _png_encode_rect(w: int, h: int, rgba: bytes) -> bytes:
    import struct
    import zlib

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    st = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(rgba[y * st:(y + 1) * st])
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


_BANNER_COLOURS = {
    "e": (255, 233, 150), "6": (255, 190, 90), "b": (140, 210, 255), "d": (230, 160, 240),
    "a": (150, 235, 150), "c": (240, 120, 120), "9": (130, 160, 245), "7": (200, 200, 210),
}


def _res_filename(res: str) -> str:
    return res.split("/")[-1].rsplit(".", 1)[0]


def build_decor_pack(dest_zip: Path, banners: dict | None = None, force: bool = False):
    """Resource pack of decorative GUI textures + optional text-banner images.
    `banners` maps 'ftbqdecor:...banner_<slug>.png' -> (text, colour_code)."""
    import zipfile as _zf
    if dest_zip.exists() and dest_zip.stat().st_size > 2000 and not force and not banners:
        return
    mcmeta = json.dumps({"pack": {"pack_format": 15,
                                  "description": "FTBQ-Decor - quest chapter art"}})
    with _zf.ZipFile(dest_zip, "w", _zf.ZIP_DEFLATED) as z:
        z.writestr("pack.mcmeta", mcmeta)
        for res in set(DECOR_IMAGE_PRESETS.values()):
            fn = _res_filename(res)
            z.writestr("assets/ftbqdecor/textures/gui/%s.png" % fn, _render_texture(fn, 160))
        for res, val in (banners or {}).items():
            text, code = (val if isinstance(val, tuple) else (val, "e"))
            col = _BANNER_COLOURS.get(code, _BANNER_COLOURS["e"])
            z.writestr("assets/ftbqdecor/textures/gui/%s.png" % _res_filename(res),
                       _render_banner(text, colour=col))


# ========================================================================== #
#  6. Environment helpers
# ========================================================================== #

LIBRARY_DIR = DATA_DIR / "library"
_BOOK_PARTS = ("chapters", "reward_tables", "chapter_groups.snbt", "data.snbt")


def _book_stats(book_dir: Path) -> tuple:
    """(chapters, quests) actually present in a saved or live book."""
    ch_dir = book_dir / "chapters"
    n_ch = n_q = 0
    for f in sorted(ch_dir.glob("*.snbt")) if ch_dir.is_dir() else []:
        n_ch += 1
        try:
            d = snbt_loads(f.read_text(encoding="utf-8"))
            n_q += len(d.get("quests") or []) if isinstance(d, dict) else 0
        except Exception:
            pass
    return n_ch, n_q


def library_books() -> list:
    """Saved books, newest first. -> [(name, saved_at, chapters, quests)]"""
    out = []
    if not LIBRARY_DIR.is_dir():
        return out
    for d in LIBRARY_DIR.iterdir():
        if not d.is_dir():
            continue
        ch, q = _book_stats(d)
        try:
            when = time.strftime("%Y-%m-%d %H:%M",
                                 time.localtime(d.stat().st_mtime))
        except OSError:
            when = "?"
        out.append((d.name, when, ch, q))
    return sorted(out, key=lambda r: r[1], reverse=True)


def save_book_to_library(quests_dir: Path, name: str, log=lambda *_a: None) -> Path:
    """Snapshot a written book so a good one can be kept and put back.

    Every build differs - reward picks especially - so before this existed a
    book you liked was unrecoverable the moment you generated another. The
    timestamped chapters.backup-* folders were the closest thing, and they are
    unnamed, invisible in the UI and deleted by Clean old backups.
    """
    safe = re.sub(r"[^A-Za-z0-9 ._-]", "_", (name or "").strip())[:60] or "book"
    dest = LIBRARY_DIR / safe
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for part in _BOOK_PARTS:
        src = quests_dir / part
        if src.is_dir():
            shutil.copytree(src, dest / part)
        elif src.is_file():
            shutil.copy2(src, dest / part)
    log("  saved '%s' to the library" % safe)
    return dest


def restore_book_from_library(name: str, quests_dir: Path,
                              log=lambda *_a: None) -> None:
    """Put a saved book back. Backs up whatever is there first.

    Guarded like every other write: FTB Quests holds the book in memory while
    the game runs and would overwrite this on exit.
    """
    src = LIBRARY_DIR / name
    if not src.is_dir():
        raise RuntimeError("no saved book called %r" % name)
    if minecraft_running():
        raise RuntimeError(
            "Minecraft is RUNNING. Close the game before restoring, or FTB "
            "Quests will overwrite the restored book when it exits.")
    quests_dir.mkdir(parents=True, exist_ok=True)
    live_ch = quests_dir / "chapters"
    if live_ch.is_dir() and any(live_ch.iterdir()):
        bak = quests_dir / ("chapters.backup-%s" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copytree(live_ch, bak)
        log("  backed up the current chapters/ -> %s" % bak.name)
    for part in _BOOK_PARTS:
        tgt = quests_dir / part
        s_part = src / part
        if not s_part.exists():
            continue
        if tgt.is_dir():
            shutil.rmtree(tgt)
        elif tgt.is_file():
            tgt.unlink()
        if s_part.is_dir():
            shutil.copytree(s_part, tgt)
        else:
            shutil.copy2(s_part, tgt)
    log("  restored '%s'" % name)


def discover_instances() -> list:
    """Every Minecraft instance this machine's launchers know about, as
    (label, mods_dir) pairs, newest-played first.

    Nothing here is this-machine-specific: every root is derived from the
    running user's home / %APPDATA%. Launchers covered: CurseForge, the
    Modrinth App (current and legacy Theseus id), Prism / PolyMC / MultiMC
    (instances hold a .minecraft or minecraft subfolder), GDLauncher, and the
    vanilla .minecraft. A launcher someone installed to a custom drive is
    invisible here - that is what the Browse button is for.
    """
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", str(home / "AppData/Roaming")))
    found: list = []
    seen: set = set()

    def add(label_prefix: str, inst_dir: Path, mods: Path):
        try:
            if not mods.is_dir():
                return
            key = str(mods.resolve()).lower()
            if key in seen:
                return
            seen.add(key)
            found.append(("%s: %s" % (label_prefix, inst_dir.name),
                          str(mods), mods.stat().st_mtime))
        except Exception:
            pass

    def scan_root(label: str, root: Path, sub: tuple = ("mods",)):
        try:
            if not root.is_dir():
                return
            for p in root.iterdir():
                if p.is_dir():
                    add(label, p, p.joinpath(*sub))
        except Exception:
            pass

    scan_root("CurseForge", home / "curseforge/minecraft/Instances")
    scan_root("Modrinth", appdata / "ModrinthApp/profiles")
    scan_root("Modrinth", appdata / "com.modrinth.theseus/profiles")
    for launcher in ("PrismLauncher", "PolyMC", "MultiMC"):
        scan_root(launcher, appdata / launcher / "instances", (".minecraft", "mods"))
        scan_root(launcher, appdata / launcher / "instances", ("minecraft", "mods"))
    scan_root("GDLauncher", appdata / "gdlauncher_next/instances")
    add("Vanilla", appdata / ".minecraft", appdata / ".minecraft/mods")

    found.sort(key=lambda r: -r[2])
    return [(label, path) for label, path, _mt in found]


def _guess_mods_dir() -> str:
    insts = discover_instances()
    return insts[0][1] if insts else ""


def _quests_dir_for(mods_dir: str) -> str:
    """The quests folder that belongs to THIS mods folder. -> str

    Every instance keeps its book beside its mods, so the output must be
    derived from whichever pack is currently selected. Deriving it once at
    startup instead let the wizard write one pack's book into ANOTHER pack's
    live folder when the user changed instance mid-flow, which a release gate
    called a blocker - correctly, since the destination is somebody's saved
    game."""
    md = str(mods_dir or "").strip()
    return str(Path(md).parent / "config/ftbquests/quests") if md else ""


def _guess_quests_dir() -> str:
    return _quests_dir_for(_guess_mods_dir())


# ========================================================================== #
#  7. Theme
# ========================================================================== #

PALETTE = {
    "bg": "#14161f", "surface": "#1c1f2b", "surface2": "#262a3a", "text": "#e8eaf2",
    "muted": "#8b90a6", "accent": "#8a7dff", "accent_h": "#7a6cf0", "border": "#333749",
    "ok": "#5ec48a", "warn": "#e6b35c", "err": "#e56b6b", "gold": "#e9dcb4",
    "night_top": "#2b2f52", "night_bot": "#0e1024",
}


def apply_theme(root: tk.Tk):
    p = PALETTE
    st = ttk.Style(root)
    st.theme_use("clam")
    root.configure(bg=p["bg"])
    root.option_add("*Font", ("Segoe UI", 10))
    st.configure(".", background=p["bg"], foreground=p["text"], fieldbackground=p["surface2"],
                 bordercolor=p["border"], lightcolor=p["surface"], darkcolor=p["surface"],
                 troughcolor=p["surface2"], focuscolor=p["accent"], insertcolor=p["text"])
    st.configure("TFrame", background=p["bg"])
    st.configure("Card.TFrame", background=p["surface"])
    st.configure("TLabel", background=p["bg"], foreground=p["text"])
    st.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
    st.configure("Card.TLabel", background=p["surface"], foreground=p["text"])
    st.configure("CardMuted.TLabel", background=p["surface"], foreground=p["muted"])
    st.configure("Head.TLabel", background=p["bg"], foreground=p["text"],
                 font=("Segoe UI Semibold", 11))
    st.configure("Status.TLabel", background=p["surface"], foreground=p["muted"])
    st.configure("TLabelframe", background=p["surface"], bordercolor=p["border"], relief="solid",
                 borderwidth=1, padding=10)
    st.configure("TLabelframe.Label", background=p["surface"], foreground=p["accent"],
                 font=("Segoe UI Semibold", 9))
    for base in ("TButton", "Card.TButton"):
        st.configure(base, background=p["surface2"], foreground=p["text"], bordercolor=p["border"],
                     focusthickness=0, padding=(13, 7), relief="flat", anchor="center")
        st.map(base, background=[("active", "#333850"), ("disabled", p["surface"])],
               foreground=[("disabled", p["muted"])])
    st.configure("Accent.TButton", background=p["accent"], foreground="#ffffff", padding=(17, 8),
                 font=("Segoe UI Semibold", 10))
    st.map("Accent.TButton", background=[("active", p["accent_h"]), ("disabled", p["border"])],
           foreground=[("disabled", p["muted"])])
    st.configure("Warn.TButton", background="#5a3b2a", foreground=p["gold"], padding=(15, 8))
    st.map("Warn.TButton", background=[("active", "#6e4632")])
    st.configure("TEntry", fieldbackground=p["surface2"], foreground=p["text"],
                 bordercolor=p["border"], padding=6, relief="flat")
    st.map("TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])])
    st.configure("TCombobox", fieldbackground=p["surface2"], foreground=p["text"],
                 background=p["surface2"], arrowcolor=p["text"], bordercolor=p["border"], padding=5)
    st.map("TCombobox", fieldbackground=[("readonly", p["surface2"])],
           bordercolor=[("focus", p["accent"])])
    root.option_add("*TCombobox*Listbox.background", p["surface2"])
    root.option_add("*TCombobox*Listbox.foreground", p["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    for base in ("TCheckbutton", "Card.TCheckbutton"):
        st.configure(base, background=p["surface"], foreground=p["text"], focuscolor=p["surface"])
        st.map(base, background=[("active", p["surface"])],
               indicatorcolor=[("selected", p["accent"]), ("!selected", p["surface2"])])
    st.configure("TCheckbutton", background=p["bg"], focuscolor=p["bg"])
    st.map("TCheckbutton", background=[("active", p["bg"])])
    st.configure("TScale", background=p["surface"], troughcolor=p["surface2"])
    st.configure("TNotebook", background=p["bg"], bordercolor=p["border"], tabmargins=(0, 8, 0, 0))
    st.configure("TNotebook.Tab", background=p["surface"], foreground=p["muted"],
                 padding=(20, 10), bordercolor=p["border"], font=("Segoe UI Semibold", 10))
    st.map("TNotebook.Tab", background=[("selected", p["accent"])],
           foreground=[("selected", "#ffffff")], expand=[("selected", (0, 0, 0, 0))])
    st.configure("TProgressbar", background=p["accent"], troughcolor=p["surface2"],
                 bordercolor=p["border"], thickness=6)
    st.configure("Vertical.TScrollbar", background=p["surface2"], troughcolor=p["bg"],
                 arrowcolor=p["muted"], bordercolor=p["border"])
    st.configure("Treeview", background=p["surface2"], fieldbackground=p["surface2"],
                 foreground=p["text"], bordercolor=p["border"], rowheight=24)
    st.map("Treeview", background=[("selected", p["accent"])], foreground=[("selected", "#ffffff")])
    st.configure("Treeview.Heading", background=p["surface"], foreground=p["muted"], relief="flat")


class HeaderBanner(tk.Canvas):
    """Night-sky banner: gradient, drifting stars, a moon, the app title."""

    def __init__(self, parent, height=76):
        super().__init__(parent, height=height, highlightthickness=0, bd=0,
                         bg=PALETTE["night_bot"])
        self.h = height
        self._stars = [(random.random(), random.random(), random.uniform(0.6, 1.8),
                        random.uniform(0.3, 1.0), random.random() * 6.28) for _ in range(46)]
        self._phase = 0.0
        self._shooters = []      # [x, y, vx, vy, life]  life 1.0 -> 0.0
        self.bind("<Configure>", lambda e: self._draw())
        self._animate()

    def _animate(self):
        self._phase += 0.05
        w = self.winfo_width() or 900
        # occasionally launch a shooting star from the upper area, streaking down-right
        if len(self._shooters) < 2 and random.random() < 0.03:
            self._shooters.append([
                random.uniform(-40, w * 0.5), random.uniform(-6, self.h * 0.5),
                random.uniform(9, 15), random.uniform(2.5, 5.0), 1.0])
        for sh in self._shooters:
            sh[0] += sh[2]
            sh[1] += sh[3]
            sh[4] -= 0.045
        self._shooters = [s for s in self._shooters
                          if s[4] > 0 and s[0] < w + 60 and s[1] < self.h + 40]
        self._draw()
        self.after(45, self._animate)

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or 900
        h = self.h
        # vertical gradient
        steps = 24
        c1 = tuple(int(PALETTE["night_top"][i:i + 2], 16) for i in (1, 3, 5))
        c2 = tuple(int(PALETTE["night_bot"][i:i + 2], 16) for i in (1, 3, 5))
        for s in range(steps):
            t = s / steps
            col = "#%02x%02x%02x" % tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
            self.create_rectangle(0, h * t, w, h * (t + 1 / steps) + 1, fill=col, outline="")
        # stars
        for (sx, sy, sr, br, ph) in self._stars:
            tw = 0.5 + 0.5 * math.sin(self._phase + ph)
            x = (sx * w + self._phase * 6 * br) % (w + 20) - 10
            y = sy * h
            g = int(120 + 135 * br * tw)
            self.create_oval(x - sr, y - sr, x + sr, y + sr,
                             fill="#%02x%02x%02x" % (g, g, min(255, g + 20)), outline="")
        # shooting stars (head + fading trail)
        for (sx, sy, vx, vy, life) in self._shooters:
            L = 7
            for k in range(L):
                tx, ty = sx - vx * k * 0.55, sy - vy * k * 0.55
                a = life * (1 - k / L)
                g = int(90 + 165 * a)
                self.create_line(tx, ty, tx - vx * 0.4, ty - vy * 0.4,
                                 fill="#%02x%02x%02x" % (g, g, min(255, g + 30)),
                                 width=max(1, 2.4 * a))
            hg = int(200 + 55 * life)
            self.create_oval(sx - 2, sy - 2, sx + 2, sy + 2,
                             fill="#%02x%02x%02x" % (hg, hg, 255), outline="")
        # moon
        mx, my, mr = 42, h / 2, 20
        self.create_oval(mx - mr - 6, my - mr - 6, mx + mr + 6, my + mr + 6,
                         fill="", outline="#3a3f6a")
        self.create_oval(mx - mr, my - mr, mx + mr, my + mr, fill=PALETTE["gold"], outline="")
        self.create_oval(mx - mr + 9, my - mr - 3, mx + mr + 9, my + mr - 3,
                         fill=PALETTE["night_top"], outline="")
        # title
        self.create_text(84, my - 9, anchor="w", text=APP_NAME,
                         fill="#ffffff", font=("Segoe UI Semibold", 16))
        self.create_text(85, my + 13, anchor="w",
                         text="AI-built quest books for FTB Quests  -  MC 1.20.1",
                         fill="#b9bcd8", font=("Segoe UI", 9))
        self.mc_badge_id = self.create_text(w - 14, my, anchor="e", text="",
                                            fill=PALETTE["warn"], font=("Consolas", 9))

    def set_badge(self, text):
        try:
            self.itemconfigure(self.mc_badge_id, text=text)
        except Exception:
            pass


def hover_lift(widget, normal="TButton", hover="Card.TButton"):
    widget.bind("<Enter>", lambda e: widget.configure(style=hover))
    widget.bind("<Leave>", lambda e: widget.configure(style=normal))


_CODE_HEX = {
    "0": "#000000", "1": "#0000aa", "2": "#00aa00", "3": "#00aaaa", "4": "#aa0000",
    "5": "#aa00aa", "6": "#ffaa00", "7": "#aaaaaa", "8": "#555555", "9": "#5555ff",
    "a": "#55ff55", "b": "#55ffff", "c": "#ff5555", "d": "#ff55ff", "e": "#ffff55", "f": "#ffffff",
}

# a mock chapter used only for the live preview
_PREVIEW_QUESTS = [
    {"id": "q1", "tasks": [{"type": "item"}], "dependencies": [], "x": 0, "y": 0},
    {"id": "q2", "tasks": [{"type": "item"}], "dependencies": ["q1"], "x": 2, "y": 0},
    {"id": "q3", "tasks": [{"type": "kill"}], "dependencies": ["q2"], "x": 4, "y": 0},
    {"id": "q4", "tasks": [{"type": "item"}, {"type": "item"}, {"type": "item"}],
     "dependencies": ["q3"], "x": 6, "y": 0},
    {"id": "q5", "tasks": [{"type": "checkmark"}], "dependencies": ["q2"], "x": 2, "y": -2},
    {"id": "q6", "tasks": [{"type": "dimension"}], "dependencies": ["q3"], "x": 4, "y": 2},
    {"id": "q7", "tasks": [{"type": "item"}], "dependencies": ["q4"], "x": 8, "y": 0},
    {"id": "q8", "tasks": [{"type": "kill"}], "dependencies": ["q7"], "x": 10, "y": 0},
    {"id": "q9", "tasks": [{"type": "item"}], "dependencies": ["q5"], "x": 2, "y": -4},
    {"id": "q10", "tasks": [{"type": "item"}], "dependencies": ["q8"], "x": 12, "y": 0},
    {"id": "q11", "tasks": [{"type": "item"}], "dependencies": ["q6"], "x": 6, "y": 3},
    {"id": "q12", "tasks": [{"type": "checkmark"}], "dependencies": ["q10", "q11"], "x": 12, "y": 2},
]


def _shape_points(cx, cy, r, shape):
    import math as _m
    n = {"pentagon": 5, "hexagon": 6, "heptagon": 7, "octagon": 8,
         "gear": 6, "diamond": 4}.get(shape)
    if shape == "circle" or shape is None or shape in ("", "default"):
        return None  # caller draws an oval
    if shape in ("square", "rsquare"):
        return [cx - r, cy - r, cx + r, cy - r, cx + r, cy + r, cx - r, cy + r]
    if not n:
        return [cx - r, cy - r, cx + r, cy - r, cx + r, cy + r, cx - r, cy + r]
    pts = []
    off = _m.pi / 2 if shape == "diamond" else (-_m.pi / 2)
    for i in range(n):
        a = off + i * 2 * _m.pi / n
        rr = r * (1.25 if (shape == "gear" and i % 2 == 0) else 1.0)
        pts += [cx + rr * _m.cos(a), cy + rr * _m.sin(a)]
    return pts


class QuestPreview(tk.Canvas):
    """A live mock of one chapter's quest map for the current options."""

    def __init__(self, parent, height=250):
        super().__init__(parent, height=height, highlightthickness=1,
                         highlightbackground=PALETTE["border"], bd=0, bg=PALETTE["surface"])
        self.h = height
        self._opts = {}
        self.bind("<Configure>", lambda e: self.render(self._opts))

    def render(self, opts):
        self._opts = opts or {}
        o = self._opts
        self.delete("all")
        W = self.winfo_width() or 600
        H = self.h
        decor = o.get("decor_art")
        banners = o.get("banners")
        styled = o.get("style_chapters")
        layout = o.get("layout", "line")
        grp = o.get("group", "Adventure")
        code, gshape, emblem, backdrop = _theme_for(grp, 0)
        # Mirror build_chapters: a forced quest shape wins, styled or not.
        forced = _forced_shape(o)
        if forced:
            gshape = forced

        # ---- background ----
        if decor:
            c1 = tuple(int(PALETTE["night_top"][i:i + 2], 16) for i in (1, 3, 5))
            c2 = tuple(int(PALETTE["night_bot"][i:i + 2], 16) for i in (1, 3, 5))
            for s in range(20):
                t = s / 20
                col = "#%02x%02x%02x" % tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
                self.create_rectangle(0, H * t, W, H * (t + 1 / 20) + 1, fill=col, outline="")
            rng = random.Random("prev/" + backdrop)
            if backdrop in ("starfield", "glow", "nebula"):
                for _ in range(40):
                    x, y = rng.random() * W, rng.random() * H
                    self.create_oval(x, y, x + 1.6, y + 1.6, fill="#8890c0", outline="")
            if backdrop == "temple":
                self.create_polygon(W / 2, H * 0.2, W * 0.78, H * 0.9, W * 0.22, H * 0.9,
                                    fill="#2c2f52", outline="")
            if backdrop == "nebula":
                for _ in range(4):
                    x, y = rng.random() * W, rng.random() * H
                    self.create_oval(x - 60, y - 40, x + 60, y + 40, fill="#3a2f66", outline="")
        else:
            self.create_rectangle(0, 0, W, H, fill=PALETTE["surface"], outline="")

        pad = 16
        if decor:
            self.create_rectangle(pad, pad + 22, W - pad, H - pad, outline="#5a5f9c", width=2)
            es = 16
            pts = _shape_points(pad + 22, H - pad - 12, es * 0.6, "hexagon" if emblem == "gear" else "pentagon")
            self.create_polygon(pts, fill="#6b74c8", outline="")

        # ---- title / banner ----
        title = "Adventure Chapter"
        tcol = _CODE_HEX.get(code, "#ffffff") if styled else PALETTE["text"]
        if banners:
            tw = len(title) * 9 + 20
            self.create_rectangle(W / 2 - tw / 2, 6, W / 2 + tw / 2, 26,
                                  fill="#12142b", outline=tcol)
            self.create_text(W / 2, 16, text=title.upper(), fill=tcol,
                             font=("Consolas", 10, "bold"))
        else:
            self.create_text(W / 2, 15, text=title, fill=tcol,
                             font=("Segoe UI Semibold", 11, "bold" if styled else "normal"))

        # ---- quest layout ----
        quests = [dict(q) for q in _PREVIEW_QUESTS]
        for q in quests:
            if forced:
                q["shape"] = forced
            else:
                q["shape"] = _quest_shape(q, gshape) if styled else ""
        jit = 0.1 + 1.3 * o.get("creativity", 0.3)
        pos = layout_positions(quests, layout, jit, "preview")
        if not pos:
            pos = {q["id"]: (q["x"], q["y"]) for q in quests}
        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
        bw, bh = max(0.1, bx1 - bx0), max(0.1, by1 - by0)
        m = 40
        sc = min((W - 2 * m) / bw, (H - 2 * m - 20) / bh)
        ox = (W - bw * sc) / 2 - bx0 * sc
        oy = (H - bh * sc + 20) / 2 - by0 * sc

        def sp(qid):
            x, y = pos[qid]
            return ox + x * sc, oy + y * sc

        hide = layout in LAYOUT_HIDE_LINES
        if not hide:
            for q in quests:
                for d in q["dependencies"]:
                    if d in pos:
                        x1, y1 = sp(d)
                        x2, y2 = sp(q["id"])
                        self.create_line(x1, y1, x2, y2, fill="#5b6076", width=1)
        node = _CODE_HEX.get(code, PALETTE["accent"]) if styled else PALETTE["accent"]
        for q in quests:
            cx, cy = sp(q["id"])
            r = 8
            pts = _shape_points(cx, cy, r, q["shape"] or "circle")
            if pts:
                self.create_polygon(pts, fill=node, outline="#0c0d16")
            else:
                self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=node, outline="#0c0d16")

        self.create_text(W - pad - 4, H - 6, anchor="e",
                         text="%s layout · %s" % (layout, "styled" if styled else "plain"),
                         fill=PALETTE["muted"], font=("Consolas", 8))


class MoonRollButton(tk.Canvas):
    """A little animated night-sky button: drifting stars, a moon, 'RANDOMIZE'.
    Click -> a sparkle burst + the callback."""

    def __init__(self, parent, command, w=210, h=44):
        super().__init__(parent, width=w, height=h, highlightthickness=0, bd=0,
                         bg=PALETTE["night_bot"], cursor="hand2")
        self.W, self.H, self.command = w, h, command
        self._phase = 0.0
        self._burst = []       # [(x, y, life)]
        self._hot = False
        self._stars = [(random.random(), random.random(), random.uniform(0.5, 1.6),
                        random.random() * 6.28) for _ in range(22)]
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda e: setattr(self, "_hot", True))
        self.bind("<Leave>", lambda e: setattr(self, "_hot", False))
        self._animate()

    def _click(self, _e=None):
        cx, cy = self.W / 2, self.H / 2
        self._burst = [[cx, cy, 1.0, random.uniform(0, 6.28), random.uniform(1.5, 4.0)]
                       for _ in range(16)]
        if self.command:
            self.command()

    def _animate(self):
        self._phase += 0.06
        for p in self._burst:
            p[0] += math.cos(p[3]) * p[4]
            p[1] += math.sin(p[3]) * p[4]
            p[2] -= 0.06
        self._burst = [p for p in self._burst if p[2] > 0]
        self._draw()
        self.after(60, self._animate)

    def _draw(self):
        self.delete("all")
        W, H = self.W, self.H
        top = "#343a68" if self._hot else PALETTE["night_top"]
        for s in range(14):
            t = s / 14
            c1 = tuple(int(top[i:i + 2], 16) for i in (1, 3, 5))
            c2 = tuple(int(PALETTE["night_bot"][i:i + 2], 16) for i in (1, 3, 5))
            col = "#%02x%02x%02x" % tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
            self.create_rectangle(0, H * t, W, H * (t + 1 / 14) + 1, fill=col, outline="")
        self.create_rectangle(1, 1, W - 1, H - 1, outline="#4a4f86" if self._hot else "#2f3560")
        for (sx, sy, sr, ph) in self._stars:
            tw = 0.4 + 0.6 * math.sin(self._phase + ph)
            x = (sx * W + self._phase * 3) % W
            g = int(120 + 130 * tw)
            self.create_oval(x - sr, sy * H - sr, x + sr, sy * H + sr,
                             fill="#%02x%02x%02x" % (g, g, 255), outline="")
        mx, my, mr = 22, H / 2, 9
        self.create_oval(mx - mr, my - mr, mx + mr, my + mr, fill=PALETTE["gold"], outline="")
        self.create_oval(mx - mr + 5, my - mr - 2, mx + mr + 5, my + mr - 2,
                         fill=PALETTE["night_top"], outline="")
        self.create_text(mx + 22, my, anchor="w", text="RANDOMIZE",
                         fill="#ffffff", font=("Segoe UI Semibold", 11))
        for (px, py, life, _a, _sp) in self._burst:
            r = 2.5 * life
            self.create_oval(px - r, py - r, px + r, py + r,
                             fill="#fff2c0", outline="")


def style_text(widget: tk.Text):
    p = PALETTE
    widget.configure(bg=p["surface2"], fg=p["text"], insertbackground=p["text"],
                     selectbackground=p["accent"], selectforeground="#ffffff",
                     relief="flat", bd=0, padx=8, pady=6,
                     font=("Consolas", 10))


# ========================================================================== #
#  7b. Soft sound effects (in-memory sine WAVs - gentle, quiet, no assets)
# ========================================================================== #

# each note: (frequency Hz, duration ms).  Kept low + short + soft.
_SFX = {
    "click": [(440, 55)],
    "start": [(392, 70), (523, 90)],
    "done":  [(523, 90), (659, 90), (784, 180)],
    "error": [(392, 120), (294, 200)],
    "scan":  [(587, 110)],
}
_SFX_VOL = 0.16          # 0..1 of full scale
_SR = 22050


def _sfx_wav(seq) -> bytes:
    import struct
    frames = bytearray()
    for freq, ms in seq:
        n = int(_SR * ms / 1000)
        atk = max(1, int(n * 0.12))
        for i in range(n):
            if i < atk:
                env = i / atk
            else:
                env = (1.0 - (i - atk) / max(1, n - atk)) ** 1.8
            s = math.sin(2 * math.pi * freq * i / _SR)
            # add a soft octave for warmth, keep it mellow
            s = 0.8 * s + 0.2 * math.sin(4 * math.pi * freq * i / _SR)
            frames += struct.pack("<h", int(max(-1, min(1, s)) * env * _SFX_VOL * 32767))
        frames += b"\x00\x00" * int(_SR * 0.012)  # tiny gap
    data = bytes(frames)
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
           + struct.pack("<IHHIIHH", 16, 1, 1, _SR, _SR * 2, 2, 16)
           + b"data" + struct.pack("<I", len(data)))
    return hdr + data


_SFX_CACHE: dict = {}


def play_sfx(name: str, enabled: bool):
    if not enabled or winsound is None or name not in _SFX:
        return
    if name not in _SFX_CACHE:
        try:
            _SFX_CACHE[name] = _sfx_wav(_SFX[name])
        except Exception:
            _SFX_CACHE[name] = None
    wav = _SFX_CACHE[name]
    if not wav:
        return

    def run():
        try:
            winsound.PlaySound(wav, winsound.SND_MEMORY | winsound.SND_ASYNC)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


# ========================================================================== #
#  7c. Starry-night progress bar + loading overlay
# ========================================================================== #

class StarProgress(tk.Canvas):
    """A night-sky progress bar: the fill is a starfield with a moon at its edge.
    set(frac) for determinate, set(None) / just start() for an indeterminate sweep."""

    def __init__(self, parent, w=460, h=30):
        super().__init__(parent, width=w, height=h, highlightthickness=1,
                         highlightbackground="#333a66", bd=0, bg="#0c0e20")
        self.W, self.H = w, h
        self._frac = 0.0
        self._indef = True
        self._phase = 0.0
        self._running = False
        self._stars = [(random.random(), random.random(), random.uniform(0.4, 1.7),
                        random.random() * 6.28) for _ in range(max(16, w // 8))]
        self._c1 = tuple(int(PALETTE["night_top"][i:i + 2], 16) for i in (1, 3, 5))
        self._c2 = tuple(int(PALETTE["night_bot"][i:i + 2], 16) for i in (1, 3, 5))

    def set(self, frac):
        self._indef = frac is None
        if frac is not None:
            self._frac = max(0.0, min(1.0, frac))

    def start(self):
        if not self._running:
            self._running = True
            self._loop()

    def stop(self):
        self._running = False

    def _loop(self):
        if not self._running:
            return
        self._phase += 0.05
        try:
            self._draw()
        except tk.TclError:
            return
        self.after(55, self._loop)

    def _draw(self):
        self.delete("all")
        W = self.winfo_width() or self.W
        H = self.winfo_height() or self.H
        self.create_rectangle(0, 0, W, H, fill="#0c0e20", outline="")
        if self._indef or self._frac <= 0.01:
            band = 0.30
            c = math.sin(self._phase * 1.25) * 0.5 + 0.5
            x0 = (W - 2) * c * (1 - band) + 1
            x1 = x0 + (W - 2) * band
        else:
            x0, x1 = 1, 1 + (W - 2) * self._frac
        if x1 - x0 > 1:
            steps = 9
            for s in range(steps):
                t = s / steps
                col = "#%02x%02x%02x" % tuple(
                    int(self._c1[i] + (self._c2[i] - self._c1[i]) * t) for i in range(3))
                self.create_rectangle(x0, H * t, x1, H * (t + 1 / steps) + 1, fill=col, outline="")
            for (sx, sy, sr, ph) in self._stars:
                gx = x0 + (x1 - x0) * ((sx + self._phase * 0.12) % 1.0)
                tw = 0.35 + 0.65 * math.sin(self._phase + ph)
                g = int(140 + 110 * tw)
                self.create_oval(gx - sr, sy * H - sr, gx + sr, sy * H + sr,
                                 fill="#%02x%02x%02x" % (g, g, 255), outline="")
            mr = max(4, H * 0.32)
            mx = min(x1, W - mr - 1)
            self.create_oval(mx - mr - 2, H / 2 - mr - 2, mx + mr + 2, H / 2 + mr + 2,
                             outline="#4a4270")
            self.create_oval(mx - mr, H / 2 - mr, mx + mr, H / 2 + mr,
                             fill=PALETTE["gold"], outline="")
            self.create_oval(mx - mr + mr * 0.7, H / 2 - mr - 1, mx + mr + mr * 0.7, H / 2 + mr - 1,
                             fill="#101228", outline="")


FLAVOR = [
    "Summoning the Librarian...", "Rolling on the loot table...",
    "Chiseling chapter borders...", "Consulting the advancement tree...",
    "Counting your emeralds...", "Placing bookshelves...",
    "Aligning quest dependencies...", "Enchanting the quest book...",
    "Waking the wandering trader...", "Mining for good ideas...",
    "Feeding the parrots...", "Taming the dependency graph...",
    "Rendering pixel moons...", "Asking Steve for directions...",
    "Brewing awkward potions...", "Sorting the ender chest...",
    "Polishing the diamond pickaxe...", "Herding the quests into chapters...",
]


class LoadingOverlay(tk.Frame):
    """Full-window overlay with a blocky Minecraft-style progress bar."""

    def __init__(self, parent):
        super().__init__(parent, bg=PALETTE["bg"])
        self._active = False
        self._frac = 0.0
        self._creep_to = None
        self._shimmer = 0.0
        self._t0 = 0.0
        self._flavor_i = 0

        wrap = tk.Frame(self, bg=PALETTE["bg"])
        wrap.place(relx=0.5, rely=0.5, anchor="center")

        self.title_lbl = tk.Label(wrap, text="", bg=PALETTE["bg"], fg=PALETTE["text"],
                                  font=("Segoe UI Semibold", 15))
        self.title_lbl.pack(pady=(0, 4))
        self.step_lbl = tk.Label(wrap, text="", bg=PALETTE["bg"], fg=PALETTE["muted"],
                                 font=("Consolas", 10))
        self.step_lbl.pack(pady=(0, 12))

        self.bar = StarProgress(wrap, w=480, h=30)
        self.bar.pack()

        self.flavor_lbl = tk.Label(wrap, text="", bg=PALETTE["bg"], fg=PALETTE["gold"],
                                   font=("Consolas", 11))
        self.flavor_lbl.pack(pady=(14, 0))
        self.pct_lbl = tk.Label(wrap, text="", bg=PALETTE["bg"], fg=PALETTE["muted"],
                                font=("Consolas", 9))
        self.pct_lbl.pack(pady=(4, 0))
        # The overlay covers the whole window, so the Cancel button on the
        # Generate tab is unreachable while a run is going. Put one here.
        self.cancel_btn = ttk.Button(wrap, text="Cancel", style="Warn.TButton")
        self.cancel_btn.pack(pady=(18, 0))

    # ---- api ---------------------------------------------------------- #
    def show(self, title: str):
        self._active = True
        self._frac = 0.0
        self._creep_to = None
        self._t0 = time.time()
        self._flavor_i = random.randrange(len(FLAVOR))
        self.title_lbl.configure(text=title)
        self.step_lbl.configure(text="")
        self.cancel_btn.configure(state="normal")
        self.place(relwidth=1, relheight=1)
        self.lift()
        self.bar.set(None)
        self.bar.start()
        self._tick()
        self._rotate_flavor()

    def hide(self):
        self._active = False
        self._creep_to = None
        self.bar.stop()
        self.place_forget()

    def creep(self, target: float, label: str = ""):
        """Ease the bar forward toward `target` on its own clock — for a wait
        whose real duration is unknown (the AI call). A later set_step() with a
        concrete fraction takes over again."""
        self._creep_to = min(0.98, max(self._frac, target))
        if label:
            self.step_lbl.configure(text=label)

    def set_step(self, frac, label: str):
        if frac is None:                      # keep sweeping, don't freeze
            self.bar.set(None)
            if label:
                self.step_lbl.configure(text=label)
            return
        self._creep_to = None
        self._frac = max(self._frac, min(1.0, frac))
        self.bar.set(self._frac if self._frac > 0.01 else None)
        if label:
            self.step_lbl.configure(text=label)

    # ---- internals -------------------------------------------------- #
    def _rotate_flavor(self):
        if not self._active:
            return
        self.flavor_lbl.configure(text=FLAVOR[self._flavor_i % len(FLAVOR)])
        self._flavor_i += 1
        self.after(2600, self._rotate_flavor)

    def _tick(self):
        if not self._active:
            return
        if self._creep_to is not None and self._frac < self._creep_to - 0.002:
            self._frac += (self._creep_to - self._frac) * 0.035
            self.bar.set(self._frac)
        el = int(time.time() - self._t0)
        self.pct_lbl.configure(text="%d%%    %d:%02d elapsed" % (
            int(self._frac * 100), el // 60, el % 60))
        self.after(200, self._tick)


# ========================================================================== #
#  8. Dialogs
# ========================================================================== #

class ModSelectDialog(tk.Toplevel):
    def __init__(self, parent, mods, selected_ids):
        super().__init__(parent)
        self.title("Select mods for the quest book")
        self.geometry("620x600")
        self.configure(bg=PALETTE["bg"])
        self.result = None
        self._mods = sorted(mods, key=lambda m: (m["category"], m["name"].lower()))
        sel = set(selected_ids)

        ttk.Label(self, text="Checked mods get their own chapters and item IDs in the prompt.",
                  style="Muted.TLabel").pack(anchor="w", padx=12, pady=(12, 6))
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12)
        ttk.Button(bar, text="All", width=6,
                   command=lambda: self._lb.selection_set(0, "end")).pack(side="left")
        ttk.Button(bar, text="None", width=6,
                   command=lambda: self._lb.selection_clear(0, "end")).pack(side="left", padx=4)
        ttk.Button(bar, text="Core only", command=self._core).pack(side="left")
        self._count = ttk.Label(bar, text="", style="Muted.TLabel")
        self._count.pack(side="right")

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=12, pady=10)
        sb = ttk.Scrollbar(wrap)
        sb.pack(side="right", fill="y")
        self._lb = tk.Listbox(wrap, selectmode="multiple", yscrollcommand=sb.set, activestyle="none",
                              exportselection=False, bg=PALETTE["surface2"], fg=PALETTE["text"],
                              selectbackground=PALETTE["accent"], selectforeground="#ffffff",
                              relief="flat", bd=0, highlightthickness=0, font=("Consolas", 10))
        self._lb.pack(side="left", fill="both", expand=True)
        sb.config(command=self._lb.yview)
        for i, m in enumerate(self._mods):
            self._lb.insert("end", " %-9s  %-32s  %s" % (m["category"], m["name"][:32], m["mod_id"]))
            if not sel or m["mod_id"] in sel:
                self._lb.selection_set(i)
        self._lb.bind("<<ListboxSelect>>", lambda e: self._upd())
        self._upd()

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btns, text="OK", style="Accent.TButton", command=self._ok).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=6)

        self.transient(parent)
        self.grab_set()

    def _upd(self):
        self._count.configure(text="%d / %d selected" % (len(self._lb.curselection()), len(self._mods)))

    def _core(self):
        self._lb.selection_clear(0, "end")
        for i, m in enumerate(self._mods):
            if m["category"] in ("tech", "magic", "world", "mob"):
                self._lb.selection_set(i)
        self._upd()

    def _ok(self):
        self.result = [self._mods[i]["mod_id"] for i in self._lb.curselection()]
        self.destroy()


# ========================================================================== #
#  9. Main window
# ========================================================================== #

def _set_aumid():
    """Only set an explicit AppUserModelID when running under a bare
    python/pythonw. With the renamed host exe ('FTB Quests Generator.exe')
    Windows already names + groups the taskbar button, and forcing an AUMID
    would stop a pinned shortcut from merging with the running window."""
    import sys
    if "python" in Path(sys.executable).name.lower():
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "antidqrk.AutoQuestGen")
        except Exception:
            pass


def _set_app_identity(root: tk.Tk):
    """Moon icon on the window + taskbar."""
    if ICON_PATH.exists():
        try:
            root.iconbitmap(default=str(ICON_PATH))
        except Exception:
            pass
    png = RESOURCE_DIR / "logo.png"
    if png.exists():
        try:
            root._icon_img = tk.PhotoImage(file=str(png))
            root.iconphoto(True, root._icon_img)
        except Exception:
            pass
    # nuclear option: hand the real HICONs to the window via WM_SETICON
    if ICON_PATH.exists():
        try:
            import ctypes
            u = ctypes.windll.user32
            root.update_idletasks()
            hwnd = u.GetParent(root.winfo_id()) or root.winfo_id()
            IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x0080
            for px, which in ((16, 0), (32, 1), (48, 1)):
                h = u.LoadImageW(0, str(ICON_PATH), IMAGE_ICON, px, px, LR_LOADFROMFILE)
                if h:
                    u.SendMessageW(hwnd, WM_SETICON, which, h)
        except Exception:
            pass


class GuidedWizard(tk.Toplevel):
    """Folder -> Scan -> Options -> Build, one step at a time.

    A stranger's first five minutes is where a beta lives or dies, and the
    tabbed GUI asks them to already know the order: Setup, scan, options,
    generate. This walks it instead. It is deliberately THIN - every step
    drives the same App variables and calls the same handlers the tabs use
    (on_scan, on_generate_local, on_open_out), so there is no second code
    path to go stale, and anything the wizard can do the tabs can verify.

    Optional by design: it auto-opens only on a first run (no config file
    existed), and a Guided Setup button reopens it any time. Power users
    never see it twice.
    """

    STEPS = ("Your modpack", "Scan", "Options", "Build")

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Guided setup")
        self.geometry("560x430")
        self.transient(app.root)
        apply_theme(self)
        self.step = 0

        self.hdr = ttk.Label(self, text="", font=("Segoe UI", 13, "bold"))
        self.hdr.pack(anchor="w", padx=16, pady=(14, 2))
        self.sub = ttk.Label(self, text="", wraplength=520, justify="left")
        self.sub.pack(anchor="w", padx=16)
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill="both", expand=True)
        nav = ttk.Frame(self, padding=(16, 8))
        nav.pack(fill="x", side="bottom")
        self.back_btn = ttk.Button(nav, text="Back", command=self.go_back)
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next", style="Accent.TButton",
                                   command=self.go_next)
        self.next_btn.pack(side="right")
        ttk.Button(nav, text="Close", command=self.destroy).pack(side="right", padx=8)
        self.show_step()

    # ---- steps ---------------------------------------------------------- #
    def show_step(self):
        for w in self.body.winfo_children():
            w.destroy()
        self.back_btn.configure(state="normal" if self.step else "disabled")
        self.next_btn.configure(state="normal", text="Next", command=self.go_next)
        i = self.step
        self.hdr.configure(text="Step %d of %d — %s"
                           % (i + 1, len(self.STEPS), self.STEPS[i]))
        getattr(self, "_step%d" % i)()

    def _step0(self):
        self.sub.configure(text="Where are your mods? Pick a detected instance "
                                "or browse to any mods folder.")
        found = discover_instances()
        self._inst_map = {f[0]: f[1] for f in found}
        row = ttk.Frame(self.body); row.pack(fill="x", pady=8)
        self._inst = ttk.Combobox(row, values=list(self._inst_map),
                                  state="readonly", width=44)
        cur = self.app.mods_dir.get()
        for l, p in self._inst_map.items():
            if p == cur:
                self._inst.set(l)
        self._inst.pack(side="left", fill="x", expand=True)
        self._inst.bind("<<ComboboxSelected>>", lambda _e: self._pick_inst(cur))
        ttk.Button(row, text="Browse...", command=self._browse).pack(side="left", padx=6)
        ttk.Label(self.body, textvariable=self.app.mods_dir,
                  wraplength=500).pack(anchor="w", pady=6)
        if not self._inst_map:
            ttk.Label(self.body, text="No launcher instances were detected - "
                                      "use Browse.").pack(anchor="w")

    def _pick_inst(self, fallback):
        self._retarget(self._inst_map.get(self._inst.get(), fallback))

    def _browse(self):
        d = filedialog.askdirectory(title="Pick your mods folder")
        if d:
            self._retarget(d)

    def _retarget(self, mods):
        """Point the app at a pack, output folder included.

        The output has to follow the pack. Leaving it where it was is how the
        wizard could write pack A's book into pack B's live quests folder -
        the destination is a saved game, so this is the one place a stale
        default is dangerous rather than merely wrong. An output the user
        typed themselves is left alone."""
        self.app.mods_dir.set(mods)
        want = _quests_dir_for(mods)
        cur = (self.app.out_dir1.get() or "").strip()
        if want and (not cur or cur.replace("\\", "/").endswith(
                "config/ftbquests/quests")):
            self.app.out_dir1.set(want)

    def _step1(self):
        self.sub.configure(text="Read every mod jar to learn what your pack "
                                "contains. Nothing is written anywhere yet.")
        self._scan_lbl = ttk.Label(self.body, text="", wraplength=500,
                                   justify="left")
        self._scan_lbl.pack(anchor="w", pady=8)
        s = self.app.scan_result
        if s:
            self._scan_lbl.configure(text="Already scanned: %d mods. Scan again "
                                          "if you changed the folder, or press "
                                          "Next." % len(s["mods"]))
        else:
            self._scan_lbl.configure(text="Press Scan to begin (instant with "
                                          "the cache, up to ~20s cold).")
            self.next_btn.configure(state="disabled")
        ttk.Button(self.body, text="Scan now", style="Accent.TButton",
                   command=self._scan).pack(anchor="w", pady=6)

    def _scan(self):
        if self.app._busy:
            return
        self.app.on_scan()
        self._scan_lbl.configure(text="Scanning...")
        self._poll(lambda: self.app.scan_result is not None and not self.app._busy,
                   self._scan_done)

    def _scan_done(self):
        s = self.app.scan_result
        if s and self._scan_lbl.winfo_exists():
            self._scan_lbl.configure(
                text="Found %d mods, %d items. All of them are selected - "
                     "narrow the list later on the Mods tab if you want."
                % (len(s["mods"]), sum(len(v) for v in s["items"].values())))
            self.next_btn.configure(state="normal")

    def _step2(self):
        self.sub.configure(text="The defaults make a good book. These four "
                                "matter most; everything else lives on the "
                                "main tabs.")
        for label, var, values in (
                ("Book size", self.app.density,
                 ["tiny", "small", "normal", "large", "massive", "colossal"]),
                ("Look", self.app.aesthetic,
                 ["minimal", "balanced", "decorated", "lavish"]),
                ("Quest shape", self.app.quest_shape,
                 ["auto"] + list(QUEST_SHAPES)),
                ("Progression", self.app.progression, ["open", "linear"])):
            row = ttk.Frame(self.body); row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=14).pack(side="left")
            ttk.Combobox(row, textvariable=var, values=values,
                         state="readonly", width=18).pack(side="left")

    def _step3(self):
        self.sub.configure(text="Where should the book be written? Pick an "
                                "EMPTY folder or your pack's "
                                "config/ftbquests/quests folder.")
        row = ttk.Frame(self.body); row.pack(fill="x", pady=8)
        ttk.Entry(row, textvariable=self.app.out_dir1).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self._browse_out).pack(
            side="left", padx=6)
        self._build_lbl = ttk.Label(self.body, text="", wraplength=500,
                                    justify="left")
        self._build_lbl.pack(anchor="w", pady=8)
        self._build_btn = ttk.Button(self.body, text="Build Quest Book",
                                     style="Accent.TButton", command=self._build)
        self._build_btn.pack(anchor="w")
        self._open_btn = ttk.Button(self.body, text="Open output folder",
                                    command=self.app.on_open_out)
        self.next_btn.configure(text="Finish", command=self.destroy)

    def _browse_out(self):
        d = filedialog.askdirectory(title="Pick the output folder")
        if d:
            self.app.out_dir1.set(d)

    def _build(self):
        if self.app._busy:
            return
        self._build_btn.configure(state="disabled")
        self._build_lbl.configure(text="Building...")
        self.app.on_generate_local()
        self._poll(lambda: not self.app._busy, self._build_done)

    def _build_done(self):
        if not self.winfo_exists():
            return
        self._build_btn.configure(state="normal")
        self._build_lbl.configure(
            text="Done - the RUN tab log has the full summary. Load the pack "
                 "and open the quest book, or rebuild after tweaking options.")
        self._open_btn.pack(anchor="w", pady=6)

    # ---- plumbing ------------------------------------------------------- #
    def _poll(self, ready, then):
        if not self.winfo_exists():
            return
        if ready():
            then()
        else:
            self.after(350, lambda: self._poll(ready, then))

    def go_next(self):
        if self.step == 0 and not Path(self.app.mods_dir.get().strip()).is_dir():
            messagebox.showwarning(APP_NAME, "Pick a valid mods folder first.",
                                   parent=self)
            return
        if self.step < len(self.STEPS) - 1:
            self.step += 1
            self.show_step()

    def go_back(self):
        if self.step:
            self.step -= 1
            self.show_step()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("%s  v%s" % (APP_NAME, VERSION))
        apply_theme(root)
        _set_app_identity(root)

        self.cfg = self._load_cfg()
        # No config file has ever been written = a first run = the one moment
        # the guided wizard should open itself. Captured here because the
        # first _save_cfg makes the file exist forever after.
        self._first_run = not bool(self.cfg)
        root.geometry(self.cfg.get("geometry", "920x840"))
        root.minsize(820, 680)

        self.scan_result = None
        self.selected_ids = list(self.cfg.get("selected_ids", []))
        self.events = queue.Queue()
        self._busy = False
        self._cancel = False
        self._mc_running = False

        g = self.cfg.get
        self.mods_dir = tk.StringVar(value=g("mods_dir", _guess_mods_dir()))
        self.out_dir1 = tk.StringVar(value=g("out_dir1", _guess_quests_dir()))
        self.out_dir2 = tk.StringVar(value=g("out_dir2", ""))
        self.provider = tk.StringVar(value=g("provider", "Gemini (OpenAI-compat)"))
        self.api_url = tk.StringVar(value=g("api_url", ""))
        self.api_key = tk.StringVar(value=g("api_key", ""))
        self.model = tk.StringVar(value=g("model", ""))
        self.max_tokens = tk.StringVar(value=str(g("max_tokens", 32768)))
        self.temperature = tk.DoubleVar(value=float(g("temperature", 0.4)))
        self.density = tk.StringVar(value=g("density", "normal"))
        self.target_count = tk.StringVar(value=g("target_count", ""))
        self.language = tk.StringVar(value=g("language", "English"))
        self.aesthetic = tk.StringVar(value=g("aesthetic", "balanced"))
        self.quest_shape = tk.StringVar(value=g("quest_shape", "auto"))
        # "auto" = a new book every Build. A number reproduces one exactly.
        self.run_seed = tk.StringVar(value=g("run_seed", "auto"))
        self.reward_level = tk.StringVar(value=g("reward_level", "standard"))
        self.progression = tk.StringVar(value=g("progression", "linear"))
        self.layout = tk.StringVar(value=g("layout", "line"))
        self.group_style = tk.StringVar(value=g("group_style", "bold"))
        self.book_title = tk.StringVar(value=g("book_title", ""))
        self.book_icon = tk.StringVar(value=g("book_icon", ""))
        self.themes_text = g("themes_text", "")
        self.opt_themes_only = tk.BooleanVar(value=g("opt_themes_only", True))
        self.opt_vanilla_ch = tk.BooleanVar(value=g("opt_vanilla_ch", True))
        self.opt_decor_mods = tk.BooleanVar(value=g("opt_decor_mods", False))
        self.opt_raw_prompt = tk.BooleanVar(value=g("opt_raw_prompt", False))
        self.opt_groups = tk.BooleanVar(value=g("opt_groups", True))
        self.opt_chain = tk.BooleanVar(value=g("opt_chain", True))
        self.opt_xp = tk.BooleanVar(value=g("opt_xp", False))
        self.opt_style_chapters = tk.BooleanVar(value=g("opt_style_chapters", True))
        self.opt_decor = tk.BooleanVar(value=g("opt_decor", True))
        self.opt_backup = tk.BooleanVar(value=g("opt_backup", True))
        self.opt_mcwarn = tk.BooleanVar(value=g("opt_mcwarn", True))
        self.opt_sound = tk.BooleanVar(value=g("opt_sound", True))

        # start a fresh session in the on-disk log (keep it from growing forever)
        try:
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > 2_000_000:
                LOG_PATH.unlink()
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write("\n===== %s  %s v%s =====\n"
                         % (time.strftime("%Y-%m-%d %H:%M:%S"), APP_NAME, VERSION))
        except Exception:
            pass

        self._offline_doc = None          # cached offline book, for chapter fallbacks
        self.loading = LoadingOverlay(root)
        self.loading.cancel_btn.configure(command=self._do_cancel)
        self._build_ui()
        if not self.api_url.get():
            self._preset(force=True)
        self._update_scan_lbl()
        self.root.after(80, self._pump)
        self.root.after(400, self._poll_mc)
        if self._first_run:
            self.root.after(600, self.on_wizard)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def on_wizard(self):
        GuidedWizard(self)

    # ---- config ---------------------------------------------------------- #
    def _load_cfg(self):
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cfg(self):
        self.cfg.update({
            "mods_dir": self.mods_dir.get(), "out_dir1": self.out_dir1.get(),
            "out_dir2": self.out_dir2.get(), "provider": self.provider.get(),
            "api_url": self.api_url.get().strip(),
            "api_key": self.api_key.get().strip(),
            "model": self.model.get(), "max_tokens": self._int(self.max_tokens.get(), 32768),
            "temperature": round(self.temperature.get(), 2),
            "density": self.density.get(), "target_count": self.target_count.get(),
            "language": self.language.get(), "selected_ids": self.selected_ids,
            "aesthetic": self.aesthetic.get(), "reward_level": self.reward_level.get(),
            "quest_shape": self.quest_shape.get(),
            "run_seed": self.run_seed.get(),
            "progression": self.progression.get(), "layout": self.layout.get(),
            "group_style": self.group_style.get(),
            "book_title": self.book_title.get(), "book_icon": self.book_icon.get(),
            "themes_text": self._themes_raw(),
            "opt_themes_only": self.opt_themes_only.get(),
            "opt_vanilla_ch": self.opt_vanilla_ch.get(),
            "opt_decor_mods": self.opt_decor_mods.get(),
            "opt_raw_prompt": self.opt_raw_prompt.get(),
            "opt_groups": self.opt_groups.get(), "opt_chain": self.opt_chain.get(),
            "opt_xp": self.opt_xp.get(), "opt_decor": self.opt_decor.get(),
            "opt_style_chapters": self.opt_style_chapters.get(),
            "opt_backup": self.opt_backup.get(),
            "opt_mcwarn": self.opt_mcwarn.get(), "opt_sound": self.opt_sound.get(),
            "geometry": self.root.winfo_geometry(),
        })
        try:
            CONFIG_PATH.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _int(s, d):
        try:
            return int(str(s).strip())
        except Exception:
            return d

    # ---- ui ------------------------------------------------------------- #
    def _build_ui(self):
        self.header = HeaderBanner(self.root, height=76)
        self.header.pack(fill="x")

        self._scroll_canvases = []
        self.root.bind_all("<MouseWheel>", self._global_wheel, add="+")

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(10, 4))
        self._tab_setup()
        self._tab_mods()
        self._tab_generate()
        self._tab_library()
        self._tab_editor()
        self._tab_repair()
        self._tab_import()
        self._tab_prompt()
        self._tab_log()

        status = ttk.Frame(self.root, style="Card.TFrame")
        status.pack(fill="x", side="bottom")
        self.status = ttk.Label(status, text="ready", style="Status.TLabel")
        self.status.pack(side="left", padx=14, pady=7)
        self.prog = StarProgress(status, w=170, h=13)
        self.prog.pack(side="right", padx=14, pady=7)
        # About / credits - user-directed, exact wording.
        ttk.Label(status, text=CREDIT_LINE, style="Status.TLabel").pack(
            side="right", padx=10, pady=7)

    def _global_wheel(self, e):
        """Scroll whatever page-canvas is under the pointer (not just the scrollbar).
        Forces a synchronous redraw after each step to avoid canvas tearing."""
        w = self.root.winfo_containing(e.x_root, e.y_root)
        hops = 0
        while w is not None and hops < 40:
            if isinstance(w, (tk.Text, tk.Listbox)):
                return
            if isinstance(w, tk.Canvas) and getattr(w, "_pagescroll", False):
                step = 3 if int(w.cget("yscrollincrement") or 0) else 1
                w.yview_scroll(-step if e.delta > 0 else step, "units")
                try:
                    w.update_idletasks()
                except Exception:
                    pass
                return
            try:
                w = w.master
            except Exception:
                return
            hops += 1

    def _row(self, parent, label, var, cmd):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=5)
        ttk.Label(f, text=label, width=22).pack(side="left")
        ttk.Entry(f, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="Browse", command=cmd).pack(side="left", padx=(6, 0))
        return f

    def _tab_setup(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text="  Setup  ")
        pad = ttk.Frame(t)
        pad.pack(fill="both", expand=True, padx=14, pady=12)

        lf = ttk.Labelframe(pad, text="FOLDERS", padding=12)
        lf.pack(fill="x")
        # Found instances first: a stranger should not have to know where
        # CurseForge hides its folders. Browse below stays the escape hatch.
        self._instances = discover_instances()
        if self._instances:
            fi = ttk.Frame(lf)
            fi.pack(fill="x", pady=5)
            ttk.Label(fi, text="Detected instances", width=22).pack(side="left")
            self._inst_var = tk.StringVar(value="")
            inst_cb = ttk.Combobox(fi, textvariable=self._inst_var, state="readonly",
                                   values=[lbl for lbl, _ in self._instances])
            inst_cb.pack(side="left", fill="x", expand=True)
            inst_cb.bind("<<ComboboxSelected>>", self._on_pick_instance)
        self._row(lf, "Mods / instance folder", self.mods_dir, self._b_mods)
        self._row(lf, "Output  ..quests", self.out_dir1, lambda: self._b_out(self.out_dir1))
        self._row(lf, "Output #2 (optional)", self.out_dir2, lambda: self._b_out(self.out_dir2))
        r = ttk.Frame(lf)
        r.pack(fill="x", pady=(8, 0))
        ttk.Button(r, text="🧭  Guided setup", style="Accent.TButton",
                   command=self.on_wizard).pack(side="left")
        ttk.Button(r, text="Scan mods", command=self.on_scan).pack(side="left", padx=6)
        ttk.Button(r, text="Select mods...", command=self.on_select_mods).pack(side="left", padx=6)
        self.scan_lbl = ttk.Label(r, text="", style="Muted.TLabel")
        self.scan_lbl.pack(side="left", padx=6)

        # This whole block is optional and says so. Setup is the first screen
        # a new user sees, and five rows of provider/key/model made an API key
        # look like a prerequisite for using the app at all. It is not one:
        # Build Quest Book never touches the network.
        af = ttk.Labelframe(pad, text="AI PROVIDER  (optional)", padding=12)
        af.pack(fill="x", pady=(14, 0))
        ttk.Label(af, style="CardMuted.TLabel", wraplength=560, justify="left",
                  text="Not needed to build a quest book. Leave this empty and use "
                       "Build Quest Book on the Generate tab — it runs entirely on "
                       "your machine. Fill this in only if you also want AI-written "
                       "prose.").pack(anchor="w", pady=(0, 8))
        r1 = ttk.Frame(af)
        r1.pack(fill="x", pady=4)
        ttk.Label(r1, text="Provider", width=22).pack(side="left")
        cb = ttk.Combobox(r1, textvariable=self.provider, state="readonly",
                          values=list(PROVIDER_PRESETS.keys()))
        cb.pack(side="left", fill="x", expand=True)
        cb.bind("<<ComboboxSelected>>", lambda e: self._preset(force=True))
        r2 = ttk.Frame(af)
        r2.pack(fill="x", pady=4)
        ttk.Label(r2, text="API URL", width=22).pack(side="left")
        ttk.Entry(r2, textvariable=self.api_url).pack(side="left", fill="x", expand=True)
        r3 = ttk.Frame(af)
        r3.pack(fill="x", pady=4)
        ttk.Label(r3, text="API key", width=22).pack(side="left")
        self.key_entry = ttk.Entry(r3, textvariable=self.api_key, show="\u2022")
        self.key_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(r3, text="Show", width=6, command=self._toggle_key).pack(side="left", padx=(6, 0))
        r4 = ttk.Frame(af)
        r4.pack(fill="x", pady=4)
        ttk.Label(r4, text="Model", width=22).pack(side="left")
        self.model_cb = ttk.Combobox(r4, textvariable=self.model, values=[])
        self.model_cb.pack(side="left", fill="x", expand=True)
        self._fetch_btn = ttk.Button(r4, text="Fetch", width=7, command=self.on_fetch_models)
        self._fetch_btn.pack(side="left", padx=(6, 0))
        r5 = ttk.Frame(af)
        r5.pack(fill="x", pady=4)
        ttk.Label(r5, text="Max tokens", width=22).pack(side="left")
        self._maxtok_setup = ttk.Entry(r5, textvariable=self.max_tokens, width=12)
        self._maxtok_setup.pack(side="left")
        ttk.Button(r5, text="Test connection", command=self.on_test).pack(side="left", padx=(10, 0))
        # Model, Fetch and Max tokens only reach call_ai/fetch_models - nothing
        # on the offline path reads any of them - so with no key they are
        # controls that silently do nothing (the language-dropdown defect class).
        # Grey them out and say why. Provider/URL/key stay live: they ARE how
        # you enter a key. Temperature is NOT greyed anywhere - _opts() feeds
        # it to build_chapters as "creativity", so it moves offline books too.
        self._ai_hint_setup = ttk.Label(af, text="", style="CardMuted.TLabel")
        self._ai_hint_setup.pack(anchor="w", pady=(4, 0))
        self.api_key.trace_add("write", lambda *a: self._sync_ai_state())
        self._sync_ai_state()

    def _scrollframe(self, tab):
        """A vertically scrollable body inside a notebook tab."""
        outer = tk.Frame(tab, bg=PALETTE["bg"])
        outer.pack(fill="both", expand=True)
        cv = tk.Canvas(outer, bg=PALETTE["bg"], highlightthickness=0, bd=0,
                       yscrollincrement=16)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        cv._pagescroll = True
        self._scroll_canvases.append(cv)
        body = ttk.Frame(cv)
        win = cv.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        inner = ttk.Frame(body)
        inner.pack(fill="both", expand=True, padx=16, pady=14)
        return inner

    def _combo_row(self, parent, label, var, values, hint=""):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=4)
        ttk.Label(f, text=label, width=16, style="CardMuted.TLabel").pack(side="left")
        ttk.Combobox(f, textvariable=var, state="readonly", width=13, values=values).pack(side="left")
        if hint:
            ttk.Label(f, text=hint, style="CardMuted.TLabel").pack(side="left", padx=8)
        return f

    def _sync_language_state(self):
        """Grey the Language dropdown out while no API key is set.

        Only the AI prompt builders read the language; local_quest_doc never
        does, and triage measured English vs Deutsch offline books md5-identical
        (a4dc39a9 both). With no key the offline path is the only generate path,
        so an enabled dropdown is a control that silently does nothing - the
        exact defect class self_audit's dead-options check exists for. The
        greying itself is proven live by self_audit's greyed-controls check.
        """
        if not hasattr(self, "language_combo"):
            return
        if self.api_key.get().strip():
            self.language_combo.configure(state="readonly")
            self._lang_hint.configure(text="AI generation only")
        else:
            self.language_combo.configure(state="disabled")
            self._lang_hint.configure(text="needs an API key — offline books are English-only")

    def _sync_ai_state(self):
        """Grey the Model/Fetch/Max-tokens controls while no API key is set.

        Same defect class as the Language dropdown above: grep shows model and
        max_tokens reach only call_ai / fetch_models / the AI reward editor -
        the offline path never reads either - so on a keyless (offline-only)
        setup they are live-looking controls that cannot affect the output.
        Provider, API URL, key and Test connection stay enabled: they are the
        way a key gets entered at all. Temperature is deliberately untouched:
        _opts() ships it to build_chapters as "creativity" (layout jitter,
        side-quest count), so it moves offline books and greying it would lie
        the other way. Proven live both ways by self_audit's greyed-controls
        check.
        """
        on = bool(self.api_key.get().strip())
        state = "normal" if on else "disabled"
        for w in (getattr(self, "model_cb", None), getattr(self, "_fetch_btn", None),
                  getattr(self, "_maxtok_setup", None), getattr(self, "_maxtok_gen", None)):
            if w is not None:
                w.configure(state=state)
        hint = "" if on else "Model and max tokens are greyed out — no API key, so only the offline path runs."
        if hasattr(self, "_ai_hint_setup"):
            self._ai_hint_setup.configure(text=hint)
        if hasattr(self, "_ai_hint_gen"):
            self._ai_hint_gen.configure(
                text="AI generation only" if on else "needs an API key — offline builds ignore this")

    # ---- Mods tab --------------------------------------------------- #
    MODS_PER_PAGE = 12

    def _tab_mods(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text="Mods")
        self._mod_page = 0
        self._mod_imgs = {}
        self._mod_info = {}

        top = ttk.Frame(t)
        top.pack(fill="x", padx=16, pady=(12, 4))
        ttk.Label(top, text="Search", style="Muted.TLabel").pack(side="left")
        self.mod_search = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.mod_search, width=22)
        e.pack(side="left", padx=6)
        e.bind("<KeyRelease>", lambda ev: self._mods_repage())
        self.mod_filter = tk.StringVar(value="all")
        ttk.Combobox(top, textvariable=self.mod_filter, state="readonly", width=10,
                     values=["all", "selected", "core", "tech", "magic", "world", "mob",
                             "utility", "food", "decor", "unknown"]).pack(side="left", padx=4)
        self.mod_filter.trace_add("write", lambda *a: self._mods_repage())
        ttk.Button(top, text="All", width=5, command=lambda: self._mods_bulk("all")).pack(side="left", padx=(8, 2))
        ttk.Button(top, text="None", width=6, command=lambda: self._mods_bulk("none")).pack(side="left", padx=2)
        ttk.Button(top, text="Core", width=6, command=lambda: self._mods_bulk("core")).pack(side="left", padx=2)
        b = ttk.Button(top, text="Fetch info (Modrinth)", command=self.on_fetch_mod_info)
        b.pack(side="right")
        hover_lift(b)

        nav = ttk.Frame(t)
        nav.pack(fill="x", padx=16, pady=(0, 4))
        ttk.Button(nav, text="◀", width=3, command=lambda: self._mods_page(-1)).pack(side="left")
        self._mod_pagelbl = ttk.Label(nav, text="", style="Muted.TLabel")
        self._mod_pagelbl.pack(side="left", padx=8)
        ttk.Button(nav, text="▶", width=3, command=lambda: self._mods_page(1)).pack(side="left")
        self.mods_count = ttk.Label(nav, text="scan mods first (Setup tab)", style="Muted.TLabel")
        self.mods_count.pack(side="right")

        self._mod_grid = ttk.Frame(t)
        self._mod_grid.pack(fill="both", expand=True, padx=12, pady=6)
        self._mod_grid.columnconfigure(0, weight=1)
        self._mod_grid.columnconfigure(1, weight=1)

    def _mods_page(self, delta):
        self._mod_page += delta
        self._render_mod_cards()

    def _mods_repage(self):
        self._mod_page = 0
        self._render_mod_cards()

    def _mods_bulk(self, which):
        if not self.scan_result:
            return
        vis = self._visible_mods()
        if which == "all":
            for m in vis:
                if m["mod_id"] not in self.selected_ids:
                    self.selected_ids.append(m["mod_id"])
        elif which == "none":
            for m in vis:
                if m["mod_id"] in self.selected_ids:
                    self.selected_ids.remove(m["mod_id"])
        elif which == "core":
            self.selected_ids = [m["mod_id"] for m in self.scan_result["mods"]
                                 if m["category"] in ("tech", "magic", "world", "mob")]
        self._render_mod_cards()
        self._update_scan_lbl()
        self._save_cfg()

    def _visible_mods(self):
        if not self.scan_result:
            return []
        q = self.mod_search.get().strip().lower()
        f = self.mod_filter.get()
        out = []
        for m in sorted(self.scan_result["mods"], key=lambda x: x["name"].lower()):
            if q and q not in (m["name"] + m["mod_id"] + m["description"]).lower():
                continue
            if f == "selected" and m["mod_id"] not in self.selected_ids:
                continue
            if f == "core" and m["category"] not in ("tech", "magic", "world", "mob"):
                continue
            if f not in ("all", "selected", "core") and m["category"] != f:
                continue
            out.append(m)
        return out

    def _mk_thumb(self, mod):
        mid = mod["mod_id"]
        if mid in self._mod_imgs:
            return self._mod_imgs[mid]
        img = None
        data = self._mod_info.get(mid, {}).get("logo_bytes") or mod.get("logo")
        if data:
            try:
                ph = tk.PhotoImage(data=base64.b64encode(data).decode())
                sub = max(1, round(ph.width() / 40))
                img = ph.subsample(sub, sub) if sub > 1 else ph
            except Exception:
                img = None
        self._mod_imgs[mid] = img
        return img

    def _render_mod_cards(self):
        if not hasattr(self, "_mod_grid"):
            return
        for w in self._mod_grid.winfo_children():
            w.destroy()
        vis = self._visible_mods()
        per = self.MODS_PER_PAGE
        pages = max(1, (len(vis) + per - 1) // per)
        self._mod_page = max(0, min(self._mod_page, pages - 1))
        page = vis[self._mod_page * per:(self._mod_page + 1) * per]
        self._mod_pagelbl.configure(text="page %d / %d" % (self._mod_page + 1, pages))
        total = len(self.scan_result["mods"]) if self.scan_result else 0
        self.mods_count.configure(text="%d match · %d / %d selected"
                                  % (len(vis), len(self.selected_ids), total))
        BAD = ("example mod description", "none.", "")
        for i, m in enumerate(page):
            card = ttk.Frame(self._mod_grid, style="Card.TFrame", padding=9)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=5, pady=5)
            head = ttk.Frame(card, style="Card.TFrame")
            head.pack(fill="x")
            th = self._mk_thumb(m)
            if th:
                lb = tk.Label(head, image=th, bg=PALETTE["surface"])
                lb.image = th
                lb.pack(side="left", padx=(0, 7))
            var = tk.BooleanVar(value=m["mod_id"] in self.selected_ids)

            def toggle(mid=m["mod_id"], v=var):
                if v.get() and mid not in self.selected_ids:
                    self.selected_ids.append(mid)
                elif not v.get() and mid in self.selected_ids:
                    self.selected_ids.remove(mid)
                self._update_scan_lbl()
                self._save_cfg()
                self._render_mod_cards()
            ttk.Checkbutton(head, text=" " + m["name"][:30], variable=var, command=toggle,
                            style="Card.TCheckbutton").pack(side="left")
            ttk.Label(head, text=m["category"], style="CardMuted.TLabel").pack(side="right")
            info = self._mod_info.get(m["mod_id"], {})
            desc = info.get("desc") or m["description"] or ""
            if desc.strip().lower() in BAD:
                desc = "(no description — try Fetch info)"
            ttk.Label(card, text=desc[:220], style="CardMuted.TLabel", wraplength=390,
                      justify="left").pack(anchor="w", pady=(5, 0))
            ttk.Label(card, text=m["mod_id"] + ("  ·  " + info["url"] if info.get("url") else ""),
                      style="CardMuted.TLabel").pack(anchor="w")

    def on_fetch_mod_info(self):
        if self._need_scan():
            return
        targets = [m for m in self.scan_result["mods"] if m["mod_id"] in self.selected_ids] \
            or self.scan_result["mods"]

        def job():
            got = 0
            for i, m in enumerate(targets):
                if self._cancel:
                    break
                self.phase(i / len(targets), "Modrinth: %s" % m["mod_id"])
                info = modrinth_lookup(m["mod_id"], m["name"])
                if info:
                    self._mod_info[m["mod_id"]] = info
                    got += 1
            self.events.put(("mods_refresh", None))
            return "fetched %d / %d mod descriptions" % (got, len(targets))
        self._run(job, "Fetching mod info (Modrinth)")

    def _tab_generate(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text="Generate")
        pad = self._scrollframe(t)

        cols = ttk.Frame(pad)
        cols.pack(fill="x")
        cols.columnconfigure(0, weight=1, uniform="g")
        cols.columnconfigure(1, weight=1, uniform="g")
        left = ttk.Frame(cols)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right = ttk.Frame(cols)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        of = ttk.Labelframe(left, text="BOOK SHAPE")
        of.pack(fill="x")
        qr = ttk.Frame(of)
        qr.pack(fill="x", pady=4)
        ttk.Label(qr, text="Quest count", width=16, style="CardMuted.TLabel").pack(side="left")
        ttk.Combobox(qr, textvariable=self.density, state="readonly", width=13,
                     values=DENSITY_ORDER).pack(side="left")
        self._count_hint = ttk.Label(qr, text="", style="CardMuted.TLabel")
        self._count_hint.pack(side="left", padx=8)
        self.density.trace_add("write", lambda *a: self._update_count_hint())
        self.target_count.trace_add("write", lambda *a: self._update_count_hint())
        r = ttk.Frame(of)
        r.pack(fill="x", pady=3)
        ttk.Label(r, text="Exact number", width=16, style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(r, textvariable=self.target_count, width=12).pack(side="left")
        ttk.Label(r, text="optional — overrides the dropdown",
                  style="CardMuted.TLabel").pack(side="left", padx=6)
        self._update_count_hint()
        self._combo_row(of, "Progression", self.progression, ["linear", "loose", "open"])
        # Language only reaches the AI prompt builders (build_prompt & friends);
        # local_quest_doc writes English templates and _prompt_opts carries no
        # language at all. Measured: offline books for English vs Deutsch were
        # md5-identical (a4dc39a9 both). Rather than let the dropdown lie on the
        # offline-only setup (no API key), grey it out and say why.
        lr = ttk.Frame(of)
        lr.pack(fill="x", pady=4)
        ttk.Label(lr, text="Language", width=16, style="CardMuted.TLabel").pack(side="left")
        self.language_combo = ttk.Combobox(
            lr, textvariable=self.language, state="readonly", width=13,
            values=["English", "简体中文", "Deutsch", "Français", "Español",
                    "Português", "Русский", "日本語", "한국어", "Italiano"])
        self.language_combo.pack(side="left")
        self._lang_hint = ttk.Label(lr, text="", style="CardMuted.TLabel")
        self._lang_hint.pack(side="left", padx=8)
        self.api_key.trace_add("write", lambda *a: self._sync_language_state())
        self._sync_language_state()
        self._combo_row(of, "Layout", self.layout, LAYOUTS)
        self._combo_row(of, "Group headers", self.group_style, GROUP_STYLES)
        self._combo_row(of, "Aesthetics", self.aesthetic,
                        ["minimal", "balanced", "decorated", "lavish"])
        # "auto" keeps the derived per-group shapes (hexagon=Tech, gear=boss…);
        # picking one of FTBQ's nine shapes forces it book-wide.
        self._combo_row(of, "Quest shape", self.quest_shape,
                        ["auto"] + QUEST_SHAPES)
        self._combo_row(of, "Rewards", self.reward_level, ["lean", "standard", "generous"])
        sr = ttk.Frame(of)
        sr.pack(fill="x", pady=3)
        ttk.Label(sr, text="Seed", width=14, style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(sr, textvariable=self.run_seed, width=12).pack(side="left")
        ttk.Label(sr, text="auto = a different book every time; a number repeats one",
                  style="CardMuted.TLabel").pack(side="left", padx=8)

        mf = ttk.Labelframe(left, text="MODEL")
        mf.pack(fill="x", pady=(10, 0))
        r3 = ttk.Frame(mf)
        r3.pack(fill="x", pady=3)
        ttk.Label(r3, text="Creativity", width=14, style="CardMuted.TLabel").pack(side="left")
        self._temp_lbl = ttk.Label(r3, text="", style="CardMuted.TLabel")
        ttk.Scale(r3, from_=0.0, to=1.2, variable=self.temperature, length=150,
                  command=lambda v: self._temp_lbl.configure(
                      text="%d%%" % min(100, int(float(v) * 100)))).pack(side="left")
        self._temp_lbl.configure(text="%d%%" % min(100, int(self.temperature.get() * 100)))
        self._temp_lbl.pack(side="left", padx=8)
        r4 = ttk.Frame(mf)
        r4.pack(fill="x", pady=3)
        ttk.Label(r4, text="Max tokens", width=14, style="CardMuted.TLabel").pack(side="left")
        # Same AI-only gating as the Setup copy of this entry - see _sync_ai_state.
        # Creativity above is deliberately NOT gated: it drives the offline
        # layout jitter and quest rows via _opts()["creativity"].
        self._maxtok_gen = ttk.Entry(r4, textvariable=self.max_tokens, width=12)
        self._maxtok_gen.pack(side="left")
        self._ai_hint_gen = ttk.Label(r4, text="", style="CardMuted.TLabel")
        self._ai_hint_gen.pack(side="left", padx=8)
        self._sync_ai_state()
        r5 = ttk.Frame(mf)
        r5.pack(fill="x", pady=3)
        ttk.Label(r5, text="Book title", width=14, style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(r5, textvariable=self.book_title, width=22).pack(side="left")
        r6 = ttk.Frame(mf)
        r6.pack(fill="x", pady=3)
        ttk.Label(r6, text="Book icon", width=14, style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(r6, textvariable=self.book_icon, width=22).pack(side="left")

        vd = ttk.Labelframe(right, text="VISUAL DESIGN")
        vd.pack(fill="x")
        for txt, var in [
            ("Style chapters by group (colour titles, shape quests)", self.opt_style_chapters),
            ("Chapter backgrounds (stock Minecraft textures)", self.opt_decor),
        ]:
            ttk.Checkbutton(vd, text=txt, variable=var, style="Card.TCheckbutton").pack(anchor="w", pady=1)
        ttk.Label(vd, style="CardMuted.TLabel", wraplength=360, justify="left",
                  text="Backgrounds use vanilla advancement-screen textures — no resource "
                       "pack, no downloads, no generated art.").pack(anchor="w", pady=(2, 0))

        tf = ttk.Labelframe(right, text="THEMED QUESTLINES")
        tf.pack(fill="x", pady=(10, 0))
        ttk.Label(tf, style="CardMuted.TLabel", wraplength=360, justify="left",
                  text="One questline per line. Use an idea (\"Steam power\", "
                       "\"Beekeeping\", \"Combat gear\") or an exact item "
                       "(\"minecraft:diamond\"). Each line becomes its own chapter, "
                       "pulling matching items from every mod you picked. "
                       "Leave empty for normal per-mod chapters.").pack(anchor="w", pady=(0, 5))
        self.themes_box = scrolledtext.ScrolledText(tf, height=4, wrap="word")
        style_text(self.themes_box)
        self.themes_box.pack(fill="x")
        if self.themes_text:
            self.themes_box.insert("1.0", self.themes_text)
        ttk.Checkbutton(tf, text="Build ONLY these questlines (skip per-mod chapters)",
                        variable=self.opt_themes_only,
                        style="Card.TCheckbutton").pack(anchor="w", pady=(5, 1))
        ttk.Checkbutton(tf, text="Include the vanilla progression chapters",
                        variable=self.opt_vanilla_ch,
                        style="Card.TCheckbutton").pack(anchor="w", pady=1)
        ttk.Checkbutton(tf, text="Include decoration/furniture mods (lots of filler)",
                        variable=self.opt_decor_mods,
                        style="Card.TCheckbutton").pack(anchor="w", pady=1)

        cf = ttk.Labelframe(right, text="CONVERTER")
        cf.pack(fill="x", pady=(10, 0))
        for txt, var in [("Group chapters by \"group\" field", self.opt_groups),
                         ("Auto-chain undependent chapters", self.opt_chain),
                         ("Add XP reward when a quest has none", self.opt_xp),
                         ("Back up chapters/ before writing", self.opt_backup),
                         ("Warn me if Minecraft is running", self.opt_mcwarn),
                         ("Retro sound effects", self.opt_sound)]:
            ttk.Checkbutton(cf, text=txt, variable=var, style="Card.TCheckbutton").pack(anchor="w", pady=1)

        pv = ttk.Labelframe(pad, text="LIVE PREVIEW  (mock chapter — updates as you change options)")
        pv.pack(fill="x", pady=(12, 0))
        self.preview = QuestPreview(pv, height=240)
        self.preview.pack(fill="x")
        for v in (self.layout, self.aesthetic, self.quest_shape, self.reward_level,
                  self.progression, self.density, self.opt_style_chapters,
                  self.opt_decor, self.temperature):
            v.trace_add("write", lambda *a: self._refresh_preview())
        self.root.after(300, self._refresh_preview)

        act = ttk.Labelframe(pad, text="RUN")
        act.pack(fill="x", pady=(12, 0))
        row = ttk.Frame(act)
        row.pack(fill="x")
        # Offline is the primary path and comes first. It needs no API key,
        # no account and no network, and it is the ONLY path that receives
        # the researched chain order, the guide entry rituals, the playthrough
        # positions and the measured item ranking. AI writes livelier prose;
        # it does not know any of that, so it is the option, not the default.
        # The guided flow comes FIRST and carries the accent, because a person
        # who does not already know the order needs it before they need
        # anything else on this row. It was originally last of six buttons and
        # auto-opened only on a true first run, which meant the person who
        # asked for it could not find it in their own build - a feature nobody
        # can see is not a feature.
        self.btn_wizard = ttk.Button(row, text="🧭  Guided setup",
                                     style="Accent.TButton",
                                     command=self.on_wizard)
        self.btn_wizard.pack(side="left")
        hover_lift(self.btn_wizard)
        self.btn_gen_local = ttk.Button(row, text="✦  Build Quest Book",
                                        command=self.on_generate_local)
        self.btn_gen_local.pack(side="left", padx=6)
        hover_lift(self.btn_gen_local)
        self.btn_gen = ttk.Button(row, text="Generate with AI (optional)",
                                  command=self.on_generate)
        self.btn_gen.pack(side="left", padx=6)
        hover_lift(self.btn_gen)
        b = ttk.Button(row, text="Preview (no write)", command=self.on_preview)
        b.pack(side="left", padx=6)
        hover_lift(b)
        self.btn_cancel = ttk.Button(row, text="Cancel", command=self._do_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        MoonRollButton(row, self.on_randomize).pack(side="right")
        ttk.Label(act, style="CardMuted.TLabel", wraplength=560, justify="left",
                  text="Build Quest Book needs no API key and no internet. It reads your mods "
                       "and builds everything — chapters, quests, dependencies, layout, groups, "
                       "rewards — and it is the only path that uses the researched progression "
                       "order, the guide entry steps and the measured item ranking that ship "
                       "with the app. AI writes more varied prose but knows none of "
                       "that.").pack(anchor="w", pady=(6, 0))
        row2 = ttk.Frame(act)
        row2.pack(fill="x", pady=(8, 0))
        for txt, cmd in [("Save prompt to file", self.on_save_prompt),
                         ("Convert JSON file", self.on_convert_file),
                         ("Open output folder", self.on_open_out),
                         ("Clean old backups", self.on_clean_backups)]:
            b = ttk.Button(row2, text=txt, command=cmd)
            b.pack(side="left", padx=(0, 6))
            hover_lift(b)

        pf = ttk.Labelframe(pad, text="PASTE QUEST JSON FROM A WEB AI")
        pf.pack(fill="both", expand=True, pady=(12, 0))
        self.paste = scrolledtext.ScrolledText(pf, height=5, wrap="none")
        style_text(self.paste)
        self.paste.pack(fill="both", expand=True)
        ttk.Button(pf, text="Convert pasted JSON", style="Accent.TButton",
                   command=self.on_convert_paste).pack(anchor="e", pady=(8, 0))

    # ---- Rewards & Tasks editor tab -------------------------------- #
    TASK_FIELDS = {
        "item": [("item", "minecraft:diamond"), ("count", "1")],
        "kill": [("entity", "minecraft:zombie"), ("value", "10")],
        "checkmark": [("title", "I'm ready!")],
        "advancement": [("advancement", "minecraft:story/mine_diamond")],
        "dimension": [("dimension", "minecraft:the_nether")],
        "location": [("dimension", "minecraft:overworld"), ("x", "0"), ("y", "64"),
                     ("z", "0"), ("w", "8"), ("h", "8"), ("d", "8")],
        "biome": [("biome", "minecraft:plains")],
        "structure": [("structure", "minecraft:village")],
        "stat": [("stat", "minecraft:mob_kills"), ("value", "50")],
        "fluid": [("fluid", "minecraft:water"), ("amount", "1000")],
        "energy": [("value", "10000")],
        "stage": [("stage", "example_stage")],
        "observation": [("to_observe", "minecraft:beacon")],
        "xp": [("value", "30")],
    }
    REWARD_FIELDS = {
        "item": [("item", "minecraft:diamond"), ("count", "1")],
        "xp": [("xp", "100")],
        "xp_levels": [("xp_levels", "3")],
        "command": [("command", "/say congrats @p")],
        "toast": [("description", "Well done!")],
        "advancement": [("advancement", "minecraft:story/mine_diamond")],
        "stage": [("stage", "example_stage")],
        "choice": [("table", "rare")],
        "loot": [("table", "rare")],
    }
    _NUM_FIELDS = {"count", "value", "x", "y", "z", "w", "h", "d", "amount",
                   "xp", "xp_levels", "weight", "loot_size"}

    def _tab_library(self):
        """Saved books. The answer to "I liked that one, now it is gone".

        Every build differs - reward picks most visibly - so before this the
        only copy of a book you liked was whatever happened to be sitting in
        the output folder, and the next build replaced it. Snapshots live
        beside the config, not inside the pack, so a pack update cannot take
        them.
        """
        t = ttk.Frame(self.nb)
        self.nb.add(t, text="Library")
        pad = self._scrollframe(t)

        cur = ttk.Labelframe(pad, text="BOOK CURRENTLY IN YOUR OUTPUT FOLDER")
        cur.pack(fill="x")
        self.lib_current = ttk.Label(cur, style="CardMuted.TLabel",
                                     wraplength=760, justify="left", text="")
        self.lib_current.pack(anchor="w", pady=(0, 8))
        r = ttk.Frame(cur)
        r.pack(fill="x")
        ttk.Label(r, text="Save it as", width=12).pack(side="left")
        self.lib_name = tk.StringVar(value="")
        ttk.Entry(r, textvariable=self.lib_name, width=34).pack(side="left")
        ttk.Button(r, text="💾  Save this book", style="Accent.TButton",
                   command=self.on_lib_save).pack(side="left", padx=6)
        ttk.Button(r, text="Refresh", command=self._refresh_library).pack(side="left")

        sv = ttk.Labelframe(pad, text="SAVED BOOKS")
        sv.pack(fill="both", expand=True, pady=(14, 0))
        ttk.Label(sv, style="CardMuted.TLabel", wraplength=760, justify="left",
                  text="Restoring puts a saved book back into your output "
                       "folder. Whatever is there now is backed up first, and "
                       "Minecraft must be closed - FTB Quests keeps the book "
                       "in memory and would overwrite it on exit."
                  ).pack(anchor="w", pady=(0, 8))
        self.lib_list = tk.Listbox(sv, height=9, activestyle="none")
        self.lib_list.pack(fill="both", expand=True)
        br = ttk.Frame(sv)
        br.pack(fill="x", pady=(8, 0))
        ttk.Button(br, text="↩  Restore selected", style="Accent.TButton",
                   command=self.on_lib_restore).pack(side="left")
        ttk.Button(br, text="Open folder",
                   command=self.on_lib_open).pack(side="left", padx=6)
        ttk.Button(br, text="Delete",
                   command=self.on_lib_delete).pack(side="left")
        self._refresh_library()

    def _refresh_library(self):
        outs = self._out_dirs()
        if outs:
            ch, q = _book_stats(Path(outs[0]))
            self.lib_current.configure(
                text=("%d chapters, %d quests in %s"
                      % (ch, q, outs[0])) if ch else
                     ("No book found in %s yet." % outs[0]))
            if not self.lib_name.get().strip():
                self.lib_name.set(time.strftime("%Y-%m-%d book"))
        else:
            self.lib_current.configure(text="Set an output folder on the Setup tab.")
        self.lib_list.delete(0, "end")
        self._lib_rows = library_books()
        for name, when, ch, q in self._lib_rows:
            self.lib_list.insert("end", "%-30s  %s   %d chapters, %d quests"
                                 % (name[:30], when, ch, q))
        if not self._lib_rows:
            self.lib_list.insert("end", "  (nothing saved yet)")

    def _lib_selected(self):
        sel = self.lib_list.curselection()
        if not sel or not getattr(self, "_lib_rows", None):
            messagebox.showinfo(APP_NAME, "Pick a saved book from the list first.")
            return None
        return self._lib_rows[sel[0]][0]

    def on_lib_save(self):
        outs = self._out_dirs()
        if not outs:
            messagebox.showwarning(APP_NAME, "Set an output folder first.")
            return
        ch, _q = _book_stats(Path(outs[0]))
        if not ch:
            messagebox.showwarning(APP_NAME, "There is no book in the output "
                                             "folder to save yet.")
            return
        try:
            save_book_to_library(Path(outs[0]), self.lib_name.get(), self.log)
        except Exception as e:
            messagebox.showerror(APP_NAME, "Could not save: %s" % e)
            return
        self._refresh_library()
        self.set_status("saved to library")

    def on_lib_restore(self):
        name = self._lib_selected()
        if not name:
            return
        outs = self._out_dirs()
        if not outs:
            messagebox.showwarning(APP_NAME, "Set an output folder first.")
            return
        if not messagebox.askyesno(
                APP_NAME,
                "Restore %r into %s? The book currently there will be "
                "backed up first." % (name, outs[0])):
            return
        try:
            restore_book_from_library(name, Path(outs[0]), self.log)
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self._refresh_library()
        self.set_status("restored '%s'" % name)

    def on_lib_delete(self):
        name = self._lib_selected()
        if not name:
            return
        if not messagebox.askyesno(APP_NAME, "Delete the saved book '%s'?" % name):
            return
        shutil.rmtree(LIBRARY_DIR / name, ignore_errors=True)
        self._refresh_library()

    def on_lib_open(self):
        name = self._lib_selected()
        d = LIBRARY_DIR / name if name else None
        if d and d.exists():
            os.startfile(str(d))

    def _tab_editor(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text="Rewards & Tasks")
        self._edit_doc = None
        self._edit_qref = None       # (chapter_idx, quest_idx)
        self._task_rows = []
        self._reward_rows = []

        top = ttk.Frame(t)
        top.pack(fill="x", padx=14, pady=(12, 6))
        b = ttk.Button(top, text="Load current book", command=self.on_editor_load)
        b.pack(side="left")
        hover_lift(b)
        self.editor_lbl = ttk.Label(top, text="reads chapters/ from the Output folder",
                                    style="Muted.TLabel")
        self.editor_lbl.pack(side="left", padx=10)
        b = ttk.Button(top, text="✦ AI: fill in rewards", command=self.on_editor_ai_rewards)
        b.pack(side="right")
        b2 = ttk.Button(top, text="Write book to Output", style="Accent.TButton",
                        command=self.on_editor_write)
        b2.pack(side="right", padx=6)

        body = ttk.Frame(t)
        body.pack(fill="both", expand=True, padx=12, pady=6)
        # left: quest tree + chapter management
        lf = ttk.Frame(body)
        lf.pack(side="left", fill="y")
        tw = ttk.Frame(lf)
        tw.pack(side="top", fill="y", expand=True)
        self._qtree = ttk.Treeview(tw, show="tree", height=17, selectmode="browse")
        self._qtree.pack(side="left", fill="y", expand=True)
        tsb = ttk.Scrollbar(tw, orient="vertical", command=self._qtree.yview)
        tsb.pack(side="left", fill="y")
        self._qtree.configure(yscrollcommand=tsb.set)
        self._qtree.column("#0", width=250)
        self._qtree.bind("<<TreeviewSelect>>", lambda e: self._editor_select())
        chbar = ttk.Frame(lf)
        chbar.pack(side="top", fill="x", pady=(6, 0))
        ttk.Button(chbar, text="New chapter", command=self._editor_new_chapter).pack(side="left")
        ttk.Button(chbar, text="🗑 Remove chapter", style="Warn.TButton",
                   command=self._editor_del_chapter).pack(side="left", padx=6)

        # right: task + reward editors
        rf = self._scrollframe(body)
        self._edit_panel = rf
        self._qtitle = ttk.Label(rf, text="select a quest on the left", style="Head.TLabel")
        self._qtitle.pack(anchor="w")
        self._tasks_box = ttk.Labelframe(rf, text="TASKS")
        self._tasks_box.pack(fill="x", pady=(8, 0))
        self._add_task_menu(self._tasks_box)
        self._rewards_box = ttk.Labelframe(rf, text="REWARDS")
        self._rewards_box.pack(fill="x", pady=(10, 0))
        self._add_reward_menu(self._rewards_box)
        ttk.Button(rf, text="Apply to this quest", command=self._editor_apply_quest).pack(
            anchor="w", pady=(10, 0))

        pool = ttk.Labelframe(rf, text="REWARD POOLS  (weighted loot tables)")
        pool.pack(fill="x", pady=(14, 0))
        ttk.Label(pool, style="CardMuted.TLabel", wraplength=520, justify="left",
                  text="One pool per line as JSON, e.g.  "
                       '{"id":"rare","loot_size":1,"rewards":[{"item":"minecraft:diamond","count":2,'
                       '"weight":10},{"type":"xp","xp":200,"weight":4}]}').pack(anchor="w", pady=(0, 4))
        self._pools_box = scrolledtext.ScrolledText(pool, height=5, wrap="none")
        style_text(self._pools_box)
        self._pools_box.pack(fill="x")

    def _add_task_menu(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 4))
        v = tk.StringVar(value="+ add task")
        cb = ttk.Combobox(bar, textvariable=v, state="readonly", width=16,
                          values=list(self.TASK_FIELDS))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: (self._add_task_row({"type": v.get()}),
                                                   v.set("+ add task")))

    def _add_reward_menu(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 4))
        v = tk.StringVar(value="+ add reward")
        cb = ttk.Combobox(bar, textvariable=v, state="readonly", width=16,
                          values=list(self.REWARD_FIELDS))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: (self._add_reward_row({"type": v.get()}),
                                                   v.set("+ add reward")))

    def _kv_row(self, box, kind, data, store):
        fields_map = self.TASK_FIELDS if kind == "task" else self.REWARD_FIELDS
        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        tvar = tk.StringVar(value=data.get("type", list(fields_map)[0]))
        entries = {}
        holder = ttk.Frame(row)

        def rebuild(*_):
            for w in holder.winfo_children():
                w.destroy()
            entries.clear()
            for fname, default in fields_map.get(tvar.get(), []):
                ttk.Label(holder, text=fname, style="CardMuted.TLabel").pack(side="left", padx=(4, 2))
                ev = tk.StringVar(value=str(data.get(fname, default)))
                w = ttk.Entry(holder, textvariable=ev,
                              width=8 if fname in self._NUM_FIELDS else 18)
                w.pack(side="left")
                entries[fname] = ev

        cb = ttk.Combobox(row, textvariable=tvar, state="readonly", width=13,
                          values=list(fields_map))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", rebuild)
        holder.pack(side="left", fill="x", expand=True)
        rebuild()

        def remove():
            store.remove(rec)
            row.destroy()
        ttk.Button(row, text="×", width=2, command=remove).pack(side="right")

        def read():
            out = {"type": tvar.get()}
            for fname, ev in entries.items():
                val = ev.get().strip()
                if fname in self._NUM_FIELDS:
                    try:
                        val = int(float(val))
                    except ValueError:
                        val = 0
                out[fname] = val
            return out
        rec = {"read": read}
        store.append(rec)

    def _add_task_row(self, data):
        self._kv_row(self._tasks_box, "task", data, self._task_rows)

    def _add_reward_row(self, data):
        self._kv_row(self._rewards_box, "reward", data, self._reward_rows)

    def _clear_rows(self):
        for box, store in ((self._tasks_box, self._task_rows), (self._rewards_box, self._reward_rows)):
            for rec in list(store):
                store.remove(rec)
            for w in box.winfo_children()[1:]:   # keep the +add menu (first child)
                w.destroy()

    def on_editor_load(self):
        out = self._first_out()
        if not out:
            return

        def job():
            doc = quests_to_doc(out)
            self.events.put(("editor_doc", doc))
            n = sum(len(c["quests"]) for c in doc["chapters"])
            return "loaded %d chapters, %d quests" % (len(doc["chapters"]), n)
        self._run(job, "Reading quest book")

    def _editor_populate_tree(self, doc):
        self._edit_doc = doc
        # Snapshot to disk immediately. Editor state used to live only in memory,
        # so a long AI reward run could be thrown away by closing the app or by
        # running a fresh generation over it. Recoverable via "Load snapshot".
        try:
            EDITOR_SNAPSHOT.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
            self.log("  editor snapshot saved -> %s" % EDITOR_SNAPSHOT.name)
        except Exception as e:
            self.log("  ! could not save editor snapshot: %s" % e)
        self._qtree.delete(*self._qtree.get_children())
        for ci, ch in enumerate(doc["chapters"]):
            cid = self._qtree.insert("", "end", iid="ch:%d" % ci,
                                     text="  " + re.sub(r"&.", "", ch.get("title", "?")),
                                     open=False)
            for qi, q in enumerate(ch["quests"]):
                self._qtree.insert(cid, "end", iid="%d:%d" % (ci, qi),
                                   text="   " + re.sub(r"&.", "", q.get("title", "?"))[:44])
        self.editor_lbl.configure(text="%d chapters · %d quests · click one"
                                  % (len(doc["chapters"]),
                                     sum(len(c["quests"]) for c in doc["chapters"])))

    def _editor_select(self):
        sel = self._qtree.selection()
        if not sel or ":" not in sel[0] or sel[0].startswith("ch:") or not self._edit_doc:
            return
        ci, qi = map(int, sel[0].split(":"))
        self._edit_qref = (ci, qi)
        q = self._edit_doc["chapters"][ci]["quests"][qi]
        self._qtitle.configure(text=re.sub(r"&.", "", q.get("title", "?")))
        self._clear_rows()
        for t_ in q.get("tasks") or []:
            self._add_task_row(dict(t_))
        for r_ in q.get("rewards") or []:
            self._add_reward_row(dict(r_))

    def _editor_sel_chapter(self):
        """Return the chapter index for whatever is selected (chapter or quest node)."""
        sel = self._qtree.selection()
        if not sel or not self._edit_doc:
            return None
        n = sel[0]
        if n.startswith("ch:"):
            ci = int(n.split(":")[1])
        elif ":" in n:
            ci = int(n.split(":")[0])
        else:
            return None
        return ci if 0 <= ci < len(self._edit_doc["chapters"]) else None

    def _editor_del_chapter(self):
        ci = self._editor_sel_chapter()
        if ci is None:
            messagebox.showinfo(APP_NAME, "Select a chapter (or one of its quests) first.")
            return
        ch = self._edit_doc["chapters"][ci]
        name = re.sub(r"&.", "", ch.get("title", "?"))
        if not messagebox.askyesno(
                APP_NAME,
                "Remove the whole chapter \"%s\"  (%d quests)?\n\nIt is deleted from the book "
                "when you click \"Write book to Output\". Other chapters that depended on its "
                "quests just lose those links." % (name, len(ch.get("quests") or []))):
            return
        del self._edit_doc["chapters"][ci]
        self._edit_qref = None
        self._clear_rows()
        self._qtitle.configure(text="chapter removed — Write book to apply")
        self._editor_populate_tree(self._edit_doc)
        self.set_status("removed chapter '%s' (not written yet)" % name)

    def _editor_new_chapter(self):
        if not self._edit_doc:
            self._edit_doc = {"chapters": []}
        title = "New Chapter %d" % (len(self._edit_doc["chapters"]) + 1)
        self._edit_doc["chapters"].append({
            "id": "new_chapter_%d" % random.randint(1000, 9999),
            "title": title,
            "quests": [{"id": "nc_%d" % random.randint(10000, 99999), "x": 0.0, "y": 0.0,
                        "title": "First quest", "tasks": [{"type": "checkmark", "title": "Start"}]}],
        })
        self._editor_populate_tree(self._edit_doc)
        self.set_status("added '%s' (not written yet)" % title)

    def _editor_apply_quest(self):
        if not self._edit_qref or not self._edit_doc:
            return
        ci, qi = self._edit_qref
        q = self._edit_doc["chapters"][ci]["quests"][qi]
        q["tasks"] = [rec["read"]() for rec in self._task_rows] or q.get("tasks")
        q["rewards"] = [rec["read"]() for rec in self._reward_rows]
        if not q["rewards"]:
            q.pop("rewards", None)
        self.set_status("applied to '%s' (not written yet — use 'Write book to Output')"
                        % re.sub(r"&.", "", q.get("title", "?")))

    def _editor_pools(self):
        pools = []
        for line in self._pools_box.get("1.0", "end").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pools.append(json.loads(line))
            except Exception as e:
                self.log("  ! bad pool line: %s (%s)" % (line[:60], e))
        return pools

    def on_editor_write(self):
        if not self._edit_doc:
            messagebox.showwarning(APP_NAME, "Load the book first.")
            return
        outs = self._out_dirs()
        if not outs or not self._guard_mc():
            return
        self._editor_apply_quest()
        doc = json.loads(json.dumps(self._edit_doc))   # deep copy
        pools = self._editor_pools()
        if pools:
            doc["reward_tables"] = pools

        def job():
            warns = []
            chapters, groups, extra = build_chapters(doc, warns.append,
                                                     self._opts(preserve_ids=True))
            for out in outs:
                self.log("-> %s" % out)
                write_snbt(out, chapters, groups, self.opt_backup.get(), self.log,
                           self._book(), extra.get("reward_tables"))
            for w in warns[:40]:
                self.log("  ! " + w)
            return "wrote edited book (%d chapters, %d pools)" % (len(chapters), len(pools))
        self._run(job, "Writing edited book", notify=True)

    AI_REWARD_BATCH = 40

    def on_editor_ai_rewards(self):
        if not self._edit_doc:
            messagebox.showwarning(APP_NAME, "Load the book first.")
            return
        ai = self._ai_ready()
        if not ai:
            return
        url, model, key = ai
        mt = min(self._int(self.max_tokens.get(), 32768), 8000)
        temp = round(self.temperature.get(), 2)
        scan = self.scan_result or {"mods": [], "items": {}, "entities": {}, "craftable": {}}
        doc = json.loads(json.dumps(self._edit_doc))
        lvl = self.reward_level.get()

        # Only the quests that actually need work. Sending the whole book and
        # asking the model to echo it back blows past any token cap — for a
        # 579-quest book that was a 420k-char prompt and a 40k-token answer,
        # which just retried until it timed out.
        todo = []
        for ci, ch in enumerate(doc.get("chapters") or []):
            qs = ch.get("quests") or []
            for qi, q in enumerate(qs):
                if len(q.get("rewards") or []) >= 2:
                    continue
                t = (q.get("tasks") or [{}])[0]
                todo.append({
                    "ci": ci, "qi": qi,
                    "id": str(q.get("id") or "%d:%d" % (ci, qi)),
                    "title": re.sub(r"&.", "", str(q.get("title", ""))),
                    "goal": t.get("item") or t.get("entity") or t.get("type", "checkmark"),
                    "kind": t.get("type", "item"),
                    "chapter": re.sub(r"&.", "", str(ch.get("title", ""))),
                    "step": "%d/%d" % (qi + 1, len(qs)),
                })
        if not todo:
            messagebox.showinfo(APP_NAME, "Every quest already has rewards.")
            return

        craft = scan.get("craftable", {}) or {}
        batches = [todo[i:i + self.AI_REWARD_BATCH]
                   for i in range(0, len(todo), self.AI_REWARD_BATCH)]

        def pool_for(batch):
            """A small, relevant item pool: the mods this batch touches."""
            ns = {str(b["goal"]).split(":")[0] for b in batch if ":" in str(b["goal"])}
            ids = []
            for n in sorted(ns):
                have = sorted(craft.get(n) or scan["items"].get(n) or [])
                ids += _functional_ids(have, 40)
            ids += list(_VANILLA_ITEMS[:120])
            return ids[:300]

        def job():
            done = 0
            got = 0
            pools = []
            for bi, batch in enumerate(batches):
                if self._cancel:
                    break
                frac = 0.08 + 0.84 * (bi / len(batches))
                self.phase(frac, "rewards: batch %d/%d  (%d quests)"
                           % (bi + 1, len(batches), len(batch)))
                lines = "\n".join(
                    "%s | %s | %s %s | chapter %s | step %s"
                    % (b["id"], b["title"], b["kind"], b["goal"], b["chapter"], b["step"])
                    for b in batch)
                ask = (
                    "You are balancing rewards for a Minecraft FTB Quests book.\n"
                    "For EACH quest line below decide a fitting reward. Reward richness: %s. "
                    "Early/low steps get small rewards, late steps and boss kills get big ones.\n\n"
                    "Reward objects you may use:\n"
                    '  {\"type\":\"item\",\"item\":\"<id>\",\"count\":N}\n'
                    '  {\"type\":\"xp\",\"xp\":N}          {\"type\":\"xp_levels\",\"xp_levels\":N}\n\n'
                    "Use ONLY item ids from this list (any minecraft: id is also fine):\n%s\n\n"
                    "QUESTS  (id | title | task | chapter | step):\n%s\n\n"
                    "Reply with ONLY a JSON object mapping each quest id to its reward array:\n"
                    '{\"<quest id>\":[{\"type\":\"xp\",\"xp\":100}], ...}\n'
                    "No prose, no markdown, no other keys."
                    % (lvl, ", ".join(pool_for(batch)), lines))
                if bi == 0:
                    ask += ("\nAlso add one extra key \"reward_tables\": a list of 2-3 weighted "
                            'pools like {"id":"rare","title":"Rare Crate","loot_size":1,'
                            '"rewards":[{"item":"<id>","count":1,"weight":5}]}.')
                self.log("  batch %d/%d: %d quests, prompt %d chars"
                         % (bi + 1, len(batches), len(batch), len(ask)))
                try:
                    raw = call_ai(url, key, model, ask, mt, self.log,
                                  lambda: self._cancel, temp,
                                  read_timeout=120, max_rounds=2)
                    data = extract_json(raw)
                except Exception as e:
                    self.log("  ! batch %d failed (%s) — keeping existing rewards" % (bi + 1, e))
                    continue
                if bi == 0 and isinstance(data.get("reward_tables"), list):
                    pools = data["reward_tables"]
                for b in batch:
                    rw = data.get(b["id"])
                    if isinstance(rw, dict):
                        rw = [rw]
                    if isinstance(rw, list) and rw:
                        doc["chapters"][b["ci"]]["quests"][b["qi"]]["rewards"] = rw
                        got += 1
                done += len(batch)
            self.phase(0.95, "merging...")
            self.events.put(("editor_doc", doc))
            if pools:
                self.events.put(("editor_pools", pools))
            self.phase(1.0, "done")
            return ("AI set rewards on %d of %d quests, %d pools — review, then Write book"
                    % (got, len(todo), len(pools)))
        self._run(job, "AI: generating rewards", notify=True)

    def _tab_repair(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text="Repair")
        pad = self._scrollframe(t)

        info = ttk.Labelframe(pad, text="FIX AN EXISTING QUEST BOOK")
        info.pack(fill="x")
        ttk.Label(info, wraplength=760, style="CardMuted.TLabel", justify="left",
                  text="Works on whatever is already in the Output folder's chapters/ "
                       "(from a bad import, a broken generation, or hand edits). "
                       "Close Minecraft first.").pack(anchor="w", pady=(0, 8))

        r = ttk.Frame(info)
        r.pack(fill="x")
        b = ttk.Button(r, text="Scan quest folder", command=self.on_repair_scan)
        b.pack(side="left")
        hover_lift(b)
        self.repair_lbl = ttk.Label(r, text="", style="CardMuted.TLabel")
        self.repair_lbl.pack(side="left", padx=10)

        rf = ttk.Labelframe(pad, text="REPAIR  (parse → normalise → rewrite)")
        rf.pack(fill="x", pady=(12, 0))
        ttk.Label(rf, style="CardMuted.TLabel", justify="left", wraplength=760,
                  text="• spells out '&' in titles/descriptions   • regenerates missing / "
                       "duplicate IDs\n• drops dead dependency links   • fills empty quests "
                       "with a checkmark   • re-serialises cleanly").pack(anchor="w", pady=(0, 8))
        b = ttk.Button(rf, text="🛠  Repair quest files", style="Accent.TButton",
                       command=self.on_repair)
        b.pack(anchor="w")

        pf = ttk.Labelframe(pad, text="POLISH WORDING WITH AI  (words only - nothing else can change)")
        pf.pack(fill="x", pady=(12, 0))
        ttk.Label(pf, style="CardMuted.TLabel", justify="left", wraplength=760,
                  text="Rewrites quest titles, descriptions, chapter names and group names "
                       "-- and ONLY those. Items, counts, rewards, dependencies and layout "
                       "are copied across untouched, so the AI cannot ask you for an item "
                       "that does not exist or add a quest you cannot finish. This is the "
                       "safe way to use AI on a finished book.").pack(anchor="w", pady=(0, 8))
        r1 = ttk.Frame(pf)
        r1.pack(fill="x")
        self._polish_btn = ttk.Button(r1, text="✒  Polish wording with AI",
                                      style="Accent.TButton", command=self.on_polish)
        self._polish_btn.pack(side="left")
        ttk.Label(r1, text="recommended", style="CardMuted.TLabel").pack(side="left", padx=10)

        imf = ttk.Labelframe(pad, text="IMPROVE WITH AI  (also changes structure)")
        imf.pack(fill="x", pady=(12, 0))
        ttk.Label(imf, style="CardMuted.TLabel", justify="left", wraplength=760,
                  text="The wider version: as well as the wording it adds rewards, rewrites "
                       "item IDs and invents side quests. That is where the AI makes its "
                       "mistakes, so prefer Polish above unless you want new content. "
                       "Needs a scan (Setup tab) for the item-ID list.").pack(
            anchor="w", pady=(0, 8))
        r2 = ttk.Frame(imf)
        r2.pack(fill="x")
        b = ttk.Button(r2, text="✦  Improve current book with AI", style="Accent.TButton",
                       command=self.on_improve)
        b.pack(side="left")
        ttk.Label(r2, text="uses the Model + options from the Generate tab",
                  style="CardMuted.TLabel").pack(side="left", padx=10)


    # ---- Import tab --------------------------------------------------- #
    def _tab_import(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text="Import")
        pad = self._scrollframe(t)

        src = ttk.Labelframe(pad, text="COPY QUESTLINES FROM ANOTHER MODPACK")
        src.pack(fill="x")
        ttk.Label(src, wraplength=760, style="CardMuted.TLabel", justify="left",
                  text="Take any pack's chapters 1:1 - titles, layout, rewards, all "
                       "exactly as its author wrote them. Point at an installed "
                       "instance folder, a downloaded .zip/.mrpack, or type a pack "
                       "name to fetch it from Modrinth. Quests for mods you don't "
                       "have are dropped automatically (they could never be "
                       "completed). CurseForge-only packs (All the Mods, etc.) are "
                       "not on Modrinth - install them in the CurseForge app, then "
                       "Browse to the instance folder. Imports are for your own "
                       "book - sharing a pack "
                       "with someone else's questline needs their permission."
                  ).pack(anchor="w", pady=(0, 8))
        r = ttk.Frame(src)
        r.pack(fill="x")
        self.import_src = tk.StringVar(value="")
        # Defaults to the book the rest of the app is pointed at, so the
        # common case needs no thought; changing it here does not disturb the
        # Setup tab, because importing somebody else's chapters into a second
        # book is a reasonable thing to want.
        self.import_dest = tk.StringVar(
            value=self.out_dir1.get().strip()
            or _quests_dir_for(self.mods_dir.get().strip()))
        ttk.Entry(r, textvariable=self.import_src, width=52).pack(side="left")
        b = ttk.Button(r, text="Browse folder...", command=self._import_browse_dir)
        b.pack(side="left", padx=(6, 0))
        b2 = ttk.Button(r, text="Browse zip...", command=self._import_browse_zip)
        b2.pack(side="left", padx=(6, 0))
        b3 = ttk.Button(r, text="Find chapters", style="Accent.TButton",
                        command=self.on_import_find)
        b3.pack(side="left", padx=(10, 0))
        self.import_lbl = ttk.Label(src, text="", style="CardMuted.TLabel")
        self.import_lbl.pack(anchor="w", pady=(6, 0))

        dst = ttk.Labelframe(pad, text="INTO WHICH MODPACK")
        dst.pack(fill="x", pady=(12, 0))
        ttk.Label(dst, wraplength=760, style="CardMuted.TLabel", justify="left",
                  text="The book these chapters are copied into. It starts as the "
                       "pack you scanned, which is what the \u201cMod installed?\u201d "
                       "column below is answered against - point it somewhere else "
                       "and that column is answering about a different pack.").pack(
            anchor="w", pady=(0, 8))
        if getattr(self, "_instances", None):
            di = ttk.Frame(dst)
            di.pack(fill="x", pady=(0, 4))
            ttk.Label(di, text="Detected instances", width=18).pack(side="left")
            self._import_inst = tk.StringVar(value="")
            dcb = ttk.Combobox(di, textvariable=self._import_inst, state="readonly",
                               values=[lbl for lbl, _ in self._instances])
            dcb.pack(side="left", fill="x", expand=True)
            dcb.bind("<<ComboboxSelected>>", self._on_pick_import_dest)
        dr = ttk.Frame(dst)
        dr.pack(fill="x")
        ttk.Label(dr, text="Book folder", width=18).pack(side="left")
        ttk.Entry(dr, textvariable=self.import_dest).pack(
            side="left", fill="x", expand=True)
        ttk.Button(dr, text="Browse",
                   command=lambda: self._b_out(self.import_dest)).pack(
            side="left", padx=(6, 0))
        self.import_dest_lbl = ttk.Label(dst, text="", style="CardMuted.TLabel")
        self.import_dest_lbl.pack(anchor="w", pady=(6, 0))
        self.import_dest.trace_add("write", lambda *_a: self._update_import_dest())
        self._update_import_dest()

        lst = ttk.Labelframe(pad, text="CHAPTERS FOUND  (select the ones to copy)")
        lst.pack(fill="both", expand=True, pady=(12, 0))
        cols = ("chapter", "mod", "quests", "have")
        self.import_tree = ttk.Treeview(lst, columns=cols, show="headings",
                                        height=12, selectmode="extended")
        for c, w, txt in (("chapter", 320, "Chapter"), ("mod", 170, "Mod"),
                          ("quests", 70, "Quests"), ("have", 130, "Mod installed?")):
            self.import_tree.heading(c, text=txt)
            self.import_tree.column(c, width=w, anchor="w")
        self.import_tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(lst, orient="vertical", command=self.import_tree.yview)
        sb.pack(side="right", fill="y")
        self.import_tree.configure(yscrollcommand=sb.set)

        act = ttk.Frame(pad)
        act.pack(fill="x", pady=(10, 0))
        self.import_merge = tk.BooleanVar(value=False)
        cbm = ttk.Checkbutton(
            act, variable=self.import_merge,
            text="Blend into my normal groups (Magic, Tech...) instead of one Imported group")
        cbm.pack(side="left", padx=(0, 14))
        self.import_remix = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(
            act, variable=self.import_remix,
            text="Remix (keep the progression, regenerate all text, layout & rewards)")
        cb.pack(side="left", padx=(0, 14))
        ttk.Label(act, text="Chapter group:", style="CardMuted.TLabel").pack(side="left")
        self.import_group = tk.StringVar(value="Imported")
        ttk.Entry(act, textvariable=self.import_group, width=24).pack(side="left", padx=6)
        b4 = ttk.Button(act, text="Import selected into my book",
                        style="Accent.TButton", command=self.on_import_install)
        b4.pack(side="left", padx=(12, 0))
        self._import_rows = []

    def _on_pick_import_dest(self, _e=None):
        lbl = self._import_inst.get()
        for name, mods in self._instances:
            if name == lbl:
                self.import_dest.set(_quests_dir_for(mods))
                return

    def _update_import_dest(self):
        """Say which pack the destination is, and whether it is the scanned one."""
        dest = self.import_dest.get().strip()
        if not dest:
            self.import_dest_lbl.configure(text="no destination set")
            return
        d_inst = self._instance_of(dest)
        s_inst = self._instance_of(self.mods_dir.get().strip())
        name = Path(d_inst).name if d_inst else dest
        if d_inst and s_inst and d_inst != s_inst:
            self.import_dest_lbl.configure(
                text="writing into %s - a DIFFERENT pack from the one you "
                     "scanned (%s), so the Mod installed? column is about %s, "
                     "not about where these chapters are going"
                     % (name, Path(s_inst).name, Path(s_inst).name))
        else:
            self.import_dest_lbl.configure(text="writing into %s" % name)

    def _import_browse_dir(self):
        from tkinter import filedialog
        start = Path.home() / "curseforge" / "minecraft" / "Instances"
        d = filedialog.askdirectory(
            title="Pick a modpack instance folder",
            initialdir=str(start) if start.is_dir() else str(Path.home()))
        if d:
            self.import_src.set(d)

    def _import_browse_zip(self):
        from tkinter import filedialog
        f = filedialog.askopenfilename(
            title="Pick a modpack archive",
            filetypes=[("Modpack", "*.zip *.mrpack"), ("All files", "*.*")])
        if f:
            self.import_src.set(f)

    def on_import_find(self):
        srcv = self.import_src.get().strip()
        if not srcv:
            messagebox.showwarning(APP_NAME, "Enter a pack name, folder or zip first.")
            return
        self.import_lbl.configure(text="searching...")

        def work():
            try:
                rows = find_pack_chapters(srcv, self.log)
            except Exception as e:
                rows = []
                self.log("import search failed: %s" % e)
            self.root.after(0, lambda: self._import_show(rows))
        threading.Thread(target=work, daemon=True).start()

    def _import_show(self, rows):
        self._import_rows = rows
        tr = self.import_tree
        for i in tr.get_children():
            tr.delete(i)
        have = set((self.scan_result or {}).get("items") or {})
        for i, r in enumerate(rows):
            ok = "yes" if r["mod"] in have else ("vanilla" if r["mod"] == "minecraft"
                                                 else "NO - will thin out")
            tr.insert("", "end", iid=str(i),
                      values=(r["title"], r["mod"], r["nquests"], ok))
        self.import_lbl.configure(
            text="%d chapter(s) found - select and press Import" % len(rows))

    def _guard_import(self, dest) -> bool:
        """Confirm before copying chapters into a pack they were not checked
        against. Returns True to proceed.

        _guard_pack compares the mods folder against the OUTPUT folders, and
        import writes somewhere it chose itself, so that guard cannot see this
        one. The consequence is specific enough to be worth its own sentence:
        the chapters were filtered against the scanned pack's mod list, so
        into a different pack they bring quests for mods that are not there.
        """
        d_inst = self._instance_of(dest)
        s_inst = self._instance_of(self.mods_dir.get().strip())
        if not d_inst or not s_inst or d_inst == s_inst:
            return True
        nl = chr(10)
        return messagebox.askyesno(APP_NAME, nl.join([
            "These chapters were checked against a different pack.", "",
            "Checked against:", "    " + (Path(s_inst).name or s_inst), "",
            "Importing into:", "    " + (Path(d_inst).name or d_inst), "",
            "The Mod installed? column is about the first one. Quests for "
            "mods the second pack does not have will show in game as a purple "
            "\u201cMissing Item\u201d and can never be completed.", "",
            "Import anyway?"]), icon="warning", default="no")

    def on_import_install(self):
        if self._need_scan():
            return
        sel = [int(i) for i in self.import_tree.selection()]
        if not sel:
            messagebox.showwarning(APP_NAME, "Select at least one chapter in the list.")
            return
        dest = self.import_dest.get().strip()
        if not dest:
            messagebox.showwarning(
                APP_NAME, "Pick the modpack to import into, above the list.")
            return
        outs = [Path(dest)]
        # The same gate the generate paths go through. Import had none at all,
        # which made it the easiest way in the whole app to put one pack's
        # quests into another pack's book.
        if not self._guard_import(dest) or not self._guard_mc():
            return
        picks = [self._import_rows[i] for i in sel]
        group = self.import_group.get().strip() or "Imported"

        def work():
            try:
                total = 0
                for out in outs:
                    total = install_imported_chapters(
                        Path(out), picks, self.scan_result, self.log, group,
                        remix=self.import_remix.get(),
                        merge_groups=self.import_merge.get())
                self.root.after(0, lambda: messagebox.showinfo(
                    APP_NAME, "Imported %d chapter(s) into your book.\n"
                    "Open the game (or reload) to see them under '%s'."
                    % (total, group)))
            except Exception as e:
                self.log("import failed: %s" % e)
                self.root.after(0, lambda: messagebox.showerror(APP_NAME, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _tab_prompt(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text="Prompt")
        pad = ttk.Frame(t)
        pad.pack(fill="both", expand=True, padx=14, pady=12)
        bar = ttk.Frame(pad)
        bar.pack(fill="x")
        ttk.Button(bar, text="Build / refresh prompt", style="Accent.TButton",
                   command=self.on_build_prompt).pack(side="left")
        ttk.Button(bar, text="Copy", command=self._copy_prompt).pack(side="left", padx=6)
        ttk.Button(bar, text="Clear", command=lambda: self.prompt_box.delete("1.0", "end")).pack(side="left")
        self.prompt_info = ttk.Label(bar, text="", style="Muted.TLabel")
        self.prompt_info.pack(side="right")
        ttk.Checkbutton(pad, variable=self.opt_raw_prompt, style="Card.TCheckbutton",
                        text="Send this prompt as-is in ONE call "
                             "(skips batched chapter-by-chapter generation)").pack(
            anchor="w", pady=(8, 0))
        ttk.Label(pad, style="Muted.TLabel", wraplength=760, justify="left",
                  text="Leave this unticked and Generate with AI plans the chapters first, "
                       "then writes one chapter per call with an offline fallback — far more "
                       "robust when the provider is busy. This box is only sent when the "
                       "option above is ticked.").pack(anchor="w", pady=(2, 4))
        self.prompt_box = scrolledtext.ScrolledText(pad, wrap="word")
        style_text(self.prompt_box)
        self.prompt_box.pack(fill="both", expand=True)
        self.prompt_box.bind("<KeyRelease>", lambda e: self._prompt_stats())

    def _tab_log(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text="Log")
        pad = ttk.Frame(t)
        pad.pack(fill="both", expand=True, padx=14, pady=12)
        bar = ttk.Frame(pad)
        bar.pack(fill="x")
        ttk.Button(bar, text="Clear", command=lambda: self._logclear()).pack(side="left")
        ttk.Button(bar, text="Copy all", command=self._logcopy).pack(side="left", padx=6)
        self.logbox = scrolledtext.ScrolledText(pad, wrap="word", state="disabled")
        style_text(self.logbox)
        self.logbox.pack(fill="both", expand=True, pady=(8, 0))

    # ---- log / status -------------------------------------------------- #
    def log(self, msg):
        self.events.put(("log", msg))
        # also append to a session log on disk, so a run can be inspected (or
        # watched) from outside the app — the Log tab dies with the window.
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as fh:
                fh.write("%s  %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:
            pass

    def set_status(self, msg):
        self.events.put(("status", msg))

    def _logclear(self):
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

    def _logcopy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.logbox.get("1.0", "end"))

    def _pump(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end", payload + "\n")
                    self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                elif kind == "status":
                    if isinstance(payload, str) and payload.startswith("__mc__"):
                        self._apply_mc(payload.endswith("1"))
                    else:
                        self.status.configure(text=payload)
                elif kind == "scan_done":
                    self.scan_result = payload
                    valid = {m["mod_id"] for m in payload["mods"]}
                    self.selected_ids = [i for i in self.selected_ids if i in valid] or list(valid)
                    self._mod_imgs.clear()
                    self._update_scan_lbl()
                    self._render_mod_cards()
                    # Say it in a dialog, not only in the log. The case this
                    # exists for is a folder of 80 jars where 76 are for
                    # Minecraft 1.12: the scan "succeeds", builds a book out
                    # of the one readable mod, and looks finished. Nobody
                    # reads the log to find out why their pack is missing.
                    _note = str(payload.get("blocked") or "")
                    if _note:
                        messagebox.showwarning(
                            APP_NAME,
                            "This pack did not read cleanly." + chr(10) * 2
                            + _note + chr(10) * 2
                            + "You can still build a book from what was "
                              "read - it will just cover less than you "
                              "expect.")
                elif kind == "mods_refresh":
                    self._mod_imgs.clear()
                    self._render_mod_cards()
                elif kind == "models":
                    self.model_cb["values"] = payload
                    if payload and self.model.get() not in payload:
                        self.model.set(payload[0])
                    self.set_status("fetched %d models" % len(payload))
                elif kind == "repair_lbl":
                    self.repair_lbl.configure(text=payload)
                elif kind == "editor_doc":
                    self._editor_populate_tree(payload)
                elif kind == "editor_pools":
                    self._pools_box.delete("1.0", "end")
                    self._pools_box.insert("1.0", "\n".join(
                        json.dumps(p, ensure_ascii=False) for p in payload))
                elif kind == "phase":
                    frac, label = payload
                    self.loading.set_step(frac, label)
                    if label:
                        self.status.configure(text=label)
                elif kind == "creep":
                    target, label = payload
                    self.loading.creep(target, label)
                    if label:
                        self.status.configure(text=label)
                elif kind == "done":
                    self._set_busy(False)
                    self._sfx("done")
                    if payload:
                        self.set_status(payload)
                    goto = getattr(self, "_goto_after", None)
                    if goto:
                        self.nb.select(self._tab_index(goto))
                    self._goto_after = None
                    if getattr(self, "_notify_after", False):
                        self._done_popup(payload or "Done.",
                                         open_out=getattr(self, "_notify_out", True))
                    self._notify_after = False
                elif kind == "cancelled":
                    # Not a success ("Finished" popup would be a lie — nothing
                    # was written) and not an error (the user asked for it):
                    # stand down quietly. _set_busy also resets self._cancel.
                    self._set_busy(False)
                    self.set_status(payload or "cancelled")
                    self._goto_after = None
                    self._notify_after = False
                elif kind == "error":
                    self._set_busy(False)
                    self._sfx("error")
                    self.set_status("error - see the Log tab")
                    self.nb.select(self._tab_index("Log"))
                    messagebox.showerror(APP_NAME, payload)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _poll_mc(self):
        def check():
            r = minecraft_running()
            self.events.put(("status", "__mc__%d" % (1 if r else 0)))
        threading.Thread(target=check, daemon=True).start()
        self.root.after(5000, self._poll_mc)

    # override status handling for the mc sentinel
    def _apply_mc(self, running):
        self._mc_running = running
        self.header.set_badge("\u25cf  Minecraft is running" if running else "")

    def _set_busy(self, busy, title=""):
        self._busy = busy
        self._cancel = False
        self.btn_gen.configure(state="disabled" if busy else "normal")
        if hasattr(self, "btn_gen_local"):
            self.btn_gen_local.configure(state="disabled" if busy else "normal")
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        if busy:
            self.prog.set(None)
            self.prog.start()
            self.loading.show(title or "Working...")
        else:
            self.prog.stop()
            self.loading.hide()

    def _sfx(self, name):
        play_sfx(name, self.opt_sound.get())

    def _done_popup(self, msg, open_out=True):
        p = PALETTE
        top = tk.Toplevel(self.root)
        top.title("Done")
        top.configure(bg=p["bg"])
        top.resizable(False, False)
        top.transient(self.root)
        HeaderBanner(top, height=58).pack(fill="x")
        tk.Label(top, text="✓  Finished", bg=p["bg"], fg=p["ok"],
                 font=("Segoe UI Semibold", 14)).pack(padx=26, pady=(16, 4))
        tk.Label(top, text=msg, bg=p["bg"], fg=p["text"], wraplength=380,
                 justify="center", font=("Segoe UI", 10)).pack(padx=26, pady=(0, 6))
        if self._mc_running:
            tk.Label(top, text="Minecraft is running — close & relaunch it to load the changes.",
                     bg=p["bg"], fg=p["warn"], wraplength=380, justify="center",
                     font=("Segoe UI", 9)).pack(padx=26, pady=(0, 4))
        btns = ttk.Frame(top)
        btns.pack(pady=(8, 18))
        if open_out:
            ttk.Button(btns, text="Open output folder",
                       command=self.on_open_out).pack(side="left", padx=5)
        ttk.Button(btns, text="OK", style="Accent.TButton", command=top.destroy).pack(side="left", padx=5)
        top.update_idletasks()
        w, h = top.winfo_width(), top.winfo_height()
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        top.geometry("+%d+%d" % (rx + (rw - w) // 2, ry + (rh - h) // 3))
        top.lift()

    def phase(self, frac, label=""):
        self.events.put(("phase", (frac, label)))

    def phase_creep(self, target, label=""):
        """Tell the loading bar to ease forward toward `target` while we wait on
        something of unknown length (the AI call)."""
        self.events.put(("creep", (target, label)))

    def _do_cancel(self):
        self._cancel = True
        self.log("cancel requested...")
        try:
            self.loading.step_lbl.configure(
                text="cancelling — finishing the current request first...")
            self.loading.cancel_btn.configure(state="disabled")
        except Exception:
            pass

    def _run(self, fn, title="Working...", goto=None, notify=False, notify_out=True):
        if self._busy:
            return
        self._save_cfg()
        self._goto_after = goto
        self._notify_after = notify
        self._notify_out = notify_out
        self._set_busy(True, title)
        self._sfx("start")
        self.log("--- START: %s ---" % title)
        t0 = time.time()

        def wrap():
            try:
                res = fn()
                self.log("--- DONE (%.0fs): %s ---" % (time.time() - t0, res))
                self.events.put(("done", res))
            except Exception as e:
                # "cancelled..." raises (call_ai, write_snbt's declined shrink,
                # _write's pre-write guard) are the user saying stop, not a
                # failure: no error popup, no traceback — a quiet status line.
                if str(e).lower().startswith("cancelled"):
                    self.log("--- CANCELLED (%.0fs): %s ---" % (time.time() - t0, e))
                    self.events.put(("cancelled", str(e)))
                    return
                self.log("--- FAILED (%.0fs): %s ---" % (time.time() - t0, e))
                self.log(traceback.format_exc())
                self.events.put(("error", str(e)))
            finally:
                # Release the run seed only once the build is over: the next
                # Build then genuinely produces a different book, which is
                # what "generate" is supposed to mean. Cleared on failure and
                # on cancel too - a retry the user expects to differ should.
                self._active_seed = None
        threading.Thread(target=wrap, daemon=True).start()

    def _preset(self, force=False):
        p = PROVIDER_PRESETS.get(self.provider.get(), {})
        if p.get("url"):
            self.api_url.set(p["url"])
        elif force:
            self.api_url.set("")
        if p.get("model") and (force or not self.model.get()):
            self.model.set(p["model"])

    def _toggle_key(self):
        self.key_entry.configure(show="" if self.key_entry.cget("show") else "\u2022")

    # ---- browse ------------------------------------------------------- #
    def _on_pick_instance(self, _e=None):
        lbl = self._inst_var.get()
        for name, mods in self._instances:
            if name == lbl:
                self.mods_dir.set(mods)
                self.out_dir1.set(str(Path(mods).parent / "config/ftbquests/quests"))
                self.set_status("instance picked - now press Scan mods")
                return

    def _b_mods(self):
        d = filedialog.askdirectory(title="Modpack folder (or its mods/ folder)")
        if d:
            p = Path(d)
            self.mods_dir.set(str(p / "mods" if (p / "mods").is_dir() else p))

    def _b_out(self, var):
        d = filedialog.askdirectory(title="config/ftbquests/quests")
        if d:
            var.set(d)

    def _out_dirs(self):
        return [Path(v.strip()) for v in (self.out_dir1.get(), self.out_dir2.get()) if v.strip()]

    def _update_scan_lbl(self):
        if not self.scan_result:
            self.scan_lbl.configure(text="not scanned")
            return
        n = len(self.scan_result["mods"])
        it = sum(len(v) for v in self.scan_result["items"].values())
        txt = "%d mods (%d selected) - %d item IDs" % (n, len(self.selected_ids), it)
        # The warning dialog is dismissed once; this line stays. A user who
        # comes back to the tab later should still be able to see that the
        # book they are about to build covers part of their pack.
        if self.scan_result.get("blocked"):
            txt += "   -   PART OF THIS PACK COULD NOT BE READ"
        self.scan_lbl.configure(text=txt)

    # ---- opts -------------------------------------------------------- #
    _REWARD_MULT = {"lean": 0.5, "standard": 1.0, "generous": 2.0}

    def _creativity(self):
        return min(1.0, max(0.0, self.temperature.get() / 1.0))

    def _update_count_hint(self):
        if not hasattr(self, "_count_hint"):
            return
        exact = self.target_count.get().strip()
        if exact.isdigit():
            self._count_hint.configure(text="→ exactly %s quests" % exact)
        else:
            self._count_hint.configure(
                text="≈ %s quests total" % DENSITY_TARGETS.get(self.density.get(), "120-180"))

    def _refresh_preview(self):
        if not hasattr(self, "preview"):
            return
        if getattr(self, "_prev_job", None):
            self.root.after_cancel(self._prev_job)
        self._prev_job = self.root.after(120, lambda: self.preview.render(self._opts()))

    def _seed_for_run(self):
        """A fresh seed per build, or the one the user pinned. -> int

        Created lazily and held for the whole build, because a build asks for
        its options more than once (_prompt_opts for the doc, _opts for the
        chapters) and those calls must agree - a seed that changed between
        them would name the chapters from one book and lay out another. It is
        logged every run so a book the user liked can be reproduced by typing
        the number back into Setup.
        """
        pinned = ""
        try:
            pinned = str(self.run_seed.get()).strip()
        except Exception:
            pass
        if pinned and pinned.lower() not in ("auto", "random", ""):
            return pinned
        if getattr(self, "_active_seed", None) is None:
            self._active_seed = random.randrange(1, 2 ** 31)
            self.log("  run seed %d  (type this into Setup > Seed to get this "
                     "exact book again)" % self._active_seed)
        return self._active_seed

    def _opts(self, preserve_ids=False):
        # `aesthetic` and `_scan` belong here and not only in _prompt_opts:
        # build_chapters reads both, and without them the dropdown moved nothing
        # on the offline path and chapter art had no pack to source from.
        return {"auto_chain": self.opt_chain.get(), "xp_rewards": self.opt_xp.get(),
                "seed": self._seed_for_run(),
                "decor_art": self.opt_decor.get(),
                "aesthetic": self.aesthetic.get(),
                "quest_shape": self.quest_shape.get(),
                "_scan": getattr(self, "scan_result", None),
                "groups": self.opt_groups.get(),
                "style_chapters": self.opt_style_chapters.get(),
                "layout": self.layout.get(), "group_style": self.group_style.get(), "creativity": self._creativity(),
                "preserve_ids": preserve_ids,
                "reward_mult": self._REWARD_MULT.get(self.reward_level.get(), 1.0)}

    def _themes_raw(self):
        try:
            return self.themes_box.get("1.0", "end").strip()
        except Exception:
            return getattr(self, "themes_text", "") or ""

    def _themes(self):
        return [t.strip() for t in self._themes_raw().splitlines() if t.strip()]

    def _prompt_opts(self):
        return {"density": self.density.get(), "target": self.target_count.get(),
                "seed": self._seed_for_run(),
                "groups": self.opt_groups.get(), "aesthetic": self.aesthetic.get(),
                "reward": self.reward_level.get(), "progression": self.progression.get(),
                "layout": self.layout.get(), "group_style": self.group_style.get(), "creativity": self._creativity(),
                "themes": self._themes(), "themes_only": self.opt_themes_only.get(),
                "vanilla_chapters": self.opt_vanilla_ch.get(),
                "include_decor": self.opt_decor_mods.get(),
                "mod_desc": {k: v.get("desc") for k, v in self._mod_info.items() if v.get("desc")}}

    def _book(self):
        return {"title": self.book_title.get().strip(),
                "icon": self.book_icon.get().strip(),
                "progression_mode": "flexible" if self.progression.get() == "open" else "linear"}

    def _prompt_text(self):
        return self.prompt_box.get("1.0", "end").strip() or build_prompt(
            self.scan_result, self.selected_ids, self.language.get(), self._prompt_opts())

    @staticmethod
    def _instance_of(path) -> str:
        """The instance folder that owns this path, lowercased. -> str

        A mods folder is <instance>/mods; a book is <instance>/config/
        ftbquests/quests. Searching from the END matters: a path can carry
        the word "mods" high up (a "Downloaded Mods" folder), and it is the
        LAST marker that says which instance we are actually inside.
        """
        try:
            parts = list(Path(str(path)).resolve().parts)
        except Exception:
            parts = list(Path(str(path)).parts)
        low = [x.lower() for x in parts]
        for i in range(len(low) - 1, -1, -1):
            if low[i] in ("mods", "config"):
                return str(Path(*parts[:i])).lower() if i else ""
        return ""

    def _guard_pack(self) -> bool:
        """Refuse to write one pack's book into a DIFFERENT pack's folder.

        This is the bug that produced a book full of purple "Missing Item"
        icons and magenta chapter art in a real game: the book was built from
        a 35-jar pack's scan and written into a 128-jar pack's config, so
        every item from a mod the running pack did not have rendered as a
        hole. Nothing in the app noticed, because each half was individually
        valid - the scan was of a real folder and the destination was a real
        book folder. Only the PAIR is wrong, so only a check that sees both
        can catch it, and there wasn't one.

        Silent on the ordinary cases: same pack, or either path sitting
        outside any recognisable instance layout (a scratch folder, a
        server), where there is no second pack to be confused with.
        """
        src = self._instance_of(self.mods_dir.get().strip())
        if not src:
            return True
        for out in self._out_dirs():
            dst = self._instance_of(out)
            if not dst or dst == src:
                continue
            nl = chr(10)
            msg = nl.join([
                "These are two different modpacks.", "",
                "Scanned mods:", "    " + (Path(src).name or src), "",
                "Writing the book into:", "    " + (Path(dst).name or dst), "",
                "The book would ask for items from the first pack while "
                "the second pack is the one you play. Every item the "
                "destination does not have shows up in game as a purple "
                "“Missing Item”, and chapter art from those "
                "mods renders as magenta squares.", "",
                "Write it anyway?"])
            return messagebox.askyesno(APP_NAME, msg, icon="warning",
                                       default="no")
        return True

    def _guard_mc(self) -> bool:
        """The single pre-write gate - all seven write paths funnel here.

        Pack-match runs FIRST: writing the wrong pack's book ruins the book
        whether or not the game is open, and there is no point asking about
        autosave for a write the user is about to cancel anyway.
        """
        if not self._guard_pack():
            return False
        if self.opt_mcwarn.get() and self._mc_running:
            return messagebox.askyesno(
                APP_NAME,
                "Minecraft appears to be running.\n\nFTB Quests keeps the quest book in "
                "memory and will overwrite anything you generate the moment it autosaves.\n\n"
                "Close Minecraft first for the change to stick.\n\nWrite anyway?",
                icon="warning", default="no")
        return True

    # ---- actions --------------------------------------------------- #
    def on_scan(self):
        folder = Path(self.mods_dir.get().strip())
        if not folder.is_dir():
            messagebox.showwarning(APP_NAME, "Pick a valid mods folder first.")
            return
        def job():
            self.phase(0.05, "reading jars...")
            scan = scan_mods(folder, 400, self.log, self.phase)
            self.phase(1.0, "done")
            self.events.put(("scan_done", scan))
            self._sfx("scan")
            return "scanned %d mods, %d item IDs" % (
                len(scan["mods"]), sum(len(v) for v in scan["items"].values()))
        self._run(job, "Scanning mods", goto="Mods")

    def _need_scan(self):
        if not self.scan_result:
            messagebox.showwarning(APP_NAME, "Scan the mods first (Setup tab).")
            return True
        return False

    def on_select_mods(self):
        if self._need_scan():
            return
        dlg = ModSelectDialog(self.root, self.scan_result["mods"], self.selected_ids)
        self.root.wait_window(dlg)
        if dlg.result is not None:
            self.selected_ids = dlg.result
            self._update_scan_lbl()
            self._save_cfg()

    def on_fetch_models(self):
        url, key = self.api_url.get().strip(), self.api_key.get().strip()
        if not url:
            messagebox.showwarning(APP_NAME, "Set the API URL first.")
            return
        self.set_status("fetching models...")

        def job():
            self.phase(0.3, "contacting provider...")
            models = fetch_models(url, key)
            self.phase(1.0, "done")
            self.events.put(("models", models))
            return "%d models" % len(models)
        self._run(job, "Fetching model list")

    def on_test(self):
        url, key = self.api_url.get().strip(), self.api_key.get().strip()
        if not url:
            messagebox.showwarning(APP_NAME, "Set the API URL first.")
            return

        def job():
            self.phase(0.35, "contacting provider...")
            models = fetch_models(url, key)
            self.phase(1.0, "done")
            self.events.put(("models", models))
            self.log("connection OK - %d models visible" % len(models))
            mm = self.model.get().strip()
            note = ""
            if mm and models and mm not in models:
                note = "\n\nHeads up: your model \"%s\" isn't in the list." % mm
            return "Connection OK — %d models visible.%s" % (len(models), note)
        self._run(job, "Testing connection", notify=True, notify_out=False)

    def on_build_prompt(self):
        if self._need_scan():
            return
        text = build_prompt(self.scan_result, self.selected_ids, self.language.get(),
                            self._prompt_opts())
        self.prompt_box.delete("1.0", "end")
        self.prompt_box.insert("1.0", text)
        self._prompt_stats()
        self.nb.select(self._tab_index("Prompt"))

    def _prompt_stats(self):
        n = len(self.prompt_box.get("1.0", "end")) - 1
        self.prompt_info.configure(text="%d chars  ~%d tokens" % (n, n // 4))

    def _copy_prompt(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.prompt_box.get("1.0", "end"))
        self.set_status("prompt copied")

    def _tab_index(self, name):
        for i in range(self.nb.index("end")):
            if self.nb.tab(i, "text").strip() == name:
                return i
        return 0

    def on_save_prompt(self):
        if self._need_scan():
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="quest_prompt.txt")
        if not path:
            return
        Path(path).write_text(self._prompt_text(), encoding="utf-8")
        self.set_status("prompt saved -> %s" % Path(path).name)

    def _ai_ready(self):
        url, model = self.api_url.get().strip(), self.model.get().strip()
        if not url or not model:
            messagebox.showwarning(APP_NAME, "Set API URL and model (Setup tab).")
            return None
        return url, model, self.api_key.get().strip()

    _FUN_TITLES = [
        "The Wandering Codex", "Moonlit Ledger", "Almanac of the Overworld",
        "A Traveller's Field Notes", "The Compendium of Small Miracles",
        "Chronicle of the Chunk", "The Crafter's Grimoire", "Notes from the Night Shift",
        "The Big Book of Doing Things", "Adventures, Assorted", "The Emerald Itinerary",
        "Tales the Villagers Won't Tell", "The Speedrunner's Scripture",
    ]

    def on_randomize(self):
        rng = random.Random()
        self.density.set(rng.choice(DENSITY_ORDER[:5]))  # skip 'colossal' by default
        self.target_count.set("")
        self.layout.set(rng.choice([l for l in LAYOUTS if l != "ai"]))
        self.aesthetic.set(rng.choice(["minimal", "balanced", "decorated", "lavish"]))
        self.reward_level.set(rng.choice(["lean", "standard", "generous"]))
        self.progression.set(rng.choice(["linear", "loose", "open"]))
        self.temperature.set(round(rng.uniform(0.35, 1.1), 2))
        self._temp_lbl.configure(text="%d%%" % min(100, int(self.temperature.get() * 100)))
        self.opt_style_chapters.set(rng.random() < 0.85)
        self.opt_decor.set(rng.random() < 0.7)
        self.opt_groups.set(True)
        self.opt_chain.set(rng.random() < 0.5)
        self.opt_xp.set(rng.random() < 0.6)
        if rng.random() < 0.7:
            self.book_title.set(rng.choice(self._FUN_TITLES))
        if self.scan_result:
            mods = self.scan_result["mods"]
            core = [m["mod_id"] for m in mods if m["category"] in ("tech", "magic", "world", "mob")]
            rest = [m["mod_id"] for m in mods if m["mod_id"] not in core]
            keep_core = rng.sample(core, k=max(1, int(len(core) * rng.uniform(0.5, 1.0)))) if core else []
            keep_rest = rng.sample(rest, k=min(len(rest), rng.randint(2, 8))) if rest else []
            self.selected_ids = keep_core + keep_rest
            self._render_mod_cards()
            self._update_scan_lbl()
        self._save_cfg()
        self._sfx("done")
        self.set_status("\U0001f319 randomized  —  %s book, %s layout, %s style, creativity %d%%"
                        % (self.density.get(), self.layout.get(), self.aesthetic.get(),
                           int(self.temperature.get() * 100)))
        self.log("randomized: size=%s layout=%s aesthetic=%s rewards=%s progression=%s "
                 "creativity=%d%% mods=%d" % (
                     self.density.get(), self.layout.get(), self.aesthetic.get(),
                     self.reward_level.get(), self.progression.get(),
                     int(self.temperature.get() * 100), len(self.selected_ids)))

    def on_generate(self):
        if self._need_scan():
            return
        outs = self._out_dirs()
        if not outs:
            messagebox.showwarning(APP_NAME, "Set at least one output folder.")
            return
        ai = self._ai_ready()
        if not ai or not self._guard_mc():
            return
        url, model, key = ai
        mt = self._int(self.max_tokens.get(), 32768)
        temp = round(self.temperature.get(), 2)
        scan = self.scan_result
        sel = list(self.selected_ids)
        lang = self.language.get()
        popts = self._prompt_opts()
        popts["book_title"] = self.book_title.get().strip()
        # Only send the Prompt tab verbatim when the user explicitly asks for it.
        # Text left over from clicking "Build prompt" used to silently bypass
        # batching and turn one 503 into a dead run.
        edited = (self.prompt_box.get("1.0", "end").strip()
                  if self.opt_raw_prompt.get() else "")

        def job():
            # --- one-shot path: the user ticked "send my prompt as-is" --------
            if edited:
                self.log("sending the Prompt tab verbatim (single call, no batching)")
                self.phase_creep(0.58, "waiting for %s ..." % model)
                try:
                    raw = call_ai(url, key, model, edited, mt, self.log,
                                  lambda: self._cancel, temp, read_timeout=600)
                    if len(raw.strip()) < 40:
                        raise RuntimeError("the model returned nothing usable.")
                    return self._write(raw, scan, outs, save_raw=True, base=0.62)
                except Exception as e:
                    # don't lose the run - drop through to the batched path
                    self.log("  ! single-call prompt failed (%s)" % str(e)[:120])
                    self.log("  -> falling back to batched chapter-by-chapter generation")

            # --- 1. chapter plan (small call) ---------------------------------
            self.phase(0.04, "asking %s for a chapter plan..." % model)
            pp = build_plan_prompt(scan, sel, lang, popts)
            self.log("plan prompt: %d chars" % len(pp))
            plan = {}
            try:
                plan = extract_json(call_ai(url, key, model, pp, min(mt, 6000), self.log,
                                            lambda: self._cancel, temp,
                                            read_timeout=180, max_rounds=2))
            except Exception as e:
                self.log("  ! plan call failed (%s) — planning offline instead" % e)
            specs = [c for c in (plan.get("chapters") or []) if isinstance(c, dict)]
            if not specs:
                self.log("  no usable plan — falling back to the offline outline")
                off = local_quest_doc(scan, sel, popts)
                specs = [{"id": c["id"], "group": c.get("group", ""), "title": c["title"],
                          "icon": c.get("icon"), "focus": "", "quests": len(c["quests"])}
                         for c in off["chapters"]]
                self._offline_doc = off
            else:
                self._offline_doc = None
            self.log("plan: %d chapters" % len(specs))

            # --- 2. one call per chapter, offline fallback per chapter --------
            chapters, prev_last, ai_ok, fell_back = [], None, 0, 0
            prev_spec = None
            for i, spec in enumerate(specs):
                if self._cancel:
                    break
                frac = 0.10 + 0.62 * (i / max(1, len(specs)))
                title = str(spec.get("title") or "Chapter %d" % (i + 1))
                self.phase(frac, "chapter %d/%d — %s" % (i + 1, len(specs), title[:40]))
                cid = str(spec.get("id") or "ch%d" % (i + 1))
                quests = []
                try:
                    cp = build_chapter_prompt(spec, scan, popts, lang, prev_last)
                    self.log("  ch %d/%d '%s': prompt %d chars"
                             % (i + 1, len(specs), title[:30], len(cp)))
                    got = extract_json(call_ai(url, key, model, cp, min(mt, 12000),
                                               self.log, lambda: self._cancel, temp,
                                               read_timeout=180, max_rounds=2))
                    quests = [q for q in (got.get("quests") or []) if isinstance(q, dict)]
                except Exception as e:
                    self.log("  ! chapter %d failed (%s)" % (i + 1, str(e)[:120]))
                # Enforce the known play order no matter what the model did:
                # stable-sort its quests by each quest's first target's index
                # in mod_play_order. Quests with unknown targets keep their
                # relative slots. Prompting alone was not enough - the wand
                # still drifted late whenever the model free-styled.
                focus_mid = _resolve_focus(str(spec.get("focus") or ""), scan)
                if quests and focus_mid and focus_mid not in ("", "vanilla"):
                    order_ix = {k: ix for ix, k in
                                enumerate(mod_play_order(focus_mid, scan, 200))}

                    def _qkey(pair):
                        pos, q2 = pair
                        for t2 in (q2.get("tasks") or []):
                            it2 = t2.get("item")
                            if isinstance(it2, str) and it2 in order_ix:
                                return (order_ix[it2], pos)
                            if t2.get("type") == "kill" \
                                    and ("kill:" + str(t2.get("entity"))) in order_ix:
                                return (order_ix["kill:" + str(t2.get("entity"))], pos)
                            if t2.get("type") == "dimension" \
                                    and ("dim:" + str(t2.get("dimension"))) in order_ix:
                                return (order_ix["dim:" + str(t2.get("dimension"))], pos)
                        return (pos, pos)      # unknown target: hold position
                    quests = [q2 for _k, q2 in
                              sorted(((_qkey((i2, q2)), q2)
                                      for i2, q2 in enumerate(quests)),
                                     key=lambda z: z[0])]
                # A chapter about a mod that comes back as nothing but vanilla
                # means the model was handed the wrong item list. Treat it as a
                # failure, not a result - shipping it silently once made every
                # AI chapter in the book 100% minecraft:.
                # Promote any prerequisite the model only EXPLAINED. A
                # description that walks through building a multiblock is a
                # quest that should exist - insert it just before the step
                # that needs it.
                _fm = _resolve_focus(str(spec.get("focus") or ""), scan)
                if quests and _fm and _fm not in ("", "vanilla"):
                    for _pid in missing_prereqs(quests, _fm, scan):
                        _disp = (scan.get("names") or {}).get(_pid) or _pretty_name(_pid)
                        _at = 0
                        for _qi, _q in enumerate(quests):
                            _body = " ".join(str(x) for x in (_q.get("description") or []))
                            if _disp.lower() in _body.lower():
                                _at = _qi
                                break
                        quests.insert(_at, {
                            "title": _disp,
                            "description": ["%sBuild the %s%s%s before going further "
                                            "- the next step needs it."
                                            % (DESC_BODY, DESC_KEY, _disp, DESC_BODY)],
                            "tasks": [{"type": "item", "item": _pid, "count": 1}],
                        })
                        self.log("  + inserted missing prerequisite '%s'" % _disp)
                if quests and _vanilla_flooded(quests, _resolve_focus(
                        str(spec.get("focus") or ""), scan)):
                    self.log("  ! chapter %d ('%s') came back all-vanilla — rebuilding"
                             % (i + 1, title[:30]))
                    quests = []
                if quests:
                    ai_ok += 1
                else:
                    quests = self._offline_chapter(scan, sel, popts, spec, i)
                    if quests:
                        fell_back += 1
                        self.log("  -> built chapter %d offline instead (%d quests)"
                                 % (i + 1, len(quests)))
                if not quests:
                    continue
                for qi, q in enumerate(quests):        # ids must be unique book-wide
                    q["id"] = "%s_q%d" % (cid, qi)
                gate = prev_last if _same_line(prev_spec, spec) else None
                # Same tree shaping the offline builder uses: real packs run
                # ~6 tiers deep and fan out, they are not single files.
                # wide-and-shallow, per the measured norms (see _design)
                _qids = [q["id"] for q in quests]
                _ad, _al = shape_as_trunk(_qids, gate)
                for q in quests:
                    q["dependencies"] = list(_ad.get(q["id"], []))
                    # optional is a per-chapter decision, not a leaf property -
                    # see the CHK-16/17 note in build_chapters. The AI path used
                    # the same leaf map and so had the same defect.
                prev_last = quests[-1]["id"]
                prev_spec = spec
                # The AI path had no chapter intro either, and unlike the
                # offline builder it knows the modid outright - the plan says
                # which mod the chapter is about. A model's own intro would be
                # invented; this one is the mod author's sentence, or a
                # guide's account of how the mod is entered.
                _focus = str(spec.get("focus") or "").strip().lower()
                _intro = (chapter_intro(_focus, scan)
                          if _focus and _focus != "vanilla" else "")
                chapters.append({"id": cid, "group": spec.get("group", ""),
                                 "title": title, "icon": spec.get("icon"),
                                 **({"description": [_intro]} if _intro else {}),
                                 "quests": quests})
            if not chapters:
                raise RuntimeError("no chapters were produced — try again, or use "
                                   "'Build offline (no AI)'.")
            doc = {"title": plan.get("title") or popts.get("book_title") or "Quest Book",
                   "chapters": chapters}
            nq = sum(len(c["quests"]) for c in chapters)
            self.log("assembled %d chapters / %d quests  (%d from AI, %d offline)"
                     % (len(chapters), nq, ai_ok, fell_back))
            self.phase(0.74, "assembled %d chapters / %d quests" % (len(chapters), nq))
            res = self._write(json.dumps(doc, ensure_ascii=False), scan, outs,
                              save_raw=True, base=0.76)
            if fell_back:
                res += "  (%d chapter(s) built offline after AI failures)" % fell_back
            return res
        self._run(job, "Generating quest book", notify=True)

    def _offline_chapter(self, scan, sel, popts, spec, idx):
        """Deterministic fallback for a chapter the model couldn't deliver.

        Matches on the chapter's FOCUS MOD. Falling back to an arbitrary index
        once put Twilight Forest's quests inside the Botania chapter.
        """
        try:
            focus = _resolve_focus(str(spec.get("focus") or ""), scan)
            if focus and focus not in ("vanilla", ""):
                rows = _mod_quest_rows(
                    {"mod_id": focus,
                     "name": next((m["name"] for m in scan["mods"]
                                   if m["mod_id"] == focus), focus),
                     "category": next((m["category"] for m in scan["mods"]
                                       if m["mod_id"] == focus), "unknown")},
                    scan, max(6, _int_of(spec.get("quests", 12), 12)),
                    random.Random("fallback/" + focus),
                    float(popts.get("creativity", 0.3)))
                if rows:
                    return [{"id": "fb%d" % i, "title": t,
                             "tasks": [task],
                             **({"description": [d]} if d else {})}
                            for i, (t, task, d) in enumerate(rows)]
            if self._offline_doc is None:
                self._offline_doc = local_quest_doc(scan, sel, popts)
            want = str(spec.get("title", "")).lower()
            for c in self._offline_doc["chapters"]:
                if str(c["title"]).lower() == want:
                    return json.loads(json.dumps(c["quests"]))
            # no sensible match - better an empty chapter than someone else's
            self.log("  ! no offline source for '%s' — leaving it to the AI"
                     % spec.get("title"))
        except Exception as e:
            self.log("  ! offline fallback failed: %s" % e)
        return []

    def on_generate_local(self):
        if self._need_scan():
            return
        outs = self._out_dirs()
        if not outs:
            messagebox.showwarning(APP_NAME, "Set at least one output folder.")
            return
        if not self._guard_mc():
            return
        scan = self.scan_result
        opts = self._prompt_opts()
        opts["book_title"] = self.book_title.get().strip()
        sel = list(self.selected_ids)
        # The offline builder writes English templates only; say so instead of
        # silently ignoring the dropdown (which is enabled when a key is set,
        # because the AI path DOES honour it). Read the var here, not in job() -
        # tk vars are not thread-safe.
        lang = self.language.get()

        def job():
            self.phase(0.15, "building chapters from the mod scan...")
            if lang and lang != "English":
                self.log("note: the offline builder writes English only - the "
                         "Language setting applies to AI generation")
            doc = local_quest_doc(scan, sel, opts)
            self.phase(0.35, "offline build: " + summarize(doc))
            self.log("offline build: " + summarize(doc))
            # excluded_mods was put on the doc for exactly this moment and then
            # never read anywhere (grep: two occurrences, both the assignment) -
            # so picking a mod and getting no chapter was STILL silence, the
            # failure the field was built to end. Log every reason; raise a
            # dialog only for a deliberate small selection (the builder's own
            # len<=8 deliberateness test), so "all mods ticked" doesn't nag
            # about every library in the pack.
            dropped = doc.get("excluded_mods") or {}
            for dmid, dwhy in sorted(dropped.items()):
                self.log("  ! excluded %s: %s" % (dmid, dwhy))
            if dropped and len(sel) <= 8:
                msg = "\n".join("%s — %s" % kv for kv in sorted(dropped.items()))
                self.root.after(0, lambda m=msg: messagebox.showwarning(
                    APP_NAME, "Some picked mods produced no chapter:\n\n" + m))
            return self._write(json.dumps(doc, ensure_ascii=False), scan, outs,
                               save_raw=True, base=0.4)
        self._run(job, "Building quest book (offline, no AI)", notify=True)

    def on_preview(self):
        raw = self.paste.get("1.0", "end").strip()
        if not raw:
            path = filedialog.askopenfilename(title="Quest JSON to preview",
                                              filetypes=[("JSON / text", "*.json *.txt"), ("All", "*.*")])
            if not path:
                return
            raw = Path(path).read_text(encoding="utf-8")
        scan = self.scan_result or {"mods": [], "items": {}, "entities": {}, "craftable": {}}

        def job():
            self.phase(0.3, "parsing JSON...")
            doc = extract_json(raw)
            self.log("PREVIEW: " + summarize(doc))
            self.phase(0.6, "cleaning...")
            doc = clean_doc(doc, scan, self.log)
            self.phase(0.85, "building chapters...")
            warns = []
            chapters, groups, _extra = build_chapters(doc, warns.append, self._opts())
            self.log("would write %d chapter files%s"
                     % (len(chapters), (", %d groups" % len(groups)) if groups else ""))
            for w in warns[:40]:
                self.log("  ! " + w)
            self.phase(1.0, "done")
            return "preview done - %d chapters, %d warnings" % (len(chapters), len(warns))
        self._run(job, "Previewing")

    def on_convert_paste(self):
        raw = self.paste.get("1.0", "end").strip()
        if not raw:
            messagebox.showwarning(APP_NAME, "Paste some JSON first.")
            return
        self._convert(raw, save_raw=True)

    def on_convert_file(self):
        path = filedialog.askopenfilename(title="Quest JSON",
                                          filetypes=[("JSON / text", "*.json *.txt"), ("All", "*.*")])
        if path:
            self._convert(Path(path).read_text(encoding="utf-8"), save_raw=False)

    def _convert(self, raw, save_raw):
        outs = self._out_dirs()
        if not outs:
            messagebox.showwarning(APP_NAME, "Set at least one output folder.")
            return
        if not self._guard_mc():
            return
        scan = self.scan_result or {"mods": [], "items": {}, "entities": {}, "craftable": {}}
        self._run(lambda: self._write(raw, scan, outs, save_raw=save_raw),
                  "Converting JSON to SNBT", notify=True)

    def _write(self, raw, scan, outs, save_raw, base=0.0, preserve_ids=False):
        # Honour Cancel HERE: every generate path (offline job, AI run - which
        # breaks its chapter loop on cancel and then falls through with a
        # PARTIAL chapter list - and both convert buttons) funnels into _write,
        # and none of them checked self._cancel before writing. A cancelled
        # offline run still wrote all 19 chapters and reported success
        # (repro: scratchpad/triage_a.py). Checked again just before the write
        # loop so a cancel during clean/build also lands before any file I/O.
        if self._cancel:
            raise RuntimeError("cancelled — nothing was written; your book is untouched")
        span = 1.0 - base
        self.phase(base + span * 0.15, "parsing JSON...")
        doc = extract_json(raw)
        self.log("  " + summarize(doc))
        if not doc.get("chapters"):
            raise ValueError("no chapters in the JSON")
        self.phase(base + span * 0.4, "cleaning / validating...")
        doc = clean_doc(doc, scan, self.log)
        self.phase(base + span * 0.6, "building chapters...")
        warns = []
        chapters, groups, extra = build_chapters(doc, warns.append, self._opts(preserve_ids=preserve_ids))
        book = self._book()
        if self._cancel:      # cancel pressed while cleaning/building: still in time
            raise RuntimeError("cancelled — nothing was written; your book is untouched")
        if self.opt_decor.get():
            self._retire_decor_pack()
        for i, out in enumerate(outs):
            self.phase(base + span * (0.75 + 0.2 * i / len(outs)), "writing SNBT -> %s" % out.name)
            self.log("-> %s" % out)
            write_snbt(out, chapters, groups, self.opt_backup.get(), self.log, book,
                       extra.get("reward_tables"), confirm_shrink=self._confirm_shrink)
            if save_raw:
                try:
                    (out / "autoquest_raw.json").write_text(
                        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
        if warns:
            self.log("%d warning(s):" % len(warns))
            for w in warns[:60]:
                self.log("  ! " + w)
        self.phase(1.0, "done")
        self.log("done." + ("  Close & relaunch Minecraft to load it." if self._mc_running else ""))
        return "wrote %d chapters to %d folder(s)" % (len(chapters), len(outs))

    def _confirm_shrink(self, old_n, new_n):
        """Called from the worker thread before a write that would shrink the book
        a lot. Tk isn't thread-safe, so bounce the dialog onto the UI thread."""
        box = {}
        done = threading.Event()

        def ask():
            try:
                box["ok"] = messagebox.askyesno(
                    APP_NAME,
                    "This will REPLACE your %d-chapter book with only %d chapter(s).\n\n"
                    "That usually means a themed generation ran with \"Build ONLY these "
                    "questlines\" ticked, or the model returned less than expected.\n\n"
                    "Replace the book?\n\n"
                    "(No = keep what you have. Your current chapters are backed up either way.)"
                    % (old_n, new_n))
            except Exception:
                # The dialog failed, so nobody was actually asked. This used to
                # default to True, which made the ONE guard against replacing a
                # 25-chapter book with 1 chapter answer "yes, replace it" by
                # itself (measured: dialog error -> True; unattended user ->
                # True after exactly 120s). The silent default must be the
                # non-destructive choice: keep the book.
                box["ok"] = False
            done.set()
        try:
            self.root.after(0, ask)
        except Exception:
            return False              # UI gone -> can't ask -> keep the book
        done.wait()                   # a question about destroying work can wait forever
        return box.get("ok", False)

    def _pack_root(self):
        """<instance>/resourcepacks from the first output dir (…/config/ftbquests/quests)."""
        for out in self._out_dirs():
            inst = out.parent.parent.parent  # quests -> ftbquests -> config -> instance
            if inst.is_dir():
                return inst / "resourcepacks"
        return None

    def _retire_decor_pack(self):
        """Backgrounds now use stock vanilla textures — delete the old generated pack."""
        rp = self._pack_root()
        if not rp:
            return
        old = rp / "FTBQ-Decor.zip"
        if old.exists():
            try:
                old.unlink()
                self.log("  removed old FTBQ-Decor.zip — backgrounds now use vanilla textures")
            except Exception:
                pass

    # ---- repair / improve --------------------------------------------- #
    def _first_out(self):
        outs = self._out_dirs()
        if not outs:
            messagebox.showwarning(APP_NAME, "Set the Output folder first (Setup tab).")
            return None
        return outs[0]

    def on_repair_scan(self):
        out = self._first_out()
        if not out:
            return

        def job():
            cd = out / "chapters"
            files = sorted(cd.glob("*.snbt")) if cd.is_dir() else []
            ok = bad = amp = 0
            for f in files:
                try:
                    d = snbt_loads(f.read_text(encoding="utf-8"))
                    ok += 1
                    if "&" in f.read_text(encoding="utf-8"):
                        amp += 1
                except Exception as e:
                    bad += 1
                    self.log("  ! %s: %s" % (f.name, e))
            msg = "%d chapter files - %d parse OK, %d unparseable, %d contain '&'" % (
                len(files), ok, bad, amp)
            self.events.put(("repair_lbl", msg))
            return msg
        self._run(job, "Scanning quest folder")

    def on_repair(self):
        out = self._first_out()
        if not out or not self._guard_mc():
            return
        backup = self.opt_backup.get()

        def job():
            self.phase(0.2, "parsing chapters...")
            r = repair_quests(out, backup, self.log)
            self.phase(1.0, "done")
            return "repair: %d files, %d fixes" % (r["files"], r["changes"])
        self._run(job, "Repairing quest files", notify=True)

    def on_polish(self):
        """Let the AI rewrite the WORDS and nothing else.

        on_improve asks the model to write prose AND add rewards AND fix item
        ids AND invent side quests. The prose is the part it is reliably good
        at; the structural edits are where it invents items that do not exist
        and quests that cannot be finished. This path asks for the first and
        makes the second impossible: merge_prose_only copies the verified book
        and lifts only title / subtitle / description / chapter name / group
        name out of the reply. A swapped item id or an invented quest is not
        rejected with a warning - it is never read.
        """
        out = self._first_out()
        if not out:
            return
        ai = self._ai_ready()
        if not ai or not self._guard_mc():
            return
        url, model, key = ai
        mt = self._int(self.max_tokens.get(), 32768)
        temp = round(self.temperature.get(), 2)
        scan = self.scan_result or {"mods": [], "items": {}, "entities": {},
                                    "craftable": {}}
        lang = self.language.get()
        popts = self._prompt_opts()

        def job():
            self.phase(0.1, "reading current book...")
            base = quests_to_doc(out)
            nq = sum(len(c["quests"]) for c in base["chapters"])
            if not nq:
                raise ValueError("no readable quests in %s/chapters" % out)
            self.log("current book: %d chapters, %d quests"
                     % (len(base["chapters"]), nq))
            self.phase(0.2, "building wording prompt...")
            prompt = build_prose_prompt(base, lang, popts)
            self.phase(0.28, "asking %s to reword %d quests..." % (model, nq))
            raw = call_ai(url, key, model, prompt, mt, self.log,
                          lambda: self._cancel, temp)
            self.phase(0.66, "AI responded (%d chars)" % len(raw))
            reply = extract_json(raw)
            self.phase(0.7, "merging wording only...")
            merged, st = merge_prose_only(base, reply, self.log)
            if not st["changed"]:
                raise ValueError(
                    "the AI returned no usable wording changes - your book is "
                    "untouched")
            return self._write(json.dumps(merged, ensure_ascii=False), scan,
                               [out], save_raw=True, base=0.72,
                               preserve_ids=True)
        self._run(job, "Polishing wording with AI", notify=True)

    def on_improve(self):
        out = self._first_out()
        if not out:
            return
        ai = self._ai_ready()
        if not ai or not self._guard_mc():
            return
        url, model, key = ai
        mt = self._int(self.max_tokens.get(), 32768)
        temp = round(self.temperature.get(), 2)
        scan = self.scan_result or {"mods": [], "items": {}, "entities": {}, "craftable": {}}
        lang = self.language.get()
        popts = self._prompt_opts()

        def job():
            self.phase(0.1, "reading current book...")
            doc = quests_to_doc(out)
            nq = sum(len(c["quests"]) for c in doc["chapters"])
            if not nq:
                raise ValueError("no readable quests in %s/chapters" % out)
            self.log("current book: %d chapters, %d quests" % (len(doc["chapters"]), nq))
            self.phase(0.2, "building improve prompt...")
            base_prompt = build_prompt(scan, self.selected_ids, lang, popts) if scan["mods"] else ""
            idlist = ""
            if scan["mods"]:
                idlist = base_prompt.split("=== VERIFIED ITEM IDs", 1)[-1]
                idlist = "=== VERIFIED ITEM IDs" + idlist
            prompt = (
                "You are improving an existing FTB Quests book (MC 1.20.1). Return the SAME "
                "JSON shape, KEEPING every existing quest \"id\" and chapter \"id\" exactly.\n"
                "Improve it: write / expand descriptions (%s), add a sensible \"rewards\" array "
                "to quests that lack one, fix any item id that is not in the verified list "
                "below, and add 2-3 new side quests per chapter that branch off an existing "
                "quest via \"dependencies\". New quest ids must be new lowercase strings.\n"
                "%s\n%s\n\nCURRENT BOOK:\n%s\n\nOutput only the improved JSON."
                % (lang,
                   "[STYLE] " + AESTHETIC_TEXT.get(popts["aesthetic"], ""),
                   idlist,
                   json.dumps(doc, ensure_ascii=False)))
            self.phase(0.28, "asking %s to improve %d quests..." % (model, nq))
            raw = call_ai(url, key, model, prompt, mt, self.log, lambda: self._cancel, temp)
            self.phase(0.7, "AI responded (%d chars)" % len(raw))
            return self._write(raw, scan, [out], save_raw=True, base=0.7, preserve_ids=True)
        self._run(job, "Improving quest book with AI", notify=True)

    def on_open_out(self):
        outs = self._out_dirs()
        if outs and outs[0].exists():
            os.startfile(str(outs[0]))
        elif outs:
            os.startfile(str(outs[0].parent if outs[0].parent.exists() else Path.home()))

    def on_clean_backups(self):
        removed = 0
        for out in self._out_dirs():
            for b in out.glob("chapters.backup-*"):
                if b.is_dir():
                    shutil.rmtree(b, ignore_errors=True)
                    removed += 1
        self.set_status("removed %d backup folder(s)" % removed)
        self.log("removed %d chapters.backup-* folder(s)" % removed)

    def _close(self):
        self._save_cfg()
        self.root.destroy()


def run_cli_build(mods_dir: str, out_dir: str, density: str = "normal") -> int:
    """Headless scan+build+write - the frozen-exe smoke path.

    AutoQuestGen.exe --build <modsdir> <outdir> [--density X]

    Deliberately THIN: it walks the exact functions the GUI's offline
    Build Quest Book button walks (scan_mods -> local_quest_doc ->
    extract_json -> clean_doc -> build_chapters -> write_snbt), with the
    GUI's own defaults for every option, so a CLI pass genuinely proves the
    GUI pipeline works on this machine. Returns a process exit code.
    """
    import sys

    def log(msg=""):
        line = str(msg)
        try:
            print(line)
        except Exception:
            pass                     # windowed exe: stdout may be gone
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    try:
        folder = Path(mods_dir)
        if not folder.is_dir():
            log("ERROR: mods folder does not exist: %s" % folder)
            return 2
        if density not in DENSITY_TARGETS:
            log("ERROR: unknown density %r (choose from: %s)"
                % (density, ", ".join(DENSITY_ORDER)))
            return 2
        out = Path(out_dir)
        log("AutoQuestGen %s - CLI build" % VERSION)
        log("  mods: %s" % folder)
        log("  out:  %s" % out)
        scan = scan_mods(folder, 400, log)
        sel = [m["mod_id"] for m in scan["mods"]]
        if not sel:
            log("ERROR: no mods found in %s" % folder)
            return 3
        # the GUI's defaults (App.__init__ tk vars), minus anything AI-only
        popts = {"density": density, "target": "", "groups": True,
                 "aesthetic": "balanced", "reward": "standard",
                 "progression": "linear", "layout": "line",
                 "group_style": "bold", "creativity": 0.4, "themes": [],
                 "themes_only": True, "vanilla_chapters": True,
                 "include_decor": False, "mod_desc": {}, "book_title": ""}
        doc = local_quest_doc(scan, sel, popts)
        log("offline build: " + summarize(doc))
        doc = extract_json(json.dumps(doc, ensure_ascii=False))
        if not doc.get("chapters"):
            log("ERROR: builder produced no chapters")
            return 3
        doc = clean_doc(doc, scan, log)
        warns = []
        bopts = {"auto_chain": True, "xp_rewards": False, "decor_art": True,
                 "aesthetic": "balanced", "quest_shape": "auto", "_scan": scan,
                 "groups": True, "style_chapters": True, "layout": "line",
                 "group_style": "bold", "creativity": 0.4,
                 "preserve_ids": False, "reward_mult": 1.0}
        chapters, groups, extra = build_chapters(doc, warns.append, bopts)
        if not chapters:
            log("ERROR: build produced no chapter files")
            return 3
        write_snbt(out, chapters, groups, True, log, {"title": "", "icon": "",
                   "progression_mode": "linear"}, extra.get("reward_tables"))
        for w in warns[:40]:
            log("  ! " + w)
        log("OK: wrote %d chapters, %d groups -> %s"
            % (len(chapters), len(groups), out))
        return 0
    except Exception:
        log("FAILED:\n" + traceback.format_exc())
        return 1


def main():
    import sys
    argv = sys.argv[1:]
    if "--build" in argv:
        i = argv.index("--build")
        rest = argv[i + 1:]
        if len(rest) < 2:
            print("usage: AutoQuestGen --build <modsdir> <outdir> [--density X]")
            raise SystemExit(2)
        density = "normal"
        if "--density" in rest:
            j = rest.index("--density")
            density = rest[j + 1] if j + 1 < len(rest) else ""
            del rest[j:j + 2]
        raise SystemExit(run_cli_build(rest[0], rest[1], density))
    _set_aumid()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()