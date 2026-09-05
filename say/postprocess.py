"""Deterministic clean-up applied between transcription and injection.

This is the seam the engine never had: raw ASR text goes in, the text that
actually gets typed comes out. Everything here is rule-based, pure, and fast
(single-digit milliseconds) — there is no model in this module. A future optional LLM tier
plugs in *after* these stages, never instead of them, because rules cannot
hallucinate and are the only thing you want touching numbers and proper nouns.

Two stages run today:

  1. Vocabulary correction — fixes terms the acoustic model reliably mishears
     ("cloud code" → "Claude Code"). Exact replacements first, then a guarded
     fuzzy/phonetic pass for near-misses.
  2. Formatting — capitalisation, terminal punctuation, whitespace repair.

`asr_prompt()` is the other half of the vocabulary story: it produces the
`initial_prompt` string handed to Whisper *before* decoding, so the model is
biased toward the user's terms instead of only being corrected afterwards.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import config
from logging_setup import get_logger

_log = get_logger('postprocess')

# A bland, well-formed sentence used to establish register for the decoder's
# first 30-second window. Deliberately free of nouns so it biases *style*
# (capitals, commas, full stops) without biasing vocabulary.
STYLE_SEED = (
    "Hello. Here is a clear, well-punctuated transcript. "
    "It uses capital letters, commas, and full stops correctly."
)

# Fuzzy matching thresholds. Multi-word terms are far safer to correct than
# single words — "cloud code" can only plausibly mean "Claude Code", whereas a
# bare "cloud" usually means the sky or AWS. So single-word entries need near
# certainty, multi-word entries can be matched more loosely.
_FUZZY_MIN_SINGLE = 0.92
_FUZZY_MIN_MULTI = 0.78

# Tokens that should never gain a trailing full stop.
_NO_TERMINAL_PUNCT = re.compile(r'[)\]}>:;,\-_/\\|=+*&^%$#@~`]$')


# ── phonetics ────────────────────────────────────────────────────────

def _soundex(word: str) -> str:
    """Classic Soundex. Cheap stand-in for a real phonetic index.

    Good enough for the job here: it collapses vowels, so "cloud" and
    "claude" both reduce to C43 and compare equal, which is exactly the
    class of error a speech model makes.
    """
    word = re.sub(r'[^a-z]', '', word.lower())
    if not word:
        return ''
    codes = {
        **dict.fromkeys('bfpv', '1'),
        **dict.fromkeys('cgjkqsxz', '2'),
        **dict.fromkeys('dt', '3'),
        'l': '4',
        **dict.fromkeys('mn', '5'),
        'r': '6',
    }
    first = word[0].upper()
    out: list[str] = []
    prev = codes.get(word[0], '')
    for ch in word[1:]:
        code = codes.get(ch, '')
        if code and code != prev:
            out.append(code)
        # h and w are transparent: they don't break a repeat run.
        if ch not in 'hw':
            prev = code
    return (first + ''.join(out) + '000')[:4]


def _similar(a: str, b: str) -> float:
    """Blend literal similarity with phonetic agreement, 0..1."""
    a_l, b_l = a.lower(), b.lower()
    if a_l == b_l:
        return 1.0
    literal = SequenceMatcher(None, a_l, b_l).ratio()
    a_tokens, b_tokens = a_l.split(), b_l.split()
    if len(a_tokens) != len(b_tokens):
        return literal
    phonetic_hits = sum(
        1 for x, y in zip(a_tokens, b_tokens, strict=False)
        if _soundex(x) and _soundex(x) == _soundex(y)
    )
    phonetic = phonetic_hits / len(a_tokens)
    # Literal similarity dominates; phonetic agreement pulls near-misses up.
    return 0.6 * literal + 0.4 * phonetic


# ── configuration access ─────────────────────────────────────────────

def _terms() -> list[str]:
    raw = getattr(config, 'DICTIONARY', None) or []
    return [t.strip() for t in raw if isinstance(t, str) and t.strip()]


def _replacements() -> dict[str, str]:
    raw = getattr(config, 'DICTIONARY_REPLACEMENTS', None) or {}
    if not isinstance(raw, dict):
        return {}
    return {
        k.strip(): v
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, str) and k.strip()
    }


# ── stage 0: decoder repetition ──────────────────────────────────────

# When an encoder-decoder ASR model loses acoustic footing — a clip that is
# mostly silence, or a strong text prompt with almost no audio behind it — the
# decoder stops attending to the audio and re-emits the same token until it
# reaches its token cap. Whisper ships guards for exactly this
# (`compression_ratio_threshold` and the temperature fallback), but they only
# *re-roll* the window: when every temperature in the ladder degenerates, the
# final attempt is returned anyway. So the loop still arrives here, and this is
# the last place it can be stopped before it is typed into someone's document.
#
# Thresholds sit where natural speech does not go. People genuinely repeat
# themselves — "very very good", "no no no" — so pairs and triples are left
# alone; four identical words in a row is a machine, not a person.
# How many consecutive repeats it takes to call something a loop, by phrase
# length. The threshold falls as phrases get longer because the odds of saying
# the same thing verbatim collapse with every extra word: "no no no no" is
# emphasis, but an eight-word sentence produced three times in a row is a
# decoder that has stopped listening.
#
# Validated by replaying the whole transcript store (see the gotcha in
# CLAUDE.md — replay, don't guess). At four-for-short, the only things
# collapsed are unambiguous loops (217x "drag", 222x "yeah", ~200x "hold"
# inside one token); at three, natural emphasis like "and again, and again,
# and again" was being flattened too.
_MAX_PHRASE_LEN = 12    # longest n-gram checked for looping


def _run_threshold(size: int) -> int:
    """Consecutive repeats required to treat a `size`-word phrase as a loop."""
    if size <= 4:
        return 4
    if size <= 8:
        return 3
    return 2

# The same failure with no spaces, when the repeated unit is a sub-word token:
# "addholdholdholdhold". A 2-12 character unit repeated four or more times.
# "banana" is safe — "an" occurs only twice, below the {3,} tail.
_INTRA_TOKEN_LOOP = re.compile(r'(.{2,12}?)\1{3,}', re.IGNORECASE)


def _norm_word(word: str) -> str:
    """Compare words ignoring case and surrounding punctuation."""
    return re.sub(r'^[^\w]+|[^\w]+$', '', word).lower()


def collapse_intra_token_loops(text: str) -> str:
    """Collapse loops that occur inside a single whitespace-delimited token."""
    return _INTRA_TOKEN_LOOP.sub(lambda m: m.group(1), text)


def collapse_repetitions(text: str) -> str:
    """Collapse degenerate repeated words and phrases to a single instance.

    Only *consecutive* repeats are considered — a word recurring throughout a
    paragraph is normal prose, and only an adjacent verbatim run is a loop.

    The first instance is always kept, so the salvageable part of the
    transcript survives: "And, these are all available available available…"
    becomes "And, these are all available", which is very probably what the
    user actually said.
    """
    if not text or not text.strip():
        return text

    text = collapse_intra_token_loops(text)
    # Process each run of non-newline text separately and stitch the original
    # newline runs back in, so paragraph structure survives a stage whose
    # tokenisation is otherwise whitespace-flattening.
    pieces = re.split(r'(\n+)', text)
    return ''.join(
        piece if piece.startswith('\n') else _collapse_line(piece)
        for piece in pieces
    )


def _collapse_line(text: str) -> str:
    """Collapse repeated words/phrases within a single line."""
    tokens = text.split()
    if len(tokens) < 2:
        return text

    leading = text[:len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()):]

    # Shortest n-gram first. Scanning longest-first interacts badly with the
    # length-scaled thresholds: a five-word phrase said four times matches as a
    # ten-word pair (2 repeats, which clears the long-phrase threshold) and
    # collapses to ten words, leaving a duplicate behind. Going up from one word
    # retires the tightest repeats first, so each loop is caught at its own
    # period and the longer passes only see what genuinely remains.
    for size in range(1, _MAX_PHRASE_LEN + 1):
        threshold = _run_threshold(size)
        out: list[str] = []
        i = 0
        while i < len(tokens):
            phrase = [_norm_word(t) for t in tokens[i:i + size]]
            if len(phrase) < size or not any(phrase):
                out.append(tokens[i])
                i += 1
                continue
            repeats = 1
            j = i + size
            while j + size <= len(tokens):
                if [_norm_word(t) for t in tokens[j:j + size]] != phrase:
                    break
                repeats += 1
                j += size
            if repeats >= threshold:
                # Keep the first instance, drop the rest of the run.
                out.extend(tokens[i:i + size])
                _log.warning(
                    'Collapsed a %d-word phrase repeated %d times — decoder '
                    'repetition loop', size, repeats,
                )
                i = j
            else:
                out.append(tokens[i])
                i += 1
        tokens = out

    return leading + ' '.join(tokens) + trailing


def looks_degenerate(original: str, collapsed: str) -> bool:
    """True when collapsing removed so much that the clip was mostly a loop.

    Used only for logging and metrics — the collapsed text is still returned,
    because the surviving prefix is usually the user's real words and throwing
    it away would lose a dictation they would have to repeat.
    """
    o_words = len(original.split())
    c_words = len(collapsed.split())
    return o_words >= 20 and c_words <= o_words * 0.4


# ── stage 1: vocabulary ──────────────────────────────────────────────

def _match_case(replacement: str, observed: str) -> str:
    """Keep the writer's intent: only force case when the source was neutral."""
    if observed.isupper() and len(observed) > 1:
        return replacement.upper()
    return replacement


