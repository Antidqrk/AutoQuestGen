"""Theme-aware placement of the bundled chapter artwork."""
from __future__ import annotations

from pathlib import Path
import hashlib

from decor_library import (load_catalog, choose_theme, resource_pack_bytes,
                           export_resource_pack, install_resource_pack,
                           validate_installation, PACK_FILENAME)


def chapter_images(key, quests, level, opts, context, make_id):
    catalog = opts.get('_decor_catalog') or load_catalog()
    theme = opts.get('decor_theme', 'auto')
    if theme == 'auto':
        namespaces = set()
        for quest in quests:
            for task in quest.get('tasks') or []:
                item = task.get('item')
                if isinstance(item, dict):
                    item = item.get('id')
                if isinstance(item, str) and ':' in item:
                    namespaces.add(item.split(':')[0])
        theme = choose_theme(context, namespaces)
    assets = catalog.by_theme(theme)
    if not assets:
        raise ValueError('No artwork exists for theme ' + str(theme))
    collection = opts.get('decor_collection', 'classic')
    kinds = {}
    for kind in {a.kind for a in assets}:
        choices = [a for a in assets if a.kind == kind]
        if kind == 'background':
            selected = [a for a in choices if ('epic' in a.keywords) == (collection == 'epic')]
            choices = selected or choices
        choices.sort(key=lambda a: a.id)
        digest = hashlib.sha256((str(key) + '/' + str(opts.get('seed', 0)) + '/' + kind).encode()).digest()
        kinds[kind] = choices[int.from_bytes(digest[:4], 'big') % len(choices)]
    xs = [float(q.get('x') or 0) for q in quests] or [0]
    ys = [float(q.get('y') or 0) for q in quests] or [0]
    cx, cy = (min(xs) + max(xs))/2, (min(ys) + max(ys))/2
    bw, bh = max(xs) - min(xs), max(ys) - min(ys)
    pw, ph = max(bw + 5, 10), max(bh + 5, 8)
    out = []

    def add(kind, x, y, w=None, h=None, alpha=None, order=-10, rotation=0):
        a = kinds.get(kind)
        if a is None:
            return
        if w is None:
            w = h * a.display_width / a.display_height
        if h is None:
            h = w * a.display_height / a.display_width
        out.append(dict(id=make_id('library-art', key, a.id, str(len(out))),
                        image=a.texture, x=round(x, 3), y=round(y, 3),
                        width=round(w, 3), height=round(h, 3), rotation=float(rotation),
                        alpha=a.alpha if alpha is None else int(alpha),
                        order=order, color=0xFFFFFF))

    lavish = int(level.get('motifs', 0)) >= 8
    decorated = int(level.get('field', 0)) > 0
    if opts.get('decor_source') == 'library':
        background = kinds.get('background')
        if background:
            ratio = background.display_width / background.display_height
            w = max(pw, ph * ratio)
            add('background', cx, cy, w=w, alpha=160 if lavish else 128 if decorated else 105, order=-40)
    # Transparent frames keep the center clear. Respect the source aspect ratio.
    frame = kinds.get('frame')
    if frame:
        ratio = frame.display_width / frame.display_height
        w = max(pw, ph * ratio)
        add('frame', cx, cy, w=w, alpha=210 if lavish else 160, order=-8)
    if decorated:
        add('panel', cx, cy, w=max(pw, ph), alpha=40 if lavish else 28, order=-25)
        add('divider', cx, cy - ph/2 - .3, w=min(pw, 11), alpha=210, order=-2)
        add('emblem', cx, cy + ph/2 + .4, h=1.6 if lavish else 1.2, alpha=220, order=-1)
    if lavish:
        add('ornament', cx - pw/2 + .7, cy - ph/2 + .7, h=2.4, alpha=200, order=-3)
        add('ornament', cx + pw/2 - .7, cy + ph/2 - .7, h=2.4, alpha=200, order=-3, rotation=180)
    # Isolated landmarks sit beyond the actual quest bounds. Animated atlas
    # sprites use their frame dimensions, never the full multi-frame sheet.
    if collection == 'epic' and decorated:
        size = opts.get('decor_size', 'mixed')
        large = 4.0 if size == 'large' else 2.0 if size == 'small' else (3.4 if lavish else 2.6)
        add('landmark', cx - pw/2 + .9, cy + ph/2 + .8, h=large, alpha=255, order=-1)
    if opts.get('decor_motion', False):
        size = opts.get('decor_size', 'mixed')
        motion_size = 3.6 if size == 'large' else 1.6 if size == 'small' else (2.8 if lavish else 2.0)
        add('animation', cx + pw/2 - .9, cy - ph/2 - .8, h=motion_size, alpha=255, order=-1)
    return out


def prepare_art_export(quests_dir, chapters, mc_version=''):
    """Read-only validation before any quest files are replaced."""
    textures = [i.get('image', '') for _, c in chapters for i in c.get('images') or []]
    if not any(str(t).startswith('autoquestgen:') for t in textures):
        return None
    catalog = load_catalog()
    catalog.validate_references(textures)
    # This also checks every image and the selected version before writing.
    resource_pack_bytes(mc_version, catalog=catalog)
    target = Path(quests_dir)
    live = (target.name.lower() == 'quests' and target.parent.name.lower() == 'ftbquests'
            and target.parent.parent.name.lower() == 'config')
    instance = target.parent.parent.parent if live else None
    if instance is not None:
        validate_installation(instance, enable=True)
    return catalog, instance, mc_version


def finish_art_export(quests_dir, prepared, log):
    if prepared is None:
        return
    catalog, instance, version = prepared
    if instance is not None:
        result = install_resource_pack(instance, version, enable=True, catalog=catalog)
        log('  bundled artwork installed and enabled: ' + str(result['path']))
    else:
        folder = Path(quests_dir) / 'resourcepacks'
        folder.mkdir(parents=True, exist_ok=True)
        export_resource_pack(folder / PACK_FILENAME, version, catalog=catalog)
        (folder / 'Artwork instructions.txt').write_text(
            'Bundled quest artwork\n\nCopy AutoQuestGen-Art.zip into the Minecraft '
            'instance resourcepacks folder. In Minecraft, open Options > Resource '
            'Packs and enable AutoQuestGen-Art. Distribute this resource pack with '
            'your quest book so players can see its artwork.\n\nThe Generator '
            'installs and enables it automatically when building directly into '
            'an instance config/ftbquests/quests folder. No internet is needed.\n', encoding='utf-8')
        log('  included offline artwork resource pack: ' + str(folder / PACK_FILENAME))
