from unittest.mock import Mock

import config
import controller as controller_module
import numpy as np
import pytest
from controller import DictationController, DictationState
from server.store import TranscriptStore


class Recorder:
    live_seconds = 1.0

    def start(self):
        pass

    def stop(self):
        return np.full(16_000, 0.1, dtype='float32')


class Transcriber:
    def __init__(self, text='Manual dictation works.'):
        self.text = text

    def transcribe(self, audio, **kwargs):
        return self.text


def make_controller(monkeypatch, tmp_path, **kwargs):
    monkeypatch.setattr(config, 'STREAMING_TRANSCRIPTION', False)
    monkeypatch.setattr(config, 'SAVE_RECORDINGS', False)
    monkeypatch.setattr(controller_module, 'notify_error', Mock())
    store = TranscriptStore(tmp_path / 'transcripts.db')
    monkeypatch.setattr(controller_module, 'get_store', lambda: store)
    injector = Mock()
    clipboard = Mock(return_value=False)
    events = []
    outcomes = []
    monkeypatch.setattr(controller_module, 'record_press',
                        lambda outcome, **fields: outcomes.append(outcome))
    ctrl = DictationController(
        Recorder(), Transcriber(), injector=injector, logger=None,
        clipboard_writer=clipboard, status_callback=events.append,
        frontmost=lambda: None, **kwargs,
    )
    ctrl.state = DictationState.READY
    return ctrl, store, injector, clipboard, events, outcomes


@pytest.mark.parametrize(('mode', 'manual_delivery'), [('manual', False), ('ptt', True)])
def test_manual_delivery_is_not_reported_as_inserted(monkeypatch, tmp_path, mode, manual_delivery):
    ctrl, store, injector, clipboard, events, outcomes = make_controller(
        monkeypatch, tmp_path, manual_delivery=manual_delivery,
    )
    assert ctrl.start_recording(mode=mode)
    assert ctrl.stop_recording()
    injector.assert_not_called()
    clipboard.assert_not_called()
    row = store.list(limit=1)[0]
    assert row.text == 'Manual dictation works.'
    assert not row.success
    assert row.inject_method == 'manual'
    assert ctrl.last_result['delivery'] == 'manual'
    assert ctrl.last_result['text'] == row.text
    assert ctrl.state == DictationState.READY
    assert 'Ready to copy' in events[-1].message
    assert outcomes == ['held']


def test_linux_automatic_delivery_refuses_unknown_focus(monkeypatch, tmp_path):
    ctrl, store, injector, clipboard, events, outcomes = make_controller(
        monkeypatch, tmp_path, require_focus=True,
    )
    assert ctrl.start_recording()
    assert ctrl.stop_recording()
    injector.assert_not_called()
    assert store.list(limit=1)[0].inject_method == 'held'
    assert 'could not be verified' in events[-1].message
    assert outcomes == ['held']


def test_full_manual_result_survives_a_store_failure(monkeypatch, tmp_path):
    ctrl, store, injector, clipboard, events, outcomes = make_controller(monkeypatch, tmp_path)
    text = ' '.join(f'Word{i}' for i in range(40)) + '.'
    ctrl.transcriber.text = text
    monkeypatch.setattr(controller_module, 'get_store', Mock(side_effect=OSError('disk full')))
    assert ctrl.start_recording(mode='manual')
    assert ctrl.stop_recording()
    assert len(ctrl.last_result['text']) > 80
    assert ctrl.last_result['id'] is None
    assert 'history could not be saved' in events[-1].message
    injector.assert_not_called()
    clipboard.assert_not_called()
    assert outcomes == ['held']