def apply_replacements(text: str) -> str:
    """Literal, user-defined substitutions. Whole-word, case-insensitive."""
    for wrong, right in _replacements().items():
        pattern = r'\b' + re.escape(wrong) + r'\b'

        def _swap(m: re.Match[str], r: str = right) -> str:
            return _match_case(r, m.group(0))

        text = re.sub(pattern, _swap, text, flags=re.IGNORECASE)
    return text


def apply_fuzzy_terms(text: str) -> str:
    """Repair near-misses of known vocabulary terms.

    Slides a window of the same word-length as each dictionary term across the
    transcript and replaces windows that are close enough phonetically and
    literally. Exact matches are left alone; anything already correct never
    gets touched.
    """
    terms = _terms()
    if not terms:
        return text

    tokens = text.split(' ')
    if not tokens:
        return text

    # Longest terms first so "Claude Code" wins over a bare "Claude" entry.
    for term in sorted(terms, key=lambda t: -len(t.split())):
        term_words = len(term.split())
        if term_words == 0:
            continue
        # Speech models split and join compound words unpredictably —
        # "codebase" is heard as "code base", "kubectl" as "kube control".
        # So a term of W words is compared against windows of W-1..W+1 words,
        # with whitespace ignored, instead of only exact-width windows.
        spans = [s for s in (term_words, term_words + 1, term_words - 1) if 1 <= s <= len(tokens)]
        for span in spans:
            tokens = _match_span(tokens, term, span)
    return ' '.join(tokens)


