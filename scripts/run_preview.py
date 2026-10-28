"""Run an installed candidate in a separate home; never activate the main installation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def preview_command(home: Path, port: int):
    root = home.expanduser().resolve()
    if root == (Path.home() / '.nerv').resolve():
        raise ValueError('Choose a separate preview home, not the main NERV home')
    if not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535')
    record = json.loads((root / 'active.json').read_text(encoding='utf-8'))
    python = Path(record['python'])
    if not python.is_absolute() or not python.is_file() or not python.resolve().is_relative_to(root):
        raise ValueError('Install the candidate into this preview home before starting it')
    return root, [str(python), '-I', '-m', 'nerv.platform.cli', 'serve', '--port', str(port), '--no-browser']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home() / '.nerv/profiles/preview')
    parser.add_argument('--port', type=int, default=8100)
    args = parser.parse_args()
    try:
        root, command = preview_command(args.home, args.port)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'Cannot start preview: {error}\n')
    os.environ['NERV_HOME'] = str(root)
    os.chdir(root)
    os.execv(command[0], command)


if __name__ == '__main__':
    main()
