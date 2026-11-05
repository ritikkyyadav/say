"""The diagnostic must not call a measured rewrite a clean insertion."""
from __future__ import annotations

from contextlib import redirect_stdout
from importlib import import_module
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from server.store import TranscriptStore

# The developer tool is also an executable script; import dynamically to avoid
# mypy assigning the same file two module names in its project-wide check.
corrections_report = import_module('tools.corrections_report')


def test_report_distinguishes_measured_rewrite_from_unmeasured():
    with TemporaryDirectory() as directory:
        path = Path(directory) / 't.db'
        store = TranscriptStore(path)
        rid = store.insert(
            text='a b c', audio_seconds=2, inference_seconds=0.1,
            model='m', inject_method='accessibility', target_app='com.test.Editor',
            observation_status='pending',
        )
        store.finish_observation(rid, 'rewrite')
        store.close()
        output = StringIO()
        with patch.object(corrections_report, 'default_db_path', return_value=path), \
                patch('sys.argv', ['corrections_report.py']), redirect_stdout(output):
            assert corrections_report.main() == 0
        result = output.getvalue()
        assert 'Read-back coverage 1/1 (100.0%)' in result
        assert 'rewrite: 1' in result
        assert 'No scoreable insertions' in result
        assert 'none measured yet' not in result
