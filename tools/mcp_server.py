#!/usr/bin/env python3
"""
TimeSpace MCP Server — event discovery and content-creation tools.

Usage (stdio transport):
  python tools/mcp_server.py

Configuration (environment variables or .env in the repo root):
  TIMESPACE_REPO      Path to this repo (default: auto-detected from __file__)
  WORLD66_CONTENT_DIR Path to the world66 content directory

To register with Claude Code:
  claude mcp add timespace -- python /path/to/timespace/tools/mcp_server.py
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Repo paths — loaded once at startup
# ---------------------------------------------------------------------------

def _find_repo() -> Path:
    env = os.environ.get("TIMESPACE_REPO", "")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


REPO_PATH = _find_repo()

_dotenv = REPO_PATH / ".env"
if _dotenv.exists():
    for _line in _dotenv.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

EVENTS_DIR  = Path(os.environ.get("TIMESPACE_EVENTS_DIR", str(REPO_PATH / "events")))
POIS_DIR    = Path(os.environ.get("TIMESPACE_POIS_DIR",   str(REPO_PATH / "pois")))
SOURCES_DIR = REPO_PATH / "sources"
W66_DIR     = Path(os.environ.get("WORLD66_CONTENT_DIR",
                   str(REPO_PATH.parent / "world66" / "content")))
W66_POIS_INDEX = REPO_PATH / "w66_pois.json"

# Lazy-loaded POI index: populated on first call to tool_find_poi
_w66_pois_cache: list[dict] | None = None


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_events",
        "description": (
            "Search for upcoming events in TimeSpace. Returns a list of events "
            "and a coverage assessment (how well the city is monitored). "
            "If coverage looks thin, the response will tell you — use add_source "
            "or add_event to improve it.\n\n"
            "Provide either a city name OR lat+lng (for radius search). "
            "If both are omitted, returns events across all cities."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name (e.g. 'Rotterdam', 'Amsterdam'). Matched against venue paths.",
                },
                "lat": {"type": "number", "description": "Latitude for radius search."},
                "lng": {"type": "number", "description": "Longitude for radius search."},
                "radius_km": {
                    "type": "number",
                    "description": "Search radius in km (default 25). Only used with lat/lng.",
                },
                "days": {
                    "type": "integer",
                    "description": "Days ahead to look (default 30, max 90).",
                },
                "category": {
                    "type": "string",
                    "description": "Filter by category: concert|festival|market|museum|exhibition|food|sport|other",
                },
            },
        },
    },
    {
        "name": "list_venues",
        "description": (
            "List known venues (sources) for a city, with their monitoring status. "
            "Shows which sources are overdue, never checked, or up to date. "
            "Use this before add_source to check if a venue is already tracked."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name to filter (e.g. 'Rotterdam'). Omit to list all.",
                },
            },
        },
    },
    {
        "name": "add_event",
        "description": (
            "Write a new event file into the TimeSpace repo. "
            "Use this when you know a specific event is happening and it's missing from the data. "
            "The poi field must be an existing venue path (shadow POI or W66). "
            "Call add_poi first if the venue doesn't exist yet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title":       {"type": "string"},
                "poi":         {"type": "string", "description": "Venue path, e.g. 'spacetime/europe/netherlands/rotterdam/bird' or 'europe/netherlands/amsterdam/paradiso'"},
                "date":        {"type": "string", "description": "ISO date: YYYY-MM-DD"},
                "time_start":  {"type": "string", "description": "24h time: HH:MM"},
                "time_end":    {"type": "string", "description": "24h time: HH:MM"},
                "category":    {"type": "string", "description": "concert|festival|market|museum|exhibition|food|sport|other"},
                "url":         {"type": "string"},
                "description": {"type": "string", "description": "1–3 sentence description of the event."},
            },
            "required": ["title", "poi", "date"],
        },
    },
    {
        "name": "add_poi",
        "description": (
            "Add a shadow POI for a venue that isn't in World66 yet. "
            "Call find_poi first — if the venue already exists in W66 or as a shadow POI, "
            "use that path instead. Only call this when find_poi returns nothing useful. "
            "Call geocode to get accurate lat/lng — never guess coordinates. "
            "The poi path in future events will be 'spacetime/<city_path>/<slug>'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title":     {"type": "string", "description": "Venue name"},
                "city_path": {"type": "string", "description": "City path, e.g. 'europe/netherlands/breda'"},
                "slug":      {"type": "string", "description": "URL-safe slug, e.g. 'chassetheater'"},
                "latitude":  {"type": "number", "description": "REQUIRED. Use geocode tool — never estimate."},
                "longitude": {"type": "number", "description": "REQUIRED. Use geocode tool — never estimate."},
                "snippet":   {"type": "string", "description": "One sentence describing the venue."},
                "tags":      {
                    "type": "array", "items": {"type": "string"},
                    "description": "Venue type tags, e.g. ['theatre', 'concert']",
                },
            },
            "required": ["title", "city_path", "slug", "latitude", "longitude"],
        },
    },
    {
        "name": "add_source",
        "description": (
            "Register a venue as a source to monitor for future events. "
            "This tells the todo system to check this URL periodically for new events. "
            "The poi must already exist (as a shadow POI or W66 path). "
            "Call add_poi first if the venue is new."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title":        {"type": "string", "description": "Venue name"},
                "city_path":    {"type": "string", "description": "City path, e.g. 'europe/netherlands/breda'"},
                "slug":         {"type": "string", "description": "Slug matching the venue, e.g. 'chassetheater'"},
                "url":          {"type": "string", "description": "The venue's events/agenda page URL"},
                "poi":          {"type": "string", "description": "Venue path, e.g. 'spacetime/europe/netherlands/breda/chassetheater'"},
                "interval_days": {"type": "integer", "description": "How often to check in days (default 7)"},
                "notes":        {"type": "string", "description": "What to look for on the events page — categories, date range, what to skip."},
            },
            "required": ["title", "city_path", "slug", "url", "poi"],
        },
    },
    {
        "name": "find_poi",
        "description": (
            "Search for a venue in the W66 POI index and in existing shadow POIs. "
            "Call this BEFORE add_poi to check whether the venue already exists. "
            "If a W66 match is found, use that path directly in events and sources — "
            "no shadow POI needed. If only a shadow POI is found, use that. "
            "Only call add_poi if find_poi returns nothing useful.\n\n"
            "Also useful when you know a venue name but not its exact W66 path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Venue name to search for, e.g. 'Paradiso' or 'Tivoli Vredenburg'",
                },
                "city": {
                    "type": "string",
                    "description": "Optional city name to narrow results, e.g. 'Amsterdam'",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "geocode",
        "description": (
            "Look up the latitude and longitude of a place by name. "
            "Call this before add_poi — never estimate coordinates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Place name, e.g. 'Chassé Theater, Breda'"},
            },
            "required": ["query"],
        },
    },
]


# ---------------------------------------------------------------------------
# Frontmatter parser (stdlib only)
# ---------------------------------------------------------------------------

def _parse_fm(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter. Returns (meta, body). Handles strings, dates, lists, null."""
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
        # list item (indented or bare "- ")
        stripped = line.strip()
        if stripped.startswith("- ") and current_key is not None and isinstance(meta.get(current_key), list):
            meta[current_key].append(stripped[2:].strip().strip('"\''))
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip().strip('"\'')
        current_key = k
        if v == "" or v == "~":
            meta[k] = []          # potential list
        elif v == "null":
            meta[k] = None
        elif v.lower() in ("true", "false"):
            meta[k] = v.lower() == "true"
        else:
            meta[k] = v
    return meta, body


