# Contributing to Say

Thanks for your interest in improving Say. Quick guide:

## Dev setup

```bash
# Clone, create venv, install deps + dev tools
git clone <repo-url> Say && cd Say
python3 -m venv ~/.venvs/say
source ~/.venvs/say/bin/activate
pip install -r say/requirements.txt
pip install ruff mypy pytest pytest-cov
```

Run the app from a terminal during development:

```bash
cd say
python main.py     # CLI/debug mode
# or
python app.py      # menu-bar mode
```

## Tests

```bash
cd say
python -m pytest tests/
# or with coverage:
python -m pytest --cov=. tests/
```

## Linting

```bash
ruff check say
mypy say        # advisory; type coverage matures over time
```

## Building a `.app`

```bash
cd say
make all        # build + bundle model + create unsigned DMG
```

To codesign and notarize, set `DEVELOPER_ID`, `APPLE_ID`, `APPLE_TEAM_ID`,
`APPLE_APP_PASSWORD` in your environment and run `make sign notarize`.

## Code style

- `ruff` enforces formatting + lint. Run `ruff check --fix say` to autofix.
- Default to writing no comments. Add a comment only when the *why* is
  non-obvious (a hidden constraint, a workaround for a specific bug,
  surprising behavior).
- Avoid backwards-compatibility shims unless explicitly needed.

## Filing issues

Include:
- macOS version (`sw_vers -productVersion`)
- Apple Silicon vs Intel
- Python version (`python3 --version`)
- The relevant log file: `~/Library/Logs/Say/say.log`

For mic / hotkey bugs, run the **Run Diagnostics** menu item and paste
the output.
