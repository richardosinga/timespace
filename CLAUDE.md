# TimeSpace — Agent & Developer Guide

## What is this project?

TimeSpace is a Django app that maps cultural events onto their venues across Dutch cities. Events are stored as markdown files in the filesystem; venues are either shadow POIs or World66 content paths. The site shows an interactive map with an event list, filterable by date and category.

## Project structure

```
events/      # One markdown file per event, organised by city path
pois/        # Shadow venue POIs (used when a venue isn't yet in World66)
sources/     # One file per venue to monitor for new events
todo/        # Task definitions for source-checking and maintenance work
timespace/   # Django app — models.py, views.py, templates
project/     # Django settings and URL config
```

## Running the site

```bash
source venv/bin/activate
python manage.py runserver
```

Map at `http://localhost:8000/spacetime/`, event list at `http://localhost:8000/spacetime/events/`.

## Events

Each event lives at `events/<continent>/<country>/<city>/YYYY-MM-DD-slug.md`.

Required frontmatter:

| Field        | Description |
|---|---|
| `title`      | Event name |
| `poi`        | Venue path — either a shadow POI (`spacetime/europe/netherlands/amersfoort/de-flint`) or a W66 path (`europe/netherlands/amsterdam/paradiso`) |
| `date`       | ISO date: `2026-06-15` |

Optional frontmatter: `time_start`, `time_end` (24h), `category` (`concert` · `festival` · `market` · `museum` · `exhibition` · `food` · `sport` · `other`), `url`.

Body: 1–3 sentences describing the event.

## Shadow POIs

When a venue isn't in World66 yet, it gets a shadow POI in `pois/<path>.md`. Frontmatter: `title`, `latitude`, `longitude`, `snippet`, `tags`, `location`, `w66_path` (null until linked).

Once a venue gets a World66 page, run `todo/check_w66.py` and follow the delist process (`todo/DELIST.md`): set `w66_path`, migrate event and source files, delete the shadow. The `load_venue()` function transparently redirects as soon as `w66_path` is set.

## Sources

Each file in `sources/<city>/` describes one venue to monitor:

| Field          | Description |
|---|---|
| `url`          | Venue's events page |
| `poi`          | Venue path |
| `interval_days`| How often to check |
| `last_checked` | ISO date of last check (null = never) |

Body: notes on what to look for and which categories to use.

## The todo system

### Checking sources — most common task

```bash
python todo/list_due.py          # list overdue sources
```

Pick one (or a small city batch), fetch the events page, write event files for the next 60 days, update `last_checked`, push a PR.

Full instructions: `todo/TASK.md`. Branch naming: `spacetime-source-<slug>`.

### Delisting shadow POIs

When `todo/check_w66.py` finds a match: `todo/DELIST.md`. Branch naming: `spacetime-delist-<slug>`.

## W66 POI index

`w66_pois.json` in the repo root is a pre-built index of all geocoded W66 POIs (path, title, lat, lng, snippet). The MCP server uses it for `find_poi` without needing a local W66 checkout.

Regenerate after significant W66 content changes:

```bash
python3 tools/export_w66_pois.py                        # uses sibling world66 repo by default
python3 tools/export_w66_pois.py --w66-dir /path/to/world66/content
```

Commit the updated `w66_pois.json` so the server version stays current.

## Tabbi integration

The **tabbi MCP** (connected globally via claude.ai) lets Claude create and populate trip plans using World66 content. TimeSpace and tabbi complement each other:

- TimeSpace knows *what's on* at a venue (events with dates).
- Tabbi knows *what a venue is* (descriptions, categories, map position).

When helping a user plan a trip to a Dutch city using tabbi tools:
- After calling `research_city`, cross-reference upcoming TimeSpace events — a venue with something notable happening during the trip dates is a strong reason to include it even if it's a smaller World66 entry.
- Mention specific events in the POI body or the city intro (e.g. "there's a jazz festival at Paradiso during your stay").

## Don't

- Don't add events more than 60 days out — they're hard to keep accurate
- Don't hallucinate event details — skip uncertain ones, note them in the PR
- Don't add shadow POIs for venues already in World66
- Don't modify the URL structure

## Working with Git

- Don't work on main — always branch off `origin/main`
- One PR per source slug, one per delist
- We squash PRs on merge
- No force pushes, no amended commits

## Software engineering practices

- Store requirements in `requirements.in`, compile with `uv` to `requirements.txt`
- Use `python-frontmatter` for reading/writing frontmatter — don't roll your own
