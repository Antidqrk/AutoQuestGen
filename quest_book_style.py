"""Original, content-aware sidebar presentation for an offline quest book.

Only chapter organization and presentation metadata change. Graphs, raw IDs,
objectives, authored coordinates, and rewards never do. No reference-pack prose
or illustrations are imported by this module.
"""
from __future__ import annotations

from collections import Counter
import copy
import re


GROUP_ORDER = ('Main Questline', 'Tools and Gear', 'Storage', 'Logistics', 'Resources',
               'Tech', 'Power', 'Magic', 'Exploration', 'Nature and Food', 'Building and Utilities', 'Shop')
GROUP_COLORS = {'Main Questline': 'a', 'Tools and Gear': 'e', 'Storage': 'b', 'Logistics': '3',
                'Resources': '6', 'Tech': 'b', 'Power': 'e', 'Magic': 'd', 'Exploration': '6',
                'Nature and Food': 'a', 'Building and Utilities': '7', 'Shop': 'e'}
GROUP_ART_THEMES = {'Main Questline': 'exploration', 'Tools and Gear': 'machinery', 'Storage': 'storage',
                    'Logistics': 'storage', 'Resources': 'exploration', 'Tech': 'machinery', 'Power': 'power',
                    'Magic': 'arcane', 'Exploration': 'exploration', 'Nature and Food': 'farming',
                    'Building and Utilities': 'restoration', 'Shop': 'storage'}
_FORMAT = re.compile(r'(?:&|§)[0-9a-fk-or]', re.I)
_SPACE = re.compile(r'\s+')
_VAGUE = {'new horizons', 'further afield', 'the frontier', 'beyond the horizon', 'expansion',
          'more to discover', 'the next horizon', 'a wider world', 'the wider world', 'miscellany',
          'fresh horizons', 'uncharted territory', 'more possibilities', 'new possibilities'}
_MOD_GROUPS = {
    'Tools and Gear': 'silentgear silentgems tconstruct tinkersconstruct tetra apotheosis apothic_attributes '
                      'artifacts relics travelersbackpack sophisticatedbackpacks simplyswords majruszsdifficulty',
    'Storage': 'ae2 appliedenergistics2 ae2things ae2wtlib advanced_ae extendedae megacells refinedstorage '
               'refinedstorageaddons functionalstorage storagedrawers sophisticatedstorage ironchest toms_storage '
               'colossalchests colossal_chests dankstorage storagecontainers',
    'Logistics': 'pipez modularrouters laserio integrateddynamics integratedtunnels integratedcrafting '
                 'xnet entangled fluxnetworks enderstorage prettypipes computercraft advancedperipherals pylons',
    'Resources': 'allthemodium mysticalagriculture mysticalagradditions productivebees productive_trees productivetrees '
                 'hostilenetworks hostile_neural_networks exnihilosequentia exdeorum excompressum '
                 'apothic_spawners rftoolsbuilder quarries quarryplus',
    'Tech': 'create createaddition createdieselgenerators immersiveengineering mekanism modern_industrialization '
            'industrialforegoing thermal thermalfoundation thermalexpansion pneumaticcraft draconicevolution '
            'rftoolsbase rftoolsutility enderio oritech xycraft railcraft justdirethings gregtech gtceu',
    'Power': 'powah bigreactors extreme_reactors biggerreactors mekanismgenerators generators '
             'solarflux solarfluxreborn compactsolars ironfurnaces energymeters energymeter',
    'Magic': 'botania ars_nouveau ars_elemental ars_instrumentum bloodmagic occultism forbidden_arcanus '
             'irons_spellbooks astralsorcery thaumcraft evilcraft hexerei malum eidolon naturesaura '
             'bewitchment roots rootsclassic mna manabotany mythicbotany mahou_tsukai theurgy apothic_enchanting',
    'Exploration': 'aether twilightforest ad_astra beyond_earth deeperdarker cataclysm alexscaves alexsmobs '
                   'iceandfire iceandfire_ce the_bumblezone undergarden eternal_starlight dimdungeons '
                   'blue_skies lostcities betterend betternether aquamirae',
    'Nature and Food': 'farmersdelight farmersrespite croptopia cookingforblockheads farmingforblockheads '
                       'pamhc2foodcore pamhc2crops pamhc2trees aquaculture forestry vinery beachparty '
                       'brewery candlelight butchercraft nethersdelight endsdelight',
    'Building and Utilities': 'chipped rechiseled supplementaries quark macawsbridges macawsdoors '
                              'mcwbridges mcwdoors mcwwindows mcwroofs mrcrayfishfurniture furniture '
                              'buildinggadgets buildinggadgets2 constructionwand effortlessbuilding',
}
_MOD_LOOKUP = {namespace: group for group, namespaces in _MOD_GROUPS.items() for namespace in namespaces.split()}
_CATEGORY = {'tech': 'Tech', 'magic': 'Magic', 'world': 'Exploration', 'mob': 'Exploration',
             'food': 'Nature and Food', 'decor': 'Building and Utilities', 'utility': 'Building and Utilities'}
