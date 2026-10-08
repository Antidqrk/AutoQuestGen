"""Editable, deterministic scenario outlines grounded in local mod mechanics.

These plans create quest goals and manual checkpoints, not world-generation
changes or terrain scanners. Authors remain responsible for starting supplies
and for reviewing the conditions of their chosen world.
"""
from __future__ import annotations

from copy import deepcopy
import re


_BOTANIA_SOURCE = {
    "type": "installed_guide",
    "mod": "botania",
    "verified_version": "1.20.1-456-FORGE",
    "guide": "Lexica Botania",
    "jar": "Botania-1.20.1-456-FORGE.jar",
}
_CREATE_SOURCE = {"type": "installed_guide", "mod": "create", "verified_version": "1.21.1-6.0.10",
                  "guide": "Ponder", "jar": "create-1.21.1-6.0.10.jar"}
_ARS_SOURCE = {"type": "installed_guide", "mod": "ars_nouveau", "verified_version": "1.21.1-5.13.1",
               "guide": "Worn Notebook", "jar": "ars_nouveau-1.21.1-5.13.1.jar"}
_AURA_SOURCE = {"type": "installed_guide", "mod": "naturesaura", "verified_version": "41.10",
                "guide": "Book of Natural Aura", "jar": "NaturesAura-41.10.jar"}

