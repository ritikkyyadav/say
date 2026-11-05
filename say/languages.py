"""Which language the decoder should expect.

`config.LANGUAGE` is a Whisper language code, or `None` for automatic
detection. Until this module existed the value was `'en'` with no way to
change it from the desktop app — while the Models page promised "99
languages, switch freely". Whisper forced to English on Hindi speech does
not transcribe it; it translates or transliterates. So the setting has to be
reachable, validated, and honest about which models honour it.

Pure: no AppKit, no I/O. The API layer persists through `settings`, and
`transcriber._transcribe_whisper` already omits the `language` argument when
the value is falsy, which is what makes `None` mean "detect".
"""

from __future__ import annotations

import re
from typing import TypedDict

import config
import model_catalog

AUTO = None

# What the desktop picker shows, by Whisper code. Whisper accepts about a
# hundred codes and the API accepts any two- or three-letter ISO 639 code;
# this list only decides what is offered without typing.
LANGUAGES: tuple[tuple[str, str], ...] = (
    ('en', 'English'),
    ('hi', 'Hindi'),
    ('es', 'Spanish'),
    ('fr', 'French'),
    ('de', 'German'),
    ('pt', 'Portuguese'),
    ('it', 'Italian'),
    ('nl', 'Dutch'),
    ('pl', 'Polish'),
    ('ru', 'Russian'),
    ('uk', 'Ukrainian'),
    ('tr', 'Turkish'),
    ('ar', 'Arabic'),
    ('fa', 'Persian'),
    ('ur', 'Urdu'),
    ('bn', 'Bengali'),
    ('ta', 'Tamil'),
    ('te', 'Telugu'),
    ('mr', 'Marathi'),
    ('gu', 'Gujarati'),
    ('pa', 'Punjabi'),
    ('id', 'Indonesian'),
    ('vi', 'Vietnamese'),
    ('th', 'Thai'),
    ('ja', 'Japanese'),
    ('ko', 'Korean'),
    ('zh', 'Chinese'),
)

_NAMES = dict(LANGUAGES)
_CODE = re.compile(r'^[a-z]{2,3}$')

# ── Focus: what the person dictates in ───────────────────────────────────────
# The question a user actually has is not "which of seven models" but "will it
# understand me". A focus is that answer: it fixes the language picker to a
# short list, decides the default (English is fixed, everything else detects
# automatically so a person can switch mid-sentence), and names the one model
# worth having installed for it. Persisted as `config.LANGUAGE_FOCUS`.
_EUROPEAN = ('en', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl', 'ru', 'uk', 'tr')
_SOUTH_ASIAN = ('en', 'hi', 'ur', 'bn', 'ta', 'te', 'mr', 'gu', 'pa')

ENGLISH_MODEL = 'moonshine/medium-streaming-en'
MULTILINGUAL_MODEL = 'mlx-community/whisper-large-v3-turbo'


class FocusGroup(TypedDict):
    id: str
    name: str
    blurb: str
    codes: tuple[str, ...]
    default_code: str | None
    recommended_model: str


FOCUS_GROUPS: tuple[FocusGroup, ...] = (
    {
        'id': 'english',
        'name': 'English only',
        'blurb': 'The built-in model. Nothing to download, and the fastest there is.',
        'codes': ('en',),
        'default_code': 'en',
        'recommended_model': ENGLISH_MODEL,
    },
    {
        'id': 'european',
        'name': 'European languages',
        'blurb': 'Spanish, French, German, Italian, Portuguese, Dutch, Polish, Russian, Ukrainian, Turkish — and English.',
        'codes': _EUROPEAN,
        'default_code': None,
        'recommended_model': MULTILINGUAL_MODEL,
    },
    {
        'id': 'south_asian',
        'name': 'Hindi, Urdu and South Asian',
        'blurb': 'Hindi, Urdu, Bengali, Tamil, Telugu, Marathi, Gujarati, Punjabi — and English.',
        'codes': _SOUTH_ASIAN,
        'default_code': None,
        'recommended_model': MULTILINGUAL_MODEL,
    },
    {
        'id': 'any',
        'name': 'Anything',
        'blurb': 'Every language Whisper knows, detected automatically.',
        'codes': tuple(code for code, _ in LANGUAGES),
        'default_code': None,
        'recommended_model': MULTILINGUAL_MODEL,
    },
)
FOCUS_IDS: frozenset[str] = frozenset(g['id'] for g in FOCUS_GROUPS)
DEFAULT_FOCUS = 'english'


class BadLanguage(ValueError):
    """Not a language code."""


def normalise(value: object) -> str | None:
    """Canonical code, or None for automatic. Raises BadLanguage otherwise."""
    if value is None:
        return AUTO
    code = str(value).strip().lower()
    if code in ('', 'auto', 'automatic', 'none'):
        return AUTO
    # 'en-US', 'en_GB' → 'en'
    code = code.split('-')[0].split('_')[0]
    if not _CODE.match(code):
        raise BadLanguage(f'{value!r} is not a language code like "en" or "hi"')
    return code


def name_for(code: str | None) -> str:
    if code is None:
        return 'Automatic'
    return _NAMES.get(code, code.upper())


def options() -> list[dict[str, str]]:
    return [{'code': code, 'name': name} for code, name in LANGUAGES]


def current() -> str | None:
    """The engine's language as it stands, tolerant of a bad settings value."""
    try:
        return normalise(getattr(config, 'LANGUAGE', 'en'))
    except BadLanguage:
        return 'en'


def focus_group(focus_id: str) -> FocusGroup:
    for group in FOCUS_GROUPS:
        if group['id'] == focus_id:
            return group
    raise BadLanguage(f'{focus_id!r} is not a focus: {", ".join(sorted(FOCUS_IDS))}')


def normalise_focus(value: object) -> str:
    focus = str(value or '').strip().lower()
    if focus not in FOCUS_IDS:
        raise BadLanguage(f'{value!r} is not a focus: {", ".join(sorted(FOCUS_IDS))}')
    return focus


def current_focus() -> str:
    """The persisted focus, or the one the language setting implies."""
    try:
        return normalise_focus(getattr(config, 'LANGUAGE_FOCUS', DEFAULT_FOCUS))
    except BadLanguage:
        return 'english' if current() == 'en' else 'any'


def default_code_for(focus_id: str) -> str | None:
    return focus_group(focus_id)['default_code']


def options_for(focus_id: str) -> list[dict[str, str]]:
    """The picker list for a focus, in catalogue order."""
    codes = set(focus_group(focus_id)['codes'])
    return [{'code': code, 'name': name} for code, name in LANGUAGES if code in codes]


def recommended_model(focus_id: str) -> str:
    return focus_group(focus_id)['recommended_model']


def groups() -> list[dict[str, object]]:
    """Every focus, serialised for the API."""
    return [
        {
            'id': g['id'],
            'name': g['name'],
            'blurb': g['blurb'],
            'default_code': g['default_code'],
            'recommended_model': g['recommended_model'],
            'options': options_for(g['id']),
        }
        for g in FOCUS_GROUPS
    ]


def applies_to(model_id: str) -> bool:
    """Does this model read the language setting at all?

    The bundled Moonshine model is English-only and takes no language
    argument; telling someone their choice is in effect there would be a lie.
    """
    info = model_catalog.get(model_id)
    return info is not None and info.backend == 'whisper-mlx'
