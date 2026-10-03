"""Snippets: the whole utterance or nothing, two words or more, typed verbatim."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import config
import snippets
from controller import DictationController
from fastapi.testclient import TestClient
from injector import InjectionResult
from server import store as store_module
from server.api import create_app

LINK = 'https://cal.example.com/ritik/30min'


class MatchTests(unittest.TestCase):
    def test_the_whole_utterance_expands_whatever_the_case_and_punctuation(self):
        table = {'insert my calendar link': LINK}
        for said in ('Insert my calendar link.', 'insert my calendar link', 'Insert, my calendar link!'):
            self.assertEqual(snippets.match(said, table), LINK)

    def test_the_trigger_inside_a_sentence_never_fires(self):
        table = {'my calendar link': LINK}
        self.assertIsNone(snippets.match('Can you send them my calendar link today?', table))

    def test_nothing_said_matches_nothing(self):
        self.assertIsNone(snippets.match('', {'my link': LINK}))

    def test_hindi_triggers_work_too(self):
        self.assertEqual(snippets.match('मेरा पता', {'मेरा पता': 'पता'}), 'पता')


class CheckTests(unittest.TestCase):
    def test_a_one_word_trigger_is_refused(self):
        with self.assertRaises(snippets.BadSnippet) as ctx:
            snippets.check({'yes': 'Yes, absolutely.'})
        self.assertIn('at least 2 words', str(ctx.exception))

    def test_two_spellings_of_the_same_trigger_are_refused(self):
        with self.assertRaises(snippets.BadSnippet):
            snippets.check({'My link': 'a', 'my link.': 'b'})

    def test_an_empty_text_is_refused(self):
        with self.assertRaises(snippets.BadSnippet):
            snippets.check({'my link': '   '})

    def test_a_good_set_is_returned_trimmed(self):
        self.assertEqual(snippets.check({'  my link ': LINK}), {'my link': LINK})


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._saved = config.SNIPPETS

    def tearDown(self):
        config.SNIPPETS = self._saved
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._tmp.cleanup()

    def _dictate(self, heard):
        from tests.test_controller import FakeRecorder, FakeTranscriber

        typed = []
        c = DictationController(
            recorder=FakeRecorder([0.05] * config.SAMPLE_RATE * 2),
            transcriber=FakeTranscriber(text=heard),
            injector=lambda t: (typed.append(t), InjectionResult(True, 'accessibility'))[1],
            logger=None,
        )
        c.load_model()
        with patch('controller.get_frontmost_app', return_value='a'), \
                patch('controller.notify_transcription_complete'):
            c.start_recording()
            c.stop_recording()
        return typed

    def test_a_snippet_is_typed_verbatim_without_clean_up(self):
        config.SNIPPETS = {'insert my calendar link': LINK}
        # Clean-up would capitalise and add a full stop; a URL must survive as written.
        self.assertEqual(self._dictate('Insert my calendar link.'), [LINK])

    def test_ordinary_dictation_is_untouched(self):
        config.SNIPPETS = {'insert my calendar link': LINK}
        self.assertEqual(self._dictate('send them my calendar link'),
                         ['Send them my calendar link.'])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = store_module.TranscriptStore(os.path.join(self._tmp.name, 't.db'))
        self.client = TestClient(create_app(store=self.store, auth_token='secret'))
        self.auth = {'Authorization': 'Bearer secret'}
        self._saved = config.SNIPPETS

    def tearDown(self):
        config.SNIPPETS = self._saved
        self.store.close()
        self._tmp.cleanup()

    def test_saving_and_reading_back(self):
        r = self.client.post('/snippets', json={'snippets': {'my calendar link': LINK}},
                             headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['snippets'], {'my calendar link': LINK})
        self.assertEqual(self.client.get('/snippets', headers=self.auth).json()['min_trigger_words'], 2)

    def test_a_bad_set_is_refused_and_nothing_written(self):
        import settings

        config.SNIPPETS = {}
        r = self.client.post('/snippets', json={'snippets': {'yes': 'x'}}, headers=self.auth)
        self.assertEqual(r.status_code, 400)
        self.assertIn('2 words', r.json()['detail'])
        self.assertNotIn('SNIPPETS', settings.all_settings())

    def test_it_needs_the_token(self):
        self.assertEqual(self.client.get('/snippets').status_code, 401)
