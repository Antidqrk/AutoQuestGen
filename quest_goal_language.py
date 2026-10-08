"""Ground natural quest goals in local registries and authored guide facts.

This is a bounded, deterministic vocabulary matcher, not a language model.
Names never establish availability: a matching registry/guide objective must
exist in the selected pack. Concrete requests never become generic mod mastery.
"""
from __future__ import annotations

from collections import defaultdict, OrderedDict
from difflib import SequenceMatcher
import re
from threading import RLock

_CACHE = OrderedDict()
_CACHE_LOCK = RLock()
# Botania 1.20.1-456 ManaBurstEntity extends ThrowableProjectile (verified
# from the installed JAR class header), not LivingEntity. These vanilla IDs
# are likewise projectiles, decoration, vehicles or transient world objects.
_NONLIVING = {"botania:mana_burst", *{"minecraft:" + name for name in (
    "item", "armor_stand", "painting", "item_frame", "glow_item_frame",
    "end_crystal", "tnt", "fireball", "small_fireball", "snowball", "egg",
    "trident", "firework_rocket", "eye_of_ender", "evoker_fangs",
)}}
_FILLER = set("i me my we our want would like please a an the to from in of for with on at into through by some goal quest quests line lines pack modpack mod mods is be have need can you help let us".split())
_VERBS = set("kill defeat beat slay fight conquer craft make build create obtain get collect acquire reach enter visit travel explore find discover open activate start assemble operate run automate complete finish master learn unlock produce generate become".split())
_BROAD = set("magic magical tech technology factory industrial automation machines engineering power grid space outer moon planets boss bosses combat battles dungeons adventure farming farm cooking food crops storage inventory network dimensions exploration journey expedition everything all every major ultimate endgame end game campaign focused focus style".split())
_COMBAT = {"kill", "defeat", "beat", "slay", "fight"}
_TRAVEL = {"reach", "enter", "visit", "travel", "explore"}
_ITEM_VERBS = {"craft", "obtain", "collect", "acquire"}
_ACTION_VERBS = {"open", "activate", "assemble", "operate", "run", "automate"}
_BROAD_DESCRIPTORS = set("strongest powerful greatest massive giant huge big advanced amazing awesome entire whole ultimate best expert professional thriving successful self sufficient self-sufficient full sustainable".split())
_AMBIGUOUS_NOUNS = {"guardian", "dragon", "portal", "reactor", "terminal", "star", "station"}
_PREFIX_KIND = {"kill": "entity", "dim": "dimension", "structure": "structure", "check": "milestone", "advancement": "advancement"}
_ID = re.compile(r"\b(?:(?:kill|dim|structure|check|advancement):)?[a-z0-9_.-]+:[a-z0-9_./-]+\b")

# Names differ from registry IDs. Gaia's alias and hard-mode distinction are
# verified by Botania 452/455/456 en_us and player_killed_entity advancements.
_ALIASES = {
    ("entity", "botania:doppleganger"): ("Gaia Guardian", "Guardian of Gaia", "Gaia boss"),
    ("advancement", "botania:challenge/gaia_guardian_hardmode"):
        ("Gaia Guardian II", "Gaia Guardian 2", "second Gaia Guardian", "hardmode Gaia Guardian", "Gaia hard mode"),
}
_MOD_ALIASES = {"ae2": ("applied energistics", "applied energistics 2", "ae2"),
                "mna": ("mana and artifice",), "irons_spellbooks": ("irons spellbooks", "irons spells",),
                "mekanismgenerators": ("mekanism generators",)}