def _str(val) -> str:
    return "" if val is None else str(val)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text.lower())
    ascii_text = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


def _city_from_poi(poi: str) -> str:
    path = poi.removeprefix("spacetime/")
    parts = path.split("/")
    return parts[2] if len(parts) >= 3 else ""


def _load_venue(poi_path: str) -> dict | None:
    if poi_path.startswith("spacetime/"):
        slug = poi_path.removeprefix("spacetime/")
        md = POIS_DIR / f"{slug}.md"
        if not md.exists():
            return None
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        meta, _ = _parse_fm(text)
        w66 = meta.get("w66_path")
        if w66 and w66 != "null":
            return _load_venue(w66)
        lat = meta.get("latitude") or meta.get("lat")
        lng = meta.get("longitude") or meta.get("lng")
        if lat is None or lng is None:
            return None
        return {
            "path": poi_path,
            "title": _str(meta.get("title", slug.split("/")[-1])),
            "lat": float(lat), "lng": float(lng),
            "snippet": _str(meta.get("snippet", "")),
            "tags": meta.get("tags") if isinstance(meta.get("tags"), list) else [],
        }
    else:
        if not W66_DIR.exists():
            return None
        md = W66_DIR / f"{poi_path}.md"
        if not md.exists():
            md = W66_DIR / poi_path / "index.md"
        if not md.exists():
            return None
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        meta, _ = _parse_fm(text)
        lat = meta.get("latitude") or meta.get("lat")
        lng = meta.get("longitude") or meta.get("lng")
        if lat is None or lng is None:
            return None
        return {
            "path": poi_path,
            "title": _str(meta.get("title", poi_path.split("/")[-1])),
            "lat": float(lat), "lng": float(lng),
            "snippet": _str(meta.get("snippet", "")),
            "tags": meta.get("tags") if isinstance(meta.get("tags"), list) else [],
        }


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(1.0, a)))