def _despace(s: str) -> str:
    return re.sub(r'\s+', '', s).lower()


def _match_span(tokens: list[str], term: str, span: int) -> list[str]:
    """Slide a `span`-token window over `tokens`, replacing matches of `term`.

    Fuzzy similarity is only allowed when the window is the same width as the
    term. A wider or narrower window may only match on an exact despaced
    comparison — that widening exists to repair split/joined words
    ("code base" for "codebase"), and letting it match fuzzily would let a
    2-word term swallow an adjacent word ("use Claude Code" -> "Claude Code").
    """
    term_words = len(term.split())
    same_width = span == term_words
    threshold = _FUZZY_MIN_SINGLE if term_words == 1 else _FUZZY_MIN_MULTI
    term_despaced = _despace(term)
    term_len = len(term_despaced)
    # Cheap pre-filters. `_similar` runs SequenceMatcher, which is far too slow
    # to invoke on every window of a long transcript (measured: 243 ms on a
    # 1,032-word dictation with 14 terms). A length band plus a first-consonant
    # check rejects almost every candidate for the cost of a subtraction, and
    # neither can discard a window the full comparison would have accepted:
    # a match above threshold cannot differ wildly in length, and phonetically
    # similar words share a leading Soundex code.
    max_len_delta = max(2, int(0.35 * term_len))
    term_first = _soundex(term_despaced)[:1]
    i = 0
    while i + span <= len(tokens):
        window = ' '.join(tokens[i:i + span])
        # Compare on letters only, so trailing commas don't blunt the score.
        bare = re.sub(r'^[^\w]+|[^\w]+$', '', window)
        if not bare:
            i += 1
            continue
        # Already exactly right — leave it completely alone.
        if bare == term:
            i += span
            continue
        bare_despaced = _despace(bare)
        # Same letters, different spacing or case: "code base" for "codebase",
        # "kubernetes" for "Kubernetes". The dictionary is the authority on
        # spelling, so normalise rather than skipping.
        despaced_match = bare_despaced == term_despaced
        fuzzy_ok = False
        if (
            not despaced_match
            and same_width
            and abs(len(bare_despaced) - term_len) <= max_len_delta
            and _soundex(bare_despaced)[:1] == term_first
        ):
            fuzzy_ok = _similar(bare, term) >= threshold
        if despaced_match or fuzzy_ok:
            suffix = re.search(r'[^\w]+$', window)
            replaced = term + (suffix.group(0) if suffix else '')
            tokens[i:i + span] = replaced.split(' ')
            i += len(replaced.split(' '))
            continue
        i += 1
    return tokens


