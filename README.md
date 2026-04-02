# mykidspots-event-engine

Event ingestion pipeline for MyKidSpots. Scrapes kid-friendly events from libraries, parks, museums, and city tourism CVBs across North Carolina.

## Architecture

```
sources/*.yml          → YAML source configs (one file per org or region)
scrapers/              → Platform-specific scrapers (LibCal, CivicPlus, SimplyviewRest, ...)
classify/ai_classifier → Claude Haiku filters events by family relevance
normalize/pipeline     → Transforms RawEvent → NormalizedEvent (cost, age, categories, dedup)
orchestrator           → Drives all scraper runs, calls classifier, writes to Supabase
```

**Data flow:**

```
Scraper → RawEvent → [AI Classifier] → normalize() → NormalizedEvent → Supabase upsert
```

## Running

```bash
# Dry run (no DB writes) — inspect output
uv run python -m event_engine.cli scrape --sources-dir sources --dry-run

# Filter to a single source
uv run python -m event_engine.cli scrape --sources-dir sources --source-filter "visit-raleigh" --dry-run

# Live run
ANTHROPIC_API_KEY=sk-... uv run python -m event_engine.cli scrape --sources-dir sources
```

## Tests

```bash
uv run pytest tests/ -v
```

## Lint

```bash
uv run ruff check src/ && uv run mypy src/
```

---

## Adding a New Tourism CVB City

City tourism CVB sites (Convention & Visitors Bureaus) are a high-value event source — they aggregate festivals, outdoor markets, parades, and cultural events that libraries and parks departments don't cover.

Adding a new city takes **~10 minutes** once the scraper is built.

### Step 1: Identify the CVB platform

Search "[city name] tourism events" or "visit [city name]" to find the CVB site. Then inspect the events page:

1. Open browser DevTools → Network tab, navigate to the events page
2. Look for XHR calls returning JSON — common patterns:
   - `GET /includes/rest/plugins_events_events/find/` → **Simpleview REST v1**
   - `GET /includes/rest_v2/plugins_events_events_by_date/find/` → **Simpleview REST v2**
   - `POST` to a `meilisearch.*.com` host → **Meilisearch**
   - `.ics` or `ical` links in page HTML → **iCal** (use existing `ICalScraper`)
3. Look for Simpleview fingerprints: `simpleviewinc.com` CDN assets, `x-sv-pid` response header, `get_simple_token` endpoint

### Step 2: Add a source config

Append to `sources/tourism_nc.yml` (or create `sources/tourism_[state].yml`):

**For Simpleview REST v1** (legacy events plugin — common pattern):
```yaml
- id: "visit-[city]-events"
  name: "Visit [City] Events"
  platform: "simpleview_rest"
  trust_level: "new"
  content_policy: "commercial"
  enabled: true
  timezone: "America/New_York"
  base_url: "https://www.[cvb-domain].com"
  location_id: ""
  location:
    name: "[City]"
    city: "[City]"
    latitude: [lat]
    longitude: [lng]
  request_delay_ms: 1500
  default_cost_type: "free"
  ai_classification: "required"
  selectors:
    rest_version: "v1"
    calendar_id: "1"           # optional — check API response for calendarid field
  category_overrides:
    - "community"
```

**For Simpleview REST v2** (layoutjs variant — newer sites):
```yaml
- id: "visit-[city]-events"
  name: "Visit [City] Events"
  platform: "simpleview_rest"
  # ... same fields as v1, except:
  selectors:
    rest_version: "v2"
```

**For Meilisearch** (exposed public search key in page HTML):
```yaml
- id: "[city]sgotalot-events"
  name: "[City] Events"
  platform: "meilisearch"
  trust_level: "new"
  content_policy: "commercial"
  enabled: true
  timezone: "America/New_York"
  base_url: "https://www.[cvb-domain].com"
  location_id: ""
  location:
    name: "[City]"
    city: "[City]"
    latitude: [lat]
    longitude: [lng]
  request_delay_ms: 1500
  default_cost_type: "free"
  ai_classification: "required"
  selectors:
    search_url: "https://search.[domain].com"
    index: "cluster_events"          # confirm via DevTools
    api_key: "[public-key-from-html]" # intentionally public — embedded in page source
  category_overrides:
    - "community"
```

