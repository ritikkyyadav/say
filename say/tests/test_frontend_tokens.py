"""The design tokens the UI is built from, checked from the Python suite.

There is no test runner in `desktop/`, and the failure this guards is the
kind that no type-checker and no build step can see: `npm run build`
succeeds, the CSS is valid, nothing reaches the console, and the app ships
with an invisible logo.

The bug that prompted this: `--mark-chip` and `--mark-chip-ink` were both
declared empty (`--mark-chip:;`) behind an unfilled TODO. An empty custom
property is *not* an unset one -- it substitutes an empty token stream, so
the declaration using it goes invalid at computed-value time. For a
non-inherited property like `background` that lands on the initial value
(transparent); for an inherited one like `fill` it takes the PARENT's
value, which is the initial black. So the rail's chip went transparent and
its mark went black on a near-black rail. A `var(--x, fallback)` would not
have helped: a fallback only applies when the property is
guaranteed-invalid (i.e. never set), and an explicitly-empty one is not.
"""

from __future__ import annotations

import os
import re

import pytest

CSS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'desktop', 'src', 'styles', 'index.css',
)

# Custom properties Tailwind's own base layer may reference without our
# declaring them. Nothing is in here today; the hook exists so a future
# framework token does not force the guard to be loosened wholesale.
EXTERNAL: frozenset[str] = frozenset()


def _source() -> str:
    with open(CSS, encoding='utf-8') as fh:
        return fh.read()


def _strip_comments(css: str) -> str:
    """Drop /* ... */ so prose about a token is never read as a declaration."""
    return re.sub(r'/\*.*?\*/', ' ', css, flags=re.DOTALL)


def _declarations(css: str) -> list[tuple[str, str]]:
    """(name, value) for every `--name: value;`, in source order."""
    return [
        (m.group(1), m.group(2).strip())
        for m in re.finditer(r'(--[\w-]+)\s*:\s*([^;}]*)[;}]', _strip_comments(css))
    ]


def _bare_references(css: str) -> set[str]:
    """Tokens read with `var(--x)` and NO fallback.

    `var(--x, 0)` is deliberately excluded, and the distinction is the
    whole point. A token that is never declared in CSS because React sets
    it inline (`style={{'--w': ...}}`) is guaranteed-invalid until that
    happens, which is exactly when a fallback applies -- so `var(--w, 0)`
    is correct and safe. A declared-but-empty token is not
    guaranteed-invalid, so its fallback is skipped. Only bare references
    need a declaration to lean on.
    """
    return {
        m.group(1)
        for m in re.finditer(r'var\(\s*(--[\w-]+)\s*\)', _strip_comments(css))
    }


def _inline_declared() -> set[str]:
    """Custom properties the React tree sets via inline style objects.

    Scanned rather than allowlisted, so deleting the TSX that sets one
    turns the CSS that reads it into a failure instead of a blank bar.
    """
    root = os.path.join(os.path.dirname(CSS), '..')
    found: set[str] = set()
    for base, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(('.tsx', '.ts')):
                continue
            with open(os.path.join(base, name), encoding='utf-8') as fh:
                found |= set(re.findall(r"['\"](--[\w-]+)['\"]\s*:", fh.read()))
    return found


def test_the_stylesheet_is_where_we_think_it_is():
    assert os.path.isfile(CSS), CSS


def test_no_custom_property_is_declared_empty():
    """The regression. An empty token stream is valid CSS and a silent bug."""
    empty = [name for name, value in _declarations(_source()) if value == '']
    assert not empty, (
        'declared with an empty value, which makes every declaration that '
        f'uses them invalid at computed-value time: {sorted(set(empty))}. '
        'Give the token a real value -- a var() fallback will not rescue it, '
        'because a fallback only applies to a property that was never set.'
    )


def test_every_token_read_without_a_fallback_is_declared():
    """A typo'd var() fails exactly like an empty one, and just as quietly."""
    css = _source()
    declared = {name for name, _v in _declarations(css)} | _inline_declared() | EXTERNAL
    missing = sorted(_bare_references(css) - declared)
    assert not missing, (
        'read with a bare var() but declared nowhere -- in the stylesheet or '
        f'as an inline style in the React tree: {missing}. Either declare it '
        'or give the reference a fallback.'
    )


@pytest.mark.parametrize('token', ['--mark-chip', '--mark-chip-ink', '--mark', '--mark-glow'])
def test_the_logo_chip_tokens_carry_a_value(token):
    """The four the logo is drawn from, named so a failure says which broke."""
    values = dict(_declarations(_source()))
    assert token in values, f'{token} is not declared'
    assert values[token], f'{token} is declared empty'


def test_no_unfilled_todo_is_shipped_in_the_stylesheet():
    """The empty tokens sat behind a TODO(human) that went into a release."""
    hits = [
        line.strip()
        for line in _source().splitlines()
        if 'TODO(human)' in line
    ]
    assert not hits, f'unfilled TODO(human) still in the shipped stylesheet: {hits}'
