"""Read a deliberately bounded subset of pack-authored KubeJS recipe literals.

No JavaScript is executed. A recipe whose ingredients depend on variables,
interpolation, unsupported builder methods or conditional control flow is
reported as skipped, never approximated into a cheaper physical recipe.
"""
from dataclasses import dataclass
from itertools import product
from pathlib import Path
import copy
import json
import re
import tomllib
import zipfile


_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_STACK = re.compile(r"(?:(\d+)\s*x\s*)?(#?[a-z0-9_.-]+:[a-z0-9_./-]+)(.*)", re.S)
_LIMITATIONS = [
    "Only literal modern_industrialization builder chains and shaped/shapeless crafting calls are imported.",
    "JavaScript is never run; dynamic values, control-flow branches and unsupported builder methods are skipped.",
    "This is not a complete KubeJS interpreter; removals, replacements and other recipe APIs are not applied.",
    "Item component suffixes are preserved exactly, not normalized or treated as equivalent to plain items.",
]


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    line: int


def _tokens(text):
    """Tokenize strings/comments without mistaking their contents for calls."""
    result = []
    i, line = 0, 1
    while i < len(text):
        c = text[i]
        if c.isspace():
            line += c == "\n"
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i + 2)
            i = len(text) if end < 0 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("Unterminated block comment")
            line += text[i:end + 2].count("\n")
            i = end + 2
            continue
        if c in "'\"`":
            quote, start_line, value, dynamic = c, line, [], False
            i += 1
            while i < len(text) and text[i] != quote:
                if quote == "`" and text.startswith("${", i):
                    dynamic = True
                if text[i] == "\\":
                    i += 1
                    if i >= len(text):
                        raise ValueError("Unterminated string escape")
                    escaped = text[i]
                    if escaped in "ux":
                        size = 4 if escaped == "u" else 2
                        digits = text[i + 1:i + 1 + size]
                        if len(digits) != size or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                            dynamic = True
                        else:
                            value.append(chr(int(digits, 16)))
                            i += size
                    elif escaped == "\n":
                        line += 1
                    elif escaped == "\r":
                        if i + 1 < len(text) and text[i + 1] == "\n":
                            i += 1
                        line += 1
                    else:
                        value.append({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f",
                                      "v": "\v", "0": "\0"}.get(escaped, escaped))
                else:
                    if text[i] == "\n":
                        line += 1
                    value.append(text[i])
                i += 1
            if i >= len(text):
                raise ValueError("Unterminated string literal")
            result.append(_Token("dynamic" if dynamic else "string", "".join(value), start_line))
            i += 1
            continue
        match = _IDENT.match(text, i) or _NUMBER.match(text, i)
        if match:
            word = match.group()
            result.append(_Token("identifier" if _IDENT.fullmatch(word) else "number", word, line))
            i = match.end()
        else:
            result.append(_Token("punct", c, line))
            i += 1
    return result


def _call(tokens, start):
    """Return argument token lists and the token after the closing parenthesis."""
    if start >= len(tokens) or tokens[start].value != "(":
        raise ValueError("Expected a method call")
    args, current, stack = [], [], [")"]
    pairs = {"(": ")", "[": "]", "{": "}"}
    for i in range(start + 1, len(tokens)):
        token = tokens[i]
        if token.kind == "punct":
            if token.value in pairs:
                stack.append(pairs[token.value])
            elif token.value in (")", "]", "}"):
                if token.value != stack.pop():
                    raise ValueError("Mismatched recipe delimiters")
                if not stack:
                    if current or args:
                        args.append(current)
                    return args, i + 1
            elif token.value == "," and len(stack) == 1:
                args.append(current)
                current = []
                continue
        current.append(token)
    raise ValueError("Unterminated method call")


def _literal(args):
    if len(args) != 1 or len(args[0]) != 1 or args[0][0].kind != "string":
        raise ValueError("Dynamic or nonliteral stack argument")
    return args[0][0].value


