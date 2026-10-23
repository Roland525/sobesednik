"""Read-only Valve patch data. Questions and conversation never leave the PC."""
import asyncio
import html
import re
import time

import httpx


def is_dota_question(text):
    return bool(re.search(r'\bdota\s*2?\b|\bдот(?:а|ы|у|е|ой)\b', text.lower()))


def clean_note(value):
    value = re.sub(r'<[^>]*>', ' ', str(value))
    return ' '.join(html.unescape(value).split()).strip(' .;')


def notes(value):
    if isinstance(value, dict):
        if isinstance(value.get('note'), str):
            note = clean_note(value['note'])
            if note and len(note) <= 180:
                yield note
        for key, child in value.items():
            if key != 'note':
                yield from notes(child)
    elif isinstance(value, list):
        for child in value:
            yield from notes(child)


class DotaPatchSource:
    def __init__(self, logger):
        self.logger = logger
        self.cache = {}

    async def reply(self, question):
        if not is_dota_question(question):
            return None
        requested = re.search(r'\b\d+\.\d+[a-z]?\b', question.lower())
        requested = requested.group() if requested else None
        key = requested or 'latest'
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        try:
            async with asyncio.timeout(8):
                async with httpx.AsyncClient(base_url='https://www.dota2.com', timeout=4,
                                             trust_env=False, follow_redirects=False) as client:
                    async def get(route, **params):
                        response = await client.get('/datafeed/' + route, params=params)
                        response.raise_for_status()
                        return response.json()

                    listing = await get('patchnoteslist')
                    patches = [patch for patch in listing['patches']
                               if re.fullmatch(r'\d+\.\d+[a-z]?', str(patch.get('patch_number', '')))]
                    if requested:
                        patches = [patch for patch in patches if patch['patch_number'] == requested]
                    if not patches:
                        return None
                    version = max(patches, key=lambda patch: int(patch['patch_timestamp']))['patch_number']
                    patch, heroes, items = await asyncio.gather(
                        get('patchnotes', version=version, language='russian'),
                        get('herolist', language='russian'), get('itemlist', language='russian'))
            if patch.get('success') is not True or patch.get('patch_number') != version:
                return None
            hero_names = {entry['id']: entry['name_loc'] for entry in heroes['result']['data']['heroes']}
            item_names = {entry['id']: entry['name_loc'] for entry in items['result']['data']['itemabilities']}
            examples = []
            for group, id_key, names in (('heroes', 'hero_id', hero_names), ('items', 'ability_id', item_names)):
                for entry in patch.get(group, []):
                    name = clean_note(names.get(entry.get(id_key), ''))
                    note = next(notes(entry), None)
                    if name and note:
                        examples.append(f'{name}: {note}')
                        break
            if not examples:
                examples = list(notes(patch.get('general_notes', [])))[:2]
            if not examples:
                return None
            label = 'патч' if requested else 'последний патч'
            answer = f'По официальным патчноутам Valve, {label} — {version}. Например, ' + '; '.join(examples) + '.'
            if len(self.cache) >= 4:
                self.cache.clear()
            self.cache[key] = (time.monotonic(), answer)
            return answer
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
            self.logger.warning('Официальные патчноуты Valve временно недоступны')
            return None
