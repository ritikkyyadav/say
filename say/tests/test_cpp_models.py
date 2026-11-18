import hashlib
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import config
import cpp_models
import model_catalog
import model_manager
import numpy as np
import pytest
from transcriber import Transcriber

MODEL = 'whisper-cpp/tiny'


class Response:
    def __init__(self, chunks):
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, **kwargs):
        yield from self.chunks


def asset(monkeypatch, tmp_path, data=b'valid model bytes'):
    monkeypatch.setenv('SAY_WHISPER_CPP_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setitem(cpp_models.ASSETS, MODEL, cpp_models.ModelAsset(
        'ggml-tiny.bin', len(data), hashlib.sha256(data).hexdigest(),
    ))
    return data


def test_download_is_verified_atomic_and_reused(monkeypatch, tmp_path):
    import requests

    data = asset(monkeypatch, tmp_path)
    fetch = Mock(return_value=Response([data[:5], data[5:]]))
    monkeypatch.setattr(requests, 'get', fetch)
    progress = []
    path = cpp_models.ensure_downloaded(MODEL, progress=lambda done, total: progress.append(done))
    assert path.read_bytes() == data
    assert cpp_models.is_downloaded(MODEL)
    assert progress[0] == 0 and progress[-1] == len(data)
    assert cpp_models.REVISION in fetch.call_args.args[0]
    assert not path.with_suffix('.part').exists()
    assert cpp_models.ensure_downloaded(MODEL) == path
    fetch.assert_called_once()
    path.write_bytes(b'x' * len(data))
    assert not cpp_models.is_downloaded(MODEL)


def test_corrupt_download_never_becomes_installed(monkeypatch, tmp_path):
    import requests

    data = asset(monkeypatch, tmp_path)
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: Response([b'x' * len(data)]))
    with pytest.raises(ValueError, match='integrity'):
        cpp_models.ensure_downloaded(MODEL)
    assert not cpp_models.is_downloaded(MODEL)
    assert not cpp_models.model_path(MODEL).exists()
    assert not cpp_models.model_path(MODEL).with_suffix('.part').exists()


def test_cancel_removes_partial_without_installing(monkeypatch, tmp_path):
    import requests

    data = asset(monkeypatch, tmp_path)
    stop = threading.Event()
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: Response([data[:4], data[4:]]))
    def progress(done, total):
        if done:
            stop.set()
    with pytest.raises(cpp_models.DownloadCancelled):
        cpp_models.ensure_downloaded(MODEL, progress=progress, cancelled=stop.is_set)
    assert not cpp_models.is_downloaded(MODEL)
    assert not cpp_models.model_path(MODEL).with_suffix('.part').exists()


def test_model_path_cannot_follow_a_symlink_outside_cache(monkeypatch, tmp_path):
    asset(monkeypatch, tmp_path)
    root = cpp_models.cache_root()
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'tiny').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='escapes'):
        cpp_models.ensure_downloaded(MODEL)
    assert list(outside.iterdir()) == []


def test_cpp_dispatch_uses_real_contract_without_native_import(monkeypatch, tmp_path):
    observed = {}
    class Model:
        def __init__(self, path, **kwargs):
            self._ctx = object()
            observed['constructor'] = kwargs
        def transcribe(self, audio, **kwargs):
            observed['params'] = kwargs
            observed['dtype'] = audio.dtype
            return [SimpleNamespace(text='Local speech works.')]
    module = ModuleType('pywhispercpp.model')
    module.Model = Model
    monkeypatch.setitem(sys.modules, 'pywhispercpp', ModuleType('pywhispercpp'))
    monkeypatch.setitem(sys.modules, 'pywhispercpp.model', module)
    monkeypatch.setattr(cpp_models, 'ensure_downloaded', lambda model: tmp_path / 'ggml.bin')
    monkeypatch.setattr(config, 'LANGUAGE', None)
    transcriber = Transcriber(MODEL)
    transcriber.load()
    audio = np.full(config.SAMPLE_RATE, 0.05, dtype=np.float32)
    assert transcriber.transcribe(audio, prompt_override='Local terms') == 'Local speech works.'
    assert observed['constructor']['context_params'] == {'use_gpu': False}
    assert observed['params']['language'] == 'auto'
    assert observed['params']['initial_prompt'] == 'Local terms'
    assert observed['params']['no_context']
    assert observed['dtype'] == np.float32
    assert transcriber.unload_idle()
    assert transcriber._cpp is None and not transcriber.is_loaded


def test_incompatible_models_are_not_activated_or_downloaded(monkeypatch):
    manager = model_manager.ModelManager()
    persist = Mock()
    monkeypatch.setattr(model_manager, 'supports_backend', lambda backend: False)
    monkeypatch.setattr(model_manager.settings_mod, 'set_value', persist)
    with pytest.raises(ValueError, match='operating system'):
        manager.set_active(MODEL)
    with pytest.raises(ValueError, match='operating system'):
        manager.start_download(MODEL)
    persist.assert_not_called()
    assert config.VALID_MODELS == model_catalog.CATALOG_IDS