# Authored factual adapters. Availability is checked against each pack scan;
# an English display name or a story premise never creates a missing item.
_CAPABILITIES = (
    {
        "id": "botania_mana_workshop", "mod": "botania", "role": "start",
        "title": "A Small Source of Life", "goal": "craft botania:mana_pool",
        "target_id": "botania:mana_pool",
        "requirements": ["craft botania:endoflame", "craft botania:mana_spreader", "craft botania:mana_tablet"],
        "required_items": ["botania:mana_pool", "botania:endoflame", "botania:mana_spreader", "botania:mana_tablet"],
        "description": "Build an Endoflame setup that sends mana through a Mana Spreader into a Mana Pool. Charge a Mana Tablet for the restoration tools. Confirm after mana reaches the pool and the tablet carries a usable supply.",
        "completion": "manual",
        "source": {**_BOTANIA_SOURCE, "entries": ["generating_flowers/endoflame", "mana/spreader", "mana/pool", "mana/mana_tablet"]},
    },
    {
        "id": "botania_restore_water", "mod": "botania", "role": "water",
        "title": "The First Returning Water", "goal": "craft botania:water_rod",
        "target_id": "botania:water_rod", "requirements": ["craft botania:mana_tablet"],
        "required_items": ["botania:water_rod", "botania:mana_tablet"],
        "description": "Use the Rod of the Seas with a supply of mana to place water in the restoration area. Shape a small pool or streambed, then confirm once it contains water. Obtaining the rod alone does not restore the landscape.",
        "completion": "manual",
        "source": {**_BOTANIA_SOURCE, "entries": ["tools/water_rod"], "language_keys": ["botania.page.waterRod0"]},
    },
    {
        "id": "botania_restore_soil", "mod": "botania", "role": "soil",
        "title": "Soil for the Next Beginning", "goal": "craft botania:dirt_rod",
        "target_id": "botania:dirt_rod", "requirements": ["craft botania:mana_tablet"],
        "required_items": ["botania:dirt_rod", "botania:mana_tablet"],
        "description": "Use the Rod of the Lands and inventory mana to place dirt where plants will return. Prepare connected beds or patches in the chosen restoration area, then confirm after the soil is in place.",
        "completion": "manual",
        "source": {**_BOTANIA_SOURCE, "entries": ["tools/dirt_rod"], "language_keys": ["botania.page.dirtRod0"]},
    },
    {
        "id": "botania_restore_grass", "mod": "botania", "role": "grass",
        "title": "The First Green Patch", "goal": "craft botania:grass_seeds",
        "target_id": "botania:grass_seeds", "requirements": [],
        "required_items": ["botania:grass_seeds"],
        "description": "Use Pasture Seeds on dirt to grow grass in the nearby area. Restore a visible green patch around the new soil, then confirm after the grass appears. This seed spreads grass; it does not plant trees.",
        "completion": "manual",
        "source": {**_BOTANIA_SOURCE, "entries": ["tools/grass_seeds"], "language_keys": ["botania.page.grassSeeds0"]},
    },
    {
        "id": "create_workshop", "mod": "create", "role": "start",
        "title": "The First Turning Shaft", "goal": "craft create:hand_crank", "target_id": "create:hand_crank",
        "requirements": ["craft create:shaft"], "required_items": ["create:hand_crank", "create:shaft"],
        "description": "Place a Hand Crank and connect a shaft. Turn the crank to produce rotational force, then confirm after the connected shaft rotates. This provides a small manual power source for the first machines.",
        "completion": "manual", "source": {**_CREATE_SOURCE, "language_keys": ["create.ponder.hand_crank.text_1"]},
    },
    {
        "id": "create_transport_water", "mod": "create", "role": "water",
        "title": "Carry Water Back to the Valley", "goal": "craft create:mechanical_pump", "target_id": "create:mechanical_pump",
        "requirements": ["craft create:fluid_pipe", "craft create:hand_crank"],
        "required_items": ["create:mechanical_pump", "create:fluid_pipe", "create:hand_crank"],
        "description": "Connect a Mechanical Pump and Fluid Pipes to an existing water source. Supply rotational power, using a Hand Crank for a small first setup, and direct an open pipe outlet into the restoration area. Confirm after water reaches the destination. This transports existing water; it does not create the world's first water supply.",
        "completion": "manual", "water_mode": "transport_existing",
        "source": {**_CREATE_SOURCE, "language_keys": ["create.ponder.fluid_pipe_flow.text_4", "create.ponder.fluid_pipe_interaction.text_3", "create.ponder.fluid_pipe_interaction.text_4"]},
    },
    {
        "id": "ars_spell_workshop", "mod": "ars_nouveau", "role": "start",
        "title": "Prepare the Spell Workshop", "goal": "craft ars_nouveau:apprentice_spell_book", "target_id": "ars_nouveau:apprentice_spell_book",
        "requirements": ["craft ars_nouveau:scribes_table"],
        "required_items": ["ars_nouveau:apprentice_spell_book", "ars_nouveau:scribes_table"],
        "description": "Prepare an Apprentice's Spell Book and a Scribe's Table for learning and composing the restoration spell. Review the Worn Notebook's glyph instructions, then confirm when the workshop is ready for tier-two spell effects.",
        "completion": "manual", "source": {**_ARS_SOURCE, "entries": ["glyphs_2/glyph_conjure_water"]},
    },
    {
        "id": "ars_conjure_water", "mod": "ars_nouveau", "role": "water",
        "title": "Give the Dry Ground Water", "goal": "craft ars_nouveau:glyph_conjure_water", "target_id": "ars_nouveau:glyph_conjure_water",
        "requirements": ["craft ars_nouveau:apprentice_spell_book", "craft ars_nouveau:scribes_table"],
        "required_items": ["ars_nouveau:glyph_conjure_water", "ars_nouveau:apprentice_spell_book", "ars_nouveau:scribes_table"],
        "description": "Learn Conjure Water, include it in a usable spell in the Apprentice's Spell Book, and cast it to place water in the chosen restoration area. Confirm after water appears. The verified glyph recipe already needs a water bucket, so the author must provide a starting water route.",
        "completion": "manual", "water_mode": "conjure_after_bootstrap",
        "source": {**_ARS_SOURCE, "entries": ["glyphs_2/glyph_conjure_water"], "language_keys": ["ars_nouveau.glyph_desc.glyph_conjure_water"], "recipes": ["ars_nouveau:glyph_conjure_water"]},
    },
    {
        "id": "naturesaura_ritual_workshop", "mod": "naturesaura", "role": "start",
        "title": "Prepare a Forest Ritual", "goal": "craft naturesaura:wood_stand", "target_id": "naturesaura:wood_stand",
        "requirements": ["craft naturesaura:gold_powder"], "required_items": ["naturesaura:wood_stand", "naturesaura:gold_powder"],
        "description": "Prepare Wooden Stands and Gold Powder, then lay out the Ritual of the Forest as shown in the guide. Each recipe needs its specified sapling and ingredients; growing that sapling starts the ritual and consumes the tree. Confirm after the ritual site is ready.",
        "completion": "manual", "source": {**_AURA_SOURCE, "entries": ["practices/tree_ritual"]},
    },
    {
        "id": "naturesaura_restore_trees", "mod": "naturesaura", "role": "trees",
        "title": "An Ancient Tree for the Overworld", "goal": "craft naturesaura:ancient_sapling", "target_id": "naturesaura:ancient_sapling",
        "requirements": [], "required_items": ["naturesaura:ancient_sapling"],
        "description": "Plant an Ancient Sapling in the Overworld and let it grow. Its leaves release Aura toward normal local levels, but those leaves gradually die as they supply it. Confirm after the tree grows, and plan replacement planting if you want the canopy to remain green. Its Aura-restoring effect does not work outside the Overworld.",
        "completion": "manual", "source": {**_AURA_SOURCE, "entries": ["creating/ancient_tree"]},
    },
)

