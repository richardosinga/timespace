#!/usr/bin/env python3
"""
Scan the W66 content tree and write w66_pois.json.

Run this whenever W66 content changes significantly. The output is committed
to this repo so the MCP server can use it without a local W66 checkout.

Usage:
    python tools/export_w66_pois.py
    python tools/export_w66_pois.py --w66-dir /path/to/world66/content

Configuration:
    WORLD66_CONTENT_DIR  env var (overridden by --w66-dir argument)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_PATH = Path(__file__).resolve().parent.parent
OUT_FILE = REPO_PATH / "w66_pois.json"

_dotenv = REPO_PATH / ".env"
if _dotenv.exists():
    for _line in _dotenv.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


def _parse_fm(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1:]).strip()
    meta: dict = {}
    current_key: str | None = None
    for line in fm_lines:
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_key and isinstance(meta.get(current_key), list):
            meta[current_key].append(stripped[2:].strip().strip('"\''))
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip().strip('"\'')
        current_key = k
        if v == "" or v == "~":
            meta[k] = []
        elif v == "null":
            meta[k] = None
        else:
            meta[k] = v
    return meta, body


def export(w66_content_dir: Path) -> None:
    if not w66_content_dir.exists():
        print(f"Error: W66 content directory not found: {w66_content_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {w66_content_dir} …")
    pois = []
    scanned = skipped = 0

    for md in sorted(w66_content_dir.rglob("*.md")):
        scanned += 1
        if scanned % 5000 == 0:
            print(f"  {scanned} files scanned, {len(pois)} POIs found …")

        try:
            # Fast pre-check on first 500 bytes before full parse
            head = md.read_bytes()[:500].decode("utf-8", errors="ignore")
        except OSError:
            skipped += 1
            continue

        if "type: poi" not in head or "latitude:" not in head:
            continue

        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            skipped += 1
            continue

        meta, _ = _parse_fm(text)
        if meta.get("type") != "poi":
            continue
        lat = meta.get("latitude") or meta.get("lat")
        lng = meta.get("longitude") or meta.get("lng")
        if lat is None or lng is None:
            continue

        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (ValueError, TypeError):
            continue

        path = str(md.relative_to(w66_content_dir).with_suffix(""))
        title = str(meta.get("title") or path.split("/")[-1]).strip().strip('"\'')
        snippet = str(meta.get("snippet") or "").strip().strip('"\'')

        entry: dict = {"path": path, "title": title, "lat": lat_f, "lng": lng_f}
        if snippet:
            entry["snippet"] = snippet
        pois.append(entry)

    print(f"Done. {scanned} files scanned, {len(pois)} geocoded POIs found.")
    print(f"Writing {OUT_FILE} …")
    OUT_FILE.write_text(json.dumps(pois, ensure_ascii=False, indent=None) + "\n", encoding="utf-8")

    size_kb = OUT_FILE.stat().st_size / 1024
    print(f"Wrote {len(pois)} entries ({size_kb:.0f} KB).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--w66-dir", help="Path to world66/content directory")
    args = parser.parse_args()

    if args.w66_dir:
        w66_dir = Path(args.w66_dir)
    else:
        default = Path(os.environ.get("WORLD66_CONTENT_DIR",
                       str(REPO_PATH.parent / "world66" / "content")))
        w66_dir = default

    export(w66_dir)


if __name__ == "__main__":
    main()
