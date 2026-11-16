"""Provenance hashes settings without leaking their contents to API rows."""
from __future__ import annotations

from unittest.mock import patch

import config
from provenance import snapshot


def test_fingerprint_changes_when_a_replacement_changes():
    before = snapshot()
    with patch.object(config, 'DICTIONARY_REPLACEMENTS', {'one': 'secret term'}):
        after = snapshot()
    assert before['pipeline_hash'] != after['pipeline_hash']
    assert 'secret term' not in str(after)
    assert after['pipeline_version'] == before['pipeline_version']
    assert after['model_backend'] == before['model_backend']


def test_fingerprint_changes_for_prompt_and_language():
    before = snapshot()
    with patch.object(config, 'INITIAL_PROMPT', 'private meeting topic'), \
            patch.object(config, 'LANGUAGE', 'en'):
        after = snapshot()
    assert before['pipeline_hash'] != after['pipeline_hash']
    assert after['language'] == 'en'
    assert 'private meeting topic' not in str(after)


def test_a_packaged_build_still_has_a_code_version(tmp_path, monkeypatch):
    """The shipped engine has no .py sources to hash. Without a fallback every
    dictation the real app records carried no version at all."""
    import sys

    import provenance

    exe = tmp_path / 'Say'
    exe.write_bytes(b'build one')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(exe))
    first = provenance._frozen_build_id()
    assert first is not None

    exe.write_bytes(b'a different, longer build')
    assert provenance._frozen_build_id() != first


def test_a_dev_checkout_is_not_mistaken_for_a_packaged_build(monkeypatch):
    import sys

    import provenance

    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert provenance._frozen_build_id() is None