# Small mechanic adapters extend the same registry/guide index. Their material
# anchors are authored guide rows, and completion is explicitly manual: owning
# a casing, station or pool is not proof that its machine actually works.
_ACTIONS = (
    ("botania", "mana_automation", "Automate Mana Production",
     ("automate mana", "mana automation", "mana production", "mana farm"),
     ("botania:open_crate", "botania:endoflame", "botania:mana_spreader", "botania:mana_pool"),
     "Set up controlled fuel delivery to an Endoflame, bind it through a Mana Spreader to a Mana Pool, and verify mana keeps arriving without manual refuelling. Confirm this milestone after the setup works."),
    ("botania", "open_alfheim", "Open the Elven Gateway",
     ("open alfheim portal", "activate alfheim portal", "open elven portal", "elven gateway", "open alfheim"),
     ("botania:alfheim_portal",),
     "Build the Elven Gateway using the Lexica's layout, including Natura Pylons and mana supplies. Open it and complete an Elven trade, then confirm this milestone. Simply owning the portal block does not complete the action."),
    ("create", "train", "Assemble a Working Train",
     ("train", "build train", "assemble train", "working railway", "working train"),
     ("create:track_station", "create:track", "create:railway_casing"),
     "Lay a usable stretch of track, place a Train Station and use its assembly controls to assemble a train. Confirm once the train has actually assembled; collecting railway parts alone does not complete this milestone."),
    ("mekanismgenerators", "fission", "Build a Working Fission Reactor",
     ("fission", "fission reactor", "build fission reactor", "mekanism fission"),
     ("mekanismgenerators:fission_reactor_casing", "mekanismgenerators:fission_fuel_assembly",
      "mekanismgenerators:control_rod_assembly", "mekanismgenerators:fission_reactor_port"),
     "Build the complete fission multiblock and verify that it forms. Connect fuel, coolant, steam handling and radioactive waste storage before operation. Confirm after the completed reactor is ready; a collection of casing blocks is not a formed reactor."),
    ("ae2", "autocrafting", "Complete an ME Autocrafting Job",
     ("autocrafting", "automatic crafting", "automate crafting", "me autocrafting"),
     ("ae2:1k_crafting_storage", "ae2:pattern_provider", "ae2:molecular_assembler", "ae2:pattern_encoding_terminal"),
     "Connect a crafting CPU, Pattern Provider and Molecular Assembler to the powered ME network. Encode a recipe, request it from the terminal, and confirm this milestone after the requested output returns to storage."),
)


