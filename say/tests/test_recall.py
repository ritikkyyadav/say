"""Re-insert and undo: the rule that keeps undo from deleting the user's text."""
from __future__ import annotations

import recall


def _d(text='Ship it today.', app='com.app.a', at=100.0, keys=7):
    return recall.Delivery(transcript_id=1, text=text, target_app=app, at=at, keystrokes=keys)


def test_undo_is_allowed_only_when_nothing_has_changed():
    assert recall.undo_refusal(_d(), now=105.0, frontmost='com.app.a', keystrokes=7) is None


def test_nothing_typed_yet():
    assert 'Nothing to undo' in recall.undo_refusal(None, now=1, frontmost='x', keystrokes=0)


def test_a_single_key_press_or_click_since_refuses():
    reason = recall.undo_refusal(_d(), now=105.0, frontmost='com.app.a', keystrokes=8)
    assert reason and 'typed since' in reason


def test_not_knowing_about_keystrokes_refuses():
    """The fallback hotkey cannot see the keyboard; guessing would be unsafe."""
    assert recall.undo_refusal(_d(keys=None), now=105.0, frontmost='com.app.a', keystrokes=7)
    assert recall.undo_refusal(_d(), now=105.0, frontmost='com.app.a', keystrokes=None)


def test_switching_apps_refuses():
    reason = recall.undo_refusal(_d(), now=105.0, frontmost='com.app.b', keystrokes=7)
    assert reason and 'switched apps' in reason


def test_too_late_refuses():
    late = 100.0 + recall.UNDO_WINDOW_S + 1
    reason = recall.undo_refusal(_d(), now=late, frontmost='com.app.a', keystrokes=7)
    assert reason and 'Too late' in reason


def test_one_backspace_per_character_for_plain_text():
    assert recall.backspaces_for('Ship it today.') == 14
    assert recall.backspaces_for('café, naïve') == 11      # precomposed accents
    assert recall.backspaces_for('Отправь отчёт') == 13    # Cyrillic


def test_text_apps_delete_differently_is_refused_not_guessed():
    assert recall.backspaces_for('done 👍') is None              # astral emoji
    assert recall.backspaces_for('café') is None           # decomposed accent
    assert recall.backspaces_for('मुझे जाना है') is None         # Devanagari vowel signs
    assert recall.backspaces_for('') is None
    reason = recall.undo_refusal(_d(text='done 👍'), now=101, frontmost='com.app.a', keystrokes=7)
    assert reason and 'characters' in reason