_GROUP_ALIASES = {'technology': 'Tech', 'adventure': 'Exploration', 'combat': 'Tools and Gear',
                  'farm and food': 'Nature and Food', 'farming': 'Nature and Food', 'nature': 'Nature and Food',
                  'utility': 'Building and Utilities', 'decoration': 'Building and Utilities',
                  'main goal': 'Main Questline', 'getting started': 'Main Questline', 'starting out': 'Main Questline'}
_TITLE_TOPICS = (
    ('Power', {'reactor', 'reactors', 'turbine', 'turbines', 'generator', 'generators', 'power', 'energy'}),
    ('Storage', {'storage', 'drawer', 'drawers', 'inventory', 'inventories'}),
    ('Logistics', {'logistics', 'pipes', 'routing', 'routers', 'transport', 'transfers'}),
    ('Tools and Gear', {'gear', 'armor', 'armour', 'weapons', 'tools', 'equipment', 'relics', 'artifacts'}),
    ('Nature and Food', {'farming', 'food', 'cooking', 'crops', 'harvest', 'garden', 'husbandry'}),
    ('Exploration', {'nether', 'end', 'exploration', 'adventure', 'dimension', 'dimensions', 'dungeons', 'bosses'}),
    ('Resources', {'resources', 'ores', 'mining', 'quarry', 'quarries'}),
)


def plain_text(value):
    return _SPACE.sub(' ', _FORMAT.sub('', str(value or '')).replace(r'\&', '&')).strip()


def _tokens(value):
    return set(re.findall(r'[a-z0-9]+', plain_text(value).lower().replace('_', ' ')))


def _mod_metadata(scan):
    return {m.get('mod_id'): m for m in (scan or {}).get('mods', ()) if isinstance(m, dict) and m.get('mod_id')}


def _chapter_namespaces(chapter):
    counts = Counter()
    template = chapter.get('_template')
    if isinstance(template, dict) and template.get('mod'):
        counts[str(template['mod'])] += 5
    for quest in chapter.get('quests') or ():
        owned = set()
        for task in quest.get('tasks') or ():
            for field in ('item', 'entity', 'dimension', 'structure', 'advancement'):
                value = task.get(field)
                if isinstance(value, dict): value = value.get('id')
                if isinstance(value, str) and ':' in value: owned.add(value.split(':', 1)[0])
        reference = quest.get('_guide_ref')
        if isinstance(reference, str):
            bits = reference.split(':')
            if len(bits) >= 2: owned.add(bits[1] if bits[0] in {'check', 'kill', 'dimension', 'structure', 'advancement'} else bits[0])
        counts.update(owned)
    return counts


def _is_main(chapter):
    return bool(chapter.get('_campaign') or chapter.get('_campaign_summary') or
                chapter.get('_campaign_role') in {'goal', 'journey', 'finale'} or
                plain_text(chapter.get('group')).lower() in {'main goal', 'main questline', 'getting started', 'starting out'})


def group_for_chapter(chapter, scan=None):
    """Classify a whole chapter without moving a quest to another ID space."""
    if chapter.get('_shop') or plain_text(chapter.get('group')).lower() == 'shop': return 'Shop'
    if _is_main(chapter): return 'Main Questline'
    title = plain_text(chapter.get('title'))
    tokens = _tokens(title)
    if chapter.get('_vanilla') and not tokens & {'nether', 'end'}: return 'Main Questline'
    if tokens & {'welcome', 'introduction'} or title.lower() in {'overworld beginnings', 'first steps'}:
        return 'Main Questline'
    # Specific subchapters (Mekanism Reactors, Apotheosis Gear) take precedence
    # over their broad namespace, while generic shared titles do not.
    for group, words in _TITLE_TOPICS:
        if tokens & words: return group
    metadata = _mod_metadata(scan)
    scores = Counter()
    for namespace, weight in _chapter_namespaces(chapter).items():
        if namespace in {'minecraft', 'forge', 'c', 'ftbquests'}: continue
        group = _MOD_LOOKUP.get(namespace) or _CATEGORY.get(metadata.get(namespace, {}).get('category'))
        if group: scores[group] += weight
    if scores:
        return max(GROUP_ORDER, key=lambda group: scores[group])
    old = plain_text(chapter.get('group'))
    match = next((group for group in GROUP_ORDER if group.lower() == old.lower()), None)
    if match: return match
    if old.lower() in _GROUP_ALIASES: return _GROUP_ALIASES[old.lower()]
    # Ground the final fallback in objective paths, not generated poetic names.
    objective_words = _tokens(' '.join(str(task.get('item', '')) for quest in chapter.get('quests', ())
                                    for task in quest.get('tasks', ())))
    for group, words in _TITLE_TOPICS:
        if objective_words & words: return group
    return 'Building and Utilities'


