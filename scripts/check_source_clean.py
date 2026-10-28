"""Fail when build dependencies or caches have been written into a source checkout."""
from pathlib import Path
import os


def violations(root):
    found = []
    for directory, names, files in os.walk(root):
        if 'pyvenv.cfg' in files:
            found.append(str(Path(directory).relative_to(root)))
            names[:] = []
            continue
        names[:] = [name for name in names if name not in ('.git', 'docs')]
        for name in list(names):
            path = Path(directory) / name
            # worlds/build contains indexed generated scene sources, not Python output.
            relative = path.relative_to(root)
            if (name in ('node_modules', '__pycache__', '.pytest_cache', '.next', '.cache')
                    or name == '.venv' or name.startswith('.venv-') or name.endswith('.egg-info')
                    or relative.as_posix() in ('build', 'dist', 'frontend/out')):
                found.append(str(relative))
                names.remove(name)
    return found


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    found = violations(root)
    if found:
        preview = ', '.join(found[:40])
        suffix = f' (and {len(found) - 40} more)' if len(found) > 40 else ''
        raise SystemExit('Source checkout contains generated dependencies: ' + preview + suffix)
    print('Source checkout contains no generated dependency directories.')
