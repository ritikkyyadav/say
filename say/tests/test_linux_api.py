from types import SimpleNamespace
from unittest.mock import Mock

from controller import DictationState
from fastapi.testclient import TestClient
from platform_support import get_platform_info
from server.api import create_app
from server.linux_api import register_linux_controls
from server.store import TranscriptStore

AUTH = {'Authorization': 'Bearer linux-test-token'}


def setup_api(tmp_path, **overrides):
    store = TranscriptStore(tmp_path / 'linux.db')
    ctrl = SimpleNamespace(
        state=DictationState.READY, last_result=None, recording_sequence=0,
        can_start_recording=lambda: True,
    )
    for key, value in overrides.items():
        setattr(ctrl, key, value)
    action = Mock(return_value=True)
    app = create_app(store=store, auth_token='linux-test-token')
    register_linux_controls(app, ctrl, action)
    return TestClient(app), store, ctrl, action


def test_linux_controls_and_results_require_authentication(tmp_path):
    client, store, ctrl, action = setup_api(tmp_path)
    with client:
        assert client.get('/platform').status_code == 401
        assert client.get('/dictation').status_code == 401
        assert client.post('/dictation/control', json={'action': 'start'}).status_code == 401
        assert client.get('/platform', headers=AUTH).status_code == 200
    action.assert_not_called()


def test_platform_metadata_is_honest_about_wayland(tmp_path, monkeypatch):
    client, store, ctrl, action = setup_api(tmp_path)
    info = get_platform_info(system='linux', env={'WAYLAND_DISPLAY': 'wayland-test'})
    monkeypatch.setattr('platform_support.get_platform_info', lambda: info)
    with client:
        data = client.get('/platform', headers=AUTH).json()
    assert data['manual_paste']
    assert not data['automatic_insertion']
    assert not data['global_hotkey']


def test_result_is_complete_not_the_event_preview(tmp_path):
    text = 'Full transcript ' + 'with another word ' * 30
    result = {'id': None, 'text': text, 'delivery': 'manual'}
    client, store, ctrl, action = setup_api(tmp_path, last_result=result, recording_sequence=1)
    with client:
        data = client.get('/dictation', headers=AUTH).json()
    assert data['latest'] == result
    assert data['state'] == 'ready'
    assert not data['shortcut_started']


def test_no_speech_does_not_offer_an_old_result_as_new(tmp_path):
    client, store, ctrl, action = setup_api(tmp_path, recording_sequence=1)
    store.insert(text='Old words.', audio_seconds=1, inference_seconds=0.1,
                 model='test', inject_method='manual', success=False, target_app=None)
    with client:
        assert client.get('/dictation', headers=AUTH).json()['latest'] is None


def test_actions_are_validated_and_busy_is_not_success(tmp_path):
    client, store, ctrl, action = setup_api(tmp_path)
    with client:
        response = client.post('/dictation/control', json={'action': 'start'}, headers=AUTH)
        assert response.json() == {'accepted': True}
        action.assert_called_once_with('start')
        assert client.post('/dictation/control', json={'action': 'shell'}, headers=AUTH).status_code == 422
        action.return_value = False
        assert client.post('/dictation/control', json={'action': 'stop'}, headers=AUTH).status_code == 409
