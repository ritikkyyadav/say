"""Tests for the deterministic transcript clean-up stage."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import postprocess  # noqa: E402


class _ConfigPatch(unittest.TestCase):
    """Base class that restores every config attribute it touches."""

    KEYS = (
        'AUTO_CORRECT', 'AUTO_CAPITALISE', 'AUTO_TERMINAL_PUNCTUATION',
        'DICTIONARY', 'DICTIONARY_REPLACEMENTS', 'INITIAL_PROMPT',
        'STYLE_SEED_ENABLED',
    )

    def setUp(self):
        self._saved = {k: getattr(config, k, None) for k in self.KEYS}
        config.AUTO_CORRECT = True
        config.AUTO_CAPITALISE = True
        config.AUTO_TERMINAL_PUNCTUATION = True
        config.DICTIONARY = []
        config.DICTIONARY_REPLACEMENTS = {}
        config.INITIAL_PROMPT = ''
        config.STYLE_SEED_ENABLED = True

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)


class SoundexTests(unittest.TestCase):
    def test_cloud_and_claude_collide(self):
        """The whole fuzzy strategy rests on this: they sound alike."""
        self.assertEqual(postprocess._soundex('cloud'), postprocess._soundex('claude'))

    def test_distinct_words_do_not_collide(self):
        self.assertNotEqual(postprocess._soundex('cloud'), postprocess._soundex('kubernetes'))

    def test_empty_is_safe(self):
        self.assertEqual(postprocess._soundex(''), '')
        self.assertEqual(postprocess._soundex('!!!'), '')


class VocabularyTests(_ConfigPatch):
    def test_fixes_the_real_world_case(self):
        """'cloud code' -> 'Claude Code'. 26 occurrences in the user's history."""
        config.DICTIONARY = ['Claude Code']
        out = postprocess.apply('i use cloud code every day')
        self.assertIn('Claude Code', out)
        self.assertNotIn('cloud code', out.lower())

    def test_leaves_correct_text_alone(self):
        config.DICTIONARY = ['Claude Code']
        out = postprocess.apply_fuzzy_terms('I use Claude Code daily')
        self.assertEqual(out, 'I use Claude Code daily')

    def test_does_not_hijack_unrelated_use_of_a_word(self):
        """A bare 'cloud' must survive — only the full phrase is corrected."""
        config.DICTIONARY = ['Claude Code']
        out = postprocess.apply_fuzzy_terms('we deployed it to the cloud today')
        self.assertIn('cloud', out)
        self.assertNotIn('Claude', out)

    def test_single_word_terms_need_near_certainty(self):
        """Loose matching on single words would wreck ordinary prose."""
        config.DICTIONARY = ['Kubernetes']
        # Close enough — a plausible mishearing.
        self.assertIn('Kubernetes', postprocess.apply_fuzzy_terms('deploy to kubernetes now'))
        # Not close enough — must not be dragged in.
        self.assertNotIn('Kubernetes', postprocess.apply_fuzzy_terms('deploy the burner now'))

    def test_punctuation_after_a_term_is_preserved(self):
        config.DICTIONARY = ['Claude Code']
        out = postprocess.apply_fuzzy_terms('I like cloud code, honestly')
        self.assertIn('Claude Code,', out)

    def test_repairs_a_word_the_model_split_in_two(self):
        """ASR renders compounds inconsistently: 'codebase' comes out 'code base'."""
        config.DICTIONARY = ['codebase']
        out = postprocess.apply_fuzzy_terms('dig into the code base it changed')
        self.assertIn('the codebase it', out)

    def test_widening_never_swallows_an_adjacent_word(self):
        """Regression: a 2-word term matching a 3-word window ate the extra word.

        'use Claude Code' scored 0.85 against 'Claude Code' on raw string
        similarity, so the wider window matched and dropped 'use'. Widening is
        only for split/joined words now, and must compare exactly.
        """
        config.DICTIONARY = ['Claude Code']
        self.assertEqual(
            postprocess.apply_fuzzy_terms('I use Claude Code daily'),
            'I use Claude Code daily',
        )
        self.assertEqual(
            postprocess.apply_fuzzy_terms('please run Claude Code now'),
            'please run Claude Code now',
        )

    def test_literal_replacements_apply(self):
        config.DICTIONARY_REPLACEMENTS = {'code base': 'codebase'}
        out = postprocess.apply_replacements('the code base is large')
        self.assertEqual(out, 'the codebase is large')

    def test_replacement_is_whole_word_only(self):
        config.DICTIONARY_REPLACEMENTS = {'cat': 'dog'}
        self.assertEqual(postprocess.apply_replacements('concatenate'), 'concatenate')