def _load_events(days: int = 30) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=min(days, 90))
    events = []
    for path in sorted(EVENTS_DIR.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        meta, body = _parse_fm(text)
        poi = _str(meta.get("poi", ""))
        raw_date = meta.get("date", "")
        if not poi or not raw_date:
            continue
        try:
            ev_date = date.fromisoformat(str(raw_date).strip())
        except (ValueError, TypeError):
            continue
        if ev_date < today or ev_date > cutoff:
            continue
        events.append({
            "slug": path.stem,
            "title": _str(meta.get("title", path.stem)),
            "poi": poi,
            "date": ev_date,
            "time_start": _str(meta.get("time_start", "")),
            "category": _str(meta.get("category", "other")),
            "url": _str(meta.get("url", "")),
            "description": body,
        })
    events.sort(key=lambda e: e["date"])
    return events


def _load_sources(city_slug: str = "") -> list[dict]:
    today = date.today()
    sources = []
    for path in sorted(SOURCES_DIR.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        meta, body = _parse_fm(text)
        slug = path.stem
        poi = _str(meta.get("poi", ""))
        src_city = _city_from_poi(poi)

        if city_slug:
            cs = _slugify(city_slug)
            rel = str(path.relative_to(SOURCES_DIR)).replace("\\", "/")
            if cs not in (src_city.lower(), slug.lower()) and cs not in rel.lower():
                continue

        interval = int(_str(meta.get("interval_days", "7")) or "7")
        lc_raw = meta.get("last_checked")
        lc = None
        overdue_days = 0
        if lc_raw and lc_raw != "null":
            try:
                lc = date.fromisoformat(str(lc_raw).strip())
                due = lc + timedelta(days=interval)
                overdue_days = max(0, (today - due).days)
            except (ValueError, TypeError):
                pass
        sources.append({
            "slug": slug,
            "title": _str(meta.get("title", slug)),
            "poi": poi,
            "url": _str(meta.get("url", "")),
            "interval_days": interval,
            "last_checked": str(lc) if lc else "never",
            "overdue_days": overdue_days,
            "city_slug": src_city,
            "notes": body,
        })
    return sources


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_search_events(city: str = "", lat=None, lng=None, radius_km: float = 25,
                       days: int = 30, category: str = "") -> str:
    all_events = _load_events(days)

    # Filter by category
    if category:
        all_events = [e for e in all_events if e["category"].lower() == category.lower()]

    # Filter by location
    city_slug = _slugify(city) if city else ""

    def _matches(ev: dict) -> tuple[bool, float | None]:
        venue = _load_venue(ev["poi"])
        if venue is None:
            return False, None
        if lat is not None and lng is not None:
            dist = _haversine_km(lat, lng, venue["lat"], venue["lng"])
            return dist <= radius_km, dist
        if city_slug:
            vc = _city_from_poi(ev["poi"])
            return city_slug in vc.lower() or vc.lower() in city_slug, None
        return True, None

    matched: list[tuple[dict, dict, float | None]] = []
    for ev in all_events:
        ok, dist = _matches(ev)
        if ok:
            matched.append((ev, _load_venue(ev["poi"]), dist))

    # Header
    if city:
        header = f"## Events in {city.title()} — next {days} days"
    elif lat is not None:
        header = f"## Events within {radius_km} km — next {days} days"
    else:
        header = f"## All upcoming events — next {days} days"

    if not matched:
        lines = [header, "", "No events found."]
    else:
        lines = [header, ""]
        for ev, venue, dist in matched:
            time = f" {ev['time_start']}" if ev["time_start"] else ""
            venue_name = venue["title"] if venue else ev["poi"].split("/")[-1]
            venue_city = _city_from_poi(ev["poi"]).replace("-", " ").title()
            dist_str = f"  ({dist:.1f} km)" if dist is not None else ""
            url_str = f"  → {ev['url']}" if ev["url"] else ""
            lines.append(
                f"- **{ev['date']}{time}** — {ev['title']}  "
                f"_{venue_name}, {venue_city}_ [{ev['category']}]{dist_str}{url_str}"
            )
            if ev["description"]:
                lines.append(f"  {ev['description'][:120]}{'…' if len(ev['description']) > 120 else ''}")

    # Coverage assessment
    lines += ["", "---", "## Coverage assessment"]
    affected_cities = {_city_from_poi(ev["poi"]) for ev, _, _ in matched}
    if not affected_cities and city_slug:
        affected_cities = {city_slug}

    if affected_cities:
        sources = []
        for cs in affected_cities:
            sources.extend(_load_sources(cs))
    else:
        sources = _load_sources()

    if not sources:
        lines.append(
            f"No venue sources registered yet for this area. "
            f"Call `add_source` to start monitoring venues — and `add_poi` first if the venue isn't in World66."
        )
    else:
        overdue = [s for s in sources if s["overdue_days"] > 0]
        never   = [s for s in sources if s["last_checked"] == "never"]
        fresh   = [s for s in sources if s["last_checked"] != "never" and s["overdue_days"] == 0]

        lines.append(f"{len(sources)} venue source(s) monitored.")
        if fresh:
            lines.append(f"- Up to date: {', '.join(s['slug'] for s in fresh)}")
        if overdue:
            lines.append(
                f"- **Overdue** ({len(overdue)}): " +
                ", ".join(f"{s['slug']} ({s['overdue_days']}d)" for s in overdue)
            )
        if never:
            lines.append(
                f"- Never checked ({len(never)}): " +
                ", ".join(s["slug"] for s in never)
            )

        n_events = len(matched)
        if n_events < 3 or overdue or never:
            lines.append(
                "\n**Coverage looks thin.** "
                + ("Check overdue sources using the todo workflow (`python todo/list_due.py`). " if overdue else "")
                + ("Check never-checked sources. " if never else "")
                + (f"Only {n_events} event(s) found — consider adding events directly with `add_event`. " if n_events < 3 else "")
            )
        else:
            lines.append(f"\n{n_events} event(s) found — coverage looks good.")

    return "\n".join(lines)


def tool_list_venues(city: str = "") -> str:
    sources = _load_sources(city)
    if not sources:
        label = f" for {city.title()}" if city else ""
        return (
            f"No venue sources found{label}. "
            f"Use `add_poi` to register a venue and `add_source` to start monitoring it."
        )

    today = date.today()
    lines = [f"## Venues{' in ' + city.title() if city else ''} ({len(sources)} source(s))", ""]

    overdue  = [s for s in sources if s["overdue_days"] > 0]
    never    = [s for s in sources if s["last_checked"] == "never"]
    fresh    = [s for s in sources if s["last_checked"] != "never" and s["overdue_days"] == 0]

    def _fmt(s):
        status = (f"⚠ overdue {s['overdue_days']}d" if s["overdue_days"] > 0
                  else ("✗ never checked" if s["last_checked"] == "never"
                        else f"✓ checked {s['last_checked']}"))
        return f"- **{s['title']}** (`{s['poi']}`), every {s['interval_days']}d — {status}"

    if overdue:
        lines += ["### Overdue"] + [_fmt(s) for s in overdue] + [""]
    if never:
        lines += ["### Never checked"] + [_fmt(s) for s in never] + [""]
    if fresh:
        lines += ["### Up to date"] + [_fmt(s) for s in fresh] + [""]

    return "\n".join(lines)


def tool_add_event(title: str, poi: str, date_str: str, time_start: str = "",
                   time_end: str = "", category: str = "other", url: str = "",
                   description: str = "") -> str:
    try:
        ev_date = date.fromisoformat(date_str.strip())
    except ValueError:
        return f"Invalid date '{date_str}'. Use ISO format YYYY-MM-DD."

    today = date.today()
    if ev_date < today:
        return "Date is in the past — not adding past events."
    if ev_date > today + timedelta(days=90):
        return "Date is more than 90 days out — TimeSpace only tracks near-term events."

    # Verify venue exists
    venue = _load_venue(poi)
    if venue is None:
        return (
            f"Venue not found: '{poi}'. "
            f"If it's a shadow POI, call add_poi first. "
            f"If it's a W66 path, check the spelling."
        )

    # Determine event file path
    city_path = "/".join(poi.removeprefix("spacetime/").split("/")[:3])
    event_dir = EVENTS_DIR / city_path
    slug = f"{date_str}-{_slugify(title)}"
    out_path = event_dir / f"{slug}.md"

    if out_path.exists():
        return f"Event file already exists: `{out_path.relative_to(REPO_PATH)}`"

    event_dir.mkdir(parents=True, exist_ok=True)

    # Build frontmatter
    fm_lines = [
        "---",
        f'title: "{title}"',
        f"poi: {poi}",
        f"date: {date_str}",
    ]
    if time_start:
        fm_lines.append(f'time_start: "{time_start}"')
    if time_end:
        fm_lines.append(f'time_end: "{time_end}"')
    if category and category != "other":
        fm_lines.append(f"category: {category}")
    if url:
        fm_lines.append(f"url: {url}")
    fm_lines.append("---")
    body = f"\n{description}" if description else ""
    content = "\n".join(fm_lines) + body + "\n"

    out_path.write_text(content, encoding="utf-8")
    rel = str(out_path.relative_to(REPO_PATH))
    return f"Created `{rel}` — event '{title}' on {date_str} at {venue['title']}."


def tool_add_poi(title: str, city_path: str, slug: str, latitude: float,
                 longitude: float, snippet: str = "", tags: list | None = None) -> str:
    if tags is None:
        tags = []
    out_path = POIS_DIR / city_path / f"{slug}.md"
    if out_path.exists():
        return f"Shadow POI already exists: `{out_path.relative_to(REPO_PATH)}`"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    tag_lines = "\n".join(f"- {t}" for t in tags)
    tag_block = f"tags:\n{tag_lines}\n" if tags else "tags: []\n"

    content = (
        f"---\n"
        f'title: "{title}"\n'
        f"latitude: {latitude}\n"
        f"longitude: {longitude}\n"
        f'snippet: "{snippet}"\n'
        f"{tag_block}"
        f"location: {city_path}\n"
        f"w66_path: null\n"
        f"added: {today}\n"
        f"---\n"
    )
    out_path.write_text(content, encoding="utf-8")
    poi_ref = f"spacetime/{city_path}/{slug}"
    rel = str(out_path.relative_to(REPO_PATH))
    return (
        f"Created shadow POI `{rel}`.\n"
        f"Use `{poi_ref}` as the `poi` field in events and sources."
    )


def tool_add_source(title: str, city_path: str, slug: str, url: str, poi: str,
                    interval_days: int = 7, notes: str = "") -> str:
    out_path = SOURCES_DIR / city_path / f"{slug}.md"
    if out_path.exists():
        return f"Source already exists: `{out_path.relative_to(REPO_PATH)}`"

    # Warn if POI doesn't resolve
    venue = _load_venue(poi)
    if venue is None:
        return (
            f"Venue not found: '{poi}'. "
            f"Call add_poi first if it's a new venue, or check the poi path spelling."
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"---\n"
        f'title: {title}\n'
        f"poi: {poi}\n"
        f"url: {url}\n"
        f"interval_days: {interval_days}\n"
        f"last_checked: null\n"
        f"---\n"
    )
    if notes:
        content += f"{notes}\n"

    out_path.write_text(content, encoding="utf-8")
    rel = str(out_path.relative_to(REPO_PATH))
    return (
        f"Created source `{rel}`. "
        f"Run `python todo/list_due.py` to see it appear in the check queue."
    )


def _load_w66_index() -> list[dict]:
    global _w66_pois_cache
    if _w66_pois_cache is not None:
        return _w66_pois_cache
    if not W66_POIS_INDEX.exists():
        _w66_pois_cache = []
        return _w66_pois_cache
    try:
        entries = json.loads(W66_POIS_INDEX.read_text(encoding="utf-8"))
    except Exception:
        _w66_pois_cache = []
        return _w66_pois_cache
    # Pre-compute normalised word sets once so searches are set-ops only (~18 ms vs ~108 ms)
    _norm = re.compile(r"[^\w\s]")
    for e in entries:
        e["_words"] = frozenset(_norm.sub(" ", e["title"].lower()).split())
    _w66_pois_cache = entries
    return _w66_pois_cache


def _score_match(query_words: frozenset, entry_words: frozenset) -> float:
    intersection = query_words & entry_words
    if not intersection:
        return 0.0
    score = len(intersection) / len(query_words | entry_words)
    if intersection == query_words:
        score += 0.3
    if query_words == entry_words:
        score += 0.2
    return score


def tool_find_poi(name: str, city: str = "") -> str:
    _norm = re.compile(r"[^\w\s]")
    query_words = frozenset(_norm.sub(" ", name.lower()).split())
    city_slug = _slugify(city) if city else ""

    # Search W66 index (word sets are pre-computed at load time)
    w66_results: list[tuple[float, dict]] = []
    for entry in _load_w66_index():
        if city_slug and city_slug not in entry["path"].lower():
            continue
        score = _score_match(query_words, entry["_words"])
        if score > 0.2:
            w66_results.append((score, entry))
    w66_results.sort(key=lambda x: -x[0])

    # Search shadow POIs in pois/
    shadow_results: list[tuple[float, dict]] = []
    for md in sorted(POIS_DIR.rglob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        meta, _ = _parse_fm(text)
        title = _str(meta.get("title", md.stem))
        if city_slug:
            rel = str(md.relative_to(POIS_DIR)).replace("\\", "/")
            if city_slug not in rel.lower():
                continue
        title_words = frozenset(_norm.sub(" ", title.lower()).split())
        score = _score_match(query_words, title_words)
        if score > 0.2:
            slug = str(md.relative_to(POIS_DIR).with_suffix("")).replace("\\", "/")
            shadow_results.append((score, {
                "path": f"spacetime/{slug}",
                "title": title,
                "lat": float(meta.get("latitude") or meta.get("lat") or 0),
                "lng": float(meta.get("longitude") or meta.get("lng") or 0),
                "snippet": _str(meta.get("snippet", "")),
                "w66_path": meta.get("w66_path"),
            }))
    shadow_results.sort(key=lambda x: -x[0])

    if not w66_results and not shadow_results:
        lines = [f"No matches found for '{name}'" + (f" in {city.title()}" if city else "") + "."]
        lines.append("The venue is not in W66 or shadow POIs — call add_poi to register it.")
        return "\n".join(lines)

    lines = [f"## POI search results for '{name}'" + (f" in {city.title()}" if city else ""), ""]

    if w66_results:
        lines.append("### In World66 (use these paths directly in events/sources)")
        for score, e in w66_results[:6]:
            city_part = "/".join(e["path"].split("/")[:-1])
            snippet = f"  — {e['snippet'][:80]}" if e.get("snippet") else ""
            lines.append(f"- **{e['title']}** `{e['path']}`  _{city_part}_{snippet}")
        lines.append("")

    if shadow_results:
        lines.append("### Shadow POIs (already in this repo)")
        for score, e in shadow_results[:4]:
            w66 = e.get("w66_path")
            note = f" → links to W66 `{w66}`" if w66 and w66 != "null" else " (no W66 link yet)"
            lines.append(f"- **{e['title']}** `{e['path']}`{note}")
        lines.append("")

    lines.append(
        "Use a W66 path directly as `poi` in events and sources if one matches. "
        "Only call add_poi if nothing here is the right venue."
    )
    return "\n".join(lines)


def tool_geocode(query: str) -> str:
    try:
        url = (f"https://nominatim.openstreetmap.org/search"
               f"?q={urllib.parse.quote(query)}&format=json&limit=1")
        req = urllib.request.Request(url, headers={"User-Agent": "timespace-mcp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.load(r)
    except Exception as e:
        return f"Geocoding failed: {e}"
    if not result:
        return f"No results found for '{query}'."
    r = result[0]
    return f"latitude: {r['lat']}, longitude: {r['lon']}, display_name: {r['display_name']}"


# ---------------------------------------------------------------------------
# MCP JSON-RPC dispatch
# ---------------------------------------------------------------------------

def _handle(message: dict) -> dict | None:
    method = message.get("method", "")
    msg_id = message.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, msg):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return ok({
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "timespace", "version": "1.0.0"},
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "tools/list":
        return ok({"tools": TOOLS})

    if method == "resources/list":
        return ok({"resources": []})

    if method == "prompts/list":
        return ok({"prompts": []})

    if method == "tools/call":
        params = message.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        try:
            if name == "search_events":
                text = tool_search_events(
                    city=args.get("city", ""),
                    lat=args.get("lat"), lng=args.get("lng"),
                    radius_km=float(args.get("radius_km", 25)),
                    days=int(args.get("days", 30)),
                    category=args.get("category", ""),
                )
            elif name == "list_venues":
                text = tool_list_venues(city=args.get("city", ""))
            elif name == "add_event":
                text = tool_add_event(
                    title=args["title"], poi=args["poi"], date_str=args["date"],
                    time_start=args.get("time_start", ""), time_end=args.get("time_end", ""),
                    category=args.get("category", "other"), url=args.get("url", ""),
                    description=args.get("description", ""),
                )
            elif name == "add_poi":
                text = tool_add_poi(
                    title=args["title"], city_path=args["city_path"], slug=args["slug"],
                    latitude=float(args["latitude"]), longitude=float(args["longitude"]),
                    snippet=args.get("snippet", ""), tags=args.get("tags", []),
                )
            elif name == "add_source":
                text = tool_add_source(
                    title=args["title"], city_path=args["city_path"], slug=args["slug"],
                    url=args["url"], poi=args["poi"],
                    interval_days=int(args.get("interval_days", 7)),
                    notes=args.get("notes", ""),
                )
            elif name == "find_poi":
                text = tool_find_poi(name=args["name"], city=args.get("city", ""))
            elif name == "geocode":
                text = tool_geocode(query=args["query"])
            else:
                return err(-32601, f"Unknown tool: {name}")
        except KeyError as e:
            return err(-32602, f"Missing argument: {e}")
        except Exception as e:
            return err(-32603, f"Tool error: {e}")
        return ok({"content": [{"type": "text", "text": text}]})

    if method == "ping":
        return ok({})

    return err(-32601, f"Method not found: {method}")


def main():
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }) + "\n")
            sys.stdout.flush()
            continue
        response = _handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
