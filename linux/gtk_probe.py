"""A real GTK input target for CI; it never manufactures dictated text."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed')
    parser.add_argument('--paste-on-map', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.out.resolve()
    if sys.platform != 'linux' or os.environ.get('CI') != 'true' or not output.is_relative_to(root):
        raise SystemExit('This input target is restricted to native Linux CI artifacts.')
    import gi

    gi.require_version('Gtk', '3.0')
    from gi.repository import Gdk, GLib, Gtk

    window = Gtk.Window(title='Say Linux input probe')
    window.set_default_size(540, 240)
    view = Gtk.TextView()
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    window.add(view)
    buffer = view.get_buffer()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('')
    def changed(current) -> None:
        output.write_text(current.get_text(current.get_start_iter(), current.get_end_iter(), True))
    buffer.connect('changed', changed)
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    if args.seed is not None:
        clipboard.set_text(args.seed, -1)
    window.connect('destroy', Gtk.main_quit)
    window.show_all()
    view.grab_focus()
    if args.paste_on_map:
        def paste() -> bool:
            view.emit('paste-clipboard')
            return False
        GLib.timeout_add(600, paste)
    Gtk.main()


if __name__ == '__main__':
    main()