_NOTES = [
    "This is an editable quest outline. It does not change world generation, generate terrain, or measure how much land has been restored.",
    "Manual stages ask the player to confirm the described work. Collecting a tool alone does not prove that its world-changing action happened.",
    "Stage links express the author's campaign order. They do not claim that every earlier story stage is a crafting ingredient for the next one.",
]
_BOOTSTRAP = (
    "A barren-world start needs an author-defined supply or acquisition route for Botania's starting materials: "
    "flowers, wood, stone, seeds, initial water and fuel, plus dirt, tall grass and any chosen saplings. "
    "The verified Rod of the Seas recipe needs a water bottle, the Rod of the Lands needs dirt, and Pasture Seeds need tall grass; they cannot bootstrap a world containing none of those resources."
)


def _selection(scan, selected_ids):
    installed = {str(m["mod_id"]): str(m.get("name") or m["mod_id"]) for m in scan.get("mods", ())}
    chosen = set(selected_ids) if selected_ids else set(installed)
    return installed, chosen


def _known(scan):
    items = set(scan.get("vanilla") or ())
    for group in (scan.get("items") or {}).values():
        items.update(group)
    return items - set(scan.get("unobtainable") or ())


def available_capabilities(scan, selected_ids, allow_vanilla=False):
    """List verified-mechanic adapters whose required items are in this scan."""
    installed, chosen = _selection(scan, selected_ids)
    known = _known(scan)
    found = [deepcopy(row) for row in _CAPABILITIES if row["mod"] in installed
             and row["mod"] in chosen and set(row["required_items"]) <= known]
    if allow_vanilla:
        # Plain oak/birch saplings grow individually; avoid dark-oak and other
        # saplings whose special arrangements need different instructions.
        tree = next((item for item in ("minecraft:oak_sapling", "minecraft:birch_sapling") if item in known), None)
        if tree:
            found.append({
                "id": "vanilla_restore_trees", "mod": "minecraft", "role": "trees",
                "title": "A Canopy Returns", "goal": "craft " + tree, "target_id": tree,
                "requirements": [], "required_items": [tree], "completion": "manual",
                "description": "Plant the selected saplings on suitable soil with light and enough growing space. Let trees grow and collect replacement saplings from their leaves. Confirm after the restored area contains living trees and a supply for the next planting.",
                "source": {"type": "vanilla_mechanic", "mechanic": "Oak or birch sapling growth; the actual sapling must be present in the scanned pack."},
            })
    return found


def stage_from_capability(capability, stage_id, depends_on=()):
    """Make an editable stage from one available_capabilities entry."""
    row = deepcopy(capability)
    row["capability_id"] = row.pop("id")
    row["id"] = stage_id
    row["depends_on"] = list(depends_on)
    row["status"] = "draft"
    return row