def _stack(literal, output=False):
    raw = literal.strip()
    match = _STACK.fullmatch(raw)
    if not match:
        raise ValueError("Invalid literal item or fluid identifier")
    count, item, components = match.groups()
    count = int(count or 1)
    components = components.strip()
    if count <= 0 or count > 2147483647:
        raise ValueError("Stack quantity is outside the supported integer range")
    if output and item.startswith("#"):
        raise ValueError("A recipe output cannot be a tag")
    if item == "ftbquests:missing_item":
        raise ValueError("Recipe requires a missing-item placeholder")
    if components and not ((components.startswith("[") and components.endswith("]"))
                           or (components.startswith("{") and components.endswith("}"))):
        raise ValueError("Unsupported trailing stack expression")
    return {"id": item, "count": count, "components": components,
            "identity": item + components, "raw": raw}


def _split(tokens, delimiter=","):
    parts, current, stack = [], [], []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for token in tokens:
        if token.kind == "punct":
            if token.value == delimiter and not stack:
                parts.append(current)
                current = []
                continue
            if token.value in pairs:
                stack.append(pairs[token.value])
            elif token.value in (")", "]", "}"):
                if not stack or token.value != stack.pop():
                    raise ValueError("Mismatched literal delimiters")
        current.append(token)
    if stack:
        raise ValueError("Unterminated literal expression")
    if current:
        parts.append(current)
    return parts


def _array(tokens):
    if len(tokens) < 2 or tokens[0].value != "[" or tokens[-1].value != "]":
        raise ValueError("Expected a literal array")
    return _split(tokens[1:-1])


def _ingredient_options(tokens, loaded_mods=None, output=False, depth=0):
    """Expand literal OR choices; recognize only one explicit mod-loaded guard."""
    if depth > 16 or not tokens:
        raise ValueError("Unsupported or empty ingredient expression")
    if len(tokens) == 1 and tokens[0].kind == "string":
        return [_stack(tokens[0].value, output)]
    if tokens[0].value == "[":
        if output:
            raise ValueError("An output must be a single item stack")
        return [stack for part in _array(tokens)
                for stack in _ingredient_options(part, loaded_mods, depth=depth + 1)]
    if len(tokens) >= 5 and [t.value for t in tokens[:4]] == ["Platform", ".", "isLoaded", "("]:
        args, end = _call(tokens, 3)
        mod = _literal(args)
        if loaded_mods is None:
            raise ValueError("Mod-loaded condition needs installed mod metadata")
        if end >= len(tokens) or tokens[end].value != "?":
            raise ValueError("Unsupported mod-loaded expression")
        branches = _split(tokens[end + 1:], ":")
        if len(branches) != 2:
            raise ValueError("Unsupported conditional ingredient expression")
        return _ingredient_options(branches[0 if mod in loaded_mods else 1], loaded_mods, output, depth + 1)
    if len(tokens) >= 5 and tokens[0].value in {"Item", "Ingredient"} and [t.value for t in tokens[1:4]] == [".", "of", "("]:
        wrapper = tokens[0].value
        if output and wrapper != "Item":
            raise ValueError("An Ingredient cannot serve as an ItemStack output")
        args, end = _call(tokens, 3)
        if len(args) not in {1, 2}:
            raise ValueError("Unsupported stack wrapper arguments")
        options = _ingredient_options(args[0], loaded_mods, output, depth + 1)
        if len(args) == 2:
            if len(args[1]) != 1 or args[1][0].kind != "number" or not args[1][0].value.isdigit():
                raise ValueError("Dynamic stack quantity")
            count = int(args[1][0].value)
            if not 0 < count <= 2147483647:
                raise ValueError("Invalid stack quantity")
            options = [dict(stack, count=count, raw=str(count) + "x " + stack["identity"]) for stack in options]
        while end < len(tokens):
            if end + 2 >= len(tokens) or [t.value for t in tokens[end:end + 3]] != [".", "or", "("] or output:
                raise ValueError("Unsupported ingredient wrapper method")
            args, end = _call(tokens, end + 2)
            if len(args) != 1:
                raise ValueError("Unsupported OR ingredient arguments")
            options += _ingredient_options(args[0], loaded_mods, depth=depth + 1)
        return options
    raise ValueError("Dynamic or unsupported ingredient expression")


