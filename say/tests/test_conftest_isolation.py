"""The test suite must never touch the user's real files.

These assert on the session fixture in `conftest.py` rather than on any
engine behaviour: if a future change binds a path at import time again, one
of them fails before the real ledger, settings file or database is written.
"""
from __future__ import annotations

import os


def _under(path: str, root: str) -> bool:
    return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)


def test_the_metrics_ledger_writes_under_the_test_root(isolated_user_data):
    import metrics

    ledger = metrics.get_ledger()
    ledger.record(event='test', note='isolation probe')
    assert _under(ledger._path, isolated_user_data)
    assert _under(metrics.LEDGER_PATH, isolated_user_data)
    assert os.path.exists(ledger._path)


def test_the_settings_file_lives_under_the_test_root(isolated_user_data):
    import settings

    assert _under(settings.SETTINGS_DIR, isolated_user_data)
    assert _under(settings.SETTINGS_FILE, isolated_user_data)
    assert _under(settings.FIRST_RUN_MARKER, isolated_user_data)


def test_the_transcript_store_lives_under_the_test_root(isolated_user_data):
    from server import store as store_module

    assert _under(str(store_module.get_store().path), isolated_user_data)


def test_the_model_caches_point_at_the_test_root(isolated_user_data):
    import model_catalog

    assert _under(model_catalog._cache_root(), isolated_user_data)
    assert _under(model_catalog.moonshine_cache_root(), isolated_user_data)


def test_no_real_user_path_is_reachable_by_default(isolated_user_data):
    """The real locations, spelled out, so a regression names its victim."""
    import metrics
    import settings
    from server import store as store_module

    home = os.path.expanduser('~')
    real = (
        os.path.join(home, 'Library', 'Logs', 'Say', 'metrics.jsonl'),
        os.path.join(home, 'Library', 'Application Support', 'Say', 'settings.json'),
        os.path.join(home, 'Library', 'Application Support', 'Say', 'transcripts.db'),
    )
    current = (
        metrics.get_ledger()._path,
        settings.SETTINGS_FILE,
        str(store_module.get_store().path),
    )
    for real_path, current_path in zip(real, current, strict=True):
        assert os.path.realpath(current_path) != os.path.realpath(real_path)


def test_importing_a_tool_does_not_load_the_users_settings():
    """Regression: replay_bench applied settings.json at import, so a test that
    imported a tool at collection time pulled the developer's real model and
    overlay theme into the run and broke three unrelated tests."""
    import importlib
    from unittest.mock import patch

    import settings

    with patch.object(settings, 'apply_to_config') as applied:
        for name in ('tools.replay_bench', 'tools.accuracy_bench', 'tools.disagreement_mine',
                     'tools.corpus_audit', 'tools.scorecard'):
            importlib.reload(importlib.import_module(name))
    applied.assert_not_called()


def test_a_settings_reload_cannot_leak_into_the_next_test_part_one():
    """Part one reloads settings, which points it back at the real file."""
    import importlib

    import settings

    importlib.reload(settings)
    assert settings.SETTINGS_FILE.startswith(os.path.expanduser('~/Library'))


def test_a_settings_reload_cannot_leak_into_the_next_test_part_two(isolated_user_data):
    """Part two runs next: the per-test fixture must have re-pointed it."""
    import settings

    assert settings.SETTINGS_FILE.startswith(isolated_user_data), settings.SETTINGS_FILE
