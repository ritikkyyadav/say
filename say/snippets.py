"""Snippets: say a short phrase, type a longer text you saved.

"Insert my calendar link" types the whole URL; "my address" types the address.
It is the cheapest convenience a dictation tool can offer and the riskiest one
to get wrong, because an expansion firing in the middle of a sentence replaces
words the person meant. So the rule is narrow on purpose:

  * **Whole utterance only.** The trigger has to be everything that was said,
    ignoring case and the punctuation Whisper adds ("Insert my calendar
    link."). The same words inside a longer dictation never fire.
  * **At least two words.** A one-word trigger ("yes", "address") is something
    people say as a real reply; two words that make a deliberate command are
    not.
  * **Typed verbatim.** The expansion skips post-processing: it is text the
    user wrote, not something we heard.

Pure: no settings I/O here. `config.SNIPPETS` holds them, `settings` persists
them, the API validates them with `check()`.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

MIN_TRIGGER_WORDS = 2
MAX_TRIGGER_CHARS = 80
MAX_EXPANSION_CHARS = 4000
MAX_SNIPPETS = 100

_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def normalise(text: str) -> str:
    """Comparison form: words only, lower case, single spaces."""
    return ' '.join(m.group(0).lower() for m in _WORD.finditer(text or ''))


def match(spoken: str, snippets: Mapping[str, str]) -> str | None:
    """The expansion for `spoken` if the whole of it is a trigger, else None."""
    said = normalise(spoken)
    if not said:
        return None
    for trigger, expansion in snippets.items():
        if normalise(trigger) == said and expansion:
            return expansion
    return None


class BadSnippet(ValueError):
    pass


def check(snippets: Mapping[str, str]) -> dict[str, str]:
    """Validate a whole set; return it cleaned. Raises BadSnippet with a reason."""
    if not isinstance(snippets, Mapping):
        raise BadSnippet('Snippets must be a mapping of trigger to text.')
    if len(snippets) > MAX_SNIPPETS:
        raise BadSnippet(f'At most {MAX_SNIPPETS} snippets.')
    out: dict[str, str] = {}
    seen: dict[str, str] = {}
    for trigger, expansion in snippets.items():
        if not isinstance(trigger, str) or not isinstance(expansion, str):
            raise BadSnippet('Triggers and texts must both be text.')
        key = normalise(trigger)
        if len(key.split()) < MIN_TRIGGER_WORDS:
            raise BadSnippet(
                f'"{trigger.strip()}" is too short: a trigger needs at least '
                f'{MIN_TRIGGER_WORDS} words, so a one-word reply never expands.'
            )
        if len(trigger) > MAX_TRIGGER_CHARS:
            raise BadSnippet(f'"{trigger[:30]}…" is too long for a trigger.')
        if not expansion.strip():
            raise BadSnippet(f'"{trigger.strip()}" has no text to type.')
        if len(expansion) > MAX_EXPANSION_CHARS:
            raise BadSnippet(f'The text for "{trigger.strip()}" is too long.')
        if key in seen:
            raise BadSnippet(f'"{trigger.strip()}" and "{seen[key]}" are the same trigger.')
        seen[key] = trigger.strip()
        out[trigger.strip()] = expansion
    return out
