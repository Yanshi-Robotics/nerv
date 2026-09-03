"""README checks, machine-run before every commit that touches the READMEs.

1. Every relative link and image in each README resolves to a path tracked by git
   (the index, not the working tree — what GitHub shows is what is committed).
2. Each translation has the same number of `##` sections as the English original.
3. Style budget: bold runs and table rows stay within the house limits.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READMES = ["README.md", "docs/i18n/zh/README.md"]
MAX_TABLE_ROWS = 40
MAX_BOLD = 60
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)|<img src=\"([^\"]+)\"|<a href=\"([^\"]+)\"")


def tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files", "--cached"], cwd=ROOT, capture_output=True, text=True).stdout
    files = set(out.split())
    dirs = set()
    for f in files:
        d = os.path.dirname(f)
        while d:
            dirs.add(d)
            d = os.path.dirname(d)
    return files | dirs


def main() -> int:
    ok = True
    idx = tracked()
    h2 = {}
    for rel in READMES:
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        h2[rel] = len(re.findall(r"^## ", text, flags=re.M))
        for m in LINK.finditer(text):
            target = next(g for g in m.groups() if g)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            norm = os.path.normpath(os.path.join(os.path.dirname(rel), target))
            if norm not in idx:
                print(f"{rel}: not in git index: {target} -> {norm}")
                ok = False
        rows = sum(1 for ln in text.splitlines() if ln.startswith("|") and not set(ln) <= set("|- "))
        bold = len(re.findall(r"\*\*[^*]+\*\*", text))
        if rows > MAX_TABLE_ROWS:
            print(f"{rel}: {rows} table rows (> {MAX_TABLE_ROWS})")
            ok = False
        if bold > MAX_BOLD:
            print(f"{rel}: {bold} bold runs (> {MAX_BOLD})")
            ok = False
    if len(set(h2.values())) > 1:
        print(f"## counts differ across languages: {h2}")
        ok = False
    print("README check:", "ok" if ok else "FAIL", h2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