**For iCal** (calendar feed link in page source):
```yaml
- id: "visit-[city]-events"
  name: "Visit [City] Events"
  platform: "ical"
  trust_level: "new"
  content_policy: "commercial"
  enabled: true
  timezone: "America/New_York"
  base_url: "https://www.[cvb-domain].com/events/feed.ics"
  location_id: ""
  location:
    name: "[City]"
    city: "[City]"
    latitude: [lat]
    longitude: [lng]
  request_delay_ms: 1500
  default_cost_type: "free"
  ai_classification: "required"
  category_overrides:
    - "community"
```

### Step 3: Dry-run to verify

```bash
uv run python -m event_engine.cli scrape --sources-dir sources --source-filter "visit-[city]-events" --dry-run
```

Check log output for:
- `scrape_complete` — should show `raw_count > 0`
- `ai_classification_complete` — check `yes`/`no`/`maybe` ratio (target: ≥25% yes)
- No `scrape_failed` errors

### Step 4: Commit

```bash
git add sources/tourism_nc.yml
git commit -m "feat: add Visit [City] tourism CVB source"
```

### Classifier thresholds for tourism sources

Tourism sources use `TOURISM_SYSTEM_PROMPT` (inclusive family filter — festivals, markets, parades = yes). Expected ratios:
- `yes` ≥ 25% — CVBs have lots of adult-oriented events, this is expected
- `no` ≤ 60% — higher suggests the prompt is too strict
- `maybe` ≤ 30% — maybes go to curator queue, keep low

If ratios are off, tune `TOURISM_SYSTEM_PROMPT` in `src/event_engine/classify/ai_classifier.py`.

### Cloudflare-protected sites

Some CVB sites (e.g., ExploreAsheville) are protected by Cloudflare bot detection, which blocks all automated HTTP clients. Options in priority order:
1. **Contact the CVB directly** — as a public-private partnership, they often have data feeds or API keys available for partners
2. **Check for iCal feeds** — try `[domain]/events/feed.ics` (sometimes not Cloudflare-protected even if the HTML page is)
3. **Defer to Phase 2** — seed that city from other sources (libraries, parks) until a data partnership is established

Do **not** build Playwright-based scrapers for MVP — they are fragile and high-maintenance.

---

## Source Platforms

| Platform key | Scraper class | Used for |
|---|---|---|
| `simpleview_rest` | `SimplyviewRestScraper` | Simpleview CMS CVB sites (v1 + v2 REST API) |
| `meilisearch` | `MeilisearchTourismScraper` | CVB sites with exposed Meilisearch endpoint |
| `ical` | `ICalScraper` | Any site with an iCal feed |
| `civicplus` | `CivicPlusScraper` | Town/city parks & rec departments |
| `libcal` | `LibCalScraper` | Library systems on SpringShare LibCal |
| `bibliocommons` | `BiblioCommonsScraper` | Library systems on BiblioCommons |
| `librarymarket` | `LibraryMarketScraper` | Library systems on LibraryMarket |
| `localist` | `LocalistScraper` | Museums and universities on Localist |
| `wakegov` | `WakeGovScraper` | Wake County Parks (legacy direct API) |

## Content Policy

| `content_policy` | Description | Scraper drops description? |
|---|---|---|
| `government` | Public-domain content | No — kept as-is |
| `commercial` | Third-party copyright | Yes — description always set to `None` |

Tourism CVBs always use `commercial` — event descriptions are aggregated from venues and contain third-party copyright. Only title, date, location, and source URL are retained.
