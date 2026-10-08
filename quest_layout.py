"""Deterministic, dependency-aware quest maps. No content or graph mutations.

The map follows actual prerequisite branches. In particular, sorting by rank
alone is insufficient: two parallel recipe chains must retain their own lanes.
"""
from __future__ import annotations

import collections
import hashlib
import math
import random


GAP = 2.45
STEP = 2.9


def _graph(quests):
    ids = [q["id"] for q in quests]
    own = set(ids)
    deps = {q["id"]: list(dict.fromkeys(d for d in q.get("dependencies", [])
                                      if d in own and d != q["id"])) for q in quests}
    kids = {qid: [] for qid in ids}
    for qid, parents in deps.items():
        for parent in parents:
            kids[parent].append(qid)
    return ids, deps, kids


def _ranks(ids, deps, kids):
    pending = {qid: len(deps[qid]) for qid in ids}
    ready = collections.deque(qid for qid in ids if not pending[qid])
    rank = {qid: 0 for qid in ids}
    while ready:
        qid = ready.popleft()
        for child in kids[qid]:
            rank[child] = max(rank[child], rank[qid] + 1)
            pending[child] -= 1
            if not pending[child]:
                ready.append(child)
    return rank


def _components(ids, deps, kids):
    unseen = set(ids)
    result = []
    for qid in ids:
        if qid not in unseen:
            continue
        unseen.remove(qid)
        todo, component = [qid], []
        while todo:
            current = todo.pop()
            component.append(current)
            for other in deps[current] + kids[current]:
                if other in unseen:
                    unseen.remove(other)
                    todo.append(other)
        result.append(component)
    return result


def _ordered_layers(ids, deps, kids, rank, rng):
    """Barycentric sweeps keep related branches together at every tier."""
    tie = {qid: rng.random() for qid in ids}
    layers = [[] for _ in range(1 + max(rank[qid] for qid in ids))]
    # Initial depth-first order already groups descendants of each branch.
    seen, order = set(), {}

    def visit(qid):
        if qid in seen:
            return
        seen.add(qid)
        order[qid] = len(order)
        for child in sorted(kids[qid], key=lambda q: (tie[q], q)):
            visit(child)

    for qid in sorted(ids, key=lambda q: (rank[q], tie[q])):
        visit(qid)
    for qid in ids:
        layers[rank[qid]].append(qid)
    for layer in layers:
        layer.sort(key=order.get)
    for _ in range(6):
        for forward in (True, False):
            indices = range(1, len(layers)) if forward else range(len(layers) - 2, -1, -1)
            positions = {q: (i - (len(layer) - 1) / 2)
                         for layer in layers for i, q in enumerate(layer)}
            adjacency = deps if forward else kids
            for depth in indices:
                def bary(q):
                    neighbours = adjacency[q]
                    return (sum(positions[n] for n in neighbours) / len(neighbours)
                            if neighbours else positions[q], order[q])
                layers[depth].sort(key=bary)
                positions.update({q: i - (len(layers[depth]) - 1) / 2
                                  for i, q in enumerate(layers[depth])})
    return layers


def _layered(ids, deps, kids, rank, rng, vertical=False, flowing=False, layer_order=None):
    layers = layer_order if layer_order is not None else _ordered_layers(ids, deps, kids, rank, rng)
    cross = {q: GAP * (i - (len(layer) - 1) / 2)
             for layer in layers for i, q in enumerate(layer)}
    # Compact each ordered row toward its parents and children. Project onto
    # the minimum-gap constraints; unconstrained force pulls collapse rows.
    for _ in range(10):
        for layer in layers:
            if not layer:
                continue
            ideal = []
            for qid in layer:
                near = deps[qid] + kids[qid]
                target = sum(cross[n] for n in near) / len(near) if near else cross[qid]
                ideal.append(0.35 * cross[qid] + 0.65 * target)
            values = list(ideal)
            for i in range(1, len(values)):
                values[i] = max(values[i], values[i - 1] + GAP)
            shift = sum(a - b for a, b in zip(ideal, values)) / len(values)
            for qid, value in zip(layer, values):
                cross[qid] = value + shift
    phase = rng.uniform(-0.5, 0.5)
    pos = {}
    for qid in ids:
        along = rank[qid] * STEP
        across = cross[qid]
        if flowing:
            across += 0.6 * math.sin(rank[qid] * 0.85 + phase)
        pos[qid] = (across, along) if vertical else (along, across)
    return pos


