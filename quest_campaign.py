"""Offline campaign planning from installed content and the generated quest graph.

No network, model, invented recipes, or new Minecraft items. Goal descriptions
are rules and templates; all objectives are resolved against the current scan.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import defaultdict


_DOMAINS = {
    "technology": {
        "words": ("technology", "tech", "factory", "industrial", "industry", "automation", "automate", "machines", "engineering", "power grid"),
        "mods": ("create", "mekanism", "thermal", "immersiveengineering", "industrialforegoing", "pneumaticcraft", "powah", "enderio"),
        "categories": ("tech",), "themes": ("automation", "power"),
        "title": "The Grand Workshop", "finale": "The Factory Comes Together",
        "purpose": "Bring together the machines and equipment earned through the engineering branches.",
    },
    "magic": {
        "words": ("magic", "magical", "mage", "wizard", "sorcery", "arcane", "rituals", "spellcasting"),
        "mods": ("botania", "ars_nouveau", "bloodmagic", "occultism", "malum", "irons_spellbooks", "goety", "eidolon"),
        "categories": ("magic",), "themes": ("magic",),
        "title": "The Arcane Convergence", "finale": "A Collection of Great Works",
        "purpose": "Assemble the artifacts earned along the selected magical paths.",
    },
    "space": {
        "words": ("space", "rocket", "rockets", "astronaut", "planets", "moon", "mars", "galactic"),
        "mods": ("ad_astra", "beyond_earth", "galacticraft", "advancedrocketry"),
        "categories": (), "themes": ("space",),
        "title": "Beyond the Horizon", "finale": "Ready for the Next World",
        "purpose": "Bring together the flight equipment and milestones from the space branches.",
    },
    "adventure": {
        "words": ("boss", "bosses", "combat", "defeat", "conquer", "battle", "dungeons", "warrior"),
        "mods": ("twilightforest", "cataclysm", "aquamirae", "iceandfire", "alexscaves", "blue_skies"),
        "categories": ("mob",), "themes": ("combat", "exploration"),
        "title": "The Great Hunts", "finale": "Proof of the Journey",
        "purpose": "Complete the major encounters and gather the trophies selected for this campaign.",
    },
    "farming": {
        "words": ("farm", "farming", "agriculture", "harvest", "cooking", "food", "kitchen", "chef", "crops", "bakery"),
        "mods": ("farmersdelight", "croptopia", "bakery", "candlelight", "farm_and_charm", "productivebees", "mysticalagriculture"),
        "categories": ("food",), "themes": ("farming", "cooking"),
        "title": "From Field to Feast", "finale": "The Harvest Table",
        "purpose": "Gather the finished dishes and useful harvest equipment from the selected food and farming branches.",
    },
    "storage": {
        "words": ("storage", "warehouse", "inventory", "digital network", "me network", "autocrafting", "autocraft"),
        "mods": ("ae2", "refinedstorage", "storagedrawers", "sophisticatedstorage", "sophisticatedbackpacks", "functionalstorage", "toms_storage"),
        "categories": (), "themes": ("storage", "automation"),
        "title": "Everything in Its Place", "finale": "The Collection Is Connected",
        "purpose": "Assemble the storage and crafting equipment earned through the selected storage branches.",
    },
    "exploration": {
        "words": ("explore", "exploration", "journey", "expedition", "dimensions", "discover", "adventure"),
        "mods": ("twilightforest", "aether", "deep_aether", "blue_skies", "undergarden", "alexscaves", "tropicraft"),
        "categories": ("world",), "themes": ("exploration",),
        "title": "An Atlas of Adventures", "finale": "Bring the Journey Home",
        "purpose": "Complete the selected expeditions and bring their achievements together.",
    },
}
_ALL_WORDS = ("everything", "all mods", "every mod", "all the mods", "all major", "endgame", "end game", "ultimate", "campaign", "complete the pack", "finish the pack")
_GENERIC_WORDS = {"i", "want", "a", "an", "the", "to", "make", "build", "create", "craft", "obtain", "get", "collect", "reach", "complete", "master", "finish", "my", "goal", "is", "pack", "modpack", "please", "with", "and", "in", "of", "for", "become", "focused", "on", "style", "like"}
_MATERIAL = re.compile(r"(?:^|_)(?:ingot|nugget|dust|ore|log|plank|petal|seed|leaf|brick|slab|stairs|wall|fence)s?(?:_|$)")
_EQUIPMENT = re.compile(r"(?:sword|blade|staff|wand|spell_?book|grimoire|rocket|reactor|controller|assembler|crafter|mechanism|jetpack|armor|chestplate|storage|drive|terminal|network|trophy|relic|pylon|portal|tiara|ring|amulet|meal|feast|cake|pie)")
_CAPSTONES = {
    "botania:gaia_ingot": 85, "botania:dice": 100, "botania:king_key": 95,
    "botania:flugel_eye": 95, "botania:odin_ring": 95,
    "botania:thor_ring": 95, "botania:loki_ring": 95,
    "create:train_controls": 80, "create:precision_mechanism": 70,
    "create:mechanical_crafter": 60, "mekanism:ultimate_control_circuit": 70,
    "mekanism:mekasuit_bodyarmor": 100, "mekanism:pellet_polonium": 90,
    "ae2:crafting_terminal": 70, "ae2:crafting_storage_64k": 90,
    "ars_nouveau:archmage_spell_book": 90,
}
_VANILLA_GOALS = {
    "minecraft:beacon": ("minecraft:nether_star", "minecraft:glass", "minecraft:obsidian"),
    "minecraft:ender_chest": ("minecraft:ender_eye", "minecraft:obsidian"),
}
_VANILLA_ACQUISITION = {
    "minecraft:nether_star", "minecraft:obsidian", "minecraft:glass", "minecraft:diamond",
    "minecraft:emerald", "minecraft:redstone", "minecraft:lapis_lazuli", "minecraft:coal",
    "minecraft:iron_ingot", "minecraft:gold_ingot", "minecraft:copper_ingot", "minecraft:quartz",
    "minecraft:ender_pearl", "minecraft:blaze_rod", "minecraft:gunpowder", "minecraft:bone",
    # Vanilla End City ship.nbt contains dragon_wall_head; the dragon_head
    # block loot table drops the inventory head. It is not a crafting recipe.
    "minecraft:dragon_head",
}


def _norm(text):
    return " ".join(re.findall(r"[a-z0-9]+", str(text).lower()))


def _label(item, scan):
    name = (scan.get("names") or {}).get(item)
    # Some translations are format templates (e.g. "%s Data Model"), not
    # names until their stack's creature/type argument has been supplied.
    if name and not re.search(r"%(?:\d+\$)?[sdif]", str(name)):
        return str(name)
    return item.split(":")[-1].replace("_", " ").replace("/", " ").title()


def _known(scan):
    values = set(scan.get("vanilla") or ())
    for items in (scan.get("items") or {}).values():
        values.update(items)
    return values - set(scan.get("unobtainable") or ())


def _selected(scan, selected_ids):
    all_mods = {m["mod_id"]: m for m in scan.get("mods", [])}
    chosen = set(selected_ids) if selected_ids else set(all_mods)
    return all_mods, chosen


def _phrase(text, phrase):
    return (" " + _norm(phrase) + " ") in (" " + text + " ")


def _named_mod(text, mid, name):
    if mid == "create":
        # In freeform goals, "create a magic pack" uses an ordinary verb.
        # Require a mod-shaped phrase before interpreting that verb as a name.
        return text == "create" or any(_phrase(text, phrase) for phrase in (
            "master create", "complete create", "learn create", "using create",
            "with create", "want create", "create mod", "create automation", "create s",
        ))
    return _phrase(text, mid.replace("_", " ")) or _phrase(text, name)


def _ordinary_required(item, scan):
    """All-route prerequisites. Alternative materials never become an AND gate."""
    # Mods often add recycling/compression recipes for vanilla resources.
    # Those recipes do not erase their ordinary mining, smelting or loot routes.
    if item in _VANILLA_ACQUISITION:
        return set()
    recipes = (scan.get("craft_recipes") or {}).get(item) or ()
    if not recipes:
        ingredients = (scan.get("edges") or {}).get(item, ()) or _VANILLA_GOALS.get(item, ())
        return {g for g in ingredients if g != item and not g.startswith("#")}
    routes = []
    for _kind, ingredients in recipes:
        route = {g for g in ingredients if not g.startswith("#") and g != item}
        for ingredient in ingredients:
            if ingredient.startswith("#"):
                members = set((scan.get("tags") or {}).get(ingredient[1:]) or ())
                if len(members) == 1:
                    route.update(members - {item})
        routes.append(route)
    return set.intersection(*routes) if routes else set()


def _stack_identity(stack):
    return str(stack.get("id", "")) + str(stack.get("components") or "")


def _base_item(identity):
    return re.split(r"[\[{]", identity, maxsplit=1)[0]


def _pack_candidates(identity, scan):
    return [row for row in (scan.get("pack_recipe_report") or {}).get("recipes", [])
            if isinstance(row.get("output"), dict) and _stack_identity(row["output"]) == identity]


def _ordinary_route_available(item, scan, trail=()):
    """Proof independent of a rejected optional script, not registry presence alone."""
    if item not in _known(scan) or item in trail:
        return False
    if (item in _VANILLA_ACQUISITION or item in set(scan.get("vanilla_craftable") or ())
            or (scan.get("drops") or {}).get(item)
            or (item.startswith("minecraft:") and (scan.get("vanilla_sources") or {}).get(item))):
        return True
    if _reviewed_acquisition(item):
        return True
    known = _known(scan)
    for _kind, ingredients in (scan.get("craft_recipes") or {}).get(item, []):
        valid = True
        for ingredient in ingredients:
            if ingredient.startswith("#"):
                if not (set((scan.get("tags") or {}).get(ingredient[1:]) or ()) & known):
                    valid = False
                    break
            elif ingredient not in known or ingredient in set(trail) | {item}:
                valid = False
                break
        if valid:
            return True
    return False


def _reviewed_acquisition(item):
    # The scanner already requires real inventory assets before an item enters
    # _known. These individually reviewed guide notes supply the separate
    # acquisition evidence for event drops and in-world conversion mechanics.
    import autoquestgen as A
    return bool(A._GUIDE_ACQUISITION.get(item))


def _pack_recipe(identity, scan, trail=(), depth=0):
    """First viable authored route, keeping component identities and alternatives apart."""
    if identity in trail or depth > 16:
        return None
    known = _known(scan)
    installed = {m["mod_id"] for m in scan.get("mods", [])} | {"minecraft"}
    trail = set(trail) | {identity}
    for recipe in _pack_candidates(identity, scan):
        machine = str(recipe.get("kind") or "").split(":")[0]
        if machine not in installed or not recipe.get("ingredients"):
            continue
        viable = True
        for stack in recipe["ingredients"]:
            ingredient = _stack_identity(stack)
            item = str(stack.get("id") or "")
            if int(stack.get("count", 0)) <= 0 or ingredient in trail:
                viable = False
                break
            if item.startswith("#"):
                if not (set((scan.get("tags") or {}).get(item[1:]) or ()) & known):
                    viable = False
                    break
            elif item not in known:
                viable = False
                break
            if _pack_candidates(ingredient, scan):
                if not _pack_recipe(ingredient, scan, trail, depth + 1) and not (
                        not stack.get("components") and _ordinary_route_available(item, scan, trail)):
                    viable = False
                    break
            else:
                # Reject a recycling input whose only known prerequisite is
                # the requested final item. Do not turn that into a startup route.
                todo, seen = [item], set()
                while todo and len(seen) < 2000:
                    current = todo.pop()
                    if current in trail:
                        viable = False
                        break
                    if current in seen:
                        continue
                    seen.add(current)
                    todo.extend(_ordinary_required(current, scan) - seen)
                if not viable:
                    break
        if viable and all(str(fluid.get("id", "")).lstrip("#").split(":")[0] in installed
                          for fluid in recipe.get("fluids", [])):
            return recipe
    return None


def _required(item, scan):
    """Use the chosen pack project, otherwise conservative ordinary prerequisites."""
    recipe = _pack_recipe(item, scan)
    if recipe:
        return {_stack_identity(stack) for stack in recipe["ingredients"]}
    if _pack_candidates(item, scan):
        if not _ordinary_route_available(item, scan):
            raise ValueError("No complete non-circular pack recipe is available for " + _label(_base_item(item), scan)
                             + ". A required item, ingredient tag or machine mod is unavailable.")
        if _reviewed_acquisition(item):
            return set()  # An optional scripted craft does not gate a real drop.
    return _ordinary_required(item, scan)


def _component_values(stack):
    """Read literal component assignments as data; never run script text."""
    text = str(stack.get("components") or "")
    if not (text.startswith("[") and text.endswith("]")):
        return {}
    import autoquestgen as A
    parts, start, depth, quote, escaped = [], 1, 0, "", False
    for pos, char in enumerate(text[1:-1], 1):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:pos])
            start = pos + 1
    parts.append(text[start:-1])
    values = {}
    for part in parts:
        match = re.fullmatch(r"\s*([a-z0-9_:.-]+)\s*=\s*([\s\S]+)", part)
        if match:
            try:
                values[match[1]] = A.snbt_loads("{value:" + match[2] + "}")["value"]
            except (ValueError, TypeError, KeyError):
                continue
    return values


def _component_details(stack, scan):
    values = _component_values(stack)
    details = []
    for key in ("custom_name", "minecraft:custom_name"):
        value = values.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if isinstance(value, dict):
            value = str(value.get("text", "")) + "".join(str(p.get("text", ""))
                    for p in value.get("extra", []) if isinstance(p, dict))
        if isinstance(value, str) and value.strip():
            details.append("Name: " + value.strip())
    for key, value in values.items():
        short = key.split(":")[-1]
        if short in ("enchantments", "stored_enchantments") and isinstance(value, dict):
            for enchantment, level in (value.get("levels") or {}).items():
                roman = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 10: "X"}.get(level, str(level))
                details.append("Enchantment: " + _label(enchantment, scan) + " " + roman)
        elif short in ("data_model", "entitytype", "bee_type") and isinstance(value, str):
            prefix = {"data_model": "Creature model", "entitytype": "Captured creature", "bee_type": "Bee type"}[short]
            details.append(prefix + ": " + _label(value, scan))
        elif key == "hostilenetworks:data" and isinstance(value, (int, float)):
            details.append("Model data: " + format(value, ","))
        elif short == "entity_data" and isinstance(value, dict):
            entity = value.get("type") or value.get("id")
            if isinstance(entity, str):
                details.append("Spawned creature: " + _label(entity, scan))
        elif short == "fluid_content" and isinstance(value, dict):
            if isinstance(value.get("id"), str) and isinstance(value.get("amount"), (int, float)):
                details.append("Fluid: " + format(value["amount"], ",") + " mB of " + _label(value["id"], scan))
        elif short == "potion_contents" and isinstance(value, dict) and isinstance(value.get("potion"), str):
            details.append("Potion: " + _label(value["potion"], scan))
        elif short == "spell_container" and isinstance(value, dict):
            for spell in value.get("data", []):
                if isinstance(spell, dict) and isinstance(spell.get("id"), str):
                    details.append("Spell: " + _label(spell["id"], scan) + " (level " + str(spell.get("level", 1)) + ")")
        elif short.startswith("has_") and isinstance(value, dict):
            details.append("Installed upgrade: " + short[4:].replace("_", " ").title())
        elif short == "stored_energy" and isinstance(value, (int, float)):
            details.append("Stored energy: " + format(int(value) if int(value) == value else value, ","))
    return details


def _component_label(stack, scan):
    details = _component_details(stack, scan)
    label = _label(str(stack["id"]).lstrip("#"), scan)
    for detail in details:
        kind, value = detail.split(": ", 1)
        if kind == "Name":
            return value
        if kind == "Creature model":
            return value + " " + label
        if kind == "Captured creature":
            return label + " · " + value
        if kind == "Spawned creature":
            return value + (" Bee" if "bee" in stack["id"] and "bee" not in value.lower() else "") + " Spawn Egg"
        if kind == "Bee type":
            return value + " " + label
    return label + (" (prepared)" if stack.get("components") else "")


def _recipe_scope(items, scan):
    known, seen, todo = _known(scan), set(items), list(items)
    machines = set()
    while todo and len(seen) < 2000:
        item = todo.pop()
        # Only actual common prerequisites belong to the requested project.
        # Traversing every alternate/recycling recipe pulls an entire pack
        # into a simple target such as a beacon via glass-unpacking recipes.
        ingredients = _required(item, scan)
        recipe = _pack_recipe(item, scan)
        if recipe:
            machines.add(recipe["kind"].split(":")[0])
            machines.update(str(stack["id"]).lstrip("#").split(":")[0] for stack in recipe.get("fluids", []))
        for ingredient in ingredients:
            if ingredient.startswith("#"):
                # A tag means any matching material, never every mod that
                # supplies it. Only a singleton can add a required branch.
                members = set((scan.get("tags") or {}).get(ingredient[1:]) or ()) & known
                if len(members) != 1:
                    continue
                ingredient = next(iter(members))
            if _base_item(ingredient) in known and ingredient not in seen:
                seen.add(ingredient)
                todo.append(ingredient)
    return {item.split(":")[0] for item in seen} | machines


def _recipe_ingredient_choices(ingredient, scan, known):
    """Prefer ordinary materials only among this recipe's confirmed alternatives."""
    if not ingredient.startswith("#"):
        return [ingredient]
    members = set((scan.get("tags") or {}).get(ingredient[1:]) or ()) & known
    # Alphabetical order sends a basic sapling recipe to the Aether before
    # oak. Vanilla membership is evidence from the scanned tag, never inferred
    # from the tag's name. Exclusive mod tags therefore stay exclusive.
    common = {"minecraft:" + name for name in (
        "oak_sapling", "oak_log", "oak_wood", "oak_planks", "stick", "cobblestone",
        "stone", "dirt", "sand", "iron_ingot", "wheat", "wheat_seeds", "coal")}
    return sorted(members, key=lambda item: (not item.startswith("minecraft:"),
                                             item not in common, item))


