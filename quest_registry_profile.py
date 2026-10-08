"""Offline runtime evidence applies only to an exactly matching pack input set.

Models and translations can survive after an item registration is removed.
Captured absent IDs are therefore useful facts, but optional integrations can
register them in another pack. Never apply these exclusions by mod name alone.
No JavaScript, game code, or user configuration is executed here.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import sys
import zipfile

PROFILE_FILE = 'runtime_item_profiles.json'
SOURCE_ROOTS = ('config', 'defaultconfigs', 'kubejs/startup_scripts',
                'kubejs/server_scripts', 'kubejs/config', 'kubejs/data',
                'kubejs/assets', 'datapacks', 'global_packs', 'openloader', 'paxi')
_SKIP_DIRS = {'cache', 'caches', '.cache', 'logs', 'backups', 'backup', 'output',
              'outputs', 'tmp', 'temp', '.git', 'ftbquests', 'playerdata', 'saves', 'world'}


def profile_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return root / PROFILE_FILE


def _hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()


def _jars(instance):
    folder = Path(instance) / 'mods'
    return sorted([*folder.glob('*.jar'), *folder.glob('*.zip')], key=lambda p: p.name)


def _loader_facts(instance):
    # Only public loader identifiers, never the surrounding launcher profile.
    for name in ('minecraftinstance.json', 'instance.json', 'mmc-pack.json'):
        path = Path(instance) / name
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding='utf-8', errors='replace'))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        result = {}
        loader = doc.get('baseModLoader')
        if isinstance(loader, dict):
            result = {key: loader[key] for key in ('name', 'type', 'version', 'forgeVersion', 'minecraftVersion')
                      if isinstance(loader.get(key), (str, int))}
        components = doc.get('components')
        if isinstance(components, list):
            result['components'] = sorted(
                (str(row['uid']), str(row.get('version', ''))) for row in components
                if isinstance(row, dict) and row.get('uid') in
                ('net.minecraftforge', 'net.neoforged', 'net.fabricmc.fabric-loader', 'org.quiltmc.quilt-loader'))
        if result:
            return result
    return {}


def pack_signature(instance, minecraft_version):
    """Reproducible ZIP-content/config signature without absolute paths.

    ZIP entry names, CRCs and expanded sizes describe shipped content rather
    than download timestamps or compression settings. Config/script/data bytes
    participate; generated quests, cache files and player worlds do not.
    Any unreadable file/symlink makes the signature unavailable, never weaker.
    """
    instance = Path(instance).resolve()
    jars, sources = [], []
    for path in _jars(instance):
        if path.is_symlink():
            raise ValueError('A mod JAR is a symlink')
        with zipfile.ZipFile(path) as zf:
            entries = sorted((entry.filename, entry.CRC, entry.file_size)
                             for entry in zf.infolist() if not entry.is_dir())
        jars.append([path.name, _hash_bytes(_canonical(entries))])
    for relative in SOURCE_ROOTS:
        root = instance / relative
        if not root.exists():
            continue
        if root.is_symlink() or not root.resolve().is_relative_to(instance):
            raise ValueError('A pack source folder is outside the instance')
        for base, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name.lower() not in _SKIP_DIRS)
            if any((Path(base) / name).is_symlink() for name in dirs):
                raise ValueError('A pack source folder is a symlink')
            for name in sorted(names):
                low = name.lower()
                if low.startswith('ftbquests') or low.endswith(('.log', '.tmp', '.bak', '.lock')):
                    continue
                path = Path(base) / name
                if path.is_symlink():
                    raise ValueError('A pack source file is a symlink')
                sources.append([path.relative_to(instance).as_posix(), _hash_bytes(path.read_bytes())])
    facts = {'schema': 1, 'minecraft_version': str(minecraft_version or ''),
             'loader': _loader_facts(instance), 'jars': jars, 'sources': sorted(sources)}
    return {'signature': _hash_bytes(_canonical(facts)),
            'minecraft_version': facts['minecraft_version'], 'loader': facts['loader'],
            'jar_names': [name for name, _digest in jars],
            'jar_count': len(jars), 'source_file_count': len(sources)}


def match_profile(instance, minecraft_version, data_path=None):
    """Return exclusions only with exact source equality; otherwise none."""
    result = {'matched': False, 'absent_ids': [], 'excluded_entities': [],
              'signature': '', 'data_digest': '',
              'message': 'No verified runtime item profile matched; using conservative asset checks.'}
    path = Path(data_path) if data_path is not None else profile_path()
    try:
        raw = path.read_bytes()
        result['data_digest'] = _hash_bytes(raw)
        doc = json.loads(raw)
        if not isinstance(doc, dict) or doc.get('schema') != 1:
            return result
        names = [path.name for path in _jars(instance)]
        # Most packs can reject the profile without rereading their configs
        # or ZIP directories. A coincidentally matching mod name is irrelevant.
        candidates = [row for row in doc.get('profiles', []) if isinstance(row, dict)
                      and row.get('minecraft_version') == minecraft_version and row.get('jar_names') == names]
        if not candidates:
            return result
        facts = pack_signature(instance, minecraft_version)
        result['signature'] = facts['signature']
        for row in candidates:
            if row.get('signature') != facts['signature']:
                continue
            absent = row.get('absent_ids')
            entities = row.get('excluded_entities', [])
            if not all(isinstance(ids, list) and all(isinstance(i, str) and
                    re.fullmatch(r'[a-z0-9_.-]+:[a-z0-9_./-]+', i) for i in ids)
                       for ids in (absent, entities)):
                continue
            result.update(matched=True, absent_ids=sorted(set(absent)),
                          excluded_entities=sorted(set(entities)),
                          profile_id=str(row.get('id', 'verified-runtime')),
                          message='Verified runtime item profile matched; dormant or disabled items and creatures are excluded from goals, icons, and rewards.')
            return result
    except (OSError, ValueError, TypeError, zipfile.BadZipFile):
        return result
    return result


def apply_profile(scan, profile):
    """Filter both goals and payouts; acquisition claims never restore IDs."""
    scan['runtime_item_profile'] = {key: value for key, value in profile.items()
                                    if key not in ('absent_ids', 'excluded_entities')}
    if not profile.get('matched'):
        return 0
    absent = set(profile.get('absent_ids', ()))
    removed = set()
    for key in ('items', 'craftable'):
        for mid, values in scan.get(key, {}).items():
            removed.update(set(values) & absent)
            kept = set(values) - absent
            scan[key][mid] = kept if isinstance(values, set) else sorted(kept)
    if 'vanilla' in scan:
        scan['vanilla'] = set(scan['vanilla']) - absent
    scan['unobtainable'] = set(scan.get('unobtainable', ())) | absent
    excluded_entities = set(profile.get('excluded_entities', ()))
    removed_entities = set()
    for mid, values in scan.get('entities', {}).items():
        removed_entities.update(set(values) & excluded_entities)
        kept = set(values) - excluded_entities
        scan['entities'][mid] = kept if isinstance(values, set) else sorted(kept)
    scan['runtime_item_profile']['removed_ids'] = sorted(removed)
    scan['runtime_item_profile']['removed_count'] = len(removed)
    scan['runtime_item_profile']['removed_entities'] = sorted(removed_entities)
    scan['runtime_item_profile']['removed_entity_count'] = len(removed_entities)
    return len(removed)
