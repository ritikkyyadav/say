"""Session-wide isolation of the user's own data files.

Every test used to be responsible for redirecting the paths it touched, and
one class of them forgot: the controller's injection-failure tests redirected
the transcript store per test but not the metrics ledger, so every run
appended real rows to `~/Library/Logs/Say/metrics.jsonl`. By the time it was
noticed the file held 288 records of `stage: injection, message: "boom"` —
phantom failures that any error-rate metric would have counted as real.

This fixture is the floor beneath every test: before the first one runs, each
path the engine can write to points into a temporary directory — the metrics
ledger, the settings file and first-run marker, the transcript store (and with
it the recordings and exports directories, which derive from the store's
path) and both model caches. Per-test fixtures that already redirect these
keep working exactly as before; they simply can no longer fall through to the
real files when they are missing.

Tests never configure logging, so `say.log` needs no redirect: the `say`
logger has no file handler in a test process and its records propagate to
pytest's capture instead.
"""
from __future__ import annotations

import os
import tempfile

import pytest

_CACHE_ENV = ('HF_HUB_CACHE', 'HUGGINGFACE_HUB_CACHE', 'HF_HOME', 'SAY_MOONSHINE_CACHE')


@pytest.fixture(scope='session', autouse=True)
def isolated_user_data():
    """Redirect every user-data path into a temp directory for the whole run.

    Yields the temporary root so a test can assert against it.
    """
    import metrics
    import settings
    from server import store as store_module

    tmp = tempfile.TemporaryDirectory(prefix='say-tests-')
    root = tmp.name
    support = os.path.join(root, 'Application Support', 'Say')
    os.makedirs(support, exist_ok=True)

    saved_env = {k: os.environ.get(k) for k in _CACHE_ENV}
    os.environ['HF_HUB_CACHE'] = os.path.join(root, 'hf-hub')
    os.environ.pop('HUGGINGFACE_HUB_CACHE', None)
    os.environ.pop('HF_HOME', None)
    os.environ['SAY_MOONSHINE_CACHE'] = os.path.join(root, 'moonshine')

    saved_metrics = (metrics.LEDGER_PATH, metrics._ledger)
    metrics.LEDGER_PATH = os.path.join(root, 'Logs', 'Say', 'metrics.jsonl')
    metrics._ledger = None

    saved_settings = (settings.SETTINGS_DIR, settings.SETTINGS_FILE, settings.FIRST_RUN_MARKER)
    settings.SETTINGS_DIR = support
    settings.SETTINGS_FILE = os.path.join(support, 'settings.json')
    settings.FIRST_RUN_MARKER = os.path.join(support, 'first_run_complete')

    store_module.reset_store_for_tests(os.path.join(support, 'transcripts.db'))

    try:
        yield root
    finally:
        metrics.LEDGER_PATH, metrics._ledger = saved_metrics
        settings.SETTINGS_DIR, settings.SETTINGS_FILE, settings.FIRST_RUN_MARKER = saved_settings
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            store_module.get_store().close()
        except Exception:
            pass
        store_module._store = None
        tmp.cleanup()