# ── stage 2: formatting ──────────────────────────────────────────────

def fix_whitespace(text: str) -> str:
    text = text.replace(' ', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' +([,.!?;:])', r'\1', text)     # no space before punctuation
    text = re.sub(r'([,;:])(?=[^\s\d])', r'\1 ', text)  # space after, but not in 1,000
    return text.strip()


def fix_capitalisation(text: str) -> str:
    """Capitalise the opening word and anything following a sentence end."""
    if not text:
        return text
    text = re.sub(
        r'([.!?]\s+)([a-z])',
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )
    # A line or paragraph break starts a new sentence even without punctuation.
    text = re.sub(
        r'(\n\s*)([a-z])',
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )
    # Standalone "i" is always capitalised in English.
    text = re.sub(r'\bi\b', 'I', text)
    return text[:1].upper() + text[1:]


def ensure_terminal_punctuation(text: str) -> str:
    """Give the last sentence an ending, unless one would be wrong."""
    if not text:
        return text
    stripped = text.rstrip()
    if not stripped:
        return text
    if stripped[-1] in '.!?"\')…':
        return stripped
    if _NO_TERMINAL_PUNCT.search(stripped):
        return stripped
    return stripped + '.'


# ── public API ───────────────────────────────────────────────────────

def asr_prompt(include_style_seed: bool = True) -> str:
    """Build the `initial_prompt` handed to the decoder before transcribing.

    Combines the style seed (which fixes punctuation drift) with the user's
    vocabulary (which biases the model toward their jargon). Whisper treats
    this as preceding context, so it establishes both register and terms for
    the first window.

    `include_style_seed=False` drops the seed while keeping the vocabulary.
    Callers use it for very short clips: the seed is two full sentences of
    text prior, and when there is only a fraction of a second of audio to
    weigh against it the decoder follows the prompt instead of the sound and
    degenerates into a repetition loop. Vocabulary terms are a short comma
    list and carry nowhere near the same pull.
    """
    parts: list[str] = []
    user_prompt = (getattr(config, 'INITIAL_PROMPT', '') or '').strip()
    if include_style_seed and getattr(config, 'STYLE_SEED_ENABLED', True):
        parts.append(STYLE_SEED)
    terms = _terms()
    if terms:
        parts.append('Terms: ' + ', '.join(terms) + '.')
    if user_prompt:
        parts.append(user_prompt)
    return ' '.join(parts).strip()


def apply(text: str, target_app: str | None = None) -> str:
    """Run every enabled deterministic stage. Never raises.

    `target_app` is accepted now so per-app profiles can land later without
    changing the call site in the controller.
    """
    if not text or not text.strip():
        return text
    if not getattr(config, 'AUTO_CORRECT', True):
        return text
    try:
        out = text
        # First, before anything else looks at the text: a decoder repetition
        # loop must never reach the user's document, and collapsing it here
        # also spares the fuzzy-vocabulary pass from sliding a window over
        # several hundred identical tokens.
        out = collapse_repetitions(out)
        if looks_degenerate(text, out):
            _log.error(
                'Transcript was mostly a repetition loop (%d words -> %d); '
                'the clip probably contained little or no speech',
                len(text.split()), len(out.split()),
            )
        out = apply_replacements(out)
        out = apply_fuzzy_terms(out)
        out = fix_whitespace(out)
        if getattr(config, 'AUTO_CAPITALISE', True):
            out = fix_capitalisation(out)
        if getattr(config, 'AUTO_TERMINAL_PUNCTUATION', True):
            out = ensure_terminal_punctuation(out)
        return out
    except Exception:
        # A formatting bug must never cost the user their dictation.
        _log.exception('postprocess failed; returning raw transcript')
        return text