def _pending(stage_id, title, role, depends_on, description):
    return {"id": stage_id, "title": title, "goal": "", "mod": "", "description": description,
            "depends_on": list(depends_on), "completion": "manual", "role": role,
            "requirements": [], "status": "needs_goal"}


def propose_scenario(scan, selected_ids, premise, preferred_start="botania", mode="assisted", allow_vanilla=False):
    """Return an editable draft; unsupported premises remain visibly unfinished.

    ``authored`` starts with no stages. ``assisted`` recognizes restoration
    premises and proposes known capabilities from the allowed installed mods.
    The proposal is never silently accepted or applied to a Minecraft world.
    """
    premise = str(premise or "").strip()
    if mode in ("author", "manual"):
        mode = "authored"
    if mode not in ("assisted", "authored"):
        raise ValueError("Choose assisted or authored scenario planning.")
    installed, chosen = _selection(scan, selected_ids)
    result = {"premise": premise, "title": "Your Campaign", "mode": mode, "preferred_start": preferred_start,
              "allow_vanilla": bool(allow_vanilla), "stages": [], "notes": list(_NOTES), "requires_review": True}
    if mode == "authored":
        result["notes"].append("Add stages, choose their goals and mods, and set their order before validation and generation.")
        return result
    words = set(re.findall(r"[a-z]+", premise.lower()))
    restoration = bool(words & {"barren", "wasteland", "lifeless", "restore", "restoring", "restoration", "regrow", "regrowth", "rewild", "revive", "reviving"})
    if not restoration:
        result["notes"].append("There is no authored automatic outline for this premise. The planner has left an editable draft instead of inventing mod abilities.")
        return result
    result["title"] = "A World Worth Growing"
    result["notes"].append(_BOOTSTRAP)
    # Do not let dictionary insertion order quietly select the last method.
    # Prefer the chosen starting mod, then Botania; ordinary vanilla trees
    # remain the conservative default when allowed, with other mods selectable.
    available = available_capabilities(scan, selected_ids, allow_vanilla)
    available.sort(key=lambda row: (0 if row["mod"] == preferred_start else 1 if row["mod"] == "botania" else 2 if row["mod"] == "minecraft" else 3, row["id"]))
    caps = {}
    for capability in available:
        caps.setdefault(capability["role"], capability)
    stages = result["stages"]
    if "start" in caps and caps["start"]["mod"] == preferred_start:
        stages.append(stage_from_capability(caps["start"], "stage_start", []))
    else:
        name = installed.get(preferred_start, str(preferred_start).replace("_", " ").title())
        reason = "Choose a concrete starting goal for " + name + "."
        if preferred_start not in installed or preferred_start not in chosen:
            reason += " This starting mod must be installed and selected before it can supply a stage."
        stages.append(_pending("stage_start", "Begin with " + name, "start", [], reason))
    titles = {"water": "Choose How Water Returns", "soil": "Choose How Soil Returns",
              "grass": "Choose How Grass Returns", "trees": "Choose How Trees Return"}
    previous = "stage_start"
    for role in ("water", "soil", "grass", "trees"):
        stage_id = "stage_" + role
        if role in caps:
            stages.append(stage_from_capability(caps[role], stage_id, [previous]))
        else:
            detail = "Choose a verified installed method for this restoration stage, or remove the stage from the outline."
            if role == "trees" and not allow_vanilla:
                detail += " Vanilla sapling stages are disabled; enable them or choose an appropriate goal from an allowed mod."
            stages.append(_pending(stage_id, titles[role], role, [previous], detail))
        previous = stage_id
    result["notes"].append("Review the restoration area, the starting resource route, and each manual completion condition before generating the book.")
    if any(row.get("mod") == "create" and row.get("role") == "water" for row in stages):
        result["notes"].append("The Create route needs an existing water source to move; the pump is not a first-water generator.")
    if any(row.get("mod") == "ars_nouveau" and row.get("role") == "water" for row in stages):
        result["notes"].append("The verified Conjure Water glyph recipe uses a water bucket. Supply a starter water route before relying on the spell.")
    return result