def _objective_route(target, scan, *, include_recipes=False):
    """Resolve an encounter/action's authored preparation graph, even at tiny budgets.

    Only a verified objective can enter this graph. Missing guide requirements
    are reported rather than replaced with an unrelated item or achievement.
    """
    import autoquestgen as A
    known = _known(scan)
    installed = {m["mod_id"] for m in scan.get("mods", [])} | {"minecraft"}
    checks = A._available_guide_checks(scan)
    available = set(known) | checks
    for field, prefix in (("entities", "kill:"), ("dimensions", "dim:"),
                          ("structures", "structure:"), ("advancements", "advancement:")):
        for values in (scan.get(field) or {}).values():
            available.update(prefix + value for value in values)
    available.update("dim:" + value for value in ("minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"))
    disabled = A._disabled_guide_objectives(installed)
    available.difference_update(disabled)
    catalog = {}
    for mid in sorted(installed):
        for raw_ref, title, description in A._curated_chain(mid):
            for ref in raw_ref.split("|"):
                catalog[ref] = {"title": title, "description": description, "mod": mid}
    prefix = {"entity": "kill:", "milestone": "check:", "dimension": "dim:",
              "structure": "structure:", "advancement": "advancement:", "item": ""}[target["kind"]]
    root = target.get("guide_ref") or prefix + target["target_id"]
    route, visiting = {}, set()

    def visit(ref, root_node=False):
        if ref in visiting:
            raise ValueError("The guide for this goal has a circular preparation step: " + ref)
        if ref in route:
            return
        if len(route) + len(visiting) > 500:
            raise ValueError("This goal's preparation route is too large to resolve safely.")
        if not root_node and ref not in available:
            raise ValueError("This goal's preparation guide needs unavailable content: " + ref
                             + ". Check the pack version and companion mods.")
        visiting.add(ref)
        info = catalog.get(ref, {})
        if root_node:
            task = copy.deepcopy(target.get("task") or {})
            kind, identity = target["kind"], target["target_id"]
            if not task:
                task = ({"type": "kill", "entity": identity, "value": 1} if kind == "entity" else
                        {"type": "checkmark", "_guide_ref": root, "_guide_mod": target["mod"],
                         "title": "Confirm after completing this milestone"} if kind == "milestone" else
                        {"type": kind, kind: identity})
            row = {"ref": ref, "task": task, "title": target.get("title") or target["label"],
                   "description": target.get("description") or info.get("description", ""),
                   "mod": target["mod"], "icon": target.get("icon", "")}
            needs = target.get("requires", A._GUIDED_REQUIREMENTS.get(ref, []))
        else:
            if ref.startswith("kill:"):
                task = {"type": "kill", "entity": ref[5:], "value": 1}
            elif ref.startswith("dim:"):
                task = {"type": "dimension", "dimension": ref[4:]}
            elif ref.startswith("structure:"):
                task = {"type": "structure", "structure": ref[10:]}
            elif ref.startswith("advancement:"):
                task = {"type": "advancement", "advancement": ref[12:]}
            elif ref.startswith("check:"):
                task = {"type": "checkmark", "_guide_ref": ref,
                        "_guide_mod": A._GUIDE_CHECKS[ref]["mod"],
                        "title": "Confirm after completing this milestone"}
            else:
                task = {"type": "item", "item": ref, "count": 1, "consume_items": False}
            identity = next((task[key] for key in ("item", "entity", "dimension", "structure", "advancement") if key in task), ref)
            row = {"ref": ref, "task": task, "title": info.get("title") or _label(identity, scan),
                   "description": info.get("description", ""),
                   "mod": info.get("mod") or _task_mod(task),
                   "icon": (A._GUIDE_CHECKS.get(ref) or {}).get("icon", "")}
            needs = A._GUIDED_REQUIREMENTS.get(ref, [])
        parents = []
        for alternatives in needs:
            choices = [candidate.strip() for candidate in str(alternatives).split("|")]
            parent, failure = None, None
            for candidate in choices:
                if candidate not in available:
                    continue
                saved_route, saved_visiting = dict(route), set(visiting)
                try:
                    visit(candidate)
                except ValueError as exc:
                    # A renamed/alternative objective is useful only if its
                    # own preparation is viable. Leave no partial failed route.
                    route.clear()
                    route.update(saved_route)
                    visiting.clear()
                    visiting.update(saved_visiting)
                    failure = exc
                    continue
                parent = candidate
                break
            if parent is None:
                if failure:
                    raise failure
                raise ValueError("The preparation route for " + target["label"]
                                 + " needs unavailable content: " + " or ".join(choices)
                                 + ". Check the pack version and companion mods.")
            if parent not in parents:
                parents.append(parent)
        if include_recipes and task.get("type") == "item":
            item = task["item"]
            # World acquisition does not become a recycling/compression loop.
            # Exact pack-script stacks are expanded by apply_campaign below;
            # flattening them here would discard counts and component data.
            natural = (item in _VANILLA_ACQUISITION or (scan.get("drops") or {}).get(item)
                       or (scan.get("vanilla_sources") or {}).get(item) or _reviewed_acquisition(item))
            recipes = [] if natural or _pack_recipe(item, scan) else list((scan.get("craft_recipes") or {}).get(item) or ())
            if not recipes and not natural and not _pack_recipe(item, scan):
                fallback = sorted((scan.get("edges") or {}).get(item, ()) or _VANILLA_GOALS.get(item, ()))
                if fallback:
                    recipes = [("known recipe", fallback)]
            if recipes:
                successful, failure = False, None
                for _kind, ingredients in recipes:
                    saved_route, saved_visiting = dict(route), set(visiting)
                    recipe_parents = []
                    try:
                        for ingredient in sorted(ingredients):
                            if ingredient.startswith("#") and ingredient[1:] not in (scan.get("tags") or {}):
                                # Some scans only contain the mod's additions
                                # to vanilla/nested tags. Preserve the literal
                                # recipe requirement without guessing a member.
                                tag_ref = "check:recipe_tag:" + ingredient[1:]
                                if tag_ref not in route:
                                    route[tag_ref] = {"ref": tag_ref, "mod": item.split(":")[0],
                                        "title": "Prepare " + ingredient[1:].split(":")[-1].replace("_", " "),
                                        "task": {"type": "checkmark", "_guide_ref": tag_ref,
                                                 "title": "Confirm an accepted recipe ingredient"},
                                        "description": "Prepare a material accepted by " + ingredient + ". Check the installed recipe viewer for its allowed materials and required quantity, then confirm this checkpoint. The scan did not resolve this tag's members, so this step uses manual confirmation.",
                                        "parents": [], "icon": item}
                                recipe_parents.append(tag_ref)
                                continue
                            choices = _recipe_ingredient_choices(ingredient, scan, known)
                            chosen = None
                            for candidate in choices:
                                if candidate not in available or candidate == item:
                                    continue
                                before, before_visiting = dict(route), set(visiting)
                                try:
                                    visit(candidate)
                                except ValueError as exc:
                                    route.clear(); route.update(before)
                                    visiting.clear(); visiting.update(before_visiting)
                                    failure = exc
                                    continue
                                chosen = candidate
                                break
                            if chosen is None:
                                raise ValueError("The preparation recipe for " + _label(item, scan)
                                                 + " needs an available non-circular ingredient: " + ingredient)
                            if chosen not in recipe_parents:
                                recipe_parents.append(chosen)
                    except ValueError as exc:
                        route.clear(); route.update(saved_route)
                        visiting.clear(); visiting.update(saved_visiting)
                        failure = exc
                        continue
                    parents = list(dict.fromkeys(parents + recipe_parents))
                    successful = True
                    break
                if not successful:
                    raise failure
        row["parents"] = parents
        route[ref] = row
        visiting.remove(ref)

    visit(root, True)
    return root, route