class FormattingTests(_ConfigPatch):
    def test_capitalises_opening_word(self):
        self.assertTrue(postprocess.apply('hello there').startswith('Hello'))

    def test_capitalises_after_sentence_end(self):
        out = postprocess.fix_capitalisation('one thing. another thing')
        self.assertIn('. Another', out)

    def test_capitalises_after_a_line_break(self):
        out = postprocess.fix_capitalisation('first line here\n\nsecond paragraph starts')
        self.assertIn('\n\nSecond', out)

    def test_line_breaks_are_preserved(self):
        out = postprocess.apply('first line here\n\nsecond paragraph starts')
        self.assertIn('\n\n', out)

    def test_standalone_i_is_capitalised(self):
        self.assertIn('I think', postprocess.fix_capitalisation('i think so'))

    def test_adds_missing_terminal_punctuation(self):
        self.assertTrue(postprocess.apply('this has no ending').endswith('.'))

    def test_does_not_double_punctuate(self):
        self.assertEqual(postprocess.ensure_terminal_punctuation('done.'), 'done.')
        self.assertEqual(postprocess.ensure_terminal_punctuation('really?'), 'really?')

    def test_does_not_punctuate_a_trailing_symbol(self):
        """Text ending in an operator or bracket is mid-thought, not a sentence.

        Note this only covers trailing *symbols*. Recognising that "run make -j"
        is a shell command rather than prose needs to know which app is focused,
        which is a per-app-profile concern, not a formatting rule.
        """
        self.assertEqual(postprocess.ensure_terminal_punctuation('the flag is -'), 'the flag is -')
        self.assertEqual(postprocess.ensure_terminal_punctuation('see item 3:'), 'see item 3:')

    def test_whitespace_repair(self):
        self.assertEqual(postprocess.fix_whitespace('too   many  spaces'), 'too many spaces')
        self.assertEqual(postprocess.fix_whitespace('space , before'), 'space, before')

    def test_thousands_separator_survives(self):
        self.assertEqual(postprocess.fix_whitespace('costs 1,000 dollars'), 'costs 1,000 dollars')


class SwitchTests(_ConfigPatch):
    def test_master_switch_disables_everything(self):
        config.AUTO_CORRECT = False
        config.DICTIONARY = ['Claude Code']
        raw = 'i use cloud code'
        self.assertEqual(postprocess.apply(raw), raw)

    def test_capitalisation_can_be_disabled_alone(self):
        config.AUTO_CAPITALISE = False
        self.assertTrue(postprocess.apply('hello there').startswith('hello'))

    def test_terminal_punctuation_can_be_disabled_alone(self):
        config.AUTO_TERMINAL_PUNCTUATION = False
        self.assertFalse(postprocess.apply('no ending here').endswith('.'))

    def test_empty_input_is_untouched(self):
        self.assertEqual(postprocess.apply(''), '')
        self.assertEqual(postprocess.apply('   '), '   ')

    def test_never_raises_on_bad_config(self):
        """A malformed dictionary must not cost the user their dictation."""
        config.DICTIONARY = [None, 123, 'Claude Code']
        out = postprocess.apply('i use cloud code')
        self.assertIn('Claude Code', out)


class AsrPromptTests(_ConfigPatch):
    def test_includes_style_seed(self):
        self.assertIn('full stops', postprocess.asr_prompt())

    def test_style_seed_can_be_disabled(self):
        config.STYLE_SEED_ENABLED = False
        self.assertNotIn('full stops', postprocess.asr_prompt())

    def test_includes_dictionary_terms(self):
        config.DICTIONARY = ['Claude Code', 'Kubernetes']
        prompt = postprocess.asr_prompt()
        self.assertIn('Claude Code', prompt)
        self.assertIn('Kubernetes', prompt)

    def test_includes_user_prompt(self):
        config.INITIAL_PROMPT = 'Ritik Yadav'
        self.assertIn('Ritik Yadav', postprocess.asr_prompt())

    def test_empty_when_everything_is_off(self):
        config.STYLE_SEED_ENABLED = False
        config.DICTIONARY = []
        config.INITIAL_PROMPT = ''
        self.assertEqual(postprocess.asr_prompt(), '')


if __name__ == '__main__':
    unittest.main()