def _crafting_variants(method, args, loaded_mods):
    expected = 3 if method == "shaped" else 2
    if len(args) != expected:
        raise ValueError("Wrong crafting argument count")
    outputs = _ingredient_options(args[0], loaded_mods, output=True)
    if len(outputs) != 1:
        raise ValueError("Crafting needs one literal output stack")
    choices, multipliers = [], []
    if method == "shaped":
        rows = [_literal([row]) for row in _array(args[1])]
        if not rows or len(rows) > 3 or not 0 < len(rows[0]) <= 3 or any(len(row) != len(rows[0]) for row in rows):
            raise ValueError("Invalid crafting-table pattern")
        mapping = args[2]
        if len(mapping) < 2 or mapping[0].value != "{" or mapping[-1].value != "}":
            raise ValueError("Expected a literal pattern key object")
        keys = {}
        for part in _split(mapping[1:-1]):
            if len(part) < 3 or part[0].kind not in {"identifier", "string"} or part[1].value != ":":
                raise ValueError("Dynamic pattern key")
            key = part[0].value
            if len(key) != 1 or key == " " or key in keys:
                raise ValueError("Invalid or duplicate pattern key")
            options = _ingredient_options(part[2:], loaded_mods)
            if any(stack["count"] != 1 for stack in options):
                raise ValueError("Shaped recipe inputs must have a count of one per slot")
            keys[key] = options
        pattern = "".join(rows)
        if set(pattern) - {" "} != set(keys):
            raise ValueError("Pattern symbols and ingredient keys do not match")
        for key in dict.fromkeys(pattern.replace(" ", "")):
            choices.append(keys[key])
            multipliers.append(pattern.count(key))
    else:
        for part in _array(args[1]):
            choices.append(_ingredient_options(part, loaded_mods))
            multipliers.append(1)
    combinations = 1
    for options in choices:
        combinations *= len(options)
    if not choices or combinations < 1 or combinations > 64:
        raise ValueError("Crafting alternatives exceed the supported bound")
    recipes = []
    for combination in product(*choices):
        ingredients = [dict(stack, count=stack["count"] * multiplier,
                            raw=str(stack["count"] * multiplier) + "x " + stack["identity"])
                       for stack, multiplier in zip(combination, multipliers)]
        if sum(stack["count"] for stack in ingredients) > 9:
            raise ValueError("Crafting-table inputs exceed nine slots")
        recipes.append({"kind": "minecraft:crafting_" + method, "ingredients": ingredients,
                        "outputs": outputs, "fluids": [], "fluid_outputs": [], "parameters": []})
    return recipes