def resolve_goal(scan, selected_ids, text):
    """Explain exactly which installed content a freeform offline goal selects.

    Supported examples: automate a factory; master magic; reach space; defeat
    bosses; farming and cooking; build storage; explore dimensions; complete
    Botania; craft create:precision_mechanism; finish every major mod.
    """
    raw = str(text or "").strip()
    normalized = _norm(raw)
    mods, selected = _selected(scan, selected_ids)
    known = _known(scan)
    base = {"text": raw, "exact_goal_ids": [], "themes": [], "domains": []}
    explicit = list(dict.fromkeys(re.findall(r"\b[a-z][a-z0-9_.-]*:[a-z0-9_./-]+", raw.lower())))
    from quest_goal_language import match_goal
    target = match_goal(scan, selected_ids, raw) if len(explicit) <= 1 else None
    if target and target["kind"] != "item":
        root, route = _objective_route(target, scan)
        scope = {row["mod"] for row in route.values()} | {target["mod"]}
        scope.discard("")
        unselected = (scope - {"minecraft"}) - selected
        if unselected:
            raise ValueError("The goal's preparation uses unselected mods. Select these as well: " + ", ".join(sorted(unselected)))
        manual = target["kind"] == "milestone"
        detection = ("This is a manual checkpoint: confirm it after doing the described action."
                     if manual else "The final quest checks the requested " +
                     {"entity": "creature defeat", "dimension": "dimension visit", "structure": "structure visit", "advancement": "advancement"}[target["kind"]] + ".")
        explanation = "Understood as: " + target["label"] + ". " + detection
        if len(route) > 1:
            explanation += " Its guide contributes " + str(len(route) - 1) + " preparation milestones."
        if target.get("match_note"):
            explanation += " " + target["match_note"]
        return dict(base, kind="objective", goal_target=target, goal_route=route,
                    goal_root=root, focus_mods=sorted(scope), title="The " + target["label"] + " Project",
                    finale=target["label"], explanation=explanation)
    if target:
        explicit = [target["target_id"]]
    if not explicit and normalized:
        wanted = " ".join(word for word in normalized.split() if word not in _GENERIC_WORDS)
        matches = [item for item in sorted(known)
                   if normalized == _norm(_label(item, scan)) or wanted == _norm(_label(item, scan))
                   or wanted == _norm(item.split(":")[-1])]
        if len(matches) > 1:
            raise ValueError("That item name matches several installed items. Use its exact item ID: " + ", ".join(matches[:5]))
        explicit = matches
    if explicit:
        missing = [item for item in explicit if item not in known]
        if missing:
            raise ValueError("This pack has no verified obtainable item named " + ", ".join(missing) + ". Choose an installed item or a goal such as 'master magic'.")
        owners = {item.split(":")[0] for item in explicit} - {"minecraft"}
        if owners - selected:
            raise ValueError("Select the goal's mod first: " + ", ".join(sorted(owners - selected)))
        for item in explicit:
            # An explicit modpack project cannot silently fall back to an old
            # JAR recipe after all of its authored routes were rejected. The
            # ordinary-route exception is for nested materials such as vanilla
            # blaze powder, whose optional unpacking script is not its only source.
            if (not item.startswith("minecraft:") and _pack_candidates(item, scan)
                    and not _pack_recipe(item, scan)):
                raise ValueError("No complete non-circular pack recipe is available for " + _label(item, scan)
                                 + ". A required item, ingredient tag or machine mod is unavailable.")
        scope = _recipe_scope(explicit, scan)
        unselected = (scope - {"minecraft"}) - selected
        if unselected:
            raise ValueError("The goal's recipes use unselected mods. Select these as well: " + ", ".join(sorted(unselected)))
        pack_routes = {item: {key: row.get(key) for key in ("source", "line", "kind", "counts")}
                       for item in explicit if (row := _pack_recipe(item, scan))}
        recipe_note = (" The campaign uses the first available concrete pack-script route for "
                       + ", ".join(_label(i, scan) for i in pack_routes)
                       + ", with its exact quantities and special-item preparation steps." if pack_routes else "")
        return dict(base, kind="item", exact_goal_ids=explicit, pack_recipe_goals=pack_routes,
                    focus_mods=sorted(scope),
                    title="The Final Project", finale=" · ".join(_label(i, scan) for i in explicit),
                    explanation="Goal: " + ", ".join(_label(i, scan) for i in explicit) + ". The campaign follows the installed item's recipe branches and keeps the real item as the final objective." + recipe_note)
    # An ATM Star is an actual pack-specific item, not something this program
    # can register in Minecraft. Do not quietly turn that request into a checkbox.
    if _phrase(normalized, "atm star") or _phrase(normalized, "all the mods star"):
        raise ValueError("An ATM Star is not available among this pack's verified items. Choose an installed endgame item, or use 'an ATM-style endgame' for a campaign built from this pack's achievements.")
    if not raw:
        return dict(base, kind="all", focus_mods=sorted(selected), title="The Final Expedition",
                    finale="The Pack Comes Together",
                    explanation="Connect the selected mods through their meaningful achievements, then bring the campaign's final collection together.")
    named = [mid for mid, mod in mods.items() if mid in selected and
             _named_mod(normalized, mid, mod.get("name", mid))]
    domains = [domain for domain, profile in _DOMAINS.items()
               if any(_phrase(normalized, word) for word in profile["words"])]
    if not named and not domains and any(_phrase(normalized, word) for word in _ALL_WORDS):
        return dict(base, kind="all", focus_mods=sorted(selected), title="The Final Expedition",
                    finale="The Pack Comes Together",
                    explanation="Connect the selected mods through their meaningful achievements, then bring the campaign's final collection together.")
    focus = set(named)
    for domain in domains:
        profile = _DOMAINS[domain]
        focus.update(mid for mid, mod in mods.items() if mid in selected and
                     (mid in profile["mods"] or mod.get("category") in profile["categories"]))
    if not focus:
        if domains:
            raise ValueError("No selected mods provide the requested " + " / ".join(domains) + " content. Select a matching installed mod or choose another goal.")
        raise ValueError("I could not match that goal to installed content. Try 'master magic', 'automate a factory', 'defeat bosses', 'farming and cooking', 'build storage', a mod name, or an exact item ID.")
    profile = _DOMAINS[domains[0]] if domains else {}
    names = [mods[mid].get("name", mid) for mid in sorted(focus)]
    return dict(base, kind="branches", focus_mods=sorted(focus), domains=domains,
                themes=list(dict.fromkeys(theme for domain in domains for theme in _DOMAINS[domain]["themes"])),
                title=profile.get("title", "The " + names[0] + " Project"),
                finale=profile.get("finale", "Mastery of " + " and ".join(names[:2])),
                explanation="Goal matched to " + ", ".join(names[:8]) + (" and other selected branches" if len(names) > 8 else "") + ". " + profile.get("purpose", "Follow this mod's meaningful milestones to a final achievement."))