class RepetitionLoopTests(_ConfigPatch):
    """Guards against ASR decoder repetition loops.

    Fixtures are real failures pulled from the transcript store: a 0.63 s clip
    that produced 222 words, and a 0.5 s clip whose loop ran inside a single
    token. Both were injected into the user's document verbatim.
    """

    def test_collapses_single_word_loop(self):
        text = ('And, these are all ' + 'available ' * 218).strip()
        out = postprocess.collapse_repetitions(text)
        self.assertEqual(out, 'And, these are all available')

    def test_collapses_intra_token_loop(self):
        text = 'Here we can add' + 'hold' * 40
        out = postprocess.collapse_repetitions(text)
        self.assertEqual(out, 'Here we can addhold')

    def test_collapses_repeated_phrase(self):
        text = ('I will have the modability ' * 4).strip()
        out = postprocess.collapse_repetitions(text)
        self.assertEqual(out, 'I will have the modability')

    def test_keeps_natural_repetition(self):
        """Real speech repeats; two or three in a row must survive."""
        for phrase in ('that is very very good', 'no no no she did not'):
            self.assertEqual(postprocess.collapse_repetitions(phrase), phrase)

    def test_leaves_ordinary_prose_untouched(self):
        text = (
            'The report is due on Friday. I will send the draft to the team '
            'and then we can review the numbers together.'
        )
        self.assertEqual(postprocess.collapse_repetitions(text), text)

    def test_non_consecutive_repeats_are_prose(self):
        text = 'the cat sat on the mat and the dog sat on the log the end'
        self.assertEqual(postprocess.collapse_repetitions(text), text)

    def test_apply_collapses_before_formatting(self):
        text = 'and these are all ' + 'available ' * 218
        out = postprocess.apply(text)
        self.assertEqual(out, 'And these are all available.')

    def test_looks_degenerate_flags_a_loop(self):
        text = 'And these are all ' + 'available ' * 218
        self.assertTrue(
            postprocess.looks_degenerate(text, postprocess.collapse_repetitions(text))
        )

    def test_looks_degenerate_ignores_normal_text(self):
        text = ' '.join(f'word{i}' for i in range(40))
        self.assertFalse(postprocess.looks_degenerate(text, text))


class StyleSeedScalingTests(_ConfigPatch):
    """The style seed is what a short clip's decoder latches onto and loops."""

    def test_seed_included_by_default(self):
        self.assertIn('well-punctuated', postprocess.asr_prompt())

    def test_seed_can_be_dropped_but_vocabulary_kept(self):
        config.DICTIONARY = ['Claude Code']
        prompt = postprocess.asr_prompt(include_style_seed=False)
        self.assertNotIn('well-punctuated', prompt)
        self.assertIn('Claude Code', prompt)

    def test_transcriber_drops_seed_for_short_audio(self):
        import transcriber
        self.assertNotIn('well-punctuated', transcriber._asr_prompt(0.6))
        self.assertIn('well-punctuated', transcriber._asr_prompt(12.0))


class LengthScaledRunTests(_ConfigPatch):
    """Longer phrases need fewer repeats to count as a loop.

    The odds of saying the same thing verbatim collapse with every extra word,
    so the threshold falls as phrases grow. Fixture is a real miss: an
    eight-word sentence the engine emitted three times in a row, which the
    original flat threshold (4 repeats, n-grams capped at 6) sailed past.
    """

    def test_eight_word_phrase_repeated_three_times(self):
        loop = 'But then send people in the open source. ' * 3
        text = 'is there any people in the open source. ' + loop + 'A model available.'
        out = postprocess.collapse_repetitions(text)
        self.assertEqual(out.count('But then send people'), 1)
        self.assertIn('A model available.', out)
        self.assertIn('is there any people in the open source.', out)

    def test_long_phrase_repeated_twice(self):
        phrase = 'the quick brown fox jumps over the lazy dog again and again '
        out = postprocess.collapse_repetitions((phrase * 2).strip())
        self.assertEqual(out, phrase.strip())

    def test_short_phrase_twice_is_not_a_loop(self):
        """Two repeats of a short phrase is ordinary speech."""
        text = 'I think I think we should go'
        self.assertEqual(postprocess.collapse_repetitions(text), text)

    def test_medium_phrase_twice_is_not_a_loop(self):
        text = 'we need to ship we need to ship the thing'
        self.assertEqual(postprocess.collapse_repetitions(text), text)

    def test_threshold_scales_down_with_length(self):
        self.assertEqual(postprocess._run_threshold(1), 4)
        self.assertEqual(postprocess._run_threshold(4), 4)
        self.assertEqual(postprocess._run_threshold(8), 3)
        self.assertEqual(postprocess._run_threshold(12), 2)