def _installed_mod_ids(instance):
    """Only metadata is inspected to evaluate Platform.isLoaded literal guards."""
    ids = {"minecraft", "kubejs"}
    for path in sorted((Path(instance) / "mods").glob("*.jar")):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                metadata = next((name for name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml") if name in names), None)
                if metadata:
                    doc = tomllib.loads(z.read(metadata).decode("utf-8"))
                    ids.update(str(mod["modId"]) for mod in doc.get("mods", []) if mod.get("modId"))
                elif "fabric.mod.json" in names:
                    doc = json.loads(z.read("fabric.mod.json"))
                    if doc.get("id"):
                        ids.add(doc["id"])
                    ids.update(item for item in doc.get("provides", []) if isinstance(item, str))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
    return ids


def _conditional_positions(tokens):
    """Mark control-flow bodies rather than assuming each written call runs."""
    marked, frames, statement = set(), [], []
    pairs = {"(": ")", "[": "]", "{": "}"}
    control = {"if", "else", "for", "while", "switch", "catch", "do"}
    for i, token in enumerate(tokens):
        active = any(blocked for _, blocked in frames)
        prefix_control = any(t.kind == "identifier" and t.value in control for t in statement)
        if active or prefix_control:
            marked.add(i)
        if token.kind == "punct" and token.value in pairs:
            blocked = active or (token.value == "{" and prefix_control)
            frames.append((pairs[token.value], blocked))
            if token.value == "{":
                statement = []
        elif token.kind == "punct" and token.value in (")", "]", "}"):
            if frames and frames[-1][0] == token.value:
                frames.pop()
            if token.value == "}":
                statement = []
        elif token.kind == "punct" and token.value == ";":
            statement = []
        else:
            statement.append(token)
    return marked


def parse_recipe_script(text, source="script.js", loaded_mods=None):
    """Return concrete machine recipes and skip reasons for one source string."""
    recipes, skipped = [], []
    try:
        tokens = _tokens(text)
    except ValueError as exc:
        return {"recipes": [], "skipped": [{"source": source, "line": 1, "reason": str(exc)}]}
    conditional = _conditional_positions(tokens)
    i = 0
    while i + 6 < len(tokens):
        crafting = (tokens[i].value == "." and tokens[i + 1].value in {"shaped", "shapeless"}
                    and tokens[i + 2].value == "(")
        # The callback variable may be named anything, but the actual API is bounded.
        machine = (tokens[i].value == "." and tokens[i + 1].value == "recipes"
                and tokens[i + 2].value == "." and tokens[i + 3].value == "modern_industrialization"
                and tokens[i + 4].value == "." and tokens[i + 5].kind == "identifier"
                and tokens[i + 6].value == "(")
        if not (crafting or machine):
            i += 1
            continue
        line, start = tokens[i].line, i
        recipe = {"kind": "modern_industrialization:" + tokens[i + 5].value,
                  "ingredients": [], "fluids": [], "outputs": [], "fluid_outputs": [],
                  "source": source, "line": line, "key": source + ":" + str(line), "id": None}
        reason = "Recipe is inside conditional or iterative control flow" if start in conditional else None
        try:
            if crafting:
                method = tokens[i + 1].value
                args, i = _call(tokens, i + 2)
                variants = _crafting_variants(method, args, loaded_mods)
            else:
                args, i = _call(tokens, i + 6)
                if any(len(arg) != 1 or arg[0].kind != "number" for arg in args):
                    raise ValueError("Dynamic machine recipe parameters")
                recipe["parameters"] = [arg[0].value for arg in args]
            while i + 2 < len(tokens) and tokens[i].value == "." and tokens[i + 1].kind == "identifier":
                method = tokens[i + 1].value
                args, i = _call(tokens, i + 2)
                if not crafting and method in {"itemIn", "itemOut", "fluidIn", "fluidOut"}:
                    target = {"itemIn": "ingredients", "itemOut": "outputs",
                              "fluidIn": "fluids", "fluidOut": "fluid_outputs"}[method]
                    recipe[target].append(_stack(_literal(args), output=method.endswith("Out")))
                elif method == "id":
                    recipe["id"] = _literal(args)
                else:
                    raise ValueError("Unsupported recipe builder method: " + method)
            if reason:
                raise ValueError(reason)
            for variant, fields in enumerate(variants if crafting else [{}]):
                row = copy.deepcopy(recipe)
                row.update(fields)
                if not row["outputs"]:
                    raise ValueError("No literal item output")
                if not row["ingredients"] and not row["fluids"]:
                    raise ValueError("No literal ingredients")
                if len(row["outputs"]) != 1:
                    raise ValueError("Multiple item outputs need explicit downstream support")
                row["output"] = row["outputs"][0]
                row["variant"] = variant
                if crafting:
                    row["key"] += ":choice" + str(variant)
                row["counts"] = {}
                for ingredient in row["ingredients"]:
                    identity = ingredient["identity"]
                    row["counts"][identity] = row["counts"].get(identity, 0) + ingredient["count"]
                recipes.append(row)
        except ValueError as exc:
            skipped.append({"source": source, "line": line, "reason": str(exc)})
            i = max(i, start + 1)
    return {"recipes": recipes, "skipped": skipped}


def load_pack_recipes(instance: Path, loaded_mods=None):
    """Read instance/kubejs/server_scripts recursively; never mutate the pack."""
    instance = Path(instance)
    root = instance / "kubejs" / "server_scripts"
    report = {"recipes": [], "skipped": [], "sourcefiles": [], "limitations": list(_LIMITATIONS)}
    if not root.is_dir():
        return report
    if loaded_mods is None:
        loaded_mods = _installed_mod_ids(instance)
    for path in sorted(root.rglob("*.js")):
        source = path.relative_to(instance).as_posix()
        report["sourcefiles"].append(source)
        try:
            text = path.read_text(encoding="utf-8-sig")
            parsed = parse_recipe_script(text, source, loaded_mods)
        except (OSError, UnicodeError) as exc:
            report["skipped"].append({"source": source, "line": 1, "reason": "Cannot read script: " + str(exc)})
            continue
        report["recipes"].extend(parsed["recipes"])
        report["skipped"].extend(parsed["skipped"])
    return report