def _id(*parts):
    return "campaign_" + hashlib.sha1("/".join(str(p) for p in parts).encode()).hexdigest()[:14]


def _task_mod(task):
    return str(task.get("item") or task.get("entity") or task.get("dimension") or task.get("structure") or task.get("advancement") or task.get("_guide_mod") or "").split(":")[0]


def _strip(text):
    return re.sub(r"[&§][0-9a-fk-or]", "", str(text), flags=re.I)


def _list_names(names, limit=6):
    names = list(dict.fromkeys(str(name) for name in names))
    if len(names) > limit:
        return ", ".join(names[:limit]) + " and %d more" % (len(names) - limit)
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0] if names else "the connected branches"


def _acquisition(item, scan):
    ingredients = sorted(i for i in _required(item, scan) if i in _known(scan))
    if ingredients:
        names = [_label(i, scan) for i in ingredients[:6]]
        return "Prepare " + ", ".join(names) + (" and the remaining recipe ingredients" if len(ingredients) > 6 else "") + ". Check the installed recipe for quantities and the crafting method."
    if item == "minecraft:nether_star":
        return "The Wither drops a Nether Star. Prepare your equipment and a safe fighting area before summoning it."
    return "Obtain " + _label(item, scan) + " through its recipe or acquisition method in this pack."


def _dependency_graph(quests):
    """Resolve native task dependencies to their owner only for graph analysis."""
    ids = {q["id"] for q in quests}
    if len(ids) != len(quests):
        raise ValueError("Campaign cannot use a book with duplicate quest IDs.")
    owners = {key: key for key in ids}
    for quest in quests:
        for task in quest.get("tasks", []):
            key = task.get("id")
            if not key:
                continue
            if key in owners:
                raise ValueError("Campaign cannot use a book with duplicate quest/task IDs: " + str(key))
            owners[key] = quest["id"]
    graph = {}
    for quest in quests:
        parents = []
        for raw in quest.get("dependencies", []):
            if raw not in owners:
                raise ValueError("Campaign references a missing prerequisite: " + str(raw))
            parents.append(owners[raw])
        graph[quest["id"]] = parents
    return graph


def _assert_dag(chapters):
    quests = [q for chapter in chapters for q in chapter.get("quests", [])]
    graph = _dependency_graph(quests)
    done, visiting = set(), set()
    def visit(key):
        if key in visiting:
            raise ValueError("Campaign would create a circular prerequisite at " + str(key))
        if key in done:
            return
        visiting.add(key)
        for parent in graph[key]:
            if parent not in graph:
                raise ValueError("Campaign references a missing prerequisite: " + str(parent))
            visit(parent)
        visiting.remove(key)
        done.add(key)
    for key in graph:
        visit(key)


def _campaign_milestones(records, scan, focus, domains=()):
    """Choose existing major achievements, never filler registry entries."""
    # Eligibility is semantic, not a consequence of how far down a generated
    # graph an item happens to sit. In particular, substring matches such as
    # "ring" in "spring_green_dye" must never turn decoration into a finale.
    equipment = re.compile(r"(?:^|_)(?:sword|blade|staff|wand|spell_book|spellbook|grimoire|rocket|reactor|controller|assembler|crafter|jetpack|armor|bodyarmor|chestplate|storage|drive|terminal|network|trophy|relic|pylon|portal|tiara|ring|amulet|backpack|feast)(?:_|$)")
    parts = re.compile(r"(?:^|_)(?:raw|unprocessed|unfinished|incomplete|ingot|nugget|dust|ore|log|plank|petal|seed|leaf|brick|slab|stairs|wall|fence|dye|seat|chair|bench|bridge|pier|railing|shingle|window|door|trapdoor|roof|tile|carpet|paint|banner|facade|cover|frame|casing|processor|circuit|component|core|plate|wire|cable|coil|gear|module|upgrade|pattern|part|tank)s?(?:_|$)")
    starter = re.compile(r"(?:^|_)(?:novice|apprentice|basic|starter|wooden|stone)(?:_|$)")
    capstones = dict(_CAPSTONES, **{
        "rftoolsutility:crafter3": 80,
        "rftoolsutility:advanced_charged_porter": 75,
    })
    intermediate = {"rftoolsutility:crafter1", "rftoolsutility:crafter2",
                    "sophisticatedbackpacks:backpack", "angelring:diamond_ring"}
    # These authored checks describe demonstrated, functioning projects.
    # Arbitrary guide introductions, reading tips and first-use checks do not
    # become endgame achievements merely because they have prerequisites.
    guide_capstones = {
        "check:botanypots:automatic_harvest",
        "check:cookingforblockheads:connected_meal",
        "check:extendedcrafting:final_workshop_project",
        "check:farmersdelight:renewable_kitchen",
        "check:forbidden_arcanus:forge_tier_three",
        "check:functionalstorage:linked_workshop",
        "check:hostilenetworks:automated_line",
        "check:hostilenetworks:self_aware",
        "check:hostilenetworks:research_network",
        "check:immersiveengineering:diesel_generator",
        "check:immersiveengineering:arc_furnace",
        "check:immersiveengineering:excavator",
        "check:minecolonies:self_sufficient_colony",
        "check:mna:complete_faction_artifice",
        "check:mysticalagradditions:tier_six_harvest",
        "check:productivebees:automated_apiary",
        "check:projecte:alchemical_workshop",
        "check:sophisticatedbackpacks:expedition_loadout",
        "check:sophisticatedstorage:controller_network",
        "check:storagedrawers:working_drawer_network",
        "check:storagedrawers:bulk_farm_output",
    }
    import autoquestgen as A
    authored_encounters = {ref for mid in focus for raw, _title, _desc in A._curated_chain(mid)
                           for ref in raw.split("|") if ref.startswith(("kill:", "structure:", "advancement:"))}
    major_encounter = re.compile(r"(?:^|_)(?:boss|doppleganger|guardian|lich|hydra|minoshroom|naga|ur_ghast|snow_queen|ignis|leviathan|harbinger|ancient_remnant|ender_golem|netherite_monstrosity|ender_dragon|wither|valkyrie_queen|sun_spirit|slider|summoner|alchemist|starlit_crusher|arachnarch|dragon)(?:_|$)")
    major_structure = re.compile(r"(?:^|[_/])(?:fortress|dungeon|temple|pyramid|citadel|stronghold|castle|mansion|palace|sanctum|labyrinth|tower|city|acropolis)(?:[_/]|$)")
    minor_structure = re.compile(r"(?:^|[_/])(?:small|tiny|mini|house|hut|igloo|geode|meteorite|island|village)(?:[_/]|$)")
    categories = {m["mod_id"]: m.get("category") for m in scan.get("mods", [])}
    known = _known(scan)
    graph = _dependency_graph([q for q, _c in records])
    child_ids = {d for parents in graph.values() for d in parents}
    depths = {}
    def depth(key):
        if key not in depths:
            depths[key] = 1 + max((depth(p) for p in graph.get(key, [])), default=-1)
        return depths[key]
    candidates = defaultdict(list)
    for q, chapter in records:
        if q.get("optional") or chapter.get("_campaign") or chapter.get("_campaign_summary"):
            continue
        for task in q.get("tasks", []):
            mid = _task_mod(task)
            if mid not in focus or mid == "minecraft":
                continue
            item = task.get("item")
            typ = task.get("type", "item")
            if typ == "item":
                if not isinstance(item, str) or item not in known:
                    continue
                leaf = item.split(":")[-1]
                if item not in capstones:
                    if (item in intermediate or categories.get(mid) == "decor" or mid in {"additional_lights", "crystalix"}
                            or parts.search(leaf) or starter.search(leaf) or not equipment.search(leaf)):
                        continue
                tier = (scan.get("tier") or {}).get(item, 0)
                score = capstones.get(item, 0) + 15 * bool(equipment.search(leaf)) + min(12, tier)
            elif typ in ("kill", "dimension", "structure", "advancement"):
                target = task.get({"kill": "entity"}.get(typ, typ), "")
                ref = typ + ":" + str(target)
                if typ in ("kill", "advancement") and ref not in authored_encounters:
                    continue
                if typ == "kill" and not major_encounter.search(str(target).split(":")[-1]):
                    continue
                if typ == "structure":
                    leaf = str(target).split(":")[-1]
                    if minor_structure.search(leaf) or (ref not in authored_encounters and not major_structure.search(leaf)):
                        continue
                score = (35 if "adventure" in domains else 12) if typ == "kill" else 8
            elif typ == "checkmark" and task.get("_guide_ref") in guide_capstones:
                score = 70
            else:
                continue
            score += min(12, depth(q["id"])) + (8 if q["id"] not in child_ids else 0)
            candidates[mid].append((score, q["id"], q, chapter, task))
    milestones = [max(rows, key=lambda row: (row[0], row[1])) for rows in candidates.values()]
    return sorted(milestones, key=lambda row: (-row[0], row[1]))