class TailCleanerTests(unittest.TestCase):
    def test_strips_trailing_guillemets(self):
        text = "And show me the output. There is nothing else you have to do. »."
        self.assertEqual(
            postprocess.clean_hallucinated_tail(text),
            "And show me the output. There is nothing else you have to do.",
        )

    def test_strips_multi_layered_guillemets_and_thank_you(self):
        text = "what actually has happened? ». ». Thank you. What? ». Thank you."
        cleaned = postprocess.clean_hallucinated_tail(text)
        self.assertNotIn("»", cleaned)
        self.assertNotIn("Thank you", cleaned)

    def test_strips_foreign_script_tail_on_latin_text(self):
        text1 = "What is the command to create a folder in macOS. 의의의."
        self.assertEqual(
            postprocess.clean_hallucinated_tail(text1),
            "What is the command to create a folder in macOS.",
        )
        text2 = "so that the agent can decide what to do in the future. Reավ."
        self.assertEqual(
            postprocess.clean_hallucinated_tail(text2),
            "so that the agent can decide what to do in the future.",
        )

    def test_preserves_short_legitimate_thank_you(self):
        self.assertEqual(postprocess.clean_hallucinated_tail("Thank you."), "Thank you.")
        self.assertEqual(postprocess.clean_hallucinated_tail("Thank you so much."), "Thank you so much.")

    def test_a_single_closing_thank_you_is_the_users_and_is_kept(self):
        text = "Please review the change and merge it today. Thank you."
        self.assertEqual(postprocess.clean_hallucinated_tail(text), text)

    def test_a_repeated_thank_you_collapses_to_one(self):
        # Stored rows 104 and 212 ended this way.
        self.assertEqual(
            postprocess.clean_hallucinated_tail("I think so, yeah. Thank you. Thank you. Thank you."),
            "I think so, yeah. Thank you.",
        )
        self.assertEqual(
            postprocess.clean_hallucinated_tail("Nothing. Thank you. Thank you."),
            "Nothing. Thank you.",
        )

    def test_an_outro_nobody_dictates_is_stripped(self):
        self.assertEqual(
            postprocess.clean_hallucinated_tail("That is the whole plan for today. Thanks for watching."),
            "That is the whole plan for today.",
        )

    def test_the_sentence_before_a_guillemet_keeps_its_full_stop(self):
        self.assertTrue(
            postprocess.clean_hallucinated_tail("There is nothing else you have to do. ».").endswith("do.")
        )

    def test_a_guillemet_followed_by_real_dictation_is_not_a_tail(self):
        text = "He wrote « " + "and then the meeting carried on for another nine words at least."
        self.assertEqual(postprocess.clean_hallucinated_tail(text), text)

    def test_text_in_another_script_is_left_alone(self):
        # Not predominantly Latin, so nothing here is a "foreign tail".
        self.assertEqual(postprocess.clean_hallucinated_tail("Μου μου."), "Μου μου.")


class ReplacementSeparatorTests(_ConfigPatch):
    """The separator between a phrase's words was once written `[,\\\\s]+` in a
    raw string: a class of comma, backslash and the letter s. Nothing errored;
    every multi-word replacement simply stopped matching."""

    def test_a_two_word_phrase_matches_across_plain_whitespace(self):
        config.DICTIONARY_REPLACEMENTS = {'cloud code': 'Claude Code'}
        self.assertEqual(
            postprocess.apply_replacements('open cloud code and cloud  code again'),
            'open Claude Code and Claude Code again',
        )

    def test_the_separator_is_not_the_letter_s(self):
        config.DICTIONARY_REPLACEMENTS = {'code base': 'codebase'}
        self.assertEqual(postprocess.apply_replacements('codesbase'), 'codesbase')

    def test_longer_phrases_win_over_their_own_prefix(self):
        config.DICTIONARY_REPLACEMENTS = {'s50': 'STT', 's50 models': 'STT models'}
        self.assertEqual(postprocess.apply_replacements('these s50 models'), 'these STT models')


class AuditedReplacementsTests(_ConfigPatch):
    def test_replaces_technical_homophones(self):
        config.DICTIONARY_REPLACEMENTS = {
            'cloud code': 'Claude Code',
            'ea agent': 'AI agent',
            's50 models': 'STT models',
            'front-views models': 'frontier models',
            'onto that rapport': 'onto that repo',
            "it's called, see": "It's called Say",
            'package for the nga': 'package for the engineer',
            'gohercel': 'Vercel',
        }
        raw = "It's called, see, using open source s50 models and cloud code."
        out = postprocess.apply_replacements(raw)
        self.assertIn("It's called Say", out)
        self.assertIn("open source STT models", out)
        self.assertIn("Claude Code", out)

    def test_flexible_inter_word_spacing_and_commas(self):
        config.DICTIONARY_REPLACEMENTS = {
            "it's called see": "It's called Say",
            "code base": "codebase",
        }
        self.assertEqual(
            postprocess.apply_replacements("it's called, see, a tool"),
            "It's called Say, a tool",
        )
        self.assertEqual(
            postprocess.apply_replacements("dig into the code, base now"),
            "dig into the codebase now",
        )
