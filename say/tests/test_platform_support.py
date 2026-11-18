from pathlib import Path

from platform_paths import application_data_dir, application_log_dir, ensure_private_directory
from platform_support import get_platform_info, linux_session, supports_backend


def test_x11_supports_hotkey_and_automatic_delivery():
    info = get_platform_info(system='linux', env={'DISPLAY': ':99'})
    assert info['session'] == 'x11'
    assert info['automatic_insertion'] and info['global_hotkey'] and info['overlay']
    assert info['hotkey_label'] == 'Ctrl+Space'
    assert not info['manual_paste']
    assert info['supported_backends'] == ['moonshine', 'whisper-cpp']


def test_wayland_does_not_claim_xwayland_is_global_input():
    info = get_platform_info(system='linux', env={'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0'})
    assert info['session'] == 'wayland'
    assert info['manual_paste']
    assert not info['automatic_insertion']
    assert not info['global_hotkey']
    assert not info['overlay']
    assert info['hotkey_label'] is None


def test_wayland_session_is_recognised_without_display_variable():
    assert linux_session({'XDG_SESSION_TYPE': 'wayland'}) == 'wayland'
    assert linux_session({}) == 'headless'
    assert not get_platform_info(system='linux', env={})['automatic_insertion']


def test_mac_capabilities_remain_native():
    info = get_platform_info(system='darwin', env={})
    assert info['platform'] == 'macos'
    assert info['hotkey_label'] == 'Fn'
    assert info['automatic_insertion'] and info['overlay']
    assert supports_backend('whisper-mlx', 'darwin')
    assert not supports_backend('whisper-mlx', 'linux')
    assert supports_backend('whisper-cpp', 'linux')
    assert not supports_backend('whisper-cpp', 'darwin')


def test_macos_data_and_log_contracts_do_not_move():
    home = Path('/example/home')
    assert application_data_dir(system='darwin', home=home) == home / 'Library/Application Support/Say'
    assert application_log_dir(system='darwin', home=home) == home / 'Library/Logs/Say'


def test_linux_paths_agree_with_the_shell_xdg_contract(tmp_path):
    env = {'XDG_DATA_HOME': str(tmp_path / 'data')}
    data = application_data_dir(system='linux', env=env, home=tmp_path)
    assert data == tmp_path / 'data/Say'
    assert application_log_dir(system='linux', env=env, home=tmp_path) == data / 'logs'
    assert application_data_dir(system='linux', env={'XDG_DATA_HOME': 'relative'}, home=tmp_path) == tmp_path / '.local/share/Say'


def test_windows_data_path_keeps_appdata():
    assert application_data_dir(system='win32', env={'APPDATA': '/roaming'}, home=Path('/home')) == Path('/roaming/Say')


def test_private_data_directories_are_owner_only(tmp_path):
    target = tmp_path / 'Say'
    ensure_private_directory(target)
    assert target.stat().st_mode & 0o777 == 0o700