def _connect_selected_campaign(doc, scan, selected_ids):
    """Join pack achievements after the real goal, without adding boss gates."""
    plan = doc["campaign"]
    _mods, selected = _selected(scan, selected_ids)
    if plan["kind"] == "objective":
        goal_mods = {plan["goal_target"]["mod"]}
    elif plan["kind"] == "item":
        goal_mods = {item.split(":")[0] for item in plan["exact_goal_ids"]}
    else:
        goal_mods = set(plan["focus_mods"])
    records = [(q, c) for c in doc["chapters"] for q in c.get("quests", [])]
    milestones = _campaign_milestones(records, scan, selected - goal_mods, plan.get("domains", ()))
    plan["selected_mods"] = sorted(selected)
    plan["branch_milestone_ids"] = [row[1] for row in milestones]
    if not milestones:
        return
    known = _known(scan)
    mods = {m["mod_id"]: m.get("name", m["mod_id"]) for m in scan.get("mods", [])}
    names = {row[1]: mods.get(_task_mod(row[4]), _task_mod(row[4])) + ": " + _strip(row[2].get("title", "Milestone")) for row in milestones}
    icons = {row[1]: row[4].get("item") or row[2].get("icon") for row in milestones}
    goal_ids = list(plan["goal_quest_ids"])
    graph = _dependency_graph([q for q, _c in records])
    preceding_goals = set()
    for key in goal_ids:
        todo, seen = list(graph[key]), set()
        while todo:
            parent = todo.pop()
            if parent not in seen:
                seen.add(parent)
                todo.extend(graph[parent])
        preceding_goals.update(seen.intersection(goal_ids))
    goal_ids = [key for key in goal_ids if key not in preceding_goals]
    goal_label = plan.get("goal_target", {}).get("label") or plan["finale"]
    goal_icon = next((c.get("icon") for c in doc["chapters"] if c.get("_campaign") and c.get("icon") in known), None)
    checks = []
    pending = [row[1] for row in milestones]
    open_mode = plan["progression"] == "open"
    if len(goal_ids) > 5:
        # Open progression has no edges from which to infer the terminal
        # requested goal. A many-item request still needs bounded grouping.
        by_id = {q["id"]: q for q, _c in records}
        for key in goal_ids:
            names[key] = _strip(by_id[key].get("title", goal_label))
            icons[key] = by_id[key].get("icon") or goal_icon
        pending = goal_ids + pending
        goal_ids = []
    # Direct branch milestones feed small joins; no extra collection tasks
    # repeat already completed item objectives or imply fictional recipes.
    level = 0
    while len(pending) + len(goal_ids) > 6:
        next_level = []
        for index in range(0, len(pending), 6):
            batch = pending[index:index + 6]
            key = _id("selected_achievements", level, *batch)
            title = "Paths of the Journey " + str(len(checks) + 1)
            icon = next((icons[q] for q in batch if icons.get(q) in known), goal_icon)
            checks.append({"id": key, "title": title,
                           "description": ["&7Complete " + _list_names(names[q] for q in batch) + ", then confirm these campaign achievements."],
                           "tasks": [{"type": "checkmark", "title": "Confirm the connected achievements"}],
                           "dependencies": [] if open_mode else batch, "rewards": [{"type": "xp", "xp": 100}],
                           "optional": False, "size": 1.3, "shape": "hexagon", **({"icon": icon} if icon else {})})
            names[key], icons[key] = title, icon
            next_level.append(key)
        pending = next_level
        level += 1
    finale = {"id": _id("selected_campaign_final", plan["text"], *goal_ids),
              "title": "A Journey Complete",
              "description": ["&7Achieve " + goal_label + " and complete " + _list_names(names[q] for q in pending) + ".",
                              "&7These are the achievements of your modpack journey. Your goal's own chapter shows its required preparation. Return here to confirm the whole campaign is complete."],
              "tasks": [{"type": "checkmark", "title": "Confirm the campaign achievements"}],
              "dependencies": [] if open_mode else list(dict.fromkeys(goal_ids + pending)),
              "rewards": [{"type": "xp_levels", "xp_levels": 10}], "optional": False, "size": 2.0, "shape": "octagon",
              **({"icon": goal_icon} if goal_icon else {})}
    checks.append(finale)
    for i, q in enumerate(checks):
        q["x"], q["y"] = (i % 6) * 3, (i // 6) * 3
    doc["chapters"].append({"id": "campaign_selected_achievements", "title": "Your Modpack Journey",
                            "group": "Main Goal", "description": ["Bring your goal and the achievements across your selected modpack together."],
                            "quests": checks, "_campaign_summary": True, "_campaign_role": "journey",
                            **({"icon": goal_icon} if goal_icon else {})})
    plan["finale_quest_ids"] = [finale["id"]]
    plan["contributing_mods"] = sorted(set(plan.get("contributing_mods", ())) | {_task_mod(row[4]) for row in milestones})
    plan["explanation"] += " The selected modpack's chapters are retained, with a separate checkpoint for the goal and major branch achievements."


def apply_campaign(doc, scan, selected_ids, goal_text="", seed=0, progression="linear", scope="goal"):
    """Return a new document with a truthful final project and connected routes.

    Open progression keeps the same goals without adding prerequisite locks.
    Existing IDs are preserved; generated objectives have stable item-based IDs.
    """
    if scope not in ("goal", "selected"):
        raise ValueError("Campaign scope must be 'selected' or 'goal'.")
    plan = resolve_goal(scan, selected_ids, goal_text)
    if scope == "selected":
        plan["explanation"] = plan["explanation"].replace("The final quest checks", "The goal quest checks")
    result = copy.deepcopy(doc)
    if result.get("campaign"):
        if result["campaign"].get("text") == plan["text"] and result["campaign"].get("progression") == progression \
                and result["campaign"].get("scope", "goal") == scope:
            return result
        raise ValueError("Build a fresh quest book to change an existing campaign goal.")
    chapters = result["chapters"]
    _assert_dag(chapters)
    known, focus = _known(scan), set(plan["focus_mods"])
    records = [(q, chapter) for chapter in chapters for q in chapter.get("quests", [])]
    by_item = defaultdict(list)
    for q, chapter in records:
        for task in q.get("tasks", []):
            if task.get("item") in known:
                by_item[task["item"]].append((q, chapter))
    campaign = {"id": "campaign_final_project",
                "title": plan["finale"] if plan["kind"] == "item" else plan["title"], "group": "Main Goal",
                "icon": "minecraft:nether_star" if "minecraft:nether_star" in known else "minecraft:book",
                "description": [plan["explanation"]], "quests": [], "_campaign": True, "_campaign_role": "goal"}
    open_mode = progression == "open"
    campaign_quests = campaign["quests"]
    supporting = {}
    original_graph = _dependency_graph([q for q, _c in records])
    def depends_on(key, target, seen=None):
        if key == target:
            return True
        seen = set(seen or ())
        if key in seen:
            return False
        seen.add(key)
        return any(depends_on(parent, target, seen) for parent in original_graph.get(key, []))

    def existing(item):
        matches = by_item.get(item, [])
        if not matches:
            return None
        return min(matches, key=lambda pair: (len(pair[0].get("tasks", [])), bool(pair[0].get("optional"))))

    def prerequisite_quests(item, seen=None, depth=0, target_id=None):
        seen = set(seen or ()) | {item}
        found = set()
        if depth >= 5:
            return found
        for ingredient in sorted(_required(item, scan)):
            if ingredient in seen or ingredient not in known:
                continue
            match = existing(ingredient)
            if match:
                if not target_id or not depends_on(match[0]["id"], target_id):
                    found.add(match[0]["id"])
            elif len(supporting) < 12 and ingredient.split(":")[0] in focus:
                if ingredient not in supporting:
                    quest = {"id": _id("ingredient", ingredient), "title": "Prepare " + _label(ingredient, scan),
                             "description": ["&7Prepare this ingredient for " + _label(item, scan) + ". " + _acquisition(ingredient, scan)],
                             "tasks": [{"type": "item", "item": ingredient, "count": 1}],
                             "rewards": [{"type": "xp", "xp": 50}], "x": 0, "y": 0}
                    supporting[ingredient] = quest
                    quest["dependencies"] = sorted(prerequisite_quests(ingredient, seen, depth + 1, target_id)) if not open_mode else []
                    campaign_quests.append(quest)
                found.add(supporting[ingredient]["id"])
            else:
                found.update(prerequisite_quests(ingredient, seen, depth + 1, target_id))
        return found

    pack_support = {}
    def pack_material(stack, owner, final_id, trail=()):
        """An exact-count preparation spoke; special data is always manual."""
        identity = _stack_identity(stack)
        count = int(stack["count"])
        key = (final_id, identity, count)
        if key in pack_support:
            return pack_support[key]["id"]
        if identity in trail:
            return None
        name = _component_label(stack, scan)
        machine_name = owner["kind"].split(":", 1)[-1].replace("_", " ").title()
        manual = bool(stack.get("components")) or stack["id"].startswith("#")
        text = "Prepare %d × %s for the %s recipe." % (count, name, machine_name)
        if manual:
            text += (" Confirm this checkpoint only after preparing the full quantity with the enchantments, contents, and settings shown in the recipe."
                     " Ordinary versions do not satisfy this preparation step." if stack.get("components") else
                     " Choose a material accepted by the recipe's ingredient tag, then confirm once the full quantity is ready.")
        else:
            text += " This task detects the items in your inventory without consuming them."
        quest = {"id": _id("pack_material", *key), "title": "%d × %s" % (count, name),
                 "description": ["&7" + text], "tasks": [{"type": "checkmark", "title": "Confirm the exact preparation"}]
                 if manual else [{"type": "item", "item": stack["id"], "count": count, "consume_items": False}],
                 "dependencies": [], "rewards": [{"type": "xp", "xp": 50}],
                 "shape": "hexagon", "size": 1.2, "optional": False,
                 "_pack_identity": identity, "_pack_count": count,
                 "_pack_components": str(stack.get("components") or ""),
                 "_pack_source": owner.get("source", ""), "_pack_line": owner.get("line", 0)}
        if stack["id"] in known:
            quest["icon"] = stack["id"]
        if stack.get("components"):
            details = _component_details(stack, scan)
            quest["description"].extend("&7" + detail for detail in details)
            if not details:
                quest["description"].append("&7Match this specially prepared version in the recipe viewer before confirming.")
        pack_support[key] = quest
        campaign_quests.append(quest)
        parents = set()
        match = existing(stack["id"]) if not manual else None
        if match and not depends_on(match[0]["id"], final_id):
            parents.add(match[0]["id"])
        nested = _pack_recipe(identity, scan, trail)
        if nested:
            batches = math.ceil(count / int(nested["output"].get("count", 1)))
            parents.update(pack_inputs(nested, batches, final_id, set(trail) | {identity}))
        elif not manual:
            # Retain useful existing chapter milestones without manufacturing
            # an arbitrary chain or imposing all alternate recipes.
            for ingredient in sorted(_ordinary_required(stack["id"], scan)):
                match = existing(ingredient)
                if match and not depends_on(match[0]["id"], final_id):
                    parents.add(match[0]["id"])
        quest["dependencies"] = [] if open_mode else sorted(parents)
        return quest["id"]

    def pack_inputs(recipe, batches, final_id, trail):
        # Merge duplicate identical stacks, but never merge different components.
        stacks = {}
        for source in recipe["ingredients"]:
            identity = _stack_identity(source)
            if identity not in stacks:
                stacks[identity] = dict(source, count=0)
            stacks[identity]["count"] += int(source["count"]) * batches
        parents = set()
        for stack in stacks.values():
            key = pack_material(stack, recipe, final_id, trail)
            if key:
                parents.add(key)
        for fluid in recipe.get("fluids", []):
            count = int(fluid["count"]) * batches
            key = _id("pack_fluid", final_id, recipe.get("key"), _stack_identity(fluid), count)
            if not any(q["id"] == key for q in campaign_quests):
                campaign_quests.append({"id": key, "title": "Prepare %s fluid" % _label(fluid["id"].lstrip("#"), scan),
                    "description": ["&7Prepare %d mB of %s for the %s recipe, then confirm this checkpoint."
                                    % (count, fluid["raw"], recipe["kind"].split(":")[-1].replace("_", " "))],
                    "tasks": [{"type": "checkmark"}], "dependencies": [], "rewards": [],
                    "_pack_identity": _stack_identity(fluid), "_pack_count": count,
                    "shape": "hexagon", "size": 1.1})
            parents.add(key)
        return parents

    if plan["kind"] == "objective":
        target = plan["goal_target"]
        root, route = plan["goal_root"], plan["goal_route"]
        campaign["title"] = target["label"]
        campaign["description"] = ["Follow the preparation branches to " + target["label"] + ".",
                                   plan["explanation"]]
        refs = {}

        def task_identity(task):
            typ = task.get("type", "item")
            key = {"kill": "entity", "checkmark": "_guide_ref"}.get(typ, typ)
            value = task.get(key)
            return typ, value if isinstance(value, str) else None

        for ref, row in route.items():
            identity = task_identity(row["task"])
            matches = [(q, chapter) for q, chapter in records
                       if len(q.get("tasks", [])) == 1 and task_identity(q["tasks"][0]) == identity]
            match = min(matches, key=lambda pair: (bool(pair[0].get("optional")), pair[0]["id"])) if matches else None
            if match:
                quest, old_chapter = match
                old_chapter["quests"].remove(quest)
            else:
                quest = {"id": _id("objective", ref), "rewards": [{"type": "xp", "xp": 50}]}
            old_task_id = quest.get("tasks", [{}])[0].get("id")
            quest["tasks"] = [copy.deepcopy(row["task"])]
            if old_task_id:
                quest["tasks"][0]["id"] = old_task_id
            quest["title"] = row["title"]
            description = row.get("description")
            if isinstance(description, list):
                lines = [str(line) for line in description if line]
            else:
                lines = ["&7" + description] if description else []
            if ref == root:
                quest["title"] = target["label"]
                if target["kind"] == "milestone":
                    lines.append("&7Complete the described setup and test that it works, then confirm this manual checkpoint. Owning its parts alone does not complete this goal.")
                elif target["kind"] == "entity":
                    lines.append("&7Defeat " + target["label"] + " to complete this " + ("goal" if scope == "selected" else "campaign") + ". This quest checks the actual creature defeat.")
                elif target["kind"] == "advancement":
                    lines.append("&7Earn the installed advancement for this achievement. This quest checks that advancement rather than the contents of your inventory.")
                else:
                    lines.append("&7Visit " + target["label"] + (" to complete this goal." if scope == "selected" else " to finish the journey.") + " The quest checks the location you reach.")
            if not lines:
                task = row["task"]
                if task.get("item"):
                    lines = ["&7" + _acquisition(task["item"], scan)]
                else:
                    lines = ["&7Complete " + row["title"] + " as preparation for " + target["label"] + "."]
            quest["description"] = lines
            # Authored prerequisites replace incidental ordering in a sampled
            # chapter. Every preparation node is included before links are set.
            quest.pop("dependency_requirement", None)
            quest["dependencies"] = []
            quest["optional"] = False
            quest["x"], quest["y"] = (len(refs) % 6) * 3, (len(refs) // 6) * 3
            quest["size"] = 1.9 if ref == root else 1.0
            quest["shape"] = "octagon" if ref == root else "hexagon"
            if row.get("icon") in known:
                quest["icon"] = row["icon"]
            if ref == root:
                quest["rewards"] = [{"type": "xp_levels", "xp_levels": 5}]
            refs[ref] = quest
            campaign_quests.append(quest)
        if not open_mode:
            for ref, row in route.items():
                refs[ref]["dependencies"] = [refs[parent]["id"] for parent in row["parents"]]
        final = refs[root]
        icon = target.get("icon")
        if icon not in known:
            icon = next((row["task"].get("item") for row in reversed(list(route.values()))
                         if row["task"].get("item") in known), None)
        if icon:
            campaign["icon"] = icon
            final["icon"] = icon
        plan["contributing_mods"] = sorted({row["mod"] for row in route.values()} - {"minecraft"})
        # A concrete encounter/action goal ends at that achievement. Unrelated
        # post-goal relics or random sampled items do not belong in its route.
        if scope == "goal":
            chapters = []
    elif plan["kind"] == "item":
        campaign["description"] = ["Finish the route to " + _list_names(_label(item, scan) for item in plan["exact_goal_ids"]) + ". Prepare the ingredients along the way, then obtain your final item."]
        finals = []
        for item in plan["exact_goal_ids"]:
            match = existing(item)
            # A single-objective existing quest keeps its identity when moved.
            # An existing bundle is left intact and receives a specific goal
            # quest instead of imposing unrelated bundle requirements.
            if match and len(match[0].get("tasks", [])) == 1:
                quest, old_chapter = match
                old_chapter["quests"].remove(quest)
            else:
                quest = {"id": _id("goal", item), "title": _label(item, scan),
                         "tasks": [{"type": "item", "item": item, "count": 1}],
                         "description": ["&7" + ("Obtain this item to complete your chosen goal. " if scope == "selected" else "This is the final item for your campaign. ") + _acquisition(item, scan)],
                         "rewards": [{"type": "xp_levels", "xp_levels": 5}]}
            pack_recipe = _pack_recipe(item, scan)
            parents = set(quest.get("dependencies", []))
            if pack_recipe:
                # This is a selected project route, so a JAR recycling route or
                # earlier OR gate must not replace its complete material spokes.
                parents = pack_inputs(pack_recipe, 1, quest["id"], {item})
                quest.pop("dependency_requirement", None)
                old_task_id = quest["tasks"][0].get("id")
                quest["tasks"] = [{"type": "item", "item": item, "count": 1, "consume_items": False}]
                if old_task_id:
                    quest["tasks"][0]["id"] = old_task_id
                machine = pack_recipe["kind"].split(":", 1)[-1].replace("_", " ").title()
                quest["description"] = ["&7Bring the prepared materials together at the %s and complete its %s recipe."
                                        % (machine, _label(item, scan)),
                                        "&7The connected preparation quests show every direct material and its required quantity."
                                        " Specially prepared items have manual checkpoints; match their enchantments, contents, and settings to the recipe.",
                                        "&7Collect the finished %s to complete this project. The quest detects it without consuming it." % _label(item, scan)]
                quest["_pack_source"] = pack_recipe.get("source", "")
                quest["_pack_line"] = pack_recipe.get("line", 0)
            elif quest.get("dependency_requirement") != "one":
                parents.update(prerequisite_quests(item, target_id=quest["id"]))
            parents.discard(quest["id"])
            quest["dependencies"] = [] if open_mode else sorted(parents)
            quest.update({"x": 6, "y": len(finals) * 3, "size": 1.7, "shape": "octagon", "optional": False})
            if pack_recipe and len(parents) >= 8:
                quest.update({"x": 0, "y": 0, "size": 2.6, "shape": "pentagon"})
            campaign_quests.append(quest)
            finals.append(quest)
        campaign["icon"] = plan["exact_goal_ids"][0]
        if len(finals) > 1:
            campaign_quests.append({"id": _id("final", *plan["exact_goal_ids"]), "title": "The Final Project Is Complete",
                                    "description": ["&7Complete each of the requested final objectives to finish this campaign."],
                                    "tasks": [{"type": "checkmark"}], "dependencies": [] if open_mode else [q["id"] for q in finals],
                                    "rewards": [{"type": "xp_levels", "xp_levels": 10}], "x": 10, "y": 0, "size": 2.0, "shape": "octagon",
                                    "icon": campaign["icon"]})
    else:
        # Score already-generated milestones; never sample arbitrary registry
        # entries merely to increase the final collection's size.
        graph = _dependency_graph([q for q, _c in records])
        child_ids = {d for parents in graph.values() for d in parents}
        depths = {}
        def depth(key):
            if key not in depths:
                depths[key] = 1 + max((depth(p) for p in graph.get(key, [])), default=-1)
            return depths[key]
        candidates = defaultdict(list)
        for q, chapter in records:
            if q.get("optional"):
                continue
            for task in q.get("tasks", []):
                mid = _task_mod(task)
                if mid not in focus or mid == "minecraft":
                    continue
                item = task.get("item")
                typ = task.get("type", "item")
                if typ == "item":
                    if item not in known:
                        continue
                    leaf = item.split(":")[-1]
                    if _MATERIAL.search(leaf) and not _EQUIPMENT.search(leaf) and item not in _CAPSTONES:
                        continue
                    tier = (scan.get("tier") or {}).get(item, 0)
                    if not (_EQUIPMENT.search(leaf) or item in _CAPSTONES or tier >= 3 or depth(q["id"]) >= 3):
                        continue
                    score = _CAPSTONES.get(item, 0) + 15 * bool(_EQUIPMENT.search(leaf)) + min(12, tier)
                elif typ in ("kill", "dimension", "structure", "advancement"):
                    score = (35 if "adventure" in plan["domains"] else 12) if typ == "kill" else 8
                elif typ == "checkmark" and task.get("_guide_ref") and depth(q["id"]) >= 2:
                    score = 18
                else:
                    continue
                score += min(12, depth(q["id"])) + (8 if q["id"] not in child_ids else 0)
                candidates[mid].append((score, q["id"], q, chapter, task))
        milestones = [max(rows, key=lambda row: (row[0], row[1])) for rows in candidates.values()]
        milestones.sort(key=lambda row: (-row[0], row[1]))
        if not milestones:
            raise ValueError("The generated book has no suitable milestones for this campaign goal. Include a matching content mod or use an exact installed item as the goal.")
        mods = {m["mod_id"]: m.get("name", m["mod_id"]) for m in scan.get("mods", [])}
        branch_names = [mods.get(_task_mod(row[4]), _task_mod(row[4])) for row in milestones]
        campaign["description"] = ["Bring your adventures through " + _list_names(branch_names) + " together. Finish each branch's milestone, then complete the collections below."]
        collection, proxy_ids = [], []
        branch_collections = []
        for index, (_score, _key, quest, chapter, task) in enumerate(milestones):
            mid = _task_mod(task)
            name = mods.get(mid, mid)
            proxy_id = _id("branch", mid, quest["id"])
            proxy_ids.append(proxy_id)
            campaign_quests.append({"id": proxy_id, "title": name + ": " + _strip(quest.get("title", "Milestone")),
                                    "description": ["&7Finish “" + _strip(quest.get("title", "this milestone")) + "” in " + _strip(chapter.get("title", name)) + ", then return here to record this part of your journey."],
                                    "tasks": [{"type": "checkmark"}], "dependencies": [] if open_mode else [quest["id"]],
                                    "rewards": [{"type": "xp", "xp": 100}], "x": (index % 4) * 3, "y": (index // 4) * 3,
                                    "shape": "hexagon", "size": 1.2,
                                    **({"icon": task["item"]} if task.get("item") else
                                       {"icon": quest["icon"]} if quest.get("icon") in known else {})})
            if task.get("item"):
                collection.append({"type": "item", "item": task["item"], "count": 1, "consume_items": False})
            branch_collections.append((proxy_id, task.get("item"), mid))
        # Large packs keep every suitable branch. Small collections form
        # readable intermediate achievements instead of one enormous AND gate.
        final_dependencies = proxy_ids
        final_tasks = collection or [{"type": "checkmark", "title": "Confirm the campaign achievements"}]
        collection_names = []
        if len(branch_collections) > 8:
            categories = defaultdict(list)
            mod_info = {m["mod_id"]: m for m in scan.get("mods", [])}
            for row in branch_collections:
                category = mod_info.get(row[2], {}).get("category", "unknown")
                categories[category].append(row)
            names = {"magic": "Arcane Works", "tech": "Engineering Works", "food": "Harvest and Kitchen",
                     "world": "Expedition Records", "mob": "Great Hunts", "utility": "The Well-equipped Base",
                     "unknown": "Discoveries", "decor": "Great Builds"}
            final_dependencies = []
            for category, rows in sorted(categories.items()):
                for start in range(0, len(rows), 6):
                    batch = rows[start:start + 6]
                    group_id = _id("collection", category, *[row[0] for row in batch])
                    title = names.get(category, category.title())
                    if len(rows) > 6:
                        title += " " + str(start // 6 + 1)
                    collection_names.append(title)
                    final_dependencies.append(group_id)
                    tasks = [{"type": "item", "item": item, "count": 1, "consume_items": False}
                             for _key, item, _mid in batch if item]
                    campaign_quests.append({"id": group_id, "title": title,
                                            "description": ["&7Finish the milestones for " + _list_names(mods.get(mid, mid) for _key, _item, mid in batch) + ". " +
                                                            ("Collect " + _list_names(_label(task["item"], scan) for task in tasks) + ". These items are detected in your inventory without being consumed."
                                                             if tasks else "Return here when these adventures are complete and mark this checkpoint.")],
                                            "tasks": tasks or [{"type": "checkmark", "title": "Confirm the branch achievements"}],
                                            "dependencies": [] if open_mode else [row[0] for row in batch],
                                            "rewards": [{"type": "xp_levels", "xp_levels": 2}], "x": 15, "y": 3 * len(final_dependencies),
                                            "shape": "hexagon", "size": 1.4,
                                            **({"icon": tasks[0]["item"]} if tasks else {})})
            # Recursively group exceptionally large packs to keep each join
            # readable while retaining every achievement transitively.
            level = 0
            while len(final_dependencies) > 6:
                combined = []
                for start in range(0, len(final_dependencies), 6):
                    batch = final_dependencies[start:start + 6]
                    group_id = _id("chapter", level, *batch)
                    combined.append(group_id)
                    icons = {q["id"]: q.get("icon") for q in campaign_quests}
                    group_icon = next((icons[key] for key in batch if icons.get(key) in known), None)
                    campaign_quests.append({"id": group_id, "title": "Campaign Milestones " + str(start // 6 + 1),
                                            "description": ["&7Complete the connected collections to record this part of the final project."],
                                            "tasks": [{"type": "checkmark"}], "dependencies": [] if open_mode else batch,
                                            "rewards": [], "x": 18 + level * 3, "y": start, "shape": "hexagon", "size": 1.4,
                                            **({"icon": group_icon} if group_icon else {})})
                level += 1
                final_dependencies = combined
            final_tasks = [{"type": "checkmark", "title": "Complete all campaign collections"}]
        if collection_names:
            finale_text = ["&7Your journeys meet here. Complete " + _list_names(collection_names) + ".",
                           "&7Once these collections are finished, mark this final checkpoint to complete the great work."]
        else:
            finale_text = ["&7Bring the achievements of " + _list_names(branch_names) + " together."]
            if collection:
                finale_text.append("&7Collect " + _list_names(_label(task["item"], scan) for task in collection) +
                                   ". These tasks detect the items in your inventory without consuming them.")
            else:
                finale_text.append("&7Finish the branch milestones, then return here and mark this final checkpoint.")
        campaign_quests.append({"id": _id("final", plan["kind"], *sorted(focus)), "title": plan["finale"],
                                "description": finale_text,
                                "tasks": final_tasks,
                                "dependencies": [] if open_mode else final_dependencies,
                                "rewards": [{"type": "xp_levels", "xp_levels": 10}], "x": 24, "y": 0, "shape": "octagon", "size": 2.0})
        if collection:
            campaign["icon"] = collection[0]["item"]
            campaign_quests[-1]["icon"] = campaign["icon"]
        represented = {_task_mod(row[4]) for row in milestones}
        plan["contributing_mods"] = sorted(represented)
        plan["skipped_mods"] = sorted(focus - represented - {"minecraft"})
        if plan["skipped_mods"]:
            plan["explanation"] += " Selected mods without a suitable major achievement in this book are omitted from the finale: " + ", ".join(mods.get(mid, mid) for mid in plan["skipped_mods"]) + "."
    for i, quest in enumerate(supporting.values()):
        quest["x"], quest["y"] = (i % 4) * 3, (i // 4) * 3
    result["chapters"] = [chapter for chapter in chapters if chapter.get("quests")] + [campaign]
    result["_offline_generated"] = True
    goal_ids = [q["id"] for q in campaign_quests if q.get("size", 1) >= 1.7]
    result["campaign"] = dict(plan, progression=progression, scope=scope, goal_quest_ids=goal_ids, finale_quest_ids=goal_ids,
                              explanation=plan["explanation"] + (" Open progression keeps these objectives available without prerequisite locks." if open_mode else ""))
    if scope == "selected":
        _connect_selected_campaign(result, scan, selected_ids)
        if open_mode:
            for chapter in result["chapters"]:
                for quest in chapter.get("quests", []):
                    quest["dependencies"] = []
    result["campaign"]["sidebar"] = {
        "group": "Main Goal", "after_group": "Getting Started",
        "chapter_ids": [chapter["id"] for chapter in result["chapters"]
                        if chapter.get("_campaign_role") in ("goal", "journey")],
    }
    _assert_dag(result["chapters"])
    return result


def _reduce_scenario_dependencies(quests, protected=()):
    """Remove redundant AND gates while retaining every prerequisite ancestor.

    Explicit author stage edges stay visible even when preparation already
    implies them. Scenario graphs contain AND gates only; OR gates must not
    pass through this transformation.
    """
    by_id = {q["id"]: q for q in quests}
    protected = set(protected)
    ancestors = {}
    pending = set(by_id)
    while pending:
        ready = [key for key in by_id if key in pending
                 and all(p in ancestors for p in by_id[key].get("dependencies", []))]
        if not ready:
            raise ValueError("Scenario preparation has a missing or circular prerequisite")
        for key in ready:
            parents = by_id[key].get("dependencies", [])
            ancestors[key] = set(parents).union(*(ancestors[p] for p in parents))
            pending.remove(key)
    for key, quest in by_id.items():
        parents = list(dict.fromkeys(quest.get("dependencies", [])))
        redundant = set().union(*(ancestors[p] for p in parents))
        quest["dependencies"] = [p for p in parents if p not in redundant or (key, p) in protected]


def apply_scenario(doc, scan, selected_ids, scenario, seed=0, progression="linear"):
    """Build an authored scenario from full, shared preparation graphs.

    Story gates connect stage achievements, never claim to be recipes. A
    restoration stage remains a manual action after obtaining its tools; no
    terrain change or world measurement is performed by this quest generator.
    """
    from quest_scenarios import validate_scenario
    verified = validate_scenario(scan, selected_ids, scenario, strict=True)
    stages = verified["stages"]
    known = _known(scan)
    _mods, selected = _selected(scan, selected_ids)
    rows, original_ids, by_id = {}, {}, {}
    completed_routes = set()

    def identity(quest):
        tasks = quest.get("tasks", [])
        if len(tasks) != 1:
            return "bundle:" + json.dumps(tasks, sort_keys=True)
        task = tasks[0]
        typ = task.get("type", "item")
        if typ == "checkmark":
            # Two actions using the same tool are not the same milestone.
            ref = task.get("_guide_ref") or quest.get("_pack_identity") or quest.get("id")
            return "check:" + str(ref) + ":" + str(quest.get("_pack_count", ""))
        key = {"kill": "entity"}.get(typ, typ)
        details = {k: v for k, v in task.items() if k not in ("id", "count", "value", "consume_items", "title")}
        return typ + ":" + json.dumps(details, sort_keys=True)

    for chapter in doc.get("chapters", []):
        for quest in chapter.get("quests", []):
            if len(quest.get("tasks", [])) == 1:
                original_ids.setdefault(identity(quest), quest["id"])

    def merge(quest):
        key = identity(quest)
        if key not in rows:
            new = copy.deepcopy(quest)
            new["id"] = original_ids.get(key) or _id("scenario_preparation", key)
            new["dependencies"] = []
            new.pop("dependency_requirement", None)
            new.update(optional=False, size=1.0, shape="hexagon", _scenario_preparation=True)
            rows[key] = new
            by_id[new["id"]] = new
        else:
            new = rows[key]
            for old_task, incoming in zip(new.get("tasks", []), quest.get("tasks", [])):
                for count_key in ("count", "value"):
                    if count_key in incoming:
                        old_task[count_key] = max(int(old_task.get(count_key, 1)), int(incoming[count_key]))
            # Preserve exact recipe preparation instructions alongside guide
            # prose when the same material is shared by several stages.
            if quest.get("_pack_identity"):
                for field in ("_pack_identity", "_pack_count", "_pack_components", "_pack_source", "_pack_line"):
                    if field in quest:
                        new[field] = copy.deepcopy(quest[field])
                new["description"] = list(dict.fromkeys(new.get("description", []) + quest.get("description", [])))
        return new

    def add_dependencies(quest, parents):
        quest["dependencies"] = list(dict.fromkeys(quest.get("dependencies", []) + list(parents)))

    def add_route(route):
        refs = {}
        for ref, row in route.items():
            description = row.get("description")
            if isinstance(description, str):
                description = ["&7" + description] if description else []
            if not description:
                item = row["task"].get("item")
                description = ["&7" + (_acquisition(item, scan) if item else "Complete " + row["title"] + " as preparation.")]
            quest = {"id": _id("objective", ref), "title": row["title"], "description": description,
                     "tasks": [copy.deepcopy(row["task"])], "rewards": [{"type": "xp", "xp": 40}]}
            if row.get("icon") in known:
                quest["icon"] = row["icon"]
            refs[ref] = merge(quest)
        for ref, row in route.items():
            key = identity(refs[ref])
            if key not in completed_routes:
                add_dependencies(refs[ref], [refs[parent]["id"] for parent in row["parents"]])
                completed_routes.add(key)
        return refs

    prepared = {}
    def prepare(plan):
        cache_key = plan["text"]
        if cache_key in prepared:
            return prepared[cache_key]
        # Existing campaign construction preserves exact scripted ingredient
        # counts, component checkpoints and fluids. Its sampled ordinary
        # prerequisites are augmented with complete authored/recipe closure.
        if plan["kind"] == "item" and not any(_pack_recipe(item, scan) for item in plan["exact_goal_ids"]):
            targets = [{"id": _id("goal", item), "title": _label(item, scan),
                        "description": ["&7" + _acquisition(item, scan)],
                        "tasks": [{"type": "item", "item": item, "count": 1, "consume_items": False}],
                        "dependencies": [], "rewards": [{"type": "xp", "xp": 50}]}
                       for item in plan["exact_goal_ids"]]
            built = {"chapters": [{"quests": targets}], "campaign": {"finale_quest_ids": [q["id"] for q in targets]}}
        else:
            built = apply_campaign({"chapters": []}, scan, selected_ids, plan["text"], seed, "linear")
        quests = [q for chapter in built["chapters"] for q in chapter.get("quests", [])]
        ids = {q["id"]: merge(q) for q in quests}
        for quest in quests:
            add_dependencies(ids[quest["id"]], [ids[parent]["id"] for parent in quest.get("dependencies", [])])
        if plan["kind"] == "objective":
            _root, route = _objective_route(plan["goal_target"], scan, include_recipes=True)
            add_route(route)
        # Include every item's guide and recipe route, even when the ordinary
        # campaign's tiny preparation budget would otherwise omit it.
        todo = [t["item"] for q in quests for t in q.get("tasks", [])
                if isinstance(t.get("item"), str) and t["item"] in known]
        visited = set()
        while todo:
            item = todo.pop(0)
            if item in visited:
                continue
            visited.add(item)
            if len(visited) > 1500 or len(rows) > 2500:
                raise ValueError("This scenario's preparation graph is too large. Split it into smaller scenarios.")
            target = {"kind": "item", "target_id": item, "label": _label(item, scan),
                      "mod": item.split(":")[0], "task": {"type": "item", "item": item, "count": 1, "consume_items": False}}
            _root, route = _objective_route(target, scan, include_recipes=True)
            add_route(route)
            for row in route.values():
                candidate = row["task"].get("item")
                if candidate in known and candidate not in visited and _pack_recipe(candidate, scan):
                    nested = resolve_goal(scan, selected_ids, "craft " + candidate)
                    if nested["text"] != cache_key:
                        prepare(nested)
            scope = {row["mod"] for row in route.values()} - {"minecraft", ""}
            if scope - selected:
                raise ValueError("The scenario's preparation uses unselected mods. Select these as well: " + ", ".join(sorted(scope - selected)))
        finals = [ids[key]["id"] for key in built["campaign"]["finale_quest_ids"]]
        prepared[cache_key] = finals
        return finals

    stage_quests, stage_goals, claimed = {}, {}, set()
    owners, story_edges = {}, set()
    for stage in stages:
        previous_ids = set(by_id)
        plans = stage["goal_plans"]
        goal_ids = [prepare(plan) for plan in plans]
        main_ids = goal_ids[0]
        preparation = list(dict.fromkeys(key for group in goal_ids for key in group))
        order = [] if progression == "open" else [stage_quests[parent] for parent in stage["depends_on"]]
        introduced = set(by_id) - previous_ids
        if order:
            def already_required(key, wanted, seen=None):
                if key == wanted:
                    return True
                seen = set(seen or ())
                if key in seen:
                    return False
                seen.add(key)
                return any(already_required(parent, wanted, seen) for parent in by_id[key].get("dependencies", []))
            for key in sorted(introduced):
                quest = by_id[key]
                if not (set(quest.get("dependencies", [])) & introduced):
                    safe_order = [parent for parent in order if not already_required(parent, key)]
                    add_dependencies(quest, safe_order)
                    if safe_order:
                        quest.setdefault("description", []).append("Begin this preparation after completing " + ", ".join(by_id[parent]["title"] for parent in safe_order) + ".")
        title = str(stage.get("title") or stage["goal_plan"]["finale"])
        if stage.get("completion") == "manual":
            description = [str(stage["description"])]
            if stage.get("mechanic_note") and stage["mechanic_note"] not in description:
                description.append(stage["mechanic_note"])
            description.append("Manual checkpoint: confirm after completing the work described above.")
            quest = {"id": _id("scenario_stage", stage["id"]), "title": title, "description": description,
                     "tasks": [{"type": "checkmark", "title": "Confirm " + title}], "dependencies": preparation + order,
                     "rewards": [{"type": "xp", "xp": 100}], "optional": False, "size": 1.5, "shape": "octagon"}
            rows["stage:" + stage["id"]] = quest
            by_id[quest["id"]] = quest
        elif len(main_ids) == 1 and main_ids[0] not in claimed:
            quest = by_id[main_ids[0]]
            quest["title"] = title
            quest["size"], quest["shape"] = 1.5, "octagon"
            if stage.get("description"):
                quest["description"] = [str(stage["description"])] + quest.get("description", [])
            add_dependencies(quest, [key for key in preparation + order if key != quest["id"]])
            claimed.add(quest["id"])
        else:
            # A repeated author goal is an explicit new checkpoint. Keeping
            # its own objective prevents reverse locks on shared preparation.
            tasks = [copy.deepcopy(t) for key in main_ids for t in by_id[key]["tasks"]]
            quest = {"id": _id("scenario_stage", stage["id"]), "title": title,
                     "description": [str(stage.get("description") or "Complete the listed objectives for this stage.")],
                     "tasks": tasks, "dependencies": preparation + order, "rewards": [],
                     "optional": False, "size": 1.5, "shape": "octagon"}
            rows["stage:" + stage["id"]] = quest
            by_id[quest["id"]] = quest
        if order:
            quest.setdefault("description", []).append("Continue here after completing " + ", ".join(by_id[key]["title"] for key in order) + ".")
        quest["_scenario_stage"] = stage["id"]
        stage_quests[stage["id"]] = quest["id"]
        stage_goals[stage["id"]] = preparation
        for key in by_id:
            owners.setdefault(key, stage["id"])
        owners[quest["id"]] = stage["id"]
        story_edges.update((quest["id"], parent) for parent in order)

    depended = {parent for stage in stages for parent in stage["depends_on"]}
    leaves = [stage_quests[stage["id"]] for stage in stages if stage["id"] not in depended]
    if len(leaves) == 1:
        final = by_id[leaves[0]]
    else:
        final = {"id": _id("scenario_final", *[stage["id"] for stage in stages]),
                 "title": "The Campaign Comes Together", "description": ["Complete every connected stage, then confirm the campaign's achievements."],
                 "tasks": [{"type": "checkmark", "title": "Confirm all scenario stages"}],
                 "dependencies": leaves, "rewards": [{"type": "xp_levels", "xp_levels": 5}]}
        rows["final"] = final
    final.update(size=2.0, shape="octagon")
    quests = list(rows.values())
    for i, quest in enumerate(quests):
        quest["x"], quest["y"] = (i % 8) * 3, (i // 8) * 3
    try:
        _assert_dag([{"quests": quests}])
    except ValueError as exc:
        raise ValueError("The authored stage order conflicts with a goal's required preparation. Reorder the stages or their dependencies. " + str(exc)) from exc
    if progression == "open":
        for quest in quests:
            quest["dependencies"] = []
    else:
        _reduce_scenario_dependencies(quests, story_edges)
    # A story is read stage by stage. Put shared tools in their first stage
    # once, and retain cross-chapter prerequisites for every later use.
    # The explicit checkpoint remains in its own numbered story chapter.
    title = verified.get("title") or "Your Campaign"
    chapters = []
    for index, stage in enumerate(stages):
        chapter_quests = [q for q in quests if owners.get(q["id"]) == stage["id"]]
        checkpoint = by_id[stage_quests[stage["id"]]]
        description = [verified.get("premise", "")] if index == 0 else []
        description.append(str(stage.get("description") or "Complete the connected preparation, then finish this stage."))
        # Bootstrap review, terrain-detection limits and authoring mechanics
        # stay in scenario metadata/the editor, not every player's quest map.
        chapter = {"id": "scenario_stage_" + stage["id"],
                   "title": "%02d · %s" % (index + 1, stage.get("title") or checkpoint["title"]),
                   "group": title, "description": list(dict.fromkeys(filter(None, description))),
                   "quests": chapter_quests, "_scenario": True, "_campaign_role": "stage"}
        icon = checkpoint.get("icon") or next((t.get("item") for t in checkpoint["tasks"] if t.get("item")), None)
        if not icon:
            icon = next((by_id[key].get("icon") or next((t.get("item") for t in by_id[key]["tasks"] if t.get("item")), None)
                         for key in stage_goals[stage["id"]] if key in by_id), None)
        if icon in known:
            chapter["icon"] = icon
            checkpoint.setdefault("icon", icon)
        chapters.append(chapter)
    if len(leaves) > 1:
        chapters.append({"id": "scenario_final", "title": "%02d · The Campaign Comes Together" % (len(stages) + 1),
                         "group": title, "description": ["Complete every story branch to finish your campaign."],
                         "quests": [final], "_scenario": True, "_campaign_role": "finale"})
    _assert_dag(chapters)
    result = copy.deepcopy(doc)
    result.update(chapters=chapters, _offline_generated=True)
    result["scenario"] = dict(verified, stage_quest_ids=stage_quests, stage_preparation_ids=stage_goals,
                              stage_chapter_ids={stage["id"]: chapter["id"] for stage, chapter in zip(stages, chapters)},
                              finale_quest_ids=[final["id"]], progression=progression)
    result["campaign"] = {"kind": "scenario", "text": verified.get("premise", ""), "title": title,
                          "finale_quest_ids": [final["id"]], "progression": progression,
                          "focus_mods": sorted({m for stage in stages for m in stage["resolved_mods"]}),
                          "explanation": "The authored stages share complete preparation routes and preserve their declared story order.",
                          "sidebar": {"group": title, "after_group": "Getting Started",
                                      "chapter_ids": [chapter["id"] for chapter in chapters]}}
    return result