def _words(text):
    words = re.findall(r"[a-z0-9]+", str(text).lower().replace("'", ""))
    return tuple("boss" if word == "bosses" else word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")) else word for word in words)


def _phrase(text):
    return " ".join(_words(text))


def _tokens(text):
    return tuple(word for word in _words(text) if word not in _FILLER and word not in _VERBS)


def _label(ref, names):
    label = names.get(ref)
    if label and not re.search(r"%(?:\d+\$)?[sdif]", str(label)):
        return str(label)
    return ref.split(":")[-1].replace("_", " ").replace("/", " ").title()


def _catalog(scan, field):
    value = scan.get(field) or {}
    if isinstance(value, dict):
        return {ref for values in value.values() for ref in values}
    return set(value)


def _stamp(scan, A):
    # Cache is bounded and mutation-sensitive. Hashing the supplied catalog is
    # cheap compared with rebuilding labels or fuzzy-matching every item.
    fields = []
    for key in ("items", "entities", "dimensions", "structures", "advancements"):
        fields.append(hash(frozenset(_catalog(scan, key))))
    for key in ("names", "entity_names", "dimension_names", "structure_names"):
        fields.append(hash(frozenset((k, str(v)) for k, v in (scan.get(key) or {}).items())))
    fields.extend((hash(frozenset(scan.get("vanilla") or ())), hash(frozenset(scan.get("unobtainable") or ())),
                   tuple((m["mod_id"], m.get("name", "")) for m in scan.get("mods", ())),
                   id(scan.get("advancement_info")), id(scan.get("drops")), id(A._RESEARCHED),
                   id(A._GUIDED_REQUIREMENTS), id(A._GUIDE_CHECKS)))
    return (id(scan), tuple(fields))


def _index(scan):
    import autoquestgen as A
    stamp = _stamp(scan, A)
    with _CACHE_LOCK:
        if stamp in _CACHE:
            _CACHE.move_to_end(stamp)
            return _CACHE[stamp][1]
    installed = {m["mod_id"]: m.get("name", m["mod_id"]) for m in scan.get("mods", ())}
    installed["minecraft"] = "Minecraft"
    catalog = {"item": (_catalog(scan, "items") | set(scan.get("vanilla") or ())) - set(scan.get("unobtainable") or ()),
               "entity": _catalog(scan, "entities"), "dimension": _catalog(scan, "dimensions"),
               "structure": _catalog(scan, "structures"), "advancement": _catalog(scan, "advancements"),
               "milestone": A._available_guide_checks(scan)}
    catalog["entity"] = set(A._mob_entities(catalog["entity"], catalog["item"])) - _NONLIVING
    names = {"item": scan.get("names") or {}, "entity": scan.get("entity_names") or {},
             "dimension": scan.get("dimension_names") or {}, "structure": scan.get("structure_names") or {}}
    rows = {}
    def add(kind, target, owner=None, aliases=(), **extra):
        owner = owner or target.split(":")[0]
        key = (kind, target)
        if key not in rows:
            rows[key] = {"kind": kind, "target_id": target, "mod": owner,
                         "label": _label(target, names.get(kind, {})), "requires": [],
                         "_aliases": set(), "_available": target in catalog[kind]}
        row = rows[key]
        row.update(extra)
        row["_aliases"].update(str(alias) for alias in aliases if alias)
        row["_aliases"].update((row["label"], target.split(":")[-1].replace("_", " ").replace("/", " ")))
        return row
    for kind in catalog:
        for target in sorted(catalog[kind]):
            if kind == "milestone":
                continue
            add(kind, target)
    mods = set(installed) | set(A._RESEARCHED)
    for mid in sorted(mods):
        for ref, title, description in A._curated_chain(mid):
            for candidate in ref.split("|"):
                candidate = candidate.strip()
                prefix, _, rest = candidate.partition(":")
                kind = _PREFIX_KIND.get(prefix, "item")
                target = candidate if kind in ("item", "milestone") else rest
                owner = mid if kind == "milestone" else target.split(":")[0]
                row = add(kind, target, owner, aliases=(title,), guide_ref=candidate,
                          requires=list(A._GUIDED_REQUIREMENTS.get(candidate, ())), description=description)
                if kind == "milestone":
                    row["label"] = title or _label(target, {})
                    row["_aliases"].add(row["label"])
                    row["icon"] = A._GUIDE_CHECKS.get(candidate, {}).get("icon", "")
                needs = A._GUIDE_REQUIRED_MODS.get(candidate, ())
                if set(needs) - set(installed):
                    row["_available"] = False
                    row["_missing_mods"] = sorted(set(needs) - set(installed))
    for target, info in (scan.get("advancement_info") or {}).items():
        if target not in catalog["advancement"]:
            continue
        row = add("advancement", target, aliases=(info.get("title", ""),),
                  label=str(info.get("title") or _label(target, {})), description=str(info.get("description") or ""),
                  guide_ref="advancement:" + target, icon=info.get("icon", ""))
    for (kind, target), aliases in _ALIASES.items():
        row = add(kind, target, aliases=aliases)
        if kind == "entity":
            row["label"] = "Gaia Guardian"
            # The installed Lexica Ritual of Gaia entry explicitly requires
            # an active beacon, Gaia Pylons and a Terrasteel offering.
            row["requires"] = list(dict.fromkeys(row["requires"] + ["minecraft:beacon"]))
        else:
            row["label"] = "Gaia Guardian II"
            row["requires"] = ["kill:botania:doppleganger", "botania:gaia_ingot"]
            row["description"] = "Complete the harder Ritual of Gaia and earn its matching advancement. The ordinary Gaia Guardian fight does not satisfy this goal."
        row.setdefault("guide_ref", ("kill:" if kind == "entity" else "advancement:") + target)
    # A trophy's unique drop source can identify an otherwise obscure entity.
    for item, evidence in (scan.get("drops") or {}).items():
        entities = list(evidence.get("entities") or ())
        display = _label(item, names["item"])
        if len(entities) == 1 and entities[0] in catalog["entity"] and display.lower().endswith(" trophy"):
            add("entity", entities[0], aliases=(display[:-7],))
    for mid, slug, title, aliases, requirements, description in _ACTIONS:
        target = "check:" + mid + ":goal_" + slug
        row = add("milestone", target, mid, aliases=aliases, label=title, guide_ref=target,
                  requires=list(requirements), description=description, icon=requirements[0], _action=True)
        row["_available"] = mid in installed and all(ref in catalog["item"] for ref in requirements)
        if mid == "mekanismgenerators" and "mekanism" not in installed:
            row["_available"] = False
            row["_missing_mods"] = ["mekanism"]
    by_token = defaultdict(set)
    by_id = defaultdict(set)
    vocab = defaultdict(set)
    for key, row in rows.items():
        row["_forms"] = {tuple(_tokens(alias)) for alias in row["_aliases"] if _tokens(alias)}
        for form in row["_forms"]:
            for token in form:
                by_token[token].add(key)
                vocab[(token[:1], len(token))].add(token)
        by_id[row["target_id"]].add(key)
        typed_prefix = {"entity": "kill:", "dimension": "dim:", "structure": "structure:", "advancement": "advancement:"}.get(row["kind"])
        if typed_prefix:
            by_id[typed_prefix + row["target_id"]].add(key)
        if row.get("guide_ref"):
            by_id[row["guide_ref"]].add(key)
    result = {"rows": rows, "tokens": by_token, "vocab": vocab, "ids": by_id,
              "installed": installed, "mods": mods}
    with _CACHE_LOCK:
        # Concurrent chooser and validation requests may build the same scan
        # once each; publish one immutable index, with atomic LRU bookkeeping.
        stored = _CACHE.setdefault(stamp, (scan, result))[1]
        _CACHE.move_to_end(stamp)
        while len(_CACHE) > 3:
            _CACHE.popitem(last=False)
        return stored


def _qualifiers(text, index):
    tokens = list(_words(text))
    found = set()
    choices = []
    for mid in index["mods"]:
        aliases = {mid.replace("_", " "), index["installed"].get(mid, ""), *_MOD_ALIASES.get(mid, ())}
        aliases.update(re.sub(r"^(?:the|a|an)\s+", "", alias, flags=re.I) for alias in tuple(aliases))
        for alias in aliases:
            words = _words(alias)
            if words:
                choices.append((words, mid))
    for words, mid in sorted(choices, key=lambda x: (-len(x[0]), x[1])):
        if mid == "create" and re.match(r"^create\s+(?:a|an|the|my|magic|tech)\b", text.lower()):
            continue
        for pos in range(len(tokens) - len(words) + 1):
            if tuple(tokens[pos:pos + len(words)]) == words:
                found.add(mid)
                tokens[pos:pos + len(words)] = [""] * len(words)
    return found, tuple(token for token in tokens if token and token not in _FILLER and token not in _VERBS)


def _owner_matches(owner, qualifiers):
    return not qualifiers or owner in qualifiers or (owner == "mekanismgenerators" and "mekanism" in qualifiers)


def _broad(query):
    # Preserve the campaign planner's established domain vocabulary. This
    # executes only after concrete matching, so an installed Moon dimension
    # wins over the broad space domain for "reach the Moon".
    from quest_campaign import _DOMAINS
    vocabulary = set(_words(" ".join(_BROAD)))
    for profile in _DOMAINS.values():
        for phrase in profile["words"]:
            vocabulary.update(_words(phrase))
    core = set(query) - _BROAD_DESCRIPTORS
    return bool(core) and core <= vocabulary


def _score(query, form):
    q, f = set(query), set(form)
    if not q or not f:
        return 0.0
    if q == f:
        return 1.0
    if q <= f:
        return max(0.0, .94 - .045 * (len(f) - len(q)))
    if len(q) == 1:
        if len(f) != 1 or len(next(iter(q))) < 5:
            return 0.0
        score = SequenceMatcher(None, next(iter(q)), next(iter(f))).ratio()
        return score if score >= .88 else 0.0
    similarities = [max(SequenceMatcher(None, token, other).ratio() for other in f) for token in q]
    # At least one exact content word anchors typo correction. A nonsense
    # sentence cannot be silently mapped to the closest vaguely related item.
    if min(similarities) < .70 or not q & f:
        return 0.0
    return sum(similarities) / len(similarities) - .04 * max(0, len(f) - len(q))


def _pool(query, index):
    pool = set()
    for token in query:
        pool.update(index["tokens"].get(token, ()))
        if len(token) >= 5:
            for length in range(len(token) - 2, len(token) + 3):
                for other in index["vocab"].get((token[0], length), ()):
                    if SequenceMatcher(None, token, other).ratio() >= .70:
                        pool.update(index["tokens"][other])
    return pool


def _public(row, note=""):
    result = {key: value for key, value in row.items() if not key.startswith("_")}
    result["match_note"] = note
    kind, target = row["kind"], row["target_id"]
    if kind == "milestone":
        result["task"] = {"type": "checkmark", "title": "Confirm " + row["label"],
                          "_guide_ref": row.get("guide_ref", target), "_guide_mod": row["mod"]}
        if "confirm" not in result.get("description", "").lower():
            result["description"] = result.get("description", "") + " Confirm this milestone only after completing the described action."
    else:
        task_type, field = {"item": ("item", "item"), "entity": ("kill", "entity"),
                            "dimension": ("dimension", "dimension"), "structure": ("structure", "structure"),
                            "advancement": ("advancement", "advancement")}[kind]
        result["task"] = {"type": task_type, field: target}
        result["task"].update({"value": 1} if kind == "entity" else {"count": 1} if kind == "item" else {})
    return result


def _finish(row, index, selected, text):
    owner = row["mod"]
    if owner not in index["installed"]:
        raise ValueError("This goal needs " + owner.replace("_", " ").title() + ", which is not installed in this pack.")
    if owner != "minecraft" and owner not in selected:
        raise ValueError("Select " + index["installed"][owner] + " to use the goal ‘" + row["label"] + "’.")
    if not row["_available"]:
        needs = row.get("_missing_mods") or []
        suffix = " It also needs " + ", ".join(needs) + "." if needs else " Choose another goal in Explore available goals."
        raise ValueError("This pack has no verified available " + row["label"] + " objective." + suffix)
    return _public(row, "Matched ‘" + str(text).strip() + "’ to " + row["label"] + " (" + index["installed"][owner] + ").")


def match_goal(scan, selected_ids, text):
    """Resolve one concrete goal, return None for broad domains/mod mastery.

    Exact IDs are strict; fuzzy matching only repairs close, anchored typos.
    Missing/unselected/ambiguous concrete targets raise actionable ValueError.
    """
    text = str(text or "").strip()
    if not text:
        return None
    index = _index(scan)
    selected = set(selected_ids) if selected_ids else set(index["installed"])
    words = set(_words(text))
    ids = list(dict.fromkeys(_ID.findall(text.lower())))
    if len(ids) > 1:
        return None  # Existing campaign API handles explicit multi-item goals.
    requested = "entity" if words & _COMBAT else "item" if words & _ITEM_VERBS else "place" if words & _TRAVEL else None
    if ids:
        matches = [index["rows"][key] for key in index["ids"].get(ids[0], ())]
        prefix = ids[0].split(":")[0]
        explicit_kind = _PREFIX_KIND.get(prefix, requested)
        if explicit_kind:
            allowed = ("dimension", "structure") if explicit_kind == "place" else (explicit_kind,)
            matches = [row for row in matches if row["kind"] in allowed]
        if not matches:
            if requested == "item":
                raise ValueError("This pack has no verified obtainable item named ‘" + ids[0] + "’ (exact ID). Check its spelling or choose a goal in Explore available goals.")
            raise ValueError("No verified goal matches the exact ID ‘" + ids[0] + "’. Check its spelling or choose a goal in Explore available goals.")
        if len(matches) > 1:
            raise ValueError("That ID identifies several goal types. Specify whether you want to craft an item, defeat a creature, or visit a place.")
        return _finish(matches[0], index, selected, text)
    qualifiers, query = _qualifiers(text, index)
    if not query and requested == "place":
        # A dimension may have exactly the same name as its parent mod.
        # "Enter the Twilight Forest" requests a place, not mod mastery.
        query = _tokens(text)
    if not query:
        return None
    pool = _pool(query, index)
    scored = []
    for key in pool:
        row = index["rows"][key]
        if not _owner_matches(row["mod"], qualifiers):
            continue
        if requested == "entity" and row["kind"] not in ("entity", "advancement"):
            continue
        if requested == "item" and row["kind"] != "item":
            continue
        if requested == "place" and row["kind"] not in ("dimension", "structure"):
            continue
        score = max((_score(query, form) for form in row["_forms"]), default=0.0)
        if score >= .80:
            # A typed fight prefers the entity over an ordinary kill advancement;
            # explicit II/hardmode aliases still win by their exact extra words.
            if requested == "entity" and row["kind"] == "advancement":
                score -= .04
            if requested is None and words & _ACTION_VERBS and row["kind"] != "milestone":
                score -= .04
            scored.append((score, key, row))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    broad = _broad(query)
    if broad:
        # Domain words found inside poetic guide titles are not specific goals.
        # A real canonical location (Moon) or project (AE2 autocrafting) is.
        scored = [(score, key, row) for score, key, row in scored
                  if row.get("_action") or set(query) == set(_tokens(row["label"]))]
    if scored:
        best = scored[0][0]
        available = [(score, key, row) for score, key, row in scored
                     if row["_available"] and row["mod"] in index["installed"] and score >= best - .025]
        if available:
            # Knowledge of an absent mod is useful for missing-goal feedback,
            # but must not make a uniquely installed target ambiguous.
            scored = available + [(score, key, row) for score, key, row in scored if score < best - .025]
    if scored and scored[0][0] >= .84:
        best = scored[0][0]
        close = [row for score, _key, row in scored if score >= best - .025]
        if len(query) == 1 and query[0] in _AMBIGUOUS_NOUNS:
            close = [row for score, _key, row in scored if score >= .80]
        if len(close) > 1:
            options = "; ".join(row["label"] + " (" + index["installed"].get(row["mod"], row["mod"]) + ")" for row in close[:5])
            raise ValueError("That goal is ambiguous. Choose one: " + options + ". Add the mod or the full target name.")
        return _finish(scored[0][2], index, selected, text)
    if broad:
        return None
    suggestions = available_goals(scan, selected_ids, query=" ".join(query), limit=4)
    hint = " Try " + "; ".join(row["label"] for row in suggestions) + "." if suggestions else " Try a specific boss, item, location or guided project, or open Explore available goals."
    raise ValueError("I could not match ‘" + text + "’ to an installed Minecraft goal." + hint)


def available_goals(scan, selected_ids, query="", limit=200):
    """Searchable offline chooser entries; labels are friendly, queries exact."""
    index = _index(scan)
    selected = set(selected_ids) if selected_ids else set(index["installed"])
    tokens = _tokens(query)
    pool = _pool(tokens, index) if tokens else set(index["rows"])
    rows = [index["rows"][key] for key in pool]
    rows = [row for row in rows if row["_available"] and (row["mod"] == "minecraft" or row["mod"] in selected)]
    if tokens:
        rows = [row for row in rows if max((_score(tokens, form) for form in row["_forms"]), default=0) >= .65]
    rows.sort(key=lambda row: (not bool(row.get("guide_ref")), row["mod"], row["label"].lower(), row["kind"], row["target_id"]))
    out = []
    for row in rows[:max(0, int(limit))]:
        value = _public(row)
        value["query"] = ("craft " + row["target_id"]) if row["kind"] == "item" else row.get("guide_ref") or (("kill:" if row["kind"] == "entity" else "dim:" if row["kind"] == "dimension" else "structure:" if row["kind"] == "structure" else "advancement:" if row["kind"] == "advancement" else "") + row["target_id"])
        out.append(value)
    return out
