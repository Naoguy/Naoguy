#!/usr/bin/env python3
"""Package the addon as an installable Blender extension zip.

    python3 scripts/build_extension.py [--out dist]

Produces ``dist/pcb_generator-<version>.zip``, installable through
Preferences > Add-ons > Install from Disk.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "pcb_generator"
MANIFEST = PACKAGE / "blender_manifest.toml"

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".git"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def read_version() -> str:
    text = MANIFEST.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("No version found in blender_manifest.toml")
    return match.group(1)


def collect() -> list[pathlib.Path]:
    files = []
    for path in sorted(PACKAGE.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        files.append(path)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist", help="Output directory")
    args = parser.parse_args()

    if not MANIFEST.exists():
        raise SystemExit(f"Missing {MANIFEST}")

    version = read_version()
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"pcb_generator-{version}.zip"

    files = collect()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            # Blender expects the manifest at the root of the package folder
            # inside the zip.
            zf.write(path, pathlib.Path("pcb_generator") / path.relative_to(PACKAGE))

    size_kb = archive.stat().st_size / 1024
    print(f"{archive.relative_to(ROOT)}  ({len(files)} files, {size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