def _specific_title(chapter, scan):
    title = plain_text(chapter.get('title'))
    lower = re.sub(r'\s+(?:[ivx]+|\d+)$', '', title.lower())
    if lower not in _VAGUE: return title
    metadata = _mod_metadata(scan)
    namespaces = _chapter_namespaces(chapter)
    selected = [namespace for namespace in sorted(namespaces, key=lambda n: (-namespaces[n], n))
                if namespace not in {'minecraft', 'forge', 'c', 'ftbquests'}]
    names = [plain_text(metadata.get(namespace, {}).get('name') or namespace.replace('_', ' ').title())
             for namespace in selected[:2]]
    if not names: return group_for_chapter(chapter, scan)
    result = ' & '.join(names)
    if len(selected) > 2: result += ' and More'
    return result


def prepare_book(doc, style='classic', scan=None):
    """Return a deep copy with an immersive sidebar; classic is a no-op copy."""
    result = copy.deepcopy(doc)
    if style == 'classic': return result
    if style != 'immersive': raise ValueError('Unknown quest book style: ' + str(style))
    chapters = result.get('chapters') or []
    result['_book_style'] = 'immersive'
    # Authored scenario stage order is itself the story. It must not become a
    # category-sorted mod catalog merely because its presentation changes.
    if result.get('scenario') or (result.get('campaign') or {}).get('kind') == 'scenario':
        for chapter in chapters:
            chapter['_book_style'] = 'immersive'
            chapter['_book_style_preserve_title'] = True
        return result
    campaign = result.get('campaign') or {}
    sidebar = campaign.get('sidebar') or {}
    ranked_main = {str(key): i for i, key in enumerate(sidebar.get('chapter_ids') or [])}
    old_main_group = plain_text(sidebar.get('group'))
    for original_index, chapter in enumerate(chapters):
        original_title = str(chapter.get('title') or '')
        if not chapter.get('id') and original_title:
            # build_chapters originally uses the title as the ID seed when a
            # raw chapter lacks an ID. Preserve that seed before retitling.
            chapter['id'] = original_title
        chapter['_book_style'] = 'immersive'
        chapter.setdefault('_book_style_original_group', chapter.get('group', ''))
        chapter.setdefault('_book_style_original_index', original_index)
        is_campaign = str(chapter.get('id')) in ranked_main or bool(old_main_group and plain_text(chapter.get('group')) == old_main_group)
        chapter['group'] = 'Main Questline' if is_campaign else group_for_chapter(chapter, scan)
        if chapter.get('_native_reference') or chapter.get('_template'):
            chapter['_book_style_preserve_title'] = True
        else:
            chapter['title'] = _specific_title(chapter, scan)
    group_rank = {name: index for index, name in enumerate(GROUP_ORDER)}

    def ordering(pair):
        index, chapter = pair
        group = chapter.get('group')
        role = chapter.get('_campaign_role')
        goal_priority = 0 if role == 'goal' or (chapter.get('_campaign') and role != 'journey') else 1
        return (group_rank.get(group, len(group_rank)), goal_priority if group == 'Main Questline' else 0,
                ranked_main.get(str(chapter.get('id')), len(ranked_main) + index) if group == 'Main Questline' else index)

    chapters[:] = [chapter for _, chapter in sorted(enumerate(chapters), key=ordering)]
    main = [chapter for chapter in chapters if chapter.get('group') == 'Main Questline']
    for index, chapter in enumerate(main, 1): chapter['_book_style_main_number'] = index
    if result.get('campaign') and main:
        result['campaign']['sidebar'] = dict(sidebar, group='Main Questline', after_group='',
                                             chapter_ids=[chapter.get('id') for chapter in main])
    return result


def chapter_presentation(raw, plain_title, code, index):
    """A readable sidebar title; callers retain control over enabling colors."""
    if raw.get('_book_style_preserve_title') or raw.get('_native_reference') or raw.get('_template'):
        return str(raw.get('title') or plain_title)
    title = plain_text(plain_title)
    if raw.get('group') == 'Main Questline':
        number = raw.get('_book_style_main_number', index + 1)
        return '&aChapter %s&r: &e%s' % (number, title)
    return '&7' + title


def group_presentation(plain, code, index, heading_style='bold'):
    """Compact themed headings still honor each selected heading treatment."""
    title = plain_text(plain)
    fallback = code if isinstance(code, str) and len(code) == 1 and code in '0123456789abcdef' else '7'
    prefix = '&' + GROUP_COLORS.get(title, fallback)
    if heading_style == 'bracket': return prefix + '[' + title + ']'
    if heading_style == 'stage': return prefix + '%02d · %s' % (index + 1, title)
    if heading_style == 'rule': return prefix + '— ' + title + ' —'
    return prefix + title


def group_art_theme(group):
    return GROUP_ART_THEMES.get(plain_text(group), 'exploration')
