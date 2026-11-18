"""Offline recognition checks for owned package artifacts on native CI only."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from smoke_common import ROOT, isolated_env, speech_fixture, word_error_rate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--test-cpp', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.environ.get('CI') != 'true':
        raise SystemExit('Run these package tests on the native Linux CI runner.')
    engine, output = args.engine.resolve(), args.out.resolve()
    if not engine.is_relative_to(ROOT) or not output.is_relative_to(ROOT):
        raise SystemExit('Test inputs and outputs must stay inside this checkout.')
    if engine.name != 'SayEngine' or not engine.is_file():
        raise SystemExit('Expected the owned SayEngine package artifact.')
    manifest = json.loads((engine.parent / '_internal' / 'linux-build.json').read_text())
    assert manifest['source_commit'] == os.environ['GITHUB_SHA'] and manifest['frozen']
    with engine.open('rb') as source:
        assert source.read(4) == b'\x7fELF'
    output.mkdir(parents=True, exist_ok=True)
    audio = speech_fixture(output)
    env = isolated_env(output / 'isolated')

    def execute(arguments: list[str], environment: dict[str, str], log: Path) -> None:
        with log.open('w') as stream:
            completed = subprocess.run([str(engine), *arguments], env=environment,
                                       stdout=stream, stderr=subprocess.STDOUT, timeout=240)
        if completed.returncode:
            raise RuntimeError(f'Packaged recognition failed; see {log}.')

    results = []
    models = ['moonshine/medium-streaming-en']
    if args.test_cpp:
        models.append('whisper-cpp/tiny')
    for model in models:
        if model.startswith('whisper-cpp'):
            online = dict(env)
            for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy',
                        'all_proxy', 'HF_HUB_OFFLINE'):
                online.pop(key, None)
            execute(['--model', model, '--predownload'], online, output / 'cpp-download.log')
        name = model.rsplit('/', 1)[-1]
        target = output / f'{name}.json'
        execute(['--model', model, '--transcribe-file', str(audio), '--output', str(target)],
                env, output / f'{name}.log')
        text = json.loads(target.read_text())['text']
        score = word_error_rate(text)
        assert text.strip() and score <= 0.45, f'{model}: synthetic smoke WER {score:.3f}: {text}'
        results.append({'model': model, 'text': text, 'synthetic_word_error_rate': score})
    report = {'source_commit': manifest['source_commit'], 'version': manifest['version'],
              'fixture': 'synthetic eSpeak speech, not an accuracy benchmark',
              'external_network_blocked_during_decode': True, 'results': results}
    (output / 'cli-smoke.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
