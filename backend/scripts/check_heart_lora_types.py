"""Check the optional Heart worker against real pinned runtime packages, without GPU work."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True, type=Path, help='Prepared trainer interpreter; packages are inspected, never installed.')
    parser.add_argument('--simpletuner', required=True, type=Path)
    parser.add_argument('--heartlib', required=True, type=Path)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    roots = (args.simpletuner.resolve(), args.heartlib.resolve())
    for root, revision in zip(roots, ('a0e7ffadfcd0d3d0f68b400f9697c464da6f9408', '5858ca8f1ffe58d62be7c007ea55f1033c5ca456'), strict=True):
        actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != revision:
            raise ValueError('heart_typing_source_revision_mismatch')
    with tempfile.TemporaryDirectory(prefix='openfabric-heart-types-') as temporary:
        directory = Path(temporary)
        # The core's small torch declaration is for legacy helpers; use real torch here.
        shutil.copyfile(backend / 'typings/soundfile.pyi', directory / 'soundfile.pyi')
        config = directory / 'mypy.ini'
        config.write_text('[mypy]\n', encoding='utf-8')
        paths = (directory, roots[0], roots[1] / 'src/heartlib', backend)
        environment = {**os.environ, 'MYPYPATH': os.pathsep.join(str(path) for path in paths)}
        subprocess.run([sys.executable, '-m', 'mypy', '--config-file', str(config), '--strict', '--follow-imports=silent',
            '--python-executable', str(args.python), str(backend / 'scripts/heart_lora_worker.py')],
            check=True, env=environment, cwd=backend.parent)


if __name__ == '__main__':
    main()
