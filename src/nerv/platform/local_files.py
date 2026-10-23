"""Operator folder navigation. Returns directory names, never file contents."""
from pathlib import Path
import os
import string

PAGE_SIZE = 100  # Bound responses for large checkpoint and dataset directories.


def directories(path=None, offset=0):
    current = Path(path or Path.home()).expanduser().resolve(strict=True)
    if not current.is_dir():
        raise ValueError("Choose a directory")
    if offset < 0:
        raise ValueError("Invalid directory page")
    roots = [Path.home(), Path(current.anchor)]
    if os.name == "nt":
        roots += [Path(f"{letter}:/") for letter in string.ascii_uppercase if Path(f"{letter}:/").is_dir()]
    children = []
    with os.scandir(current) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    children.append({"name": entry.name, "path": str(Path(entry.path).resolve())})
            except OSError:
                continue
    children.sort(key=lambda item: item["name"].casefold())
    return {"path": str(current), "parent": str(current.parent),
            "roots": list(dict.fromkeys(str(p) for p in roots)),
            "directories": children[offset:offset + PAGE_SIZE],
            "next_offset": offset + PAGE_SIZE if offset + PAGE_SIZE < len(children) else None}
