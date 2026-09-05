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


def applies_to(model_id: str) -> bool:
    """Does this model read the language setting at all?

    The bundled Moonshine model is English-only and takes no language
    argument; telling someone their choice is in effect there would be a lie.
    """
    info = model_catalog.get(model_id)
    return info is not None and info.backend == 'whisper-mlx'
