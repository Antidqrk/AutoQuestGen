"""Read an installed FTB quest book as a reference without changing it.

This is an exact source-book baseline, not newly generated quest content. The
caller supplies its SNBT parser so this module does not import the application.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path


class ReferenceBookError(ValueError):
    """The requested installed book could not be read completely."""


_LANG_FIELDS = {"quest_desc": "description", "quest_subtitle": "subtitle",
                "chapter_subtitle": "subtitle", "chapter_desc": "description"}


def load_installed_book(instance, parse):
    """Return the installed book with English text resolved and source evidence.

    All parsed fields, including components, conditions, links and image details,
    are retained. ``extras`` retains the original non-core SNBT documents,
    including untranslated language entries and reward tables. No path is written.
    The usual instance path and a direct ``quests`` directory are accepted.
    """
    instance = Path(instance).resolve()
    candidates = [instance / "config/ftbquests/quests", instance / "quests", instance]
    root = next((p for p in candidates if (p / "chapters").is_dir()), None)
    if root is None:
        raise ReferenceBookError("No installed FTB quest book found in " + str(instance))
    chapter_files = sorted((root / "chapters").glob("*.snbt"))
    if not chapter_files:
        raise ReferenceBookError("The installed quest book has no chapter files: " + str(root))
    sources, documents = [], {}
    for path in sorted(root.rglob("*.snbt")):
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        try:
            value = parse(raw.decode("utf-8-sig"))
        except Exception as exc:
            raise ReferenceBookError("Cannot read " + relative + ": " + str(exc)) from exc
        if not isinstance(value, dict):
            raise ReferenceBookError("Expected an SNBT compound in " + relative)
        documents[relative] = value
        sources.append({"path": str(path), "relative_path": relative,
                        "sha256": hashlib.sha256(raw).hexdigest(), "kind": "snbt"})

    # Older books use lang/en_us.snbt; the language splitter uses en_us/**.
    translations, conflicts = {}, []
    for relative, value in documents.items():
        if relative == "lang/en_us.snbt" or relative.startswith("lang/en_us/"):
            for key, text in value.items():
                if key in translations and translations[key] != text:
                    conflicts.append(key)
                translations[key] = text

    by_object = {}
    for key, value in translations.items():
        parts = key.split(".", 2)
        if len(parts) == 3:
            by_object.setdefault((parts[0], parts[1]), {})[parts[2]] = value

    def resolve(obj, kind):
        result = copy.deepcopy(obj)
        ident = str(result.get("id", ""))
        if ident:
            for field, value in by_object.get((kind, ident), {}).items():
                result[_LANG_FIELDS.get(field, field)] = copy.deepcopy(value)
        for field, child_kind in (("quests", "quest"), ("tasks", "task"),
                                  ("rewards", "reward"), ("images", "image")):
            if isinstance(result.get(field), list):
                result[field] = [resolve(child, child_kind) if isinstance(child, dict)
                                 else copy.deepcopy(child) for child in result[field]]
        return result

    chapters = [(path.stem, resolve(documents[path.relative_to(root).as_posix()], "chapter"))
                for path in chapter_files]
    chapters.sort(key=lambda pair: (pair[1].get("order_index", 0), pair[0]))
    group_document = documents.get("chapter_groups.snbt", {})
    groups = [resolve(group, "chapter_group") for group in group_document.get("chapter_groups", [])]
    group_data = copy.deepcopy(group_document)
    group_data["chapter_groups"] = copy.deepcopy(groups)
    data = copy.deepcopy(documents.get("data.snbt", {}))
    # FTB's file object has a reserved ID even when data.snbt omits it.
    for key, value in translations.items():
        if key.startswith("file.0000000000000001."):
            field = key.rsplit(".", 1)[-1]
            data[_LANG_FIELDS.get(field, field)] = copy.deepcopy(value)
    extras = {relative: copy.deepcopy(value) for relative, value in documents.items()
              if not relative.startswith("chapters/") and relative not in
              {"chapter_groups.snbt", "data.snbt"}}
    reward_tables = [(Path(relative).stem, resolve(value, "reward_table"))
                     for relative, value in documents.items() if relative.startswith("reward_tables/")]
    warnings = []
    if conflicts:
        warnings.append("English translation files contain conflicting values for "
                        + str(len(set(conflicts))) + " key(s); the last path in sorted order was used.")
    ids = [str(q.get("id", "")) for _name, chapter in chapters for q in chapter.get("quests", [])]
    if len(ids) != len(set(ids)):
        raise ReferenceBookError("The installed book contains duplicate quest IDs.")
    task_owners = {str(task["id"]): str(quest["id"])
                   for _name, chapter in chapters for quest in chapter.get("quests", [])
                   for task in quest.get("tasks", []) if isinstance(task, dict) and task.get("id")}
    return {"kind": "installed_reference", "source_instance": str(instance), "quest_root": str(root),
            "chapters": chapters, "groups": groups, "group_data": group_data, "data": data, "extras": extras,
            "reward_tables": reward_tables, "sources": sources, "warnings": warnings,
            "translation_count": len(translations), "translation_conflicts": sorted(set(conflicts)),
            "task_owners": task_owners}


def _task_item(task):
    item = task.get("item")
    if isinstance(item, dict):
        return item.get("id") or item.get("item") or ""
    return item if isinstance(item, str) else ""


def goal_ancestry(book, goal_item):
    """Read all goals for an exact item and their cross-chapter prerequisites.

    The result preserves original IDs and fields in copied chapter subsets.
    Unknown prerequisite IDs are reported, never silently substituted. Multiple
    goals remain separate in ``goals`` and are combined in the returned subgraph.
    """
    index, owner, task_owners = {}, {}, {}
    for filename, chapter in book.get("chapters", []):
        for quest in chapter.get("quests", []):
            ident = str(quest.get("id", ""))
            index[ident], owner[ident] = quest, filename
            for task in quest.get("tasks", []):
                if isinstance(task, dict) and task.get("id"):
                    task_owners[str(task["id"])] = ident
    goals = [ident for ident, quest in index.items()
             if any(_task_item(task) == goal_item for task in quest.get("tasks", [])
                    if isinstance(task, dict))]
    selected, missing, edges, task_dependencies = set(), set(), set(), {}
    per_goal = []
    for goal in goals:
        seen, todo = set(), [goal]
        while todo:
            ident = todo.pop()
            if ident in seen:
                continue
            seen.add(ident)
            if ident not in index:
                missing.add(ident)
                continue
            for parent in index[ident].get("dependencies", []):
                parent = str(parent)
                edges.add((parent, ident))
                if parent in task_owners:
                    # FTB permits a dependency on one task rather than its
                    # entire quest. Include that quest for context, retaining
                    # the exact task reference and its narrower meaning.
                    task_dependencies[parent] = task_owners[parent]
                    todo.append(task_owners[parent])
                else:
                    todo.append(parent)
        ancestors = sorted((seen & index.keys()) - {goal})
        per_goal.append({"id": goal, "chapter": owner[goal], "title": index[goal].get("title", ""),
                         "ancestor_ids": ancestors, "quest": copy.deepcopy(index[goal])})
        selected.update(seen & index.keys())
    chapters = []
    for filename, chapter in book.get("chapters", []):
        quests = [copy.deepcopy(q) for q in chapter.get("quests", []) if str(q.get("id")) in selected]
        if quests:
            subset = copy.deepcopy(chapter)
            subset["quests"] = quests
            chapters.append((filename, subset))
    return {"goal_item": goal_item, "goals": per_goal, "quest_ids": sorted(selected),
            "chapters": chapters, "edges": [list(edge) for edge in sorted(edges)],
            "missing_dependencies": sorted(missing),
            "task_dependencies": task_dependencies,
            "cross_chapter_edges": [list(edge) for edge in sorted(edges)
                                    if task_owners.get(edge[0], edge[0]) in owner
                                    and owner[task_owners.get(edge[0], edge[0])] != owner.get(edge[1])]}


def installed_goal_blueprint(book, goal_item):
    """Return one closed source chapter as an explicitly copied local blueprint.

    The earliest matching chapter is the main goal; a later automation or
    creative continuation must not replace it. If that chapter needs outside
    quest/task references, return ``None``. Nothing is generated, pruned,
    renumbered or relaid out here. The caller must label this source-derived
    option distinctly from independent generation and preserve its provenance.
    """
    candidates = []
    for position, (filename, chapter) in enumerate(book.get("chapters", [])):
        for quest in chapter.get("quests", []):
            if any(_task_item(task) == goal_item for task in quest.get("tasks", [])
                   if isinstance(task, dict)):
                candidates.append((chapter.get("order_index", position), position,
                                   filename, chapter, quest))
                break
    if not candidates:
        return None
    _order, _position, filename, chapter, goal = min(candidates, key=lambda row: row[:2])
    quests = chapter.get("quests", [])
    local = {str(q.get("id", "")) for q in quests}
    local.update(str(task["id"]) for quest in quests for task in quest.get("tasks", [])
                 if isinstance(task, dict) and task.get("id"))
    for obj in [chapter] + quests:
        if any(str(parent) not in local for parent in obj.get("dependencies", [])):
            return None
    for link in chapter.get("quest_links", []):
        if isinstance(link, dict):
            linked = link.get("linked_quest") or link.get("quest")
            if linked is not None and str(linked) not in local:
                return None
    chapter_source = next((s for s in book.get("sources", [])
                           if s.get("relative_path") == "chapters/" + filename + ".snbt"), {})
    provenance = {"kind": "installed_goal_blueprint", "copied_source_blueprint": True,
                  "label": "Installed quest book blueprint", "source_instance": book.get("source_instance"),
                  "quest_root": book.get("quest_root"), "source_chapter": chapter_source.get("path"),
                  "source_sha256": chapter_source.get("sha256"), "chapter_id": chapter.get("id"),
                  "chapter_title": chapter.get("title", ""), "goal_id": goal.get("id"),
                  "goal_item": goal_item, "quest_count": len(quests),
                  "dependency_count": sum(len(q.get("dependencies", [])) for q in quests),
                  "image_count": len(chapter.get("images", []))}
    return {"filename": filename, "chapter": copy.deepcopy(chapter), "provenance": provenance}
