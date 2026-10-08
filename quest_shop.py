"""Optional offline supply shop using native FTB Quests barter mechanics.

The installed FTB Quests 2001.4.21 and 2101.1.36 ItemTask implementations
require deliberate submission for consume_items tasks. Repeatable quests reset
after claiming their rewards. Explicit team rewards prevent one payment from
producing one reward per teammate; manual claiming avoids immediate rebuy loops.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re

CURRENCY = "minecraft:emerald"
SHOP_STYLES = ("balanced", "essentials", "builder", "adventurer")
SHOP_PRICES = ("low", "normal", "high")
_GENERATOR = "autoquestgen"

# Item, quantity, normal emerald price, shelf tier, useful shop styles.
# Deliberately excludes machines, ores, rare materials, dimension keys and boss
# drops. Every candidate still needs this pack's verified obtainable inventory.
_OFFERS = (
    ("bread", 16, 2, 0, "essentials adventurer"),
    ("baked_potato", 16, 2, 0, "essentials adventurer"),
    ("torch", 32, 1, 0, "essentials builder adventurer"),
    ("arrow", 32, 2, 0, "essentials adventurer"),
    ("cobblestone", 64, 1, 0, "essentials builder"),
    ("oak_log", 16, 2, 0, "essentials builder"),
    ("carrot", 16, 2, 0, "essentials"),
    ("potato", 16, 2, 0, "essentials"),
    ("wheat_seeds", 16, 1, 0, "essentials"),
    ("sugar_cane", 16, 2, 0, "essentials"),
    ("sand", 32, 2, 0, "builder"),
    ("gravel", 32, 2, 0, "builder"),
    ("oak_boat", 1, 1, 0, "adventurer"),
    ("cooked_beef", 16, 3, 1, "adventurer"),
    ("cooked_chicken", 16, 3, 1, "adventurer"),
    ("bone_meal", 32, 2, 1, "essentials builder"),
    ("pumpkin_seeds", 16, 2, 1, "essentials"),
    ("melon_seeds", 16, 2, 1, "essentials"),
    ("stone", 32, 2, 1, "builder"),
    ("stone_bricks", 32, 2, 1, "builder"),
    ("glass", 16, 2, 1, "essentials builder"),
    ("white_wool", 16, 3, 1, "builder adventurer"),
    ("flower_pot", 8, 2, 1, "builder"),
    ("oak_leaves", 32, 2, 1, "builder"),
    ("oak_fence", 16, 2, 1, "builder"),
    ("compass", 1, 4, 1, "adventurer"),
    ("paper", 16, 2, 1, "adventurer"),
    ("leather", 8, 3, 2, "essentials adventurer"),
    ("terracotta", 32, 3, 2, "builder"),
    ("lantern", 8, 3, 2, "builder adventurer"),
    ("scaffolding", 32, 3, 2, "builder"),
    ("candle", 8, 3, 2, "builder"),
)

# These are real early/middle achievements, not arbitrary registry depth. Larger
# authored capstones are shared with the campaign's existing semantic filter.
_EARLY_ACHIEVEMENTS = {
    "minecraft:iron_pickaxe", "minecraft:iron_chestplate", "minecraft:diamond_pickaxe",
    "minecraft:enchanting_table", "minecraft:brewing_stand", "minecraft:netherite_pickaxe",
    "minecraft:elytra", "minecraft:beacon",
    "botania:apothecary_default", "botania:mana_pool", "botania:runic_altar", "botania:terra_plate",
    "create:mechanical_press", "create:mechanical_mixer", "create:mechanical_crafter", "create:steam_engine",
    "mekanism:metallurgic_infuser", "mekanism:enrichment_chamber", "mekanism:basic_energy_cube",
    "ae2:charger", "ae2:inscriber", "ae2:controller", "refinedstorage:controller",
    "ars_nouveau:novice_spell_book", "ars_nouveau:enchanting_apparatus",
    "thermal:machine_furnace", "thermal:machine_pulverizer",
}


def _known(scan):
    items = set(scan.get("vanilla") or ())
    for values in (scan.get("items") or {}).values():
        items.update(values)
    return items - set(scan.get("unobtainable") or ())


def _item_id(value):
    return value.get("id") if isinstance(value, dict) else value if isinstance(value, str) else None


def _key(*parts):
    return "shop_" + hashlib.sha256("/".join(map(str, parts)).encode()).hexdigest()[:20]


def _ftb_id(*parts):
    value = int.from_bytes(hashlib.sha256("\x1f".join(parts).encode()).digest()[:8], "big")
    return "%016X" % ((value & ((1 << 63) - 1)) or 1)


def _exported_quest_id(quest, chapter, opts):
    key = str(quest.get("id") or quest.get("title") or "")
    if chapter.get("_native_reference") or (opts.get("preserve_ids") and re.fullmatch(r"[0-9A-Fa-f]{16}", key)):
        return "%016X" % ((int(key, 16) & ((1 << 63) - 1)) or 1)
    chapter_key = str(chapter.get("id") or chapter.get("title") or "")
    return _ftb_id("chapter/" + chapter_key + "/" + key)


def _bonus_id(quest_id, attempt=0):
    digest = hashlib.sha256(("autoquestgen/shop/currency/%s/%d" % (quest_id, attempt)).encode()).digest()
    return "%016X" % (int.from_bytes(digest[:8], "big") & ((1 << 63) - 1))


def _native_reward_id(quest_id, used):
    """Native reference chapters keep rewards verbatim, so bonuses need IDs now."""
    attempt = 0
    while True:
        candidate = _bonus_id(quest_id, attempt)
        if int(candidate, 16) and candidate not in used:
            used.add(candidate)
            return candidate
        attempt += 1


def _existing_object_ids(value):
    if isinstance(value, dict):
        own = {str(value["id"]).upper()} if "id" in value else set()
        return own.union(*(_existing_object_ids(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_existing_object_ids(v) for v in value))
    return set()


def _label(item, scan):
    label = (scan.get("names") or {}).get(item)
    if label and not re.search(r"%(?:\d+\$)?[sdif]", str(label)):
        return str(label)
    return item.split(":", 1)[-1].replace("_", " ").title()


def _is_exported_shop(chapter):
    """Recognize only our stable exported chapter and complete native offers."""
    seed = "chapter/" + _key("chapter")
    if str(chapter.get("id", "")).upper() != _ftb_id(seed):
        return False
    quests = chapter.get("quests") or []
    if not quests or len(quests) > len(_OFFERS):
        return False
    offered = {"minecraft:" + row[0]: row for row in _OFFERS}
    seen = set()
    for quest in quests:
        tasks, rewards = quest.get("tasks") or [], quest.get("rewards") or []
        if (not quest.get("optional") or not quest.get("can_repeat") or quest.get("repeat_cooldown", 0) != 0
                or len(tasks) != 1 or len(rewards) != 1):
            return False
        task, reward = tasks[0], rewards[0]
        item = _item_id(reward.get("item"))
        row = offered.get(item)
        if row is None or item in seen:
            return False
        seen.add(item)
        if str(quest.get("id", "")).upper() != _ftb_id(seed + "/" + _key("offer", item)):
            return False
        if (task.get("type") != "item" or _item_id(task.get("item")) != CURRENCY
                or task.get("consume_items") is not True or task.get("task_screen_only", False)
                or task.get("count", 1) not in {max(1, math.ceil(row[2] * m)) for m in (.5, 1, 1.5)}
                or reward.get("type") != "item" or reward.get("count", 1) != row[1]
                or reward.get("team_reward") is not True or reward.get("auto") != "disabled"):
            return False
    return True


def has_generated_shop(doc):
    """Cheap build hook, including a shop read back without private metadata."""
    return bool(isinstance(doc.get("shop"), dict) and doc["shop"].get("generator") == _GENERATOR
                or any(_is_exported_shop(c) for c in doc.get("chapters", [])))


def _is_exported_bonus(reward, quest_id):
    if (reward.get("type") != "item" or _item_id(reward.get("item")) != CURRENCY
            or reward.get("team_reward") is not True or reward.get("auto") != "disabled"):
        return False
    key = str(reward.get("id", "")).upper()
    # Normal allocation uses attempt zero; bounded extra slots recognize the
    # deterministic collision repair without accepting arbitrary emerald pay.
    return bool(re.fullmatch(r"[0-7][0-9A-F]{15}", key)
                and key in {_bonus_id(str(quest_id).upper(), n) for n in range(32)})


def _remove_previous(result):
    previous = result.get("shop")
    owned = isinstance(previous, dict) and previous.get("generator") == _GENERATOR
    imported_chapters = [c for c in result.get("chapters", []) if _is_exported_shop(c)]
    if not owned and not imported_chapters:
        return
    if owned:
        result.pop("shop", None)
    introduced_reward_fields = {row["quest_id"] for row in previous.get("currency_awards", [])
                                if row.get("created_rewards_field")} if owned else set()
    result["chapters"] = [chapter for chapter in result.get("chapters", [])
                          if not (owned and chapter.get("_shop") == _GENERATOR)
                          and not any(chapter is imported for imported in imported_chapters)]
    for chapter in result["chapters"]:
        for quest in chapter.get("quests", []):
            if "rewards" in quest:
                quest["rewards"] = [r for r in quest["rewards"]
                                    if not (owned and r.get("_shop_bonus") == _GENERATOR)
                                    and not (imported_chapters and _is_exported_bonus(r, quest.get("id")))]
                if not quest["rewards"] and quest.get("id") in introduced_reward_fields:
                    quest.pop("rewards")


def _milestones(result, scan, known):
    records = [(q, c) for c in result.get("chapters", []) for q in c.get("quests", [])]
    by_id = {str(q["id"]): q for q, _ in records if q.get("id")}
    task_owners = {str(t["id"]): str(q["id"]) for q, _ in records if q.get("id")
                   for t in q.get("tasks", []) if t.get("id")}
    selected = set()
    early_items = known & _EARLY_ACHIEVEMENTS
    for q, _ in records:
        if any(_item_id(t.get("item")) in early_items for t in q.get("tasks", [])):
            selected.add(str(q.get("id", "")))
    campaign = result.get("campaign") or {}
    selected.update(str(v) for name in ("goal_quest_ids", "branch_milestone_ids") for v in campaign.get(name, []))
    focus = {m["mod_id"] for m in scan.get("mods", []) if m.get("mod_id") != "minecraft" and m.get("mod_id")}
    if focus:
        from quest_campaign import _campaign_milestones
        selected.update(row[1] for row in _campaign_milestones(records, scan, focus))
    selected = {key for key in selected if key in by_id and not by_id[key].get("can_repeat")
                and not by_id[key].get("optional")}
    depths = {}

    def depth(key, visiting=frozenset()):
        if key not in depths:
            if key in visiting:
                raise ValueError("Shop cannot use a quest book with circular prerequisites")
            parents = [task_owners.get(str(p), str(p)) for p in by_id.get(key, {}).get("dependencies", [])]
            depths[key] = 1 + max((depth(p, visiting | {key}) for p in parents if p in by_id), default=-1)
        return depths[key]

    return [by_id[key] for key in sorted(selected, key=lambda key: (depth(key), key))]


def apply_shop(doc: dict, scan: dict, opts: dict | None = None) -> dict:
    """Copy a book and optionally add a native emerald barter chapter.

    Apply after campaign/scenario generation, before cleaning/export. The shop
    only depends on existing achievements; the original graph never gains an
    edge back to a purchase. Reapplying replaces only this module's own chapter
    and marked bonus rewards, so repeated calls cannot multiply the economy.
    """
    opts = opts or {}
    result = copy.deepcopy(doc)
    _remove_previous(result)
    if not opts.get("shop_enabled", False):
        return result
    style, prices = opts.get("shop_style", "balanced"), opts.get("shop_prices", "normal")
    if style not in SHOP_STYLES or prices not in SHOP_PRICES:
        raise ValueError("Choose a supported shop style and price level")
    known = _known(scan)
    if CURRENCY not in known:
        raise ValueError("The shop needs verified obtainable minecraft:emerald in this pack")
    originals = [q for c in result.get("chapters", []) for q in c.get("quests", [])]
    protected = {_item_id(t.get("item")) for q in originals for t in q.get("tasks", [])}
    protected.update((result.get("campaign") or {}).get("exact_goal_ids", []))
    protected.discard(None)
    milestones = _milestones(result, scan, known)
    gates = {0: None}
    if milestones:
        gates[1] = milestones[0]
    if len(milestones) > 1:
        gates[2] = milestones[max(1, len(milestones) // 2)]
    multiplier = {"low": 0.5, "normal": 1, "high": 1.5}[prices]
    offers, excluded, unavailable = [], [], []
    for leaf, quantity, price, tier, styles in _OFFERS:
        item = "minecraft:" + leaf
        if style != "balanced" and style not in styles.split():
            continue
        if item in protected:
            excluded.append(item)
            continue
        if item not in known:
            unavailable.append(item)
            continue
        if tier not in gates:
            continue  # Do not invent a progress gate for a book without one.
        cost = max(1, math.ceil(price * multiplier))
        name = _label(item, scan)
        gate = gates[tier]
        offers.append({"id": _key("offer", item), "title": "%d × %s" % (quantity, name),
            "subtitle": "%d emerald%s · supply shop" % (cost, "" if cost == 1 else "s"),
            "description": ["Submit %d emerald%s to buy %d × %s. Payment is consumed."
                            % (cost, "" if cost == 1 else "s", quantity, name),
                            "Click the emerald task to submit payment, then claim the item reward. The offer resets after claiming.",
                            "If you submit fewer emeralds than the price, they remain as a partial payment until you pay the rest.",
                            "One purchase gives one reward to your team. Buying is optional and never required to finish the campaign."],
            "icon": item, "optional": True, "can_repeat": True, "repeat_cooldown": 0,
            "dependencies": [gate["id"]] if gate else [], "shape": "rsquare", "size": 1.0,
            "x": float((len(offers) % 5) * 3), "y": float((len(offers) // 5) * 3),
            "tasks": [{"type": "item", "item": CURRENCY, "count": cost, "consume_items": True}],
            "rewards": [{"type": "item", "item": item, "count": quantity,
                         "team_reward": True, "auto": "disabled"}],
            "_shop_offer": _GENERATOR, "_shop_tier": tier})
    if not offers:
        raise ValueError("No safe shop supplies remain after checking this pack and protecting quest objectives")

    awards = []
    used_reward_ids = _existing_object_ids(result)
    exported_quest_ids = {str(q.get("id")): _exported_quest_id(q, c, opts)
                          for c in result.get("chapters", []) for q in c.get("quests", [])}
    # Keep bonus income bounded even in a book with thousands of quests. Spread
    # selected milestones across progress, rather than paying only early rows.
    indices = sorted({round(i * (len(milestones) - 1) / 23) for i in range(min(24, len(milestones)))}) if len(milestones) > 24 else range(len(milestones))
    for index in indices:
        quest = milestones[index]
        count = 1 + min(2, index * 3 // max(1, len(milestones)))
        created_rewards_field = "rewards" not in quest
        quest.setdefault("rewards", []).append({"id": _native_reward_id(exported_quest_ids[str(quest["id"])], used_reward_ids),
                                               "type": "item", "item": CURRENCY, "count": count,
                                               "team_reward": True, "auto": "disabled", "_shop_bonus": _GENERATOR})
        awards.append({"quest_id": quest["id"], "count": count, "created_rewards_field": created_rewards_field})
    chapter_id = _key("chapter")
    if any(str(c.get("id")) == chapter_id or str(c.get("id", "")).upper() == _ftb_id("chapter/" + chapter_id)
           for c in result.get("chapters", [])):
        raise ValueError("An existing chapter already uses the generated shop ID")
    description = ["Trade emeralds for useful supplies. Every purchase is optional.",
                   "Click an offer's emerald task to pay, then claim its item reward. Your team can buy it again afterward."]
    if awards:
        description.append("Earn bonus emeralds by completing selected milestones elsewhere in this book. Later shelves unlock after real achievements.")
    else:
        description.append("Bring emeralds you obtain through normal play. This small book has no suitable milestones for extra shop rewards.")
    result.setdefault("chapters", []).append({"id": chapter_id, "title": "Supply Shop", "group": "Shop",
        "description": description, "icon": CURRENCY, "quests": offers, "_shop": _GENERATOR})
    result["shop"] = {"generator": _GENERATOR, "schema": 1, "currency": CURRENCY, "style": style, "prices": prices,
                      "chapter_id": chapter_id, "offer_count": len(offers), "currency_awards": awards,
                      "bonus_emeralds": sum(row["count"] for row in awards),
                      "unlocks": {str(tier): gate["id"] if gate else None for tier, gate in gates.items()},
                      "protected_objectives": sorted(excluded), "unavailable_supplies": sorted(unavailable)}
    return result