def _radial(ids, deps, kids, rank, rng, arc=math.tau):
    """Allocate a persistent angular sector to each real branch."""
    roots = [q for q in ids if not deps[q]]
    if len(roots) != 1:
        return None
    root = roots[0]
    # A merge-heavy graph reads better as lanes; radial sectors describe trees.
    if sum(len(deps[q]) > 1 for q in ids) > max(2, len(ids) // 8):
        return None
    layers = _ordered_layers(ids, deps, kids, rank, rng)
    seating = {q: i for layer in layers for i, q in enumerate(layer)}
    parent = {q: max(deps[q], key=lambda p: rank[p]) for q in ids if deps[q]}
    tree = {q: [] for q in ids}
    for child, par in parent.items():
        tree[par].append(child)
    weight = {}
    for q in sorted(ids, key=lambda q: rank[q], reverse=True):
        weight[q] = max(1, sum(weight[c] for c in tree[q]))
    if len(tree[root]) < 3:
        return None
    # Per-rank circumference must fit the icons, including enlarged landmarks.
    counts = collections.Counter(rank[q] for q in ids)
    radii = {0: 0.0}
    for depth in range(1, max(counts) + 1):
        radii[depth] = max(radii[depth - 1] + STEP, counts[depth] * GAP / arc)
    spin = rng.uniform(-0.4, 0.4) - (math.pi / 2 if arc == math.tau else arc / 2)
    pos = {root: (0.0, 0.0)}

    def place(qid, start, end):
        children = sorted(tree[qid], key=lambda q: seating[q])
        total = sum(weight[c] for c in children)
        cursor = start
        for child in children:
            stop = cursor + (end - start) * weight[child] / max(1, total)
            angle = (cursor + stop) / 2
            radius = radii[rank[child]]
            pos[child] = (radius * math.cos(angle), radius * math.sin(angle))
            place(child, cursor, stop)
            cursor = stop
    place(root, spin, spin + arc)
    return pos


def _chain(ids, deps, kids, rng, vertical=False):
    """A real long chain can bend without inventing branches or crossings."""
    if len(ids) < 9 or any(len(deps[q]) > 1 or len(kids[q]) > 1 for q in ids):
        return None
    roots = [q for q in ids if not deps[q]]
    if len(roots) != 1:
        return None
    order, q = [], roots[0]
    while q not in order:
        order.append(q)
        if not kids[q]:
            break
        q = kids[q][0]
    width = max(5, int(math.ceil(math.sqrt(len(ids) * 1.5))))
    pos = {}
    for i, qid in enumerate(order):
        row, col = divmod(i, width)
        col = col if row % 2 == 0 else width - 1 - col
        point = (col * STEP, row * STEP)
        pos[qid] = point[::-1] if vertical else point
    return pos


def _bend_long_progression(pos, ids, rank, style, rng):
    """Turn a narrow campaign into an open arc with its branches alongside.

    Rank remains the distance along the route. Only sparse, elongated graphs
    qualify; wide parallel production systems retain their readable lanes.
    """
    depth = max(rank[q] for q in ids)
    rows = collections.Counter(rank[q] for q in ids)
    if (style not in ("lanes", "spine", "spiral", "clusters", "radial", "rosette", "spread")
            or depth < 8 or max(rows.values()) > 4):
        return pos
    low = min(y for x, y in pos.values())
    high = max(y for x, y in pos.values())
    if depth * STEP < max(1, high - low) * 2.7:
        return pos
    centre = (low + high) / 2
    spread = (high - low) / 2
    arc = 4.0
    # Radius leaves room inside the curve for side objectives; an inner lane
    # still gets at least a full icon spacing between successive ranks.
    radius = depth * STEP / arc + spread
    rotate = rng.uniform(-0.15, 0.15)
    result = {}
    for qid, (x, y) in pos.items():
        angle = -math.pi / 2 - arc / 2 + arc * rank[qid] / depth + rotate
        r = radius + y - centre
        result[qid] = (r * math.cos(angle), r * math.sin(angle))
    return result


def _node_sizes(quests):
    sizes = {}
    for q in quests:
        try:
            size = float(q.get("size") or 1.0)
        except (TypeError, ValueError):
            size = 1.0
        sizes[q["id"]] = max(0.25, size) if math.isfinite(size) else 1.0
    return sizes


def _space(pos, min_gap=2.05, sizes=None):
    """Resolve local overlaps, then guarantee clearance after rounding."""
    ids = list(pos)
    sizes = sizes or {}
    for _ in range(100):
        moved = False
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                ax, ay = pos[a]
                bx, by = pos[b]
                dx, dy = bx - ax, by - ay
                distance = math.hypot(dx, dy)
                # Generated maps leave a short readable connection between
                # the actual icon footprints. This is deliberately separate
                # from reference_positions' tightly authored half-grid maps.
                minimum = max(min_gap, (sizes.get(a, 1.0) + sizes.get(b, 1.0)) / 2 + .6)
                if distance >= minimum - 1e-6:
                    continue
                if distance < 1e-6:
                    dx, dy, distance = 1.0, 0.3, math.hypot(1, 0.3)
                    amount = minimum / 2
                else:
                    amount = (minimum - distance) / 2 + 0.0001
                dx, dy = amount * dx / distance, amount * dy / distance
                pos[a], pos[b] = (ax - dx, ay - dy), (bx + dx, by + dy)
                moved = True
        if not moved:
            break
    return pos


def fit_node_sizes(positions, quests, min_gap=2.05):
    """Repair generated-map clearance without changing nodes or dependencies.

    Use after a geometry transform that can shrink icon spacing. Authored
    reference coordinates instead use reference_positions, whose compact
    source composition intentionally has a different clearance rule.
    """
    return _space(dict(positions), min_gap=min_gap, sizes=_node_sizes(quests))


def _relax_long_edges(pos, deps):
    """Settle exceptional dense joins using every real edge simultaneously.

    A joined recipe can span two angular sectors or many vertical lanes. A
    one-sided pull relocates just the endpoint and creates another bad edge;
    balanced springs move the connected neighbourhood together. Ordinary
    lanes and authored maps never take this fallback.
    """
    ids = list(pos)
    edges = [(qid, parent) for qid in ids for parent in deps[qid]]
    xs, ys = zip(*pos.values())
    fanout = collections.Counter(parent for qid, parent in edges)
    relative = max(8.0, 0.45 * math.hypot(max(xs) - min(xs), max(ys) - min(ys)))
    # A very tall tier must not excuse a 30-unit ray from its central hub.
    # Degree, rather than the initial canvas size, determines the extra room
    # a large family physically needs for legible icons.
    limit = min(relative, max(8.0, 1.65 * math.sqrt(max(fanout.values(), default=1))))
    if not any(math.dist(pos[a], pos[b]) > limit for a, b in edges):
        return pos
    origins = dict(pos)
    for iteration in range(180):
        forces = {qid: [0.0, 0.0] for qid in ids}
        for a, b in edges:
            dx, dy = pos[b][0] - pos[a][0], pos[b][1] - pos[a][1]
            distance = math.hypot(dx, dy) or 0.001
            force = 0.075 * max(0.0, distance - 3.3) / distance
            forces[a][0] += dx * force
            forces[a][1] += dy * force
            forces[b][0] -= dx * force
            forces[b][1] -= dy * force
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                dx, dy = pos[b][0] - pos[a][0], pos[b][1] - pos[a][1]
                distance = math.hypot(dx, dy) or 0.001
                if distance >= 3.0:
                    continue
                force = 0.35 * (3.0 - distance) / distance
                forces[a][0] -= dx * force
                forces[a][1] -= dy * force
                forces[b][0] += dx * force
                forces[b][1] += dy * force
        for qid in ids:
            dx, dy = forces[qid]
            dx += (origins[qid][0] - pos[qid][0]) * 0.006
            dy += (origins[qid][1] - pos[qid][1]) * 0.006
            speed = max(1.0, math.hypot(dx, dy) / 0.45)
            pos[qid] = (pos[qid][0] + dx / speed, pos[qid][1] + dy / speed)
    return _space(pos)


def _matrix(ids, deps, kids, rank, rng):
    layers = _ordered_layers(ids, deps, kids, rank, rng)
    order = [q for layer in layers for q in layer]
    width = max(3, int(math.ceil(math.sqrt(len(order)))))
    return {q: ((i % width) * GAP, (i // width) * GAP) for i, q in enumerate(order)}


def _spiral(ids, deps, kids, rank, rng):
    layers = _ordered_layers(ids, deps, kids, rank, rng)
    angle = -math.pi / 2
    pos = {}
    for qid in (q for layer in layers for q in layer):
        radius = 2.2 + 0.56 * (angle + math.pi / 2)
        pos[qid] = (radius * math.cos(angle), radius * math.sin(angle))
        angle += 2.7 / radius
    return pos


def _spine(ids, deps, kids, rank, rng):
    pos = _layered(ids, deps, kids, rank, rng)
    endpoint = max(ids, key=lambda q: rank[q])
    trunk = {endpoint}
    while deps[endpoint]:
        endpoint = max(deps[endpoint], key=lambda q: rank[q])
        if endpoint in trunk:
            break
        trunk.add(endpoint)
    rows = collections.defaultdict(list)
    for q in ids:
        if q in trunk:
            pos[q] = (rank[q] * STEP, 0.0)
        else:
            rows[rank[q]].append(q)
    for depth, row in rows.items():
        row.sort(key=lambda q: pos[q][1])
        for i, q in enumerate(row):
            side = -1 if i % 2 == 0 else 1
            pos[q] = (depth * STEP, side * GAP * (1 + i // 2))
    return pos


def _leaf_groups(pos, kids, full_circle=False):
    """Only terminal siblings move; intermediate nodes retain their routes."""
    for parent, children in kids.items():
        leaves = [q for q in children if not kids[q]]
        if len(leaves) < (3 if full_circle else 2):
            continue
        px, py = pos[parent]
        centre = (sum(pos[q][0] for q in leaves) / len(leaves),
                  sum(pos[q][1] for q in leaves) / len(leaves))
        heading = math.atan2(centre[1] - py, centre[0] - px)
        arc = math.tau if full_circle else math.pi * 0.85
        radius = max(2.8, len(leaves) * GAP / arc)
        for i, q in enumerate(leaves):
            angle = heading - arc / 2 + arc * (i + 0.5) / len(leaves)
            pos[q] = (px + radius * math.cos(angle), py + radius * math.sin(angle))
    return pos


def _clusters(ids, deps, kids, rank, rng):
    pos = _layered(ids, deps, kids, rank, rng)
    family = {}
    for q in sorted(ids, key=lambda q: rank[q]):
        if not deps[q]:
            family[q] = None
        else:
            parent = max(deps[q], key=lambda p: rank[p])
            family[q] = family.get(parent) or (q if len(kids[parent]) > 1 else None)
    branches = collections.defaultdict(list)
    for q, branch in family.items():
        if branch is not None:
            branches[branch].append(q)
    zones = sorted(branches.values(), key=lambda group: sum(pos[q][1] for q in group) / len(group))
    cursor = 0.0
    for group in zones:
        low, high = min(pos[q][1] for q in group), max(pos[q][1] for q in group)
        for q in group:
            pos[q] = (pos[q][0], pos[q][1] - low + cursor)
        cursor += high - low + 5.4
    if zones:
        centre = (cursor - 5.4) / 2
        for q in ids:
            x, y = pos[q]
            pos[q] = (x, y - centre if family[q] is not None else 0.0)
    return _leaf_groups(pos, kids)


def reference_fallback_style(quests, seed=""):
    """Choose an original composition when a chapter has no usable reference."""
    ids, deps, kids = _graph(quests)
    if not ids:
        return "lanes"
    rank = _ranks(ids, deps, kids)
    if max(rank.values()) >= 8 or max(len(kids[q]) for q in ids) >= 8:
        # Broad catalogs need the compact fan/edge settling of lanes. One
        # separate cluster per root child expands a large catalog needlessly.
        return "lanes"
    choice = int.from_bytes(hashlib.sha256(str(seed).encode("utf-8")).digest()[:4], "big")
    return ("lanes", "clusters", "spine")[choice % 3]


def reference_introductions(quests):
    """Broad read-and-confirm gates are chapter introductions, not recipe hubs.

    Their prerequisites remain in the exported book. The reference map can
    suppress only their outgoing decorative rays, leaving every actual item,
    exploration and combat prerequisite visible.
    """
    ids, deps, kids = _graph(quests)
    return {q["id"] for q in quests if not deps[q["id"]]
            and len(kids[q["id"]]) >= 18 and q.get("tasks")
            and all(t.get("type") == "checkmark" for t in q["tasks"])}


def campaign_positions(quests, jitter=0.0, seed=""):
    """Bring achievement branches inward to the campaign's actual finale.

    Campaigns are usually the reverse of an unlock tree: many achievements
    feed collections, collections feed larger joins, then one final goal.
    Ordinary rank lanes put every achievement in one enormous column. A
    reverse tree reserves a sector for each collection and a ring for each
    real prerequisite tier, keeping the largest final objective in the centre.
    Dependencies, including cross-chapter achievement gates, are untouched.
    """
    if not quests:
        return {}
    ids, deps, kids = _graph(quests)
    by_id = {q["id"]: q for q in quests}
    ranks = _ranks(ids, deps, kids)
    endings = [q for q in ids if not kids[q]]
    if not endings:
        return layout(quests, "lanes", jitter, seed)
    final = max(endings, key=lambda q: (float(by_id[q].get("size", 1)), ranks[q]))
    depth, queue = {final: 0}, collections.deque([final])
    while queue:
        current = queue.popleft()
        for parent in deps[current]:
            if parent not in depth:
                depth[parent] = depth[current] + 1
                queue.append(parent)
    # A small recipe or a long chain already has a clear compact route. Open
    # progression has no convergence edges, so its independent objectives
    # must not be made to look like a gated tree either.
    if len(depth) < 9 or len(depth) != len(ids) or max(depth.values()) >= 7:
        return layout(quests, "lanes", jitter, seed)
    tree = {q: [] for q in ids}
    for q in ids:
        if q != final:
            # Shared prerequisites keep their real extra edges. Their visual
            # seat belongs to one closest downstream collection only.
            owner = next(k for k in kids[q] if depth.get(k) == depth[q] - 1)
            tree[owner].append(q)
    weights = {}
    for q in sorted(ids, key=lambda q: depth[q], reverse=True):
        weights[q] = max(1, sum(weights[parent] for parent in tree[q]))
    rng = random.Random(str(seed) + "/campaign")
    phase = -math.pi / 2 + rng.uniform(-.12, .12)
    angles = {final: 0.0}

    def seat(q, start, end):
        total = sum(weights[p] for p in tree[q])
        cursor = start
        for parent in tree[q]:
            stop = cursor + (end - start) * weights[parent] / max(total, 1)
            angles[parent] = (cursor + stop) / 2
            seat(parent, cursor, stop)
            cursor = stop

    seat(final, phase, phase + math.tau)
    radius = {0: 0.0}
    # A narrow sector in one busy material branch must not set the radius of
    # every branch on the map. The top-level ingredients need one clear ring;
    # crowded ingredients farther out can spread locally into compact fans.
    # In the real 120-node Star recipe, one 79-node tier otherwise pushed the
    # whole book out to a 78-unit circle despite most sectors being sparse.
    root_count = max(3, len(tree[final]))
    ring_clearance_limit = max(4.8, min(7.5, 1.95 / (2 * math.sin(math.pi / root_count))))
    for tier in range(1, max(depth.values()) + 1):
        ordered = sorted(angles[q] for q in ids if depth[q] == tier)
        clearance = 0.0
        if len(ordered) > 1:
            gaps = [b - a for a, b in zip(ordered, ordered[1:])]
            gaps.append(math.tau + ordered[0] - ordered[-1])
            clearance = 2.05 / (2 * math.sin(min(gaps) / 2))
        radius[tier] = max(radius[tier - 1] + 3.5, min(clearance, ring_clearance_limit))
    creativity = max(0.0, min(1.0, (float(jitter) - .15) / 1.4))
    pos = {final: (0.0, 0.0)}
    for q in ids:
        if q != final:
            angle = angles[q] + rng.uniform(-.015, .015) * creativity
            distance = radius[depth[q]] + rng.uniform(-.10, .10) * creativity
            pos[q] = (distance * math.cos(angle), distance * math.sin(angle))
    _space(pos, min_gap=1.95, sizes=_node_sizes(quests))
    # The finale remains the map's clear visual centre, even if a rare
    # shared-prerequisite collision required local spacing repair.
    cx, cy = pos[final]
    return {q: (round(x - cx, 3), round(y - cy, 3)) for q, (x, y) in pos.items()}


def _edge_crossings(pos, deps):
    """Count proper intersections; shared endpoints are legitimate joins."""
    edges = [(q, p) for q, parents in deps.items() for p in parents]

    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    crossings = 0
    for i, (a, b) in enumerate(edges):
        for c, d in edges[i + 1:]:
            if len({a, b, c, d}) < 4:
                continue
            pa, pb, pc, pd = pos[a], pos[b], pos[c], pos[d]
            if (orient(pa, pb, pc) * orient(pa, pb, pd) < -1e-8
                    and orient(pc, pd, pa) * orient(pc, pd, pb) < -1e-8):
                crossings += 1
    return crossings


def _virtual_rank_layers(ids, deps, kids, rank, rng):
    """Account for edges which skip tiers while ordering, never as quests.

    A long recipe edge needs a seat in each intermediate tier. Without those
    temporary seats, barycentric ordering puts unrelated branches across its
    path. All temporary nodes are removed before coordinates are assigned.
    """
    expanded = list(ids)
    ds, ks = {q: [] for q in ids}, {q: [] for q in ids}
    ranks = dict(rank)
    for q in ids:
        for p in deps[q]:
            parent = p
            for depth in range(rank[p] + 1, rank[q]):
                dummy = ("layout-only", p, q, depth)
                expanded.append(dummy)
                ranks[dummy], ds[dummy], ks[dummy] = depth, [parent], []
                ks[parent].append(dummy)
                parent = dummy
            ds[q].append(parent)
            ks[parent].append(q)
    return [[q for q in layer if q in deps]
            for layer in _ordered_layers(expanded, ds, ks, ranks, rng)]


def _improve_reference_island(quests, baseline, jitter, seed):
    """Improve dense generated islands only when both geometry guards pass.

    Explicit layout choices and authored reference coordinates bypass this
    helper. Crossing reduction must not buy an enormous map or longer links.
    """
    ids, deps, kids = _graph(quests)
    edge_count = sum(map(len, deps.values()))
    if not 60 <= len(ids) <= 180 or not len(ids) <= edge_count <= 400:
        return baseline
    rank = _ranks(ids, deps, kids)
    virtual_count = sum(max(0, rank[q] - rank[p] - 1) for q in ids for p in deps[q])
    if not 1 <= virtual_count <= 600:
        return baseline
    original_crossings = _edge_crossings(baseline, deps)
    if original_crossings < 30:
        return baseline

    def bounds(pos):
        xs, ys = zip(*pos.values())
        w, h = max(xs) - min(xs) + 2.05, max(ys) - min(ys) + 2.05
        return w * h, max(w, h)

    area, span = bounds(baseline)
    longest = max(math.dist(baseline[q], baseline[p]) for q in ids for p in deps[q])
    creativity = max(0.0, min(1.0, (float(jitter) - .15) / 1.4))
    best, best_score = baseline, (original_crossings, area)
    for attempt in range(6):
        rng = random.Random(str(seed) + "/ordered-island/" + str(attempt))
        layers = _virtual_rank_layers(ids, deps, kids, rank, rng)
        pos = _layered(ids, deps, kids, rank, rng, layer_order=layers)
        if creativity:
            pos = {q: (x + rng.uniform(-.65, .65) * creativity,
                       y + rng.uniform(-.65, .65) * creativity)
                   for q, (x, y) in pos.items()}
        _space(pos)
        # A second settlement uses the already compact branch origins. The
        # initial virtual ordering can otherwise leave a sparse long tail.
        for _ in range(3):
            _relax_long_edges(pos, deps)
        pos = fit_node_sizes(pos, quests)
        candidate_area, candidate_span = bounds(pos)
        if candidate_area > area * 1.6 or candidate_span > span * 1.6:
            continue
        if any(math.dist(pos[q], pos[p]) > max(longest * 1.1, 10.0)
               for q in ids for p in deps[q]):
            continue
        score = (_edge_crossings(pos, deps), candidate_area)
        if score[0] <= original_crossings * .8 and score < best_score:
            best, best_score = pos, score
    return best


def _reference_atlas(quests, introductions, jitter, seed):
    """Seat real recipe branches in compact islands beside an intro card.

    A wide intro fan is often mostly unrelated entry items. Forcing these
    onto one rank or circle makes the entire chapter enormous. Detaching
    only the *visual* intro edges reveals the real recipe components; small
    independent objectives form a collection, with no invented links.
    """
    forest = [dict(q, dependencies=[d for d in q.get("dependencies", [])
                                   if d not in introductions])
              for q in quests if q["id"] not in introductions]
    ids, deps, kids = _graph(forest)
    by_id = {q["id"]: q for q in forest}
    islands, singles = [], []
    for component in _components(ids, deps, kids):
        if len(component) == 1:
            singles.extend(component)
            continue
        island_quests = [by_id[q] for q in component]
        island_seed = str(seed) + "/island/" + component[0]
        pos = layout(island_quests, "lanes", jitter, island_seed)
        pos = _improve_reference_island(island_quests, pos, jitter, island_seed)
        xs, ys = zip(*pos.values())
        if max(ys) - min(ys) > (max(xs) - min(xs)) * 1.2:
            pos = {q: (-y, x) for q, (x, y) in pos.items()}
        compact = {q: (x * 0.8, y * 0.8) for q, (x, y) in pos.items()}
        islands.append(fit_node_sizes(compact, island_quests, min_gap=1.64))
    if singles:
        # A collection without recipe links uses a calm compact grid; it is
        # deliberately not presented as a made-up progression chain.
        columns = max(3, math.ceil(math.sqrt(len(singles) * 1.6)))
        rng = random.Random(str(seed) + "/collection")
        creativity = max(0.0, min(1.0, (float(jitter) - 0.15) / 1.4))
        collection = {q: ((i % columns) * 2.05 + rng.uniform(-.12, .12) * creativity,
                          (i // columns) * 2.05 + rng.uniform(-.12, .12) * creativity)
                      for i, q in enumerate(singles)}
        islands.append(fit_node_sizes(collection, [by_id[q] for q in singles], min_gap=1.64))
    # Pack rectangles, testing several sensible landscape widths. The former
    # shelf layout left a full empty column under each short branch.
    rectangles = []
    for island in islands:
        left = min(x for x, y in island.values())
        top = min(y for x, y in island.values())
        width = max(x for x, y in island.values()) - left + 2.6
        height = max(y for x, y in island.values()) - top + 2.6
        rectangles.append((width, height, {q: (x - left, y - top)
                                          for q, (x, y) in island.items()}))
    rectangles.sort(key=lambda rect: rect[0] * rect[1], reverse=True)
    area = sum(w * h for w, h, p in rectangles)
    minimum = max(w for w, h, p in rectangles)
    best = None
    for factor in (1.05, 1.2, 1.35, 1.5, 1.7, 1.9):
        target = max(minimum, math.sqrt(area) * factor)
        boxes, result = [], {}
        for width, height, island in rectangles:
            candidates = {0.0, *(x + w for x, y, w, h in boxes)}
            choices = []
            for x in candidates:
                if x + width > target + .001:
                    continue
                y = max((by + bh for bx, by, bw, bh in boxes
                         if x < bx + bw - .001 and x + width > bx + .001), default=0.0)
                choices.append((y + height, y, x))
            _, y, x = min(choices)
            boxes.append((x, y, width, height))
            result.update({q: (x + a, y + b) for q, (a, b) in island.items()})
        width = max(x + w for x, y, w, h in boxes)
        height = max(y + h for x, y, w, h in boxes)
        score = width * height * (1 + .45 * abs(math.log((width / height) / 1.45)))
        if best is None or score < best[0]:
            best = (score, result, width)
    _, result, width = best
    for i, q in enumerate(q for q in quests if q["id"] in introductions):
        result[q["id"]] = ((width - 2.6) / 2 + i * 3.0, -3.4)
    result = fit_node_sizes(result, quests, min_gap=1.64)
    cx = (min(x for x, y in result.values()) + max(x for x, y in result.values())) / 2
    cy = (min(y for x, y in result.values()) + max(y for x, y in result.values())) / 2
    return {q: (round(x - cx, 3), round(y - cy, 3)) for q, (x, y) in result.items()}


def layout(quests, style="lanes", jitter=0.0, seed=""):
    if not quests:
        return {}
    if style == "reference":
        introductions = reference_introductions(quests)
        if introductions:
            return _reference_atlas(quests, introductions, jitter, seed)
        style = reference_fallback_style(quests, seed)
    ids, deps, kids = _graph(quests)
    rank = _ranks(ids, deps, kids)
    sizes = _node_sizes(quests)
    rng = random.Random(seed)
    components = _components(ids, deps, kids)
    maps = []
    for component in components:
        cids = set(component)
        ds = {q: [p for p in deps[q] if p in cids] for q in component}
        ks = {q: [c for c in kids[q] if c in cids] for q in component}
        vertical = style == "tree"
        pos = None
        if style == "matrix":
            pos = _matrix(component, ds, ks, rank, rng)
        elif style == "spiral":
            pos = _spiral(component, ds, ks, rank, rng)
        elif style == "spine":
            pos = _spine(component, ds, ks, rank, rng)
        elif style == "clusters":
            pos = _clusters(component, ds, ks, rank, rng)
        elif style in ("lanes", "tree"):
            pos = _chain(component, ds, ks, rng, vertical)
        max_fan = max(len(ks[q]) for q in component)
        shallow_fan = max(rank[q] for q in component) <= 3 and max_fan >= 7
        if pos is None and (style in ("radial", "rosette") or (style == "lanes" and shallow_fan)):
            pos = _radial(component, ds, ks, rank, rng,
                          arc=math.pi * 1.3 if style == "lanes" else math.tau)
        if pos is None:
            pos = _layered(component, ds, ks, rank, rng, vertical,
                           flowing=style in ("lanes", "spread"))
            if style in ("lanes", "radial"):
                pos = _bend_long_progression(pos, component, rank, style, rng)
        if style == "rosette":
            pos = _leaf_groups(pos, ks, full_circle=True)
            # Flower maps start from a petal axis; radial maps start from a
            # spoke axis. A quarter turn keeps their reading direction clear
            # even when a chapter is a single hub with no deeper branches.
            pos = {q: (-y, x) for q, (x, y) in pos.items()}
        if style == "spread":
            angle = math.pi / 10
            pos = {q: (x * math.cos(angle) - y * math.sin(angle) + rng.uniform(-0.7, 0.7),
                       x * math.sin(angle) + y * math.cos(angle) + rng.uniform(-0.7, 0.7))
                   for q, (x, y) in pos.items()}
        # Creativity controls the looseness of the chosen composition. The
        # prior engine accepted this setting but never used it. At zero the
        # geometry is crisp; higher values gently shift nodes without changing
        # prerequisites or removing the final spacing and edge checks.
        creativity = max(0.0, min(1.0, (float(jitter) - 0.15) / 1.4))
        if creativity:
            pos = {q: (x + rng.uniform(-0.65, 0.65) * creativity,
                       y + rng.uniform(-0.65, 0.65) * creativity)
                   for q, (x, y) in pos.items()}
        _space(pos)
        _relax_long_edges(pos, ds)
        _space(pos, sizes=sizes)
        maps.append(pos)
    # Independent objectives form a tidy collection. Larger disconnected
    # branches receive separate islands instead of all sharing rank zero.
    total_area = sum((max(x for x, y in p.values()) - min(x for x, y in p.values()) + 4) *
                     (max(y for x, y in p.values()) - min(y for x, y in p.values()) + 4)
                     for p in maps)
    target_width = max(12.0, math.sqrt(total_area) * 1.4)
    result = {}
    x, y, row_height = 0.0, 0.0, 0.0
    for pos in maps:
        left, top = min(a for a, b in pos.values()), min(b for a, b in pos.values())
        width = max(a for a, b in pos.values()) - left
        height = max(b for a, b in pos.values()) - top
        if x and x + width > target_width:
            x, y, row_height = 0.0, y + row_height + 4.0, 0.0
        for qid, (a, b) in pos.items():
            result[qid] = (x + a - left, y + b - top)
        x += width + 4.0
        row_height = max(row_height, height)
    cx = (min(x for x, y in result.values()) + max(x for x, y in result.values())) / 2
    cy = (min(y for x, y in result.values()) + max(y for x, y in result.values())) / 2
    return {qid: (round(x - cx, 3), round(y - cy, 3)) for qid, (x, y) in result.items()}


def authored_positions(quests):
    """Keep useful authored maps, but repair collapsed or overlapping imports."""
    pos = {q["id"]: (float(q.get("x", 0) or 0), float(q.get("y", 0) or 0)) for q in quests}
    if any(not math.isfinite(v) for p in pos.values() for v in p):
        return None
    if len(set(pos.values())) < max(1, len(pos) * 0.75):
        return None
    ids, deps, _kids = _graph(quests)
    if pos:
        xs, ys = zip(*pos.values())
        diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        if any(math.dist(pos[qid], pos[parent]) > max(12.0, diagonal * 0.65)
               for qid in ids for parent in deps[qid]):
            # Cropping a reference can strand an endpoint far from what is
            # left of its chapter. Retain normal authored routes, rebuild
            # genuinely chapter-spanning outliers with the current graph.
            return None
    for i, qid in enumerate(ids):
        for other in ids[i + 1:]:
            if math.dist(pos[qid], pos[other]) < 1.35:
                return _space(pos, min_gap=1.85)
    return pos


def reference_positions(quests):
    """Preserve a reference's composition; only separate colliding icons.

    Authored reference books sometimes deliberately use long connections.
    Those routes are part of the requested design, so length alone does not
    trigger a new map here. Missing, non-finite or collapsed coordinates do.
    Shapes, sizes, quest order and dependencies are never modified.
    """
    if any("x" not in q or "y" not in q for q in quests):
        return None
    pos = {q["id"]: (float(q["x"]), float(q["y"])) for q in quests}
    if any(not math.isfinite(v) for point in pos.values() for v in point):
        return None
    if len(set(pos.values())) < max(1, len(pos) * 0.75):
        return None
    ids = list(pos)
    sizes = {}
    for q in quests:
        size = float(q.get("size") or 1.0)
        sizes[q["id"]] = max(0.25, size) if math.isfinite(size) else 1.0
    for _ in range(120):
        moved = False
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                ax, ay = pos[a]
                bx, by = pos[b]
                dx, dy = bx - ax, by - ay
                distance = math.hypot(dx, dy)
                # ATM's valid compact grids use diagonal half-grid steps
                # (distance 0.707 at size 1). Preserve that authored spacing;
                # the generated-map clearance rule would destroy the design.
                # This conservative footprint only separates clear collisions.
                minimum = (sizes[a] + sizes[b]) / 2 * 0.70
                if distance >= minimum - 0.0001:
                    continue
                if distance < 0.0001:
                    angle = (i + 1) * 2.399963229728653
                    dx, dy, distance = math.cos(angle), math.sin(angle), 1.0
                    push = minimum / 2
                else:
                    push = (minimum - distance) / 2 + 0.0001
                dx, dy = dx * push / distance, dy * push / distance
                pos[a], pos[b] = (ax - dx, ay - dy), (bx + dx, by + dy)
                moved = True
        if not moved:
            break
    return pos