def validate_scenario(scan, selected_ids, stages, premise="", allow_vanilla=False, strict=False):
    """Validate a draft without rearranging it or replacing an author's goals.

    Returns ``{valid, stages, issues, notes}``. Draft errors are collected for
    the editor; ``strict=True`` raises ValueError if generation must be blocked.
    Each valid stage exposes ``goal_plan`` and ``goal_plans`` for the existing
    campaign builder, plus its honest manual or detected completion task.
    """
    from quest_campaign import resolve_goal

    source_notes, title = [], "Your Campaign"
    if isinstance(stages, dict):
        plan = stages
        if "allow_vanilla" in plan and not isinstance(plan["allow_vanilla"], bool):
            raise ValueError("The vanilla-stage setting must be true or false.")
        if not isinstance(plan.get("notes", []), list) or any(not isinstance(note, str) for note in plan.get("notes", [])):
            raise ValueError("Scenario notes must be a list of text entries.")
        if any(key in plan and not isinstance(plan[key], str) for key in ("title", "premise")):
            raise ValueError("The campaign title and premise must be text.")
        premise = premise or str(plan.get("premise") or "")
        allow_vanilla = bool(plan.get("allow_vanilla", allow_vanilla))
        source_notes = [str(note) for note in plan.get("notes", ())]
        title = str(plan.get("title") or title)
        stages = plan.get("stages", ())
    if not isinstance(stages, (list, tuple)) or any(not isinstance(row, dict) for row in stages):
        raise ValueError("Scenario stages must be a list of editable stage records.")
    if len(stages) > 64:
        raise ValueError("A scenario can contain up to 64 stages. Split a larger story into separate campaigns.")
    for row in stages:
        for key in ("id", "title", "goal", "mod", "description", "completion", "capability_id"):
            if key in row and not isinstance(row[key], str):
                raise ValueError("Stage field ‘" + key + "’ must be text.")
    rows = deepcopy(list(stages))
    issues = []
    installed, chosen = _selection(scan, selected_ids)
    caps = {row["id"]: row for row in available_capabilities(scan, selected_ids, allow_vanilla)}
    known_caps = {row["id"] for row in _CAPABILITIES} | {"vanilla_restore_trees"}
    ids = [str(row.get("id") or "") for row in rows]
    def issue(stage_id, message):
        issues.append({"stage_id": stage_id, "message": message})
    if not rows:
        issue("", "Add at least one stage before generating a campaign.")
    seen = set()
    for row, stage_id in zip(rows, ids):
        start_errors = len(issues)
        # Never trust cached resolutions after the author edits a stage.
        for key in ("goal_plan", "goal_plans", "goal_target", "task", "resolved_mods"):
            row.pop(key, None)
        if not stage_id or not re.fullmatch(r"[a-zA-Z0-9_-]+", stage_id):
            issue(stage_id, "Each stage needs a unique ID made of letters, numbers, underscores or hyphens.")
        if stage_id in seen:
            issue(stage_id, "Two stages have the same ID: " + stage_id + ".")
        dependencies = row.get("depends_on") or []
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            issue(stage_id, "Stage dependencies must be a list of earlier stage IDs.")
            dependencies = []
        row["depends_on"] = list(dict.fromkeys(dependencies))
        for dependency in dependencies:
            if dependency == stage_id:
                issue(stage_id, "A stage cannot depend on itself.")
            elif dependency not in seen:
                issue(stage_id, "Dependency ‘" + dependency + "’ must name an earlier stage. Move the stage or change the dependency; the planner will not reorder your story.")
        seen.add(stage_id)
        completion = row.get("completion", "objective")
        if completion not in ("manual", "objective"):
            issue(stage_id, "Choose objective or manual completion for this stage.")
        if completion == "manual" and not str(row.get("description") or "").strip():
            issue(stage_id, "A manual stage needs a concrete description of the action the player must confirm.")
        declared_mod = str(row.get("mod") or "")
        if declared_mod and declared_mod != "minecraft" and (declared_mod not in installed or declared_mod not in chosen):
            issue(stage_id, "The stage's chosen mod is not installed and selected: " + declared_mod + ".")
        if declared_mod == "minecraft" and not allow_vanilla:
            issue(stage_id, "Enable vanilla stages to use this Minecraft objective.")
        cap_id = row.get("capability_id")
        cap = caps.get(cap_id)
        if cap_id and cap is None:
            issue(stage_id, "The chosen restoration method is unavailable in the allowed content." if cap_id in known_caps else "Unknown restoration method; choose a listed capability or clear it and author a normal goal.")
        goal = str(row.get("goal") or "").strip()
        if not goal:
            issue(stage_id, "Choose a concrete installed goal for this stage.")
        requirements = row.get("requirements") or []
        if not isinstance(requirements, list) or any(not isinstance(req, str) for req in requirements):
            issue(stage_id, "Stage requirements must be a list of goal descriptions.")
            requirements = []
        if cap:
            # Real tool operation still needs its factual material anchors
            # even if an editable draft omitted those supporting goals.
            requirements = list(dict.fromkeys(requirements + cap["requirements"]))
            row["mechanic_note"] = cap["description"]
            row["source"] = deepcopy(cap["source"])
            if completion != "manual":
                issue(stage_id, "This restoration method changes the world through player actions and requires manual confirmation.")
        row["requirements"] = requirements
        plans, main_plan = [], None
        for goal_index, wanted in enumerate(list(dict.fromkeys([goal] + requirements)) if goal else []):
            try:
                resolved = resolve_goal(scan, selected_ids, wanted)
                if resolved["kind"] not in ("item", "objective"):
                    raise ValueError("Choose a concrete item, boss, location or guided project; a whole-mod theme is too broad for one ordered stage.")
                owners = set(resolved.get("focus_mods") or ())
                # Vanilla ingredients of modded recipes are permitted. This
                # switch controls standalone vanilla goals in the outline.
                if not allow_vanilla and owners == {"minecraft"}:
                    raise ValueError("Enable vanilla stages to use a standalone Minecraft objective.")
                plans.append(resolved)
                if goal_index == 0:
                    main_plan = resolved
            except ValueError as exc:
                issue(stage_id, str(exc))
        if main_plan and cap and cap["target_id"] not in main_plan.get("exact_goal_ids", ()):
            issue(stage_id, "The edited goal no longer matches this restoration method. Choose a matching method or clear capability_id for a fully authored stage.")
        if main_plan:
            row["goal_plan"] = main_plan
            row["goal_plans"] = plans
            row["resolved_mods"] = sorted({mod for plan in plans for mod in plan.get("focus_mods", ())})
            target = main_plan.get("goal_target")
            goal_mods = {target["mod"]} if target else {ref.split(":")[0] for ref in main_plan.get("exact_goal_ids", ())}
            if declared_mod and declared_mod not in goal_mods:
                issue(stage_id, "The goal belongs to " + ", ".join(sorted(goal_mods)) + ", not the stage's chosen mod. Update the goal or its mod.")
            if not declared_mod and len(goal_mods) == 1:
                row["mod"] = next(iter(goal_mods))
            if target:
                row["goal_target"] = deepcopy(target)
            if completion == "manual":
                row["task"] = {"type": "checkmark", "title": "Confirm " + str(row.get("title") or "the stage")}
            elif target:
                row["task"] = deepcopy(target["task"])
            elif len(main_plan.get("exact_goal_ids", ())) == 1:
                row["task"] = {"type": "item", "item": main_plan["exact_goal_ids"][0], "count": 1}
        row["status"] = "valid" if len(issues) == start_errors else "needs_review"
    notes = list(dict.fromkeys(list(_NOTES) + source_notes))
    if any(row.get("capability_id") in known_caps for row in rows) and _BOOTSTRAP not in notes:
        notes.append(_BOOTSTRAP)
    result = {"valid": not issues, "premise": premise, "title": title, "allow_vanilla": bool(allow_vanilla),
              "stages": rows, "issues": issues, "notes": notes}
    if strict and issues:
        raise ValueError("Review the scenario before generating: " + " ".join(issue["message"] for issue in issues))
    return result
